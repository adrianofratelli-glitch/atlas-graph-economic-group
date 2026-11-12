import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Padrão: portas do PORTS.md. Uma cópia de teste em porta livre usa
// `GRAPH_API_TARGET=http://127.0.0.1:<porta> npx vite preview --port <outra>`,
// sem disputar a porta do Portal.
const api = process.env.GRAPH_API_TARGET || 'http://127.0.0.1:8350'
const proxy = { '/api': api, '/health': api }

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5350, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 5350, strictPort: true, proxy },
})
