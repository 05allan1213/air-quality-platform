import { expect, test, type APIResponse, type Page } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { browserSeed } from './seed'

const standard = 'HJ633-2012'
const nationalPath = '/api/v1/overview/national/'

function overviewEntry(page: Page) {
  const paths = new Map([
    ['/api/v1/me/', 'profile'], ['/api/v1/me/capabilities/', 'capabilities'], [nationalPath, 'national'],
  ])
  const entry = { shell: 0, csrf: 0, login: 0, failures: 0, pageErrors: 0, reads: [] as { endpoint: string; status: number }[] }
  page.on('response', response => {
    const endpoint = paths.get(new URL(response.url()).pathname)
    if (endpoint && entry.reads.length < 8) entry.reads.push({ endpoint, status: response.status() })
  })
  page.on('requestfailed', () => { entry.failures++ })
  page.on('pageerror', () => { entry.pageErrors++ })
  return entry
}

async function openOverview(page: Page, query: Record<string, string>, entry: ReturnType<typeof overviewEntry>) {
  const seed = browserSeed()
  const csrf = await page.request.get('/api/v1/auth/csrf/')
  entry.csrf = csrf.status()
  expect(entry.csrf).toBe(200)
  const { csrf_token } = await csrf.json() as { csrf_token: string }
  const login = await page.request.post('/api/v1/auth/login/', {
    headers: { Origin: new URL(csrf.url()).origin, 'X-CSRFToken': csrf_token },
    data: { username: seed.accounts.reader.username, password: seed.accounts.reader.password },
  })
  entry.login = login.status()
  expect(entry.login).toBe(200)
  const selection = new URLSearchParams({ scope: 'synthetic', product: String(seed.products.L), ...query })
  const shell = await page.goto(`/app/?${selection}`, { waitUntil: 'domcontentloaded' })
  entry.shell = shell?.status() ?? 0
  expect(entry.shell).toBe(200)
  await expect(page).toHaveURL(url => url.pathname === '/app/' && [...selection].every(([key, value]) => url.searchParams.get(key) === value))
  await expect(page.getByRole('heading', { name: '全国空气质量总览' })).toBeVisible()
  await expect(page.getByRole('table', { name: '城市空气质量列表' }).getByRole('button', { name: seed.cities[0].name, exact: true })).toBeVisible()
}

test('F national overview pins the anchor, keeps page and selection independent, and restores the return context', async ({ page }) => {
  const seed = browserSeed()
  const evidence = process.env.S08_EVIDENCE_DIR
  const entry = overviewEntry(page)
  const checkpoints: string[] = []
  const layout: { viewport: number; document: number; tableClient: number; tableScroll: number }[] = []
  const mask = [page.locator('.account-link')]
  const table = page.getByRole('table', { name: '城市空气质量列表' })
  const ranking = page.getByRole('region', { name: '城市 AQI 排名' })
  const summary = page.getByRole('region', { name: '全国观测摘要' })
  const north = table.getByRole('button', { name: seed.cities[0].name, exact: true })
  const shortDetail = page.locator('#home-selection')
  const anchorTime = (value: string | null) => Date.parse(value ?? '')
  try {
    await openOverview(page, { standard_series: standard }, entry)
    await expect(summary).toContainText('51 / 52')
    await expect(summary).toContainText('名录部分到报')
    await expect(page.getByTestId('distribution-base')).toContainText('51 城')
    await expect(page.getByText('地图未开放', { exact: true })).toBeVisible()
    await expect(page.getByText(`L 全国城市列表 · ${seed.source} · 产品 ${seed.products.L}`, { exact: true })).toBeVisible()
    if (evidence) await page.screenshot({ path: join(evidence, 'overview-1440.png'), mask })
    checkpoints.push('source_coverage_distribution')

    const nextRead = () => page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === nationalPath && url.searchParams.has('cursor') && response.status() === 200
    })
    const [next] = await Promise.all([nextRead(), page.getByRole('button', { name: '下一页' }).click()])
    const nextQuery = new URL(next.url()).searchParams
    expect(anchorTime(nextQuery.get('at'))).toBe(anchorTime(seed.snapshot_at))
    const pageTwoCursor = nextQuery.get('cursor')
    expect(pageTwoCursor).toBeTruthy()
    await expect(page).toHaveURL(url => !url.searchParams.has('cursor') && anchorTime(url.searchParams.get('at')) === anchorTime(seed.snapshot_at))
    await expect(page.getByText('第 2 页 · 25 城', { exact: true })).toBeVisible()
    const rowsOnPageTwo = await table.getByRole('rowheader').allTextContents()
    expect(rowsOnPageTwo).toHaveLength(25)
    await expect(north).toHaveCount(0)
    checkpoints.push('exact_anchor_and_session_cursor')

    const [low] = await Promise.all([nextRead(), ranking.getByRole('button', { name: 'AQI 较低' }).click()])
    expect(new URL(low.url()).searchParams.get('cursor') === pageTwoCursor).toBe(true)
    const ranked = ranking.locator(`#home-rank-${seed.city}`)
    await expect(ranked).toBeVisible()
    await expect(ranked).toContainText(seed.cities[0].name)
    await ranked.press('Enter')
    await expect(shortDetail).toHaveAttribute('data-city', String(seed.city))
    await expect(ranked).toHaveAttribute('aria-current', 'true')
    const chart = shortDetail.getByRole('img', { name: /独立 T 产品 24 小时 AQI 趋势/ })
    await expect(chart).toBeVisible()
    await shortDetail.getByText('查看 24 小时数据表').press('Enter')
    await expect(shortDetail.getByRole('row')).toHaveCount(25)
    expect(await table.getByRole('rowheader').allTextContents()).toEqual(rowsOnPageTwo)
    await expect(north).toHaveCount(0)
    await table.getByRole('rowheader').first().hover()
    await expect(shortDetail).toHaveAttribute('data-city', String(seed.city))
    await expect(page).toHaveURL(url => url.searchParams.get('focus_city') === String(seed.city))
    await shortDetail.getByRole('button', { name: '关闭城市概览' }).press('Enter')
    await expect(shortDetail).toHaveCount(0)
    await expect(ranked).toBeFocused()
    await ranked.click()
    await expect(shortDetail).toHaveAttribute('data-city', String(seed.city))
    checkpoints.push('off_page_rank_selection_hover_and_keyboard')

    await shortDetail.getByRole('button', { name: '查看选中城市' }).click()
    await expect(page.getByRole('heading', { name: 'C 城市指标' })).toBeVisible()
    await page.getByRole('button', { name: '返回上页' }).click()
    await expect(page.getByText('第 2 页 · 25 城', { exact: true })).toBeVisible()
    await expect(shortDetail).toHaveAttribute('data-city', String(seed.city))
    await expect(shortDetail.getByRole('button', { name: '查看选中城市' })).toBeFocused()
    expect(await table.getByRole('rowheader').allTextContents()).toEqual(rowsOnPageTwo)
    await page.getByRole('button', { name: '上一页' }).click()
    await expect(north).toHaveAttribute('aria-pressed', 'true')
    await page.reload()
    await expect(north).toHaveAttribute('aria-pressed', 'true')
    await expect(page).toHaveURL(url => anchorTime(url.searchParams.get('at')) === anchorTime(seed.snapshot_at))
    checkpoints.push('city_drilldown_back_and_reload')

    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 })
      const legend = page.getByText('AQI 六级区间说明', { exact: true })
      if (!await legend.evaluate(el => (el.parentElement as HTMLDetailsElement).open)) await legend.press('Enter')
      await expect(page.getByText('0–50', { exact: true })).toBeVisible()
      await expect(page.getByText('> 300', { exact: true })).toBeVisible()
      const dimensions = await page.getByLabel('城市空气质量表，可横向滚动').evaluate(element => ({
        viewport: window.innerWidth, document: document.documentElement.scrollWidth,
        tableClient: element.clientWidth, tableScroll: element.scrollWidth,
      }))
      layout.push(dimensions)
      expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport)
      expect(dimensions.tableClient).toBeLessThan(dimensions.tableScroll)
      if (evidence) await page.screenshot({ path: join(evidence, `overview-legend-${width}.png`), mask })
    }
    checkpoints.push('narrow_legend_and_local_scroll')

    const region = page.getByLabel('地域', { exact: true })
    await region.fill('south', { timeout: 10_000 })
    const readWith = (key: string, value: string) => page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === nationalPath && url.searchParams.get(key) === value
    }, { timeout: 20_000 })
    const [regionRead] = await Promise.all([readWith('geography', 'south'), page.getByRole('button', { name: '应用检索' }).press('Enter', { timeout: 10_000 })])
    expect(regionRead.status()).toBe(200)
    checkpoints.push('applied_geography_filter')
    await expect(page.getByText('所选城市已不符合当前条件或无法显示。')).toBeVisible()
    await expect(shortDetail).toHaveCount(0)
    await expect(page).toHaveURL(url => !url.searchParams.has('focus_city'))
    checkpoints.push('excluded_focus_cleared')
    const [gradeRead] = await Promise.all([readWith('grade', '3'), page.getByLabel('AQI 等级', { exact: true }).selectOption('3', { timeout: 10_000 })])
    expect(gradeRead.status()).toBe(200)
    checkpoints.push('applied_grade_filter')
    const filtered = (await gradeRead.json()).summary
    expect(filtered).toMatchObject({ expected: null, completeness: 'unknown' })
    const coverage = summary.getByRole('group', { name: '同刻覆盖 / 应到' })
    await expect(coverage).toContainText(`${filtered.covered_city_count} / —`)
    await expect(coverage).toContainText('按等级筛选时应到未知')
    await expect(page.getByTestId('distribution-base')).toContainText(standard)
    await page.getByRole('button', { name: '清除筛选' }).click({ timeout: 10_000 })
    await expect(north).toBeVisible()
    await expect(ranking).toContainText('不跨标准混排')
    await expect(page.getByLabel('地域', { exact: true })).toHaveValue('')
    checkpoints.push('applied_filter_excludes_selection_and_reset', 'complete')
  } finally {
    if (evidence) writeFileSync(join(evidence, 'overview-flow.json'), JSON.stringify({ origin: 'F', entry, checkpoints, layout }, null, 2))
  }
})

test('an old real T response cannot repopulate the overview after a different city is selected', async ({ page }) => {
  const seed = browserSeed()
  const evidence = process.env.S08_EVIDENCE_DIR
  const entry = overviewEntry(page)
  let complete = false
  let release: (() => void) | undefined
  let upstream: APIResponse | undefined
  let oldRequestCancelled = false
  let oldRouteCompleted = false
  const delayedPath = `/api/v1/cities/${seed.city}/trend/`
  const gate = new Promise<void>(resolve => { release = resolve })
  page.on('requestfailed', request => { if (new URL(request.url()).pathname === delayedPath) oldRequestCancelled = true })
  await page.route(url => url.pathname === delayedPath, async route => {
    upstream = await route.fetch()
    await gate
    await route.fulfill({ response: upstream }).catch(() => { /* A canceled browser read may no longer have a receiver. */ })
    oldRouteCompleted = true
  }, { times: 1 })
  try {
    await openOverview(page, { at: seed.snapshot_at, standard_series: standard }, entry)
    const table = page.getByRole('table', { name: '城市空气质量列表' })
    const shortDetail = page.locator('#home-selection')
    await table.getByRole('button', { name: seed.cities[0].name, exact: true }).click()
    await expect(shortDetail).toHaveAttribute('data-city', String(seed.city))
    await expect.poll(() => upstream?.status()).toBe(200)
    await expect(shortDetail.getByText('正在读取独立 T 趋势…', { exact: true })).toBeVisible()
    await table.getByRole('button', { name: seed.cities[1].name, exact: true }).click()
    await expect(shortDetail).toHaveAttribute('data-city', String(seed.cities[1].id))
    await expect(shortDetail.getByText(/(没有可用的获准 T 趋势|没有可显示的有效 AQI|当前选中城市的 T 趋势不可用)/)).toBeVisible()
    release?.()
    await expect.poll(() => oldRequestCancelled).toBe(true)
    await expect.poll(() => oldRouteCompleted).toBe(true)
    await expect(shortDetail.getByRole('img', { name: /独立 T 产品/ })).toHaveCount(0)
    await expect(page).toHaveURL(url => url.searchParams.get('focus_city') === String(seed.cities[1].id))
    complete = true
  } finally {
    release?.()
    if (evidence) writeFileSync(join(evidence, 'overview-trend-race.json'), JSON.stringify({ origin: 'F', entry, upstream: upstream?.status(), oldRequestCancelled, oldRouteCompleted, complete }))
  }
})
