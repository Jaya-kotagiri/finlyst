import { expect, test } from '@playwright/test'
import type { Account, Envelope, Snapshot } from '../src/api'
import { money } from '../src/format'

// Opt-in only: backend and validated imports must already exist. No writes or private fixtures.
test('production frontend displays the selected PostgreSQL account view exactly', async ({ page, request }) => {
  test.setTimeout(60000)
  const response = await request.get('/api/v1/snapshots', { headers: { origin: 'http://127.0.0.1:4175' } })
  expect(response.status()).toBe(200)
  const listing: Envelope<Snapshot[]> = await response.json()
  const id = process.env.FINLYST_TEST_SNAPSHOT
  expect(id, 'Explicit FINLYST_TEST_SNAPSHOT is required for live verification').toBeTruthy()
  expect(listing.data.some(item => item.snapshot_id === id)).toBe(true)
  const expected = await request.get(`/api/v1/accounts?snapshot_id=${id}`)
  expect(expected.status()).toBe(200)
  const rows: Envelope<Account[]> = await expected.json()
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await page.getByLabel('Statement import').selectOption(id!)
  await expect(page.getByRole('article')).toHaveCount(rows.data.length)
  for (const account of rows.data) {
    const card = page.getByRole('article').filter({ hasText: `Account ending ${account.data.account.slice(-4)}` })
    await expect(card).toContainText(money(account.balance_after))
    await expect(card).toContainText(account.balance_transaction_id)
  }
  expect(errors).toEqual([])
})
