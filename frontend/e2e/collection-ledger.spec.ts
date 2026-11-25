import { writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import type { CollectionWindowProjection, DirectoryEntry, WindowRow } from '../src/manage/collectionLedger'
import { browserSeed, type BrowserAccount } from './seed'

async function openLedger(page: Page, account: BrowserAccount) {
  await page.goto('/app/manage/collection?scope=synthetic')
  await page.getByLabel('用户名').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '采集任务', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '采集账本与执行' })).toBeVisible()
}

test('F 操作者只读取授权 Job 控制和执行目录，不加载全账本', async ({ page }) => {
  const seed = browserSeed()
  const unexpected: string[] = []
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/v1/admin/') && request.method() !== 'GET') unexpected.push(request.method())
    if (/\/collection-(windows|runs)\//.test(url.pathname) || url.searchParams.get('purpose') === 'ledger') unexpected.push('ledger')
  })
  await openLedger(page, seed.accounts.operator)
  const ledger = page.getByRole('region', { name: '采集账本与执行' })
  await expect(ledger.getByRole('button', { name: '读取账本目录' })).toHaveCount(0)
  await expect(ledger.getByRole('button', { name: '历史导入' })).toHaveCount(0)
  const jobs = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/admin/collection-jobs/' && response.request().method() === 'GET')
  await ledger.getByRole('button', { name: '读取获权 Job' }).click()
  expect((await jobs).status()).toBe(200)
  const diagnostic = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/admin/collection-jobs/${seed.collection.job}/`)
  await ledger.getByRole('button', { name: `Job ${seed.collection.job} · 产品 ${seed.collection.product} · 已暂停`, exact: true }).click()
  expect((await diagnostic).status()).toBe(200)
  await expect(ledger.getByRole('button', { name: '重新读取控制状态' })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const execution = ledger.getByRole('button', { name: '执行采集' })
  await execution.focus()
  await page.keyboard.press('Enter')
  await ledger.getByLabel('目标整点（含时区）').fill(seed.snapshot_at)
  const catalog = page.waitForResponse(response => new URL(response.url()).searchParams.get('purpose') === 'execute')
  await ledger.getByRole('button', { name: '读取执行目标' }).click()
  expect((await catalog).status()).toBe(200)
  await expect(ledger.getByText('当前条件没有可执行目标。', { exact: true })).toBeVisible()
  await expect(ledger.getByRole('button', { name: '提交合成采集' })).toHaveCount(0)
  expect(unexpected).toEqual([])
  const evidence = process.env.S08_EVIDENCE_DIR
  if (evidence) await writeFile(join(evidence, 'collection-ledger-flow.json'), JSON.stringify({ origin: 'F', jobList: 200, jobControl: 200, executeDirectory: 200, unexpected, viewport: 390 }))
})

test('F 审阅者从获权账本打开原窗口、Run、子项和回执', async ({ page }) => {
  const seed = browserSeed()
  const fixture = seed.quality.close
  const evidence = process.env.S08_EVIDENCE_DIR
  const statuses: Record<string, number> = {}
  let stage = 'sign-in'
  let counts: { runs: number; items: number; receipts: number } | undefined
  try {
    await openLedger(page, seed.accounts.reviewer)
    const ledger = page.getByRole('region', { name: '采集账本与执行' })
    await expect(ledger.getByRole('button', { name: '执行采集' })).toHaveCount(0)
    await expect(ledger.getByRole('button', { name: '读取获权 Job' })).toHaveCount(0)
    await ledger.getByLabel('来源日期起点').fill(seed.quality.start)
    await ledger.getByLabel('来源日期终点（不含）').fill(seed.quality.end)

    stage = 'directory'
    const catalog = page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === '/api/v1/admin/catalog/products/' && url.searchParams.get('purpose') === 'ledger'
    })
    await ledger.getByRole('button', { name: '读取账本目录' }).click()
    const directory = await catalog
    statuses.directory = directory.status()
    expect(statuses.directory).toBe(200)
    const conditions = new URL(directory.url()).searchParams
    expect([conditions.get('scope'), conditions.get('start'), conditions.get('end')])
      .toEqual([seed.scope, seed.quality.start, seed.quality.end])
    const { results } = await directory.json() as { results: DirectoryEntry[] }
    expect(results).toContainEqual(expect.objectContaining({ product: fixture.product, source: seed.quality.source, shape: 'C' }))

    stage = 'windows'
    const windows = page.waitForResponse(result => new URL(result.url()).pathname === '/api/v1/admin/collection-windows/')
    await ledger.getByRole('button', { name: `产品 ${fixture.product} · C · ${seed.quality.source}`, exact: true }).click()
    const windowList = await windows
    statuses.windows = windowList.status()
    expect(statuses.windows).toBe(200)
    const list = await windowList.json() as { results: WindowRow[] }
    expect(list.results).toContainEqual(expect.objectContaining({ id: fixture.window, product: fixture.product, synthetic: true, full_scope: true }))
    await expect(ledger.getByRole('heading', { name: '获权业务窗口' })).toBeVisible()

    stage = 'window'
    const windowRead = page.waitForResponse(result => new URL(result.url()).pathname === `/api/v1/admin/collection-windows/${fixture.window}/`)
    await ledger.getByRole('button', { name: `查看窗口 ${fixture.window}`, exact: true }).click()
    const windowResponse = await windowRead
    statuses.window = windowResponse.status()
    expect(statuses.window).toBe(200)
    const window = await windowResponse.json() as CollectionWindowProjection
    expect(window).toMatchObject({
      window_id: fixture.window, product: fixture.product, synthetic: true,
      coverage: { E: 7, O: 1, V: 0, accepted: 1, attempted_cells: 2 },
    })
    expect(window.runs).toContainEqual(expect.objectContaining({ id: fixture.run, batch_id: fixture.batch, attempt: 2, result: 'quality_only' }))
    expect(window.runs).toContainEqual(expect.objectContaining({ attempt: 1, result: 'partial' }))
    const detail = ledger.getByRole('region', { name: '窗口与运行证据' })
    const original = detail.getByRole('row').filter({ hasText: fixture.run })
    await expect(original).toBeVisible()

    stage = 'run'
    const runRead = page.waitForResponse(result => new URL(result.url()).pathname === `/api/v1/admin/collection-runs/${fixture.run}/`)
    await original.getByRole('button', { name: '查看 Run', exact: true }).click()
    const runResponse = await runRead
    statuses.run = runResponse.status()
    expect(statuses.run).toBe(200)
    const run = await runResponse.json() as CollectionWindowProjection
    // Run detail keeps window-wide counts and recalculates observation age on each read.
    expect(run).toMatchObject({
      window_id: fixture.window, product: fixture.product, synthetic: true,
      coverage: { E: 7, O: 1, V: 0, accepted: 1, attempted_cells: 2 },
    })
    expect(run.runs).toEqual([expect.objectContaining({ id: fixture.run, batch_id: fixture.batch, status: 'finished', dispatch: 'sent', result: 'quality_only' })])
    expect(run.items).toEqual([expect.objectContaining({ run_id: fixture.run, batch_id: fixture.batch, status: 'reread', accepted: false })])
    expect(run.receipts).toEqual([expect.objectContaining({
      id: fixture.receipt, item_id: run.items?.[0].id, batch_id: fixture.batch, outcome: 'same_version_conflict',
      revision: { type: 'city', id: expect.any(Number) },
    })])
    expect(run.quality_issues).toContainEqual(expect.objectContaining({ id: fixture.issue, run: fixture.run, window: fixture.window }))

    stage = 'dom'
    await expect(detail.getByRole('heading', { name: `Run ${fixture.run}`, exact: true })).toBeVisible()
    const items = detail.getByRole('region', { name: '所选 Run 子项' })
    const row = items.getByRole('row').filter({ hasText: run.items?.[0].item_key ?? '' })
    await expect(row.getByText('未接受')).toBeVisible()
    await expect(row.getByText(`批次 ${fixture.batch}`, { exact: true })).toBeVisible()
    await expect(row.getByText(new RegExp(`^#${fixture.receipt} ·`))).toBeVisible()
    await expect(detail.getByRole('button', { name: `打开质量事件 ${fixture.issue}`, exact: true })).toBeVisible()
    await expect(detail.getByRole('button', { name: '重新投递' })).toHaveCount(0)
    counts = { runs: run.runs?.length ?? 0, items: run.items?.length ?? 0, receipts: run.receipts?.length ?? 0 }
    stage = 'complete'
  } finally {
    if (evidence) await writeFile(join(evidence, 'collection-ledger-window-flow.json'), JSON.stringify({ origin: 'F', stage, statuses, counts }))
  }
})

interface LedgerFixture {
  product: number
  start: string
  end: string
  full_window: number
  empty_run: string
  narrow_window: number
  hidden_run: string
  allowed_code: string
  hidden_code: string
}

test('F 账本分别显示无新增 Run 与开放部分窗口，并限制授权投影和单元分页', async ({ page }) => {
  const seed = browserSeed()
  const fixture = (seed.quality as typeof seed.quality & { ledger: LedgerFixture }).ledger
  const report: {
    origin: 'F'
    stage: string
    cellsByPage?: number[]
    emptyRun?: { status: string; result?: string }
    window?: { state: string; result?: string | null; E?: number | null; O?: number; V?: number }
    narrow?: { cells: number; runs: number; receipts: number; unfilteredStatus: number }
  } = { origin: 'F', stage: 'sign-in' }
  const evidence = process.env.S08_EVIDENCE_DIR
  const pathname = (kind: 'windows' | 'runs', id?: number | string) => `/api/v1/admin/collection-${kind}/${id === undefined ? '' : `${id}/`}`
  try {
    await openLedger(page, seed.accounts.reviewer)
    const ledger = page.getByRole('region', { name: '采集账本与执行' })
    await ledger.getByLabel('来源日期起点').fill(fixture.start)
    await ledger.getByLabel('来源日期终点（不含）').fill(fixture.end)
    await ledger.getByRole('button', { name: '读取账本目录' }).click()
    report.stage = 'window-list'
    const windowsRead = page.waitForResponse(response => new URL(response.url()).pathname === pathname('windows'))
    await ledger.getByRole('button', { name: `产品 ${fixture.product} · S · ${seed.quality.source}`, exact: true }).click()
    const fullList = await windowsRead
    expect(fullList.status()).toBe(200)
    expect((await fullList.json() as { results: WindowRow[] }).results).toEqual([
      expect.objectContaining({ id: fixture.full_window, full_scope: true, result: 'partial' }),
    ])
    await expect(ledger.getByRole('button', { name: `查看窗口 ${fixture.narrow_window}`, exact: true })).toHaveCount(0)

    report.stage = 'full-window-and-pages'
    const detail = ledger.getByRole('region', { name: '窗口与运行证据' })
    const fullRead = page.waitForResponse(response => new URL(response.url()).pathname === pathname('windows', fixture.full_window))
    await ledger.getByRole('button', { name: `查看窗口 ${fixture.full_window}`, exact: true }).click()
    const fullResponse = await fullRead
    expect(fullResponse.status()).toBe(200)
    const full = await fullResponse.json() as CollectionWindowProjection
    expect(full).toMatchObject({
      state: 'open', result: 'partial', coverage: { E: 63, O: 1, V: 1, accepted: 1 }, next_after: 50,
      runs: [
        expect.objectContaining({ attempt: 1, status: 'finished', result: 'partial' }),
        expect.objectContaining({ id: fixture.empty_run, attempt: 2, status: 'finished', dispatch: 'sent', result: 'no_new' }),
      ],
    })
    const summary = detail.locator('.ledger-window-summary')
    await expect(summary.getByText('开放', { exact: true })).toBeVisible()
    await expect(summary.getByText('部分完成', { exact: true })).toBeVisible()
    const emptyRow = detail.getByRole('row').filter({ hasText: fixture.empty_run })
    await expect(emptyRow).toHaveCount(1)
    await expect(emptyRow.getByRole('cell').filter({ hasText: '已结束 / 已投递' })).toBeVisible()
    await expect(emptyRow.getByText('本次运行未产生新观测；窗口结果仍见上方。')).toBeVisible()
    const cells = detail.getByRole('region', { name: '获权应到单元表' })
    await expect(cells.getByRole('row')).toHaveCount(51)
    const nextRead = page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === pathname('windows', fixture.full_window) && url.searchParams.get('after') === '50'
    })
    await detail.getByRole('button', { name: '读取下一页单元' }).click()
    const nextResponse = await nextRead
    expect(nextResponse.status()).toBe(200)
    const next = await nextResponse.json() as CollectionWindowProjection
    expect(full.cells).toHaveLength(50)
    expect(next.cells).toHaveLength(13)
    expect(next.next_after).toBeNull()
    expect(new Set([...(full.cells ?? []), ...(next.cells ?? [])].map(cell => cell.item_key)).size).toBe(63)
    await expect(cells.getByRole('row')).toHaveCount(14)
    await expect(detail.getByRole('button', { name: '读取下一页单元' })).toHaveCount(0)
    report.cellsByPage = [50, 13]
    report.window = { state: full.state, result: full.result, E: full.coverage?.E, O: full.coverage?.O, V: full.coverage?.V }

    report.stage = 'empty-run'
    const runRead = page.waitForResponse(response => new URL(response.url()).pathname === pathname('runs', fixture.empty_run))
    await emptyRow.getByRole('button', { name: '查看 Run' }).click()
    const runResponse = await runRead
    expect(runResponse.status()).toBe(200)
    const run = await runResponse.json() as CollectionWindowProjection
    expect(run).toMatchObject({
      window_id: fixture.full_window, state: 'open', result: 'partial',
      runs: [{ id: fixture.empty_run, status: 'finished', result: 'no_new', manifest_count: 0, batch_id: null }],
      items: [], receipts: [],
    })
    await expect(detail.getByText('执行状态 已结束 · 投递 已投递 · 本次结果 未产生新观测')).toBeVisible()
    await expect(detail.getByText('当前授权投影没有可披露的子项。')).toBeVisible()
    await expect(summary.getByText('开放', { exact: true })).toBeVisible()
    await expect(summary.getByText('部分完成', { exact: true })).toBeVisible()
    report.emptyRun = { status: 'finished', result: 'no_new' }

    report.stage = 'narrow-window'
    const filter = ledger.getByRole('form', { name: '获权窗口筛选' })
    await filter.getByLabel('来源对象代码').fill(fixture.allowed_code)
    await filter.getByLabel('指标代码').fill('CO')
    const scopedListRead = page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === pathname('windows') && url.searchParams.get('object_code') === fixture.allowed_code
        && url.searchParams.get('indicator') === 'CO'
    })
    await filter.getByRole('button', { name: '筛选窗口' }).click()
    const scopedList = await scopedListRead
    expect(scopedList.status()).toBe(200)
    const options = (await scopedList.json() as { results: WindowRow[] }).results
    const choice = options.find(window => window.id === fixture.narrow_window)
    expect(choice).toMatchObject({ full_scope: false, result: null })
    expect(choice?.links).not.toHaveProperty('runs')
    const detailRead = page.waitForResponse(response => new URL(response.url()).pathname === pathname('windows', fixture.narrow_window))
    await ledger.getByRole('button', { name: `查看窗口 ${fixture.narrow_window}`, exact: true }).click()
    const narrowResponse = await detailRead
    expect(narrowResponse.status()).toBe(200)
    const query = new URL(narrowResponse.url()).searchParams
    expect([query.get('object_code'), query.get('indicator')]).toEqual([fixture.allowed_code, 'CO'])
    const narrow = await narrowResponse.json() as CollectionWindowProjection
    expect(narrow).toMatchObject({
      state: 'partially_authorized', result: null, scope: null, profile: null, roster_basis: '', closed_at: null,
      coverage: { E: 1, O: 1, V: 1, accepted: 1 }, next_after: null, next_run_after: null, runs: [],
      cells: [{ object_code: fixture.allowed_code, indicator: 'CO' }],
    })
    expect(Object.keys(narrow.coverage?.by_indicator ?? {})).toEqual(['CO'])
    expect(narrow.items).toHaveLength(1)
    expect(narrow.receipts).toHaveLength(1)
    expect(JSON.stringify(narrow)).not.toContain(fixture.hidden_code)
    expect(JSON.stringify(narrow)).not.toContain(fixture.hidden_run)
    await expect(summary.getByText('部分授权', { exact: true })).toBeVisible()
    await expect(summary.getByText('未披露', { exact: true })).toBeVisible()
    await expect(detail.getByText('只显示当前获权部分，未披露的范围不计入推断。')).toBeVisible()
    await expect(detail.getByRole('region', { name: '各次 Run 列表' })).toHaveCount(0)
    await expect(cells.getByRole('row')).toHaveCount(2)
    await expect(cells.getByText(fixture.hidden_code, { exact: true })).toHaveCount(0)
    const unfiltered = await page.request.get(pathname('windows', fixture.narrow_window))
    expect(unfiltered.status()).toBe(403)
    report.narrow = { cells: 1, runs: 0, receipts: 1, unfilteredStatus: unfiltered.status() }
    report.stage = 'complete'
  } finally {
    if (evidence) await writeFile(join(evidence, 'collection-ledger-scope-flow.json'), JSON.stringify(report))
  }
})
