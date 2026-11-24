import { expect, test } from '@playwright/test';
import { E2E } from './support/env';

// Fresh browser: no session cookies.
test.use({ storageState: { cookies: [], origins: [] } });

test.describe('sign-in', () => {
  test('protected pages redirect to /login and come back after signing in', async ({ page }) => {
    await page.goto('/deployments');
    await expect(page).toHaveURL(/\/login\?from=%2Fdeployments/);
    await page.getByLabel('Username').fill(E2E.username);
    await page.getByLabel('Password', { exact: true }).fill(E2E.password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/deployments$/);
    await expect(page.getByRole('heading', { name: 'Deployments' })).toBeVisible();
    const names = (await page.context().cookies()).map((c) => c.name);
    expect(names).toContain(E2E.sessionCookie);
    expect(names).toContain(E2E.csrfCookie);
  });

  test('a wrong password shows the backend message', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Username').fill(E2E.username);
    await page.getByLabel('Password', { exact: true }).fill(`${E2E.password}-wrong`);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByText('Invalid username or password')).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
    // A good password right after clears the failure counter.
    await page.getByLabel('Password', { exact: true }).fill(E2E.password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/dashboard/);
  });

  test('repeated failures show the lockout notice (throwaway username)', async ({ page }) => {
    const user = `e2e-lockout-${Date.now().toString(36)}`;
    await page.goto('/login');
    await page.getByLabel('Username').fill(user);
    const password = page.getByLabel('Password', { exact: true });
    const submit = page.getByRole('button', { name: 'Sign in' });
    for (let i = 0; i < 4; i += 1) {
      await password.fill(`not-the-password-${i}`);
      await submit.click();
      await expect(page.getByText('Invalid username or password')).toBeVisible();
    }
    await password.fill('not-the-password-4');
    await submit.click();
    await expect(page.getByText(/Account is locked\. Try again in \d+ minutes?\./)).toBeVisible();
  });
});
