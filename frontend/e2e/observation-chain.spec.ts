import { expect, test, type Page } from '@playwright/test'
import { freshReader } from './registration'
import { join } from 'node:path'
import { browserDiagnostics } from './diagnostics'
import { browserSeed } from './seed'

const nationalPath = '/api/v1/overview/national/'
const favoritesPath = '/api/v1/me/favorites/'

function apiResponse(page: Page, path: string, method = 'GET') {
  return page.waitForResponse(response => new URL(response.url()).pathname === path && response.request().method() === method)
}

async function signIn(page: Page, username: string, password: string) {
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  const [login] = await Promise.all([
    apiResponse(page, '/api/v1/auth/login/', 'POST'),
    page.getByRole('button', { name: '登录', exact: true }).press('Enter'),
  ])
  expect(login.status()).toBe(200)
  expect(Boolean(await login.request().headerValue('x-csrftoken'))).toBe(true)
  await expect(page).toHaveURL(/\/app\/$/)
}

async function useSyntheticScope(page: Page) {
  await expect(page.getByLabel('数据域')).toHaveValue('')
  await page.getByLabel('数据域').selectOption('synthetic')
  await expect(page).toHaveURL('/app/?scope=synthetic')
}

async function favorite(page: Page, name: string) {
  const [response] = await Promise.all([
    apiResponse(page, favoritesPath, 'POST'),
    page.getByRole('button', { name, exact: true }).click(),
  ])
  expect(response.status()).toBe(201)
  expect(new URL(response.url()).searchParams.get('scope')).toBe('synthetic')
  expect(Boolean(await response.request().headerValue('x-csrftoken'))).toBe(true)
  await expect(page.getByRole('button', { name: '取消关注', exact: true })).toBeVisible()
  const { id } = await response.json() as { id: number }
  expect(Number.isSafeInteger(id) && id > 0).toBe(true)
  return id
}

test('a newly registered reader follows the observation chain and revisits private favorites', async ({ page, browser, baseURL }, testInfo) => {
  const seed = browserSeed()
  const account = freshReader()
  const evidence = process.env.S08_EVIDENCE_DIR
  const diagnostics = browserDiagnostics(testInfo, 'observation-chain.json', baseURL, url => {
    if (!url.pathname.startsWith('/api/v1/')) return undefined
    const path = url.pathname.replace(/\/\d+(?=\/|$)/g, '/:id')
    return `${path} [${url.searchParams.get('scope') === 'synthetic' ? 'F' : 'default'}]`
  })
  diagnostics.watch(page)

  try {
    diagnostics.at('register through the public form')
    await page.goto('/app/login')
    await page.getByRole('link', { name: '创建普通账号' }).click()
    await expect(page.getByRole('heading', { name: '创建普通账号' })).toBeVisible()
    await page.getByLabel('用户名').fill(account.username)
    await page.getByLabel('密码').fill(account.password)
    const [registered] = await Promise.all([
      apiResponse(page, '/api/v1/auth/register/', 'POST'),
      page.getByRole('button', { name: '创建账号', exact: true }).press('Enter'),
    ])
    expect(registered.status()).toBe(201)
    expect((await registered.json()).role).toBe('USER')
    expect(Boolean(await registered.request().headerValue('x-csrftoken'))).toBe(true)
    await expect(page).toHaveURL('/app/login')

    diagnostics.at('authenticate in the default formal scope')
    const formalOverview = apiResponse(page, nationalPath)
    await signIn(page, account.username, account.password)
    const formal = await formalOverview
    expect(formal.status()).toBe(403)
    expect(new URL(formal.url()).searchParams.has('scope')).toBe(false)
    await expect(page.getByLabel('数据域')).toHaveValue('')
    await expect(page.getByRole('heading', { name: '全国空气质量总览' })).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('当前筛选的观测不在可用范围内。')
    await expect(page.getByRole('link', { name: '采集任务', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: seed.cities[0].name, exact: true })).toHaveCount(0)

    diagnostics.at('select F scope and filter the common L snapshot')
    await useSyntheticScope(page)
    const table = page.getByRole('table', { name: '城市空气质量列表' })
    const cityRow = table.getByRole('row').filter({ has: page.getByRole('button', { name: seed.cities[0].name, exact: true }) })
    await expect(cityRow).toBeVisible()
    await page.getByLabel('地域', { exact: true }).fill('north')
    const [filtered] = await Promise.all([
      page.waitForResponse(response => {
        const url = new URL(response.url())
        return url.pathname === nationalPath && url.searchParams.get('geography') === 'north'
      }),
      page.getByRole('button', { name: '应用检索', exact: true }).press('Enter'),
    ])
    expect(filtered.status()).toBe(200)
    const query = new URL(filtered.url()).searchParams
    expect(query.get('product') === String(seed.products.L)).toBe(true)
    expect(Date.parse(query.get('at') ?? '')).toBe(Date.parse(seed.snapshot_at))
    await expect(cityRow).toBeVisible()

    diagnostics.at('follow C and T from L and star the city')
    await cityRow.getByRole('button', { name: '城市记录', exact: true }).press('Enter')
    await expect(page).toHaveURL(url => url.searchParams.get('product') === String(seed.products.C))
    await expect(page.getByRole('region', { name: 'C 城市指标' })).toBeVisible()
    await expect(page.getByRole('region', { name: '独立 T 城市趋势' }).getByRole('img')).toBeVisible()
    const cityFavorite = await favorite(page, '关注城市')

    diagnostics.at('select the historical station site epoch and star the station')
    await page.getByRole('button', { name: /North Sample 01/ }).click()
    await expect(page).toHaveURL(url => url.searchParams.get('site_epoch') === String(seed.site_epoch))
    await page.getByRole('link', { name: '切换站址期', exact: true }).click()
    const epoch = page.getByRole('region', { name: '选择站址期' }).getByRole('article').filter({ hasText: '历史站址期' })
    await epoch.getByRole('button', { name: '查看记录', exact: true }).click()
    await expect(page).toHaveURL(url => url.searchParams.get('site_epoch') === String(seed.previous_site_epoch))
    const stationFavorite = await favorite(page, '关注站点')

    diagnostics.at('save the account profile')
    await page.getByRole('link', { name: '我的空间', exact: true }).click()
    const profile = page.getByRole('region', { name: '账号设置' })
    const familyName = profile.getByLabel('姓', { exact: true })
    const givenName = profile.getByLabel('名', { exact: true })
    await expect(familyName).toHaveValue('')
    await expect(givenName).toHaveValue('')
    await familyName.fill('测试')
    await givenName.fill('观察者')
    const [saved] = await Promise.all([
      apiResponse(page, '/api/v1/me/', 'PATCH'),
      profile.getByRole('button', { name: '保存资料', exact: true }).click(),
    ])
    expect(saved.status()).toBe(200)
    await expect(profile.getByText('个人资料已更新。', { exact: true })).toBeVisible()

    diagnostics.at('end the first session and revisit with a fresh login')
    const [logout] = await Promise.all([
      apiResponse(page, '/api/v1/auth/logout/', 'POST'),
      page.getByRole('button', { name: '退出', exact: true }).click(),
    ])
    expect(logout.status()).toBe(204)
    await expect(page).toHaveURL('/app/login')
    await signIn(page, account.username, account.password)
    await useSyntheticScope(page)
    await page.getByRole('link', { name: '我的空间', exact: true }).click()
    await expect(familyName).toHaveValue('测试')
    await expect(givenName).toHaveValue('观察者')

    diagnostics.at('revisit original products and the historical site epoch')
    const favorites = page.getByRole('region', { name: '我的关注' })
    await expect(favorites.getByRole('article')).toHaveCount(2)
    const cityLink = favorites.getByRole('link', { name: '查看城市详情', exact: true })
    await expect(cityLink).toBeVisible()
    if (evidence) {
      await favorites.screenshot({ path: join(evidence, 'observation-chain-favorites.png') })
    }
    await cityLink.click()
    await expect(page).toHaveURL(url => url.pathname === `/app/cities/${seed.city}`
      && url.searchParams.get('product') === String(seed.products.C)
      && Date.parse(url.searchParams.get('at') ?? '') === Date.parse(seed.snapshot_at))
    await expect(page.getByRole('region', { name: 'C 城市指标' })).toBeVisible()
    await page.getByRole('link', { name: '我的空间', exact: true }).click()
    const siteDetails = favorites.locator('details').filter({ hasText: '获准站址期' })
    await expect(siteDetails).toBeVisible()
    if (await siteDetails.getAttribute('open') === null) await siteDetails.getByText('获准站址期', { exact: true }).click()
    const stationLink = siteDetails.getByRole('link', { name: '查看此站址期', exact: true })
      .and(siteDetails.locator(`a[href*="site_epoch=${seed.previous_site_epoch}&"]`))
    await stationLink.click()
    await expect(page).toHaveURL(url => url.pathname === `/app/stations/${seed.station}`
      && url.searchParams.get('product') === String(seed.products.S)
      && url.searchParams.get('site_epoch') === String(seed.previous_site_epoch))
    await expect(page.getByRole('heading', { name: '最近有效观测' })).toBeVisible()

    diagnostics.at('verify the peer cannot read either private favorite')
    const peer = await browser.newContext({ baseURL })
    try {
      const peerPage = await peer.newPage()
      diagnostics.watch(peerPage)
      await peerPage.goto('/app/login')
      await signIn(peerPage, seed.accounts.peer.username, seed.accounts.peer.password)
      for (const id of [cityFavorite, stationFavorite]) {
        const response = await peerPage.request.get(`${favoritesPath}${id}/?scope=synthetic`)
        diagnostics.note(response.url(), response.status())
        expect(response.status()).toBe(404)
        expect((await response.json()).code).toBe('not_found')
      }
    } finally {
      await peer.close()
    }
    diagnostics.at('complete')
  } catch (error) {
    await diagnostics.save(page, error)
    throw error
  }
})
