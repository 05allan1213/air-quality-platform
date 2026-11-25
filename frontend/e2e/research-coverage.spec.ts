import { expect, test, type Page } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import type { Detail, Result } from '../src/research/analysisContract'
import { browserSeed } from './seed'

const hour = 3_600_000

async function openAnalysis(page: Page, account: { username: string; password: string }) {
  await page.goto(`/app/login?${new URLSearchParams({ next: '/analysis?scope=synthetic' })}`)
  await page.getByLabel('用户名').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/app/analysis?scope=synthetic')
}

function responseFor(page: Page, path: string, method: 'GET' | 'POST') {
  return page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/v1/${path}/` && response.request().method() === method,
  )
}

async function discover(page: Page, { start, end, product }: { start: string; end: string; product: number }) {
  await page.getByLabel('起始时间（UTC）').fill(start.slice(0, 16))
  await page.getByLabel('结束时间（UTC，不含）').fill(end.slice(0, 16))
  await page.getByLabel('产品编号（可选）').fill(String(product))
  const pending = responseFor(page, 'catalog/series', 'GET')
  await page.getByRole('button', { name: '发现可选系列' }).click()
  expect((await pending).status()).toBe(200)
}

async function analyze(page: Page, method: 'compare' | 'statistics'): Promise<Result> {
  const pending = responseFor(page, `analysis/${method}`, 'POST')
  await page.getByRole('button', { name: '运行分析' }).click()
  const response = await pending
  expect(response.status()).toBe(200)
  return response.json() as Promise<Result>
}

function recordEvidence(name: string, evidence: Record<string, unknown>) {
  const directory = process.env.S08_EVIDENCE_DIR
  if (directory) writeFileSync(join(directory, name), JSON.stringify({ origin: 'F', ...evidence }, null, 2))
}

test('complete paired coverage displays the ranking and ordered CO difference for the shared 12 hours', async ({ page }) => {
  const seed = browserSeed()
  const product = seed.products.S
  const [first, second] = seed.paired_site_epochs
  const start = new Date(Date.parse(seed.start) + 6 * hour).toISOString()
  const end = new Date(Date.parse(seed.start) + 18 * hour).toISOString()
  await openAnalysis(page, seed.accounts.reader)
  await page.getByRole('button', { name: '对象对比', exact: true }).click()
  await discover(page, { start, end, product })
  for (const epoch of [first, second]) {
    await page.getByRole('checkbox', { name: new RegExp(`^选择 S 站址期 #${epoch} · CO · 产品 #${product} · `) }).check()
  }
  const result = await analyze(page, 'compare')
  expect(result).toMatchObject({
    state: 'ok', selection: { product, scope: 'synthetic', site_epochs: [first, second], indicators: ['CO'] },
    columns: [{ entity: first, E: 12, O: 12, V: 12 }, { entity: second, E: 12, O: 12, V: 12 }],
    E_common: 12, P: 12, actual_pairs: 12, paired_ratio: 1, conclusion: { state: 'sufficient' }, mean_difference: -1,
    ranking: [{ entity: second, pos: 1, rank: 1 }, { entity: first, pos: 0, rank: 2 }],
  })
  expect(Date.parse(result.requested_window.start)).toBe(Date.parse(start))
  expect(Date.parse(result.requested_window.end)).toBe(Date.parse(end))
  expect(result.ranking?.[0]?.mean).toBeCloseTo(35 / 12)
  expect(result.ranking?.[1]?.mean).toBeCloseTo(23 / 12)

  await expect(page.getByRole('status').filter({ hasText: /^覆盖充分$/ })).toBeVisible()
  const coverage = page.getByRole('region', { name: '样本与覆盖表', exact: true })
  for (const epoch of [first, second]) {
    const cells = coverage.getByRole('row').filter({ hasText: new RegExp(`#${epoch}(?!\\d)`) }).getByRole('cell')
    for (const index of [0, 1, 2]) await expect(cells.nth(index)).toHaveText('12')
  }
  function sharedMetric(label: string) {
    return page.locator('.analysis-metrics > div').filter({ has: page.getByText(label, { exact: true }) }).locator('dd')
  }
  await expect(sharedMetric('共同预期 E共')).toHaveText('12')
  await expect(sharedMetric('共同有效 P')).toHaveText('12')
  await expect(sharedMetric('配对覆盖 P/E共')).toHaveText('100%')
  await expect(sharedMetric('实际配对数')).toHaveText('12')
  await expect(sharedMetric('整期共同样本均值差')).toHaveText('-1 mg/m³')
  const ranking = page.getByRole('region', { name: '整期共同样本排名', exact: true }).getByRole('row')
  await expect(ranking).toHaveCount(3)
  await expect(ranking.nth(1).getByRole('rowheader')).toContainText(`#${second}`)
  await expect(ranking.nth(2).getByRole('rowheader')).toContainText(`#${first}`)
  recordEvidence('research-complete-coverage.json', {
    columns: result.columns.map(({ E, O, V }) => ({ E, O, V })),
    expectedCommon: result.E_common, validPairs: result.P, pairedCoverage: result.paired_ratio,
    conclusion: result.conclusion.state, rankOrder: result.ranking?.map(row => row.pos), difference: result.mean_difference,
  })
})

test('long-window trend shows valid extrema and gaps and drills into the same version by keyboard', async ({ page }) => {
  const seed = browserSeed()
  const { range } = seed.research
  const start = Date.parse(range.start)
  const widthSeconds = 5_184
  await openAnalysis(page, seed.accounts.reader)
  await page.getByLabel('分桶粒度').selectOption('month')
  await discover(page, range)
  await page.getByRole('checkbox', { name: new RegExp(`^选择 C 城市 #${range.city} · CO · 产品 #${range.product} · `) }).check()
  const result = await analyze(page, 'statistics')
  expect(result).toMatchObject({
    state: 'ok', grain: 'month', source_timezone: 'UTC',
    selection: { scope: 'synthetic', product: range.product, cities: [range.city], indicators: ['CO'] },
    columns: [{ E: 1440, O: 3, V: 3 }], chart: { resolution: 'range', interval_seconds: widthSeconds },
  })
  expect(Date.parse(result.selection.start)).toBe(start)
  expect(Date.parse(result.selection.end)).toBe(Date.parse(range.end))
  const chart = result.chart
  if (chart?.resolution !== 'range') throw new Error('Expected the authorized range chart')
  expect(chart.points).toHaveLength(1_000)
  expect(chart.points.slice(0, 3)).toMatchObject([
    { pos: 0, minimum: 2, maximum: 4, valid_count: 2, gaps: 0 },
    { pos: 0, minimum: 3, maximum: 3, valid_count: 1, gaps: 0 },
    { pos: 0, minimum: null, maximum: null, valid_count: 0, gaps: 2 },
  ])
  expect(chart.points.slice(0, 3).map(point => Date.parse(point.at))).toEqual([start, start + widthSeconds * 1_000, start + widthSeconds * 2_000])
  expect(chart.points.reduce((count, point) => count + point.gaps, 0)).toBe(1_437)

  await expect(page.getByRole('img', { name: '时间趋势', exact: true })).toBeVisible()
  await expect(page.getByText('每 1.44 小时的有效极值范围，不代表逐时值。有效数和缺口数见数据表。')).toBeVisible()
  await page.getByText('查看时间趋势数据表', { exact: true }).press('Enter')
  const table = page.getByRole('region', { name: '时间趋势数据表', exact: true })
  await expect(table.getByRole('columnheader', { name: '下界 / 上界' })).toBeVisible()
  await expect(table.getByRole('columnheader', { name: '有效数' })).toBeVisible()
  await expect(table.getByRole('columnheader', { name: '缺口数' })).toBeVisible()
  const firstBin = table.getByRole('row').nth(1)
  const gapBin = table.getByRole('row').nth(3)
  const firstCells = firstBin.getByRole('cell')
  await expect(firstCells.nth(1)).toHaveText('2 / 4 mg/m³')
  await expect(firstCells.nth(2)).toHaveText('2')
  await expect(firstCells.nth(3)).toHaveText('0')
  const gapCells = gapBin.getByRole('cell')
  await expect(gapCells.nth(1)).toHaveText('— / — mg/m³')
  await expect(gapCells.nth(2)).toHaveText('0')
  await expect(gapCells.nth(3)).toHaveText('2')

  const pending = responseFor(page, 'analysis/details', 'POST')
  await firstBin.getByRole('button', { name: '查看记录', exact: true }).press('Enter')
  const detailResponse = await pending
  expect(detailResponse.status()).toBe(200)
  expect(detailResponse.request().postDataJSON()).toMatchObject({
    method: result.method, method_version: result.method_version, grain: result.grain,
    selection: result.selection, input_version: result.input_version, support: 'observed',
    bucket: { start: new Date(start).toISOString(), end: new Date(start + widthSeconds * 1_000).toISOString(), pos: 0 },
  })
  const detail = await detailResponse.json() as Detail
  expect(result.input_version).toMatch(/^[a-f\d]{64}$/)
  expect(detail).toMatchObject({ state: 'ok', selection: result.selection, input_version: result.input_version, page: { next_cursor: null } })
  expect(detail.results.map(row => ({ pos: row.pos, at: Date.parse(row.observed_at), value: Number(row.value), unit: row.unit, quality: row.quality }))).toEqual([
    { pos: 0, at: start, value: 2, unit: 'mg/m³', quality: 'valid' },
    { pos: 0, at: start + hour, value: 4, unit: 'mg/m³', quality: 'valid' },
  ])
  const records = page.getByRole('region', { name: '支撑记录表', exact: true }).getByRole('row')
  await expect(records).toHaveCount(3)
  for (const [index, row] of detail.results.entries()) {
    await expect(records.nth(index + 1).getByRole('cell').nth(1)).toHaveText(row.value!)
  }
  recordEvidence('research-range-chart.json', {
    resolution: chart.resolution, bucketCount: chart.points.length, widthSeconds, expectedSlots: result.columns[0]?.E,
    populatedBucket: { minimum: 2, maximum: 4, valid: 2, gaps: 0 }, emptyBucket: { valid: 0, gaps: 2 },
    detailsStatus: detailResponse.status(), supportRows: detail.results.length,
    sameParentVersion: detail.input_version === result.input_version, keyboard: true,
  })
})
