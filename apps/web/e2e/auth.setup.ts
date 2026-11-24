import { expect, test as setup } from '@playwright/test';
import { E2E } from './support/env';

/** Sign in through the real login form once; every spec reuses the session cookies. */
setup('sign in as the admin', async ({ page }) => {
  expect(E2E.password, 'set E2E_ADMIN_PASSWORD or ADMIN_PASSWORD in apps/fastapi/.env').not.toBe('');
  await page.goto('/login');
  await page.getByLabel('Username').fill(E2E.username);
  await page.getByLabel('Password', { exact: true }).fill(E2E.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL('**/dashboard');
  const cookies = await page.context().cookies();
  expect(cookies.map((c) => c.name)).toEqual(expect.arrayContaining([E2E.sessionCookie, E2E.csrfCookie]));
  await page.context().storageState({ path: E2E.authFile });
});
