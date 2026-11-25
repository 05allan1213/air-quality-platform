import { expect, test, type Locator, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import AxeBuilder from '@axe-core/playwright'
import { browserSeed } from './seed'
import { foregroundPair, switchAway } from './tabFocus'

const evidence = process.env.S08_EVIDENCE_DIR ? join(process.env.S08_EVIDENCE_DIR, 'overlays') : undefined
const queryPath = '/api/v1/observations/query/'
const dailyPath = '/api/v1/observations/current/'

async function signIn(page: Page) {
  const { reader } = browserSeed().accounts
  const csrf = await page.request.get('/api/v1/auth/csrf/')
  expect(csrf.status()).toBe(200)
  const { csrf_token } = await csrf.json() as { csrf_token: string }
  const login = await page.request.post('/api/v1/auth/login/', {
    headers: { Origin: new URL(csrf.url()).origin, 'X-CSRFToken': csrf_token },
    data: { username: reader.username, password: reader.password },
  })
  expect(login.status()).toBe(200)
  await page.goto('/app/query?scope=synthetic')
  await expect(page.getByRole('heading', { name: '数据查询' })).toBeVisible()
}

async function pickObject(page: Page, product: number, city: number) {
  const select = page.getByLabel('来源产品与对象')
  await expect(select).toBeEnabled()
  const choice = `${product}:${city}`
  while (await select.locator(`option[value="${choice}"]`).count() === 0) {
    const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/catalog/objects/')
    await page.getByRole('button', { name: '加载更多目录项' }).click()
    expect((await pending).status()).toBe(200)
    await expect(page.getByText('正在读取获准目录…')).toHaveCount(0)
  }
  await select.selectOption(choice)
}

async function popupFit(popup: Locator) {
  const fit = await popup.evaluate(element => {
    const bounds = element.getBoundingClientRect()
    const surface = element.closest('.aq-query-time-dialog')!
    const centre = document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2)
    return { viewport: document.documentElement.clientWidth, left: Math.floor(bounds.left), right: Math.ceil(bounds.right),
      withinDialog: surface.contains(element), onTop: !!centre && element.contains(centre),
      opaque: getComputedStyle(element).backgroundColor.startsWith('rgb(') }
  })
  expect(fit.left).toBeGreaterThanOrEqual(0)
  expect(fit.right).toBeLessThanOrEqual(fit.viewport)
  expect(fit.withinDialog).toBe(true)
  expect(fit.onTop).toBe(true)
  expect(fit.opaque).toBe(true)
  return fit
}

async function snapshot(page: Page, name: string) {
  if (!evidence) return
  mkdirSync(evidence, { recursive: true })
  await page.screenshot({ path: join(evidence, `${name}.png`), mask: [page.locator('.account-link')] })
}

function writeReceipt(name: string, receipt: unknown) {
  if (!evidence) return
  mkdirSync(evidence, { recursive: true })
  writeFileSync(join(evidence, `${name}.json`), JSON.stringify(receipt, null, 2))
}

test('nested window, calendar and offset help stay in the modal with keyboard and a real hourly read', async ({ page }) => {
  const seed = browserSeed()
  const replies: number[] = []
  page.on('response', response => {
    if (new URL(response.url()).pathname === queryPath) replies.push(response.status())
  })
  await signIn(page)
  await pickObject(page, seed.products.C, seed.city)
  const opener = page.getByRole('button', { name: '设置查询时段' })
  const dialog = page.getByRole('dialog', { name: '设置查询时段', exact: true })
  const window = dialog.getByRole('combobox', { name: '统计窗口' })
  const start = '2024-02-01T09:00:00+08:00'
  const end = '2024-02-01T11:00:09+08:00'
  await page.getByLabel('开始时刻（含）').fill(start)
  await page.getByLabel('结束时刻（不含）').fill(end)
  await opener.press('Enter')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('保留条件后，回到查询页应用。')).toBeFocused()
  await expect(page.locator('.application')).toHaveAttribute('inert', '')
  await expect(page.locator('body')).toHaveClass(/el-popup-parent--hidden/)
  const accessibility = await page.context().newCDPSession(page)
  const { nodes } = await accessibility.send('Accessibility.getFullAXTree')
  await accessibility.detach()
  expect(nodes.some(node => !node.ignored && node.role?.value === 'button' && node.name?.value === '应用查询')).toBe(false)
  await page.locator('.application .topbar a').first().evaluate(element => (element as HTMLElement).focus())
  expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true)
  await dialog.getByRole('button', { name: '保留条件' }).focus()
  await page.keyboard.press('Tab')
  await expect(window).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(dialog.getByRole('button', { name: '保留条件' })).toBeFocused()

  await window.press('ArrowDown')
  const menu = dialog.getByRole('listbox')
  await expect(menu).toBeVisible()
  await expect(dialog.getByRole('option', { name: '小时 · 起点标记' })).toBeVisible()
  const selectFit = await popupFit(dialog.locator('.el-select__popper.aq-query-select-popup:visible'))
  await snapshot(page, 'query-select-1440')
  await page.keyboard.press('Escape')
  await expect(menu).toBeHidden()
  await expect(dialog).toBeVisible()
  await expect(window).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(opener).toBeFocused()
  await expect(page.locator('.application')).not.toHaveAttribute('inert', '')

  await opener.press('Enter')
  await expect(dialog).toBeVisible()
  await window.press('ArrowDown')
  await expect(menu).toBeVisible()
  await window.press('End')
  await window.press('Enter')
  await expect(menu).toBeHidden()
  await window.press('ArrowDown')
  await expect(menu.getByRole('option', { name: '滚动 24 小时 · 终点标记', selected: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(menu).toBeHidden()
  await dialog.getByRole('button', { name: '取消' }).press('Enter')
  await expect(opener).toBeFocused()
  await expect(page.getByLabel('开始时刻（含）')).toHaveValue(start)
  await expect(page.getByLabel('结束时刻（不含）')).toHaveValue(end)
  expect(replies).toEqual([])

  await page.getByRole('button', { name: '实体主题' }).click()
  const viewports = []
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 720 })
    await opener.press('Enter')
    await expect(dialog).toBeVisible()
    const calendarButton = dialog.getByRole('button', { name: '选日期：结束时刻（不含）' })
    await calendarButton.press('Enter')
    const calendar = dialog.getByRole('grid')
    await expect(calendar).toBeVisible()
    await expect(calendar.locator('[tabindex="0"]')).toBeFocused()
    const popup = dialog.locator('.aq-query-calendar-popup:visible')
    const fit = await popupFit(popup)
    const nestedLayer = await popup.evaluate(node => {
      const modalLayer = node.closest('.aq-query-time-scrim')!
      return Number(getComputedStyle(node).zIndex) > Number(getComputedStyle(modalLayer).zIndex)
    })
    expect(nestedLayer).toBe(true)
    const surface = await dialog.locator('.aq-query-time-dialog').evaluate(element => ({
      fill: getComputedStyle(element).backgroundColor, filter: getComputedStyle(element).backdropFilter,
      documentWidth: document.documentElement.scrollWidth,
    }))
    expect(surface.fill).toBe('rgb(245, 250, 255)')
    expect(surface.filter).toBe('none')
    expect(surface.documentWidth).toBeLessThanOrEqual(width)
    const tree = await dialog.ariaSnapshot()
    expect(tree).toContain('grid')
    expect(tree).not.toContain('应用查询')
    viewports.push({ width, ...fit, ...surface, nestedLayer })
    await snapshot(page, `query-calendar-solid-${width}`)
    await page.keyboard.press('Escape')
    await expect(calendar).toBeHidden()
    await expect(calendarButton).toBeFocused()
    if (width === 320) {
      await calendarButton.click()
      await expect(calendar).toBeVisible()
      await calendar.locator('[tabindex="0"]').click()
      await expect(calendar).toBeHidden()
      await expect(dialog.getByRole('textbox', { name: '结束时刻（不含）', exact: true })).toHaveValue(end)
    }
    if (width === 390) {
      await page.keyboard.press('Escape')
      await expect(dialog).toBeHidden()
      await expect(opener).toBeFocused()
    }
  }

  const tip = dialog.getByRole('button', { name: '偏移说明' })
  await tip.focus()
  const tooltip = dialog.getByRole('tooltip').filter({ hasText: 'Z 表示 UTC。' })
  await expect(tooltip).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(tooltip).toBeHidden()
  await expect(tip).toBeFocused()
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '保留条件' }).press('Enter')
  await expect(opener).toBeFocused()
  expect(replies).toEqual([])
  const pending = page.waitForResponse(response => new URL(response.url()).pathname === queryPath)
  await page.getByRole('button', { name: '应用查询' }).press('Enter')
  const response = await pending
  expect(response.status()).toBe(200)
  const params = new URL(response.url()).searchParams
  expect(Object.fromEntries(params)).toMatchObject({ scope: 'synthetic', product: `${seed.products.C}`, start, end,
    window_definition: 'hourly_label_unspecified', report_kind: 'hourly' })
  await expect(page.getByRole('heading', { name: '查询结果' })).toBeVisible()
  writeReceipt('query-window-overlays', { origin: 'F', selectFit, viewports, hourlyRead: response.status(),
    oldDraftSupported: true, backgroundLocked: true, nestedEscape: true, tooltipKeyboard: true })
})

test('daily calendar uses the source-local report date and still requires explicit read', async ({ page }) => {
  const seed = browserSeed()
  let reads = 0
  page.on('request', request => { if (new URL(request.url()).pathname === dailyPath) reads++ })
  await page.setViewportSize({ width: 390, height: 844 })
  await signIn(page)
  await pickObject(page, seed.products.D, seed.city)
  await page.getByRole('radio', { name: /^AQI / }).check()
  await page.getByLabel('AQI / 等级标准系列').selectOption('HJ633-2012')
  const opener = page.getByRole('button', { name: '设置查询时段' })
  await opener.press('Enter')
  const dialog = page.getByRole('dialog', { name: '设置查询时段', exact: true })
  const input = dialog.getByRole('textbox', { name: '源地方报告日', exact: true })
  await input.fill('2024-02-01')
  const calendarButton = dialog.getByRole('button', { name: '选日期：源地方报告日' })
  await calendarButton.press('Enter')
  const grid = dialog.getByRole('grid')
  await expect(grid.locator('[tabindex="0"]')).toBeFocused()
  const axe = await new AxeBuilder({ page }).include('.aq-query-time-dialog')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  const rules = (findings: typeof axe.violations) => findings.map(rule => ({ rule: rule.id, nodes: rule.nodes.length }))
  await page.keyboard.press('ArrowRight')
  await expect(grid.locator('[tabindex="0"]')).toHaveText('2')
  await expect(grid.locator('[tabindex="0"]')).toBeFocused()
  await expect(input).toHaveValue('2024-02-01')
  await page.keyboard.press('Enter')
  await expect(grid).toBeHidden()
  await expect(calendarButton).toBeFocused()
  await expect(input).toHaveValue('2024-02-02')
  await dialog.getByRole('button', { name: '保留条件' }).press('Enter')
  await expect(opener).toBeFocused()
  await expect(page.getByLabel('源地方报告日')).toHaveValue('2024-02-02')
  expect(reads).toBe(0)
  const pending = page.waitForResponse(response => new URL(response.url()).pathname === dailyPath)
  await page.getByRole('button', { name: '读取日报' }).press('Enter')
  const response = await pending
  expect(response.status()).toBe(200)
  expect(Object.fromEntries(new URL(response.url()).searchParams)).toMatchObject({
    scope: 'synthetic', product: `${seed.products.D}`, city: `${seed.city}`, local_report_date: '2024-02-02',
    report_kind: 'daily', window_definition: 'local_daily', standard_series: 'HJ633-2012',
  })
  const payload = await response.json()
  expect(payload.count).toBe(1)
  writeReceipt('query-daily-calendar', { origin: 'F', dailyRead: response.status(), sourceLocalDate: true, keyboardSelection: true,
    axe: { passedRules: axe.passes.length, violations: rules(axe.violations), needsManualReview: rules(axe.incomplete) } })
  expect(rules(axe.violations)).toEqual([])
})

test('a real tab return restores time edits and focus after checking the session and catalog', async ({ page }) => {
  const seed = browserSeed()
  const profilePath = '/api/v1/me/'
  const capabilitiesPath = '/api/v1/me/capabilities/'
  const directoryPath = '/api/v1/catalog/objects/'
  const queryResponses: number[] = []
  page.on('response', response => {
    if (new URL(response.url()).pathname === queryPath) queryResponses.push(response.status())
  })
  await signIn(page)
  const other = await foregroundPair(page)
  await pickObject(page, seed.products.C, seed.city)
  const start = '2024-02-01T09:00:00+08:00'
  await page.getByLabel('开始时刻（含）').fill(start)
  await page.getByLabel('结束时刻（不含）').fill('2024-02-01T11:00:00+08:00')
  await page.getByRole('button', { name: '应用查询' }).press('Enter')
  const result = page.getByRole('heading', { name: '查询结果' })
  await expect(result).toBeVisible()
  await expect.poll(() => queryResponses).toEqual([200])

  const opener = page.getByRole('button', { name: '设置查询时段' })
  const dialog = page.getByRole('dialog', { name: '设置查询时段', exact: true })
  await opener.press('Enter')
  const end = dialog.getByRole('textbox', { name: '结束时刻（不含）', exact: true })
  const partial = '2024-02-01T11:'
  await end.fill(partial)
  await expect(end).toBeFocused()
  await switchAway(page, other)

  let release!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  let verifying = false
  await page.route(url => url.pathname === capabilitiesPath, async route => {
    const response = await route.fetch()
    verifying = true
    await held
    await route.fulfill({ response })
  }, { times: 1 })
  try {
    const pendingProfile = page.waitForResponse(response => new URL(response.url()).pathname === profilePath)
    const pendingCapabilities = page.waitForResponse(response => new URL(response.url()).pathname === capabilitiesPath)
    const pendingDirectory = page.waitForResponse(response => new URL(response.url()).pathname === directoryPath)
    await page.bringToFront()
    await expect.poll(() => verifying).toBe(true)
    await expect(page.getByText('正在核验当前会话…')).toBeVisible()
    await expect(dialog).toHaveCount(0)
    await expect(result).toHaveCount(0)
    release()
    expect((await pendingProfile).status()).toBe(200)
    expect((await pendingCapabilities).status()).toBe(200)
    expect((await pendingDirectory).status()).toBe(200)
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('textbox', { name: '开始时刻（含）', exact: true })).toHaveValue(start)
    await expect(end).toHaveValue(partial)
    await expect(end).toBeFocused()
    await expect(result).toHaveCount(0)
    expect(queryResponses).toEqual([200])

    const finalEnd = '2024-02-01T11:00:09+08:00'
    await end.fill(finalEnd)
    await dialog.getByRole('button', { name: '保留条件' }).press('Enter')
    await expect(dialog).toBeHidden()
    await expect(opener).toBeFocused()
    await expect(page.getByLabel('结束时刻（不含）')).toHaveValue(finalEnd)
    expect(queryResponses).toEqual([200])
    writeReceipt('query-time-tab-resume', { origin: 'F', trustedAway: true, newAuthorization: true, currentCatalog: true,
      restoredPartialEdit: true, restoredFocus: true, staleResultsRemoved: true, automaticReadsAfterResume: 0 })
  } finally {
    release()
  }
})

test('returning to a whole-detail handoff preserves its verified indicator and standard', async ({ page }) => {
  const seed = browserSeed()
  await signIn(page)
  const intent = new URLSearchParams({ intent: 'whole', scope: 'synthetic', product: `${seed.products.C}`, source: seed.source,
    city: `${seed.city}`, indicator: 'AQI', unit: '1', standard_series: 'HJ633-2012', report_kind: 'hourly',
    window_definition: 'hourly_label_unspecified', start: seed.start, end: seed.end })
  await page.goto(`/app/query?${intent}`)
  const other = await foregroundPair(page)
  await expect(page.getByRole('radio', { name: '整组明细' })).toBeChecked()
  const aqi = page.getByRole('checkbox', { name: 'AQI 1', exact: true })
  const series = page.getByLabel('AQI / 等级标准系列')
  await expect(aqi).toBeChecked()
  await expect(series).toHaveValue('HJ633-2012')
  await page.getByRole('button', { name: '设置查询时段' }).press('Enter')
  const dialog = page.getByRole('dialog', { name: '设置查询时段', exact: true })
  const input = dialog.getByRole('textbox', { name: '开始时刻（含）', exact: true })
  await input.fill('2024-02-01T08:')
  await switchAway(page, other)
  const verified = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/me/capabilities/')
  await page.bringToFront()
  expect((await verified).status()).toBe(200)
  await expect(input).toHaveValue('2024-02-01T08:')
  await expect(input).toBeFocused()
  await dialog.getByRole('button', { name: '取消' }).press('Enter')
  await expect(dialog).toBeHidden()
  await expect(page.getByRole('radio', { name: '整组明细' })).toBeChecked()
  await expect(aqi).toBeChecked()
  await expect(series).toHaveValue('HJ633-2012')
  writeReceipt('query-handoff-tab-resume', { origin: 'F', trustedAway: true, restoredFocus: true, preservedIntent: true })
})
