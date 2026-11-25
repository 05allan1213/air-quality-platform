import { expect, test, type Page } from '@playwright/test'
import { browserSeed, type BrowserAccount, type BrowserSeed, type QualityFixture } from './seed'

interface EventDetail {
  id: number
  status: 'open' | 'in_review' | 'resolved'
  event_version: number
  disputed?: boolean
  qualification_version?: number
  current_revision?: number | null
  candidate_revisions?: number[]
  batch: number | null
  window: number | null
  receipt: number | null
  affected: { product: number; synthetic: boolean; indicator: string; object_code: string; observed_at?: string }
  evidence?: { correct_source?: string; correct_mapping?: string }
}
interface Coverage {
  E: number | null
  O: number
  V: number
  by_indicator: Record<string, { E: number | null; O: number; V: number }>
  observation_age_seconds: number | null
}

const eventPath = (id: number) => `/api/v1/admin/quality-issues/${id}/`

async function signIn(page: Page, account: BrowserAccount, product?: number) {
  const path = `/app/manage/quality?scope=synthetic${product ? `&product=${product}` : ''}`
  await page.goto(path)
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
  await page.getByLabel('用户名').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
  const listing = product && page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.pathname === '/api/v1/admin/quality-issues/' && url.searchParams.get('product') === String(product) && url.searchParams.get('scope') === 'synthetic'
  })
  await page.getByRole('button', { name: '登录', exact: true }).click()
  if (listing) expect((await listing).status()).toBe(200)
  await expect(page).toHaveURL(path)
  await expect(page.getByRole('heading', { name: '数据质量', exact: true })).toBeVisible()
}

async function discoverProduct(page: Page, seed: BrowserSeed['quality']) {
  const product = seed.close.product
  await page.getByText('查找获权产品', { exact: true }).click()
  await page.getByLabel('产品发现起始日期').fill(seed.start)
  await page.getByLabel('产品发现结束日期（不含）').fill(seed.end)
  const [discovery] = await Promise.all([
    page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === '/api/v1/admin/catalog/products/' && url.searchParams.get('purpose') === 'quality' && url.searchParams.get('scope') === 'synthetic'
    }),
    page.getByRole('button', { name: '查询获权产品' }).click(),
  ])
  expect(discovery.status()).toBe(200)
  expect(await discovery.json()).toMatchObject({ results: expect.arrayContaining([expect.objectContaining({ product: product, shape: 'C' })]) })
  const [listing] = await Promise.all([
    page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === '/api/v1/admin/quality-issues/' && url.searchParams.get('product') === String(product)
    }),
    page.getByRole('button', { name: `查看产品 ${product}（C）` }).click(),
  ])
  expect(listing.status()).toBe(200)
  expect(new URL(listing.url()).searchParams.has('start')).toBe(false)
}

async function selectEvent(page: Page, issue: number): Promise<EventDetail> {
  const row = page.getByRole('row').filter({ has: page.getByRole('rowheader', { name: String(issue), exact: true }) })
  const [response] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === eventPath(issue) && response.request().method() === 'GET'),
    row.getByRole('button', { name: '查看事件' }).click(),
  ])
  expect(response.status()).toBe(200)
  await expect(page.getByRole('heading', { name: '当前资格与候选' })).toBeVisible()
  return response.json() as Promise<EventDetail>
}

async function deniedClose(page: Page, before: EventDetail) {
  return page.evaluate(async ({ path, eventVersion }) => {
    const { csrf_token } = await fetch('/api/v1/auth/csrf/', { cache: 'no-store' }).then(response => response.json()) as { csrf_token: string }
    const result = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRFToken': csrf_token },
      body: JSON.stringify({ action: 'close', request_id: crypto.randomUUID(), reason_code: 'review_complete', expected_version: eventVersion, evidence_ref: null }),
    })
    return { status: result.status, code: (await result.json() as { code: string }).code }
  }, { path: `${eventPath(before.id)}actions/`, eventVersion: before.event_version })
}

async function csrf(page: Page) {
  const response = await page.request.get('/api/v1/auth/csrf/')
  expect(response.status()).toBe(200)
  return { 'X-CSRFToken': (await response.json() as { csrf_token: string }).csrf_token }
}

function researchQuery(fixture: QualityFixture, source: string, at = fixture.observed_at, city = fixture.city) {
  return {
    selection: {
      product: fixture.product, source, scope: 'synthetic', cities: [city], indicators: ['CO'],
      start: at, end: new Date(Date.parse(at) + 3_600_000).toISOString(),
    },
    method: 'statistics', method_version: 's05.v1', grain: 'day',
  }
}

async function statistics(page: Page, query: ReturnType<typeof researchQuery>) {
  const response = await page.request.post('/api/v1/analysis/statistics/', { data: query, headers: await csrf(page) })
  expect(response.status()).toBe(200)
  return response.json() as Promise<{ input_version: string; columns: { E: number | null; O: number; V: number }[] }>
}

async function readCoverage(page: Page, id: number) {
  const response = await page.request.get(`/api/v1/admin/collection-windows/${id}/`)
  expect(response.status()).toBe(200)
  const body = await response.json() as { window_id: number; coverage: Coverage | null }
  expect(body.window_id).toBe(id)
  expect(body.coverage).not.toBeNull()
  return body.coverage!
}

function stableCoverage({ observation_age_seconds: age, ...coverage }: Coverage) {
  expect(age).toBeGreaterThan(0)
  return coverage
}

async function windowEvidence(page: Page, fixture: QualityFixture, event: EventDetail) {
  expect(event).toMatchObject({ id: fixture.issue, window: fixture.window })
  if (fixture.batch != null) expect(event.batch).toBe(fixture.batch)
  if (fixture.receipt != null) expect(event.receipt).toBe(fixture.receipt)
  const section = page.getByRole('region', { name: '窗口覆盖与缺口' })
  const coverage = await readCoverage(page, fixture.window)
  expect(coverage).toMatchObject({ E: 7, O: 1, V: 0, by_indicator: { CO: { E: 1, O: 1, V: 0 } } })
  const counts = section.getByTestId('window-coverage').locator(':scope > div')
  for (const [name, value] of [['E 应到', coverage.E ?? '未知'], ['O 实到', coverage.O], ['V 有效', coverage.V]] as const) {
    await expect(counts.filter({ has: page.getByText(name, { exact: true }) }).locator('dd')).toHaveText(String(value))
  }
  const cell = section.getByRole('region', { name: '获准单元与缺口表' }).getByRole('row')
    .filter({ has: page.getByRole('rowheader', { name: event.affected.object_code, exact: true }) })
    .filter({ has: page.getByRole('cell', { name: event.affected.indicator, exact: true }) })
    .filter({ hasText: '存在争议' })
  await expect(cell).toHaveCount(1)
  await cell.getByRole('button', { name: '定位子项' }).click()
  const chain = page.getByLabel('当前单元链路')
  await expect(chain).toContainText(fixture.run)
  await expect(chain).toContainText(`回执 ${fixture.receipt} · 批次 ${fixture.batch}`)
  const [batchResponse] = await Promise.all([
    page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === '/api/v1/admin/observation-evidence/' && url.searchParams.get('batch') === String(fixture.batch)
    }),
    chain.getByText(`回执 ${fixture.receipt} · 批次 ${fixture.batch}`, { exact: false }).locator('..').getByRole('button', { name: '查关联批次与回执' }).click(),
  ])
  expect(batchResponse.status()).toBe(200)
  await expect(page.getByRole('region', { name: '批次回执表' })).toContainText(`${fixture.receipt}`)
  await page.getByRole('button', { name: /读取观测键/ }).click()
  await expect(page.getByRole('region', { name: '观测修订表' })).toContainText('候选')
  return coverage
}

test('reviewer is read-only; ordinary adjudicator closure leaves observation disputed', async ({ page }) => {
  const seed = browserSeed()
  const fixture = seed.quality.close
  const { product, issue } = fixture
  await signIn(page, seed.accounts.reviewer)
  await discoverProduct(page, seed.quality)
  const before = await selectEvent(page, issue)
  expect(before).toMatchObject({ id: issue, status: 'open', disputed: true, affected: { product, synthetic: true } })
  await expect(page.getByText('当前会话仅可审阅。')).toBeVisible()
  await expect(page.getByRole('button', { name: '普通结案', exact: true })).toHaveCount(0)
  await expect(page.getByText(/当前键仍有争议/)).toBeVisible()
  if (before.batch) {
    await page.getByRole('button', { name: '读取事件批次' }).click()
    const evidence = page.getByRole('region', { name: '批次与观测证据' })
    await expect(evidence.getByRole('region', { name: '批次回执表' })).toBeVisible()
    await expect(evidence.getByRole('button', { name: /读取观测键/ })).toHaveCount(0)
  }
  expect(await deniedClose(page, before)).toEqual({ status: 403, code: 'permission_denied' })
  const coverageBefore = await windowEvidence(page, fixture, before)

  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page).toHaveURL(/\/app\/login/)
  await expect(page.getByRole('region', { name: '事件详情与裁决' })).toHaveCount(0)
  await signIn(page, seed.accounts.adjudicator, product)
  const current = await selectEvent(page, issue)
  expect(current.event_version).toBe(before.event_version)
  const open = page.getByRole('button', { name: '普通结案', exact: true })
  await open.focus()
  await open.press('Enter')
  await expect(page.getByRole('group', { name: '确认普通结案' })).toContainText('普通结案只记录审阅决定，不解除争议。')
  const confirm = page.getByRole('button', { name: '确认提交', exact: true })
  await expect(confirm).toBeFocused()
  const [write, readback] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === `${eventPath(issue)}actions/` && response.request().method() === 'POST'),
    page.waitForResponse(response => new URL(response.url()).pathname === eventPath(issue) && response.request().method() === 'GET'),
    confirm.press('Enter'),
  ])
  expect(write.status()).toBe(200)
  const payload = write.request().postDataJSON() as Record<string, unknown>
  expect(payload).toMatchObject({ action: 'close', reason_code: 'review_complete', expected_version: current.event_version, evidence_ref: null, request_id: expect.stringMatching(/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/) })
  expect(await write.json()).toMatchObject({ status: 'resolved', event_version: current.event_version + 1, outcome: 'close' })
  expect(readback.status()).toBe(200)
  expect(await readback.json()).toMatchObject({
    status: 'resolved', event_version: current.event_version + 1, disputed: true,
    current_revision: current.current_revision, qualification_version: current.qualification_version, candidate_revisions: current.candidate_revisions,
  })
  await expect(page.getByRole('heading', { name: '裁决回执' })).toBeVisible()
  await expect(page.getByText(/当前键仍有争议/)).toBeVisible()
  await expect(open).toBeDisabled()
  expect(stableCoverage(await readCoverage(page, fixture.window))).toEqual(stableCoverage(coverageBefore))
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10)
  const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10)
  await page.getByText('查本事件治理经过', { exact: true }).click()
  await page.getByLabel('审计起始日期（UTC）').fill(yesterday)
  await page.getByLabel('审计结束日期（UTC，不含）').fill(tomorrow)
  const [audit] = await Promise.all([
    page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === '/api/v1/admin/object-audit/' && url.searchParams.get('kind') === 'quality_issue' && url.searchParams.get('id') === String(issue)
    }),
    page.getByRole('button', { name: '读取本事件审计' }).click(),
  ])
  expect(audit.status()).toBe(200)
  expect(await audit.json()).toMatchObject({ results: [expect.objectContaining({
    action: 'close', outcome: 'applied', actor_relation: 'self', before_version: current.event_version, after_version: current.event_version + 1,
  })] })
  await expect(page.getByRole('region', { name: '本事件审计' })).toContainText('本人')
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  }
})

test('cross-key F mapping requires the signed proof for the selected target', async ({ page }) => {
  const seed = browserSeed()
  const fixture = seed.quality.correct_mapping
  await signIn(page, seed.accounts.adjudicator, fixture.product)
  const before = await selectEvent(page, fixture.issue)
  const origin = researchQuery(fixture, seed.quality.source)
  const target = researchQuery(fixture, seed.quality.source, fixture.observed_at, fixture.target_city)
  const beforeOrigin = await statistics(page, origin)
  const beforeTarget = await statistics(page, target)
  expect(beforeOrigin.columns[0]).toMatchObject({ O: 1, V: 1 })
  expect(beforeTarget.columns[0]).toMatchObject({ O: 1, V: 1 })
  await page.getByText('核验跨键映射目标（F）', { exact: true }).click()
  await page.getByLabel('目标对象代码').fill(fixture.target)
  await expect(page.getByRole('button', { name: '映射更正（待核）', exact: true })).toBeDisabled()
  const [preview] = await Promise.all([
    page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === eventPath(fixture.issue) && url.searchParams.get('mapping_target') === fixture.target
    }),
    page.getByRole('button', { name: '核验目标', exact: true }).click(),
  ])
  expect(preview.status()).toBe(200)
  const proof = await preview.json() as EventDetail
  expect(proof.event_version).toBe(before.event_version)
  expect(proof.evidence?.correct_mapping).toBeTruthy()
  await page.getByRole('button', { name: '映射更正', exact: true }).click()
  await expect(page.getByRole('group', { name: '确认映射更正' })).toContainText(fixture.target)
  const [write] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === `${eventPath(fixture.issue)}actions/`),
    page.getByRole('button', { name: '确认提交', exact: true }).click(),
  ])
  expect(write.status()).toBe(200)
  expect((write.request().postDataJSON() as { evidence_ref: string }).evidence_ref === proof.evidence?.correct_mapping).toBe(true)
  expect(await write.json()).toMatchObject({
    outcome: 'mapping_conflict',
    event_version: before.event_version + 1,
    affected: expect.arrayContaining([expect.objectContaining({ object_code: before.affected.object_code }), expect.objectContaining({ object_code: fixture.target })]),
  })
  await expect(page.getByRole('status').filter({ has: page.getByRole('heading', { name: '裁决回执' }) })).toContainText(fixture.target)
  expect(await readCoverage(page, fixture.window)).toMatchObject({ E: 7, O: 1, V: 0 })
  expect(await readCoverage(page, fixture.target_window)).toMatchObject({ E: 7, O: 1, V: 0 })
  const targetHistory = await page.request.get(`/api/v1/admin/collection-windows/${fixture.target_window}/`)
  expect(await targetHistory.json()).toMatchObject({
    runs: expect.arrayContaining([expect.objectContaining({ id: fixture.target_run })]),
    receipts: expect.arrayContaining([expect.objectContaining({ id: fixture.target_receipt })]),
  })
  for (const [query, version] of [[origin, beforeOrigin.input_version], [target, beforeTarget.input_version]] as const) {
    const after = await statistics(page, query)
    expect(after.input_version).not.toBe(version)
    expect(after.columns[0]).toMatchObject({ O: 1, V: 0 })
  }
})

test('signed F correction is confirmed from the current event and brings valid coverage back', async ({ page }) => {
  const seed = browserSeed()
  const fixture = seed.quality.correct_source
  await signIn(page, seed.accounts.adjudicator, fixture.product)
  const before = await selectEvent(page, fixture.issue)
  expect(before).toMatchObject({ status: 'open', disputed: true, window: fixture.window })
  expect(before.evidence?.correct_source).toBeTruthy()
  const coverage = await readCoverage(page, fixture.window)
  const query = researchQuery(fixture, seed.quality.source)
  const sibling = researchQuery(fixture, seed.quality.source, fixture.sibling_at)
  const previous = await statistics(page, query)
  const untouched = await statistics(page, sibling)
  expect(previous.columns[0]).toMatchObject({ O: 1, V: 0 })
  expect(untouched.columns[0]).toMatchObject({ O: 1, V: 1 })
  const prepared = await page.request.post('/api/v1/me/exports/', {
    data: { kind: 'detail', groups: [{ ...query, input_version: previous.input_version }] }, headers: await csrf(page),
  })
  expect(prepared.status()).toBe(201)
  const { id: ticket } = await prepared.json() as { id: string }
  const download = `/api/v1/me/exports/${ticket}/download/?scope=synthetic`
  expect((await page.request.get(download)).status()).toBe(200)
  await page.getByRole('button', { name: '可信来源更正', exact: true }).click()
  const [write, readback] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === `${eventPath(fixture.issue)}actions/`),
    page.waitForResponse(response => new URL(response.url()).pathname === eventPath(fixture.issue) && response.request().method() === 'GET'),
    page.getByRole('button', { name: '确认提交', exact: true }).click(),
  ])
  expect(write.status()).toBe(200)
  expect((write.request().postDataJSON() as { evidence_ref: string }).evidence_ref === before.evidence?.correct_source).toBe(true)
  expect(await write.json()).toMatchObject({ status: 'resolved', outcome: 'correct_source', event_version: before.event_version + 1 })
  expect(await readback.json()).toMatchObject({ status: 'resolved', disputed: false })
  await expect(page.getByText('当前键未标记争议。')).toBeVisible()
  await expect(page.getByRole('heading', { name: '裁决回执' })).toBeVisible()
  const indicator = before.affected.indicator
  const updated = await readCoverage(page, fixture.window)
  expect(updated).toMatchObject({ E: coverage.E, O: coverage.O, V: coverage.V + 1 })
  expect(updated.by_indicator[indicator].V).toBe(coverage.by_indicator[indicator].V + 1)
  const recomputed = await statistics(page, query)
  expect(recomputed.columns[0]).toMatchObject({ O: 1, V: 1 })
  expect(recomputed.input_version).not.toBe(previous.input_version)
  expect(await statistics(page, sibling)).toEqual(untouched)
  const details = '/api/v1/analysis/details/'
  const obsolete = await page.request.post(details, {
    data: { ...query, input_version: previous.input_version, support: 'effective' }, headers: await csrf(page),
  })
  expect(obsolete.status()).toBe(409)
  expect(await obsolete.json()).toEqual({ code: 'input_changed' })
  const next = await page.request.post(details, {
    data: { ...query, input_version: recomputed.input_version, support: 'effective' }, headers: await csrf(page),
  })
  expect(next.status()).toBe(200)
  const { results: rows } = await next.json() as { results: { value: string }[] }
  expect(rows.map(row => Number(row.value))).toEqual([30])
  const revoked = await page.request.get(download)
  expect(revoked.status()).toBe(409)
  expect(revoked.headers()['content-disposition']).toBeUndefined()
  expect(await revoked.json()).toEqual({ code: 'input_changed' })
})

test('F station change recovers its original receipt after losing the real write response', async ({ page }) => {
  const seed = browserSeed()
  const instance = seed.quality.station
  const path = `/api/v1/admin/stations/${instance.station}/changes/`
  await signIn(page, seed.accounts.station_manager)
  const station = page.getByRole('region', { name: '站址治理' })
  await station.getByLabel('站点 ID', { exact: true }).fill(String(instance.station))
  await station.getByLabel('站址产品 ID').fill(String(instance.product))
  await station.getByLabel('站点标识 ID').fill(String(instance.identifier))
  await station.getByLabel('生效时刻（含时区）').fill(instance.at)
  await station.getByLabel('新名称').fill(instance.target)
  const [preview] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === path && response.request().method() === 'GET'),
    station.getByRole('button', { name: '预览站址变更' }).click(),
  ])
  expect(preview.status()).toBe(200)
  const { version, evidence_ref } = await preview.json() as { version: number; evidence_ref: string }
  expect(evidence_ref).toBeTruthy()
  let writes = 0
  let original: Record<string, unknown> | undefined
  let applied: { action_id: string; status: string; version: number; affected: { old_epoch: number } } | undefined
  await page.route(`**${path}`, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    writes++
    original = route.request().postDataJSON() as Record<string, unknown>
    const result = await route.fetch()
    expect(result.status()).toBe(200)
    applied = await result.json() as NonNullable<typeof applied>
    await route.abort()
  })
  await Promise.all([
    page.waitForEvent('requestfailed', request => new URL(request.url()).pathname === path && request.method() === 'POST'),
    station.getByRole('button', { name: '确认提交站址变更' }).press('Enter'),
  ])
  const lookup = station.getByRole('button', { name: '只查询原始回执' })
  await expect(lookup).toBeVisible()
  expect(original).toMatchObject({ action: 'rename', reason_code: 'documented_rename', expected_version: version })
  expect(original?.evidence_ref === evidence_ref).toBe(true)
  expect(applied).toMatchObject({ status: 'applied', version: version + 1, affected: { old_epoch: instance.site_epoch } })
  const [found] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/admin/command-receipts/lookup/'),
    lookup.click(),
  ])
  expect(found.status()).toBe(200)
  expect(found.request().postDataJSON()).toEqual({ family: 'station_change', target: instance.station, command: original })
  expect(await found.json()).toMatchObject({ action_id: applied?.action_id, state: 'applied', version: version + 1 })
  await expect(station.getByRole('heading', { name: '站址回执' })).toBeVisible()
  await expect(station.getByRole('status')).toContainText(`目录版本 v${version + 1}`)
  expect(writes).toBe(1)
})
