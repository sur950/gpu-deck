import { defineConfig } from 'vite';

// Tauri loads dist/ in production and the dev server below in `tauri dev`.
// 1420 is Tauri's conventional dev port (not one of GPU Deck's service ports).
export default defineConfig({
  clearScreen: false,
  cacheDir: 'node_modules/.vite',
  server: { port: 1420, strictPort: true, host: '127.0.0.1' },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
  },
});
