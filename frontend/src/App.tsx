import { useEffect, useState } from 'react'
import { Layout, pages } from './components/Layout'
import { EmptyState } from './components/States'
import { Accounts } from './pages/Accounts'
import { Placeholder } from './pages/Placeholder'

function currentPage() {
  const path = window.location.hash.replace(/^#\/?/, '') || 'accounts'
  return pages.find(page => page.toLowerCase() === path) ?? null
}

export function App() {
  const [page, setPage] = useState(currentPage)
  useEffect(() => {
    const changed = () => { setPage(currentPage()); window.scrollTo(0, 0) }
    window.addEventListener('hashchange', changed)
    return () => window.removeEventListener('hashchange', changed)
  }, [])
  return <Layout page={page}>{page === 'Accounts' ? <Accounts /> : page ? <Placeholder page={page} />
    : <EmptyState title="This page isn’t here"><p>Head back to your accounts to continue.</p><a className="button primary" href="#/accounts">Go to accounts</a></EmptyState>}</Layout>
}
