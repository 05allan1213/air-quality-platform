import { expect, test, type Page, type Response } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { browserDiagnostics } from './diagnostics'
import { browserSeed, type BrowserAccount } from './seed'

const operationsPath = '/app/manage/operations?scope=synthetic'
const operationsApi = '/api/v1/admin/operations/'

async function signIn(page: Page, account: BrowserAccount) {
  const login = new URLSearchParams({ next: operationsPath.slice('/app'.length) })
  await page.goto(`/app/login?${login}`)
  await page.getByLabel('用户名').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(operationsPath)
}

async function openOperations(page: Page, account: BrowserAccount) {
  await signIn(page, account)
  await expect(page.getByRole('heading', { name: '系统运行', exact: true })).toBeVisible()
}

function isResponse(response: Response, pathname: string, method = 'GET') {
  return new URL(response.url()).pathname === pathname && response.request().method() === method
}

function record(name: string, values: Record<string, unknown>) {
  const evidence = process.env.S08_EVIDENCE_DIR
  if (evidence) writeFileSync(join(evidence, `operations-${name}.json`), JSON.stringify({ origin: 'F', ...values }, null, 2))
}

async function capture(page: Page, name: string) {
  const evidence = process.env.S08_EVIDENCE_DIR
  if (evidence) await page.screenshot({ path: join(evidence, `operations-${name}.png`), fullPage: true, mask: [page.locator('.account-link')] })
}

test('pure diagnostics reads only component evidence and cannot probe an enhanced window', async ({ page }) => {
  const seed = browserSeed()
  const seen: string[] = []
  page.on('request', request => {
    const { pathname, search } = new URL(request.url())
    if (pathname.startsWith('/api/v1/admin/')) seen.push(pathname + search)
  })
  const service = page.waitForResponse(response => isResponse(response, `${operationsApi}health/`))
  await openOperations(page, seed.accounts.diagnostics)
  const health = await service
  expect(health.status()).toBe(200)
  expect((await health.json()).window).toBeNull()
  const components = page.getByRole('list', { name: '五项组件状态' })
  await expect(components.getByRole('listitem')).toHaveCount(5)
  for (const name of ['Web 服务', 'PostgreSQL', 'Redis', '采集 Worker', '调度 Beat']) {
    await expect(components.getByRole('heading', { name, exact: true })).toBeVisible()
  }
  await expect(components.getByText('队列订阅')).toBeVisible()
  await expect(page.getByRole('heading', { name: '业务窗口诊断' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '对象审计' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '许可与处置交接' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '受限管理' })).toHaveCount(0)
  expect(seen).toEqual([`${operationsApi}health/`])
  const denied = await page.request.get(`${operationsApi}health/`, { params: { include: 'first_valid_write', window: seed.operations.window } })
  expect(denied.status()).toBe(403)
  expect(await denied.json()).toEqual({ code: 'permission_denied' })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await capture(page, 'services-390')
  record('services', { health: health.status(), components: 5, denied: denied.status(), privateAreasHidden: true })
})

test('account and dictionary workspace entries require distinct named capabilities', async ({ browser, page, baseURL }) => {
  const seed = browserSeed()
  await openOperations(page, seed.accounts.account_admin)
  await expect(page.getByRole('link', { name: '账号目录' })).toHaveAttribute('href', '/admin/auth/user/')
  await expect(page.getByRole('link', { name: '城市检索别名' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '服务组件' })).toHaveCount(0)
  await page.getByRole('link', { name: '账号目录' }).click()
  await expect(page).toHaveURL(/\/admin\/auth\/user\/$/)
  const forbiddenAlias = await page.request.get('/admin/observations/city-search-alias/')
  expect(forbiddenAlias.status()).toBe(403)

  const dictionary = await browser.newContext({ baseURL })
  try {
    const editor = await dictionary.newPage()
    await openOperations(editor, seed.accounts.admin)
    await expect(editor.getByRole('link', { name: '账号目录' })).toHaveCount(0)
    await expect(editor.getByRole('link', { name: '城市检索别名' })).toHaveAttribute('href', '/admin/observations/city-search-alias/')
    await editor.getByRole('link', { name: '城市检索别名' }).click()
    await expect(editor).toHaveURL(/\/admin\/observations\/city-search-alias\/$/)
    const forbiddenAccount = await editor.request.get('/admin/auth/user/')
    expect(forbiddenAccount.status()).toBe(403)
    record('native-admin', { accountDirectory: true, aliasDirectory: true, forbiddenAccount: forbiddenAccount.status(), forbiddenAlias: forbiddenAlias.status() })
  } finally {
    await dictionary.close()
  }
})

async function verifyAuthorizedWindow(page: Page, at: (phase: string) => void) {
  const seed = browserSeed()
  const fixture = seed.operations
  at('login')
  await openOperations(page, seed.accounts.ops_reviewer)
  at('product-discovery')
  const picker = page.getByRole('region', { name: '业务窗口诊断' })
  await picker.getByLabel('起始日期').fill(fixture.start)
  await picker.getByLabel('结束日期').fill(fixture.end)
  const directory = page.waitForResponse(response => isResponse(response, '/api/v1/admin/catalog/products/') && !new URL(response.url()).searchParams.has('product'))
  await picker.getByRole('button', { name: '查找获权产品' }).click()
  const catalog = await directory
  expect(catalog.status()).toBe(200)
  const catalogQuery = new URL(catalog.url()).searchParams
  expect(catalogQuery.get('purpose')).toBe('operations')
  expect(catalogQuery.get('scope')).toBe('synthetic')
  await picker.getByLabel('获权产品').selectOption(String(fixture.product))
  at('apply-product')
  const windows = page.waitForResponse(response => isResponse(response, '/api/v1/admin/collection-windows/'))
  await picker.getByRole('button', { name: '应用', exact: true }).click()
  expect((await windows).status()).toBe(200)

  at('window-diagnosis')
  const enhanced = page.waitForResponse(response => isResponse(response, `${operationsApi}health/`) && new URL(response.url()).searchParams.has('include'))
  await picker.getByRole('button', { name: new RegExp(`窗口 ${fixture.window} ·`) }).press('Enter')
  const snapshot = await enhanced
  expect(snapshot.status()).toBe(200)
  const dto = (await snapshot.json()).window as {
    window_id: number; latest_first_valid_write_at: string | null; first_valid_write_state: string;
    coverage: { latest_valid_at: string | null } | null; latest_visible_fetched_at: string | null;
  }
  expect(dto.window_id).toBe(fixture.window)
  expect(dto).toMatchObject({ state: 'open', result: 'partial', coverage: { E: 7, O: 1, V: 1 } })
  expect(new URL(snapshot.url()).searchParams.get('include')).toBe('first_valid_write')
  const facts = [
    ['源观测时标', dto.coverage?.latest_valid_at ?? null],
    ['抓取时刻', dto.latest_visible_fetched_at],
    ['首次有效入库', dto.latest_first_valid_write_at],
  ] as const
  for (const [name, datetime] of facts) {
    const time = picker.locator('.time-provenance > div').filter({ has: page.getByText(name, { exact: true }) }).locator('time')
    if (datetime) await expect(time).toHaveAttribute('datetime', datetime)
    else await expect(time).toHaveText('—')
  }
  expect(dto.first_valid_write_state).toBe('known')
  const instants = facts.map(([, datetime]) => Date.parse(datetime ?? ''))
  expect(instants).toEqual([fixture.observed_at, fixture.fetched_at, fixture.first_valid_write_at].map(Date.parse))
  expect(instants[0]).toBeLessThan(instants[1])
  expect(instants[1]).toBeLessThan(instants[2])
  await expect(picker.getByText('最早可见排队')).toBeVisible()
  await expect(picker.getByRole('heading', { name: '获权运行错误摘要' })).toBeVisible()

  at('window-trend')
  const pendingTrend = page.waitForResponse(response => isResponse(response, `${operationsApi}trends/`))
  await page.getByRole('button', { name: '读取趋势' }).press('Enter')
  const trend = await pendingTrend
  expect(trend.status()).toBe(200)
  const table = page.getByRole('region', { name: '窗口日趋势表格，可横向滚动' })
  await expect(table.getByRole('rowheader', { name: fixture.start })).toBeVisible()
  await expect(page.getByText('源发布时间无记录，不以抓取时刻代替。')).toBeVisible()
  await capture(page, 'windows-1440')

  await page.setViewportSize({ width: 390, height: 844 })
  await table.focus()
  await expect(table).toBeFocused()
  await expect.poll(() => table.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await capture(page, 'windows-390')
  record('windows', { catalog: catalog.status(), scope: 'synthetic', window: snapshot.status(), trend: trend.status(), firstValidWriteState: dto.first_valid_write_state, allClockBindingsAsserted: true, localScroll: true })
  at('complete')
}

test('authorized diagnostics discovers a product, separates window clocks and reads bounded daily trends', async ({ page, baseURL }, testInfo) => {
  const diagnostics = browserDiagnostics(testInfo, 'operations-window-diagnostic.json', baseURL, ({ pathname, searchParams }) => {
    if (pathname === '/api/v1/admin/catalog/products/') return searchParams.has('product') ? 'product-verification' : 'product-discovery'
    if (pathname === '/api/v1/admin/collection-windows/') return 'collection-windows'
    if (pathname === `${operationsApi}health/`) return searchParams.has('include') ? 'window-health' : 'service-health'
    if (pathname === `${operationsApi}trends/`) return 'window-trend'
    if (pathname === '/api/v1/auth/login/' || pathname === '/app/login') return 'login'
    if (pathname === '/app/' || pathname === '/app/manage/operations') return 'application'
  })
  diagnostics.watch(page)
  let failure: unknown
  try {
    await verifyAuthorizedWindow(page, diagnostics.at)
  } catch (error) {
    failure = error
    throw error
  } finally {
    await diagnostics.save(page, failure)
  }
})

test('grant manager recovers the original withdrawal after editing the handoff and leaving operations', async ({ page }) => {
  const seed = browserSeed()
  const { operations: fixture } = seed
  await openOperations(page, seed.accounts.grant_manager)
  await expect(page.getByRole('heading', { name: '服务组件' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '受限管理' })).toHaveCount(0)

  const audit = page.getByRole('region', { name: '对象审计', exact: true })
  await expect(audit.getByLabel('审计对象类型').getByRole('option')).toHaveCount(1)
  await expect(audit.getByLabel('审计对象类型')).toHaveValue(fixture.audit.kind)
  await audit.getByLabel('审计对象编号').fill(String(fixture.audit.id))
  await audit.getByLabel('审计开始 UTC').fill(new Date(fixture.audit.start).toISOString().slice(0, 16))
  await audit.getByLabel('审计结束 UTC').fill(new Date(fixture.audit.end).toISOString().slice(0, 16))
  const auditResponse = page.waitForResponse(response => isResponse(response, '/api/v1/admin/object-audit/'))
  await audit.getByRole('button', { name: '查看对象审计' }).press('Enter')
  const history = await auditResponse
  expect(history.status()).toBe(200)
  const row = (await history.json()).results[0] as Record<string, unknown>
  expect(Object.keys(row).sort()).toEqual(['action', 'actor_relation', 'after_version', 'at', 'before_version', 'outcome'])
  expect(row).toMatchObject({ action: 'withdraw', actor_relation: 'other', outcome: 'applied' })
  await expect(audit.getByRole('row').filter({ hasText: 'withdraw' })).toContainText('其他操作者')

  const grants = page.getByRole('region', { name: '许可与处置交接' })
  const product = grants.getByLabel('交接产品编号')
  const grantPath = `/api/v1/admin/grants/${fixture.grant.id}/actions/`
  const previewPath = `/api/v1/admin/disposition-products/${fixture.grant.product}/`
  const readPending = () => Promise.all([
    page.waitForResponse(response => isResponse(response, previewPath)),
    grants.getByRole('button', { name: '读取处置待办' }).click(),
  ])
  let writes = 0
  page.on('request', request => {
    if (request.method() === 'POST' && /^\/api\/v1\/admin\/grants\/\d+\/actions\/$/.test(new URL(request.url()).pathname)) writes += 1
  })
  await product.fill(String(seed.products.C))
  await grants.getByLabel('交接许可编号').fill(String(fixture.grant.id))
  await grants.getByRole('button', { name: '核对许可' }).click()
  await expect(grants.getByRole('alert')).toHaveText('许可与交接产品不匹配。请核对交接资料。')
  await expect(grants.getByRole('button', { name: '确认撤回许可' })).toHaveCount(0)
  expect(writes).toBe(0)

  await product.fill(String(fixture.grant.product))
  // The Worker startup reconciles the historical withdrawal before the browser starts.
  const [initialPreview] = await readPending()
  expect(initialPreview.status()).toBe(200)
  const initial = await initialPreview.json() as { product: number; version: number; jobs: unknown[] }
  expect(initial.product).toBe(fixture.grant.product)
  expect(initial.version).toBeGreaterThan(fixture.grant.version)
  expect(initial.jobs).toHaveLength(0)
  await expect(grants.getByText('当前无到期待办', { exact: false })).toBeVisible()

  const pendingVerify = page.waitForResponse(response => isResponse(response, grantPath))
  await grants.getByRole('button', { name: '核对许可' }).click()
  const verified = await pendingVerify
  expect(verified.status()).toBe(200)
  expect(await verified.json()).toEqual({
    product: fixture.grant.product, grant: fixture.grant.id, version: initial.version, state: 'registered',
  })
  await expect(grants.getByText(`产品 ${fixture.grant.product} · 许可 ${fixture.grant.id} · 版本 ${initial.version} · 已登记`)).toBeVisible()
  const submit = grants.getByRole('button', { name: '确认撤回许可' })
  await expect(submit).toBeDisabled()
  await grants.getByLabel('撤回原因').selectOption('terms_ended')
  await grants.getByRole('checkbox').check()
  // Commit the real write and lose only the browser-facing response.
  let submitted: { sent: unknown; result: { state: string; version: number; action_id: string }; status: number } | undefined
  await page.route(url => url.pathname === grantPath, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    const upstream = await route.fetch({ maxRetries: 0 })
    submitted = { sent: route.request().postDataJSON(), result: await upstream.json(), status: upstream.status() }
    await route.abort('connectionreset')
  }, { times: 1 })
  await submit.click()
  const checkReceipt = grants.getByRole('button', { name: '查询本人撤回回执', exact: true })
  await expect(checkReceipt).toBeVisible()
  expect(submitted?.status).toBe(200)
  const { sent, result, status } = submitted!
  expect(sent).toEqual({ action: 'withdraw', reason_code: 'terms_ended', expected_version: initial.version, request_id: expect.any(String) })
  expect(result.state).toBe('withdrawn')
  expect(result.version).toBeGreaterThan(initial.version)
  await expect(grants.getByText(/^许可已撤回/)).toHaveCount(0)

  await product.fill(String(seed.products.C))
  const handoffGrant = grants.getByLabel('交接许可编号')
  await handoffGrant.fill(String(fixture.audit.id))
  await expect(checkReceipt).toBeVisible()
  await page.getByRole('link', { name: '监测总览', exact: true }).click()
  await expect(grants).toHaveCount(0)
  await page.getByRole('link', { name: '系统运行', exact: true }).click()
  await expect(product).toHaveValue('')
  await expect(handoffGrant).toHaveValue('')
  await expect(checkReceipt).toBeVisible()
  const pendingLookup = page.waitForResponse(response => isResponse(response, '/api/v1/admin/command-receipts/lookup/', 'POST'))
  await checkReceipt.click()
  const lookup = await pendingLookup
  expect(lookup.status()).toBe(200)
  expect(await lookup.json()).toMatchObject(result)
  expect(lookup.request().postDataJSON()).toEqual({
    family: 'grant_withdraw', target: fixture.grant.id, command: sent,
  })
  await expect(grants.getByText(/^许可已撤回/)).toBeVisible()
  expect(writes).toBe(1)

  await product.fill(String(fixture.grant.product))
  const [preview] = await readPending()
  expect(preview.status()).toBe(200)
  expect(await preview.json()).toMatchObject({
    product: fixture.grant.product, version: result.version,
    jobs: [{ id: expect.any(Number), reason: 'revocation', error_code: '' }],
  })
  await expect(grants.getByRole('listitem').filter({ hasText: 'revocation' })).toBeVisible()

  // The audit form was also remounted by navigation.
  await audit.getByLabel('审计开始 UTC').fill(new Date(fixture.audit.start).toISOString().slice(0, 16))
  await audit.getByLabel('审计结束 UTC').fill(new Date(fixture.audit.end).toISOString().slice(0, 16))
  await audit.getByLabel('审计对象编号').fill(String(fixture.grant.id))
  const selfAudit = page.waitForResponse(response => isResponse(response, '/api/v1/admin/object-audit/'))
  await audit.getByRole('button', { name: '查看对象审计' }).click()
  expect((await selfAudit).status()).toBe(200)
  await expect(audit.getByRole('row').filter({ hasText: 'withdraw' })).toContainText('本人')
  record('grant-handoff', { audit: history.status(), initialPreview: initialPreview.status(), preview: preview.status(), write: status, lookup: lookup.status(), startupReconciled: true, freshPendingRevocation: true, editedAndReturned: true, exactHandoffAndOriginalCommand: true, singleWrite: writes === 1 })
})
