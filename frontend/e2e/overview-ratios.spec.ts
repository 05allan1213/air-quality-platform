import { expect, test, type CDPSession, type Locator, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { browserSeed } from './seed'
import type { NationalResponse } from '../src/monitor/HomeProjection'

const overview = '/api/v1/overview/national/'
const standard = 'HJ633-2012'
const labels = ['同刻覆盖 / 应到', '可排名 / 可汇总']
const evidence = process.env.S08_EVIDENCE_DIR

async function openOverview(page: Page, selection: URLSearchParams) {
  const [response, shell] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === overview),
    page.goto(`/app/?${selection}`, { waitUntil: 'domcontentloaded' }),
  ])
  expect(shell?.status()).toBe(200)
  expect(response.status()).toBe(200)
  const data = await response.json() as NationalResponse
  expect(data.synthetic).toBe(true)
  return data
}

async function checkReadout(groups: Locator[], summary: NationalResponse['summary'], rankStandard = standard) {
  const pairs = [
    [summary.covered_city_count, summary.expected],
    [summary.groups?.[rankStandard]?.ranked_city_count, summary.summary_eligible_city_count],
  ]
  for (const [index, group] of groups.entries()) {
    const [numerator, denominator] = pairs[index]
    const text = `${numerator ?? '—'} / ${denominator ?? '—'}`
    await expect(group.locator('.ratio-value')).toHaveText(text)
    const accessibility = await group.ariaSnapshot()
    expect(accessibility).toContain(labels[index])
    expect(accessibility).toContain(text)
    const hasProportion = numerator != null && denominator != null && denominator > 0
    await expect(group.locator('.ratio-arc')).toHaveCount(hasProportion ? 1 : 0)
  }
}

async function recordPaint(page: Page, cdp: CDPSession, name: string, close: boolean, labelsInView: boolean) {
  const geometry = await page.evaluate(() => {
    const rings = [...document.querySelectorAll<HTMLElement>('.ratio-ring')].map(ring => {
      const value = ring.querySelector<HTMLElement>('.ratio-value')!
      const text = document.createRange()
      text.selectNodeContents(value)
      const rect = (bounds: DOMRect) => ({
        x: bounds.x + window.scrollX, y: bounds.y + window.scrollY,
        width: bounds.width, height: bounds.height,
      })
      const bounds = value.getBoundingClientRect()
      return {
        ring: rect(ring.getBoundingClientRect()),
        text: rect(text.getBoundingClientRect()),
        hit: document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2) === value,
        color: getComputedStyle(value).color,
      }
    })
    return {
      rings, fontStatus: document.fonts.status, dpr: devicePixelRatio,
      viewport: { width: innerWidth, height: innerHeight, y: scrollY, landscape: matchMedia('(orientation: landscape)').matches },
      document: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight },
    }
  })
  expect(geometry.rings).toHaveLength(2)
  expect(geometry.document.width).toBeLessThanOrEqual(geometry.viewport.width)
  for (const { text, ring, hit } of geometry.rings) {
    expect(text.x).toBeGreaterThan(ring.x)
    expect(text.y).toBeGreaterThan(ring.y)
    expect(text.x + text.width).toBeLessThan(ring.x + ring.width)
    expect(text.y + text.height).toBeLessThan(ring.y + ring.height)
    expect(hit).toBe(labelsInView)
  }
  const top = Math.floor(Math.min(...geometry.rings.map(item => item.ring.y)))
  const bottom = Math.ceil(Math.max(...geometry.rings.map(item => item.ring.y + item.ring.height)))
  const clip = close
    ? { x: 0, y: Math.max(0, top - 12), width: geometry.document.width, height: bottom - top + 24, scale: 1 }
    : { x: 0, y: 0, ...geometry.document, scale: 1 }
  // CDP captures the page as painted while the local font requests are still paused.
  const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip }) as { data: string }
  const pixelCounts = await page.evaluate(async ({ data: png, clip: area, geometry: shot }) => {
    const pngBytes = Uint8Array.from(atob(png), character => character.charCodeAt(0))
    const bitmap = await createImageBitmap(new Blob([pngBytes], { type: 'image/png' }))
    const canvas = document.createElement('canvas')
    canvas.width = bitmap.width
    canvas.height = bitmap.height
    const context = canvas.getContext('2d')!
    context.drawImage(bitmap, 0, 0)
    bitmap.close()
    const xScale = canvas.width / area.width
    const yScale = canvas.height / area.height
    return shot.rings.map(({ text, color }) => {
      const expected = color.match(/\d+/g)!.slice(0, 3).map(Number)
      const { data: pixels } = context.getImageData(
        Math.floor((text.x - area.x) * xScale), Math.floor((text.y - area.y) * yScale),
        Math.ceil(text.width * xScale), Math.ceil(text.height * yScale),
      )
      let ink = 0
      for (let offset = 0; offset < pixels.length; offset += 4) {
        if (expected.every((channel, index) => Math.abs(pixels[offset + index] - channel) <= 35)) ink++
      }
      return ink
    })
  }, { data, clip, geometry })
  for (const count of pixelCounts) expect(count).toBeGreaterThan(20)
  if (evidence) writeFileSync(join(evidence, 'regressions', `overview-ratios-${name}.png`), Buffer.from(data, 'base64'))
  return { name, ...geometry, pixelCounts }
}

test('F overview retains both ratio readouts through slow fonts, scrolling, reflow and the solid theme', async ({ browser, page, baseURL }) => {
  const seed = browserSeed()
  const cdp = await page.context().newCDPSession(page)
  const origin = new URL(baseURL!).origin
  const selection = new URLSearchParams({ scope: 'synthetic', product: String(seed.products.L), at: seed.snapshot_at, standard_series: standard })
  const groups = labels.map(label => page.getByRole('group', { name: label, exact: true }))
  const api = new Set(['/api/v1/me/', '/api/v1/me/capabilities/', overview])
  const issues = { external: 0, unknownApi: 0, http: 0, pageError: 0, console: 0, failed: 0 }
  const captures: Awaited<ReturnType<typeof recordPaint>>[] = []
  let heldFonts = 0
  let allowFonts = () => {}
  const fontGate = new Promise<void>(resolve => { allowFonts = resolve })
  const csrf = await page.request.get('/api/v1/auth/csrf/')
  expect(csrf.status()).toBe(200)
  const { csrf_token } = await csrf.json() as { csrf_token: string }
  const session = await page.request.post('/api/v1/auth/login/', {
    headers: { Origin: origin, 'X-CSRFToken': csrf_token },
    data: { username: seed.accounts.reader.username, password: seed.accounts.reader.password },
  })
  expect(session.status()).toBe(200)
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) {
      issues.external++
      await route.abort()
      return
    }
    if (url.pathname.startsWith('/api/') && !api.has(url.pathname)) issues.unknownApi++
    if (route.request().resourceType() === 'font') {
      heldFonts++
      await fontGate
    }
    await route.continue()
  })
  page.on('response', response => { if (response.status() >= 400) issues.http++ })
  page.on('pageerror', () => { issues.pageError++ })
  page.on('console', message => { if (message.type() === 'error') issues.console++ })
  page.on('requestfailed', request => { if (request.failure()?.errorText !== 'net::ERR_ABORTED') issues.failed++ })
  if (evidence) mkdirSync(join(evidence, 'regressions'), { recursive: true })
  try {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    const { summary } = await openOverview(page, selection)
    expect(summary).toMatchObject({ state: 'ok', completeness: 'partial', covered_city_count: 51, expected: 52 })
    await checkReadout(groups, summary)
    await expect(groups[0]).toContainText('名录部分到报')
    await expect.poll(() => heldFonts).toBeGreaterThan(0)
    await expect.poll(() => page.evaluate(() => document.fonts.status)).toBe('loading')
    await page.addStyleTag({ content: '.account-link { visibility: hidden !important; }' })
    captures.push(await recordPaint(page, cdp, 'font-pending-full-390', false, true))
    captures.push(await recordPaint(page, cdp, 'font-pending-close-390', true, true))
    expect(captures.slice(0, 2).every(capture => capture.fontStatus === 'loading')).toBe(true)

    allowFonts()
    await page.evaluate(async () => { await document.fonts.ready })
    await expect.poll(() => page.evaluate(() => document.fonts.status)).toBe('loaded')
    captures.push(await recordPaint(page, cdp, 'font-ready-full-390', false, true))
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
    await expect.poll(() => page.evaluate(() => document.querySelector('.ratio-ring')!.getBoundingClientRect().bottom)).toBeLessThan(0)
    captures.push(await recordPaint(page, cdp, 'scrolled-away-full-390', false, false))
    await groups[0].scrollIntoViewIfNeeded()
    await checkReadout(groups, summary)
    captures.push(await recordPaint(page, cdp, 'scroll-return-close-390', true, true))

    await page.setViewportSize({ width: 320, height: 800 })
    await page.evaluate(() => window.scrollTo(0, 0))
    captures.push(await recordPaint(page, cdp, 'reflow-full-320', false, true))
    await page.setViewportSize({ width: 844, height: 390 })
    await groups[0].scrollIntoViewIfNeeded()
    await checkReadout(groups, summary)
    captures.push(await recordPaint(page, cdp, 'landscape-close-844', true, true))
    expect(captures.at(-1)?.viewport.landscape).toBe(true)

    const material = page.getByRole('button', { name: '实体主题' })
    await expect(material).toHaveAttribute('aria-pressed', 'false')
    await material.click()
    await expect(page.locator('.application')).toHaveAttribute('data-material', 'solid')
    await groups[0].scrollIntoViewIfNeeded()
    await checkReadout(groups, summary)
    captures.push(await recordPaint(page, cdp, 'solid-full-844', false, true))
    captures.push(await recordPaint(page, cdp, 'solid-close-844', true, true))

    const filtered = await openOverview(page, new URLSearchParams([...selection, ['grade', '2']]))
    expect(filtered.summary.expected).toBeNull()
    await checkReadout(groups, filtered.summary)
    await expect(groups[0]).toContainText('按等级筛选时应到未知')

    const absentSelection = new URLSearchParams(selection)
    absentSelection.set('at', new Date(Date.parse(seed.snapshot_at) - 3_600_000).toISOString())
    const absent = await openOverview(page, absentSelection)
    expect(absent.summary.summary_eligible_city_count).toBe(0)
    await checkReadout(groups, absent.summary)
    await expect(groups[1]).toContainText('无可汇总城市')
    expect(issues).toEqual({ external: 0, unknownApi: 0, http: 0, pageError: 0, console: 0, failed: 0 })
  } finally {
    allowFonts()
    await page.unrouteAll({ behavior: 'wait' })
    await cdp.detach()
    if (evidence) writeFileSync(join(evidence, 'regressions', 'overview-ratios.json'), JSON.stringify({
      origin: seed.origin, fixture: seed.fixture_version, snapshot: seed.snapshot_at, browser: browser.version(),
      platform: process.platform, heldFonts, issues, captures,
    }, null, 2))
  }
})
