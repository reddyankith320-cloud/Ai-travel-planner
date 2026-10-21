import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The browser only ever talks to our own backend. API keys stay on the
    // server, so nothing secret is reachable from this origin.
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    // Source maps would expose our own code, not secrets, but there is no
    // reason to ship them to production either.
    sourcemap: false,
    target: 'es2022',
  },
});
