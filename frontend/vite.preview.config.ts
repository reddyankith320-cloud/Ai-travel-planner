import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { viteSingleFile } from 'vite-plugin-singlefile';

/**
 * Builds the static preview: ONE self-contained HTML file with the app, its
 * styles, Leaflet's stylesheet and the recorded example results all inlined.
 *
 *   npm run build:preview     ->  dist-preview/preview.html
 *
 * It needs no server. It is also not the real product: it replays recorded
 * results for a handful of example trips (see src/preview/shim.ts).
 */
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  define: { 'import.meta.env.VITE_PREVIEW': JSON.stringify('1') },
  build: {
    outDir: 'dist-preview',
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2022',
    rollupOptions: { input: 'preview.html' },
  },
});
