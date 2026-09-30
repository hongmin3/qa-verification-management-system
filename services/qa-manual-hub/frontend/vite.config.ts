import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies /api to a locally running backend so the SPA behaves
// exactly as it does behind nginx in production (same origin, cookies included).
// Override the target with VITE_API_TARGET in .env.local when the backend runs
// somewhere other than the default port.
//
// VITE_BASE_PATH decides where the built SPA is mounted:
//   unset (default)  -> '/'            standalone deployment, its own host/site
//   '/manual-hub/'   -> subpath        mounted under the QA platform's nginx
// Everything downstream reads `import.meta.env.BASE_URL` instead of hardcoding
// '/', so router basename, asset URLs and the API prefix all follow this one
// value. See deploy/nginx/qa-platform.conf in the repository root.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const base = env.VITE_BASE_PATH || '/'
  return {
    plugins: [react()],
    base,
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.VITE_API_TARGET ?? 'http://127.0.0.1:24358',
          changeOrigin: false,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      chunkSizeWarningLimit: 900,
    },
  }
})
