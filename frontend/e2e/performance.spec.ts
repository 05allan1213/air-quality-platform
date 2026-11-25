import { expect, test, type Browser, type BrowserContext, type Page } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { arch, availableParallelism, platform, totalmem } from 'node:os'
import { join } from 'node:path'
import { browserSeed, type BrowserSeed } from './seed'

const samplesPerMode = 5
const viewports = { desktop: { width: 1440, height: 900 }, narrow: { width: 390, height: 844 } }
const chartHost = '[aria-labelledby="trend-title"] .trend-chart'
const canvasMarker = '_echarts_instance_'
const round = (value: number) => Math.round(value * 1000) / 1000

// Used by addInitScript, so dependencies must be local to the function.
function installTiming() {
  type LayoutShift = PerformanceEntry & { value: number; hadRecentInput: boolean }
  const tasks: PerformanceEntry[] = []
  const shifts: LayoutShift[] = []
  const observe = (type: string, add: (entries: PerformanceEntry[]) => void) => {
    if (!PerformanceObserver.supportedEntryTypes.includes(type)) return null
    const observer = new PerformanceObserver(list => add(list.getEntries()))
    observer.observe({ type, buffered: true })
    return () => add(observer.takeRecords())
  }
  const takeTasks = observe('longtask', entries => tasks.push(...entries))
  const takeShifts = observe('layout-shift', entries => shifts.push(...entries as LayoutShift[]))
  performance.setResourceTimingBufferSize(1000)

  let interactive: number | undefined
  let chart: number | undefined
  let toggleAt: number | undefined
  let toggleDraw: number | undefined
  const afterPaint = (stamp: (at: number) => void) => {
    requestAnimationFrame(() => requestAnimationFrame(stamp))
  }
  let waitingForContent = true
  let waitingForChart = true
  function ready() {
    const favorite = document.querySelector<HTMLButtonElement>('.detail-heading .heading-tools button')
    if (waitingForContent && favorite && !favorite.disabled && document.querySelector('.indicator-panel .indicator-result')) {
      waitingForContent = false
      afterPaint(at => { interactive = at })
    }
    const canvas = document.querySelector<HTMLCanvasElement>('[aria-labelledby="trend-title"] .trend-chart canvas')
    if (waitingForChart && canvas && canvas.width > 0 && canvas.height > 0 && canvas.getBoundingClientRect().width > 0) {
      waitingForChart = false
      afterPaint(at => { chart = at })
    }
    if (waitingForContent || waitingForChart) requestAnimationFrame(ready)
  }
  requestAnimationFrame(ready)
  document.addEventListener('click', event => {
    if (!(event.target instanceof Element) || !event.target.closest('[aria-labelledby="trend-title"] .trend-data summary')) return
    toggleAt = performance.now()
    afterPaint(at => { toggleDraw = at })
  }, { capture: true })

  const probe = {
    ready: () => interactive !== undefined && chart !== undefined,
    toggled: () => toggleDraw !== undefined,
    snapshot: () => {
      takeTasks?.()
      takeShifts?.()
      const byPeriod = (from: number, to: number) => {
        const inPeriod = (entry: PerformanceEntry) => entry.startTime >= from && entry.startTime < to
        const durations = tasks.filter(inPeriod).map(task => task.duration)
        const layout = shifts.filter(inPeriod)
        return {
          longTaskCount: takeTasks ? durations.length : null,
          longTaskTotalMs: takeTasks ? durations.reduce((sum, ms) => sum + ms, 0) : null,
          longestTaskMs: takeTasks ? Math.max(0, ...durations) : null,
          layoutShiftCount: takeShifts ? layout.length : null,
          layoutShiftSumWithoutRecentInput: takeShifts
            ? layout.filter(shift => !shift.hadRecentInput).reduce((sum, shift) => sum + shift.value, 0) : null,
        }
      }
      return {
        interactive: interactive!, chart: chart!, toggleMs: toggleDraw! - toggleAt!,
        initialRender: byPeriod(0, Math.max(interactive!, chart!)),
        toggleRender: byPeriod(toggleAt!, toggleDraw!),
      }
    },
  }
  Object.assign(window, { s08Performance: probe })
  return probe
}

declare global {
  interface Window {
    s08Performance: ReturnType<typeof installTiming>
    s08ObservedCharts?: Set<Element>
  }
}

type Storage = Awaited<ReturnType<BrowserContext['storageState']>>

async function authenticatedStorage(browser: Browser, baseURL: string, account: BrowserSeed['accounts']['reader']): Promise<Storage> {
  const context = await browser.newContext({ baseURL })
  try {
    const csrf = await context.request.get('/api/v1/auth/csrf/')
    expect(csrf.status()).toBe(200)
    const { csrf_token } = await csrf.json() as { csrf_token: string }
    const login = await context.request.post('/api/v1/auth/login/', {
      headers: { Origin: new URL(baseURL).origin, 'X-CSRFToken': csrf_token },
      data: { username: account.username, password: account.password },
    })
    expect(login.status()).toBe(200)
    return await context.storageState()
  } finally {
    await context.close()
  }
}

const endpoint = (seed: BrowserSeed) => `/api/v1/cities/${seed.city}/`
const cityPage = (seed: BrowserSeed) => `/app/cities/${seed.city}?${new URLSearchParams({
  scope: 'synthetic', product: String(seed.products.C), at: seed.snapshot_at,
})}`
const trendRegion = (page: Page) => page.getByRole('region', { name: '独立 T 城市趋势' })

async function sample(page: Page, seed: BrowserSeed) {
  const cPath = endpoint(seed)
  const tPath = `${cPath}trend/`
  const responseFor = (path: string) => page.waitForResponse(response => new URL(response.url()).pathname === path && response.request().method() === 'GET')
  const [city, trend, shell] = await Promise.all([
    responseFor(cPath), responseFor(tPath), page.goto(cityPage(seed), { waitUntil: 'domcontentloaded' }),
  ])
  expect(shell?.status()).toBe(200)
  expect(city.status()).toBe(200)
  expect(trend.status()).toBe(200)
  await page.waitForFunction(() => window.s08Performance.ready())
  await expect(page.getByRole('heading', { name: seed.cities[0].name, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '关注城市', exact: true })).toBeEnabled()
  const region = trendRegion(page)
  const chart = region.getByRole('img', { name: /独立 T 产品 24 小时 AQI 趋势/ })
  await expect(chart).toBeVisible()
  const chartScrollToPaintMs = await chart.evaluate(element => new Promise<number>(resolve => {
    const started = performance.now()
    element.scrollIntoView({ block: 'center', behavior: 'instant' })
    requestAnimationFrame(() => requestAnimationFrame(at => resolve(at - started)))
  }))
  await expect(chart).toBeInViewport()
  await region.getByText('查看 24 小时数据表', { exact: true }).click()
  await page.waitForFunction(() => window.s08Performance.toggled())

  // Keep data validation outside the interaction timing and never write response bodies.
  const cityData = await city.json() as {
    city: number; product: number; synthetic: boolean; snapshot_at: string
    slots: { indicator: string; results: { value: string | null; quality: string }[] }[]
  }
  expect(cityData).toMatchObject({ city: seed.city, product: seed.products.C, synthetic: true })
  expect(Date.parse(cityData.snapshot_at)).toBe(Date.parse(seed.snapshot_at))
  const cityAqi = cityData.slots.find(slot => slot.indicator === 'AQI')?.results[0]
  expect(cityAqi?.quality).toBe('valid')
  expect(Number(cityAqi?.value)).toBe(44)
  await expect(page.getByRole('region', { name: 'C 城市指标' }).locator('.indicator-row')
    .filter({ has: page.getByText('AQI', { exact: true }) }).locator('.metric-value')).toHaveText(String(cityAqi!.value))
  const points = await trend.json() as {
    city: number; product: number; synthetic: boolean
    points: { results: { indicator: string; value: string | null }[] }[]
  }
  expect(points).toMatchObject({ city: seed.city, product: seed.products.T, synthetic: true })
  expect(points.points).toHaveLength(24)
  const values = points.points.map(point => point.results.find(row => row.indicator === 'AQI')?.value ?? '无有效值')
  expect(values.filter(value => value === '无有效值')).toHaveLength(3)
  const chartRows = await region.getByRole('table').locator('tbody tr td:nth-child(2)').allTextContents()
  expect(chartRows).toEqual(values.map(String))
  await page.evaluate(() => document.fonts.ready.then(() => true))

  const timing = await page.evaluate(({ cPath, tPath }) => {
    const timing = window.s08Performance.snapshot()
    const [navigation] = performance.getEntriesByType('navigation') as PerformanceNavigationTiming[]
    const resources = (performance.getEntriesByType('resource') as PerformanceResourceTiming[])
      .filter(entry => new URL(entry.name).origin === location.origin)
    const byPath = (path: string) => resources.find(entry => new URL(entry.name).pathname === path)
    const c = byPath(cPath)
    const t = byPath(tPath)
    const transfer = (entry: PerformanceResourceTiming | undefined) => entry ? entry.responseEnd - entry.requestStart : null
    const count = (entries: PerformanceResourceTiming[]) => ({
      count: entries.length,
      transferBytes: entries.reduce((sum, entry) => sum + entry.transferSize, 0),
      encodedBytes: entries.reduce((sum, entry) => sum + entry.encodedBodySize, 0),
      decodedBytes: entries.reduce((sum, entry) => sum + entry.decodedBodySize, 0),
      localCacheCount: entries.filter(entry => entry.transferSize === 0 && entry.decodedBodySize > 0).length,
    })
    const assets = resources.filter(entry => new URL(entry.name).pathname.startsWith('/static/app/'))
    const byType = (pattern: RegExp) => assets.filter(entry => pattern.test(new URL(entry.name).pathname))
    return {
      navigationHttpMs: transfer(navigation), cityHttpMs: transfer(c), trendHttpMs: transfer(t),
      navigationToInteractiveMs: timing.interactive, navigationToChartMs: timing.chart,
      cityBodyEndToInteractiveMs: c ? timing.interactive - c.responseEnd : null,
      trendBodyEndToChartMs: t ? timing.chart - t.responseEnd : null,
      tableToggleToNextPaintMs: timing.toggleMs,
      initialRender: timing.initialRender, toggleRender: timing.toggleRender,
      resources: {
        sameOrigin: count([navigation, ...resources]), assets: count(assets),
        scripts: count(byType(/\.js$/)), styles: count(byType(/\.css$/)), fonts: count(byType(/\.woff2?$/)),
      },
    }
  }, { cPath, tPath })
  expect(timing.resources.scripts.count).toBeGreaterThan(0)
  for (const value of [timing.navigationHttpMs, timing.cityHttpMs, timing.trendHttpMs, timing.cityBodyEndToInteractiveMs, timing.trendBodyEndToChartMs, timing.tableToggleToNextPaintMs]) {
    expect(value).not.toBeNull()
    expect(value!).toBeGreaterThanOrEqual(0)
  }
  return { ...timing, chartScrollToPaintMs }
}

type Sample = Awaited<ReturnType<typeof sample>>

function distribution(series: (number | null)[]) {
  const points = series.filter((value): value is number => value !== null).sort((a, b) => a - b)
  const nearestRank = (position: number) => points.length ? round(points[Math.ceil(points.length * position) - 1]) : null
  return { n: points.length, p50: nearestRank(.5), p95: nearestRank(.95) }
}

function aggregate(samples: Sample[]) {
  const summarize = <T extends Record<string, number | null>>(rows: T[]) => Object.fromEntries(
    Object.keys(rows[0] ?? {}).map(key => [key, distribution(rows.map(row => row[key]))]),
  )
  const timingKeys = [
    'navigationHttpMs', 'cityHttpMs', 'trendHttpMs', 'navigationToInteractiveMs', 'navigationToChartMs',
    'cityBodyEndToInteractiveMs', 'trendBodyEndToChartMs', 'tableToggleToNextPaintMs', 'chartScrollToPaintMs',
  ] as const
  return {
    n: samples.length,
    timingMs: Object.fromEntries(timingKeys.map(key => [key, distribution(samples.map(row => row[key]))])),
    initialRender: summarize(samples.map(row => row.initialRender)),
    toggleRender: summarize(samples.map(row => row.toggleRender)),
    resources: Object.fromEntries((['sameOrigin', 'assets', 'scripts', 'styles', 'fonts'] as const)
      .map(key => [key, summarize(samples.map(row => row.resources[key]))])),
  }
}

async function lifecycle(context: BrowserContext, seed: BrowserSeed) {
  const page = await context.newPage()
  await page.addInitScript(() => {
    const active = new Set<Element>()
    window.s08ObservedCharts = active
    const Observer = window.ResizeObserver
    window.ResizeObserver = class extends Observer {
      private chart?: Element
      observe(target: Element, options?: ResizeObserverOptions) {
        super.observe(target, options)
        if (target.classList.contains('trend-chart')) { this.chart = target; active.add(target) }
      }
      unobserve(target: Element) {
        super.unobserve(target)
        if (target === this.chart) { active.delete(target); this.chart = undefined }
      }
      disconnect() {
        super.disconnect()
        if (this.chart) active.delete(this.chart)
        this.chart = undefined
      }
    }
  })
  const session = await context.newCDPSession(page)
  const snapshot = async () => {
    await session.send('HeapProfiler.collectGarbage')
    const heap = await session.send('Runtime.getHeapUsage')
    const dom = await session.send('Memory.getDOMCounters')
    return { heapUsedBytes: heap.usedSize, documents: dom.documents, nodes: dom.nodes, jsEventListeners: dom.jsEventListeners }
  }
  try {
    await page.goto(cityPage(seed))
    const records = []
    const origin = await page.evaluate(() => performance.timeOrigin)
    for (let cycle = 0; cycle < 3; cycle++) {
      const host = page.locator(chartHost)
      await expect(host.locator('canvas').first()).toBeVisible()
      const element = await host.elementHandle()
      const hadInstance = Boolean(await element.getAttribute(canvasMarker))
      const mounted = await snapshot()
      await page.getByRole('link', { name: '城市空气质量平台', exact: true }).click()
      await expect(page).toHaveURL(url => url.pathname === '/app/')
      await expect(host).toHaveCount(0)
      const release = await element.evaluate((node, key) => ({
        detached: !node.isConnected,
        instanceMarkerCleared: !node.getAttribute(key),
        canvasCleared: !node.querySelector('canvas'),
        resizeObserverDisconnected: window.s08ObservedCharts?.has(node) === false,
      }), canvasMarker)
      await element.dispose()
      expect(await page.evaluate(() => performance.timeOrigin)).toBe(origin)
      expect(hadInstance).toBe(true)
      expect(release).toEqual({ detached: true, instanceMarkerCleared: true, canvasCleared: true, resizeObserverDisconnected: true })
      const unmounted = await snapshot()
      records.push({ ...release, mounted, unmounted })
      await page.evaluate(() => history.back())
      await expect(page).toHaveURL(url => url.pathname === `/app/cities/${seed.city}`)
    }
    return records
  } finally {
    await session.detach()
    await page.close()
  }
}

for (const [label, viewport] of Object.entries(viewports)) {
  test(`production F city performance ${label}`, async ({ browser, browserName, baseURL }) => {
    test.setTimeout(90_000)
    expect(baseURL).toBeTruthy()
    const seed = browserSeed()
    const cold: Sample[] = []
    const warm: Sample[] = []
    let phase = 'authenticate'
    let lifecycles: Awaited<ReturnType<typeof lifecycle>> | null = null
    try {
      const storageState = await authenticatedStorage(browser, baseURL!, seed.accounts.reader)
      for (let index = 0; index < samplesPerMode; index++) {
        const context = await browser.newContext({ baseURL, storageState, viewport, deviceScaleFactor: 1, reducedMotion: 'reduce', locale: 'zh-CN', timezoneId: 'Asia/Shanghai' })
        try {
          await context.addInitScript(installTiming)
          phase = 'cold'
          const firstPage = await context.newPage()
          cold.push(await sample(firstPage, seed))
          phase = 'warm'
          const secondPage = await context.newPage()
          warm.push(await sample(secondPage, seed))
          await firstPage.close()
          await secondPage.close()
          if (index === samplesPerMode - 1) {
            phase = 'same-document chart unmount'
            lifecycles = await lifecycle(context, seed)
          }
        } finally {
          await context.close()
        }
      }
      expect(cold.every(row => row.resources.scripts.transferBytes > 0)).toBe(true)
      expect(warm.every(row => row.resources.assets.localCacheCount > 0)).toBe(true)
      phase = 'complete'
    } finally {
      const evidence = process.env.S08_EVIDENCE_DIR
      if (evidence) writeFileSync(join(evidence, `performance-city-${label}.json`), JSON.stringify({
        origin: 'F', fixture: seed.fixture_version, route: '/app/cities/:id', viewport, phase,
        engine: { name: browserName, version: browser.version(), headed: false, deviceScaleFactor: 1, reducedMotion: 'reduce' },
        host: { platform: platform(), arch: arch(), availableParallelism: availableParallelism(), memoryBytes: totalmem() },
        method: {
          samplesPerMode, percentile: 'Nearest rank. At n=5 P95 equals the largest sample.',
          cold: 'new browser context with a previously authenticated session; browser HTTP cache cold',
          warm: 'new document in the same context with hashed resource cache reuse checked',
          http: 'same-origin browser requestStart to responseEnd, includes complete response transfer',
          render: 'First usable C result and laid out T canvas, each followed by two animation frames. The canvas can be below the fold.',
          interaction: 'Scroll the chart into view and toggle its data table. Two animation frames are a paint proxy. Validate the 24 rows separately.',
          shift: 'Layout shifts by phase. The shift sum excludes entries with recent input.',
          heap: 'Forced-GC renderer counters at three same-document chart unmounts. GPU and whole-browser memory are unavailable.',
          observers: 'the lifetime probe checks the old instance marker, canvas and chart ResizeObserver',
          scope: 'Headless local production loopback F baseline without throttling.',
        },
        cacheReuse: { warmSamplesWithAssetCacheHit: warm.filter(row => row.resources.assets.localCacheCount > 0).length },
        cold: aggregate(cold), warm: aggregate(warm), lifecycles,
      }, null, 2))
    }
  })
}
