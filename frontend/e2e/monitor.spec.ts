import { expect, test, type Page, type Request } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { browserSeed } from './seed'

async function signIn(page: Page, username: string, password: string) {
  await page.goto('/app/login')
  await page.getByRole('heading', { name: '登录' }).waitFor()
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
}

const hour = 60 * 60 * 1000
const numeric = (value: number) => new RegExp(`^${value}(?:\\.0+)?$`)

test('monitor drill-down keeps L, C, T and station observations distinct', async ({ page }) => {
  const seed = browserSeed()
  const evidence = process.env.S08_EVIDENCE_DIR
  const screenshotOptions = { fullPage: true, mask: [page.locator('.account-link')] }
  const statusCodes: number[] = []
  const requests: { endpoint: string; scope: string; status?: number; failed?: boolean }[] = []
  const entries = new Map<Request, typeof requests[number]>()
  const stationLayouts: { viewport: number; document: number; tableClient: number; tableScroll: number }[] = []
  const checks = {
    cityIndicators: 0, distinctLAndC: false, distinctCAndT: false, trendSlots: 0, trendGaps: 0,
    separateSiteEpochs: false, distinctLatestTimes: false, emptyHistoryWindow: false, outsideSiteEpoch: false,
  }
  const instant = (index: number) => new Date(Date.parse(seed.start) + index * hour).toISOString()
  const times = await page.evaluate(stamps => stamps.map(stamp => new Date(stamp).toLocaleString('zh-CN')),
    [-1, ...Array.from({ length: 25 }, (_, index) => index)].map(instant))
  const time = (index: number) => times[index + 1]
  let syntheticFlow = false
  let stage = 'session'
  page.on('request', request => {
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/v1/')) return
    const entry = { endpoint: url.pathname.replace(/\/\d+\//g, '/:id/'), scope: url.searchParams.get('scope') === 'synthetic' ? 'synthetic' : 'formal' }
    requests.push(entry)
    entries.set(request, entry)
  })
  page.on('response', response => {
    const entry = entries.get(response.request())
    if (!entry) return
    entry.status = response.status()
    if (syntheticFlow && entry.scope === 'synthetic') statusCodes.push(entry.status)
  })
  page.on('requestfailed', request => {
    const entry = entries.get(request)
    if (entry) entry.failed = true
  })

  try {
    await signIn(page, seed.accounts.reader.username, seed.accounts.reader.password)
    await expect(page).toHaveURL(/\/app\/$/)
    await expect(page.getByRole('heading', { name: '全国空气质量总览' })).toBeVisible()
    stage = 'F national table'
    syntheticFlow = true
    await expect(page.getByLabel('数据域')).toHaveValue('')
    await page.getByLabel('数据域').selectOption('synthetic')
    await expect(page.getByLabel('数据域')).toHaveValue('synthetic')
    const firstCity = page.getByRole('row').filter({ hasText: seed.cities[0].name })
    await expect(firstCity).toBeVisible()
    await expect(firstCity.getByText(numeric(31))).toBeVisible()

    await expect(page).toHaveURL('/app/?scope=synthetic')

    stage = 'C city detail'
    await firstCity.getByRole('button', { name: '城市记录' }).click()
    await expect(page).toHaveURL(new RegExp(`/app/cities/${seed.city}(?:\\?|$)`))
    const city = page.getByRole('region', { name: 'C 城市指标' })
    const cityAQI = city.getByRole('article').filter({ has: page.getByText('AQI', { exact: true }) })
    await expect(city.getByRole('article')).toHaveCount(7)
    for (const [label, value, unit] of [
      ['AQI', 44, '1'], ['PM₂.₅', 15, 'µg/m³'], ['PM₁₀', 16, 'µg/m³'], ['SO₂', 17, 'µg/m³'],
      ['NO₂', 18, 'µg/m³'], ['CO', 19, 'mg/m³'], ['O₃', 20, 'µg/m³'],
    ] as const) {
      const indicator = city.getByRole('article').filter({ has: page.getByText(label, { exact: true }) })
      await expect(indicator.locator('.metric-value')).toHaveText(numeric(value))
      await expect(indicator.locator('.unit')).toHaveText(unit)
      await expect(indicator.getByText(`观测 ${time(23)}`, { exact: true })).toBeVisible()
      await expect(indicator.getByText(`采集 ${time(24)}`, { exact: true })).toBeVisible()
      checks.cityIndicators += 1
    }
    await expect(cityAQI.getByText('优 · 首要污染物 未披露')).toBeVisible()
    await expect(cityAQI.getByText('HJ633-2012', { exact: true })).toBeVisible()
    expect(Date.parse(new URL(page.url()).searchParams.get('at') ?? '')).toBe(Date.parse(seed.snapshot_at))
    checks.distinctLAndC = true

    stage = 'T trend and data table'
    const trend = page.getByRole('region', { name: '独立 T 城市趋势' })
    await expect(trend.getByRole('img', { name: /独立 T 产品 24 小时 AQI 趋势/ })).toBeVisible()
    await trend.getByText('查看 24 小时数据表').press('Enter')
    await expect(trend.getByRole('row')).toHaveCount(25)
    checks.trendSlots = 24
    for (const index of [3, 10, 23]) {
      const slot = trend.getByRole('row').filter({ has: page.getByRole('rowheader', { name: time(index), exact: true }) })
      await expect(slot.getByRole('cell', { name: '无有效值', exact: true })).toBeVisible()
      await expect(slot.getByRole('cell', { name: '该时槽无数据', exact: true })).toBeVisible()
      checks.trendGaps += 1
    }
    await expect(trend.getByRole('cell', { name: '该时槽无数据', exact: true })).toHaveCount(3)
    await expect(trend.getByRole('row').filter({ has: page.getByRole('rowheader', { name: time(22), exact: true }) })
      .getByRole('cell', { name: numeric(47) })).toBeVisible()
    if (evidence) {
      await page.evaluate(() => window.scrollTo(0, 0))
      await page.screenshot({ path: join(evidence, 'monitor-city-1440.png'), ...screenshotOptions })
    }

    stage = 'same-hour C and T reconciliation'
    const snapshot = new URL(page.url())
    const earlierCity = new URL(snapshot)
    earlierCity.searchParams.set('at', instant(22))
    await page.goto(earlierCity.toString())
    await expect(cityAQI.locator('.metric-value')).toHaveText(numeric(43))
    await expect(cityAQI.getByText(`观测 ${time(22)}`, { exact: true })).toBeVisible()
    checks.distinctCAndT = true
    await page.goBack()
    await expect(page).toHaveURL(snapshot.toString())

    stage = 'D daily discovery'
    const daily = page.getByRole('region', { name: '独立 D 日报告' })
    await daily.getByRole('button', { name: '查找获准报告' }).click()
    await daily.getByRole('button', { name: '继续查找', exact: true }).last().click()
    await daily.getByRole('button', { name: '精确读取' }).click()
    await expect(daily.getByRole('table')).toBeVisible()
    await expect(daily.getByRole('row')).toHaveCount(2)

    stage = 'narrow city and station directory'
    await page.setViewportSize({ width: 390, height: 844 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.getByRole('button', { name: /North Sample 01/ }).click()

    stage = 'S station detail'
    await expect(page).toHaveURL(new RegExp(`/app/stations/${seed.station}(?:\\?|$)`))
    await expect(page.getByRole('heading', { name: '最近有效观测' })).toBeVisible()
    const stationRows = page.getByLabel('站点最近观测，可横向滚动').getByRole('row')
    const coRow = stationRows.filter({ has: page.getByRole('rowheader', { name: 'CO', exact: true }) })
    await expect(coRow).toContainText('有效')
    const identity = page.getByRole('region', { name: '站址期信息' })
    await expect(identity.getByText(String(seed.site_epoch), { exact: true })).toBeVisible()
    await expect(identity.getByText(seed.cities[0].name, { exact: true })).toBeVisible()
    await expect(identity.getByText('S08-01', { exact: true })).toBeVisible()
    await expect(page.getByText('正式地图及坐标投影未开放，站址身份和记录仍可查看。')).toBeVisible()
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 })
      const layout = await page.getByLabel('站点最近观测，可横向滚动').evaluate(element => ({
        viewport: window.innerWidth,
        document: document.documentElement.scrollWidth,
        tableClient: element.clientWidth,
        tableScroll: element.scrollWidth,
      }))
      stationLayouts.push(layout)
      expect(layout.document).toBeLessThanOrEqual(layout.viewport)
      expect(layout.tableClient).toBeLessThan(layout.tableScroll)
      if (evidence) await page.screenshot({ path: join(evidence, `monitor-station-${width}.png`), ...screenshotOptions })
    }
    stage = 'historical site selection and indicator times'
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.getByRole('link', { name: '切换站址期' }).click()
    const epochs = page.getByRole('region', { name: '选择站址期' })
    await expect(epochs.getByText(/当前站址期/)).toBeVisible()
    const oldEpoch = epochs.getByRole('article').filter({ hasText: '历史站址期' })
    await oldEpoch.getByRole('button', { name: '查看记录' }).click()
    await expect(page).toHaveURL(new RegExp(`[?&]site_epoch=${seed.previous_site_epoch}(?:&|$)`))
    await expect(identity.getByText(String(seed.previous_site_epoch), { exact: true })).toBeVisible()
    await expect(identity.getByText('历史', { exact: true })).toBeVisible()
    checks.separateSiteEpochs = true
    const pmRow = stationRows.filter({ has: page.getByRole('rowheader', { name: 'PM₂.₅', exact: true }) })
    await expect(coRow.getByRole('cell', { name: /^1(?:\.0+)? mg\/m³$/ })).toBeVisible()
    await expect(coRow.getByRole('cell', { name: time(4), exact: true })).toBeVisible()
    await expect(pmRow.getByRole('cell', { name: /^15(?:\.0+)? µg\/m³$/ })).toBeVisible()
    await expect(pmRow.getByRole('cell', { name: time(5), exact: true })).toBeVisible()
    checks.distinctLatestTimes = true

    stage = 'historical site series'
    const history = page.getByRole('region', { name: '站址期历史窗口' })
    await history.getByRole('button', { name: '历史窗口 1', exact: true }).click()
    await expect(history.getByRole('row')).toHaveCount(12)
    const historyCO = history.getByRole('row').filter({ has: page.getByRole('rowheader', { name: 'CO', exact: true }) })
    await expect(historyCO).toHaveCount(5)
    await expect(historyCO.getByRole('cell', { name: time(5), exact: true })).toHaveCount(0)
    await expect(historyCO.getByRole('cell', { name: time(1), exact: true })).toHaveCount(0)

    stage = 'fixed historical window without fallback'
    await page.getByRole('link', { name: '数据查询', exact: true }).click()
    const object = page.getByLabel('来源产品与对象')
    await expect(object).toBeEnabled()
    const oldObject = `${seed.products.S}:${seed.previous_site_epoch}`
    const oldOption = object.locator(`option[value="${oldObject}"]`)
    if (!(await oldOption.count())) await page.getByRole('button', { name: '加载更多目录项' }).click()
    await expect(oldOption).toHaveCount(1)
    await object.selectOption(oldObject)
    await page.getByRole('radio', { name: '固定时间窗' }).check()
    await page.getByRole('checkbox', { name: /^CO / }).check()
    await page.getByRole('checkbox', { name: /^PM2\.5 / }).uncheck()
    const start = page.getByLabel('开始时刻（含）')
    const end = page.getByLabel('结束时刻（不含）')
    await start.fill(instant(5))
    await end.fill(instant(6))
    await page.getByRole('button', { name: '应用查询', exact: true }).click()
    const result = page.getByRole('region', { name: '查询结果' })
    await expect(result.getByText('获准范围内没有符合当前条件的观测。')).toBeVisible()
    await expect(result.getByRole('table')).toHaveCount(0)
    checks.emptyHistoryWindow = true
    await start.fill(instant(6))
    await end.fill(instant(7))
    await page.getByRole('button', { name: '应用查询', exact: true }).click()
    await expect(result.getByText('选择时段超出已核站址期覆盖。')).toBeVisible()
    await expect(result.getByRole('table')).toHaveCount(0)
    checks.outsideSiteEpoch = true
    expect(statusCodes.filter(status => status >= 400)).toEqual([])
    stage = 'complete'
  } finally {
    if (evidence) writeFileSync(join(evidence, 'monitor-flow.json'), JSON.stringify({ origin: 'F', stage, requests, stationLayouts, checks }, null, 2))
  }
})
