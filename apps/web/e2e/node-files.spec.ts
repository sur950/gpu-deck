import { expect, test } from '@playwright/test';
import { AdminApi } from './support/api';

/**
 * Node detail → Files: scan a model store through the agent (scan_path),
 * then hash a candidate's files (hash_files, 202 → poll the command) and check
 * the digest against the one the Ollama manifest names.
 */
test('scan the Ollama store on a node and verify a blob by sha256', async ({ page }) => {
  test.setTimeout(4 * 60_000);
  const api = await AdminApi.create();
  const nodes = await api.get<{ id: string; name: string; status: string; agent_version: string | null }[]>('/nodes?page_size=200');
  await api.dispose();
  const node = nodes.find((n) => n.status === 'online' && (n.agent_version ?? '') >= '1.0.0');
  test.skip(!node, 'needs an online node with agent >= 1.0.0');

  await page.goto(`/nodes/${node!.id}?tab=files`);
  await expect(page.getByText('Files on disk')).toBeVisible();
  await page.getByLabel(`Path on ${node!.name}`).fill('~/.ollama/models');
  await page.getByRole('button', { name: 'Scan', exact: true }).click();

  await expect(page).toHaveURL(/cmd=[0-9a-f-]{36}/);
  await expect(page.getByText('Succeeded').first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/\d+ candidates?/)).toBeVisible();
  const candidate = page.getByText(/qwen3|tinyllama/i).first();
  test.skip(!(await candidate.isVisible()), 'no Ollama models on this node');

  // Hash the first candidate; the command streams progress and lands in the hash table.
  await page.getByRole('button', { name: 'Hash', exact: true }).first().click();
  await expect(page.getByText('hash_files').first()).toBeVisible();
  await expect(page.getByText(/matches? the expected digest/)).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText('Matches').first()).toBeVisible();
  await expect(page.getByText('Mismatch')).toHaveCount(0);

  // The command id in the URL reopens the result after a reload.
  await page.reload();
  await expect(page.getByText('Matches').first()).toBeVisible();
});
