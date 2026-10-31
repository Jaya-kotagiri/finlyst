import { useEffect, useState } from 'react'
import { Icon, type IconName } from './Icon'

export const pages = ['Dashboard', 'Transactions', 'Accounts', 'Obligations', 'Assistant'] as const
export type Page = typeof pages[number]

function initialTheme(): 'light' | 'dark' {
  try {
    const saved = localStorage.getItem('finlyst-theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* Storage may be unavailable; use the device preference. */ }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function Layout({ page, children }: { page: Page | null; children: React.ReactNode }) {
  const [theme, setTheme] = useState(initialTheme)
  const [menuOpen, setMenuOpen] = useState(false)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('finlyst-theme', theme) } catch { /* Theme still works without storage. */ }
  }, [theme])
  useEffect(() => { setMenuOpen(false); document.title = `${page ?? 'Page not found'} · Finlyst` }, [page])
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuOpen(false) }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [])
  return <div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus() }}>Skip to content</a>
    <aside className="sidebar">
      <div className="brand-row"><a className="brand" href="#/accounts" aria-label="Finlyst accounts"><img src="/favicon.svg" alt="" />finlyst<span className="brand-dot">.</span></a>
        <button className="icon-button mobile-menu" aria-label={menuOpen ? 'Close navigation' : 'Open navigation'} aria-expanded={menuOpen}
          aria-controls="main-nav" onClick={() => setMenuOpen(!menuOpen)}><Icon name={menuOpen ? 'close' : 'menu'} /></button></div>
      <div className="nav-section"><span className="nav-caption">WORKSPACE</span>
        <nav id="main-nav" aria-label="Main navigation" className={menuOpen ? 'navigation open' : 'navigation'}>
          {pages.map(item => <a key={item} href={`#/${item.toLowerCase()}`} aria-current={page === item ? 'page' : undefined}
            onClick={() => setMenuOpen(false)}><Icon name={item.toLowerCase() as IconName} /><span>{item}</span>{item === 'Assistant' && <span className="soon">Soon</span>}</a>)}
        </nav></div>
      <div className="sidebar-bottom"><div className="local-note"><Icon name="shield" /><div><strong>Your money. Your space.</strong><p>A private, local workspace.</p></div></div>
        <div className="workspace-label"><span className="workspace-avatar">F</span><div><strong>Personal workspace</strong><span>Read-only access</span></div></div></div>
    </aside>
    <div className="workspace"><header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>{page ?? 'Not found'}</strong></div>
      <div className="topbar-actions"><span className="local-badge"><span />Local workspace</span>
        <button className="icon-button theme-toggle" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
          aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}><Icon name={theme === 'light' ? 'moon' : 'sun'} /></button></div></header>
      <main id="main-content" tabIndex={-1}>{children}</main>
      <footer className="footer"><span>Finlyst <span className="footer-dot">·</span> A little clarity, every day.</span><span>Read-only workspace</span></footer>
    </div>
  </div>
}
