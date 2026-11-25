import { expect, test, type Page } from '@playwright/test';
import { AdminApi } from './support/api';
import { e2eName } from './support/env';
import { sshEd25519PublicKey } from './support/ssh';

/**
 * The everyday admin actions on the live control plane, each one created
 * through the UI, checked through the API and removed again. Nothing here
 * loads, unloads or moves a model on the shared node.
 */

test.afterAll(async () => {
  const api = await AdminApi.create();
  const sweep = async (list: string, prefix: string, del: (id: string) => string, key = 'name') => {
    const items = await api.get<Record<string, string>[]>(`${list}${list.includes('?') ? '&' : '?'}page_size=200`).catch(() => []);
    for (const item of items.filter((x) => String(x[key] ?? '').startsWith(prefix))) await api.remove(del(item.id));
  };
  await sweep('/clusters', 'e2e-', (id) => `/clusters/${id}`);
  await sweep('/ip-rules', 'e2e', (id) => `/ip-rules/${id}`, 'description');
  await sweep('/alert-channels', 'e2e-', (id) => `/alert-channels/${id}`);
  await sweep('/layouts', 'e2e-', (id) => `/layouts/${id}`);
  await sweep('/nodes/join-tokens?active_only=true', 'e2e-', (id) => `/nodes/join-tokens/${id}`, 'node_name');
  await sweep('/benchmarks', 'e2e-', (id) => `/benchmarks/${id}`);
  const ssh = await api.get<{ id: string; name: string; state: string }[]>('/gpu-instances?page_size=200').catch(() => []);
  for (const i of ssh.filter((x) => x.name.startsWith('e2e-'))) {
    if (!['stopped', 'error'].includes(i.state)) await api.post(`/gpu-instances/${i.id}/stop`, { force: true }).catch(() => undefined);
    await api.remove(`/gpu-instances/${i.id}?force=true`);
  }
  await api.dispose();
});

async function dialogSubmit(page: Page, name: RegExp | string) {
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name }).click();
  await expect(dialog).toBeHidden();
}

test('cluster: create, edit, delete', async ({ page }) => {
  const name = e2eName('cluster');
  await page.goto('/clusters/new');
  await page.getByPlaceholder('prod-blr').fill(name);
  await page.getByRole('radio', { name: /Bare metal/ }).click();
  await page.getByRole('button', { name: 'Create cluster' }).click();
  await page.waitForURL(/\/clusters\/[0-9a-f-]{36}$/);
  await expect(page.getByRole('heading', { name })).toBeVisible();
  const id = page.url().split('/').pop() as string;

  await page.goto(`/clusters/${id}/edit`);
  await page.getByPlaceholder('What runs here and who owns it').fill('created by the e2e suite');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await page.waitForURL(new RegExp(`/clusters/${id}$`));
  await expect(page.getByText('created by the e2e suite')).toBeVisible();

  await page.getByRole('button', { name: 'Actions' }).click();
  await page.getByRole('menuitem', { name: 'Delete cluster' }).click();
  await page.getByRole('button', { name: /^Delete/ }).last().click();
  await page.waitForURL(/\/clusters$/);
  const api = await AdminApi.create();
  const left = await api.get<{ id: string }[]>('/clusters?page_size=200');
  expect(left.map((c) => c.id)).not.toContain(id);
  await api.dispose();
});

test('join token: issue, see the install one-liner, revoke', async ({ page }) => {
  const nodeName = e2eName('node');
  await page.goto('/nodes/join');
  await page.getByRole('combobox').first().click();
  await page.getByRole('option').first().click();
  await page.locator('#join-name').fill(nodeName);
  // Agent builds served by the control plane (AGENT_DIST_DIR), incl. the macOS arm64 1.1.0 build.
  await expect(page.getByText('Agent downloads')).toBeVisible();
  await expect(page.getByText(/aarch64-apple-darwin/).first()).toBeVisible();
  await page.getByRole('button', { name: 'Generate join token' }).click();
  // The one-liner pipes the install script endpoint with the one-time token header.
  await expect(page.getByText(/install\.sh/).first()).toBeVisible();
  await page.getByRole('button', { name: 'Revoke this token' }).click();
  await expect(page.getByText('Token revoked')).toBeVisible();
});

test('IP rule: block a documentation range, then delete it', async ({ page }) => {
  await page.goto('/ip-rules');
  await page.getByRole('button', { name: 'Add rule' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('radio', { name: 'API layer' }).click();
  await dialog.getByRole('radio', { name: 'Block' }).click();
  await dialog.getByPlaceholder('192.168.1.0/24 or 2001:db8::/48').fill('198.51.100.0/24');
  await dialog.getByPlaceholder('Office LAN, VPN, AI gateway host…').fill('e2e block');
  await dialog.getByRole('button', { name: /Add rule|Save|Create/ }).last().click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('button', { name: 'Actions for 198.51.100.0/24' })).toBeVisible();

  await page.getByRole('button', { name: 'Actions for 198.51.100.0/24' }).click();
  await page.getByRole('menuitem', { name: /Delete/ }).click();
  await page.getByRole('button', { name: /^Delete/ }).last().click();
  await expect(page.getByRole('button', { name: 'Actions for 198.51.100.0/24' })).toHaveCount(0);
});

test('alert channel: webhook create, test send, delete', async ({ page }) => {
  const name = e2eName('webhook');
  await page.goto('/alerts');
  await page.getByRole('button', { name: 'New channel' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('radio', { name: /Webhook/ }).click();
  await dialog.getByPlaceholder('FinOps webhook').fill(name);
  // Nothing listens there: the test send must come back as a delivery failure, not a UI error.
  await dialog.getByPlaceholder('https://hooks.example.com/gpu-deck').fill('http://127.0.0.1:9/e2e');
  await dialogSubmit(page, 'Create channel');
  const card = page.locator('[data-slot="card"]').filter({ hasText: name });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: 'Send a test message' }).click();
  await expect(card.getByText(/Last test .* — (?!delivered)/)).toBeVisible({ timeout: 30_000 });

  const api = await AdminApi.create();
  const channel = (await api.get<{ id: string; name: string; type: string }[]>('/alert-channels?page_size=200')).find((c) => c.name === name);
  expect(channel?.type).toBe('webhook');
  await api.remove(`/alert-channels/${channel!.id}`);
  await api.dispose();
  await page.reload();
  await expect(page.getByText(name)).toHaveCount(0);
});

test('GPU slice: reserve, resize, hand to an SSH instance, release', async ({ page }) => {
  test.setTimeout(4 * 60_000);
  const api = await AdminApi.create();
  const devices = await api.get<{ id: string; vram_total_bytes: number; vram_reserved_bytes: number | null }[]>('/devices?page_size=50');
  const device = devices.find((d) => d.vram_total_bytes - (d.vram_reserved_bytes ?? 0) > 4 * 1024 ** 3);
  test.skip(!device, 'needs a GPU with 4 GiB unreserved');
  type Slice = { id: string; name: string | null; status: string; vram_bytes: number; gpu_instance_id?: string | null };
  const live = async () => (await api.get<Slice[]>(`/devices/${device!.id}/slices`)).filter((s) => s.status !== 'released');
  const existing = new Set((await live()).map((s) => s.id));

  await page.goto(`/gpus/${device!.id}`);
  await page.getByRole('button', { name: 'New slice' }).first().click();
  let dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Reserve slice' }).click();
  await expect(dialog).toBeHidden();
  const slice = (await live()).find((s) => !existing.has(s.id));
  expect(slice).toBeTruthy();

  // Resize: capacity-checked PATCH of a standalone slice.
  await page.getByRole('button', { name: 'Resize' }).first().click();
  dialog = page.getByRole('dialog');
  const vram = dialog.getByRole('slider').first();
  await vram.focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await dialog.getByRole('button', { name: 'Resize slice' }).click();
  await expect(dialog).toBeHidden();
  await expect.poll(async () => (await live()).find((s) => s.id === slice!.id)?.vram_bytes).not.toBe(slice!.vram_bytes);

  // Hand the slice to an SSH instance (CPU-side on a Mac: the agent reports gpu_attached=false).
  await page.goto(`/gpu-instances/new?slice_id=${slice!.id}&node_id=${(await api.get<{ node_id: string }>(`/devices/${device!.id}`)).node_id}&device_id=${device!.id}`);
  const name = e2eName('ssh-slice');
  await page.getByLabel('Name').fill(name);
  await page.locator('#ssh-keys').fill(sshEd25519PublicKey());
  await page.getByRole('button', { name: 'Request instance' }).click();
  await page.waitForURL(/\/gpu-instances\/[0-9a-f-]{36}$/);
  const instanceId = page.url().split('/').pop() as string;
  await expect
    .poll(async () => (await api.get<{ state: string }>(`/gpu-instances/${instanceId}`)).state, { timeout: 180_000, intervals: [2_000] })
    .toMatch(/running|error/);
  const instance = await api.get<{ state: string; slice_id?: string | null; gpu_attached?: boolean | null }>(`/gpu-instances/${instanceId}`);
  expect(instance.state).toBe('running');
  await api.post(`/gpu-instances/${instanceId}/stop`, { force: true });
  await expect.poll(async () => (await api.get<{ state: string }>(`/gpu-instances/${instanceId}`)).state, { timeout: 120_000 }).toBe('stopped');
  await api.remove(`/gpu-instances/${instanceId}?force=true`);
  for (const s of await live()) if (!existing.has(s.id)) await api.delete(`/devices/${device!.id}/slices/${s.id}?force=true`);
  await api.dispose();
});

test('CSV export downloads from the audit log', async ({ page }) => {
  await page.goto('/audit-log');
  const [audit] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Export CSV' }).click()]);
  expect(audit.suggestedFilename()).toMatch(/\.csv$/);
});

test('layout: snapshot what runs now, save, delete', async ({ page }) => {
  const name = e2eName('layout');
  await page.goto('/layouts/new');
  await page.getByPlaceholder('e.g. Night').fill(name);
  const start = page.getByRole('button', { name: 'Start from what runs now' });
  await expect(start).toBeEnabled();
  await start.click();
  await expect(page.getByText('Place at least one deployment')).toHaveCount(0);
  await page.getByRole('button', { name: 'Create layout' }).click();
  await page.waitForURL(/\/layouts\/[0-9a-f-]{36}$/);
  const id = page.url().split('/').pop() as string;
  const api = await AdminApi.create();
  const layout = await api.get<{ name: string; items?: unknown[] }>(`/layouts/${id}`);
  expect(layout.name).toBe(name);
  await page.getByRole('button', { name: 'More actions' }).click();
  await page.getByRole('menuitem', { name: 'Delete layout' }).click();
  await page.getByRole('button', { name: /^Delete/ }).last().click();
  await page.waitForURL(/\/layouts$/);
  await api.dispose();
});

test('benchmark: create (not run) against a ready deployment, then delete', async ({ page }) => {
  const name = e2eName('bench');
  const api = await AdminApi.create();
  const deps = await api.get<{ id: string; name: string; ready_replicas: number | null }[]>('/deployments?page_size=200');
  const dep = deps.find((d) => (d.ready_replicas ?? 0) > 0 && d.name.startsWith('qwen3'));
  test.skip(!dep, 'needs the qwen3 deployment running');
  await page.goto('/benchmarks/new');
  await page.locator('#bm-name').fill(name);
  await page.getByRole('combobox').first().click();
  await page.getByRole('option', { name: new RegExp(`^${dep!.name} ·`) }).click();
  const runNow = page.getByRole('switch', { name: 'Run now' });
  if ((await runNow.getAttribute('aria-checked')) === 'true') await runNow.click();
  await page.getByRole('button', { name: 'Create benchmark' }).click();
  await page.waitForURL(/\/benchmarks\/[0-9a-f-]{36}$/);
  const id = page.url().split('/').pop() as string;
  const bench = await api.get<{ name: string; deployment_id: string }>(`/benchmarks/${id}`);
  expect(bench).toMatchObject({ name, deployment_id: dep!.id });
  await api.remove(`/benchmarks/${id}`);
  await api.dispose();
});
