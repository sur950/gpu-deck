import { expect, test } from '@playwright/test';
import { AdminApi } from './support/api';
import { e2eName } from './support/env';
import { sshEd25519PublicKey } from './support/ssh';

/**
 * SSH instance request through the UI against the live control plane and a
 * real agent: request a CPU-only instance with an uploaded key, wait for the
 * agent to start the sshd container, check the connection details (ssh
 * command, host key fingerprint, known_hosts line), then stop and delete it.
 */
const name = e2eName('ssh').replace(/_/g, '-');

test.afterAll(async () => {
  const api = await AdminApi.create();
  const list = await api.get<{ id: string; name: string; state: string }[]>('/gpu-instances?page_size=200');
  for (const i of list.filter((x) => x.name.startsWith('e2e-ssh-'))) {
    if (!['stopped', 'error'].includes(i.state)) await api.post(`/gpu-instances/${i.id}/stop`, { force: true }).catch(() => undefined);
    await api.remove(`/gpu-instances/${i.id}?force=true`);
  }
  await api.dispose();
});

test('request an SSH instance, see how to connect, stop it', async ({ page }) => {
  test.setTimeout(5 * 60_000);
  await page.goto('/gpu-instances/new');
  await page.getByLabel('Name').fill(name);
  await page.getByRole('radio', { name: /No GPU/ }).click();
  await page.locator('#ssh-keys').fill(sshEd25519PublicKey());
  await expect(page.getByText(/SHA256:/).first()).toBeVisible();
  await page.getByRole('button', { name: 'Request instance' }).click();

  await page.waitForURL(/\/gpu-instances\/[0-9a-f-]{36}$/);
  const id = page.url().split('/').pop() as string;
  await expect(page.getByRole('heading', { name })).toBeVisible();

  // requested → placing → starting → running, driven by the agent on the node.
  const api = await AdminApi.create();
  await expect
    .poll(async () => (await api.get<{ state: string }>(`/gpu-instances/${id}`)).state, { timeout: 180_000, intervals: [2_000] })
    .toMatch(/running|error/);
  const instance = await api.get<{ state: string; status_message: string | null; ssh_host_key_fingerprints?: unknown }>(`/gpu-instances/${id}`);
  expect(instance.state, instance.status_message ?? '').toBe('running');

  await page.reload();
  await expect(page.getByText(/ssh -p \d+/).first()).toBeVisible();
  await expect(page.getByText(/SHA256:/).first()).toBeVisible();
  await expect(page.getByText(/host key reported by the agent/)).toBeVisible();
  const conn = await api.get<{ host_key_fingerprint: string | null; known_hosts: string[] }>(`/gpu-instances/${id}/connection`);
  expect(conn.host_key_fingerprint).toMatch(/^SHA256:/);
  if (conn.known_hosts.length > 0) await expect(page.getByText(/# known_hosts: /)).toBeVisible();
  else test.info().annotations.push({ type: 'gap', description: 'API returned no known_hosts lines (agent reports only the fingerprint)' });

  // TTL extend moves expires_at forward by the chosen amount.
  const before = (await api.get<{ expires_at: string | null }>(`/gpu-instances/${id}`)).expires_at;
  await page.getByRole('button', { name: '4 h' }).click();
  await expect
    .poll(async () => {
      const after = (await api.get<{ expires_at: string | null }>(`/gpu-instances/${id}`)).expires_at;
      return before && after ? Math.round((Date.parse(after) - Date.parse(before)) / 3_600_000) : null;
    })
    .toBe(4);

  // Idle stop: pick a policy, the instance record follows.
  await page.getByText('Idle stop', { exact: true }).locator('xpath=..').getByRole('combobox').click();
  await page.getByRole('option', { name: '30 min without an SSH session' }).click();
  await expect.poll(async () => (await api.get<{ idle_timeout_seconds: number | null }>(`/gpu-instances/${id}`)).idle_timeout_seconds).toBe(1800);

  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await page.getByRole('button', { name: 'Stop instance' }).click();
  await expect
    .poll(async () => (await api.get<{ state: string }>(`/gpu-instances/${id}`)).state, { timeout: 120_000, intervals: [2_000] })
    .toBe('stopped');
  await api.dispose();
});
