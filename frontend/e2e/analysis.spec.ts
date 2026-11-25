import { expect, test, type Page, type Request } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { browserSeed } from './seed'
import { foregroundPair, switchAway } from './tabFocus'

async function signIn(page: Page, username: string, password: string) {
  await page.goto(`/app/login?${new URLSearchParams({ next: '/analysis?scope=synthetic' })}`)
  await page.getByRole('heading', { name: '登录' }).waitFor()
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL('/app/analysis?scope=synthetic')
}

function apiResponse(page: Page, path: string, method?: string) {
  return page.waitForResponse(response => {
    const request = response.request()
    return new URL(response.url()).pathname === path && (!method || request.method() === method)
  })
}

function safeApiPath(path: string) {
  return path.replace(/\/(?:\d+|[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12})(?=\/)/gi, '/:id')
}

async function setWindow(page: Page, seed: { start: string; end: string }, product: number) {
  await page.getByLabel('起始时间（UTC）').fill(seed.start.slice(0, 16))
  await page.getByLabel('结束时间（UTC，不含）').fill(seed.end.slice(0, 16))
  await page.getByLabel('产品编号（可选）').fill(String(product))
}

async function discover(page: Page) {
  const response = apiResponse(page, '/api/v1/catalog/series/')
  await page.getByRole('button', { name: '发现可选系列' }).click()
  const result = await response
  expect(result.status()).toBe(200)
  await expect(page.getByRole('heading', { name: '获准分析的系列' })).toBeVisible()
}

async function analysisLayout(page: Page, width: number) {
  await page.waitForFunction(expected => window.innerWidth === expected && window.matchMedia('(max-width: 600px)').matches, width)
  return page.evaluate(async () => {
    // Let resize-triggered layout and chart updates finish before reading geometry.
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    const viewport = document.documentElement.clientWidth
    const section = document.querySelector('.analysis-section')
    const regions = [
      ['shell', '.topbar'], ['workspace', '#main-content'], ['analysis', '.analysis-page'],
      ['methods', '.analysis-modes'], ['discovery', '.analysis-search'],
      ['discovery-fields', '.analysis-search input, .analysis-search select'], ['catalog', '.analysis-directory'],
      ['result', '.analysis-result'], ['result-sections', '.analysis-result-section'],
      ['coverage-tables', '.analysis-table-scroll'], ['paired-metrics', '.analysis-metrics'],
      ['chart', '.analysis-chart-host'], ['save-fields', '.analysis-save'],
    ]
    return {
      viewport,
      innerWidth: window.innerWidth,
      compact: window.matchMedia('(max-width: 600px)').matches,
      sectionPaddingInline: section ? getComputedStyle(section).paddingInlineStart : null,
      document: document.documentElement.scrollWidth,
      regions: regions.flatMap(([region, selector]) => Array.from(document.querySelectorAll(selector), element => {
        const box = element.getBoundingClientRect()
        return { region, left: Math.floor(box.left), right: Math.ceil(box.right), client: element.clientWidth, scroll: element.scrollWidth }
      })),
    }
  })
}

test('research statistics, saved rerun and whole CSV, paired comparison, and Pearson limits', async ({ page }) => {
  const seed = browserSeed()
  const evidence = process.env.S08_EVIDENCE_DIR
  const requests: { stage: string; path: string; method: string; status?: number | 'failed' }[] = []
  const detailBodies: { hasParent: boolean; hasInputVersion: boolean; support?: string; hasCursor: boolean; urlHasVersion: boolean }[] = []
  const requestMap = new Map<Request, typeof requests[number]>()
  const pageWidths: Awaited<ReturnType<typeof analysisLayout>>[] = []
  const pageErrors: { stage: string; kind: 'pageerror' | 'console.error'; source?: 'favicon' | 'api' | 'asset' | 'other' }[] = []
  let wholeExport: { preview: number; ticket: number; download: number; rows: number; matchesParentVersion: boolean } | undefined
  let stage = 'session'
  page.on('request', request => {
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/v1/')) return
    const entry = { stage, path: safeApiPath(url.pathname), method: request.method() }
    requests.push(entry)
    requestMap.set(request, entry)
    if (url.pathname === '/api/v1/analysis/details/' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>
      detailBodies.push({
        hasParent: Boolean(body.selection && body.method && body.method_version && body.grain),
        hasInputVersion: typeof body.input_version === 'string' && /^[a-f\d]{64}$/.test(body.input_version),
        support: typeof body.support === 'string' ? body.support : undefined,
        hasCursor: typeof body.cursor === 'string',
        urlHasVersion: url.searchParams.has('input_version'),
      })
    }
  })
  page.on('response', response => {
    const entry = requestMap.get(response.request())
    if (entry) entry.status = response.status()
  })
  page.on('requestfailed', request => {
    const entry = requestMap.get(request)
    if (entry && entry.status === undefined) entry.status = 'failed'
  })
  page.on('pageerror', () => pageErrors.push({ stage, kind: 'pageerror' }))
  page.on('console', message => {
    if (message.type() !== 'error') return
    const path = URL.parse(message.location().url)?.pathname ?? ''
    const source = path === '/favicon.ico' ? 'favicon' : path.startsWith('/api/v1/') ? 'api'
      : path.startsWith('/static/') || path.startsWith('/src/') ? 'asset' : 'other'
    pageErrors.push({ stage, kind: 'console.error', source })
  })

  try {
    await signIn(page, seed.accounts.reader.username, seed.accounts.reader.password)
    await expect(page.getByRole('heading', { name: '分析工作台' })).toBeVisible()

    stage = 'synthetic single-series statistics'
    await setWindow(page, seed, seed.products.C)
    await discover(page)
    const cityCo = page.getByRole('checkbox', { name: new RegExp(`C 城市 #${seed.city} · CO · 产品 #${seed.products.C}`) })
    await expect(cityCo).toBeEnabled()
    await cityCo.check()
    const statistics = apiResponse(page, '/api/v1/analysis/statistics/', 'POST')
    await page.getByRole('button', { name: '运行分析' }).click()
    const statisticsResponse = await statistics
    expect(statisticsResponse.status()).toBe(200)
    const statisticsBody = await statisticsResponse.json()
    expect(statisticsBody.method).toBe('statistics')
    expect(statisticsBody.selection.scope).toBe('synthetic')
    expect(statisticsBody.columns).toHaveLength(1)
    expect(statisticsBody.columns[0]).toMatchObject({ E: 24, O: 24, V: 24, denominator_state: 'known' })
    expect(statisticsBody.chart.resolution).toBe('exact')
    expect(statisticsBody.distribution.reduce((sum: number, bin: { n: number }) => sum + bin.n, 0)).toBe(24)
    await expect(page.getByRole('heading', { name: '分析结果' })).toBeVisible()
    await expect(page.getByRole('img', { name: '时间趋势' })).toBeVisible()
    await page.getByText('查看时间趋势数据表').press('Enter')
    const trendTable = page.getByRole('region', { name: '时间趋势数据表' })
    await expect(trendTable.getByRole('row')).toHaveCount(25)
    await page.getByRole('button', { name: '样本分布' }).click()
    await page.getByText('查看样本分布数据表').press('Enter')
    await expect(page.getByRole('region', { name: '样本分布数据表' }).getByRole('row')).toHaveCount(9)

    stage = 'same statistics intent bucket support to last page'
    const bucket = statisticsBody.buckets[0]
    expect(bucket.V).toBeGreaterThan(20)
    const supportRead = apiResponse(page, '/api/v1/analysis/details/', 'POST')
    await page.getByRole('region', { name: '统计分桶表' }).getByRole('button', { name: '有效样本', exact: true }).first().click()
    const supportResponse = await supportRead
    expect(supportResponse.status()).toBe(200)
    expect(supportResponse.request().postDataJSON()).toMatchObject({
      method: 'statistics', selection: statisticsBody.selection, input_version: statisticsBody.input_version,
      ...bucket.drilldown,
    })
    const supportSection = page.getByRole('region', { name: '支撑记录', exact: true })
    await expect(supportSection.getByRole('row')).toHaveCount(21)
    const lastSupport = apiResponse(page, '/api/v1/analysis/details/', 'POST')
    await supportSection.getByRole('button', { name: '下一页' }).click()
    const lastSupportResponse = await lastSupport
    expect(lastSupportResponse.status()).toBe(200)
    expect(lastSupportResponse.request().postDataJSON()).toMatchObject({
      input_version: statisticsBody.input_version, cursor: (await supportResponse.json()).page.next_cursor,
    })
    await expect(supportSection.getByRole('row')).toHaveCount(bucket.V - 20 + 1)
    await expect(supportSection.getByText('已到最后一页')).toBeVisible()
    await supportSection.getByRole('button', { name: '关闭支撑记录' }).click()

    stage = 'save statistics intent'
    await page.getByLabel('方案名称').fill('F 统计版本复访')
    const saving = apiResponse(page, '/api/v1/me/saved-queries/', 'POST')
    await page.getByRole('button', { name: '保存方案' }).click()
    const savedResponse = await saving
    expect(savedResponse.status()).toBe(201)
    const saved = await savedResponse.json()
    expect(saved).toMatchObject({
      id: expect.stringMatching(/^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/),
      title: 'F 统计版本复访', method: 'statistics', selection: statisticsBody.selection,
    })
    await expect(page.getByRole('status').filter({ hasText: '已保存「F 统计版本复访」' })).toBeVisible()

    stage = 'rerun saved statistics intent'
    const rerunning = apiResponse(page, `/api/v1/me/saved-queries/${saved.id}/run/`, 'POST')
    await page.getByRole('button', { name: '重跑已保存方案' }).click()
    const rerunResponse = await rerunning
    expect(rerunResponse.status()).toBe(200)
    expect(new URL(rerunResponse.url()).searchParams.get('scope')).toBe('synthetic')
    const rerunBody = await rerunResponse.json()
    expect(rerunBody.state).toMatch(/^(current|data_changed|baseline_unavailable)$/)
    expect(rerunBody.result).toMatchObject({
      input_version: expect.stringMatching(/^[a-f\d]{64}$/),
      method: saved.method, selection: saved.selection,
    })
    await expect(page.getByRole('status').filter({ hasText: '已按当前权限重跑' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '分析结果' })).toBeVisible()

    stage = 'handoff applied statistics intent to whole detail'
    const selection = rerunBody.result.selection
    const scopedCatalog = apiResponse(page, '/api/v1/catalog/objects/', 'GET')
    await page.getByRole('link', { name: '打开整组明细' }).press('Enter')
    await expect(page.getByRole('heading', { name: '数据查询' })).toBeVisible()
    const handoff = new URL(page.url())
    expect(handoff.pathname).toBe('/app/query')
    expect(Object.fromEntries(handoff.searchParams)).toEqual({
      intent: 'whole', scope: 'synthetic', product: String(seed.products.C), source: seed.source, city: String(seed.city),
      indicator: 'CO', unit: 'mg/m³', standard_series: 'not_applicable', report_kind: selection.report_kind,
      window_definition: selection.window_definition, start: selection.start, end: selection.end,
    })
    const catalogResponse = await scopedCatalog
    expect(catalogResponse.status()).toBe(200)
    const directoryParams = new URL(catalogResponse.url()).searchParams
    expect(directoryParams.get('scope')).toBe('synthetic')
    expect(directoryParams.get('product')).toBe(String(seed.products.C))
    await expect(page.getByText('已带入分析条件，请核对并应用整组明细查询。')).toBeVisible()
    await expect(page.getByLabel('来源产品与对象')).toHaveValue(`${seed.products.C}:${seed.city}`)
    await expect(page.getByRole('radio', { name: '整组明细' })).toBeChecked()
    await expect(page.getByRole('checkbox', { name: 'CO mg/m³', exact: true })).toBeChecked()
    await expect(page.getByLabel('开始时刻（含）')).toHaveValue(selection.start)
    await expect(page.getByLabel('结束时刻（不含）')).toHaveValue(selection.end)
    expect(requests.some(request => request.stage === stage && request.path === '/api/v1/analysis/details/')).toBe(false)

    stage = 'fresh whole-detail parent and explicit CSV confirmation'
    const previewing = apiResponse(page, '/api/v1/analysis/details/', 'POST')
    await page.getByRole('button', { name: '应用查询' }).press('Enter')
    const previewResponse = await previewing
    expect(previewResponse.status()).toBe(200)
    expect(previewResponse.request().postDataJSON()).toEqual({
      method: 'detail', method_version: rerunBody.result.method_version, grain: 'hour', selection,
      support: 'observed', page_size: 2,
    })
    const preview = await previewResponse.json()
    expect(preview).toMatchObject({ method: 'detail', selection, input_version: expect.stringMatching(/^[a-f\d]{64}$/) })
    expect(preview.results).toHaveLength(2)
    await page.getByRole('button', { name: '确认导出范围' }).press('Enter')
    const confirmation = page.getByRole('region', { name: '确认整组 CSV' })
    await expect(confirmation.getByRole('heading')).toBeFocused()
    await expect(confirmation).toContainText('CO · mg/m³ · not_applicable')
    await expect(confirmation).toContainText(preview.input_version)
    expect(requests.some(request => request.path === '/api/v1/me/exports/' && request.method === 'POST')).toBe(false)

    stage = 'authorized whole CSV ticket and download'
    const issuing = apiResponse(page, '/api/v1/me/exports/', 'POST')
    await confirmation.getByRole('button', { name: '确认并申请 CSV 票据' }).press('Enter')
    const ticketResponse = await issuing
    expect(ticketResponse.status()).toBe(201)
    expect(ticketResponse.request().postDataJSON()).toEqual({
      kind: 'detail', fields: [], groups: [{ method: preview.method, method_version: preview.method_version,
        grain: preview.grain, selection: preview.selection, input_version: preview.input_version }],
    })
    const ticket = await ticketResponse.json()
    const csvRequest = apiResponse(page, `/api/v1/me/exports/${ticket.id}/download/`, 'GET')
    const downloading = page.waitForEvent('download')
    await confirmation.getByRole('button', { name: '下载 CSV' }).press('Enter')
    const csvResponse = await csvRequest
    expect(csvResponse.status()).toBe(200)
    expect(new URL(csvResponse.url()).searchParams.get('scope')).toBe('synthetic')
    expect(csvResponse.headers()['content-type']).toMatch(/^text\/csv/)
    const downloaded = await downloading
    expect(downloaded.suggestedFilename()).toBe('air-quality.csv')
    const stream = await downloaded.createReadStream()
    const chunks: Buffer[] = []
    for await (const chunk of stream) chunks.push(Buffer.from(chunk))
    // The exporter uses CRLF for records and LF between attributions inside a quoted cell.
    const [header, ...rows] = Buffer.concat(chunks).toString('utf8').trimEnd().split('\r\n')
    expect(header.split(',')).toContain('input_version')
    expect(rows).toHaveLength(24)
    expect(rows.every(row => row.split(',').includes(preview.input_version))).toBe(true)
    wholeExport = { preview: previewResponse.status(), ticket: ticketResponse.status(), download: csvResponse.status(), rows: rows.length, matchesParentVersion: true }

    stage = 'paired stations with a stable same-city site-epoch pair'
    await page.goBack()
    await page.getByRole('button', { name: '对象对比' }).click()
    await setWindow(page, seed, seed.products.S)
    await discover(page)
    const first = page.getByRole('checkbox', { name: new RegExp(`S 站址期 #${seed.paired_site_epochs[0]} · CO · 产品 #${seed.products.S}`) })
    const second = page.getByRole('checkbox', { name: new RegExp(`S 站址期 #${seed.paired_site_epochs[1]} · CO · 产品 #${seed.products.S}`) })
    await first.check()
    await second.check()
    const compareRequest = apiResponse(page, '/api/v1/analysis/compare/', 'POST')
    await page.getByRole('button', { name: '运行分析' }).click()
    const compareResponse = await compareRequest
    expect(compareResponse.status()).toBe(200)
    const compareBody = await compareResponse.json()
    expect(compareBody.selection.site_epochs).toEqual([...seed.paired_site_epochs])
    expect(compareBody.columns.map((column: { O: number }) => column.O)).toEqual([18, 18])
    expect(compareBody).toMatchObject({ E_common: 24, P: 12, actual_pairs: 12, ranking: null, mean_difference: null })
    await expect(page.getByText('当前条件无整期排名。有效配对与各列覆盖可独立核查。')).toBeVisible()
    await expect(page.getByRole('link', { name: '打开整组明细' })).toHaveCount(0)
    await page.getByRole('button', { name: '查看配对明细' }).click()
    const detailsTable = page.getByRole('region', { name: '支撑记录表' })
    await expect(detailsTable.getByRole('row')).toHaveCount(21)
    expect(detailBodies.at(-1)).toMatchObject({ hasParent: true, hasInputVersion: true, support: 'paired', hasCursor: false, urlHasVersion: false })
    await page.getByRole('button', { name: '下一页' }).click()
    await expect(page.getByText('共同样本 · 共同有效样本 · 第 2 页')).toBeVisible()
    await expect(detailsTable.getByRole('row')).toHaveCount(5)
    expect(detailBodies.at(-1)).toMatchObject({ hasParent: true, hasInputVersion: true, support: 'paired', hasCursor: true, urlHasVersion: false })
    await expect(page.getByRole('button', { name: '下一页' })).toHaveCount(0)

    stage = 'Pearson minimum-sample state'
    await page.getByRole('button', { name: '指标关联' }).click()
    await setWindow(page, seed, seed.products.C)
    await discover(page)
    await page.getByRole('checkbox', { name: new RegExp(`C 城市 #${seed.city} · CO · 产品 #${seed.products.C}`) }).check()
    await page.getByRole('checkbox', { name: new RegExp(`C 城市 #${seed.city} · PM2[.]5 · 产品 #${seed.products.C}`) }).check()
    const pearsonRequest = apiResponse(page, '/api/v1/analysis/statistics/', 'POST')
    await page.getByRole('button', { name: '运行分析' }).click()
    const pearsonResponse = await pearsonRequest
    expect(pearsonResponse.status()).toBe(200)
    const pearsonBody = await pearsonResponse.json()
    expect(pearsonBody.method).toBe('pearson')
    expect(pearsonBody).toMatchObject({ E_common: 24, P: 24, actual_pairs: 24, pearson: null, conclusion: { state: 'insufficient_pairs' } })
    await expect(page.getByRole('status').filter({ hasText: '有效配对少于 30' })).toBeVisible()
    stage = 'narrow layout'
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 })
      const layout = await analysisLayout(page, width)
      pageWidths.push(layout)
      expect(layout.document).toBeLessThanOrEqual(layout.viewport)
      const table = page.getByRole('region', { name: '样本与覆盖表', exact: true })
      await table.focus()
      await expect(table).toBeFocused()
      expect(await table.evaluate(element => element.scrollWidth)).toBeGreaterThan(await table.evaluate(element => element.clientWidth))
    }
    if (evidence) {
      await page.setViewportSize({ width: 1440, height: 900 })
      const screenshotOptions = { fullPage: true, mask: [page.locator('.account-link')] }
      await page.screenshot({ path: join(evidence, 'analysis-pearson-1440.png'), ...screenshotOptions })
      await page.setViewportSize({ width: 390, height: 844 })
      await page.screenshot({ path: join(evidence, 'analysis-pearson-390.png'), ...screenshotOptions })
    }
    expect(detailBodies.map(body => body.support)).toEqual(['effective', 'effective', 'observed', 'paired', 'paired'])
    expect(requests.filter(request => typeof request.status === 'number' && request.status >= 400)).toEqual([])
    expect(pageErrors).toEqual([])
    stage = 'complete'
  } finally {
    if (evidence) writeFileSync(join(evidence, 'analysis-flow.json'), JSON.stringify({ origin: 'F', stage, requests, pageWidths, pageErrors, wholeExport }, null, 2))
  }
})

test('annual monthly and winter statistics find a later catalog slice and show only granted derived data', async ({ page }) => {
  const seed = browserSeed()
  const sample = seed.research.calendar
  const evidence = process.env.S08_EVIDENCE_DIR
  await signIn(page, seed.accounts.reader.username, seed.accounts.reader.password)
  await page.getByLabel('分桶粒度').selectOption('month')
  await setWindow(page, sample, sample.product)
  await page.getByLabel('指标筛选').selectOption('CO')
  const firstSearch = apiResponse(page, '/api/v1/catalog/series/')
  await page.getByRole('button', { name: '发现可选系列' }).click()
  const empty = await firstSearch
  expect(empty.status()).toBe(200)
  const firstBounds = new URL(empty.url()).searchParams
  expect(Date.parse(firstBounds.get('start')!)).toBe(Date.parse(sample.start))
  expect(Date.parse(firstBounds.get('end')!) - Date.parse(firstBounds.get('start')!)).toBe(30 * 86_400_000)
  expect((await empty.json()).results).toEqual([])
  await expect(page.getByRole('status').filter({ hasText: '当前条件下没有可选系列' })).toBeVisible()

  let catalogRequests = 1
  async function runCalendar(grain: 'month' | 'season') {
    await page.getByLabel('分桶粒度').selectOption(grain)
    await page.getByLabel('系列发现起点（UTC，可选）').fill(sample.discovery_start.slice(0, 16))
    const search = apiResponse(page, '/api/v1/catalog/series/')
    await page.getByRole('button', { name: '发现可选系列' }).click()
    const discovery = await search
    expect(discovery.status()).toBe(200)
    catalogRequests += 1
    const slice = new URL(discovery.url()).searchParams
    expect(Date.parse(slice.get('start')!)).toBe(Date.parse(sample.discovery_start))
    expect(Date.parse(slice.get('end')!) - Date.parse(slice.get('start')!)).toBe(30 * 86_400_000)
    await page.getByRole('checkbox', { name: new RegExp(`C 城市 #${sample.city} · CO · 产品 #${sample.product}`) }).check()
    const running = apiResponse(page, '/api/v1/analysis/statistics/', 'POST')
    await page.getByRole('button', { name: '运行分析' }).click()
    const response = await running
    expect(response.status()).toBe(200)
    const sent = response.request().postDataJSON()
    expect(Date.parse(sent.selection.start)).toBe(Date.parse(sample.start))
    expect(Date.parse(sent.selection.end)).toBe(Date.parse(sample.end))
    const report = await response.json()
    expect(report).toMatchObject({ state: 'ok', grain, source_timezone: 'Asia/Shanghai', columns: [{ E: 8784, O: 3, V: 3 }] })
    expect(report).not.toHaveProperty('chart')
    expect(report.anomalies).not.toHaveProperty('candidates')
    await expect(page.getByRole('heading', { name: '分析结果', exact: true })).toBeVisible()
    await expect(page.getByText('当前结果未提供原始序列和支撑记录。可查看获准的派生统计。')).toBeVisible()
    await expect(page.getByRole('button', { name: '时间趋势', exact: true })).toHaveCount(0)
    await expect(page.getByRole('img', { name: '时间趋势', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /有效样本|到报记录/ })).toHaveCount(0)
    return report
  }

  const monthly = await runCalendar('month')
  expect(monthly.buckets).toHaveLength(12)
  expect(monthly.buckets.slice(0, 3)).toMatchObject([
    { E: 744, O: 0, V: 0, mean: null, complete_month: true },
    { E: 744, O: 2, V: 2, mean: 3, complete_month: true },
    { E: 696, O: 1, V: 1, mean: 20, complete_month: true },
  ])
  await expect(page.getByRole('region', { name: '统计分桶表' }).getByRole('row')).toHaveCount(13)

  const seasonal = await runCalendar('season')
  expect(seasonal.buckets).toHaveLength(4)
  const winter = seasonal.buckets[0]
  expect(winter).toMatchObject({ season: 'winter', season_year: 2024, complete_season: true, E: 2184, O: 3, V: 3 })
  expect(winter.mean).toBeCloseTo(26 / 3)
  await expect(page.getByRole('rowheader', { name: /2024 年冬季/ })).toBeVisible()
  await page.getByText('查看分月统计', { exact: true }).press('Enter')
  await expect(page.getByRole('region', { name: '分月统计表' }).getByRole('row')).toHaveCount(13)
  if (evidence) writeFileSync(join(evidence, 'analysis-annual.json'), JSON.stringify({
    origin: 'F', catalogRequests, monthCount: 12, seasonCount: 4, winterExpected: winter.E, winterValid: winter.V,
    sourceCivilTime: true, weightedWinter: true, derivedOnly: true,
  }, null, 2))
})

test('Pearson presents the 30-pair coefficient and preserves the zero-variance explanation', async ({ page }) => {
  const seed = browserSeed()
  const sample = seed.research.pearson
  const evidence = process.env.S08_EVIDENCE_DIR
  await signIn(page, seed.accounts.reader.username, seed.accounts.reader.password)
  await page.getByRole('button', { name: '指标关联' }).click()
  await setWindow(page, sample, sample.product)
  await discover(page)
  const series = (indicator: string) => page.getByRole('checkbox', { name: `C 城市 #${sample.city} · ${indicator} · 产品 #${sample.product}` })
  await series('CO').check()
  await series('PM2.5').check()
  const runPearson = async () => {
    const pending = apiResponse(page, '/api/v1/analysis/statistics/', 'POST')
    await page.getByRole('button', { name: '运行分析' }).click()
    const response = await pending
    expect(response.status()).toBe(200)
    const result = await response.json()
    expect(result).toMatchObject({ method: 'pearson', E_common: 30, P: 30, actual_pairs: 30 })
    return result
  }
  const coefficient = page.locator('.analysis-metrics > div').filter({ has: page.getByText('Pearson r', { exact: true }) }).locator('dd')
  const sufficient = await runPearson()
  expect(sufficient.conclusion.state).toBe('sufficient')
  expect(sufficient.pearson).toBeCloseTo(1, 9)
  await expect(coefficient).toHaveText('1')

  await series('PM2.5').uncheck()
  await series('NO2').check()
  const constant = await runPearson()
  expect(constant).toMatchObject({ pearson: null, conclusion: { state: 'zero_variance' } })
  await expect(coefficient).toHaveText('—')
  await expect(page.getByRole('status').filter({ hasText: '至少一列方差为零' })).toBeVisible()
  if (evidence) writeFileSync(join(evidence, 'analysis-pearson-threshold.json'), JSON.stringify({
    origin: 'F', pairs: 30, sufficient: sufficient.conclusion.state, coefficient: sufficient.pearson,
    constant: constant.conclusion.state, constantCoefficient: constant.pearson,
  }, null, 2))
})

test('returning from another tab restores research conditions and rechecks the selected series', async ({ page }) => {
  const seed = browserSeed()
  const dir = process.env.S08_EVIDENCE_DIR
  const responses: { path: string; status: number }[] = []
  page.on('response', r => {
    const path = new URL(r.url()).pathname
    if (['/api/v1/me/', '/api/v1/me/capabilities/', '/api/v1/catalog/series/'].includes(path)) responses.push({ path, status: r.status() })
  })
  function stage(name: string) {
    if (dir) writeFileSync(join(dir, 'analysis-resume-diagnostic.json'), JSON.stringify({ stage: name, responses }))
  }
  stage('sign-in')
  await signIn(page, seed.accounts.reader.username, seed.accounts.reader.password)
  const product = page.getByLabel('产品编号（可选）')
  await expect(product).toBeVisible()
  const other = await foregroundPair(page)
  await expect(product).toBeVisible()
  stage('enter-conditions')
  await setWindow(page, seed, seed.products.C)
  await page.getByLabel('指标筛选').selectOption('CO')
  stage('discover')
  await discover(page)
  const selected = page.getByRole('checkbox', { name: new RegExp(`C 城市 #${seed.city} · CO · 产品 #${seed.products.C}`) })
  await selected.check()
  const start = await page.getByLabel('起始时间（UTC）').inputValue()
  const end = await page.getByLabel('结束时间（UTC，不含）').inputValue()
  await product.focus()
  stage('switch-away')
  await switchAway(page, other)
  stage('return')
  const freshCatalog = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/catalog/series/', { timeout: 12_000 })
  await page.bringToFront()
  stage('catalog-revalidation')
  expect((await freshCatalog).status()).toBe(200)
  stage('check-restored-conditions')
  await expect(product).toHaveValue(String(seed.products.C))
  await expect(product).toBeFocused()
  await expect(page.getByLabel('起始时间（UTC）')).toHaveValue(start)
  await expect(page.getByLabel('结束时间（UTC，不含）')).toHaveValue(end)
  await expect(page.getByLabel('指标筛选')).toHaveValue('CO')
  await expect(selected).toBeChecked()
  await expect(page.getByRole('button', { name: '运行分析' })).toBeEnabled()
  await expect(page.getByRole('heading', { name: '分析结果' })).toHaveCount(0)
  stage('complete')
  if (dir) writeFileSync(join(dir, 'analysis-resume.json'), JSON.stringify({ origin: 'F', refreshedCatalog: 200,
    conditionsRestored: true, visibleSelectionRestored: true, focusRestored: true, automaticAnalysis: false }))
})
