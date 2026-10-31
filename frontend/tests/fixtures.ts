import type { Page } from '@playwright/test'
import type { Account, Snapshot } from '../src/api'

// Entirely synthetic. Never used by the application or the live verification.
export const snapshotId = '11111111-1111-5111-8111-111111111111'
export const otherId = '22222222-2222-5222-8222-222222222222'
export const snapshots: Snapshot[] = [
  { snapshot_id: snapshotId, imported_at: '2026-08-10T10:00:00Z', counts: { transaction: 4 } },
  { snapshot_id: otherId, imported_at: '2026-09-10T10:00:00Z', counts: { transaction: 2 } },
]
export const accounts: Account[] = [{
  record_id: snapshotId, revision_id: snapshotId, legacy_id: 'ACCOUNT_SYNTHETIC', source_id: snapshotId,
  data: { bank: 'KOTAK', account: '0000001234' }, transaction_count: 3, balance_after: '12345.67',
  balance_date: '2026-08-10', balance_transaction_id: 'KOTAK_0003', provenance: [],
}, {
  record_id: otherId, revision_id: otherId, legacy_id: 'ACCOUNT_SYNTHETIC_2', source_id: otherId,
  data: { bank: 'SBI', account: '0000005678' }, transaction_count: 1, balance_after: null,
  balance_date: '2026-08-10', balance_transaction_id: 'SBI_0001', provenance: [],
}]
export const envelope = (data: unknown, snapshot_id: string | null = null) => ({ api_version: '4A.1', snapshot_id, data })
export async function mockData(page: Page) {
  await page.route('**/api/v1/snapshots', route => route.fulfill({ json: envelope(snapshots) }))
  await page.route('**/api/v1/accounts?*', route => {
    const selected = new URL(route.request().url()).searchParams.get('snapshot_id')
    return route.fulfill({ json: envelope(accounts, selected) })
  })
}
