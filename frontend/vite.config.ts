import { isAbsolute } from 'node:path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const backend = process.env.AQ_DEV_BACKEND
const target = backend && /^http:\/\/127\.0\.0\.1:\d+$/.test(backend) ? backend : undefined
const dist = process.env.AQ_SPA_DIST
if (dist && !isAbsolute(dist)) throw new Error('AQ_SPA_DIST must be an absolute build directory')

export default defineConfig(({ command, isPreview }) => ({
  plugins: [vue()],
  base: command === 'build' || isPreview ? '/static/app/' : '/',
  build: { outDir: dist || 'dist' },
  cacheDir: process.env.AQ_VITE_CACHE_DIR || 'node_modules/.vite',
  server: {
    host: '127.0.0.1',
    strictPort: true,
    proxy: target
      ? {
          '/api/': { target, changeOrigin: false },
          '/admin/': { target, changeOrigin: false },
          '/health/': { target, changeOrigin: false },
        }
      : undefined,
  },
  preview: { host: '127.0.0.1', strictPort: true },
}))
