import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'

process.env.PLAYWRIGHT_BROWSERS_PATH ??= fileURLToPath(new URL('../.local/playwright', import.meta.url))
export default defineConfig({
  testDir: './tests', testMatch: '**/*.live.spec.ts', workers: 1, retries: 0, reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:4175', browserName: 'chromium', trace: 'off', screenshot: 'off' },
  webServer: { command: 'node node_modules/vite/bin/vite.js preview --port 4175', url: 'http://127.0.0.1:4175', reuseExistingServer: false },
})
