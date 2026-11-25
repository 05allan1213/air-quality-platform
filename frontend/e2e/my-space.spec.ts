import { expect, test, type Page, type Request } from '@playwright/test'
import { freshReader, freshPassword } from './registration'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { browserSeed } from './seed'

type ApiReceipt = {
  stage: string
  path: string
  method: string
  status: number | null
  outcome?: 'finished' | 'failed'
}

async function signIn(page: Page, username: string, password: string) {
  await page.goto('/app/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/app\/$/)
}

async function postJson(page: Page, path: string, body: unknown) {
  return page.evaluate(async ({ path, body }) => {
    const csrf = await fetch('/api/v1/auth/csrf/', { credentials: 'same-origin' }).then(response => response.json())
    const response = await fetch(`/api/v1/${path}`, {
      method: 'POST', credentials: 'same-origin',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrf_token },
      body: JSON.stringify(body),
    })
    return { status: response.status, body: response.status === 204 ? null : await response.json() }
  }, { path, body })
}

test('my space revisits owned F favorites, saved analysis, and export tickets', async ({ page, browser }) => {
  const seed = browserSeed()
  const evidence = process.env.S08_EVIDENCE_DIR
  const statuses: number[] = []
  const requests: ApiReceipt[] = []
  const entries = new WeakMap<Request, ApiReceipt>()
  let preScopeFailures: { path: string; status: number }[] = []
  let stage = 'reader direct login'
  const favoriteIds: number[] = []
  let savedId = ''
  let ticketId = ''
  let favoriteProjection: {
    status: number
    city?: { state: string; hasName: boolean; resultCount: number; hasLink: boolean }
  } | undefined
  let favoriteView: { rowCount: number; cityHeadingPresent: boolean; readingCount: number } | undefined
  let peerStatuses: number[] = []
  const context = await browser.newContext({ baseURL: process.env.S08_BASE_URL, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', reducedMotion: 'reduce' })
  const peer = await context.newPage()

  for (const actorPage of [page, peer]) {
    actorPage.on('request', request => {
      const { pathname } = new URL(request.url())
      if (!pathname.startsWith('/api/v1/')) return
      const path = pathname.replace(/\/(?:\d+|[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12})(?=\/|$)/gi, '/:id')
      const entry = { stage, path, method: request.method(), status: null }
      requests.push(entry)
      entries.set(request, entry)
    })
    actorPage.on('response', response => {
      const entry = entries.get(response.request())
      if (!entry) return
      entry.status = response.status()
      if (actorPage === page) statuses.push(entry.status)
    })
    actorPage.on('requestfinished', request => {
      const entry = entries.get(request)
      if (entry) entry.outcome = 'finished'
    })
    actorPage.on('requestfailed', request => {
      const entry = entries.get(request)
      if (entry) entry.outcome = 'failed'
    })
  }

  try {
    await signIn(page, seed.accounts.reader.username, seed.accounts.reader.password)
    stage = 'switch reader to F scope'
    await page.getByLabel('数据域').selectOption('synthetic')
    await expect.poll(() => page.evaluate(() => new URL(location.href).searchParams.get('scope'))).toBe('synthetic')
    preScopeFailures = requests.flatMap(({ path, status }) => status !== null && status >= 400 ? [{ path, status }] : [])
    expect(preScopeFailures.every(item => item.path === '/api/v1/overview/national/' && item.status === 403)).toBe(true)
    statuses.length = 0

    const from = new Date(Date.parse(seed.start) + 8 * 60 * 60 * 1000).toISOString()
    const until = new Date(Date.parse(seed.start) + 11 * 60 * 60 * 1000).toISOString()
    const selection = {
      product: seed.products.C, source: seed.source, scope: 'synthetic', cities: [seed.city],
      indicators: ['CO'], start: from, end: until, report_kind: 'hourly',
      window_definition: 'hourly_label_unspecified', quality: 'reliable',
    }
    stage = 'create city favorite'
    const cityFavorite = await postJson(page, 'me/favorites/?scope=synthetic', { kind: 'city', target_id: seed.city, product: seed.products.C })
    expect(cityFavorite.status).toBe(201)
    favoriteIds.push(cityFavorite.body.id)
    stage = 'create station favorite'
    const stationFavorite = await postJson(page, 'me/favorites/?scope=synthetic', { kind: 'station', target_id: seed.station, product: seed.products.S })
    expect(stationFavorite.status).toBe(201)
    favoriteIds.push(stationFavorite.body.id)

    stage = 'read baseline statistics'
    const intent = { selection, method: 'statistics', method_version: 's05.v1', grain: 'hour' }
    const statistics = await postJson(page, 'analysis/statistics/', intent)
    expect(statistics.status).toBe(200)
    stage = 'create saved analysis'
    const saved = await postJson(page, 'me/saved-queries/?scope=synthetic', { ...intent, title: 'F 城市 CO 复访' })
    expect(saved.status).toBe(201)
    savedId = saved.body.id

    stage = 'read detail input version'
    const detailGroup = {
      ...intent, selection: { ...selection, indicators: ['NO2'] }, method: 'detail', grain: 'hour',
    }
    const details = await postJson(page, 'analysis/details/', {
      ...detailGroup, support: 'observed', page_size: 10,
    })
    expect(details.status).toBe(200)
    const group = { ...detailGroup, input_version: details.body.input_version }
    stage = 'create export ticket'
    const ticket = await postJson(page, 'me/exports/?scope=synthetic', { kind: 'detail', groups: [group], fields: [] })
    expect(ticket.status).toBe(201)
    ticketId = ticket.body.id

    stage = 'reader revisit'
    await page.goto('/app/me?scope=synthetic')
    await expect(page.getByRole('heading', { name: '我的空间' })).toBeVisible()
    favoriteProjection = await page.evaluate(async () => {
      const response = await fetch('/api/v1/me/favorites/?scope=synthetic', { credentials: 'same-origin' })
      const body = await response.json() as {
        results?: { kind: string; state: string; name?: string | null; results?: unknown[]; link?: string | null }[]
      }
      const city = body.results?.find(row => row.kind === 'city')
      return {
        status: response.status,
        ...(city ? {
          city: {
            state: city.state,
            hasName: Boolean(city.name),
            resultCount: city.results?.length ?? 0,
            hasLink: Boolean(city.link),
          },
        } : {}),
      }
    })
    expect(favoriteProjection).toMatchObject({ status: 200, city: { state: 'ok', hasName: true } })
    const favorites = page.getByRole('region', { name: '我的关注' })
    favoriteView = await favorites.locator('.me-row').evaluateAll((rows, cityName) => ({
      rowCount: rows.length,
      cityHeadingPresent: rows.some(row => row.querySelector('h3')?.textContent?.trim() === cityName),
      readingCount: rows.reduce((count, row) => count + row.querySelectorAll('.me-reading').length, 0),
    }), seed.cities[0].name)
    const cityRow = favorites.locator('.me-row').filter({ hasText: seed.cities[0].name })
      .filter({ has: page.getByRole('heading', { name: seed.cities[0].name, exact: true }) })
    await expect(cityRow.getByRole('heading', { name: seed.cities[0].name, exact: true })).toBeVisible()
    await expect(cityRow.getByText(/NO2 · \d/)).toBeVisible()
    await expect(cityRow.getByText(`快照 ${seed.snapshot_at.slice(0, 16).replace('T', ' ')} UTC`)).toBeVisible()
    await expect(favorites.getByRole('link', { name: '查看城市详情' })).toHaveAttribute('href', new RegExp(`/app/cities/${seed.city}`))
    await favorites.getByRole('link', { name: '查看城市详情' }).click()
    await expect(page).toHaveURL(new RegExp(`/app/cities/${seed.city}\\?`))
    expect(new URL(page.url()).searchParams.get('product')).toBe(String(seed.products.C))
    await page.goto('/app/me?scope=synthetic')
    const epochs = favorites.locator('details').filter({ hasText: '获准站址期' })
    await expect(epochs).toBeVisible()
    if (await epochs.getAttribute('open') === null) await epochs.getByText('获准站址期').click()
    const originalEpoch = favorites.locator(`a[href*="site_epoch=${seed.site_epoch}&"]`)
    await expect(originalEpoch).toBeVisible()
    await originalEpoch.click()
    await expect(page).toHaveURL(new RegExp(`/app/stations/${seed.station}\\?`))
    const stationQuery = new URL(page.url()).searchParams
    expect([stationQuery.get('product'), stationQuery.get('site_epoch')]).toEqual([String(seed.products.S), String(seed.site_epoch)])
    await page.goto('/app/me?scope=synthetic')

    stage = 'run and edit saved intent'
    const schemes = page.getByRole('region', { name: '分析方案' })
    const savedRow = schemes.locator('.me-row').filter({ hasText: 'F 城市 CO 复访' })
    await savedRow.getByRole('button', { name: '重新运行' }).click()
    await expect(savedRow.getByRole('heading', { name: '本次运行结果' })).toBeVisible()
    await expect(savedRow.getByText(/实到/)).toBeVisible()
    await savedRow.getByRole('button', { name: '编辑方案' }).click()
    await savedRow.getByLabel('方案名称').fill('F 城市 CO 复访已更新')
    await savedRow.getByRole('button', { name: '保存方案' }).click()
    await expect(schemes.getByText('F 城市 CO 复访已更新')).toBeVisible()

    stage = 'download and remove export ticket'
    const exports = page.getByRole('region', { name: '近期导出' })
    const ticketRow = exports.locator('.me-row').filter({ hasText: '明细 CSV' })
    await expect(ticketRow.getByText('下载时核验')).toBeVisible()
    const downloadPromise = page.waitForEvent('download')
    await ticketRow.getByRole('button', { name: '下载 CSV' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe('air-quality.csv')
    stage = 'peer direct login'
    await signIn(peer, seed.accounts.peer.username, seed.accounts.peer.password)
    stage = 'peer empty F workspace'
    await peer.getByLabel('数据域').selectOption('synthetic')
    await peer.goto('/app/me?scope=synthetic')
    await expect(peer.getByText('还没有关注对象。')).toBeVisible()
    await expect(peer.getByText('还没有分析方案。')).toBeVisible()
    await expect(peer.getByText('还没有下载票据。')).toBeVisible()
    stage = 'peer object isolation'
    const isolation = await peer.evaluate(async ({ favoriteIds, savedId, ticketId }) => {
      const paths = [
        ...favoriteIds.map(id => `me/favorites/${id}/?scope=synthetic`),
        `me/saved-queries/${savedId}/?scope=synthetic`,
        `me/exports/${ticketId}/?scope=synthetic`,
        `me/exports/${ticketId}/download/?scope=synthetic`,
      ]
      const responses = await Promise.all(paths.map(path => fetch(`/api/v1/${path}`, { credentials: 'same-origin' })))
      return Promise.all(responses.map(async response => ({ status: response.status, code: (await response.json()).code })))
    }, { favoriteIds, savedId, ticketId })
    peerStatuses = isolation.map(result => result.status)
    expect(isolation).toEqual(Array.from({ length: 5 }, () => ({ status: 404, code: 'not_found' })))

    stage = 'expire presentation and remove owned resources'
    await page.clock.setFixedTime(new Date(Date.parse(ticket.body.expires_at) + 1000))
    await expect(ticketRow.getByText('票据已过期')).toBeVisible()
    await expect(ticketRow.getByRole('button', { name: '下载 CSV' })).toBeDisabled()
    await ticketRow.getByRole('button', { name: '删除票据' }).click()
    await ticketRow.getByRole('button', { name: '确认删除票据' }).click()
    await expect(exports.getByText('还没有下载票据。')).toBeVisible()
    await savedRow.getByRole('button', { name: '删除方案' }).click()
    await savedRow.getByRole('button', { name: '确认删除方案' }).click()
    await expect(schemes.getByText('还没有分析方案。')).toBeVisible()
    await page.setViewportSize({ width: 390, height: 844 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    expect(statuses.filter(status => status >= 400)).toEqual([])
    stage = 'complete'
  } finally {
    if (evidence) {
      writeFileSync(join(evidence, 'my-space-flow.json'), JSON.stringify({
        origin: 'F', stage, favoriteCount: favoriteIds.length, saved: Boolean(savedId), ticket: Boolean(ticketId),
        statuses, preScopeFailures, peerStatuses, favoriteProjection, favoriteView, requests,
      }))
      if (stage === 'complete') {
        await page.screenshot({ path: join(evidence, 'my-space-390.png'), fullPage: true, mask: [page.locator('.account-link'), page.locator('.my-space > .section-head p')] })
      }
    }
    await context.close()
  }
})

test('server password change clears both old tabs and cancels a late real F favorite response', async ({ page, context, browser }) => {
  const seed = browserSeed()
  const evidence = process.env.S08_EVIDENCE_DIR
  const { username, password } = freshReader()
  const newPassword = freshPassword()
  const space = '/app/me?scope=synthetic'
  const favoritesPath = '/api/v1/me/favorites/'
  const isFavoriteList = (url: URL) => url.pathname === favoritesPath && url.searchParams.get('scope') === seed.scope
  const statuses: Record<string, number> = {}
  let stage = 'register'
  let heldFavorite = false
  let heldRouteCompleted = false
  let oldRequest: Request | undefined
  let oldRequestCancelled = false
  let independentSessions = false
  let privateRowsCleared = false
  let release: (() => void) | undefined
  const gate = new Promise<void>(resolve => { release = resolve })
  const changerContext = await browser.newContext({ baseURL: process.env.S08_BASE_URL, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' })
  const changer = await changerContext.newPage()
  const cityName = seed.cities[0].name

  async function loginToMySpace(tab: Page) {
    await tab.goto(space)
    await tab.getByLabel('用户名').fill(username)
    await tab.getByLabel('密码', { exact: true }).fill(password)
    await tab.getByRole('button', { name: '登录', exact: true }).click()
    await expect(tab).toHaveURL(/\/app\/me\?scope=synthetic$/)
  }

  function favoriteCard(tab: Page) {
    return tab.getByRole('region', { name: '我的关注' }).locator('.me-row').filter({
      has: tab.getByRole('heading', { name: cityName, exact: true }),
    })
  }

  try {
    await page.goto('/app/register')
    await page.getByLabel('用户名').fill(username)
    await page.getByLabel('密码', { exact: true }).fill(password)
    const registered = page.waitForResponse(result => new URL(result.url()).pathname === '/api/v1/auth/register/')
    await page.getByRole('button', { name: '创建账号', exact: true }).click()
    statuses.register = (await registered).status()
    expect(statuses.register).toBe(201)
    await expect(page).toHaveURL(/\/app\/login$/)

    stage = 'session-setup'
    await loginToMySpace(page)
    const favorite = await postJson(page, 'me/favorites/?scope=synthetic', { kind: 'city', target_id: seed.city, product: seed.products.C })
    statuses.favorite = favorite.status
    expect(statuses.favorite).toBe(201)
    await page.goto(space)
    const sibling = await context.newPage()
    await sibling.goto(space)
    await loginToMySpace(changer)
    const oldSession = (await context.cookies()).find(cookie => cookie.name === 'sessionid')?.value
    const changingSession = (await changerContext.cookies()).find(cookie => cookie.name === 'sessionid')?.value
    independentSessions = Boolean(oldSession && changingSession && oldSession !== changingSession)
    expect(independentSessions).toBe(true)
    for (const tab of [page, sibling]) {
      await expect(favoriteCard(tab).getByRole('link', { name: '查看城市详情' })).toBeVisible()
      await expect(favoriteCard(tab).locator('.me-reading').first()).toBeVisible()
    }

    stage = 'hold-real-response'
    page.on('requestfailed', request => { if (request === oldRequest) oldRequestCancelled = true })
    await page.route(isFavoriteList, async route => {
      oldRequest = route.request()
      const upstream = await route.fetch()
      statuses.heldGet = upstream.status()
      if (upstream.ok()) {
        const body = await upstream.json() as { results: { id: number; name?: string; link?: string; results?: unknown[] }[] }
        heldFavorite = body.results.some(item => item.id === favorite.body.id && item.name === cityName && Boolean(item.link) && Boolean(item.results?.length))
      }
      await gate
      await route.fulfill({ response: upstream }).catch(() => {
        // An aborted browser request can lose its receiver. The test asserts that exact cancellation separately.
      })
      heldRouteCompleted = true
    }, { times: 1 })
    await page.getByRole('button', { name: '刷新关注', exact: true }).click()
    await expect.poll(() => ({ status: statuses.heldGet, heldFavorite })).toEqual({ status: 200, heldFavorite: true })

    stage = 'change-password'
    await changer.getByLabel('当前密码').fill(password)
    await changer.getByLabel('新密码').fill(newPassword)
    const changed = changer.waitForResponse(result => new URL(result.url()).pathname === '/api/v1/auth/password/')
    await changer.getByRole('button', { name: '更新密码并重新登录' }).click()
    statuses.password = (await changed).status()
    expect(statuses.password).toBe(204)
    await expect(changer).toHaveURL(/\/app\/login$/)
    // The separate browser context cannot send BroadcastChannel messages to the old tabs.
    await expect(favoriteCard(sibling)).toBeVisible()

    stage = 'server-denial-and-broadcast'
    const denied = sibling.waitForResponse(result => isFavoriteList(new URL(result.url())) && result.request().method() === 'GET')
    await sibling.getByRole('button', { name: '刷新关注', exact: true }).click()
    const deniedResponse = await denied
    statuses.oldSession = deniedResponse.status()
    expect(statuses.oldSession).toBe(403)
    expect((await deniedResponse.json() as { code?: string }).code).toBe('not_authenticated')
    for (const tab of [sibling, page]) await expect(tab).toHaveURL(/\/app\/login(?:\?|$)/)

    stage = 'release-and-check-dom'
    release?.()
    await expect.poll(() => oldRequestCancelled).toBe(true)
    await expect.poll(() => heldRouteCompleted).toBe(true)
    for (const tab of [sibling, page]) {
      await expect(tab.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
      await expect(tab.getByRole('heading', { name: cityName, exact: true })).toHaveCount(0)
      await expect(tab.getByRole('link', { name: '查看城市详情' })).toHaveCount(0)
      await expect(tab.locator('.me-reading')).toHaveCount(0)
      await expect(tab.locator('.account-link')).toHaveCount(0)
    }
    privateRowsCleared = true
    stage = 'complete'
  } finally {
    release?.()
    if (evidence) writeFileSync(join(evidence, 'my-space-session-race.json'), JSON.stringify({
      origin: 'F', stage, statuses, independentSessions, heldFavorite, oldRequestCancelled, heldRouteCompleted, privateRowsCleared,
    }))
    await changerContext.close()
  }
})
