import { randomUUID } from 'node:crypto'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page, type Response } from '@playwright/test'
import { browserSeed, type BrowserAccount } from './seed'
import { freshReader } from './registration'
import { foregroundPair, switchAway, switchBack } from './tabFocus'

const mySpace = '/app/me?scope=synthetic'
const favoritesPath = '/api/v1/me/favorites/'
const profilePath = '/api/v1/me/'
const capabilitiesPath = '/api/v1/me/capabilities/'

function matches(response: Response, pathname: string) {
  return new URL(response.url()).pathname === pathname && response.request().method() === 'GET'
}

async function signIn(page: Page, path: string, account: Pick<BrowserAccount, 'username' | 'password'>) {
  await page.goto(path)
  await submitSignIn(page, path, account)
}

async function submitSignIn(page: Page, path: string, account: Pick<BrowserAccount, 'username' | 'password'>) {
  await page.getByLabel('用户名').fill(account.username)
  await page.getByLabel('密码', { exact: true }).fill(account.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(url => `${url.pathname}${url.search}` === path)
}

async function write(page: Page, path: string, data?: object) {
  const csrf = await page.request.get('/api/v1/auth/csrf/')
  expect(csrf.status()).toBe(200)
  const { csrf_token } = await csrf.json() as { csrf_token: string }
  return page.request.post(path, { data, headers: { 'X-CSRFToken': csrf_token } })
}

function evidence(name: string, summary: object) {
  const directory = process.env.S08_EVIDENCE_DIR
  if (directory) writeFileSync(join(directory, `session-resume-${name}.json`), JSON.stringify(summary))
}

test('returning to a tab removes stale favorites before verifying and reads only current grants', async ({ page, browser, baseURL }) => {
  const seed = browserSeed()
  const fixture = seed.favorite_rights
  const account = freshReader()
  expect((await write(page, '/api/v1/auth/register/', account)).status()).toBe(201)
  await signIn(page, mySpace, account)
  const favorites = page.getByRole('region', { name: '我的关注' })
  expect((await write(page, `${favoritesPath}?scope=synthetic`, {
    kind: 'city', target_id: fixture.city, product: fixture.product,
  })).status()).toBe(201)
  expect((await write(page, `${favoritesPath}?scope=synthetic`, {
    kind: 'station', target_id: seed.station, product: seed.products.S,
  })).status()).toBe(201)
  await favorites.getByRole('button', { name: '刷新关注' }).click()
  const city = favorites.getByRole('heading', { name: fixture.name, exact: true })
  const station = favorites.locator(`a[href*="/app/stations/${seed.station}?"][href*="site_epoch=${seed.site_epoch}&"]`)
  await expect(city).toBeVisible()
  await expect(station).toBeVisible()

  const managerContext = await browser.newContext({ baseURL })
  let releaseVerification!: () => void
  const hold = new Promise<void>(resolve => { releaseVerification = resolve })
  try {
    const manager = await managerContext.newPage()
    await signIn(manager, '/app/manage/operations?scope=synthetic', seed.accounts.grant_manager)
    const away = await foregroundPair(page)
    await expect(city).toBeVisible()
    await switchAway(page, away)

    const grantPath = `/api/v1/admin/grants/${fixture.grant}/actions/`
    const grant = await manager.request.get(grantPath)
    expect(grant.status()).toBe(200)
    const { version, product } = await grant.json() as { version: number; product: number }
    expect(product).toBe(fixture.product)
    const withdrawal = await write(manager, grantPath, {
      action: 'withdraw', reason_code: 'terms_ended', request_id: randomUUID(), expected_version: version,
    })
    expect(withdrawal.status()).toBe(200)

    let held = false
    await page.route(url => url.pathname === capabilitiesPath, async route => {
      const result = await route.fetch()
      held = true
      await hold
      await route.fulfill({ response: result })
    }, { times: 1 })
    const paths: string[] = []
    page.on('request', request => {
      const { pathname } = new URL(request.url())
      if ([profilePath, capabilitiesPath, favoritesPath].includes(pathname)) paths.push(pathname)
    })
    const profile = page.waitForResponse(response => matches(response, profilePath))
    const capabilities = page.waitForResponse(response => matches(response, capabilitiesPath))
    const refreshed = page.waitForResponse(response => matches(response, favoritesPath))
    await page.bringToFront()
    await expect.poll(() => held).toBe(true)
    await expect(page.getByText('正在核验当前会话…')).toBeVisible()
    await expect(city).toHaveCount(0)
    await expect(station).toHaveCount(0)
    expect(paths).not.toContain(favoritesPath)
    releaseVerification()
    expect((await profile).status()).toBe(200)
    expect((await capabilities).status()).toBe(200)
    expect((await refreshed).status()).toBe(200)
    await expect(favorites.getByText('当前不可查看。你仍可取消关注。')).toBeVisible()
    await expect(city).toHaveCount(0)
    await expect(station).toBeVisible()
    expect(paths.filter(path => path === profilePath)).toHaveLength(1)
    expect(paths.filter(path => path === capabilitiesPath)).toHaveLength(1)
    evidence('grants', { origin: 'F', stage: 'complete', trustedAway: true, oneVerification: true, oldDomRemoved: true, independentStationVisible: true })
  } finally {
    releaseVerification()
    await managerContext.close()
  }
})

test('returning to a tab with an ended session keeps private pages closed', async ({ page }) => {
  // Opening and foregrounding the second tab can itself trigger a session check.
  // Set up both tabs before the target page mounts its private workspace.
  await page.goto(`/app/login?next=${encodeURIComponent(mySpace.slice('/app'.length))}`)
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
  const away = await foregroundPair(page)
  await submitSignIn(page, mySpace, browserSeed().accounts.peer)
  const heading = page.getByRole('heading', { name: '我的空间', exact: true })
  await expect(heading).toBeVisible()

  let releaseFirst!: () => void
  let releaseLatest!: () => void
  const holdFirst = new Promise<void>(resolve => { releaseFirst = resolve })
  const holdLatest = new Promise<void>(resolve => { releaseLatest = resolve })
  let firstCapabilityStatus: number | undefined
  let latestStatus: number | undefined
  let loggedOut = false
  let stage = 'first-away'
  let privateReads = 0
  const verification = [profilePath, capabilitiesPath]
  const privateReadsPaths = [favoritesPath, '/api/v1/me/saved-queries/', '/api/v1/me/exports/']
  await page.route(url => verification.includes(url.pathname), async route => {
    const afterLogout = loggedOut
    const response = await route.fetch()
    if (afterLogout) {
      latestStatus = response.status()
      await holdLatest
    } else if (new URL(route.request().url()).pathname === capabilitiesPath) {
      firstCapabilityStatus = response.status()
      await holdFirst
    }
    await route.fulfill({ response })
  })
  try {
    await switchAway(page, away)
    stage = 'first-return'
    const firstProfile = page.waitForResponse(response => matches(response, profilePath), { timeout: 12_000 })
    await switchBack(page)
    stage = 'first-verification'
    expect((await firstProfile).status()).toBe(200)
    await expect.poll(() => firstCapabilityStatus).toBe(200)
    await expect(heading).toHaveCount(0)
    await expect(page.getByText('正在核验当前会话…')).toBeVisible()

    stage = 'second-away'
    page.on('request', request => {
      if (privateReadsPaths.includes(new URL(request.url()).pathname)) privateReads++
    })
    await switchAway(page, away)
    const logout = await write(away, '/api/v1/auth/logout/')
    expect(logout.status()).toBe(204)
    loggedOut = true
    stage = 'second-return'
    await switchBack(page)
    stage = 'latest-verification'
    releaseFirst()

    await expect.poll(() => latestStatus).toBe(403)
    await expect(heading).toHaveCount(0)
    await expect(page.getByText('正在核验当前会话…')).toBeVisible()
    expect(privateReads).toBe(0)

    const denial = page.waitForResponse(response => verification.some(path => matches(response, path)) && response.status() === 403, { timeout: 12_000 })
    releaseLatest()
    expect(await (await denial).json()).toEqual({ code: 'not_authenticated' })
    await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
    await expect(heading).toHaveCount(0)
    expect(privateReads).toBe(0)
    stage = 'complete'
  } finally {
    releaseFirst()
    releaseLatest()
    evidence('expired', { origin: 'F', stage, firstCapabilityStatus, latestStatus, privateReads })
  }
})

test('a same-page 403 and a workspace reentry enforce the current capability', async ({ page, browser, baseURL }) => {
  const seed = browserSeed()
  const managerContext = await browser.newContext({ baseURL })
  const samePageContext = await browser.newContext({ baseURL })
  try {
    const manager = await managerContext.newPage()
    await signIn(manager, '/app/manage/operations?scope=synthetic', seed.accounts.schedule_manager)
    const opsPath = '/app/manage/operations?scope=synthetic'
    const samePage = await samePageContext.newPage()
    await signIn(samePage, opsPath, seed.accounts.schedule_editor)
    const audit = samePage.getByRole('region', { name: '对象审计' })
    await audit.getByLabel('审计对象编号').fill(String(seed.collection.job))
    await audit.getByLabel('审计开始 UTC').fill('2024-02-01T00:00')
    await audit.getByLabel('审计结束 UTC').fill('2024-02-02T00:00')
    await signIn(page, mySpace, seed.accounts.schedule_editor)
    const collection = page.getByRole('link', { name: '采集任务', exact: true })
    await expect(collection).toBeVisible()
    const accountPath = `/api/v1/admin/accounts/${seed.accounts.schedule_editor.id}/actions/`
    const accountState = await manager.request.get(accountPath)
    expect(accountState.status()).toBe(200)
    const { version } = await accountState.json() as { version: number }
    const revoke = await write(manager, accountPath, {
      action: 'revoke', reason_code: 'account_review', request_id: randomUUID(), expected_version: version,
      permission: 'observations.configure_collection_schedule',
    })
    expect(revoke.status()).toBe(200)

    const denied = samePage.waitForResponse(response => matches(response, '/api/v1/admin/object-audit/'))
    const samePageCaps = samePage.waitForResponse(response => matches(response, capabilitiesPath))
    await audit.getByRole('button', { name: '查看对象审计' }).click()
    const deniedRead = await denied
    expect(deniedRead.status()).toBe(403)
    expect(await deniedRead.json()).toEqual({ code: 'permission_denied' })
    expect((await samePageCaps).status()).toBe(200)
    await expect(samePage).toHaveURL(opsPath)
    await expect(audit).toHaveCount(0)
    await expect(samePage.getByRole('heading', { name: '当前账号无权访问此工作区' })).toBeVisible()
    await samePage.getByRole('link', { name: '返回监测总览' }).click()
    await expect(samePage).toHaveURL('/app/?scope=synthetic')

    const profile = page.waitForResponse(response => matches(response, profilePath))
    const capabilities = page.waitForResponse(response => matches(response, capabilitiesPath))
    await collection.click()
    expect((await profile).status()).toBe(200)
    const latest = await capabilities
    expect(latest.status()).toBe(200)
    expect((await latest.json() as { actions: string[] }).actions).not.toContain('configure_collection_schedule')
    await expect(page).toHaveURL('/app/?scope=synthetic')
    await expect(collection).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '采集任务', exact: true })).toHaveCount(0)
    await page.getByRole('link', { name: '平台说明', exact: true }).click()
    await expect(page).toHaveURL('/app/about')
    evidence('workspace', { origin: 'F', stage: 'complete', permissionRevoked: true, samePageDenied: deniedRead.status(), privateRouteDenied: true, overviewReachable: true, publicRouteReachable: true })
  } finally {
    await samePageContext.close()
    await managerContext.close()
  }
})
