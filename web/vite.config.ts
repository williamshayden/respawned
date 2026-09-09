import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/v1': { target: process.env.RESPAWNED_API_URL || 'http://127.0.0.1:8000' },
      '/readyz': { target: process.env.RESPAWNED_API_URL || 'http://127.0.0.1:8000' },
    },
  },
})
