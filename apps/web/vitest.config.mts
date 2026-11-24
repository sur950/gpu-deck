import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

/**
 * Unit tests (`pnpm test`): pure helpers and the API client run in jsdom so
 * `document.cookie`, `fetch` stubs and Testing Library behave like the
 * browser. Tests live next to nothing — all of them are under `tests/`.
 */
export default defineConfig({
  // Keep Vite/Vitest caches inside apps/web (the default resolves to the
  // pnpm workspace root's node_modules).
  cacheDir: fileURLToPath(new URL('./node_modules/.vite', import.meta.url)),
  resolve: {
    alias: { '@': fileURLToPath(new URL('./', import.meta.url)) },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.{ts,tsx}'],
    setupFiles: ['./tests/setup.ts'],
    env: {
      NEXT_PUBLIC_API_URL: 'http://api.test/api/v1',
    },
    restoreMocks: true,
    // Default is <workspace root>/node_modules/.vitest-cache; keep it in apps/web.
    fsModuleCachePath: fileURLToPath(new URL('./node_modules/.vitest-cache', import.meta.url)),
  },
});
