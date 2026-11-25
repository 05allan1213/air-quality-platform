import { existsSync } from 'node:fs'
import { defineConfig } from '@playwright/test'

const executable = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || (existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined)
const baseURL = process.env.S08_BASE_URL

if (!baseURL) throw new Error('S08_BASE_URL must point to the isolated real-backend frontend instance.')

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 12_000 },
  use: {
    baseURL,
    browserName: 'chromium',
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    reducedMotion: 'reduce',
    launchOptions: { executablePath: executable },
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
  reporter: 'list',
})
