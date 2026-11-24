import type { Locator, Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { expect, noOverflow, ready, test } from './fixtures'

async function selectOption(page: Page, field: string, option: string) {
  await page.locator('.sky-select').filter({ has: page.getByRole('combobox', { name: field }) }).click()
  await page.getByRole('option', { name: option, exact: true }).click()
}

async function center(locator: Locator) {
  const box = (await locator.boundingBox())!
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

test('首页搜索、组合筛选、排序、分页与详情使用同一份合成快照', async ({ page }) => {
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  const table = page.locator('.sky-table')
  await expect(table.locator('tbody tr')).toHaveCount(5)
  await page.getByRole('button', { name: '空气较好' }).click()
  await expect(page.locator('.rank-items li').first()).toContainText('海口')
  await page.getByRole('button', { name: '查看完整榜单' }).click()
  await expect(table.locator('tbody tr').first()).toContainText('海口')
  await page.getByRole('button', { name: '切换 AQI 排序' }).click()
  await expect(table.locator('tbody tr').first()).toContainText('石家庄')
  await page.getByRole('button', { name: '下一页' }).click()
  await expect(table.locator('tbody tr').first()).not.toContainText('石家庄')
  await page.getByLabel('搜索城市').fill('京')
  await selectOption(page, '省份筛选', '北京')
  await selectOption(page, 'AQI 等级筛选', '轻度污染')
  await expect(table.locator('tbody tr')).toHaveCount(1)
  await page.reload()
  await expect(page.getByLabel('搜索城市')).toHaveValue('京')
  await page.getByRole('button', { name: '查看北京详情' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('F 合成演示 · L 全国城市列表')
  await expect(dialog).toContainText('112')
  await page.keyboard.press('Escape')
  await selectOption(page, 'AQI 等级筛选', '严重污染')
  await expect(page.getByText('没有匹配的城市')).toBeVisible()
  await page.getByRole('button', { name: '清除筛选', exact: true }).click()
  await expect(table.locator('tbody tr')).toHaveCount(5)
  await page.getByLabel('搜索城市').fill('云汀')
  await page.getByRole('button', { name: '查看云汀市详情' }).click()
  await page.getByRole('link', { name: '打开同城 C 产品详情' }).click()
  await ready(page, '云汀市')
  await page.getByRole('link', { name: '监测总览', exact: true }).click()
  await ready(page, '全国空气质量总览')
})

test('地图可定位、切换等级、缩放并通过键盘打开同城记录', async ({ page }) => {
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  await page.getByRole('button', { name: '放大地图' }).click()
  await expect(page.getByRole('button', { name: '缩小地图' })).toBeEnabled()
  const surface = page.locator('.atlas-svg')
  const transform = await surface.locator(':scope > g').first().getAttribute('transform')
  const box = (await surface.boundingBox())!
  // Drag the plot background. City hit targets are reserved for selecting a sample.
  const start = { x: box.x + 4, y: box.y + 4 }
  expect(await surface.evaluate((el, p) => document.elementFromPoint(p.x, p.y) === el, start)).toBe(true)
  await page.mouse.move(start.x, start.y)
  await page.mouse.down()
  await page.mouse.move(start.x + 35, start.y + 20, { steps: 5 })
  await page.mouse.up()
  await expect(surface.locator(':scope > g').first()).not.toHaveAttribute('transform', transform!)
  await page.getByRole('button', { name: '复位地图' }).click()
  await expect(page.getByRole('button', { name: '缩小地图' })).toBeDisabled()
  await page.getByRole('button', { name: 'O₃ 等级', exact: true }).click()
  await expect(page.locator('.atlas-legend')).toContainText('O₃ 等级')
  await expect(page.locator('.atlas-legend')).toContainText('缺测')
  const kashgar = page.locator('.map-pin[aria-label^="喀什，"]')
  await kashgar.hover()
  await expect(page.locator('.map-callout')).toContainText('O₃ 缺测')
  await kashgar.focus()
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toContainText('和田')
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: /中度污染\s*2/ }).click()
  await expect(page.locator('.sky-table tbody tr')).toHaveCount(2)
})

test('图表指示、小时窗口与图例的数值可核对', async ({page}) => {
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  const trend = page.locator('.trend-panel .chart-surface')
  const box = (await trend.boundingBox())!
  await page.mouse.move(box.x + box.width * .57, box.y + box.height * .45)
  await expect(trend.getByText(/\d{2}:00 · \d+ 城/)).toBeVisible()
  await page.getByRole('button', { name: '6 小时' }).click()
  await page.getByRole('button', { name: '查看趋势数据表' }).click()
  await expect(page.getByRole('dialog').locator('tbody tr')).toHaveCount(6)
  await expect(page.getByRole('dialog').locator('tbody tr').last()).toContainText('47 城')
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: /座名录城市本时刻缺报/ }).click()
  await expect(page.getByRole('dialog').locator('li')).toHaveCount(3)
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '数据说明', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Natural Earth')
})

for (const [width, height] of [[1448,1086], [1440,900], [1920,1080], [2000,1080], [1280,800], [768,1024], [390,844], [320,720]]) {
  test(`${width}×${height} 画布和列表边界`, async ({ page }) => {
    await page.setViewportSize({ width, height })
    await page.goto('/')
    await ready(page, '全国空气质量总览')
    await noOverflow(page)
    for (const canvas of await page.locator('.chart-surface canvas').all()) {
      const box = await canvas.boundingBox()
      expect(box!.height).toBeGreaterThan(40)
    }
    if (width === 390) {
      const table = page.locator('.sky-table-scroll')
      await table.evaluate(el => el.scrollLeft = el.scrollWidth)
      await table.getByRole('button', {name: '查看石家庄详情'}).click()
      await expect(page.getByRole('dialog')).toContainText('176')
    }
    if (width === 390 || width === 1440) {
      if (width === 390) await page.keyboard.press('Escape')
      const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice']).analyze()
      expect(result.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([])
    }
  })
}

test('窄屏工作区入口沿用已有账号权限', async ({page}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  await page.getByRole('button', {name: '演示账号与权限设置'}).click()
  await page.getByRole('radio', {name: /观察账号/}).check()
  await page.getByRole('button', {name: '完成设置'}).click()
  await page.getByRole('button', {name: '打开导航'}).click()
  await page.getByRole('button', {name: '切换工作区'}).click()
  await expect(page.getByRole('dialog', {name: '演示设置'})).toBeVisible()
  await expect(page).toHaveURL(/\/$/)
  await page.getByRole('radio', {name: /具名治理账号/}).check()
  await page.getByRole('button', {name: '完成设置'}).click()
  await page.getByRole('button', {name: '打开导航'}).click()
  await page.getByRole('button', {name: '切换工作区'}).click()
  await ready(page, '无序异值争议')
})

test('实际圆心、悬浮、选中与详情同城，后退恢复 URL 中的选点', async ({ page }) => {
  await page.setViewportSize({ width: 2000, height: 1080 })
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  const pin = (name: string) => page.locator(`.map-pin[aria-label^="${name}，"]`)
  const callout = page.locator('.map-callout')
  for (const [city, aqi] of [['北京',112], ['上海',67], ['成都',103], ['广州',64], ['深圳',48], ['杭州',60], ['上海',67]] as const) {
    const dot = await center(pin(city).locator('.pin-dot'))
    const hit = await page.evaluate(p => document.elementFromPoint(p.x, p.y)?.closest('.map-pin')?.getAttribute('aria-label'), dot)
    expect(hit).toContain(`${city}，AQI ${aqi}`)
    await page.mouse.move(dot.x, dot.y)
    await expect(callout).toContainText(`${city}AQI ${aqi}`)
    await page.mouse.click(dot.x, dot.y)
    const dialog = page.getByRole('dialog', { name: `${city} · 同刻观测详情` })
    await expect(dialog.locator('.detail-reading strong')).toHaveText(String(aqi))
    await expect(pin(city)).toHaveAttribute('aria-pressed', 'true')
    await page.keyboard.press('Escape')
    await expect(page.locator('.sky-scrim')).toBeHidden()
    await expect(pin(city)).toBeFocused()
  }
  const hover = await center(pin('北京').locator('.pin-dot'))
  await page.mouse.move(hover.x, hover.y)
  await expect(callout).toContainText('北京')
  await expect(pin('上海')).toHaveAttribute('aria-pressed', 'true')
  for (const name of ['PM₂.₅ 等级','PM₁₀ 等级','O₃ 等级','AQI','放大地图','缩小地图','放大地图','复位地图']) {
    await page.getByRole('button', { name, exact: true }).click()
    await expect(pin('上海')).toHaveAttribute('aria-pressed', 'true')
    await expect(callout).toContainText('上海')
  }
  await page.getByRole('link', { name: '城市详情', exact: true }).click()
  await ready(page, '云汀市')
  await page.goBack()
  await expect(pin('上海')).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('link', { name: '城市详情', exact: true }).click()
  await page.getByRole('link', { name: '监测总览', exact: true }).click()
  await expect(pin('北京')).toHaveAttribute('aria-pressed', 'true')
})

test('宽屏上下排无交叠，两处底部入口的按钮中心能完成操作', async ({ page }) => {
  await page.setViewportSize({ width: 2000, height: 1080 })
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  const bounds = await page.evaluate(() => {
    const rects = (s: string) => [...document.querySelectorAll(s)].map(e => e.getBoundingClientRect())
    return { firstEnd: Math.max(...rects('.sky-primary > *').map(r => r.bottom)), nextStart: Math.min(...rects('.sky-stats > *').map(r => r.top)) }
  })
  expect(bounds.nextStart).toBeGreaterThan(bounds.firstEnd)
  for (const name of ['首要污染物分布', '样本到报趋势', '查看样本来源与地图口径', '查看完整榜单']) {
    const role = name.startsWith('查看') ? 'button' : 'heading'
    const el = page.getByRole(role, { name, exact: true })
    const dot = await center(el)
    expect(await el.evaluate((node, p) => node.contains(document.elementFromPoint(p.x, p.y)), dot), `${name}中心应命中自身`).toBe(true)
    if (role !== 'button') continue
    await page.mouse.click(dot.x, dot.y)
    if (name === '查看完整榜单') await expect(page.getByRole('heading', { name: '城市空气质量列表' })).toBeFocused()
    else {
      await expect(page.getByRole('dialog', { name: '数据与地图说明' })).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(page.locator('.sky-scrim')).toBeHidden()
      await expect(el).toBeFocused()
    }
  }
})

test('筛选框支持方向键、回车和取消，焦点留在触发器', async ({ page }) => {
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  for (const [label, key] of [['省份筛选', 'province'], ['AQI 等级筛选', 'grade']]) {
    const combo = page.getByRole('combobox', { name: label })
    await combo.focus()
    await page.keyboard.press('Enter')
    await expect(combo).toHaveAttribute('aria-expanded', 'true')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(new RegExp(`${key}=`))
    await expect(combo).toBeFocused()
    const selectedURL = page.url()
    await page.keyboard.press('Enter')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Escape')
    await expect(combo).toHaveAttribute('aria-expanded', 'false')
    await expect(combo).toBeFocused()
    await expect(page).toHaveURL(selectedURL)
  }
})

test('缩放后工具栏在绘图区外，Tab 进入地图可看到当前选中点', async ({ page }) => {
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/')
    await ready(page, '全国空气质量总览')
    const surface = page.locator('.atlas-svg')
    const tools = page.locator('.atlas-tools')
    const plus = tools.getByRole('button', { name: '放大地图' })
    for (let i = 0; i < 3; i++) await plus.click()
    await surface.scrollIntoViewIfNeeded()
    const plot = (await surface.boundingBox())!
    expect(plot.x + plot.width).toBeLessThanOrEqual((await tools.boundingBox())!.x)
    await page.mouse.move(plot.x + 2, plot.y + 2)
    await page.mouse.down()
    await page.mouse.move(plot.x + plot.width - 8, plot.y + plot.height - 8, { steps: 6 })
    await page.mouse.up()
    const active = page.locator('.map-pin[aria-pressed="true"]')
    const ring = active.locator('.pin-ring')
    const fits = () => ring.evaluate(node => {
      const svg = node.closest('svg')!.getBoundingClientRect(), r = node.getBoundingClientRect()
      return r.left >= svg.left && r.right <= svg.right && r.top >= svg.top && r.bottom <= svg.bottom
    })
    if (width === 1440) expect(await fits()).toBe(false)
    await page.getByRole('button', { name: 'O₃ 等级', exact: true }).focus()
    await page.keyboard.press('Tab')
    await expect(active).toBeFocused()
    expect(await fits()).toBe(true)
    await expect(active).toHaveAttribute('aria-label', /^北京，/)
  }
})

test('拖至西侧边界后署名不会截获下沿可见的武汉圆心', async ({ page }) => {
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  const surface = page.locator('.atlas-svg')
  const plus = page.getByRole('button', { name: '放大地图' })
  for (let i = 0; i < 3; i++) await plus.click()
  const plot = (await surface.boundingBox())!
  const y = plot.y + plot.height - 10
  await page.mouse.move(plot.x + plot.width - 18, y)
  await page.mouse.down()
  await page.mouse.move(plot.x - 80, y, { steps: 5 })
  await page.mouse.up()
  const wuhan = page.getByRole('button', { name: /^武汉，AQI/ })
  const point = await center(wuhan.locator('.pin-dot'))
  expect(point.y).toBeLessThanOrEqual(plot.y + plot.height)
  expect(await wuhan.evaluate((el, p) => el.contains(document.elementFromPoint(p.x, p.y)), point)).toBe(true)
  await page.mouse.click(point.x, point.y)
  await expect(page.getByRole('dialog', { name: '武汉 · 同刻观测详情' })).toBeVisible()
})

test('侧栏切换锚定同一底端，短窗口只滚动中间导航', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 420 })
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  const rail = page.getByRole('complementary', { name: '侧栏' })
  const toggle = rail.locator('.rail-toggle')
  const getCenters = () => rail.locator('.rail-item > :is(svg,img)').evaluateAll(nodes => nodes.map(node => {
    const r = node.getBoundingClientRect()
    return r.x + r.width / 2
  }))
  const anchor = (await toggle.boundingBox())!
  const centers = await getCenters()
  expect(Math.max(...centers) - Math.min(...centers)).toBeLessThan(1)
  expect((await rail.boundingBox())!.y + (await rail.boundingBox())!.height - anchor.y - anchor.height).toBeLessThan(12)
  await toggle.focus()
  await page.keyboard.press('Enter')
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  expect(await getCenters()).toEqual(centers)
  const nav = rail.getByRole('navigation')
  await nav.evaluate(n => { n.scrollTop = n.scrollHeight })
  expect(await nav.evaluate(n => n.scrollTop)).toBeGreaterThan(0)
  await page.locator('.table-footer').scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(0)
  expect((await toggle.boundingBox())!.y).toBe(anchor.y)
  await expect(rail.locator('.side-bottom button').last()).toHaveAccessibleName('收起侧栏')
  await toggle.press('Space')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(await toggle.boundingBox()).toEqual(anchor)
})

test('工具栏和表头共用 URL 排序，翻页、表头反转及重置后同步', async ({ page }) => {
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  const sort = page.locator('.select-order')
  const head = page.getByRole('columnheader', { name: '切换 AQI 排序' })
  const first = page.locator('.sky-table tbody tr').first()
  await page.getByRole('button', { name: '下一页' }).click()
  await selectOption(page, '排序', 'AQI 升序')
  await expect(page).toHaveURL(/order=asc/)
  await expect(page).not.toHaveURL(/page=/)
  await expect(head).toHaveAttribute('aria-sort', 'ascending')
  await expect(first).toContainText('海口')
  await head.getByRole('button').click()
  await expect(sort).toContainText('AQI 降序')
  await expect(first).toContainText('石家庄')
  await head.getByRole('button').click()
  await expect(sort).toContainText('AQI 升序')
  await page.getByRole('button', { name: '重置列表筛选和排序' }).click()
  await expect(sort).toContainText('AQI 降序')
  await expect(head).toHaveAttribute('aria-sort', 'descending')
  await expect(first).toContainText('石家庄')
  const fields = await page.locator('.sky-input, .sky-select .el-select__wrapper').evaluateAll(nodes => nodes.map(n => {
    const r = n.getBoundingClientRect()
    return [r.y, r.height, getComputedStyle(n).fontWeight]
  }))
  expect(new Set(fields.map(field => JSON.stringify(field))).size).toBe(1)
  expect(fields[0][2]).toBe('400')
})

test('城市详情的文字边线与标题对齐，短屏趋势表内滚后关闭并归还焦点', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 420 })
  await page.goto('/')
  await ready(page, '全国空气质量总览')
  const table = page.locator('.sky-table-scroll')
  await table.evaluate(el => { el.scrollLeft = el.scrollWidth })
  const detail = table.getByRole('button', { name: '查看石家庄详情' })
  await detail.click()
  const sheet = page.getByRole('dialog', { name: '石家庄 · 同刻观测详情' })
  const edges = await sheet.evaluate(el => {
    function textEdges(selector: string, side: 'left' | 'right') {
      return Array.from(el.querySelectorAll(selector), n => {
        const range = document.createRange()
        range.selectNodeContents(n)
        return range.getBoundingClientRect()[side]
      })
    }
    return {
      left: textEdges('.el-dialog__title, .detail-reading strong, .detail-basis > span, dt', 'left'),
      right: textEdges('dd', 'right'), filter: getComputedStyle(el.querySelector('.sky-dialog')!).backdropFilter,
    }
  })
  expect(edges.filter).toContain('blur(')
  for (const line of [edges.left, edges.right]) expect(Math.max(...line) - Math.min(...line)).toBeLessThan(1)
  await page.keyboard.press('Escape')
  await expect(detail).toBeFocused()
  const trend = page.getByRole('button', { name: '查看趋势数据表' })
  await trend.click()
  const ledger = page.getByRole('dialog', { name: '样本到报明细' })
  const close = ledger.getByRole('button', { name: /关闭/ })
  const closeBox = await close.boundingBox()
  await ledger.locator('tbody tr').last().scrollIntoViewIfNeeded()
  expect(await ledger.locator('.el-dialog__body').evaluate(n => n.scrollTop)).toBeGreaterThan(0)
  expect(await close.boundingBox()).toEqual(closeBox)
  const header = (await ledger.getByRole('columnheader', { name: '到报城市数' }).boundingBox())!
  const body = (await ledger.locator('.el-dialog__body').boundingBox())!
  expect(header.y).toBeGreaterThanOrEqual(body.y)
  expect(header.y + header.height).toBeLessThanOrEqual(body.y + body.height)
  await close.click()
  await expect(trend).toBeFocused()
})
