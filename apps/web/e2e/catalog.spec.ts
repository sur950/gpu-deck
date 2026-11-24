import { expect, test } from '@playwright/test';
import { AdminApi } from './support/api';

/**
 * Register a model that already sits on a node (Ollama blob, used in place):
 * node Files tab → Register → catalog scan on the same node → register with
 * an e2e serving name → model detail → delete. No bytes are copied.
 */
const servingName = `e2e-tinyllama-${Date.now().toString(36)}`;

test.afterAll(async () => {
  const api = await AdminApi.create();
  const models = await api.get<{ id: string; name: string }[]>('/models?page_size=500');
  for (const m of models.filter((x) => x.name.startsWith('e2e-'))) await api.remove(`/models/${m.id}?force=true`);
  await api.dispose();
});

test('register a model found on a node, then delete it', async ({ page }) => {
  test.setTimeout(3 * 60_000);
  const api = await AdminApi.create();
  const nodes = await api.get<{ id: string; status: string; agent_version: string | null }[]>('/nodes?page_size=200');
  const node = nodes.find((n) => n.status === 'online' && (n.agent_version ?? '') >= '1.0.0');
  test.skip(!node, 'needs an online node with agent >= 1.0.0');

  await page.goto(`/nodes/${node!.id}?tab=files`);
  await page.getByRole('button', { name: 'Ollama', exact: true }).click();
  await expect(page.getByText(/\d+ candidates?/)).toBeVisible({ timeout: 60_000 });
  const candidate = page.locator('[data-slot="collapsible"]').filter({ hasText: /tinyllama/i }).first();
  test.skip((await candidate.count()) === 0, 'no tinyllama in the Ollama store');
  await candidate.getByRole('link', { name: 'Register' }).click();

  await page.waitForURL(/\/catalog\/new\?source=local_path/);
  await expect(page.getByText('Ollama blob (referenced in place)')).toBeVisible({ timeout: 60_000 });
  const name = page.getByLabel('Serving name');
  await name.fill(servingName);
  await page.getByRole('button', { name: 'Register model' }).click();

  await page.waitForURL(/\/catalog\/[0-9a-f-]{36}$/);
  await expect(page.getByText(servingName).first()).toBeVisible();
  const id = page.url().split('/').pop() as string;
  const model = await api.get<{ name: string; source: string; default_version: { format: string } | null }>(`/models/${id}`);
  expect(model).toMatchObject({ name: servingName, source: 'local_path' });
  expect(model.default_version?.format).toBe('gguf');

  await api.remove(`/models/${id}?force=true`);
  await api.dispose();
});
