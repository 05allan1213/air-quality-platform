import { expect, test } from '@playwright/test'
import { browserSeed } from './seed'

for (const kind of ['city', 'station'] as const) {
  test(`${kind}: refresh a real F deep link, then leave its selection behind when switching domains`, async ({ page }) => {
    const seed = browserSeed()
    const entity = kind === 'city' ? `cities/${seed.city}` : `stations/${seed.station}`
    const selection = new URLSearchParams({ scope: 'synthetic', product: String(seed.products[kind === 'city' ? 'C' : 'S']), as_of: seed.snapshot_at })
    if (kind === 'station') selection.set('site_epoch', String(seed.site_epoch))
    const deepLink = `/app/${entity}?${selection}`
    const expectDeepLink = () => expect(page).toHaveURL(url =>
      url.pathname === `/app/${entity}`
      && [...selection].every(([key, value]) => url.searchParams.get(key) === value),
    )
    const objectPath = `/api/v1/${entity}/`
    const objectScopes: (string | null)[] = []
    page.on('request', request => {
      const url = new URL(request.url())
      if (url.pathname === objectPath) objectScopes.push(url.searchParams.get('scope'))
    })
    await page.goto(deepLink)
    await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
    await page.getByLabel('用户名').fill(seed.accounts.reader.username)
    await page.getByLabel('密码').fill(seed.accounts.reader.password)
    const heading = page.getByRole('heading', { name: kind === 'city' ? 'C 城市指标' : '最近有效观测' })
    const objectLoaded = () => page.waitForResponse(response => new URL(response.url()).pathname === objectPath && response.status() === 200)
    await Promise.all([objectLoaded(), page.getByRole('button', { name: '登录' }).click()])
    await expectDeepLink()
    await expect(heading).toBeVisible()
    await Promise.all([objectLoaded(), page.reload()])
    await expect(heading).toBeVisible()
    await expect(page.getByLabel('选择数据域')).toHaveValue('synthetic')
    await page.getByRole('link', { name: '我的空间' }).click()
    await expect(page).toHaveURL('/app/me?scope=synthetic')
    await page.goBack()
    await expectDeepLink()
    await expect(heading).toBeVisible()

    const overview = page.waitForRequest(request => {
      const url = new URL(request.url())
      return url.pathname === '/api/v1/overview/national/' && !url.searchParams.has('scope')
    })
    const [request] = await Promise.all([overview, page.getByLabel('选择数据域').selectOption('')])
    await expect(page).toHaveURL('/app/')
    await expect(page.getByLabel('选择数据域')).toHaveValue('')
    await expect(heading).toHaveCount(0)
    for (const stale of ['product', 'site_epoch', 'as_of', 'focus_city', 'grade', 'cursor']) {
      expect(new URL(request.url()).searchParams.has(stale)).toBe(false)
    }
    expect(objectScopes.length).toBeGreaterThanOrEqual(2)
    expect(objectScopes.every(scope => scope === 'synthetic')).toBe(true)
    await page.getByLabel('选择数据域').selectOption('synthetic')
    await expect(page).toHaveURL('/app/?scope=synthetic')
    await expect(page.getByRole('row').filter({ hasText: seed.cities[0].name })).toBeVisible()
  })
}
