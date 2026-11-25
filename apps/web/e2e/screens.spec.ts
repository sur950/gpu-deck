/**
 * Visual + health sweep of every screen against the live backend.
 *
 * For each route (static pages plus one detail page per resource that
 * exists), in dark and light mode: open it, wait for the network to settle,
 * screenshot it to e2e/screenshots/<mode>/<name>.png, and record page errors,
 * console errors and failing admin API calls. The run fails when any screen
 * shows an uncaught error, a 5xx, or an unexpected 4xx; a summary is written
 * to e2e/.results/screens-report.json.
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { AdminApi } from './support/api';
import { E2E } from './support/env';

type Mode = 'dark' | 'light';

interface Screen {
  name: string;
  path: string;
  /** Admin API calls allowed to fail on this screen (regex on the path), e.g. optional probes. */
  allow?: RegExp;
}

const STATIC: Screen[] = [
  { name: 'dashboard', path: '/dashboard' },
  { name: 'topology', path: '/topology' },
  { name: 'obs-inference', path: '/observability/inference' },
  { name: 'obs-infra', path: '/observability/infra' },
  { name: 'obs-logs', path: '/observability/logs' },
  { name: 'clusters', path: '/clusters' },
  { name: 'clusters-new', path: '/clusters/new' },
  { name: 'nodes', path: '/nodes' },
  { name: 'nodes-join', path: '/nodes/join' },
  { name: 'gpus', path: '/gpus' },
  { name: 'gpu-instances', path: '/gpu-instances' },
  { name: 'gpu-instances-new', path: '/gpu-instances/new' },
  { name: 'cloud', path: '/cloud' },
  { name: 'cloud-new', path: '/cloud/new' },
  { name: 'catalog', path: '/catalog' },
  { name: 'catalog-new', path: '/catalog/new' },
  { name: 'catalog-new-local', path: '/catalog/new?source=local_path' },
  { name: 'catalog-engines', path: '/catalog/engines' },
  { name: 'deployments', path: '/deployments' },
  { name: 'deployments-new', path: '/deployments/new' },
  { name: 'benchmarks', path: '/benchmarks' },
  { name: 'benchmarks-new', path: '/benchmarks/new' },
  { name: 'model-cache', path: '/model-cache' },
  { name: 'rules', path: '/rules' },
  { name: 'rules-new', path: '/rules/new' },
  { name: 'layouts', path: '/layouts' },
  { name: 'layouts-new', path: '/layouts/new' },
  { name: 'swaps', path: '/swaps' },
  { name: 'api-keys', path: '/api-keys' },
  { name: 'api-keys-new', path: '/api-keys/new' },
  { name: 'credentials', path: '/credentials' },
  { name: 'alerts', path: '/alerts' },
  { name: 'ip-rules', path: '/ip-rules' },
  { name: 'audit-log', path: '/audit-log' },
  { name: 'account', path: '/account' },
];

/** First id of a list endpoint, or null when the list is empty / the endpoint is unavailable. */
async function firstId(api: AdminApi, path: string, key = 'id'): Promise<string | null> {
  try {
    const items = await api.get<Record<string, unknown>[]>(path);
    const first = Array.isArray(items) ? items[0] : null;
    return first && typeof first[key] === 'string' ? (first[key] as string) : null;
  } catch {
    return null;
  }
}

async function detailScreens(): Promise<Screen[]> {
  const api = await AdminApi.create();
  const ids = {
    cluster: await firstId(api, '/clusters?page_size=1'),
    node: await firstId(api, '/nodes?page_size=1'),
    device: await firstId(api, '/devices?page_size=1'),
    model: await firstId(api, '/models?page_size=1'),
    deployment: await firstId(api, '/deployments?page_size=1'),
    benchmark: await firstId(api, '/benchmarks?page_size=1'),
    rule: await firstId(api, '/rules?page_size=1'),
    layout: await firstId(api, '/layouts?page_size=1'),
    swap: await firstId(api, '/swaps?page_size=1'),
    apiKey: await firstId(api, '/api-keys?page_size=1'),
    credential: await firstId(api, '/vault?page_size=1'),
    gpuInstance: await firstId(api, '/gpu-instances?page_size=1'),
    cloud: await firstId(api, '/cloud/nodes?page_size=1'),
  };
  await api.dispose();
  const out: Screen[] = [];
  const add = (cond: string | null, name: string, path: string) => cond && out.push({ name, path });
  add(ids.cluster, 'cluster-detail', `/clusters/${ids.cluster}`);
  add(ids.cluster, 'cluster-edit', `/clusters/${ids.cluster}/edit`);
  for (const tab of ['overview', 'gpus', 'instances', 'cache', 'files', 'events', 'certificates']) add(ids.node, `node-${tab}`, `/nodes/${ids.node}?tab=${tab}`);
  add(ids.device, 'gpu-detail', `/gpus/${ids.device}`);
  add(ids.device, 'obs-device', `/observability/infra/${ids.device}`);
  add(ids.model, 'model-detail', `/catalog/${ids.model}`);
  add(ids.deployment, 'deployment-detail', `/deployments/${ids.deployment}`);
  add(ids.deployment, 'deployment-edit', `/deployments/${ids.deployment}/edit`);
  add(ids.benchmark, 'benchmark-detail', `/benchmarks/${ids.benchmark}`);
  add(ids.rule, 'rule-detail', `/rules/${ids.rule}`);
  add(ids.rule, 'rule-edit', `/rules/${ids.rule}/edit`);
  add(ids.layout, 'layout-detail', `/layouts/${ids.layout}`);
  add(ids.swap, 'swap-detail', `/swaps/${ids.swap}`);
  add(ids.apiKey, 'api-key-detail', `/api-keys/${ids.apiKey}`);
  add(ids.credential, 'credential-detail', `/credentials/${ids.credential}`);
  add(ids.gpuInstance, 'gpu-instance-detail', `/gpu-instances/${ids.gpuInstance}`);
  add(ids.cloud, 'cloud-detail', `/cloud/${ids.cloud}`);
  return out;
}

interface Finding {
  screen: string;
  mode: Mode;
  pageErrors: string[];
  consoleErrors: string[];
  failedCalls: string[];
}

function watch(page: Page, screen: Screen) {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const failedCalls: string[] = [];
  page.on('pageerror', (err) => pageErrors.push(err.message.split('\n')[0]));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text().slice(0, 300));
  });
  page.on('response', (res) => {
    const url = res.url();
    if (!url.startsWith(E2E.apiUrl) || res.status() < 400) return;
    const path = url.slice(E2E.apiUrl.length);
    if (screen.allow?.test(path)) return;
    failedCalls.push(`${res.request().method()} ${path.split('?')[0]} → ${res.status()}`);
  });
  return { pageErrors, consoleErrors, failedCalls };
}

async function settle(page: Page) {
  await page.waitForLoadState('networkidle', { timeout: 8_000 }).catch(() => undefined);
  // Skeletons → content; charts animate in.
  await page.waitForTimeout(900);
}

/**
 * The shell scrolls inside <main> (the page itself is h-screen), so a
 * full-page screenshot would stop at the viewport. Grow the viewport to the
 * scroll height of <main> for the shot, then restore it.
 */
async function shoot(page: Page, path: string) {
  const height = await page.evaluate(() => {
    const main = document.querySelector('main');
    return Math.ceil((main?.scrollHeight ?? 0) + (main?.getBoundingClientRect().top ?? 0));
  });
  const base = page.viewportSize() ?? { width: 1440, height: 900 };
  if (height > base.height) {
    await page.setViewportSize({ width: base.width, height: Math.min(height, 8000) });
    await page.waitForTimeout(400);
  }
  await page.screenshot({ path });
  if (height > base.height) await page.setViewportSize(base);
}

const findings: Finding[] = [];

test.describe.configure({ mode: 'serial' });

for (const mode of ['dark', 'light'] as const) {
  test(`every screen renders without errors (${mode})`, async ({ page }) => {
    test.setTimeout(15 * 60_000);
    await page.addInitScript((m) => window.localStorage.setItem('gpu-deck:color-mode:v1', m), mode);
    const screens = [...STATIC, ...(await detailScreens())];
    const dir = resolve(E2E.screenshotDir, mode);
    mkdirSync(dir, { recursive: true });
    for (const screen of screens) {
      await test.step(screen.name, async () => {
        const sink = watch(page, screen);
        await page.goto(screen.path);
        await settle(page);
        await expect(page).not.toHaveURL(/\/login/);
        await shoot(page, resolve(dir, `${screen.name}.png`));
        page.removeAllListeners('pageerror');
        page.removeAllListeners('console');
        page.removeAllListeners('response');
        findings.push({ screen: screen.name, mode, ...sink });
      });
    }
    mkdirSync(resolve(__dirname, '.results'), { recursive: true });
    writeFileSync(resolve(__dirname, '.results/screens-report.json'), JSON.stringify(findings.filter((f) => f.pageErrors.length + f.failedCalls.length + f.consoleErrors.length > 0), null, 2));
    const broken = findings.filter((f) => f.mode === mode && (f.pageErrors.length > 0 || f.failedCalls.length > 0));
    expect(broken, JSON.stringify(broken, null, 2)).toEqual([]);
  });
}
