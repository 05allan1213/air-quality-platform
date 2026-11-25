import { freshReader } from './registration'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page, type Response } from '@playwright/test'
import { browserSeed, type BrowserAccount } from './seed'

const space = '/app/me?scope=synthetic'
const favoritesPath = '/api/v1/me/favorites/'

type Reading = {
  indicator: string
  value: string | number | null
  unit: string
  age_seconds: number
  evaluated_at: string
}

type Favorite = {
  id: number
  kind: 'city' | 'station'
  state: string
  link?: string | null
  epochs?: { site_epoch: number; status: string; link: string }[]
  results?: Reading[]
}

function matches(response: Response, path: string, method = 'GET') {
  return new URL(response.url()).pathname === path && response.request().method() === method
}

function stationIdentityLink(link: string) {
  const parsed = new URL(link, 'http://unused.invalid')
  parsed.searchParams.delete('as_of')
  return `${parsed.pathname}${parsed.search}`
}

function stableReading(reading: Reading) {
  const projection: Partial<Reading> = { ...reading }
  delete projection.age_seconds
  delete projection.evaluated_at
  return projection
}

function stableFavorite(favorite: Favorite) {
  // Links and readings contain timestamps derived from the current request.
  return {
    ...favorite,
    link: favorite.link ? stationIdentityLink(favorite.link) : favorite.link,
    epochs: favorite.epochs?.map(epoch => ({ ...epoch, link: stationIdentityLink(epoch.link) })),
    results: favorite.results?.map(stableReading),
  }
}

async function signIn(page: Page, path: string, account: Pick<BrowserAccount, 'username' | 'password'>) {
  await page.goto(path)
  await page.getByLabel('用户名').fill(account.username)
  await page.getByLabel('密码', { exact: true }).fill(account.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(url => `${url.pathname}${url.search}` === path)
}

async function post<T>(page: Page, path: string, data: unknown): Promise<{ status: number; body: T }> {
  const csrf = await page.request.get('/api/v1/auth/csrf/')
  expect(csrf.status()).toBe(200)
  const { csrf_token } = await csrf.json() as { csrf_token: string }
  const response = await page.request.post(`/api/v1/${path}`, { data, headers: { 'X-CSRFToken': csrf_token } })
  return { status: response.status(), body: await response.json() as T }
}

async function withdraw(manager: Page, product: number, id: number) {
  const grants = manager.getByRole('region', { name: '许可与处置交接' })
  await grants.getByLabel('交接产品编号').fill(String(product))
  await grants.getByLabel('交接许可编号').fill(String(id))
  const grantPath = `/api/v1/admin/grants/${id}/actions/`
  const verification = manager.waitForResponse(response => matches(response, grantPath))
  await grants.getByRole('button', { name: '核对许可' }).click()
  const grant = await verification
  expect(grant.status()).toBe(200)
  const registered = await grant.json() as { version: number }
  expect(registered).toMatchObject({ product: product, grant: id, state: 'registered' })
  await grants.getByLabel('撤回原因').selectOption('terms_ended')
  await grants.getByRole('checkbox').check()
  const withdrawal = manager.waitForResponse(response => matches(response, grantPath, 'POST'))
  await grants.getByRole('button', { name: '确认撤回许可' }).click()
  const withdrawn = await withdrawal
  expect(withdrawn.status()).toBe(200)
  expect(withdrawn.request().postDataJSON()).toMatchObject({ action: 'withdraw', reason_code: 'terms_ended', expected_version: registered.version })
  const receipt = await withdrawn.json() as { version: number }
  expect(receipt).toMatchObject({ state: 'withdrawn', action_id: expect.any(String) })
  expect(receipt.version).toBeGreaterThan(registered.version)
  await expect(grants.getByText(/^许可已撤回/)).toBeVisible()
  return withdrawn.status()
}

test('separate display and export withdrawals clear the F favorite and invalidate its ticket', async ({ page, browser, context }) => {
  const seed = browserSeed()
  const fixture = seed.favorite_rights
  const evidence = process.env.S08_EVIDENCE_DIR
  const statuses: Record<string, number> = {}
  let stage = 'register'
  let stationUnchanged = false
  let intentOnly = false
  let sameSession = false
  let heldStatus = 0
  let deliveredDownloads = 0
  let canceledRequest = false
  let release = () => {}
  const held = new Promise<void>(resolve => { release = resolve })
  const managerContext = await browser.newContext({ baseURL: process.env.S08_BASE_URL })
  const manager = await managerContext.newPage()
  try {
    const account = freshReader()
    const registration = await post(page, 'auth/register/', account)
    statuses.register = registration.status
    expect(statuses.register).toBe(201)
    await signIn(page, space, account)
    const session = (await context.cookies()).find(cookie => cookie.name === 'sessionid')?.value
    expect(Boolean(session)).toBe(true)

    stage = 'create-favorites'
    const city = await post<Favorite>(page, 'me/favorites/?scope=synthetic', {
      kind: 'city', target_id: fixture.city, product: fixture.product,
    })
    const station = await post<Favorite>(page, 'me/favorites/?scope=synthetic', {
      kind: 'station', target_id: seed.station, product: seed.products.S,
    })
    statuses.createCity = city.status
    statuses.createStation = station.status
    expect([statuses.createCity, statuses.createStation]).toEqual([201, 201])
    const cityPath = `${favoritesPath}${city.body.id}/?scope=synthetic`
    const stationPath = `${favoritesPath}${station.body.id}/?scope=synthetic`
    const favorites = page.getByRole('region', { name: '我的关注' })
    const cityLink = favorites.getByRole('link', { name: '查看城市详情' })
    const stationLink = favorites.locator(`a[href*="/app/stations/${seed.station}?"][href*="site_epoch=${seed.site_epoch}&"]`)
    async function refreshFavorites() {
      const pending = page.waitForResponse(response => matches(response, favoritesPath))
      await favorites.getByRole('button', { name: '刷新关注' }).click()
      const response = await pending
      expect(response.status()).toBe(200)
      return (await response.json() as { results: Favorite[] }).results
    }

    stage = 'read-original-favorites-and-check-export'
    const before = await refreshFavorites()
    const cityRead = await page.request.get(cityPath)
    const stationRead = await page.request.get(stationPath)
    expect([cityRead.status(), stationRead.status()]).toEqual([200, 200])
    const originalCity = await cityRead.json() as Favorite
    const originalStation = await stationRead.json() as Favorite
    expect(before).toHaveLength(2)
    expect(before.map(stableFavorite)).toContainEqual(stableFavorite(originalCity))
    expect(before.map(stableFavorite)).toContainEqual(stableFavorite(originalStation))
    expect(originalCity).toMatchObject({ kind: 'city', state: 'ok', name: fixture.name, product: fixture.product, target_id: fixture.city })
    expect(originalCity.results).toEqual([expect.objectContaining({ indicator: 'CO', value: expect.anything(), unit: 'mg/m³' })])
    expect(originalCity.link).toBeTruthy()
    expect(originalStation).toMatchObject({ kind: 'station', state: 'select_epoch', product: seed.products.S, target_id: seed.station })
    const epoch = originalStation.epochs?.find(item => item.site_epoch === seed.site_epoch)
    expect(epoch).toMatchObject({ status: 'historical', link: expect.any(String) })
    const stationDetailsPath = epoch?.link ?? ''
    const stationDetails = await page.request.get(stationDetailsPath)
    statuses.stationDetailsBefore = stationDetails.status()
    expect(statuses.stationDetailsBefore).toBe(200)
    const stationBaseline = await stationDetails.json() as { results: Reading[] }
    expect(stationBaseline).toMatchObject({ state: 'ok', product: seed.products.S, site_epoch: seed.site_epoch })
    expect(stationBaseline.results.length).toBeGreaterThan(0)
    await expect(favorites.getByRole('heading', { name: fixture.name, exact: true })).toBeVisible()
    await expect(favorites.getByText(`CO · ${originalCity.results?.[0].value} mg/m³`, { exact: true }).first()).toBeVisible()
    await expect(cityLink).toHaveAttribute('href', new RegExp(`/app/cities/${fixture.city}.*product=${fixture.product}`))
    await expect(stationLink).toBeVisible()
    const stationHref = stationIdentityLink(await stationLink.getAttribute('href') ?? '')

    const group = {
      method: 'detail', method_version: 's05.v1', grain: 'hour',
      selection: {
        product: fixture.product, source: fixture.source, scope: seed.scope, cities: [fixture.city],
        indicators: ['CO'], start: fixture.start, end: fixture.end, report_kind: 'hourly',
        window_definition: 'hourly_label_unspecified', quality: 'reliable',
      },
    }
    const details = await post<{ input_version: string }>(page, 'analysis/details/', { ...group, support: 'observed', page_size: 10 })
    statuses.details = details.status
    expect(statuses.details).toBe(200)
    expect(details.body.input_version).toEqual(expect.any(String))
    const exportRequest = { kind: 'detail', groups: [{ ...group, input_version: details.body.input_version }], fields: [] }
    const overbroad = await post<{ code: string }>(page, 'me/exports/?scope=synthetic', {
      ...exportRequest, fields: ['qualification_version'],
    })
    expect(overbroad).toEqual({ status: 403, body: { code: 'use_denied' } })
    const ticket = await post<{ id: string }>(page, 'me/exports/?scope=synthetic', exportRequest)
    statuses.ticket = ticket.status
    expect(ticket.status).toBe(201)
    const downloadPath = `/api/v1/me/exports/${ticket.body.id}/download/`
    const tickets = page.getByRole('region', { name: '近期导出' })
    await tickets.getByRole('button', { name: '刷新票据' }).click()
    await expect(tickets.getByRole('button', { name: '下载 CSV' })).toBeVisible()
    page.on('download', () => { deliveredDownloads++ })
    page.on('requestfailed', request => {
      if (new URL(request.url()).pathname === downloadPath) canceledRequest = true
    })
    let releasedResponse = false
    await page.route(url => url.pathname === downloadPath, async route => {
      const result = await route.fetch()
      expect(result.headers()['content-disposition']).toContain('attachment')
      heldStatus = result.status()
      await held
      await route.fulfill({ response: result }).catch(() => {}) // after unmount Chromium drops the receiver
      releasedResponse = true
    }, { times: 1 })
    await tickets.getByRole('button', { name: '下载 CSV' }).click()
    await expect.poll(() => heldStatus).toBe(200)
    // This is an SPA navigation in the same tab. A download from the discarded page must stop.
    await page.getByRole('navigation', { name: '工作区导航' }).getByRole('link', { name: '监测总览' }).click()
    await expect(page.getByRole('heading', { name: '我的空间', exact: true })).toHaveCount(0)
    release()
    await expect.poll(() => releasedResponse && canceledRequest).toBe(true)
    expect(deliveredDownloads).toBe(0)
    await page.getByRole('navigation', { name: '工作区导航' }).getByRole('link', { name: '我的空间' }).click()
    await expect(tickets.getByRole('button', { name: '下载 CSV' })).toBeVisible()

    stage = 'withdraw-via-operations'
    await signIn(manager, '/app/manage/operations?scope=synthetic', seed.accounts.grant_manager)
    statuses.withdrawDisplay = await withdraw(manager, fixture.product, fixture.grant)

    stage = 'recheck-same-reader-session'
    const after = await refreshFavorites()
    const unavailable = { id: city.body.id, kind: 'city', state: 'unavailable' }
    expect(after).toHaveLength(2)
    expect(after).toContainEqual(unavailable)
    expect(after.map(stableFavorite)).toContainEqual(stableFavorite(originalStation))
    const cityAfter = await page.request.get(cityPath)
    const stationAfter = await page.request.get(stationPath)
    statuses.cityAfter = cityAfter.status()
    statuses.stationAfter = stationAfter.status()
    expect([statuses.cityAfter, statuses.stationAfter]).toEqual([200, 200])
    expect(await cityAfter.json()).toEqual(unavailable)
    expect(stableFavorite(await stationAfter.json() as Favorite)).toEqual(stableFavorite(originalStation))
    await expect(favorites.getByText('当前不可查看。你仍可取消关注。')).toBeVisible()
    await expect(favorites.getByRole('heading', { name: fixture.name, exact: true })).toHaveCount(0)
    await expect(cityLink).toHaveCount(0)
    const unavailableRow = favorites.locator('.me-row').filter({ has: page.getByRole('heading', { name: '城市关注', exact: true }) })
    await expect(unavailableRow.locator('.me-reading')).toHaveCount(0)
    await expect(unavailableRow.getByRole('link')).toHaveCount(0)
    await expect(stationLink).toBeVisible()
    expect(stationIdentityLink(await stationLink.getAttribute('href') ?? '')).toBe(stationHref)
    const stationDetailsAfter = await page.request.get(stationDetailsPath)
    statuses.stationDetailsAfter = stationDetailsAfter.status()
    expect(statuses.stationDetailsAfter).toBe(200)
    const stationProjection = await stationDetailsAfter.json() as { results: Reading[] }
    expect(stationProjection).toMatchObject({ state: 'ok', product: seed.products.S, site_epoch: seed.site_epoch })
    expect(stationProjection.results.map(stableReading)).toEqual(stationBaseline.results.map(stableReading))
    stationUnchanged = true
    intentOnly = true

    const originalLink = await page.request.get(originalCity.link ?? '')
    statuses.oldCityLink = originalLink.status()
    expect(statuses.oldCityLink).toBe(403)
    expect(await originalLink.json()).toEqual({ code: 'use_denied' })
    // Raw display and CSV are separate rights. This ticket's frozen basis still has export permission.
    const afterDisplayWithdrawal = await page.request.get(`${downloadPath}?scope=synthetic`)
    statuses.readWithIndependentExport = afterDisplayWithdrawal.status()
    expect(afterDisplayWithdrawal.status()).toBe(200)
    expect(afterDisplayWithdrawal.headers()['content-disposition']).toContain('attachment')
    statuses.withdrawExport = await withdraw(manager, fixture.product, fixture.export_grant)
    const denied = page.waitForResponse(response => matches(response, downloadPath))
    await tickets.getByRole('button', { name: '下载 CSV' }).click()
    const oldTicket = await denied
    statuses.invalidTicket = oldTicket.status()
    expect(oldTicket.status()).toBe(403)
    expect(await oldTicket.json()).toEqual({ code: 'use_denied' })
    expect(oldTicket.headers()['content-disposition']).toBeUndefined()
    await expect(tickets.getByText(/当前用途未获授权/)).toBeVisible()
    await expect(tickets.getByRole('button', { name: '下载 CSV' })).toBeDisabled()
    const afterExport = await post<{ code: string }>(page, 'me/exports/?scope=synthetic', exportRequest)
    statuses.exportAfter = afterExport.status
    expect(afterExport).toEqual({ status: 403, body: { code: 'use_denied' } })
    expect(deliveredDownloads).toBe(0)
    sameSession = (await context.cookies()).find(cookie => cookie.name === 'sessionid')?.value === session
    expect(sameSession).toBe(true)
    stage = 'complete'
  } finally {
    release()
    if (evidence) writeFileSync(join(evidence, 'favorite-rights-flow.json'), JSON.stringify({
      origin: 'F', stage, statuses, stationUnchanged, intentOnly, sameSession, heldStatus, canceledRequest, deliveredDownloads,
    }))
    await managerContext.close()
  }
})
