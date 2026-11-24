import { expect, test } from '@playwright/test';
import { AdminApi } from './support/api';
import { e2eName } from './support/env';

/**
 * API key lifecycle through the UI: create (secret shown once, `gd-` + 48
 * hex), call the serving API with it, see it on the detail page, revoke it.
 */
const name = e2eName('key');

test.afterAll(async () => {
  const api = await AdminApi.create();
  const keys = await api.get<{ id: string; name: string; status: string }[]>('/api-keys?page_size=200');
  for (const k of keys.filter((x) => x.name.startsWith('e2e-key-'))) await api.remove(`/api-keys/${k.id}`);
  await api.dispose();
});

test('create a key, use it against the serving API, revoke it', async ({ page, request }) => {
  await page.goto('/api-keys/new');
  await page.getByLabel('Name').fill(name);
  await page.getByRole('radio', { name: '30 days' }).click();
  await page.getByRole('button', { name: 'Create key' }).click();

  await expect(page.getByRole('heading', { name: 'Key created' })).toBeVisible();
  const secret = (await page.locator('code').filter({ hasText: /^gd-/ }).first().textContent())?.trim() ?? '';
  expect(secret).toMatch(/^gd-[0-9a-f]{48}$/);
  // The curl snippet points at the serving URL the control plane advertises.
  await expect(page.getByText(/curl http:\/\/localhost:8181\/openai\/v1\/chat\/completions/)).toBeVisible();

  // The new key works on the serving API right away (model list needs no engine work).
  const serving = (process.env.E2E_SERVING_URL ?? 'http://localhost:8181').replace(/\/$/, '');
  const models = await request.get(`${serving}/v1/models`, { headers: { Authorization: `Bearer ${secret}` } });
  expect(models.status()).toBe(200);
  const ids = ((await models.json()) as { data: { id: string }[] }).data.map((m) => m.id);
  expect(ids).toContain('qwen3-0.6b');

  await page.getByRole('checkbox').click();
  await page.getByRole('button', { name: 'Open key' }).click();
  await page.waitForURL(/\/api-keys\/[0-9a-f-]{36}$/);
  await expect(page.getByRole('heading', { name })).toBeVisible();
  await expect(page.getByText(`${secret.slice(0, 12)}••••••••`)).toBeVisible();

  const id = page.url().split('/').pop() as string;
  await page.getByRole('button', { name: 'Revoke' }).click();
  await page.getByRole('button', { name: 'Revoke key' }).click();
  const api = await AdminApi.create();
  await expect.poll(async () => (await api.get<{ status: string }>(`/api-keys/${id}`)).status).toBe('revoked');
  await api.dispose();
  await expect(page.getByRole('button', { name: 'Rotate' })).toHaveCount(0);
  const denied = await request.get(`${serving}/v1/models`, { headers: { Authorization: `Bearer ${secret}` } });
  expect(denied.status()).toBe(401);
});
