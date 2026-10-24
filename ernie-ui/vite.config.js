import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Backend routes the app calls. In dev Vite proxies them to uvicorn; the production build is served by
// FastAPI itself at /app, so the same relative URLs work in both.
const BACKEND = process.env.ERNIE_BACKEND || 'http://127.0.0.1:8000'
const API_PATHS = ['/agent', '/tools', '/samples', '/sample', '/generate', '/compose', '/midi', '/track', '/health', '/session']

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' ? '/app/' : '/',
  server: {
    port: 5173,
    strictPort: true,     // Electron loads exactly this port; never drift to 5174+
    proxy: Object.fromEntries(API_PATHS.map((p) => [p, BACKEND])),
  },
}))
