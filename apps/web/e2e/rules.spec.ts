import { expect, test } from '@playwright/test';
import { AdminApi } from './support/api';
import { e2eName } from './support/env';

/**
 * Rules builder + dry run against the live jobs process: build an idle-unload
 * rule in watch-only mode, save it and dry-run it immediately (the decision is
 * evaluated on real metrics), open its run history, then delete it.
 */
const name = e2eName('rule');

test.afterAll(async () => {
  const api = await AdminApi.create();
  const rules = await api.get<{ id: string; name: string }[]>('/rules?page_size=200');
  for (const r of rules.filter((x) => x.name.startsWith('e2e-rule-'))) await api.remove(`/rules/${r.id}`);
  await api.dispose();
});

test('build an idle-unload rule and dry-run it', async ({ page }) => {
  await page.goto('/rules/new?type=idle_unload');
  await expect(page.getByRole('heading', { name: 'New rule' })).toBeVisible();
  await expect(page.getByRole('radio', { name: /Idle unload/i })).toHaveAttribute('aria-checked', 'true');

  // Watch-only, so the shared fleet is never touched.
  const dryRun = page.getByRole('switch', { name: 'Dry run' });
  if ((await dryRun.getAttribute('aria-checked')) !== 'true') await dryRun.click();
  await expect(page.getByText('Dry run — watch only')).toBeVisible();

  await page.locator('#rule-name').fill(name);
  await page.getByRole('button', { name: /Save & dry run now/ }).click();

  const dialog = page.getByRole('dialog', { name: new RegExp(`Dry run — ${name}`) });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/Evaluated now against live metrics/)).toBeVisible();
  // The decision comes back from the jobs process (fired or not, it lists what was considered).
  await expect(dialog.locator('[aria-busy]')).toHaveCount(0, { timeout: 30_000 });
  await expect(dialog.getByText(/Dry run failed/)).toHaveCount(0);
  await dialog.getByRole('link', { name: 'Open run history' }).click();

  await page.waitForURL(/\/rules\/[0-9a-f-]{36}$/);
  await expect(page.getByRole('heading', { name })).toBeVisible();
  // next_evaluation_at from the jobs process drives the countdown tile.
  const nextTile = page.getByText('Next evaluation').locator('xpath=..');
  await expect(nextTile).toContainText(/in \d+|\d+ ?s|\d+:\d+|now/);

  const api = await AdminApi.create();
  const id = page.url().split('/').pop() as string;
  const rule = await api.get<{ name: string; type: string; dry_run: boolean }>(`/rules/${id}`);
  expect(rule).toMatchObject({ name, type: 'idle_unload', dry_run: true });
  const runs = await api.get<{ rule_id: string; dry_run: boolean }[]>(`/rule-runs?rule_id=${id}`);
  expect(runs.length).toBeGreaterThan(0);
  await api.dispose();
});
