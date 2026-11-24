import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end suite against a running GPU Deck stack (`pnpm e2e`).
 *
 * The web app (E2E_BASE_URL, default http://localhost:3180) must already be
 * running with mocks OFF and pointing at a live admin API — the suite does not
 * start servers. `e2e/auth.setup.ts` signs in once (credentials from
 * E2E_ADMIN_USERNAME / E2E_ADMIN_PASSWORD, else ADMIN_USERNAME / ADMIN_PASSWORD
 * in apps/fastapi/.env) and every spec reuses that session.
 *
 * Specs create their own records with an `e2e-` prefix and remove them again;
 * they run one at a time because they share one control plane.
 */
export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e/.results',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: './e2e/.report', open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3180',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    viewport: { width: 1440, height: 900 },
    actionTimeout: 15_000,
    navigationTimeout: 45_000,
  },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, storageState: './e2e/.auth/admin.json' },
      dependencies: ['setup'],
      testIgnore: /screens\.spec\.ts/,
    },
    {
      // Visual sweep: every screen in dark and light (`pnpm e2e:screens`).
      name: 'screens',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, storageState: './e2e/.auth/admin.json' },
      dependencies: ['setup'],
      testMatch: /screens\.spec\.ts/,
    },
  ],
});
