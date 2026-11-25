import { expect, test, type Page, type Request, type Response } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { browserSeed, type BrowserAccount } from './seed'

type ApiReceipt = {
  stage: string
  path: string
  method: string
  status: number | null
  directoryScope?: 'synthetic' | 'default' | 'other'
  outcome?: 'finished' | 'failed'
}

function apiResponse(path: string, method: string) {
  return (response: Response) => new URL(response.url()).pathname === `/api/v1/${path}/` && response.request().method() === method
}

async function openQuery(page: Page, account: BrowserAccount) {
  await page.goto('/app/query?scope=synthetic')
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
  await page.getByLabel('用户名').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/app\/query\?scope=synthetic$/)
  await expect(page.getByRole('heading', { name: '数据查询' })).toBeVisible()
}

function parseCsv(text: string) {
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  for (let pos = 0; pos < text.length; pos += 1) {
    const char = text[pos]
    if (char === '"') {
      if (quoted && text[pos + 1] === '"') { cell += '"'; pos += 1 }
      else quoted = !quoted
    } else if (!quoted && char === ',') {
      row.push(cell); cell = ''
    } else if (!quoted && (char === '\r' || char === '\n')) {
      if (char === '\r' && text[pos + 1] === '\n') pos += 1
      row.push(cell); rows.push(row); row = []; cell = ''
    } else cell += char
  }
  if (cell || row.length) rows.push([...row, cell])
  const header = rows.shift() ?? []
  return rows.map(cells => Object.fromEntries(header.map((name, index) => [name, cells[index]])))
}

test('independent query checks exact readings, export scope, version, CSV and ownership', async ({ page, context }) => {
  const seed = browserSeed()
  const evidence = process.env.S08_EVIDENCE_DIR
  const screenshotOptions = { fullPage: true, mask: [page.locator('.account-link')] }
  const tests: Record<string, unknown> = { origin: 'F' }
  const requests: ApiReceipt[] = []
  const entries = new WeakMap<Request, ApiReceipt>()
  let stage = 'reader direct login'
  let directoryPageAttempt = 1
  page.on('request', request => {
    const { pathname, searchParams } = new URL(request.url())
    if (!pathname.startsWith('/api/v1/')) return
    const path = pathname.replace(/\/(?:\d+|[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12})(?=\/|$)/gi, '/:id')
    const entry: ApiReceipt = { stage, path, method: request.method(), status: null }
    if (path === '/api/v1/catalog/objects/') {
      const scope = searchParams.get('scope')
      entry.directoryScope = scope === 'synthetic' ? 'synthetic' : scope === null ? 'default' : 'other'
    }
    requests.push(entry)
    entries.set(request, entry)
  })
  page.on('response', response => {
    const entry = entries.get(response.request())
    if (entry) entry.status = response.status()
  })
  page.on('requestfinished', request => {
    const entry = entries.get(request)
    if (entry) entry.outcome = 'finished'
  })
  page.on('requestfailed', request => {
    const entry = entries.get(request)
    if (entry) entry.outcome = 'failed'
  })

  try {
    await openQuery(page, seed.accounts.reader)
    stage = 'wait for initial directory'
    const option = page.getByLabel('来源产品与对象')
    const target = option.locator(`option[value="${seed.products.C}:${seed.city}"]`)
    await expect(option).toBeEnabled()
    while (await target.count() === 0) {
      stage = 'find next directory page'
      await expect(page.getByRole('button', { name: '加载更多目录项' })).toBeEnabled()
      stage = 'load next directory page'
      directoryPageAttempt += 1
      const pending = page.waitForResponse(apiResponse('catalog/objects', 'GET'))
      await page.getByRole('button', { name: '加载更多目录项' }).click()
      expect((await pending).status()).toBe(200)
      await expect(option).toBeEnabled()
      await expect.poll(async () => await target.count() || await page.getByRole('button', { name: '加载更多目录项' }).count()).toBeGreaterThan(0)
      await expect(page.getByText('正在读取获准目录…')).toHaveCount(0)
    }
    stage = 'select directory object'
    await option.selectOption(`${seed.products.C}:${seed.city}`)

    stage = 'latest and exact'
    await page.getByRole('radio', { name: '最近有效' }).check()
    const latest = page.waitForResponse(apiResponse('observations/query', 'GET'))
    await page.getByRole('button', { name: '应用查询' }).press('Enter')
    const latestResponse = await latest
    expect(latestResponse.status()).toBe(200)
    expect(new URL(latestResponse.url()).searchParams.get('mode')).toBe('latest_valid')
    expect(new URL(latestResponse.url()).searchParams.get('quality')).toBe('valid')
    await expect(page.getByText('自由查询条件与整组 CSV 的口径不同。', { exact: false })).toBeVisible()
    await expect(page.getByRole('button', { name: '确认导出范围' })).toHaveCount(0)
    const exact = page.waitForResponse(apiResponse('observations/current', 'GET'))
    const exactButton = page.getByRole('button', { name: '精确读取' }).first()
    await exactButton.press('Enter')
    expect((await exact).status()).toBe(200)
    await expect(page.getByRole('heading', { name: '精确观测' })).toBeFocused()
    await expect(page.getByText('精确读版本', { exact: false })).toBeVisible()
    await page.getByRole('button', { name: '关闭精确观测' }).press('Enter')
    await expect(exactButton).toBeFocused()

    stage = 'explicit independent whole-detail query at 390px'
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('button', { name: '打开整组明细' }).press('Enter')
    await expect(page.getByLabel('开始时刻（含）')).toBeFocused()
    const indicatorGroup = page.getByRole('group', { name: '指标', exact: true })
    for (const selected of await indicatorGroup.getByRole('checkbox', { checked: true }).all()) await selected.uncheck()
    await indicatorGroup.getByRole('checkbox', { name: /^NO2/ }).check()
    const start = new Date(Date.parse(seed.start) + 8 * 3600_000).toISOString()
    const end = new Date(Date.parse(seed.start) + 11 * 3600_000).toISOString()
    await page.getByLabel('开始时刻（含）').fill(start)
    await page.getByLabel('结束时刻（不含）').fill(end)
    const first = page.waitForResponse(apiResponse('analysis/details', 'POST'))
    await page.getByRole('button', { name: '应用查询' }).press('Enter')
    const firstResponse = await first
    expect(firstResponse.status()).toBe(200)
    const firstRequest = firstResponse.request().postDataJSON()
    const firstBody = await firstResponse.json()
    expect(firstRequest).toMatchObject({ method: 'detail', support: 'observed', page_size: 2,
      selection: { source: seed.source, product: seed.products.C, cities: [seed.city], scope: 'synthetic',
        indicators: ['NO2'], units: { NO2: 'µg/m³' }, standard_series: { NO2: 'not_applicable' }, quality: 'reliable',
        start, end, report_kind: 'hourly', window_definition: 'hourly_label_unspecified' } })
    expect(firstRequest).not.toHaveProperty('input_version')
    expect(firstBody.input_version).toMatch(/^[a-f0-9]{64}$/)
    expect(firstBody.results).toHaveLength(2)
    const grid = page.getByRole('table', { name: '本页观测' })
    await expect(grid.getByRole('row')).toHaveCount(3)
    await expect(grid.getByRole('row').nth(1)).toContainText('有效')
    await expect(grid.getByRole('row').nth(2)).toContainText('删失')
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
    const localScroll = await page.getByRole('region', { name: '本页观测（可水平滚动）' }).evaluate(el => el.scrollWidth > el.clientWidth)
    expect(localScroll).toBe(true)
    const next = page.waitForResponse(apiResponse('analysis/details', 'POST'))
    await page.getByRole('button', { name: '下一页' }).press('Enter')
    const nextResponse = await next
    expect(nextResponse.status()).toBe(200)
    const nextRequest = nextResponse.request().postDataJSON()
    const nextBody = await nextResponse.json()
    expect(nextRequest).toMatchObject({ input_version: firstBody.input_version, selection: firstBody.selection, cursor: firstBody.page.next_cursor })
    expect(nextBody.input_version).toBe(firstBody.input_version)
    await expect(grid.getByRole('row')).toHaveCount(2)
    await expect(grid.getByRole('row').nth(1)).toContainText('缺测')
    await expect(page.getByText('第 2 页 · 本页 1 条 · 整组 3 条')).toBeVisible()

    stage = 'cancel confirmation without issuing a ticket'
    let prepares = 0
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/v1/me/exports/' && request.method() === 'POST') prepares += 1 })
    const preview = page.getByRole('button', { name: '确认导出范围' })
    await preview.press('Enter')
    const confirmation = page.getByRole('region', { name: '确认整组 CSV' })
    await expect(confirmation.getByRole('heading')).toBeFocused()
    await expect(confirmation).toContainText('已核整组 3 条')
    await page.getByRole('button', { name: '取消导出' }).press('Enter')
    await expect(confirmation).toHaveCount(0)
    await expect(preview).toBeFocused()
    expect(prepares).toBe(0)

    stage = 'explicit optional-field denial'
    await preview.press('Enter')
    await confirmation.getByRole('checkbox', { name: '判定版本' }).check()
    const denied = page.waitForResponse(apiResponse('me/exports', 'POST'))
    await confirmation.getByRole('button', { name: '确认并申请 CSV 票据' }).press('Enter')
    const deniedResponse = await denied
    expect(deniedResponse.status()).toBe(403)
    expect(deniedResponse.headers()['content-disposition']).toBeUndefined()
    await expect(confirmation.getByRole('alert')).toBeVisible()
    await expect(confirmation.getByRole('button', { name: '下载 CSV' })).toHaveCount(0)
    await confirmation.getByRole('checkbox', { name: '判定版本' }).uncheck()

    stage = 'authorized ticket and full-group download'
    if (evidence) await page.screenshot({ path: join(evidence, 'query-confirm-390.png'), ...screenshotOptions })
    const preparing = page.waitForResponse(apiResponse('me/exports', 'POST'))
    await confirmation.getByRole('button', { name: '确认并申请 CSV 票据' }).press('Enter')
    const issuedResponse = await preparing
    expect(issuedResponse.status()).toBe(201)
    expect(issuedResponse.request().postDataJSON()).toEqual({ kind: 'detail', fields: [], groups: [{ selection: firstBody.selection,
      method: 'detail', method_version: firstBody.method_version, grain: firstBody.grain, input_version: firstBody.input_version }] })
    const { id } = await issuedResponse.json()
    const downloadButton = confirmation.getByRole('button', { name: '下载 CSV' })
    await expect(downloadButton).toBeFocused()
    const csvResponse = page.waitForResponse(apiResponse(`me/exports/${id}/download`, 'GET'))
    const download = page.waitForEvent('download')
    await downloadButton.press('Enter')
    const csv = await csvResponse
    expect(csv.status()).toBe(200)
    expect(csv.headers()['content-disposition']).toMatch(/^attachment/)
    expect(csv.headers()['content-type']).toMatch(/^text\/csv/)
    const downloaded = await download
    expect(downloaded.suggestedFilename()).toBe('air-quality.csv')
    const stream = await downloaded.createReadStream()
    const chunks: Buffer[] = []
    for await (const chunk of stream) chunks.push(Buffer.from(chunk))
    const data = parseCsv(Buffer.concat(chunks).toString('utf8'))
    const expected = [...firstBody.results, ...nextBody.results] as { key: number; revision: number; observed_at: string }[]
    expect(data).toHaveLength(3)
    expect(data.map(row => row.quality)).toEqual(['valid', 'censored', 'missing'])
    expect(Number(data[0].value)).toBe(0)
    expect(data[0].value).not.toBe('')
    expect(data[1].value).toBe('')
    expect(data[1].censor_direction).toBe('<')
    expect(Number(data[1].censor_limit)).toBe(3)
    expect(data[2].value).toBe('')
    expect(data[2].censor_limit).toBe('')
    expect(data.map(row => [row.entity, row.source, row.product, row.unit, row.standard_series, row.input_version])).toEqual(
      Array.from({ length: 3 }, () => [String(seed.city), seed.source, String(seed.products.C), 'µg/m³', 'not_applicable', firstBody.input_version]),
    )
    expect(data.map(row => [Number(row.key), Number(row.revision), Date.parse(row.observed_at)])).toEqual(
      expected.map(row => [row.key, row.revision, Date.parse(row.observed_at)]),
    )

    stage = 'clear view on reader logout'
    await page.getByRole('button', { name: '退出' }).click()
    await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
    await expect(confirmation).toHaveCount(0)
    stage = 'peer direct login'
    await openQuery(page, seed.accounts.peer)
    stage = 'non-owner download'
    const nonOwnerRead: ApiReceipt = { stage, path: '/api/v1/me/exports/:id/download/', method: 'GET', status: null }
    requests.push(nonOwnerRead)
    const forbidden = await context.request.get(`/api/v1/me/exports/${id}/download/?scope=synthetic`)
    nonOwnerRead.status = forbidden.status()
    nonOwnerRead.outcome = 'finished'
    expect(forbidden.status()).toBe(404)
    expect(forbidden.headers()['content-disposition']).toBeUndefined()
    expect(await forbidden.json()).toMatchObject({ code: 'not_found' })
    await expect(page.getByRole('heading', { name: '整组明细预览' })).toHaveCount(0)
    tests.receipt = { latest: latestResponse.status(), details: [firstResponse.status(), nextResponse.status()],
      deniedField: deniedResponse.status(), ticket: issuedResponse.status(), download: csv.status(), otherUser: forbidden.status(),
      pages: [2, 1], csvRows: data.length, quality: data.map(row => row.quality), localScroll, viewport: 390 }
    stage = 'complete'
  } finally {
    if (evidence) writeFileSync(join(evidence, 'query-flow.json'), JSON.stringify({ ...tests, stage, directoryPageAttempt, requests }))
  }
})
