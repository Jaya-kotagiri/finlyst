import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

const hosts = new Set(['localhost', '127.0.0.1', '[::1]'])

export function localTarget(value: string): string {
  const url = new URL(value)
  if (url.protocol !== 'http:' || !hosts.has(url.hostname) || url.username || url.password
    || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('FINLYST_API_TARGET must be a plain loopback HTTP origin, e.g. http://127.0.0.1:8000')
  }
  return url.origin
}

// Guard the browser-facing proxy before changing Host for the existing API.
function guard(req: IncomingMessage, res: ServerResponse, next: () => void) {
  const peer = req.socket.remoteAddress
  let allowed = peer === '127.0.0.1' || peer === '::1' || peer === '::ffff:127.0.0.1'
  try {
    const url = new URL(`http://${req.headers.host ?? ''}`)
    allowed &&= hosts.has(url.hostname) && !url.username && !url.password && url.pathname === '/'
      && !url.search && !url.hash
    allowed &&= !Object.keys(req.headers).some(key => key === 'forwarded' || key === 'x-real-ip' || key.startsWith('x-forwarded-'))
    allowed &&= req.headers['sec-fetch-site'] !== 'cross-site'
    if (req.headers.origin) allowed &&= req.headers.origin === url.origin
  } catch { allowed = false }
  res.setHeader('Cache-Control', 'no-store')
  res.setHeader('Referrer-Policy', 'no-referrer')
  res.setHeader('X-Content-Type-Options', 'nosniff')
  if (!allowed) {
    res.writeHead(403, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: { code: 'local_access_only', message: 'Local same-origin access only.' } }))
  } else if (req.url?.startsWith('/api/') && req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: { code: 'read_only', message: 'Only read operations are available.' } }))
  } else next()
}

export function localAccess(): Plugin {
  return {
    name: 'finlyst-local-access',
    configureServer(server) { server.middlewares.use(guard) },
    configurePreviewServer(server) { server.middlewares.use(guard) },
  }
}
