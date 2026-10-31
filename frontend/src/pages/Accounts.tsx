import { useEffect, useRef, useState } from 'react'
import { api, type Account, type Snapshot } from '../api'
import { date, importDate, money } from '../format'
import { Icon } from '../components/Icon'
import { EmptyState, ErrorState, LoadingState } from '../components/States'

type Load<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: T }

function AccountCard({ account }: { account: Account }) {
  const name = account.data.bank === 'KOTAK' ? 'Kotak Mahindra Bank' : 'State Bank of India'
  return <article className="account-card" aria-label={name}>
    <div className="account-heading"><div className={`bank-mark ${account.data.bank.toLowerCase()}`} aria-hidden="true">{account.data.bank === 'KOTAK' ? 'K' : 'S'}</div>
      <div><h2>{name}</h2><p>Account ending {account.data.account.slice(-4)}</p></div><span className="bank-label">{account.data.bank}</span></div>
    <div className="account-balance"><span>Last recorded balance</span><p className={account.balance_after === null ? 'unknown-amount' : ''}>{money(account.balance_after)}</p>
      <div className="balance-date">{account.balance_after === null ? 'Balance not supplied by the source' : `As of ${date(account.balance_date)}`}</div></div>
    <div className="account-meta"><div><span>Transactions in import</span><strong>{account.transaction_count.toLocaleString('en-IN')}</strong></div>
      <div><span>Source transaction</span><strong className="source-id">{account.balance_transaction_id}</strong></div></div>
    <div className="account-foot"><Icon name="shield" size={15} />From your bank statement<span>Read only</span></div>
  </article>
}

export function Accounts() {
  const [snapshots, setSnapshots] = useState<Load<Snapshot[]>>({ status: 'loading' })
  const [selected, setSelected] = useState('')
  const [accounts, setAccounts] = useState<Load<Account[]>>({ status: 'loading' })
  const [reload, setReload] = useState(0)
  const [retry, setRetry] = useState(0)
  const select = useRef<HTMLSelectElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    setSnapshots({ status: 'loading' })
    api.snapshots(controller.signal).then(result => {
      if (!controller.signal.aborted) setSnapshots({ status: 'ready', data: result.data })
    }).catch((error: Error) => { if (!controller.signal.aborted) setSnapshots({ status: 'error', message: error.message }) })
    return () => controller.abort()
  }, [reload])
  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    setAccounts({ status: 'loading' })
    api.accounts(selected, controller.signal).then(result => {
      if (!controller.signal.aborted) setAccounts({ status: 'ready', data: result.data })
    }).catch((error: Error) => { if (!controller.signal.aborted) setAccounts({ status: 'error', message: error.message }) })
    return () => controller.abort()
  }, [selected, retry])
  function refresh() { setSelected(''); setAccounts({ status: 'loading' }); setReload(value => value + 1) }
  function choose(value: string) { setAccounts({ status: 'loading' }); setSelected(value) }
  const active = snapshots.status === 'ready' ? snapshots.data.find(item => item.snapshot_id === selected) : undefined
  return <>
    <div className="page-heading"><div><p className="eyebrow">YOUR MONEY, IN VIEW</p><h1>Your accounts<span>.</span></h1><p className="page-description">A clear view of the accounts behind your everyday finances.</p></div>
      <button className="button secondary" onClick={refresh} disabled={snapshots.status === 'loading'}><Icon name="refresh" size={17} />Refresh imports</button></div>
    <section className="intro-panel"><span className="intro-icon"><Icon name="accounts" size={29} /></span><div><h2>Your accounts, together.</h2>
      <p>View balances directly from your imported bank statements. Nothing here changes your money.</p></div><span className="outline-badge"><Icon name="shield" size={14} />Read only</span></section>
    <section className="accounts-section" aria-labelledby="accounts-heading"><div className="section-heading"><div><h2 id="accounts-heading">Bank accounts {selected && accounts.status === 'ready' && <span className="count-badge">{accounts.data.length}</span>}</h2>
      <p>Choose an import to see its recorded account balances.</p></div></div>
      {snapshots.status === 'loading' ? <LoadingState label="Loading your imports…" />
        : snapshots.status === 'error' ? <ErrorState message={snapshots.message} retry={refresh} />
        : snapshots.data.length === 0 ? <EmptyState title="No imports yet"><p>Your accounts will appear after a validated financial snapshot has been imported into Finlyst.</p><button className="button secondary" onClick={refresh}>Refresh imports</button></EmptyState>
        : <><div className="import-toolbar"><div className="import-field"><label htmlFor="snapshot">Statement import</label><select id="snapshot" ref={select} value={selected} onChange={event => choose(event.target.value)}>
            <option value="">Select an import</option>{snapshots.data.map(item => <option value={item.snapshot_id} key={item.snapshot_id}>
              {importDate(item.imported_at)} · {item.snapshot_id.slice(0, 8)}</option>)}</select></div>
            <span className="import-hint">{active ? 'Viewing a saved import' : 'Your data stays in this workspace'}</span></div>
          {!selected ? <EmptyState title="Choose an import to get started"><p>Each import is a saved view of your accounts. Select one above to explore its balances.</p>
            <button className="button primary" onClick={() => select.current?.focus()}>Choose an import<Icon name="arrow" size={16} /></button></EmptyState>
            : accounts.status === 'loading' ? <LoadingState />
            : accounts.status === 'error' ? <ErrorState message={accounts.message} retry={() => { setAccounts({ status: 'loading' }); setRetry(value => value + 1) }} />
            : accounts.data.length === 0 ? <EmptyState title="No accounts in this import"><p>Choose another import to view its accounts. No balance is assumed for an empty import.</p></EmptyState>
            : <div className="account-grid">{accounts.data.map(account => <AccountCard key={account.record_id} account={account} />)}</div>}
        </>}
    </section>
    <aside className="information-note"><Icon name="info" size={18} /><p><strong>A recorded balance, not a live balance.</strong> These amounts reflect the last bank transaction in the selected import. Missing balances stay unavailable. They are never treated as zero.</p></aside>
  </>
}
