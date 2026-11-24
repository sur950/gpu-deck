import { expect, test, type Page } from '@playwright/test';
import { AdminApi } from './support/api';
import { e2eName } from './support/env';

/**
 * Typed credentials end to end: add an AWS key on the Credentials page
 * (schema-checked form, audited reveal), add a second one inline from the
 * cluster form's picker and create an AWS cluster with it, see the delete
 * refused while the cluster uses it, then clean up through the UI.
 */
const pageKey = e2eName('aws-page');
const inlineKey = e2eName('aws-inline');
const clusterName = e2eName('aws-cluster');
const SECRET = 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY';

test.afterAll(async () => {
  const api = await AdminApi.create();
  const clusters = await api.get<{ id: string; name: string }[]>('/clusters?page_size=200');
  for (const c of clusters.filter((x) => x.name.startsWith('e2e-aws-cluster-'))) await api.remove(`/clusters/${c.id}`);
  const creds = await api.get<{ id: string; name: string }[]>('/vault?page_size=500');
  for (const c of creds.filter((x) => x.name.startsWith('e2e-aws-'))) await api.remove(`/vault/${c.id}`);
  await api.dispose();
});

async function fillAwsKey(page: Page, name: string) {
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'New AWS access key' })).toBeVisible();
  await dialog.getByLabel(/^Name/).fill(name);
  // Client-side schema check before anything is sent.
  await dialog.getByLabel(/Access key ID/).fill('not a key');
  await dialog.getByRole('button', { name: 'Add credential' }).click();
  await expect(dialog.getByText(/Secret access key is required/)).toBeVisible();
  await expect(dialog.getByText(/Upper-case letters and digits/)).toBeVisible();
  await dialog.getByLabel(/Access key ID/).fill('AKIAIOSFODNN7EXAMPLE');
  await dialog.getByLabel(/Secret access key/).fill(SECRET);
  await dialog.getByLabel(/Default region/).fill('us-east-1');
  await dialog.getByRole('button', { name: 'Add credential' }).click();
  await expect(dialog).toBeHidden();
}

async function rowActions(page: Page, name: string) {
  await page.goto(`/credentials?search=${encodeURIComponent(name)}`);
  await expect(page.getByRole('link', { name })).toBeVisible();
  await page.getByRole('button', { name: `Actions for ${name}` }).click();
}

test('credential: create, reveal (audited), use in a cluster, delete guarded while in use', async ({ page }) => {
  // 1. Credentials page: pick the type, fill the typed form.
  await page.goto('/credentials');
  await page.getByRole('button', { name: 'Add credential' }).first().click();
  await page.getByRole('dialog').getByRole('button', { name: /AWS access key/ }).click();
  await fillAwsKey(page, pageKey);
  await page.goto(`/credentials?search=${encodeURIComponent(pageKey)}`);
  const row = page.getByRole('row').filter({ hasText: pageKey });
  await expect(row.getByText('AKIAIO...MPLE')).toBeVisible();
  await expect(row.getByText('Not referenced')).toBeVisible();
  await expect(page.getByText(SECRET)).toHaveCount(0);

  // 2. Reveal asks first, shows the secret, and lands in the audit log.
  await rowActions(page, pageKey);
  await page.getByRole('menuitem', { name: 'Reveal…' }).click();
  await page.getByRole('button', { name: 'Reveal secret' }).click();
  const revealed = page.getByRole('dialog').filter({ hasText: 'recorded in the audit log' });
  await expect(revealed.getByText(SECRET)).toBeVisible();
  await revealed.getByRole('button', { name: 'Done' }).click();
  const api = await AdminApi.create();
  const creds = await api.get<{ id: string; name: string }[]>(`/vault?search=${encodeURIComponent(pageKey)}`);
  const pageKeyId = creds[0].id;
  await expect
    .poll(async () => (await api.get<{ event_type: string }[]>(`/audit?event_type=credential.revealed&resource_id=${pageKeyId}`)).length)
    .toBeGreaterThan(0);

  // 3. Cluster form: add a key inline from the picker; it is selected on save.
  await page.goto('/clusters/new');
  await page.getByPlaceholder('prod-blr').fill(clusterName);
  await page.getByRole('radio', { name: /^AWS/ }).click();
  await page.getByPlaceholder('ap-south-1').fill('us-east-1');
  await page.getByRole('combobox').filter({ hasText: /Pick from the vault|Nothing in the vault/ }).first().click();
  await page.getByText(/Add AWS access key/).click();
  await fillAwsKey(page, inlineKey);
  await expect(page.getByRole('combobox').filter({ hasText: inlineKey })).toBeVisible();
  await page.getByRole('button', { name: 'Create cluster' }).click();
  await page.waitForURL(/\/clusters\/[0-9a-f-]{36}$/);
  const clusterId = page.url().split('/').pop() as string;
  await expect(page.getByRole('link', { name: inlineKey })).toBeVisible();

  // 4. In use → delete shows where, instead of deleting.
  await rowActions(page, inlineKey);
  await expect(page.getByRole('row').filter({ hasText: inlineKey }).getByText('1 cluster')).toBeVisible();
  await page.getByRole('menuitem', { name: 'Delete' }).click();
  const inUse = page.getByRole('dialog').filter({ hasText: 'is in use' });
  await expect(inUse.getByRole('link', { name: clusterName })).toBeVisible();
  await inUse.getByRole('button', { name: 'Close' }).last().click();
  const still = await api.get<{ id: string; name: string }[]>(`/vault?search=${encodeURIComponent(inlineKey)}`);
  expect(still.map((c) => c.name)).toContain(inlineKey);

  // 5. Free it, then delete both through the UI.
  await api.delete(`/clusters/${clusterId}`);
  for (const name of [inlineKey, pageKey]) {
    await rowActions(page, name);
    await page.getByRole('menuitem', { name: 'Delete' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: /^Delete/ }).click();
    await expect(page.getByRole('link', { name })).toHaveCount(0);
  }
  const left = await api.get<{ name: string }[]>('/vault?page_size=500');
  expect(left.map((c) => c.name)).not.toContain(pageKey);
  await api.dispose();
});
