import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The app calls the API with relative paths (/builds, /health). In development
// Vite forwards them to the FastAPI server; in Docker, FastAPI serves this app
// itself, so the same paths work without CORS.
const api = process.env.API_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/builds': api,
      '/health': api,
    },
  },
})
