import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'

process.env.PLAYWRIGHT_BROWSERS_PATH ??= fileURLToPath(new URL('../.local/playwright', import.meta.url))
export default defineConfig({
  testDir: './tests', testMatch: '**/*.spec.ts', testIgnore: '**/*.live.spec.ts',
  fullyParallel: false, workers: 1, retries: 0, reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:5175', browserName: 'chromium', trace: 'off', screenshot: 'off' },
  webServer: { command: 'node node_modules/vite/bin/vite.js --port 5175', url: 'http://127.0.0.1:5175', reuseExistingServer: false },
})
