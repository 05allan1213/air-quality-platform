import { writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { expect, test, type Page, type Response } from '@playwright/test'
import type { CollectionWindowProjection, RunCommand, SubmissionReceipt } from '../src/manage/collectionLedger'
import { browserSeed, type BrowserAccount, type BrowserSeed } from './seed'

interface ChainFixture {
  executor: BrowserAccount
  peer: BrowserAccount
  source: string
  target: string
}

const catalogPath = '/api/v1/admin/catalog/products/'
const windowsPath = '/api/v1/admin/collection-windows/'
const runsPath = '/api/v1/admin/collection-runs/'
const lookupPath = '/api/v1/admin/command-receipts/lookup/'
const healthPath = '/api/v1/admin/operations/health/'
const ledgerPage = '/app/manage/collection?scope=synthetic'
const operationsPage = '/app/manage/operations?scope=synthetic'

function seed() {
  return browserSeed() as BrowserSeed & { governance_chain?: ChainFixture }
}

function at(response: Response, pathname: string, method = 'GET') {
  return new URL(response.url()).pathname === pathname && response.request().method() === method
}

async function signIn(page: Page, account: BrowserAccount) {
  await page.goto(ledgerPage)
  await page.getByLabel('用户名').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '采集任务', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '采集账本与执行' })).toBeVisible()
}

async function lookupAs(page: Page, envelope: { family: 'manual_run'; target: number; command: RunCommand }) {
  return page.evaluate(async ({ path, envelope }) => {
    const protection = await fetch('/api/v1/auth/csrf/', { credentials: 'same-origin' })
    const { csrf_token } = await protection.json()
    const response = await fetch(path, {
      method: 'POST', credentials: 'same-origin',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRFToken': csrf_token },
      body: JSON.stringify(envelope),
    })
    return { csrf: protection.status, status: response.status, body: await response.json() }
  }, { path: lookupPath, envelope })
}

function ledger(page: Page) {
  return page.getByRole('region', { name: '采集账本与执行' })
}

test('F 私网治理链保留同一条手动命令、本人回执与审阅证据', async ({ page, browser, baseURL }) => {
  const manifest = seed()
  const fixture = manifest.governance_chain
  test.skip(!fixture, '受守卫的 governance-chain 入口专用')
  if (!fixture) return
  test.setTimeout(100_000)
  const { job, product } = manifest.collection
  const seedFile = process.env.S08_BROWSER_SEED_FILE!
  const evidence = process.env.S08_EVIDENCE_DIR!
  const statuses: Record<string, number> = {}
  const counts: Record<string, number> = {}
  let stage = 'executor-login'
  let peer: Awaited<ReturnType<typeof browser.newContext>> | undefined
  let reviewer: Awaited<ReturnType<typeof browser.newContext>> | undefined
  let submitRequests = 0
  page.on('request', request => {
    if (new URL(request.url()).pathname === runsPath && request.method() === 'POST') submitRequests++
  })
  try {
    await signIn(page, fixture.executor)
    const execution = ledger(page)
    await expect(execution.getByRole('button', { name: '读取账本目录' })).toHaveCount(0)
    await expect(execution.getByRole('button', { name: '读取获权 Job' })).toHaveCount(0)
    await expect(page.getByRole('link', { name: '数据质量', exact: true })).toHaveCount(0)
    await expect(page.getByRole('link', { name: '系统运行', exact: true })).toHaveCount(0)

    stage = 'discover'
    await execution.getByLabel('目标整点（含时区）').fill(fixture.target)
    await execution.getByLabel('执行理由').selectOption('manual')
    const discovery = page.waitForResponse(response => at(response, catalogPath) && new URL(response.url()).searchParams.get('purpose') === 'execute')
    await execution.getByRole('button', { name: '读取执行目标', exact: true }).click()
    const targets = await discovery
    statuses.discover = targets.status()
    expect(statuses.discover).toBe(200)
    expect((await targets.json()).results).toEqual([expect.objectContaining({ job, product })])
    await execution.getByRole('button', { name: `Job ${job} · 产品 ${product}`, exact: true }).click()

    stage = 'submit-and-discard-response'
    let submission: { command: RunCommand; receipt: SubmissionReceipt; status: number } | undefined
    let routeFinished = false
    await page.route(`**${runsPath}`, async route => {
      if (route.request().method() !== 'POST') return route.continue()
      const command = route.request().postDataJSON() as RunCommand
      const real = await route.fetch({ maxRedirects: 0, maxRetries: 0 })
      submission = { command, receipt: await real.json() as SubmissionReceipt, status: real.status() }
      await route.abort('failed')
      routeFinished = true
    })
    await execution.getByRole('button', { name: '提交合成采集', exact: true }).click()
    await execution.getByRole('button', { name: '确认提交', exact: true }).click()
    await expect.poll(() => routeFinished).toBe(true)
    await page.unroute(`**${runsPath}`)
    const { command, receipt, status } = submission!
    statuses.submit = status
    expect(status).toBe(202)
    expect(command).toMatchObject({ job, reason: 'manual', request_id: expect.stringMatching(/^[\da-f-]{36}$/) })
    expect(Date.parse(command.target)).toBe(Date.parse(fixture.target))
    expect(receipt).toMatchObject({ run_id: expect.stringMatching(/^[\da-f-]{36}$/), window_id: expect.any(Number) })
    const envelope = { family: 'manual_run' as const, target: job, command }
    const windowPath = `${windowsPath}${receipt.window_id}/`
    const runPath = `${runsPath}${receipt.run_id}/`

    stage = 'leave-and-reenter'
    await expect(execution.getByRole('heading', { name: '原请求待核', exact: true })).toBeVisible()
    await page.getByRole('link', { name: '监测总览', exact: true }).click()
    await expect(execution).toHaveCount(0)
    await page.getByRole('link', { name: '采集任务', exact: true }).click()
    await expect(execution.getByText(command.request_id, { exact: true })).toBeVisible()
    const ownRead = page.waitForResponse(response => at(response, lookupPath, 'POST'))
    await execution.getByRole('button', { name: '查本人执行回执', exact: true }).click()
    const own = await ownRead
    statuses.own_receipt = own.status()
    expect(statuses.own_receipt).toBe(200)
    expect(own.request().postDataJSON()).toEqual(envelope)
    expect(await own.json()).toMatchObject({
      state: 'submitted', action: 'manual', run_id: receipt.run_id, window_id: receipt.window_id,
    })
    await expect(execution.getByRole('heading', { name: '采集受理回执', exact: true })).toBeVisible()
    await expect(execution.getByRole('button', { name: '查看原 Run', exact: true })).toHaveCount(0)
    const forbidden = await page.request.get(runPath)
    statuses.executor_run = forbidden.status()
    expect(statuses.executor_run).toBe(403)

    stage = 'peer'
    peer = await browser.newContext({ baseURL })
    const peerPage = await peer.newPage()
    await signIn(peerPage, fixture.peer)
    const other = await lookupAs(peerPage, envelope)
    expect(other.csrf).toBe(200)
    statuses.peer_receipt = other.status
    expect(statuses.peer_receipt).toBe(404)
    expect(other.body).toMatchObject({ code: 'not_found' })

    stage = 'reviewer-ledger'
    reviewer = await browser.newContext({ baseURL })
    const review = await reviewer.newPage()
    await signIn(review, manifest.accounts.ops_reviewer)
    const reviewLedger = ledger(review)
    const start = fixture.target.slice(0, 10)
    const end = new Date(Date.parse(fixture.target) + 86_400_000).toISOString().slice(0, 10)
    await reviewLedger.getByLabel('来源日期起点').fill(start)
    await reviewLedger.getByLabel('来源日期终点（不含）').fill(end)
    const directory = review.waitForResponse(response => at(response, catalogPath) && new URL(response.url()).searchParams.get('purpose') === 'ledger')
    await reviewLedger.getByRole('button', { name: '读取账本目录', exact: true }).click()
    statuses.reviewer_catalog = (await directory).status()
    expect(statuses.reviewer_catalog).toBe(200)
    const list = review.waitForResponse(response => at(response, windowsPath))
    await reviewLedger.getByRole('button', { name: `产品 ${product} · C · ${fixture.source}`, exact: true }).click()
    statuses.reviewer_windows = (await list).status()
    expect(statuses.reviewer_windows).toBe(200)
    const windowRead = review.waitForResponse(response => at(response, windowPath))
    await reviewLedger.getByRole('button', { name: `查看窗口 ${receipt.window_id}`, exact: true }).click()
    const original = await windowRead
    statuses.reviewer_window = original.status()
    expect(statuses.reviewer_window).toBe(200)
    expect(await original.json()).toMatchObject({ window_id: receipt.window_id, product, synthetic: true, runs: [{ id: receipt.run_id }] })
    const detail = reviewLedger.getByRole('region', { name: '窗口与运行证据' })
    const runRead = review.waitForResponse(response => at(response, runPath))
    await detail.getByRole('row').filter({ hasText: receipt.run_id }).getByRole('button', { name: '查看 Run', exact: true }).click()
    const run = await runRead
    statuses.reviewer_run = run.status()
    expect(statuses.reviewer_run).toBe(200)
    const dto = await run.json() as CollectionWindowProjection
    expect(dto).toMatchObject({ window_id: receipt.window_id, product, synthetic: true, coverage: { E: 7, O: 7, V: 7, accepted: 7 } })
    expect(dto.runs).toEqual([expect.objectContaining({ id: receipt.run_id, status: 'finished', result: 'new', dispatch: 'sent', deliveries: 1, batch_id: expect.any(Number) })])
    const batch = dto.runs?.[0]?.batch_id
    expect(batch).toEqual(expect.any(Number))
    expect(dto.items).toHaveLength(7)
    expect(dto.receipts).toHaveLength(7)
    for (const item of dto.items ?? []) {
      expect(item).toMatchObject({ run_id: receipt.run_id, batch_id: batch, status: 'accepted', accepted: true })
      expect(dto.receipts).toContainEqual(expect.objectContaining({ item_id: item.id, batch_id: batch, outcome: 'first', revision: { type: 'city', id: expect.any(Number) } }))
    }
    await expect(detail.getByRole('heading', { name: `Run ${receipt.run_id}`, exact: true })).toBeVisible()
    await expect(detail.getByRole('region', { name: '所选 Run 子项' }).getByText(`批次 ${batch}`, { exact: true })).toHaveCount(7)
    counts.items = dto.items?.length ?? 0
    counts.receipts = dto.receipts?.length ?? 0

    stage = 'reviewer-health'
    await review.goto(operationsPage)
    await expect(review.getByRole('heading', { name: '系统运行', exact: true })).toBeVisible()
    const operations = review.getByRole('region', { name: '业务窗口诊断' })
    await operations.getByLabel('起始日期').fill(start)
    await operations.getByLabel('结束日期').fill(end)
    const healthCatalog = review.waitForResponse(response => at(response, catalogPath) && !new URL(response.url()).searchParams.has('product'))
    await operations.getByRole('button', { name: '查找获权产品', exact: true }).click()
    const diagnosed = await healthCatalog
    statuses.health_catalog = diagnosed.status()
    expect(statuses.health_catalog).toBe(200)
    expect(new URL(diagnosed.url()).searchParams.get('purpose')).toBe('operations')
    await operations.getByLabel('获权产品').selectOption(String(product))
    const healthWindows = review.waitForResponse(response => at(response, windowsPath))
    await operations.getByRole('button', { name: '应用', exact: true }).click()
    expect((await healthWindows).status()).toBe(200)
    const healthRead = review.waitForResponse(response => at(response, healthPath) && new URL(response.url()).searchParams.get('include') === 'first_valid_write')
    await operations.getByRole('button', { name: new RegExp(`窗口 ${receipt.window_id} ·`) }).press('Enter')
    const healthy = await healthRead
    statuses.health = healthy.status()
    expect(statuses.health).toBe(200)
    const { window } = await healthy.json()
    expect(window).toMatchObject({ window_id: receipt.window_id, coverage: { E: 7, O: 7, V: 7 }, first_valid_write_state: 'known' })
    const facts = [
      ['源观测时标', window.coverage.latest_valid_at],
      ['抓取时刻', window.latest_visible_fetched_at],
      ['首次有效入库', window.latest_first_valid_write_at],
    ] as const
    for (const [name, datetime] of facts) {
      const time = operations.locator('.time-provenance > div').filter({ has: review.getByText(name, { exact: true }) }).locator('time')
      await expect(time).toHaveAttribute('datetime', datetime)
    }
    const [observed, fetched, first] = facts.map(([, time]) => Date.parse(time))
    expect(observed).toBe(Date.parse(fixture.target))
    expect(observed).toBeLessThan(fetched)
    expect(fetched).toBeLessThanOrEqual(first)
    expect(submitRequests).toBe(1)
    await writeFile(join(dirname(seedFile), 'governance-witness.json'), JSON.stringify({
      command: command.request_id, run: receipt.run_id, window: receipt.window_id, batch,
      first_write: window.latest_first_valid_write_at, fetched: window.latest_visible_fetched_at,
    }), { flag: 'wx', mode: 0o600 })
    stage = 'complete'
  } finally {
    await Promise.all([peer?.close(), reviewer?.close()])
    if (evidence) await writeFile(join(evidence, 'governance-chain.json'), JSON.stringify({ origin: 'F', stage, statuses, counts, submitRequests }))
  }
})
