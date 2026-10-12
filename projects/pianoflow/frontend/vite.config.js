import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The dev server proxies /api to the Tornado backend so the browser only ever
// talks to one origin (no CORS, and SSE passes through untouched).
const BACKEND = process.env.PI_ANOFLOW_BACKEND || 'http://127.0.0.1:8890'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // Bind IPv4 explicitly. Vite's default only listens on [::1], which makes
    // http://127.0.0.1:5173 fail with "cannot connect" and looks like the dev
    // server is not running at all.
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
})
