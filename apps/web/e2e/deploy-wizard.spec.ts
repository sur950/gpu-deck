import { expect, test } from '@playwright/test';
import { AdminApi } from './support/api';
import { e2eName } from './support/env';

/**
 * Deploy wizard end to end against the live control plane: pick a catalog
 * model, let the engine auto-pick and compatibility check run on the real
 * fleet, see the scheduler's placement preview, validate with a dry run and
 * create the deployment *stopped* (so the shared node does not load a second
 * copy), then remove it.
 */
const name = e2eName('wizard');
let createdId: string | null = null;

test.afterAll(async () => {
  const api = await AdminApi.create();
  const left = await api.get<{ id: string; name: string }[]>('/deployments?page_size=200');
  for (const d of left.filter((x) => x.name.startsWith('e2e-wizard-'))) await api.remove(`/deployments/${d.id}?force=true`);
  await api.dispose();
});

test('create a deployment through every wizard step', async ({ page }) => {
  await page.goto('/deployments/new');
  await expect(page.getByRole('heading', { name: 'New deployment' })).toBeVisible();

  // 1. Model — the qwen3 GGUF registered on the Mac.
  await page.getByRole('radio', { name: /Qwen3 0\.6B/ }).click();
  await page.locator('#wz-name').fill(name);

  // 2. Engine — auto-pick recommends llama.cpp; the check runs against the online node.
  await page.getByRole('button', { name: 'Next: Engine' }).click();
  await expect(page.getByText('Auto-pick', { exact: true })).toBeVisible();
  await expect(page.getByText(/Recommends\s+llama\.cpp/)).toBeVisible();
  await expect(page.getByText(/[1-9]\d* of \d+ compatible/)).toBeVisible();

  // 3. Placement — the scheduler dry run places the replica on a real node.
  await page.getByRole('button', { name: 'Next: Placement' }).click();
  await expect(page.getByText(/1 of 1 replica placed/)).toBeVisible();

  await page.getByRole('button', { name: 'Next: Performance' }).click();
  await page.getByRole('button', { name: 'Next: Runtime' }).click();
  await page.getByRole('button', { name: 'Next: Review' }).click();

  // 6. Review — dry run, then create stopped.
  await expect(page.getByText('Ready to deploy')).toBeVisible();
  await page.getByRole('button', { name: 'Validate (dry run)' }).click();
  await expect(page.getByText(/Valid — the control plane accepted the spec/)).toBeVisible();
  await page.getByRole('switch', { name: 'Start immediately' }).click();
  await page.getByRole('button', { name: 'Create (stopped)' }).click();

  await page.waitForURL(/\/deployments\/[0-9a-f-]{36}$/);
  createdId = page.url().split('/').pop() ?? null;
  await expect(page.getByRole('heading', { name })).toBeVisible();
  await expect(page.getByText('Stopped').first()).toBeVisible();

  const api = await AdminApi.create();
  const dep = await api.get<{ name: string; status: string; replicas: number; engine_name: string | null }>(`/deployments/${createdId}`);
  expect(dep.name).toBe(name);
  expect(dep.status).toBe('stopped');
  await api.dispose();
});
