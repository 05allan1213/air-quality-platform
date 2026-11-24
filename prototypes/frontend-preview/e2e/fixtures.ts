import { test as base, expect, type Page } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'

export const test = base.extend<{ runtimeGuard: void }>({
  runtimeGuard: [async ({ page, baseURL }, use) => {
    const errors: string[] = []
    const origin = new URL(baseURL!).origin
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })
    page.on('requestfailed', req => { if (req.failure()?.errorText !== 'net::ERR_ABORTED') errors.push(`${req.method()} ${req.url()} ${req.failure()?.errorText}`) })
    page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`) })
    await page.route(/^https?:/, async route => {
      const url = new URL(route.request().url())
      if (url.origin !== origin || url.pathname.startsWith('/api/')) {
        errors.push(`意外网络请求 ${url.origin}${url.pathname}`)
        return route.abort()
      }
      return route.continue()
    })
    await use()
    expect(errors, '浏览器异常、失败的资源和越界请求').toEqual([])
  }, { auto: true }],
})
export { expect }
export async function ready(page: Page, name: string) {
  await expect(page.getByRole('heading', { level:1, name })).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
}
export async function noOverflow(page: Page) {
  const sizes = await page.evaluate(() => ({ viewport: window.innerWidth, page: document.documentElement.scrollWidth }))
  expect(sizes.page).toBeLessThanOrEqual(sizes.viewport + 1)
}
export async function shot(page: Page, file: string, fullPage = false) {
  const dir = path.resolve('evidence')
  await mkdir(dir, { recursive:true })
  await page.evaluate(async () => { await document.fonts.ready; await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))) })
  await page.screenshot({path: path.join(dir, `${file}.png`), fullPage, animations:'disabled', caret:'hide'})
}
export async function cityState(page: Page, state: string) {
  await page.getByRole('button',{ name:'演示设置', exact:true }).click()
  await page.getByLabel('城市样本状态').selectOption(state)
  await page.getByRole('button',{ name:'完成设置' }).click()
}
