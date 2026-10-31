import { Icon, type IconName } from './Icon'

export function LoadingState({ label = 'Loading accounts…' }: { label?: string }) {
  return <div className="loading-state" role="status" aria-live="polite"><span className="spinner" />{label}</div>
}

export function EmptyState({ title, children, icon = 'accounts' }: {
  title: string; children: React.ReactNode; icon?: IconName
}) {
  return <section className="empty-state"><span className="state-icon"><Icon name={icon} size={28} /></span>
    <h2>{title}</h2><div className="state-copy">{children}</div></section>
}

export function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return <section className="error-state" role="alert"><span className="state-icon"><Icon name="warning" size={25} /></span>
    <div><h2>Something didn’t load</h2><p>{message}</p><button className="button secondary" onClick={retry}><Icon name="refresh" />Try again</button></div></section>
}
