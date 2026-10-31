import { defineConfig, loadEnv, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { localAccess, localTarget } from './dev/localAccess.ts'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = localTarget(process.env.FINLYST_API_TARGET ?? env.FINLYST_API_TARGET ?? 'http://127.0.0.1:8000')
  const proxy: Record<string, ProxyOptions> = {
    '/api/v1': {
      target, changeOrigin: true, xfwd: false,
      configure(proxy) {
        // The preceding guard has verified Origin against the frontend origin.
        proxy.on('proxyReq', request => request.removeHeader('origin'))
      },
    },
  }
  return {
    plugins: [localAccess(), react()],
    server: { host: '127.0.0.1', port: 5173, strictPort: true, cors: false, proxy,
      fs: { strict: true, allow: [fileURLToPath(new URL('.', import.meta.url))] } },
    preview: { host: '127.0.0.1', port: 4173, strictPort: true, cors: false, proxy },
  }
})
