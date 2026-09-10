import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
  },
  build: {
    // Split rarely-changing vendor code (react/react-dom/leaflet/uuid)
    // into its own chunk, separate from app code that changes on every
    // deploy — the browser can then keep caching the vendor chunk
    // across releases instead of re-downloading it every time. Paired
    // with the React.lazy()-based route splitting in App.jsx (each
    // admin/manager page becomes its own on-demand chunk), this is
    // what actually addresses the ">500kB" warning rather than just
    // raising the threshold to silence it.
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          leaflet: ['leaflet'],
        },
      },
    },
  },
})
