import { expect, test } from '@playwright/test';
import { AdminApi } from './support/api';
import { e2eName } from './support/env';

/**
 * Billing against real metering: the month overview shows server costs
 * (GPU minutes from instance uptime, per API key / cluster / model / node,
 * incl. unallocated idle time), and a monthly report can be generated,
 * opened and exported as CSV.
 */

test.afterAll(async () => {
  const api = await AdminApi.create();
  const reports = await api.get<{ id: string; name: string }[]>('/billing/reports?page_size=200').catch(() => []);
  for (const r of reports.filter((x) => x.name.startsWith('e2e-report-'))) await api.remove(`/billing/reports/${r.id}`);
  await api.dispose();
});

test('month overview shows server costs by key, cluster, model and node', async ({ page }) => {
  await page.goto('/billing');
  await expect(page.getByRole('heading', { name: 'Billing' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Budgets' })).toHaveCount(0);
  const minutes = page.getByText('Billable GPU minutes').locator('xpath=ancestor::*[contains(@class,"rounded")][1]');
  await expect(minutes).toContainText(/\d+ min/);
  for (const title of ['By API key', 'By cluster', 'By model', 'By node']) await expect(page.getByText(title, { exact: true })).toBeVisible();
  // The fleet's one cluster carries every node's cost.
  const api = await AdminApi.create();
  const clusters = await api.get<{ name: string }[]>('/clusters?page_size=50');
  await api.dispose();
  const byCluster = page.locator('[data-slot="card"]').filter({ hasText: 'By cluster' });
  await expect(byCluster).toContainText(clusters[0].name);
});

test('generate a monthly report, open it, export CSV', async ({ page }) => {
  const name = e2eName('report');
  await page.goto('/billing?tab=reports');
  await page.getByRole('button', { name: 'New report' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('radio', { name: 'This month so far' }).click();
  await dialog.getByLabel('Name').fill(name);
  await dialog.getByRole('button', { name: 'Generate report' }).click();
  await expect(dialog).toBeHidden();
  await page.getByRole('link', { name }).click();
  await page.waitForURL(/\/billing\/reports\/[0-9a-f-]{36}$/);
  await expect(page.getByRole('heading', { name })).toBeVisible();
  const id = page.url().split('/').pop() as string;
  const api = await AdminApi.create();
  await expect.poll(async () => (await api.get<{ status: string }>(`/billing/reports/${id}`)).status, { timeout: 60_000 }).toBe('ready');
  await api.dispose();
  const [csv] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Export CSV' }).click()]);
  expect(csv.suggestedFilename()).toMatch(/\.csv$/);
});
