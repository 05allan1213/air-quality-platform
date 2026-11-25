import { expect, test } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { browserSeed } from './seed'

const evidence = process.env.S08_EVIDENCE_DIR

async function signIn(page: import('@playwright/test').Page, username: string, password: string) {
  await page.goto('/app/login')
  await page.getByRole('heading', { name: '登录' }).waitFor()
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
}

test('reader uses a real same-origin session and the authorized F observation workspace', async ({ page }) => {
  const seed = browserSeed()
  const account = seed.accounts.reader
  const apiResponses: number[] = []
  const timings: { endpoint: string; scope: string; ms: number; status?: number }[] = []
  const start = Date.now()
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/v1/')) timings.push({ endpoint: url.pathname, scope: url.searchParams.get('scope') || 'formal', ms: Date.now() - start })
  })
  page.on('response', (response) => {
    const url = new URL(response.url())
    if (url.pathname.startsWith('/api/v1/')) {
      apiResponses.push(response.status())
      timings.push({ endpoint: url.pathname, scope: url.searchParams.get('scope') || 'formal', ms: Date.now() - start, status: response.status() })
    }
  })
  await signIn(page, account.username, account.password)
  await expect(page).toHaveURL(/\/app\/$/)
  await expect(page.getByRole('heading', { name: '全国空气质量总览' })).toBeVisible()
  await expect(page.getByLabel('数据域')).toHaveValue('')
  await page.getByLabel('数据域').selectOption('synthetic')
  await expect(page.getByLabel('数据域')).toHaveValue('synthetic')
  try {
    await expect(page.getByRole('row').filter({ hasText: seed.cities[0].name })).toBeVisible()
  } finally {
    if (evidence) writeFileSync(join(evidence, 'auth-overview.json'), JSON.stringify({timings, selected: await page.getByLabel('数据域').inputValue(), rows: await page.getByRole('row').count(), named: await page.getByRole('row').filter({ hasText: seed.cities[0].name }).count(), loading: await page.locator('.loading-line').count(), denied: await page.locator('.error-line').count()}))
  }
  await expect.poll(() => apiResponses.filter((status) => status >= 200 && status < 300).length).toBeGreaterThan(2)
  await expect(page).toHaveURL(/\/app\/\?scope=synthetic$/)

  if (evidence) {
    mkdirSync(evidence, { recursive: true })
    const mask = [page.locator('.account-link')]
    await page.screenshot({ path: join(evidence, 's08-reader-overview-1440.png'), fullPage: true, mask })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.screenshot({ path: join(evidence, 's08-reader-overview-390.png'), fullPage: true, mask })
  }
  await page.setViewportSize({ width: 320, height: 800 })
  await expect.poll(() => page.locator('.topbar').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  await expect(page.getByRole('button', { name: '退出' })).toHaveCSS('white-space', 'nowrap')
  const refreshIcon = page.getByRole('button', { name: '重新读取全国观测' }).locator('svg')
  const fontSize = await refreshIcon.evaluate(icon => getComputedStyle(icon).fontSize)
  await expect(refreshIcon).toHaveCSS('width', fontSize)
  await expect(refreshIcon).toHaveCSS('height', fontSize)
  // The map fallback deliberately sizes its illustration independently of inline icons.
  const mapIcon = page.locator('.map-icon')
  expect(await mapIcon.evaluate(icon => icon.getBoundingClientRect().width > parseFloat(getComputedStyle(icon).fontSize))).toBe(true)
})

test('management navigation follows real server capability projection', async ({ page }) => {
  const seed = browserSeed()
  await signIn(page, seed.accounts.reader.username, seed.accounts.reader.password)
  await expect(page.getByRole('link', { name: '采集任务' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '数据质量' })).toHaveCount(0)

  await page.getByLabel('数据域').selectOption('synthetic')
  await page.getByRole('button', { name: '退出' }).click()
  await expect(page).toHaveURL(/\/app\/login/)
  await signIn(page, seed.accounts.operator.username, seed.accounts.operator.password)
  await expect(page.getByRole('link', { name: '采集任务' })).toBeVisible()
})

test('private state is cleared when the current server session ends', async ({ page }) => {
  const account = browserSeed().accounts.reader
  await signIn(page, account.username, account.password)
  await expect(page).toHaveURL(/\/app\/$/)
  await page.getByRole('button', { name: '退出' }).click()
  await expect(page).toHaveURL(/\/app\/login/)
  await page.goto('/app/me')
  await expect(page).toHaveURL(/\/app\/login/)
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
})

for (const storageMode of ['writable', 'quota-failure'] as const) {
  test(`an undelivered logout locks refreshed, existing and new tabs until explicit retry (${storageMode})`, async ({ page, context }) => {
    if (storageMode === 'quota-failure') {
      await context.addInitScript(() => {
        const setItem = Storage.prototype.setItem
        Storage.prototype.setItem = function (key, value) {
          if (this === window.localStorage && key === 'aq:logout-pending') {
            throw new DOMException('Injected storage quota failure', 'QuotaExceededError')
          }
          setItem.call(this, key, value)
        }
      })
    }
    const { reader } = browserSeed().accounts
    await signIn(page, reader.username, reader.password)
    await expect(page).toHaveURL(/\/app\/$/)
    const openTab = await context.newPage()
    await openTab.goto('/app/me?scope=synthetic')
    await expect(openTab.getByRole('heading', { name: '我的空间' })).toBeVisible()

    let attempts = 0
    page.on('request', request => {
      if (request.url().endsWith('/auth/logout/') && request.method() === 'POST') attempts++
    })
    await page.route('**/api/v1/auth/logout/', route => route.abort('failed'), { times: 1 })
    await page.getByRole('button', { name: '退出', exact: true }).click()
    for (const tab of [page, openTab]) {
      await expect(tab.getByRole('heading', { name: '退出尚未确认' })).toBeVisible()
    }
    if (storageMode === 'quota-failure') {
      expect(await page.evaluate(() => localStorage.getItem('aq:logout-pending'))).toBeNull()
      const marker = (await context.cookies()).find(cookie => cookie.name === 'aq_logout_pending')
      expect(marker).toMatchObject({ value: '1', path: '/app/', sameSite: 'Strict' })
      expect(marker!.expires - Date.now() / 1000).toBeGreaterThan(7 * 60 * 60)
    }
    expect((await page.request.get('/api/v1/me/')).status()).toBe(200)

    await page.reload()
    await expect(page.getByRole('button', { name: '重新退出' })).toBeVisible()
    const newTab = await context.newPage()
    await newTab.goto('/app/me?scope=synthetic')
    for (const tab of [openTab, newTab]) {
      await expect(tab.getByRole('heading', { name: '退出尚未确认' })).toBeVisible()
      await expect(tab.getByRole('heading', { name: '我的空间' })).toHaveCount(0)
    }
    expect(attempts).toBe(1)

    await page.getByRole('button', { name: '重新退出' }).click()
    for (const tab of [page, openTab, newTab]) {
      await expect(tab.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
    }
    expect((await context.cookies()).some(cookie => cookie.name === 'aq_logout_pending')).toBe(false)
    expect((await page.request.get('/api/v1/me/')).status()).toBe(403)
    expect(attempts).toBe(2)
    if (evidence) {
      const receipt = storageMode === 'writable' ? 'auth-logout-retry.json' : 'auth-logout-quota-retry.json'
      writeFileSync(join(evidence, receipt), JSON.stringify({ origin: 'F/I', storageMode, beforeRetry: 200, afterRetry: 403,
        reloadLocked: true, otherTabLocked: true, existingTabLocked: true, attempts }))
    }
  })
}

test('a committed logout with a lost response is confirmed using the same browser session', async ({ page }) => {
  const { reader } = browserSeed().accounts
  await signIn(page, reader.username, reader.password)
  await expect(page).toHaveURL(/\/app\/$/)
  let committed = 0
  await page.route('**/api/v1/auth/logout/', async route => {
    committed = (await route.fetch()).status()
    await route.abort('failed')
  }, { times: 1 })
  const verify = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/me/' && response.status() === 403)
  await page.getByRole('button', { name: '退出', exact: true }).click()
  expect(await (await verify).json()).toEqual({ code: 'not_authenticated' })
  expect(committed).toBe(204)
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
  await page.goto('/app/me?scope=synthetic')
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重新退出' })).toHaveCount(0)
  if (evidence) writeFileSync(join(evidence, 'auth-logout-response-lost.json'), JSON.stringify({ origin: 'F/I', committed, sessionCheck: 403, deepLinkProtected: true }))
})
