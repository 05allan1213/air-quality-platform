import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'

const browser = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || (existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined)
export default defineConfig({
  testDir: './e2e', workers: 2, retries: 0, timeout: 30000,
  use: {
    baseURL: 'http://127.0.0.1:4175', viewport: { width: 1440, height: 900 },
    locale: 'zh-CN', timezoneId: 'Asia/Shanghai', reducedMotion: 'reduce',
    launchOptions: { executablePath: browser }, screenshot: 'only-on-failure',
  },
  webServer: { command: 'npm run preview -- --port 4175', url: 'http://127.0.0.1:4175', reuseExistingServer: false },
})
