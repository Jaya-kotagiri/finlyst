import type { Page } from '../components/Layout'
import { Icon, type IconName } from '../components/Icon'

const copy: Record<Exclude<Page, 'Accounts'>, { title: string; description: string; preview: string }> = {
  Dashboard: { title: 'A little clarity. A bigger picture.', description: 'Your personal finance workspace, taking shape.', preview: 'Your overview will bring your financial picture together. For now, explore the accounts in your saved imports.' },
  Transactions: { title: 'Every movement, in context.', description: 'A home for the story behind your transactions.', preview: 'Transaction browsing is coming in a later phase. Your bank records and their original meanings remain safely in Finlyst.' },
  Obligations: { title: 'Know where things stand.', description: 'A clearer view of money owed and money expected.', preview: 'Obligation and settlement views are coming in a later phase. Unknown amounts and unassessed settlements will stay clearly distinguished.' },
  Assistant: { title: 'Space for better questions.', description: 'Your future financial assistant starts here.', preview: 'The assistant isn’t available yet. No conversation or AI request is started from this page.' },
}

export function Placeholder({ page }: { page: Exclude<Page, 'Accounts'> }) {
  const content = copy[page]
  return <><div className="page-heading"><div><p className="eyebrow">YOUR FINLYST WORKSPACE</p><h1>{page}<span>.</span></h1><p className="page-description">{content.description}</p></div><span className="outline-badge">Coming later</span></div>
    <section className="placeholder-hero"><div className="placeholder-copy"><span className="hero-icon"><Icon name={page.toLowerCase() as IconName} size={32} /></span>
      <h2>{content.title}</h2><p>{content.preview}</p><a href="#/accounts" className="button primary">Explore your accounts<Icon name="arrow" size={17} /></a></div>
      <div className="abstract-art" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="art-tile"><Icon name="accounts" size={60} /></div><span className="art-dot dot-one" /><span className="art-dot dot-two" /></div></section>
    <div className="foundation-notes"><div><Icon name="shield" /><h3>Grounded in your records</h3><p>Account balances come from your imported bank statements.</p></div>
      <div><Icon name="info" /><h3>Clarity without assumptions</h3><p>Missing information stays visible. No invented financial figures.</p></div>
      <div><Icon name="accounts" /><h3>Your space to explore</h3><p>A local, read-only workspace. Your financial records stay unchanged.</p></div></div></>
}
