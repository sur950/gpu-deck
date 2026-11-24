import { expect, test } from '@playwright/test';
import { AdminApi } from './support/api';
import { e2eName } from './support/env';

/**
 * Audit log: an admin action made through the API shows up with actor,
 * resource and source IP, is searchable, and opens its before/after detail.
 */
const name = e2eName('audit');

test.afterAll(async () => {
  const api = await AdminApi.create();
  const keys = await api.get<{ id: string; name: string }[]>('/api-keys?page_size=200');
  for (const k of keys.filter((x) => x.name.startsWith('e2e-audit-'))) await api.remove(`/api-keys/${k.id}`);
  await api.dispose();
});

test('a new API key is audited and searchable', async ({ page }) => {
  const api = await AdminApi.create();
  await api.post('/api-keys', { name });
  await api.dispose();

  await page.goto('/audit-log');
  await expect(page.getByRole('heading', { name: 'Audit log' })).toBeVisible();
  await page.getByPlaceholder('Search events, names, messages, IPs…').fill(name);
  const row = page.getByRole('row', { name: new RegExp(`api_key\\.created.*${name}`) });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row).toContainText(/admin/i);
  await expect(row).toContainText(/127\.0\.0\.1|::1/);
  await row.click();
  await expect(page.getByText(name).nth(1)).toBeVisible();
});
