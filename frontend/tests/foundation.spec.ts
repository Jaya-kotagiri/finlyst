import { expect, test } from '@playwright/test'
import { localTarget } from '../dev/localAccess'
import { money } from '../src/format'
import { accounts, envelope, mockData, otherId, snapshotId, snapshots } from './fixtures'

test('navigation, deep links, back and unknown pages work without fake metrics', async ({ page }) => {
  await mockData(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Your accounts.' })).toBeVisible()
  for (const title of ['Dashboard', 'Transactions', 'Obligations', 'Assistant']) {
    await page.getByRole('navigation').getByRole('link', { name: title }).click()
    await expect(page.getByRole('heading', { name: `${title}.`, exact: true })).toBeVisible()
    await expect(page.getByText('Coming later', { exact: true })).toBeVisible()
    await expect(page.locator('main')).not.toContainText('₹')
    await expect(page.getByRole('navigation').getByRole('link', { name: title })).toHaveAttribute('aria-current', 'page')
  }
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Assistant.', exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('heading', { name: 'Obligations.', exact: true })).toBeVisible()
  await page.goto('/#/missing')
  await expect(page.getByRole('heading', { name: 'This page isn’t here' })).toBeVisible()
  await page.getByRole('link', { name: 'Go to accounts' }).click()
  await expect(page.getByLabel('Statement import')).toBeVisible()
})

test('explicit import selection displays exact balances, dates and unavailable amounts', async ({ page }) => {
  await mockData(page)
  let calls = 0
  page.on('request', request => { if (request.url().includes('/accounts?')) { calls++; expect(request.method()).toBe('GET') } })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Choose an import to get started' })).toBeVisible()
  expect(calls).toBe(0)
  await page.getByLabel('Statement import').selectOption(snapshotId)
  await expect(page.getByRole('article', { name: 'Kotak Mahindra Bank' })).toContainText('₹12,345.67')
  await expect(page.getByRole('article', { name: 'Kotak Mahindra Bank' })).toContainText('As of 10 Aug 2026')
  await expect(page.getByRole('article', { name: 'State Bank of India' })).toContainText('Not available')
  await expect(page.locator('main')).not.toContainText('₹0.00')
  await expect(page.locator('main')).not.toContainText('0000001234')
  await expect(page.locator('main')).toContainText('KOTAK_0003')
  expect(calls).toBe(1)
  await page.getByRole('button', { name: 'Refresh imports' }).click()
  await expect(page.getByLabel('Statement import')).toHaveValue('')
  await expect(page.getByRole('article')).toHaveCount(0)
})

test('both loading states are accessible and disappear after success', async ({ page }) => {
  let releaseImports!: () => void
  const imports = new Promise<void>(resolve => { releaseImports = resolve })
  let releaseAccounts!: () => void
  const rows = new Promise<void>(resolve => { releaseAccounts = resolve })
  await page.route('**/api/v1/snapshots', async route => { await imports; await route.fulfill({ json: envelope(snapshots) }) })
  await page.route('**/api/v1/accounts?*', async route => { await rows; await route.fulfill({ json: envelope(accounts, snapshotId) }) })
  await page.goto('/')
  await expect(page.getByRole('status')).toHaveText('Loading your imports…')
  releaseImports()
  await page.getByLabel('Statement import').selectOption(snapshotId)
  await expect(page.getByRole('status')).toHaveText('Loading accounts…')
  releaseAccounts()
  await expect(page.getByRole('article')).toHaveCount(2)
  await expect(page.getByRole('status')).toHaveCount(0)
})

test('empty import list and empty selected snapshot have distinct states', async ({ page }) => {
  await page.route('**/api/v1/snapshots', route => route.fulfill({ json: envelope([]) }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'No imports yet' })).toBeVisible()
  await mockData(page)
  await page.route('**/api/v1/accounts?*', route => route.fulfill({ json: envelope([], snapshotId) }))
  await page.reload()
  await page.getByLabel('Statement import').selectOption(snapshotId)
  await expect(page.getByRole('heading', { name: 'No accounts in this import' })).toBeVisible()
  await expect(page.locator('main')).not.toContainText('₹')
})

for (const status of [403, 404, 409, 422, 500, 503]) {
  test(`HTTP ${status} displays a safe error and account retry works`, async ({ page }) => {
    await mockData(page)
    let failed = true
    await page.route('**/api/v1/accounts?*', route => route.fulfill(failed
      ? { status, json: { error: { message: 'private-server-details' } } }
      : { json: envelope(accounts, snapshotId) }))
    await page.goto('/')
    await page.getByLabel('Statement import').selectOption(snapshotId)
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page.locator('main')).not.toContainText('private-server-details')
    await expect(page.getByRole('article')).toHaveCount(0)
    failed = false
    await page.getByRole('button', { name: 'Try again' }).click()
    await expect(page.getByRole('article')).toHaveCount(2)
  })
}

test('offline request and invalid response fail visibly without coerced balances', async ({ page }) => {
  await page.route('**/api/v1/snapshots', route => route.abort())
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('Couldn’t reach Finlyst')
  await mockData(page)
  await page.getByRole('button', { name: 'Try again' }).click()
  await page.route('**/api/v1/accounts?*', route => route.fulfill({ json: envelope([{ ...accounts[0], balance_after: 123.45 }], snapshotId) }))
  await page.getByLabel('Statement import').selectOption(snapshotId)
  await expect(page.getByRole('alert')).toContainText('unexpected data format')
  await expect(page.getByRole('article')).toHaveCount(0)
})

test('switching imports cancels stale results and keeps the selected snapshot', async ({ page }) => {
  await mockData(page)
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/accounts?*', async route => {
    const id = new URL(route.request().url()).searchParams.get('snapshot_id')
    if (id === snapshotId) await pending
    await route.fulfill({ json: envelope(id === snapshotId ? accounts : [], id) })
  })
  await page.goto('/')
  await page.getByLabel('Statement import').selectOption(snapshotId)
  await expect(page.getByRole('status')).toHaveText('Loading accounts…')
  await page.getByLabel('Statement import').selectOption(otherId)
  await expect(page.getByRole('heading', { name: 'No accounts in this import' })).toBeVisible()
  release()
  await expect(page.getByLabel('Statement import')).toHaveValue(otherId)
  await expect(page.getByRole('article')).toHaveCount(0)
})

test('light/dark preference persists and keyboard navigation is accessible', async ({ page }) => {
  await mockData(page)
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' })
  await page.goto('/')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('main')).toBeFocused()
  await page.getByRole('button', { name: 'Switch to dark theme' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual(['finlyst-theme'])
  await page.getByRole('button', { name: 'Switch to light theme' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})

for (const width of [360, 768, 1440]) {
  test(`responsive layout at ${width}px has no overflow and usable navigation`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await mockData(page)
    await page.goto('/')
    await page.getByLabel('Statement import').selectOption(snapshotId)
    await expect(page.getByRole('article')).toHaveCount(2)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    if (process.env.FINLYST_REVIEW_CAPTURE === '1') {
      await page.screenshot({ path: `../tmp/phase4b/accounts-${width}-light.png`, fullPage: true, animations: 'disabled' })
      await page.getByRole('button', { name: 'Switch to dark theme' }).click()
      await page.screenshot({ path: `../tmp/phase4b/accounts-${width}-dark.png`, fullPage: true, animations: 'disabled' })
    }
    if (width < 680) await page.getByRole('button', { name: 'Open navigation' }).click()
    await page.getByRole('navigation').getByRole('link', { name: 'Dashboard' }).click()
    await expect(page.getByRole('heading', { name: 'Dashboard.', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    if (width < 680) await expect(page.getByRole('button', { name: 'Open navigation' })).toHaveAttribute('aria-expanded', 'false')
  })
}

test('proxy rejects hostile origins, hosts, forwarded headers and write methods', async ({ request }) => {
  const cases: Record<string, string>[] = [{ host: 'evil.example' }, { origin: 'https://evil.example' }, { 'x-forwarded-for': '127.0.0.1' }, { 'sec-fetch-site': 'cross-site' }]
  for (const headers of cases) {
    const response = await request.get('/api/v1/snapshots', { headers })
    expect(response.status()).toBe(403)
    expect(response.headers()['access-control-allow-origin']).toBeUndefined()
  }
  expect((await request.post('/api/v1/snapshots')).status()).toBe(405)
  const projectRoot = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1')
  expect((await request.get('/@fs/' + projectRoot + 'PROJECT_STATE.md')).status()).toBe(403)
})

test('money formatting is exact, and backend targets cannot be public or credentialed', () => {
  expect(money('9007199254740993.01')).toBe('₹9,00,71,99,25,47,40,993.01')
  expect(money('-1234.50')).toBe('−₹1,234.50')
  expect(money(null)).toBe('Not available')
  expect(localTarget('http://127.0.0.1:8000')).toBe('http://127.0.0.1:8000')
  for (const target of ['https://public.example', 'http://127.0.0.1.evil.example', 'http://user:password@localhost', 'http://localhost/path']) {
    expect(() => localTarget(target)).toThrow()
  }
})
