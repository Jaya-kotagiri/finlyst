/** Read-only HTTP client. Money stays as exact strings; evidence stays in the response. */
export interface Envelope<T> { api_version: '4A.1'; snapshot_id: string | null; data: T }
export interface Snapshot { snapshot_id: string; imported_at: string; counts: Record<string, number> }
export interface Account {
  record_id: string
  revision_id: string
  legacy_id: string
  source_id: string
  data: { bank: 'KOTAK' | 'SBI'; account: string }
  transaction_count: number
  balance_after: string | null
  balance_date: string
  balance_transaction_id: string
  provenance: Array<Record<string, unknown>>
}

export class ApiError extends Error {
  constructor(message: string, public status?: number) { super(message); this.name = 'ApiError' }
}

function baseUrl() {
  const url = new URL(import.meta.env.VITE_API_URL || '/api/v1', window.location.origin)
  if (url.origin !== window.location.origin || url.username || url.password || url.search || url.hash) {
    throw new ApiError('The API URL must use this workspace’s origin. Check the local frontend configuration.')
  }
  return url.href.replace(/\/$/, '')
}

const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
function snapshot(value: unknown): value is Snapshot {
  return object(value) && text(value.snapshot_id) && text(value.imported_at)
    && !Number.isNaN(Date.parse(value.imported_at)) && object(value.counts) && Object.values(value.counts).every(count)
}
function account(value: unknown): value is Account {
  return object(value) && text(value.record_id) && text(value.revision_id) && text(value.legacy_id) && text(value.source_id)
    && object(value.data) && ['KOTAK', 'SBI'].includes(String(value.data.bank)) && text(value.data.account)
    && count(value.transaction_count) && (value.balance_after === null
      || typeof value.balance_after === 'string' && /^-?\d+\.\d{2}$/.test(value.balance_after))
    && typeof value.balance_date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value.balance_date)
    && text(value.balance_transaction_id) && Array.isArray(value.provenance) && value.provenance.every(object)
}

async function read<T>(path: string, guard: (value: unknown) => value is T, snapshotId: string | null,
  signal: AbortSignal): Promise<Envelope<T[]>> {
  let response: Response
  try {
    response = await fetch(baseUrl() + path, { signal, cache: 'no-store', credentials: 'omit',
      headers: { Accept: 'application/json' } })
  } catch (error) {
    if (signal.aborted || error instanceof ApiError) throw error
    throw new ApiError('Couldn’t reach Finlyst. Check that the local backend is running, then try again.')
  }
  if (!response.ok) {
    const messages: Record<number, string> = {
      403: 'Access was blocked. Open this workspace on localhost and check the API proxy configuration.',
      404: 'This import is no longer available. Refresh the import list and select another.',
      409: 'This import needs an integrity check. No account data has been displayed.',
      422: 'This selection could not be read. Refresh the import list and try again.',
      503: 'The database is unavailable. Check the local backend and PostgreSQL, then try again.',
    }
    throw new ApiError(messages[response.status] ?? 'Couldn’t load your accounts. Check the local backend and try again.', response.status)
  }
  let value: unknown
  try { value = await response.json() } catch { throw new ApiError('Finlyst returned an unreadable response. No balances have been displayed.') }
  if (!object(value) || value.api_version !== '4A.1' || value.snapshot_id !== snapshotId
    || !Array.isArray(value.data) || !value.data.every(guard)) {
    throw new ApiError('Finlyst returned an unexpected data format. No balances have been displayed.')
  }
  return value as unknown as Envelope<T[]>
}

export const api = {
  snapshots: (signal: AbortSignal) => read('/snapshots', snapshot, null, signal),
  accounts: (snapshotId: string, signal: AbortSignal) => read(`/accounts?snapshot_id=${encodeURIComponent(snapshotId)}`, account, snapshotId, signal),
}
