import { chromium, expect, test, type Locator, type Page } from '@playwright/test'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { arch, platform, tmpdir } from 'node:os'
import { join } from 'node:path'
import { browserSeed } from './seed'

const nationalPath = '/api/v1/overview/national/'
const profilePaths = ['/api/v1/me/', '/api/v1/me/capabilities/']
const evidenceDirectory = process.env.S08_EVIDENCE_DIR

async function drawerFocusState(page: Page) {
  return await page.evaluate(() => {
    const active = document.activeElement
    const sidebar = document.querySelector('#workspace-sidebar')!
    return {
      active: active?.matches('.drawer-close') ? 'close' : active?.matches('.menu-toggle') ? 'toggle'
        : active?.matches('#main-content') ? 'main' : active?.tagName ?? 'none',
      viewport: [innerWidth, innerHeight],
      reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
      drawerInert: sidebar.hasAttribute('inert'),
      drawerVisibility: getComputedStyle(sidebar).visibility,
      closeVisibility: getComputedStyle(sidebar.querySelector('.drawer-close')!).visibility,
    }
  })
}

async function layout(page: Page, city: Locator) {
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  })
  return await city.evaluate(element => {
    const root = document.documentElement
    const heading = document.querySelector('h1')!
    const topbar = document.querySelector('.topbar')!
    const table = element.closest('.table-scroll')!
    const range = document.createRange()
    // Measure one glyph: wrapping can change the full label height at each zoom.
    const textNode = document.createTreeWalker(element, NodeFilter.SHOW_TEXT).nextNode()!
    const glyphOffset = textNode.textContent!.search(/\S/)
    range.setStart(textNode, glyphOffset)
    range.setEnd(textNode, glyphOffset + 1)
    const cityGlyph = range.getBoundingClientRect()
    return {
      outerWidth, outerHeight, innerWidth, innerHeight,
      documentWidth: root.scrollWidth,
      visualWidth: visualViewport?.width,
      visualHeight: visualViewport?.height,
      visualScale: visualViewport?.scale,
      dpr: devicePixelRatio,
      rootZoom: getComputedStyle(root).zoom,
      bodyZoom: getComputedStyle(document.body).zoom,
      fonts: document.fonts.status,
      cityFontCssPx: parseFloat(getComputedStyle(element).fontSize),
      cityGlyphDeviceHeight: cityGlyph.height * devicePixelRatio,
      headingFontCssPx: parseFloat(getComputedStyle(heading).fontSize),
      headerClientWidth: topbar.clientWidth,
      headerScrollWidth: topbar.scrollWidth,
      tableClientWidth: table.clientWidth,
      tableScrollWidth: table.scrollWidth,
    }
  })
}

async function userTarget(locator: Locator) {
  return await locator.evaluate(element => {
    const rect = element.getBoundingClientRect()
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
    return { top: rect.top, bottom: rect.bottom, height: innerHeight, hits: element.contains(hit), headerBottom: document.querySelector('.topbar')!.getBoundingClientRect().bottom }
  })
}

test('Chrome native page zoom enlarges list text at 200% and reflows the F overview at 400%', async ({ baseURL }, testInfo) => {
  test.setTimeout(100_000)
  const origin = new URL(baseURL!).origin
  expect(new URL(origin).hostname).toBe('127.0.0.1')
  const profile = await mkdtemp(join(tmpdir(), 's08-chrome-zoom-'))
  const receipt = {
    browser: '', os: platform(), architecture: arch(),
    route: '/app/', sourceClass: 'F', productShape: 'L', standard: 'HJ633-2012',
    zoomControl: 'chrome://settings/appearance#zoomLevel', interaction: 'desktop keyboard',
    phase: 'launch', http: { csrf: 0, login: 0, shell: 0, national: 0 },
    network: { external: 0, unknownApi: 0, badStatus: 0, failed: 0, cancelled: 0, consoleErrors: 0, pageErrors: 0 },
    sizes: {} as Record<number, Awaited<ReturnType<typeof layout>>>,
    targets: {} as Record<string, Awaited<ReturnType<typeof userTarget>>>,
    drawerFocus: null as Awaited<ReturnType<typeof drawerFocusState>> | null,
    screenshotDevicePx: null as [number, number] | null,
    localScrollLeft: 0,
  }
  try {
    const context = await chromium.launchPersistentContext(profile, {
      ...testInfo.project.use.launchOptions,
      baseURL: origin,
      headless: true,
      viewport: null,
      locale: 'zh-CN',
      timezoneId: 'Asia/Shanghai',
      reducedMotion: 'reduce',
      args: ['--window-size=1280,800'],
    })
    try {
      const page = context.pages()[0] ?? await context.newPage()
      const cdp = await context.newCDPSession(page)
      receipt.browser = (await cdp.send('Browser.getVersion')).product
      await cdp.detach()
      page.on('request', request => {
        const url = new URL(request.url())
        if (!['http:', 'https:'].includes(url.protocol)) return
        if (url.origin !== origin) receipt.network.external++
        if (url.pathname.startsWith('/api/') && !profilePaths.includes(url.pathname)
          && !(url.pathname === nationalPath && url.searchParams.get('scope') === 'synthetic')) receipt.network.unknownApi++
      })
      page.on('response', response => { if (response.status() >= 400) receipt.network.badStatus++ })
      page.on('requestfailed', request => {
        if (request.failure()?.errorText === 'net::ERR_ABORTED') receipt.network.cancelled++
        else receipt.network.failed++
      })
      page.on('console', message => { if (message.type() === 'error') receipt.network.consoleErrors++ })
      page.on('pageerror', () => { receipt.network.pageErrors++ })

      receipt.phase = 'session'
      const seed = browserSeed()
      const csrf = await page.request.get('/api/v1/auth/csrf/')
      receipt.http.csrf = csrf.status()
      expect(receipt.http.csrf).toBe(200)
      const { csrf_token } = await csrf.json() as { csrf_token: string }
      const login = await page.request.post('/api/v1/auth/login/', {
        headers: { Origin: origin, 'X-CSRFToken': csrf_token },
        data: { username: seed.accounts.reader.username, password: seed.accounts.reader.password },
      })
      receipt.http.login = login.status()
      expect(receipt.http.login).toBe(200)
      const query = new URLSearchParams({ scope: 'synthetic', product: String(seed.products.L), at: seed.snapshot_at, standard_series: receipt.standard })
      const [national, shell] = await Promise.all([
        page.waitForResponse(response => {
          const url = new URL(response.url())
          return url.pathname === nationalPath && url.searchParams.get('scope') === 'synthetic'
        }, { timeout: 25_000 }),
        page.goto(`/app/?${query}`, { waitUntil: 'domcontentloaded' }),
      ])
      receipt.http.shell = shell?.status() ?? 0
      receipt.http.national = national.status()
      expect(receipt.http.shell).toBe(200)
      expect(receipt.http.national).toBe(200)
      const heading = page.getByRole('heading', { name: '全国空气质量总览', level: 1, exact: true })
      const table = page.getByRole('table', { name: '城市空气质量列表' })
      const tableRegion = page.getByRole('region', { name: '城市空气质量表，可横向滚动' })
      const city = table.getByRole('button', { name: seed.cities[0].name, exact: true })
      await expect(heading).toBeVisible()
      await expect(city).toBeVisible()

      receipt.phase = 'browser-preference'
      const settings = await context.newPage()
      await settings.goto('chrome://settings/appearance')
      const pageZoom = settings.locator('settings-appearance-page #zoomLevel')
      await expect(pageZoom.locator('option:checked')).toHaveText('100%')
      const makeMeasurement = async (percent: number) => {
        if (percent !== 100) await pageZoom.selectOption({ label: `${percent}%` })
        await expect(pageZoom.locator('option:checked')).toHaveText(`${percent}%`)
        await page.bringToFront()
        await expect.poll(() => page.evaluate(() => innerWidth)).toBe(1280 / (percent / 100))
        const measured = await layout(page, city)
        receipt.sizes[percent] = measured
        expect(measured.outerWidth).toBe(1280)
        expect(measured.documentWidth).toBe(measured.innerWidth)
        expect(measured.visualWidth).toBe(measured.innerWidth)
        expect(measured.visualScale).toBe(1)
        expect(measured.dpr).toBe(percent / 100)
        expect(measured.rootZoom).toBe('1')
        expect(measured.bodyZoom).toBe('1')
        expect(measured.fonts).toBe('loaded')
        expect(measured.headerScrollWidth).toBeLessThanOrEqual(measured.headerClientWidth)
        return measured
      }

      const baseline = await makeMeasurement(100)
      await expect(page.getByRole('navigation', { name: '工作区导航' })).toBeVisible()
      receipt.phase = '200-percent-text-through-page-zoom'
      const enlarged = await makeMeasurement(200)
      expect(enlarged.cityFontCssPx).toBe(baseline.cityFontCssPx)
      expect(enlarged.cityGlyphDeviceHeight / baseline.cityGlyphDeviceHeight).toBeGreaterThanOrEqual(1.9)
      await expect(heading).toBeVisible()
      await expect(table.getByRole('columnheader', { name: '详情', exact: true })).toBeAttached()
      await expect(city).toBeEnabled()
      await settings.bringToFront()

      receipt.phase = '400-percent-reflow'
      const reflow = await makeMeasurement(400)
      expect(reflow.outerHeight).toBe(baseline.outerHeight)
      expect(reflow.tableScrollWidth).toBeGreaterThan(reflow.tableClientWidth)
      await settings.close()
      await page.bringToFront()
      await expect(page.getByRole('navigation', { name: '工作区导航' })).toHaveCount(0)
      await page.keyboard.press('Tab')
      const skip = page.getByRole('link', { name: '跳到主要内容' })
      await expect(skip).toBeFocused()
      await page.keyboard.press('Enter')
      await expect(page.getByRole('main')).toBeFocused()
      const title = await userTarget(heading)
      receipt.targets.title = title
      expect(title.top).toBeGreaterThanOrEqual(title.headerBottom)
      expect(title.bottom).toBeLessThanOrEqual(title.height)
      expect(title.hits).toBe(true)
      if (evidenceDirectory) {
        await expect(page.locator('.account-link')).toBeHidden()
        // With viewport:null, Playwright's default clip uses CSS dimensions for this native zoom.
        const screenshotSession = await context.newCDPSession(page)
        try {
          const { data } = await screenshotSession.send('Page.captureScreenshot', {
            format: 'png', fromSurface: true, captureBeyondViewport: false,
          })
          const screenshot = Buffer.from(data, 'base64')
          receipt.screenshotDevicePx = [screenshot.readUInt32BE(16), screenshot.readUInt32BE(20)]
          expect(receipt.screenshotDevicePx).toEqual([reflow.outerWidth, baseline.innerHeight])
          await writeFile(join(evidenceDirectory, 'zoom-400-overview.png'), screenshot)
        } finally {
          await screenshotSession.detach()
        }
      }

      receipt.phase = '400-percent-keyboard'
      const open = page.getByRole('button', { name: '打开导航' })
      await open.focus()
      await expect(open).toBeFocused()
      await page.keyboard.press('Enter')
      const drawer = page.getByRole('dialog', { name: '观测工作区' })
      const close = drawer.getByRole('button', { name: '关闭导航' })
      await expect(drawer).toHaveAttribute('aria-modal', 'true')
      await expect(drawer.getByRole('link', { name: '监测总览' })).toBeVisible()
      receipt.drawerFocus = await drawerFocusState(page)
      await expect(close).toBeFocused()
      receipt.targets.drawerClose = await userTarget(close)
      expect(receipt.targets.drawerClose.hits).toBe(true)
      await page.keyboard.press('Escape')
      await expect(open).toBeFocused()
      const legend = page.getByText('AQI 六级区间说明', { exact: true })
      await legend.evaluate(element => element.scrollIntoView({ block: 'center' }))
      await legend.press('Enter')
      await expect(page.locator('details.grade-legend').filter({ has: legend })).toHaveAttribute('open', '')
      await expect(legend).toBeFocused()
      receipt.targets.legend = await userTarget(legend)
      expect(receipt.targets.legend.hits).toBe(true)

      await tableRegion.focus()
      await expect(tableRegion).toBeFocused()
      await page.keyboard.press('ArrowRight')
      await expect.poll(() => tableRegion.evaluate(element => element.scrollLeft)).toBeGreaterThan(0)
      receipt.localScrollLeft = await tableRegion.evaluate(element => element.scrollLeft)
      expect(await page.evaluate(() => scrollX)).toBe(0)
      expect(receipt.network).toMatchObject({ external: 0, unknownApi: 0, badStatus: 0, failed: 0, consoleErrors: 0, pageErrors: 0 })
      receipt.phase = 'complete'
    } finally {
      await context.close()
    }
  } finally {
    await rm(profile, { recursive: true, force: true })
    if (evidenceDirectory) await writeFile(join(evidenceDirectory, 'zoom-layout.json'), JSON.stringify(receipt, null, 2))
  }
})
