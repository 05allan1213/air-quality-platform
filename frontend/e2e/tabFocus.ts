import { expect, type Page } from '@playwright/test'

export async function foregroundPair(page: Page) {
  const other = await page.context().newPage()
  await other.goto('/app/about')
  // Playwright turns per-page focus emulation on. Exercise Chromium's own
  // tab focus/visibility events instead of dispatching DOM events from the test.
  for (const tab of [other, page]) {
    const session = await page.context().newCDPSession(tab)
    await session.send('Emulation.setFocusEmulationEnabled', { enabled: false })
    await session.detach()
  }
  await page.bringToFront()
  await expect.poll(() => page.evaluate(() => document.hasFocus())).toBe(true)
  return other
}

async function countTrustedAwaySignals(page: Page) {
  await page.evaluate(() => {
    let count = 0
    window.addEventListener('blur', event => { if (event.isTrusted) count++ })
    document.addEventListener('visibilitychange', event => {
      if (event.isTrusted && document.visibilityState === 'hidden') count++
    })
    Object.defineProperty(window, 'resumeTestAwayCount', { get: () => count, configurable: true })
  })
}

export async function switchAway(page: Page, other: Page) {
  await countTrustedAwaySignals(page)
  await other.bringToFront()
  await expect.poll(() => page.evaluate(() => Number(Reflect.get(window, 'resumeTestAwayCount')))).toBeGreaterThan(0)
}

export async function switchBack(page: Page) {
  await page.evaluate(() => {
    let returned = false
    const observe = (event: Event) => {
      if (!event.isTrusted || document.visibilityState !== 'visible') return
      returned = true
      window.removeEventListener('focus', observe)
      document.removeEventListener('visibilitychange', observe)
    }
    window.addEventListener('focus', observe)
    document.addEventListener('visibilitychange', observe)
    Object.defineProperty(window, 'resumeTestReturned', { get: () => returned, configurable: true })
  })
  await page.bringToFront()
  await expect.poll(() => page.evaluate(() => Reflect.get(window, 'resumeTestReturned'))).toBe(true)
}
