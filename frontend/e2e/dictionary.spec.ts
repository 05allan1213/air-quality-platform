import { expect, test, type Page } from '@playwright/test'
import { browserDiagnostics } from './diagnostics'
import { browserSeed, type BrowserAccount } from './seed'

const directoryPath = '/admin/observations/city-search-alias/'
const nationalPath = '/api/v1/overview/national/'
const alias = 'Local Fixture Alias'

interface NationalResult {
  state: string
  results: { city: number; name: string }[]
}

async function signIn(page: Page, account: BrowserAccount, path: string) {
  await page.goto(`/app/login?${new URLSearchParams({ next: path.slice('/app'.length) })}`)
  await page.getByLabel('用户名', { exact: true }).fill(account.username)
  await page.getByLabel('密码', { exact: true }).fill(account.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
}

async function submitAlias(editor: Page, targetPath: string, button: '保存' | '清除别名') {
  const [response] = await Promise.all([
    editor.waitForResponse(response => new URL(response.url()).pathname === targetPath && response.request().method() === 'POST'),
    editor.getByRole('button', { name: button, exact: true }).click(),
  ])
  return response
}

async function clearAlias(editor: Page, targetPath: string) {
  const form = await editor.goto(targetPath, { timeout: 8_000 })
  expect(form?.status(), '清理前须成功打开原生别名表单').toBe(200)
  return submitAlias(editor, targetPath, '清除别名')
}

async function searchHome(page: Page, term: string): Promise<NationalResult> {
  await page.getByLabel('城市检索', { exact: true }).fill(term)
  const [request] = await Promise.all([
    page.waitForRequest(request => {
      const url = new URL(request.url())
      return url.pathname === nationalPath && url.searchParams.get('home') === '1' && url.searchParams.get('q') === term
    }, { timeout: 8_000 }),
    page.getByRole('button', { name: '应用检索', exact: true }).press('Enter'),
  ])
  const response = await request.response()
  expect(response?.status(), '首页筛选须收到真实后端响应').toBe(200)
  return await response!.json() as NationalResult
}

test('restricted city alias maintenance changes only F home search and retains the formal city identity', async ({ browser, baseURL, page }, testInfo) => {
  const seed = browserSeed()
  const city = seed.cities[0]
  const targetPath = `${directoryPath}${seed.products.L}/${city.id}/`
  const args = {
    product: String(seed.products.L), scope: seed.scope, at: seed.snapshot_at,
    standard_series: 'HJ633-2012',
  }
  const overviewPath = `/app/?${new URLSearchParams(args)}`
  const identity = [{ city: city.id, name: city.name }]
  const table = page.getByRole('table', { name: '城市空气质量列表', exact: true })
  const editorContext = await browser.newContext({ baseURL })
  const editor = await editorContext.newPage()
  let activePage = editor
  let failure: unknown
  const diagnostics = browserDiagnostics(testInfo, 'dictionary-diagnostic.json', baseURL, ({ pathname }) => {
    if (pathname === targetPath) return 'alias-form'
    if (pathname === directoryPath) return 'alias-directory'
    if (pathname === nationalPath) return 'national'
    if (/^\/api\/v1\/cities\/\d+\/$/.test(pathname)) return 'city-detail'
    if (pathname === '/api/v1/auth/login/' || pathname === '/app/login') return 'login'
    if (pathname === '/app/' || pathname === '/app/manage/operations') return 'application'
  })
  diagnostics.watch(editor)
  diagnostics.watch(page)

  try {
    diagnostics.at('editor-login')
    await signIn(editor, seed.accounts.admin, '/app/manage/operations?scope=synthetic')
    await expect(editor.getByRole('heading', { name: '系统运行', exact: true })).toBeVisible()
    diagnostics.at('editor-directory')
    await editor.getByRole('link', { name: '城市检索别名', exact: true }).click()
    await expect(editor).toHaveURL(url => url.pathname === directoryPath)
    const target = editor.getByRole('link', { name: city.name, exact: true })
    await expect(target).toHaveAttribute('href', targetPath)
    await target.click()
    await expect(editor).toHaveURL(url => url.pathname === targetPath)

    const aliasInput = editor.getByLabel('检索别名', { exact: true })
    const setVersion = editor.locator('form:has(input[name="action"][value="set"]) input[name="expected_version"]')
    await expect(aliasInput).toHaveValue('')
    await expect(setVersion).toHaveValue('0')

    activePage = page
    diagnostics.at('reader-login-and-initial-home')
    await signIn(page, seed.accounts.reader, overviewPath)
    await expect(table.getByRole('button', { name: city.name, exact: true })).toBeVisible()
    const denied = await page.request.get(targetPath, { maxRedirects: 0 })
    diagnostics.note(denied.url(), denied.status())
    expect(denied.status()).toBe(302)
    expect(new URL(denied.headers().location, denied.url()).pathname).toBe('/admin/login/')

    activePage = editor
    diagnostics.at('set-alias')
    await aliasInput.fill(alias)
    const saved = await submitAlias(editor, targetPath, '保存')
    expect(saved.status()).toBe(200)
    try {
      expect(await saved.json()).toEqual({ action_id: expect.any(String), status: 'set', version: 1 })
      await editor.goto(targetPath)
      await expect(aliasInput).toHaveValue(alias)
      await expect(setVersion).toHaveValue('1')

      activePage = page
      diagnostics.at('reader-finds-alias')
      const home = await searchHome(page, alias)
      expect(home.state).toBe('ok')
      expect(home.results.map(({ city, name }) => ({ city, name }))).toEqual(identity)
      expect(JSON.stringify(home.results).includes(alias)).toBe(false)
      await expect(table.getByRole('rowheader')).toHaveText([city.name])

      diagnostics.at('legacy-name-search')
      const legacy = await page.request.get(nationalPath, { params: { ...args, q: alias } })
      diagnostics.note(legacy.url(), legacy.status())
      expect(legacy.status()).toBe(200)
      const unmatched = await legacy.json() as NationalResult
      expect(unmatched.state).toBe('no_data')
      expect(unmatched.results).toHaveLength(0)

      const formal = await page.request.get(nationalPath, { params: { ...args, q: city.name } })
      diagnostics.note(formal.url(), formal.status())
      expect(formal.status()).toBe(200)
      const matched = await formal.json() as NationalResult
      expect(matched.results.map(({ city, name }) => ({ city, name }))).toEqual(identity)

      diagnostics.at('formal-city-drilldown')
      const cityPath = `/api/v1/cities/${city.id}/`
      const [detail] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === cityPath),
        table.getByRole('button', { name: '城市记录', exact: true }).click(),
      ])
      expect(detail.status()).toBe(200)
      await expect(page).toHaveURL(url => url.pathname === `/app/cities/${city.id}` && url.searchParams.get('scope') === seed.scope)
      await expect(page.getByRole('heading', { name: city.name, exact: true })).toBeVisible()
    } catch (error) {
      // The diagnostic retains any cleanup response, while the test reports the first failed assertion.
      await clearAlias(editor, targetPath).catch(() => undefined)
      throw error
    }

    activePage = editor
    diagnostics.at('clear-alias')
    const cleared = await clearAlias(editor, targetPath)
    expect(cleared.status()).toBe(200)
    expect(await cleared.json()).toEqual({ action_id: expect.any(String), status: 'cleared', version: 2 })
    await editor.goto(targetPath)
    await expect(aliasInput).toHaveValue('')
    await expect(setVersion).toHaveValue('2')
    await expect(editor.getByRole('button', { name: '清除别名', exact: true })).toHaveCount(0)

    activePage = page
    diagnostics.at('reader-search-after-clear')
    await page.goto(overviewPath)
    await expect(table.getByRole('button', { name: city.name, exact: true })).toBeVisible()
    const after = await searchHome(page, alias)
    expect(after.state).toBe('no_data')
    expect(after.results).toHaveLength(0)
    await expect(table.getByRole('rowheader')).toHaveCount(0)
    const byName = await searchHome(page, city.name)
    expect(byName.results.map(({ city, name }) => ({ city, name }))).toEqual(identity)
    await expect(table.getByRole('rowheader')).toHaveText([city.name])
    diagnostics.at('complete')
  } catch (error) {
    failure = error
    throw error
  } finally {
    try {
      await diagnostics.save(activePage, failure)
    } finally {
      await editorContext.close()
    }
  }
})
