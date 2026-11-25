import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { browserSeed, type BrowserAccount } from './seed'

const base = process.env.S08_BASE_URL
const evidence = process.env.S08_EVIDENCE_DIR
const national = '/api/v1/overview/national/'
const workspace = '工作区导航'
const legend = 'AQI 六级区间说明'
const desktop = [[1920, 1080], [1440, 900], [1280, 800]] as const
const profileApis = ['/api/v1/me/', '/api/v1/me/capabilities/']
const sessionEvidence = () => ({ csrf: 0, login: 0, shell: 0, national: 0, nationalMs: 0, nationalHasFirstCity: false })

function save(name: string, data: unknown) {
  if (evidence) writeFileSync(join(evidence, name), JSON.stringify(data, null, 2))
}

async function checkAxe(page: Page, scope: string, name: string) {
  const result = await new AxeBuilder({ page }).include(scope)
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  const summarize = (rules: typeof result.violations) => rules.map(rule => ({
    rule: rule.id, impact: rule.impact ?? null, nodes: rule.nodes.length,
  }))
  const violations = summarize(result.violations)
  if (evidence) mkdirSync(join(evidence, 'accessibility'), { recursive: true })
  save(`accessibility/${name}.json`, {
    origin: 'F', version: result.testEngine.version, passedRules: result.passes.length,
    violations, needsManualReview: summarize(result.incomplete),
  })
  expect(result.passes.length).toBeGreaterThan(0)
  expect.soft(violations, name).toEqual([])
}

function fixtureUrl() {
  const seed = browserSeed()
  const query = new URLSearchParams({ scope: 'synthetic', product: String(seed.products.L), at: seed.snapshot_at, standard_series: 'HJ633-2012' })
  return `/app/?${query}`
}

async function signIn(page: Page, account: BrowserAccount, next: string, entry: ReturnType<typeof sessionEvidence>) {
  // The request fixture associated with Page shares its BrowserContext cookie jar.
  const csrf = await page.request.get('/api/v1/auth/csrf/')
  entry.csrf = csrf.status()
  expect(entry.csrf).toBe(200)
  const { csrf_token } = await csrf.json() as { csrf_token: string }
  const response = await page.request.post('/api/v1/auth/login/', {
    headers: { Origin: new URL(csrf.url()).origin, 'X-CSRFToken': csrf_token },
    data: { username: account.username, password: account.password },
  })
  entry.login = response.status()
  expect(entry.login).toBe(200)
  const shell = await page.goto(next, { waitUntil: 'domcontentloaded' })
  entry.shell = shell?.status() ?? 0
  expect(entry.shell).toBe(200)
}


async function openObservation(page: Page, entry: ReturnType<typeof sessionEvidence>) {
  const seed = browserSeed()
  const url = fixtureUrl()
  const [response] = await Promise.all([
    page.waitForResponse(response => {
      const request = new URL(response.url())
      return request.pathname === national && request.searchParams.get('scope') === 'synthetic'
    }, { timeout: 25_000 }),
    signIn(page, seed.accounts.reader, url, entry),
  ])
  entry.national = response.status()
  expect(entry.national).toBe(200)
  const body = await response.json() as { results?: { city: number }[] }
  entry.nationalMs = Math.round(response.request().timing().responseEnd)
  entry.nationalHasFirstCity = body.results?.some(row => row.city === seed.city) ?? false
  expect(entry.nationalHasFirstCity).toBe(true)
  const city = page.getByRole('table', { name: '城市空气质量列表' }).getByRole('button', { name: seed.cities[0].name, exact: true })
  await expect(city).toBeVisible()
  return city
}

function guard(page: Page, allowedApi: (url: URL) => boolean) {
  const issues = { external: 0, unknownApi: 0, status: 0, pageError: 0, consoleError: 0, cancelled: 0, failed: 0 }
  const consoleErrors: { source: string; status: number | null }[] = []
  const origin = new URL(base!).origin
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return
    if (url.origin !== origin) issues.external++
    if (url.pathname.startsWith('/api/') && !allowedApi(url)) issues.unknownApi++
  })
  page.on('response', response => { if (response.status() >= 400) issues.status++ })
  page.on('requestfailed', request => {
    const code = request.failure()?.errorText
    if (code === 'net::ERR_ABORTED') issues.cancelled++
    else issues.failed++
  })
  page.on('pageerror', () => { issues.pageError++ })
  page.on('console', message => {
    if (message.type() !== 'error') return
    issues.consoleError++
    if (consoleErrors.length === 8) return
    const location = message.location().url
    const path = URL.canParse(location) ? new URL(location).pathname : ''
    const source = path === '/favicon.ico' ? 'favicon' : path.startsWith('/static/') ? 'static' : path.startsWith('/api/') ? 'api' : 'other'
    const status = message.text().match(/server responded with a status of (\d{3})/)?.[1]
    consoleErrors.push({ source, status: status ? Number(status) : null })
  })
  return { issues, consoleErrors }
}

function assertGuard(guarded: ReturnType<typeof guard>) {
  expect({ ...guarded.issues, cancelled: 0 }).toEqual({ external: 0, unknownApi: 0, status: 0, pageError: 0, consoleError: 0, cancelled: 0, failed: 0 })
}

async function geometry(page: Page) {
  return await page.evaluate(() => {
    function box(selector: string) {
      const element = document.querySelector(selector)
      const rect = element?.getBoundingClientRect()
      if (!rect) return null
      return { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height }
    }
    const wrap = document.querySelector('.observation-table')?.parentElement
    const overflow = document.documentElement.scrollWidth > innerWidth
      ? Array.from(document.querySelectorAll('body *')).flatMap(element => {
        const right = Math.round(element.getBoundingClientRect().right)
        if (right <= innerWidth) return []
        return [{ tag: element.tagName, classes: [...element.classList].slice(0, 3), right }]
      }).slice(0, 12)
      : []
    return {
      viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio, scale: visualViewport?.scale ?? null },
      documentWidth: document.documentElement.scrollWidth, overflow,
      sidebar: box('.sidebar'), main: box('#main-content'), header: box('.topbar'),
      table: wrap ? { client: wrap.clientWidth, scroll: wrap.scrollWidth } : null,
      fonts: document.fonts.status,
    }
  })
}

async function keyboardFocus(control: Locator) {
  return await control.evaluate(element => {
    const rect = element.getBoundingClientRect()
    const style = getComputedStyle(element)
    const point = [rect.left + rect.width / 2, rect.top + rect.height / 2] as const
    const first = document.elementFromPoint(...point)
    return {
      focused: document.activeElement === element,
      visibleOutline: element.matches(':focus-visible') && style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 1,
      receivesHit: Boolean(first && element.contains(first)),
      coveredByHeader: Boolean(first?.closest('.topbar') && !element.closest('.topbar')),
      width: rect.width, height: rect.height,
    }
  })
}

async function skipTarget(page: Page) {
  const heading = page.getByRole('heading', { name: '全国空气质量总览', exact: true })
  await expect(page.getByRole('main')).toBeFocused()
  return await heading.evaluate(element => {
    const rect = element.getBoundingClientRect()
    const headerBottom = document.querySelector('.topbar')!.getBoundingClientRect().bottom
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
    return { headingTop: rect.top, headingBottom: rect.bottom, headerBottom, viewportHeight: innerHeight, receivesHit: element.contains(hit) }
  })
}

async function rankingGradeLayout(page: Page) {
  return await page.locator('.ranking-row .grade-chip').evaluateAll(chips => chips.map(chip => {
    const range = document.createRange()
    range.selectNodeContents(chip)
    const lines = Array.from(range.getClientRects())
    const bounds = chip.closest('.ranking-row')!.getBoundingClientRect()
    const rect = chip.getBoundingClientRect()
    return {
      characters: (chip.textContent ?? '').trim().length, lines: lines.length, width: rect.width,
      fitsRow: rect.left >= bounds.left && rect.right <= bounds.right,
      fitsChip: lines.every(line => line.left >= rect.left && line.right <= rect.right),
    }
  }))
}

async function readSkin(page: Page) {
  return await page.evaluate(async () => {
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    const surfaces = ['.topbar', '.sidebar', '.city-table-panel', '.summary-strip > div', '.map-unavailable', '.nav-scrim']
    return surfaces.map(selector => {
      const element = document.querySelector(selector)
      if (!element) return { surface: selector, present: false as const }
      const style = getComputedStyle(element)
      return { surface: selector, present: true as const, backdrop: style.backdropFilter, background: style.backgroundColor,
        hasBackgroundImage: style.backgroundImage !== 'none', border: [style.borderTopStyle, style.borderBottomStyle].some(value => value !== 'none'),
        transitionDuration: style.transitionDuration,
        navigationFilterToken: style.getPropertyValue('--aq-navigation-filter').trim(),
        panelFillToken: style.getPropertyValue('--aq-overview-panel-fill').trim() }
    })
  })
}

async function readMaterialPolicy(page: Page) {
  return await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement)
    return {
      supportsBackdropFilter: CSS.supports('backdrop-filter', 'blur(1px)'),
      allowsGlass: matchMedia('(prefers-reduced-transparency: no-preference) and (prefers-reduced-motion: no-preference) and (forced-colors: none)').matches,
      reducesTransparency: matchMedia('(prefers-reduced-transparency: reduce)').matches,
      reducesMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
      forcesColors: matchMedia('(forced-colors: active)').matches,
      solidAttribute: document.querySelector('.application')?.getAttribute('data-material'),
      navigationFilter: root.getPropertyValue('--aq-navigation-filter').trim(),
      overviewPanelFill: root.getPropertyValue('--aq-overview-panel-fill').trim(),
    }
  })
}

function opaqueColor(color: string) {
  if (color.startsWith('rgb(')) return true
  const alpha = color.match(/^rgba\([^,]+,[^,]+,[^,]+,\s*([\d.]+)\)$/)?.[1]
  return alpha !== undefined && Number(alpha) === 1
}

test('formal shell layout, drawer keyboard access and overview surface fallbacks', async ({ page, context, browser }) => {
  test.setTimeout(120_000)
  const cases: Record<string, unknown>[] = []
  const gaps: string[] = []
  const entry = sessionEvidence()
  const guarded = guard(page, url => profileApis.includes(url.pathname) || url.pathname === national && url.searchParams.get('scope') === 'synthetic')
  await page.emulateMedia({ reducedMotion: 'no-preference', forcedColors: 'none' })
  const cdp = await context.newCDPSession(page)
  try {
    await page.setViewportSize({ width: 1440, height: 900 })
    const cityButton = await openObservation(page, entry)
    await checkAxe(page, '.overview-page', 'overview-desktop')
    const initialPaint = await page.evaluate(async () => {
      await document.fonts.ready
      const timing = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
      return {
        paint: performance.getEntriesByType('paint').map(item => ({ name: item.name, ms: item.startTime })),
        domContentLoadedMs: timing?.domContentLoadedEventEnd ?? null,
        fonts: document.fonts.status,
      }
    })
    cases.push({ state: 'initial_observation_load', sample: initialPaint })
    await page.keyboard.press('Tab')
    const skip = page.getByRole('link', { name: '跳到主要内容' })
    await expect(skip).toBeFocused()
    const skipFocus = await keyboardFocus(skip)
    expect(skipFocus.visibleOutline && skipFocus.receivesHit).toBe(true)
    await page.keyboard.press('Enter')
    const desktopTarget = await skipTarget(page)
    cases.push({ state: 'skip_link', evidence: skipFocus, target: desktopTarget })
    expect(desktopTarget.headingTop).toBeGreaterThanOrEqual(desktopTarget.headerBottom)
    expect(desktopTarget.headingBottom).toBeLessThanOrEqual(desktopTarget.viewportHeight)
    expect(desktopTarget.receivesHit).toBe(true)

    for (const [width, height] of [...desktop, [768, 1024], [390, 844], [320, 844]]) {
      await page.setViewportSize({ width, height })
      const layout = await geometry(page)
      cases.push({ state: 'overview_layout', layout })
      expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewport.width)
      if (width >= 1280) {
        expect(layout.sidebar!.x).toBeGreaterThanOrEqual(0)
        expect(layout.sidebar!.right).toBeLessThan(layout.main!.x)
        expect(layout.main!.right).toBeLessThanOrEqual(width)
      }
      if (width <= 390) {
        expect(layout.table!.scroll).toBeGreaterThan(layout.table!.client)
        await expect.poll(async () => (await geometry(page)).sidebar!.right).toBeLessThanOrEqual(0)
      }
      const grades = await rankingGradeLayout(page)
      cases.push({ state: 'ranking_grade_layout', width, grades })
      expect(grades.some(grade => grade.characters >= 4)).toBe(true)
      for (const grade of grades) expect(grade).toMatchObject({ lines: 1, fitsRow: true, fitsChip: true })
      if (width === 1440 || width === 390) await screenshot(page, `presentation-observation-${width}.png`)
      if (width >= 1280) {
        const collapse = page.getByRole('button', { name: '收起侧栏' })
        await expect(collapse).toHaveAttribute('aria-expanded', 'true')
        await collapse.press('Enter')
        const expand = page.getByRole('button', { name: '展开侧栏' })
        await expect(expand).toHaveAttribute('aria-expanded', 'false')
        await expect(expand).toBeFocused()
        await expect(page.getByRole('navigation', { name: workspace }).getByRole('link', { name: '监测总览' })).toBeVisible()
        const compact = await geometry(page)
        expect(compact.sidebar!.width).toBe(64)
        expect(compact.main!.width).toBeGreaterThan(layout.main!.width)
        expect(compact.documentWidth).toBeLessThanOrEqual(width)
        cases.push({ state: 'desktop_sidebar_collapsed', layout: compact })
        if (width === 1440) await screenshot(page, 'presentation-collapsed-1440.png')
        await expand.press('Enter')
        await expect(collapse).toHaveAttribute('aria-expanded', 'true')
      }
    }
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
    await skip.press('Enter')
    const narrowTarget = await skipTarget(page)
    cases.push({ state: 'narrow_skip_link', target: narrowTarget })
    expect(narrowTarget.headingTop).toBeGreaterThanOrEqual(narrowTarget.headerBottom)
    expect(narrowTarget.headingBottom).toBeLessThanOrEqual(narrowTarget.viewportHeight)
    expect(narrowTarget.receivesHit).toBe(true)
    const open = page.getByRole('button', { name: '打开导航' })
    const navToggle = page.locator('.menu-toggle')
    const menu = page.getByRole('navigation', { name: workspace })
    const drawer = page.getByRole('dialog', { name: '观测工作区' })
    const close = drawer.getByRole('button', { name: '关闭导航' })
    await expect(menu).toHaveCount(0)
    await open.press('Enter')
    await expect(navToggle).toHaveAttribute('aria-expanded', 'true')
    await expect(drawer).toHaveAttribute('aria-modal', 'true')
    await expect(close).toBeFocused()
    const backgroundInert = await page.locator('#main-content').evaluate(element => {
      if (!(element instanceof HTMLElement) || !element.inert) return false
      element.focus()
      return document.activeElement !== element
    })
    expect(backgroundInert).toBe(true)
    await expect(menu.getByRole('link', { name: '监测总览' })).toBeVisible()
    await expect.poll(async () => (await geometry(page)).sidebar!.x).toBeGreaterThanOrEqual(0)
    await checkAxe(page, '#workspace-sidebar', 'observation-drawer')
    await page.keyboard.press('Shift+Tab')
    await expect(drawer.getByRole('link', { name: '数据与来源' })).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(close).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(open).toHaveAttribute('aria-expanded', 'false')
    await expect(open).toBeFocused()
    await expect(menu).toHaveCount(0)
    await open.press('Enter')
    await close.press('Enter')
    await expect(open).toHaveAttribute('aria-expanded', 'false')
    await expect(open).toBeFocused()
    await expect(menu).toHaveCount(0)
    cases.push({ state: 'narrow_drawer', modal: true, backgroundInert: true, wrapsFocus: true, escapeCloses: true, returnsFocus: true, closedNavigationUnavailableByRole: true })

    await expect.poll(async () => (await geometry(page)).sidebar!.right).toBeLessThanOrEqual(0)
    const disclosure = page.getByText(legend, { exact: true })
    await disclosure.evaluate(element => element.scrollIntoView({ block: 'center' }))
    await disclosure.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByText('0–50', { exact: true })).toBeVisible()
    const legendFocus = await keyboardFocus(disclosure)
    cases.push({ state: 'narrow_legend_focus', ...legendFocus })
    expect(legendFocus.focused && legendFocus.visibleOutline && legendFocus.receivesHit && !legendFocus.coveredByHeader).toBe(true)
    expect(legendFocus.width).toBeGreaterThanOrEqual(24)
    expect(legendFocus.height).toBeGreaterThanOrEqual(24)
    const city = await cityButton.boundingBox()
    expect(city!.width).toBeGreaterThanOrEqual(24)
    expect(city!.height).toBeGreaterThanOrEqual(24)
    cases.push({ state: 'narrow_city_target', width: city!.width, height: city!.height })
    await screenshot(page, 'presentation-legend-320.png')
    const search = page.getByRole('searchbox', { name: '城市检索' })
    await search.focus()
    await expect(search).toBeFocused()
    const searchFocusOutline = await search.evaluate(element => getComputedStyle(element.parentElement!).outlineStyle)
    expect(searchFocusOutline).toBe('solid')
    cases.push({ state: 'narrow_search_focus', focused: true, outline: searchFocusOutline })

    await page.setViewportSize({ width: 768, height: 1024 })
    await open.press('Enter')
    await expect(navToggle).toHaveAttribute('aria-expanded', 'true')
    const defaultSkin = await readSkin(page)
    expect(defaultSkin[0]).toMatchObject({ present: true })
    const defaultPolicy = await readMaterialPolicy(page)
    cases.push({ state: 'default_skin', policy: defaultPolicy, surfaces: defaultSkin })
    if (defaultPolicy.allowsGlass && defaultPolicy.supportsBackdropFilter) {
      expect(defaultSkin[0].backdrop).toContain('blur(18px)')
      expect(defaultSkin[1].backdrop).toContain('blur(18px)')
      expect(opaqueColor(defaultSkin[2].background!)).toBe(false)
      expect(defaultSkin[4].hasBackgroundImage).toBe(true)
    }
    const overlaySolid = await page.evaluate(() => getComputedStyle(document.querySelector('.nav-scrim')!).backdropFilter === 'none')
    expect(overlaySolid).toBe(true)

    const application = page.locator('.application')
    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-transparency', value: 'reduce' }] })
    const reducesTransparency = await page.evaluate(() => matchMedia('(prefers-reduced-transparency: reduce)').matches)
    if (reducesTransparency) await expect(application).toHaveAttribute('data-material', 'solid')
    const reducedSkin = await readSkin(page)
    const reducedPolicy = await readMaterialPolicy(page)
    cases.push({ state: 'reduced_transparency', policy: reducedPolicy, surfaces: reducedSkin })
    if (reducedPolicy.reducesTransparency) {
      for (const surface of reducedSkin.slice(0, 5)) {
        expect(surface.present).toBe(true)
        expect(surface.backdrop).toBe('none')
        expect(opaqueColor(surface.background!)).toBe(true)
        expect(surface.hasBackgroundImage).toBe(false)
      }
    } else gaps.push('chromium_reduced_transparency_media_emulation_unavailable')
    await cdp.send('Emulation.setEmulatedMedia', { features: [] })

    await page.emulateMedia({ reducedMotion: 'reduce', forcedColors: 'none' })
    await expect(application).toHaveAttribute('data-material', 'solid')
    const motionSkin = await readSkin(page)
    expect(motionSkin[0]).toMatchObject({ present: true, backdrop: 'none' })
    expect(opaqueColor(motionSkin[0].background!)).toBe(true)
    cases.push({ state: 'reduced_motion', policy: await readMaterialPolicy(page), surfaces: motionSkin })
    expect(motionSkin[1].transitionDuration?.split(',').every(part => Number.parseFloat(part) <= 0.001)).toBe(true)

    await page.emulateMedia({ reducedMotion: 'no-preference', forcedColors: 'active' })
    await expect(application).toHaveAttribute('data-material', 'solid')
    expect(await page.evaluate(() => matchMedia('(forced-colors: active)').matches)).toBe(true)
    const forcedSkin = await readSkin(page)
    expect(forcedSkin[0]).toMatchObject({ present: true, backdrop: 'none' })
    cases.push({ state: 'forced_colors', policy: await readMaterialPolicy(page), surfaces: forcedSkin })
    expect(forcedSkin[2].border).toBe(true)

    await page.emulateMedia({ reducedMotion: 'no-preference', forcedColors: 'none' })
    await expect(application).not.toHaveAttribute('data-material', 'solid')
    // Keep application rules and media intact. Suppress only the paint property to exercise the base surfaces.
    const style = await page.addStyleTag({ content: '* { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }' })
    try {
      const disabled = await readSkin(page)
      for (const surface of disabled) {
        if (!surface.present) continue
        expect(surface.backdrop).toBe('none')
        expect(surface.background !== 'rgba(0, 0, 0, 0)' || surface.hasBackgroundImage).toBe(true)
      }
      cases.push({ state: 'backdrop_filter_css_suppression', surfaces: disabled })
      await expect(menu.getByRole('link', { name: '监测总览' })).toBeVisible()
    } finally {
      await style.evaluate(element => element.parentNode?.removeChild(element))
    }
    await close.press('Enter')
    const material = page.getByRole('button', { name: '实体主题' })
    await expect(material).toHaveAttribute('aria-pressed', 'false')
    await material.press('Enter')
    await expect(material).toHaveAttribute('aria-pressed', 'true')
    await open.press('Enter')
    const opaqueSkin = await readSkin(page)
    for (const surface of opaqueSkin.slice(0, 5)) {
      expect(surface.backdrop).toBe('none')
      expect(opaqueColor(surface.background!)).toBe(true)
      expect(surface.hasBackgroundImage).toBe(false)
    }
    cases.push({ state: 'explicit_opaque_material', policy: await readMaterialPolicy(page), surfaces: opaqueSkin })
    await screenshot(page, 'presentation-opaque-drawer-768.png')
    await page.setViewportSize({ width: 1280, height: 800 })
    await expect(drawer).toHaveCount(0)
    await expect(page.getByRole('button', { name: '收起侧栏' })).toBeFocused()
    await expect(menu).toBeVisible()
    await page.setViewportSize({ width: 768, height: 1024 })
    await expect(open).toHaveAttribute('aria-expanded', 'false')
    await expect(menu).toHaveCount(0)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(material).toHaveAttribute('aria-pressed', 'true')
    await material.press('Enter')
    await expect(material).toHaveAttribute('aria-pressed', 'false')
    cases.push({ state: 'drawer_breakpoint_and_material_preference', resetsDrawerAcrossBreakpoints: true, persistsMaterialAcrossReload: true })
    gaps.push('nested_select_and_date_picker_modal_not_exercised', 'browser_without_backdrop_filter_not_available')
    assertGuard(guarded)
  } finally {
    save('presentation-layout.json', { origin: 'F', workspace: 'observation', browser: browser.version(), platform: process.platform, input: 'keyboard', entry, cases, gaps, ...guarded })
    await cdp.detach()
  }
})


async function screenshot(page: Page, name: string) {
  if (evidence) await page.screenshot({ path: join(evidence, name), mask: [page.locator('.account-link')], animations: 'disabled' })
}

test('management workspace keeps authorized navigation and a bounded content area', async ({ page, browser }) => {
  const seed = browserSeed()
  const entry = sessionEvidence()
  const guarded = guard(page, url => profileApis.includes(url.pathname))
  const layouts = []
  try {
    await signIn(page, seed.accounts.operator, '/app/manage/collection?scope=synthetic', entry)
    await expect(page).toHaveURL('/app/manage/collection?scope=synthetic')
    await expect(page.getByRole('heading', { name: '采集任务', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '采集账本与执行' })).toBeVisible()
    await expect(page.getByText('管理工作区', { exact: true })).toBeVisible()
    await expect(page.getByRole('navigation', { name: workspace }).getByRole('link', { name: '采集任务' })).toHaveClass(/nav-active/)
    for (const [width, height] of [...desktop, [390, 844], [320, 844]]) {
      await page.setViewportSize({ width, height })
      if (width <= 390) await expect.poll(async () => (await geometry(page)).sidebar!.right).toBeLessThanOrEqual(0)
      const layout = await geometry(page)
      layouts.push(layout)
      expect(layout.documentWidth).toBeLessThanOrEqual(width)
      expect(layout.main!.right).toBeLessThanOrEqual(width)
      if (width >= 1280) expect(layout.sidebar!.right).toBeLessThan(layout.main!.x)
      if (width === 1440 || width === 390) await screenshot(page, `presentation-management-${width}.png`)
    }
    await checkAxe(page, '#main-content', 'management-narrow')
    assertGuard(guarded)
  } finally {
    save('presentation-management.json', { origin: 'F', workspace: 'management', entry, browser: browser.version(), platform: process.platform, layouts, ...guarded })
  }
})

test.describe('Chromium touch emulation', () => {
  test.use({ hasTouch: true, isMobile: true, deviceScaleFactor: 2, viewport: { width: 390, height: 844 } })

  test('narrow overview opens the AQI ranges and accepts a city selection with tap', async ({ page, browser }) => {
    const seed = browserSeed()
    const entry = sessionEvidence()
    const guarded = guard(page, url => profileApis.includes(url.pathname) || (url.pathname === national || url.pathname === `/api/v1/cities/${seed.city}/trend/`) && url.searchParams.get('scope') === 'synthetic')
    const cases: Record<string, unknown>[] = []
    try {
      const city = await openObservation(page, entry)
      const layout = await geometry(page)
      cases.push({ state: 'touch_layout', layout })
      expect(layout.viewport.width).toBe(390)
      expect(layout.documentWidth).toBeLessThanOrEqual(390)
      const disclosure = page.getByText(legend, { exact: true })
      await disclosure.tap()
      await expect(page.getByText('0–50', { exact: true })).toBeVisible()
      await expect(page.getByText('> 300', { exact: true })).toBeVisible()
      const box = await city.boundingBox()
      expect(box!.height).toBeGreaterThanOrEqual(44)
      await city.tap()
      await expect(city).toHaveAttribute('aria-pressed', 'true')
      await expect(page).toHaveURL(url => url.searchParams.get('focus_city') === String(seed.city))
      await expect(page.getByRole('img', { name: /独立 T 产品 24 小时 AQI 趋势/ })).toBeVisible()
      cases.push({ state: 'tap_legend_and_city', height: box!.height, selectedAndTrend: true })
      await screenshot(page, 'presentation-touch-390.png')
      assertGuard(guarded)
    } finally {
      save('presentation-touch.json', { origin: 'F', input: 'emulated_touch', browser: browser.version(), platform: process.platform, entry, cases, ...guarded })
    }
  })
})
