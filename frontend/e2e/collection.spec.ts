import { randomUUID } from 'node:crypto'
import { join } from 'node:path'
import { expect, test, type Page, type Request } from '@playwright/test'
import { browserSeed } from './seed'

type ScheduleResult = {
  job: number
  state: 'draft' | 'active' | 'discarded'
  version: number
  first_due_at: string
  action_id: string
}

type AccountVersion = { version: number }

async function signIn(page: Page, username: string, password: string) {
  await page.goto('/app/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/app\/$/)
}

async function openCollection(page: Page, username: string, password: string) {
  await signIn(page, username, password)
  await page.getByLabel('数据域').selectOption('synthetic')
  await expect.poll(() => page.evaluate(() => new URL(location.href).searchParams.get('scope'))).toBe('synthetic')
  await page.goto('/app/manage/collection?scope=synthetic')
  await expect(page.getByRole('heading', { name: '采集任务' })).toBeVisible()
  await expect(page.locator('.schedule-table tbody tr').first()).toBeVisible()
  await page.getByRole('button', { name: '打开计划' }).click()
}

async function governanceRequest(
  page: Page,
  path: string,
  method: 'GET' | 'POST',
  payload?: Record<string, unknown>,
) {
  return page.evaluate(async ({ path, method, payload }) => {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (method === 'POST') {
      const csrf = await fetch('/api/v1/auth/csrf/', { credentials: 'same-origin' }).then(response => response.json()) as { csrf_token: string }
      headers['Content-Type'] = 'application/json'
      headers['X-CSRFToken'] = csrf.csrf_token
    }
    const response = await fetch(`/api/v1/${path}`, {
      method,
      credentials: 'same-origin',
      headers,
      body: method === 'POST' ? JSON.stringify(payload) : undefined,
    })
    return { status: response.status, body: response.status === 204 ? null : await response.json() as unknown }
  }, { path, method, payload: payload ?? null })
}

function addHours(instant: string, hours: number) {
  return new Date(Date.parse(instant) + hours * 60 * 60 * 1000).toISOString().replace('.000Z', 'Z')
}

function isScheduleWrite(request: Request) {
  return request.method() === 'POST' && /\/api\/v1\/admin\/collection-jobs(?:\/\d+\/schedule)?\/$/.test(new URL(request.url()).pathname)
}

test('collection workspace hides schedule configuration without the named permission', async ({ page }) => {
  const seed = browserSeed()
  const scheduleRequests: number[] = []
  page.on('response', response => {
    const url = new URL(response.url())
    if ((url.pathname === '/api/v1/admin/catalog/products/' && url.searchParams.get('purpose') === 'configure') || /\/api\/v1\/admin\/collection-jobs\/\d+\/schedule\/$/.test(url.pathname)) {
      scheduleRequests.push(response.status())
    }
  })

  await page.goto('/app/login')
  await page.getByLabel('用户名').fill(seed.accounts.operator.username)
  await page.getByLabel('密码').fill(seed.accounts.operator.password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/app\/$/)
  await page.getByLabel('数据域').selectOption('synthetic')
  await page.goto('/app/manage/collection?scope=synthetic')

  await expect(page.getByRole('heading', { name: '采集任务' })).toBeVisible()
  await expect(page.getByText('当前账号没有周期配置权限。')).toBeVisible()
  await expect(page.getByRole('button', { name: '重新读取' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '建立停用草稿' })).toHaveCount(0)
  await expect.poll(() => scheduleRequests).toEqual([])
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('authorized schedule editor creates, hands off, rereads conflicts, and clears after revocation', async ({ page, browser }) => {
  const seed = browserSeed()
  const evidence = process.env.S08_EVIDENCE_DIR
  if (!evidence) throw new Error('S08_EVIDENCE_DIR is required for schedule evidence.')
  const screenshotOptions = { fullPage: true, mask: [page.locator('.account-link'), page.locator('.schedule-receipt')] }

  await openCollection(page, seed.accounts.schedule_editor.username, seed.accounts.schedule_editor.password)
  await expect(page.getByRole('heading', { name: '新建停用草稿' })).toBeVisible()
  await expect(page.locator('.schedule-table tbody tr').first()).toContainText('s03.fixture · s03.f.v1')

  // Deliver the real write exactly once, then drop its response before the UI receives it.
  const createPath = '/api/v1/admin/collection-jobs/'
  let committed: ScheduleResult | undefined
  let original: unknown
  let sent = 0
  page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === createPath) sent++ })
  await page.route(url => url.pathname === createPath, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    original = route.request().postDataJSON()
    const upstream = await route.fetch({ maxRetries: 0 })
    expect(upstream.status()).toBe(201)
    committed = await upstream.json() as ScheduleResult
    await route.abort('connectionreset')
  }, { times: 1 })
  await page.getByRole('button', { name: '建立停用草稿' }).click()
  await expect(page.getByRole('button', { name: '查本人计划回执' })).toBeVisible()
  expect(committed?.state).toBe('draft')
  const draft = committed!
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await expect(page.getByRole('button', { name: '激活计划' })).toBeDisabled()
  await page.getByRole('link', { name: '我的空间', exact: true }).click()
  await page.getByRole('link', { name: '采集任务', exact: true }).click()
  await page.getByRole('button', { name: '打开计划' }).click()
  await expect(page.getByRole('button', { name: '激活计划' })).toBeDisabled()
  const [lookup] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/admin/command-receipts/lookup/'),
    page.getByRole('button', { name: '查本人计划回执' }).click(),
  ])
  expect(lookup.status()).toBe(200)
  expect(lookup.request().postDataJSON()).toEqual({ family: 'schedule', target: seed.collection.schedule_template, command: original })
  expect(await lookup.json()).toMatchObject({ action: 'create_draft', action_id: draft.action_id, job: draft.job, version: draft.version, state: 'draft' })
  expect(sent).toBe(1)
  await expect(page.locator('.schedule-facts')).toContainText('草稿')
  await expect(page.getByText('草稿尚未启用，不会投递采集任务。')).toBeVisible()

  await page.getByRole('button', { name: '激活计划' }).click()
  await expect(page.getByRole('group', { name: '确认计划操作' })).toBeVisible()
  const activateDraftWaiter = page.waitForResponse(response => isScheduleWrite(response.request()) && response.status() === 200)
  await page.getByRole('button', { name: '确认激活' }).click()
  const activeDraft = await activateDraftWaiter.then(response => response.json()) as ScheduleResult
  expect(activeDraft).toMatchObject({ job: draft.job, state: 'active' })
  await expect(page.locator('.schedule-facts')).toContainText('生效中')
  await page.screenshot({ path: join(evidence, 'collection-schedule-active.png'), ...screenshotOptions })

  const successorDue = addHours(draft.first_due_at, 2)
  await page.getByLabel('运行频率').selectOption('7200')
  await page.getByLabel('首次到期时刻（含时区）').fill(successorDue)
  const proposeWaiter = page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/v1/admin/collection-jobs/${draft.job}/schedule/` && response.request().method() === 'POST')
  await page.getByRole('button', { name: '提交后继草稿' }).click()
  const proposedResponse = await proposeWaiter
  expect(proposedResponse.status()).toBe(201)
  const proposed = await proposedResponse.json() as ScheduleResult
  expect(proposed.state).toBe('draft')
  await expect(page.getByRole('heading', { name: `后继草稿 Job ${proposed.job}` })).toBeVisible()

  await page.getByRole('button', { name: '激活计划' }).click()
  const activateSuccessorWaiter = page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/v1/admin/collection-jobs/${proposed.job}/schedule/` && response.request().method() === 'POST')
  await page.getByRole('button', { name: '确认激活' }).click()
  const activeSuccessor = await activateSuccessorWaiter.then(response => response.json()) as ScheduleResult
  expect(activeSuccessor).toMatchObject({ job: proposed.job, state: 'active' })
  await expect(page.locator('.schedule-facts')).toContainText('生效中')

  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const tableScroll = await page.locator('.schedule-table-scroll').evaluate(element => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    overflowX: getComputedStyle(element).overflowX,
  }))
  expect(tableScroll.scrollWidth).toBeGreaterThan(tableScroll.clientWidth)
  expect(tableScroll.overflowX).toBe('auto')
  await page.screenshot({ path: join(evidence, 'collection-schedule-mobile.png'), ...screenshotOptions })

  const competingContext = await browser.newContext({ baseURL: process.env.S08_BASE_URL, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', reducedMotion: 'reduce' })
  const competingPage = await competingContext.newPage()
  try {
    await openCollection(competingPage, seed.accounts.schedule_editor.username, seed.accounts.schedule_editor.password)
    await expect(competingPage.getByRole('heading', { name: /提议后继计划/ })).toBeVisible()
    await competingPage.getByLabel('运行频率').selectOption('10800')
    await competingPage.getByLabel('首次到期时刻（含时区）').fill(addHours(activeSuccessor.first_due_at, 2))
    const competingWaiter = competingPage.waitForResponse(response =>
      new URL(response.url()).pathname === `/api/v1/admin/collection-jobs/${activeSuccessor.job}/schedule/` && response.request().method() === 'POST')
    await competingPage.getByRole('button', { name: '提交后继草稿' }).click()
    const competingResponse = await competingWaiter
    expect(competingResponse.status()).toBe(201)
    const competingDraft = await competingResponse.json() as ScheduleResult

    let stalePostCount = 0
    page.on('request', request => {
      if (isScheduleWrite(request)) stalePostCount += 1
    })
    await page.getByLabel('运行频率').selectOption('10800')
    await page.getByLabel('首次到期时刻（含时区）').fill(addHours(activeSuccessor.first_due_at, 3))
    const staleWaiter = page.waitForResponse(response =>
      new URL(response.url()).pathname === `/api/v1/admin/collection-jobs/${activeSuccessor.job}/schedule/` && response.request().method() === 'POST')
    await page.getByRole('button', { name: '提交后继草稿' }).click()
    const staleResponse = await staleWaiter
    expect(staleResponse.status()).toBe(409)
    await expect(page.getByRole('alert')).toContainText('已重新读取当前状态')
    await expect(page.getByText(`已登记后继草稿 Job ${competingDraft.job}`)).toBeVisible()
    expect(stalePostCount).toBe(1)
    await page.screenshot({ path: join(evidence, 'collection-schedule-conflict.png'), ...screenshotOptions })

    const managerContext = await browser.newContext({ baseURL: process.env.S08_BASE_URL, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', reducedMotion: 'reduce' })
    const managerPage = await managerContext.newPage()
    try {
      await signIn(managerPage, seed.accounts.schedule_manager.username, seed.accounts.schedule_manager.password)
      const accountPath = `admin/accounts/${seed.accounts.schedule_editor.id}/actions/`
      const accountState = await governanceRequest(managerPage, accountPath, 'GET')
      expect(accountState.status).toBe(200)
      const state = accountState.body as AccountVersion
      const revoke = await governanceRequest(managerPage, accountPath, 'POST', {
        action: 'revoke',
        reason_code: 'account_review',
        request_id: randomUUID(),
        expected_version: state.version,
        permission: 'observations.configure_collection_schedule',
      })
      expect(revoke.status).toBe(200)
    } finally {
      await managerContext.close()
    }

    const directoryResponseWaiter = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/v1/admin/catalog/products/' && response.request().method() === 'GET' && response.status() === 403)
    await page.getByRole('button', { name: '重新读取' }).click()
    await directoryResponseWaiter
    await expect(page.getByRole('alert')).toContainText('调度配置权限已撤销')
    await expect(page.locator('.schedule-detail')).toHaveCount(0)
  } finally {
    await competingContext.close()
  }
})
