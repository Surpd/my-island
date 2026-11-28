import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('.', import.meta.url))
export default defineConfig({
  root,
  base: './',
  plugins: [react()],
  server: { host: '127.0.0.1', port: 4176, strictPort: true, fs: { allow: [root, fileURLToPath(new URL('../../node_modules/', import.meta.url))] } },
  publicDir: false,
  envDir: root,
})
