/**
 * Captures the product screenshots as editable SVG, then renders the display SVG and PNG.
 *
 * Needs a running dev stack with some data in it (clusters, nodes, models, a deployment and a
 * rule; see docs/DEVELOPMENT.md, "Screenshots"):
 *
 *   ./infra/scripts/bootstrap.dev.sh bootstrap
 *   (cd docs/screenshots && pnpm install --ignore-workspace)
 *   node docs/screenshots/capture.mjs [name ...]
 *
 * For every screen it writes
 *   docs/screenshots/src/<name>.svg  editable source: text as <text>, colours as theme-token classes
 *   docs/screenshots/<name>.svg      display SVG (Satoshi outlined, GitHub-safe), via render.mjs
 * and checks the display SVG against a normal screenshot of the live page (pixel diff).
 *
 * Env: SHOT_BASE_URL (default http://localhost:3180), ADMIN_USERNAME / ADMIN_PASSWORD (default:
 * apps/fastapi/.env), SHOT_TMP (reference screenshots + diffs, default $TMPDIR/gpu-deck-shots),
 * SHOT_MAX_DIFF (default 0.02 = 2 % of pixels), SHOT_MAX_BLOCKS (default 4: 8x8 blocks that differ
 * densely, i.e. a structural difference rather than anti-aliasing), SHOT_CONFIG (optional local
 * config module, default docs/screenshots/capture.config.mjs; git-ignored). --skip-setup skips the
 * config's setup/teardown and per-screen hooks.
 *
 * The optional config module default-exports any of:
 *   deploymentId, ruleId          detail pages to open (default: the first row of the list)
 *   joinCluster, joinNodeName     cluster (regex source) and node name on the Add node form
 *   wizardModel, wizardName       model radio (regex source) and deployment name in the wizard
 *   cloudCluster, cloudRegion, cloudInstanceType   selections on the cloud provisioning form
 *   setup(h), teardown(h)         run before / after all screens, h = { psql, redis, env }
 *   before / after                { <screen name>: (h) => void } around single screens
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { renderOne } from './render.mjs';
import { comparePng } from './lib/diff.mjs';
import { embedSourceFonts } from './lib/svg-render.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '../..');
const require = createRequire(import.meta.url);
const { chromium } = require('playwright-core');

function dotenv(path) {
  if (!existsSync(path)) return {};
  return Object.fromEntries(
    readFileSync(path, 'utf8')
      .split('\n')
      .map((l) => /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/.exec(l))
      .filter(Boolean)
      .map((m) => [m[1], m[2].replace(/^(['"])(.*)\1$/, '$2')]),
  );
}
const env = dotenv(resolve(root, 'apps/fastapi/.env'));
const BASE = process.env.SHOT_BASE_URL ?? 'http://localhost:3180';
const USER = process.env.ADMIN_USERNAME ?? env.ADMIN_USERNAME ?? 'admin';
const PASS = process.env.ADMIN_PASSWORD ?? env.ADMIN_PASSWORD ?? '';
const TMP = process.env.SHOT_TMP ?? join(tmpdir(), 'gpu-deck-shots');
const MAX_DIFF = Number(process.env.SHOT_MAX_DIFF ?? 0.02);
const MAX_BLOCKS = Number(process.env.SHOT_MAX_BLOCKS ?? 4);
const SRC = join(here, 'src');
const VIEW = { width: 1440, height: 900 };

// Optional local config (git-ignored): pins screens to specific rows and adds setup hooks.
const CONFIG_PATH = process.env.SHOT_CONFIG
  ? resolve(process.env.SHOT_CONFIG)
  : join(here, 'capture.config.mjs');
const config = existsSync(CONFIG_PATH)
  ? ((await import(pathToFileURL(CONFIG_PATH).href)).default ?? {})
  : {};
const re = (s) => (s instanceof RegExp ? s : new RegExp(s));

const SHOTS = [
  { name: 'login', path: '/login', anonymous: true, title: 'Sign in' },
  { name: 'overview', path: '/dashboard', title: 'Dashboard' },
  { name: 'gpus', path: '/gpus', title: 'GPUs' },
  { name: 'infra', path: '/observability/infra', title: 'GPU infrastructure' },
  { name: 'inference', path: '/observability/inference', title: 'Inference' },
  { name: 'topology', path: '/topology', title: 'Topology' },
  { name: 'nodes', path: '/nodes', title: 'Nodes' },
  {
    name: 'nodes-join',
    path: '/nodes/join',
    run: joinForm,
    title: 'Add a node',
  },
  {
    name: 'nodes-join-pool',
    path: '/nodes/join',
    run: joinPool,
    title: 'Add a node pool',
  },
  { name: 'catalog', path: '/catalog', title: 'Catalog' },
  {
    name: 'deployment-wizard',
    path: '/deployments/new',
    run: wizard,
    title: 'Deploy wizard',
  },
  {
    name: 'deployment-detail',
    path: config.deploymentId
      ? `/deployments/${config.deploymentId}`
      : firstRow('/deployments'),
    title: 'Deployment',
  },
  {
    name: 'rules-builder',
    path: '/rules/new?type=time_schedule',
    title: 'Rule builder',
  },
  {
    name: 'rule-detail',
    path: config.ruleId ? `/rules/${config.ruleId}` : firstRow('/rules'),
    title: 'Rule history',
  },
  { name: 'layouts', path: '/layouts', title: 'Layouts' },
  { name: 'swaps', path: '/swaps', title: 'Swaps' },
  { name: 'metering', path: '/metering', title: 'Metering' },
  { name: 'billing', path: '/billing', title: 'Billing' },
  { name: 'credentials', path: '/credentials', title: 'Credentials' },
  { name: 'ssh-instances', path: '/gpu-instances', title: 'SSH GPU instances' },
  { name: 'cloud', path: '/cloud', title: 'Cloud VMs' },
  {
    name: 'cloud-provision',
    path: '/cloud/new',
    run: cloudProvision,
    title: 'Provision a cloud GPU VM',
  },
  {
    name: 'audit-log',
    path: '/audit-log',
    title: 'Audit log',
  },
  {
    name: 'light-overview',
    path: '/dashboard',
    mode: 'light',
    title: 'Dashboard',
  },
  {
    name: 'light-deployments',
    path: '/deployments',
    mode: 'light',
    title: 'Deployments',
  },
  { name: 'light-billing', path: '/billing', mode: 'light', title: 'Billing' },
];

// ---------------------------------------------------------------- page hooks
/** Path resolver: the first detail link (<prefix>/<uuid>) on the list page. */
function firstRow(prefix) {
  return async (page) => {
    await page.goto(`${BASE}${prefix}`);
    await settle(page);
    const href = await page.evaluate((p) => {
      const ok = new RegExp(`^${p}/[0-9a-f-]{36}$`);
      return [...document.querySelectorAll('a[href]')]
        .map((a) => a.getAttribute('href'))
        .find((h) => ok.test(h ?? ''));
    }, prefix);
    if (!href) throw new Error(`no row on ${prefix} to open (set it in the capture config)`);
    return href;
  };
}

async function pickOption(page, combobox, name) {
  await combobox.click();
  const option = name
    ? page.getByRole('option', { name: re(name) })
    : page.getByRole('option');
  await option.first().click();
}

async function wizard(page) {
  // Model -> Engine (auto-pick) -> Placement (scheduler dry run on the fleet); nothing is created.
  const model = config.wizardModel
    ? page.getByRole('radio', { name: re(config.wizardModel) })
    : page.getByRole('radio');
  await model.first().click();
  if (config.wizardName) await page.locator('#wz-name').fill(config.wizardName);
  await page.getByRole('button', { name: 'Next: Engine' }).click();
  await page
    .getByText(/compatible/)
    .first()
    .waitFor({ timeout: 20_000 })
    .catch(() => undefined);
  await page.getByRole('button', { name: 'Next: Placement' }).click();
  await page
    .getByText(/replica.? placed/)
    .first()
    .waitFor({ timeout: 20_000 })
    .catch(() => undefined);
  await page.waitForTimeout(1500);
}

async function joinForm(page) {
  // Fill the form only: generating a token would put a live join secret in the image.
  await pickOption(page, page.getByRole('combobox').first(), config.joinCluster);
  await page
    .getByPlaceholder('gpu-box-02')
    .fill(config.joinNodeName ?? 'gpu-box-02');
  await page.waitForTimeout(500);
}

async function joinPool(page) {
  // Multi-use enrolment token (node pools / DaemonSets). The issued token is a live secret: it
  // is masked in the DOM before the capture (maskSecrets) and revoked afterwards.
  await pickOption(page, page.getByRole('combobox').first(), config.joinCluster);
  await page.getByRole('combobox').nth(1).click();
  await page.getByRole('option', { name: /Up to 25 machines/ }).click();
  await page.getByRole('combobox').nth(2).click();
  await page.getByRole('option', { name: '1 day' }).click();
  await page.getByRole('button', { name: 'Generate join token' }).click();
  await page
    .getByText(/Enrolment token/)
    .first()
    .waitFor({ timeout: 20_000 });
  await page.waitForTimeout(1000);
}

async function cloudProvision(page) {
  // A cloud cluster with a credential: region + instance type, nothing is launched.
  const cluster = page.getByRole('combobox').nth(0);
  if (config.cloudCluster && (await cluster.isVisible().catch(() => false))) {
    const txt = (await cluster.textContent()) ?? '';
    if (!re(config.cloudCluster).test(txt)) {
      await cluster.click();
      await page.getByRole('option', { name: re(config.cloudCluster) }).click();
    }
  }
  await page.waitForTimeout(1500);
  const region = page
    .getByRole('combobox')
    .filter({ hasText: /region|Loading|^[a-z]+-[a-z]+-\d/i })
    .first();
  if (config.cloudRegion && (await region.isVisible().catch(() => false))) {
    const txt = (await region.textContent()) ?? '';
    if (!re(config.cloudRegion).test(txt)) {
      await region.click();
      await page
        .getByRole('option', { name: re(config.cloudRegion) })
        .first()
        .click()
        .catch(() => page.keyboard.press('Escape'));
    }
  }
  await page.waitForTimeout(1500);
  if (config.cloudInstanceType) {
    const type = page.getByText(re(config.cloudInstanceType)).first();
    if (await type.isVisible().catch(() => false))
      await type.click().catch(() => undefined);
  }
  await page.waitForTimeout(1000);
  // Frame the region step and the instance-type table (the click scrolled the row into view).
  const moved = await page.evaluate(() => {
    const scrollers = [...document.querySelectorAll('*')].filter(
      (e) =>
        (e.scrollHeight > e.clientHeight + 4 &&
          /auto|scroll/.test(getComputedStyle(e).overflowY)) ||
        e === document.scrollingElement,
    );
    const info = scrollers
      .map((e) => `${e.tagName}:${e.scrollTop}/${e.scrollHeight}`)
      .join(' ');
    const h = [
      ...document.querySelectorAll('main p, main span, main div'),
    ].find(
      (e) =>
        e.children.length === 0 &&
        /^Regions that offer/.test(e.textContent?.trim() ?? ''),
    );
    if (!h || !scrollers.length) return `no target (${info}) h=${!!h}`;
    const sc = document.querySelector('main');
    sc.scrollTop += h.getBoundingClientRect().top - 150;
    return `scrolled ${sc.tagName}.${sc.className} h=${h.tagName}:${h.getBoundingClientRect().top} now=${sc.scrollTop}`;
  });
  if (!/scrolled/.test(moved)) console.warn('cloud-provision:', moved);
  await page.waitForTimeout(500);
}

// ---------------------------------------------------------------- dev deps (Docker) helpers
// Available to the optional config hooks as { psql, redis, env }.
function psql(sql) {
  const id = execFileSync('docker', [
    'ps',
    '-q',
    '--filter',
    'label=com.docker.compose.project=gpu-deck-deps',
    '--filter',
    'label=com.docker.compose.service=postgres',
  ])
    .toString()
    .trim();
  if (!id) throw new Error('gpu-deck-deps postgres container is not running');
  const db = env.DATABASE_URL
    ? (/\/([^/?]+)(\?|$)/.exec(env.DATABASE_URL)?.[1] ?? 'gpu_deck_dev')
    : 'gpu_deck_dev';
  return execFileSync(
    'docker',
    [
      'exec',
      '-i',
      id,
      'psql',
      '-U',
      'postgres',
      '-d',
      db,
      '-v',
      'ON_ERROR_STOP=1',
      '-Atq',
    ],
    { input: `SET session_replication_role = replica;\n${sql}` },
  ).toString();
}
function redis(args, input) {
  const id = execFileSync('docker', [
    'ps',
    '-q',
    '--filter',
    'label=com.docker.compose.project=gpu-deck-deps',
    '--filter',
    'label=com.docker.compose.service=redis',
  ])
    .toString()
    .trim();
  const u = new URL(env.REDIS_URL ?? 'redis://127.0.0.1:6379/0');
  const auth = u.password
    ? [
        '--user',
        decodeURIComponent(u.username || 'default'),
        '--pass',
        decodeURIComponent(u.password),
        '--no-auth-warning',
      ]
    : [];
  return execFileSync(
    'docker',
    [
      'exec',
      '-i',
      id,
      'redis-cli',
      ...auth,
      '-n',
      u.pathname.slice(1) || '0',
      ...args,
    ],
    { input },
  ).toString();
}
function revokeJoinTokens(created) {
  // Revoke the multi-use token the node-pool capture issued (created during this run, unused).
  psql(
    `UPDATE join_tokens SET revoked_at = now() WHERE revoked_at IS NULL AND created_at >= '${created.toISOString()}';`,
  );
}

// ---------------------------------------------------------------- capture helpers
const PREP_CSS = `
  *{scrollbar-width:none!important}
  *::-webkit-scrollbar{display:none!important}
  *,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}
  nextjs-portal,[data-sonner-toaster]{display:none!important}
`;
async function settle(page) {
  await page
    .waitForLoadState('networkidle', { timeout: 10_000 })
    .catch(() => undefined);
  await page.waitForTimeout(2500);
}
async function prepare(page) {
  await page.addStyleTag({ content: PREP_CSS });
  await page.evaluate(() => {
    document.activeElement?.blur?.();
    // Secrets: join tokens (gdj_...) and full API keys (gd- + 48 hex) never reach an image.
    const rules = [
      [/gdj_[A-Za-z0-9_-]+/g, () => 'gdj_' + '•'.repeat(12)],
      [/gd-[0-9a-f]{12,}/g, (m) => m.slice(0, 11) + '•'.repeat(8)],
    ];
    const mask = (t) => rules.reduce((s, [re, f]) => s.replace(re, f), t);
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const m = mask(n.nodeValue);
      if (m !== n.nodeValue) n.nodeValue = m;
    }
    document.querySelectorAll('input,textarea').forEach((e) => {
      const m = mask(e.value);
      if (m !== e.value) e.value = m;
    });
  });
  await page.mouse.move(VIEW.width - 1, VIEW.height - 1);
  await page.waitForTimeout(400);
}
async function assertClean(page, name) {
  const bad = await page.evaluate(() => {
    const t = document.body.innerText;
    const hits = [];
    if (/gdj_[A-Za-z0-9]{6}/.test(t)) hits.push('join token');
    if (/gd-[0-9a-f]{16}/.test(t)) hits.push('API key');
    if (
      /Something went wrong|Application error|Unhandled Runtime Error/i.test(t)
    )
      hits.push('error state');
    return hits;
  });
  if (bad.length) console.warn(`WARNING ${name}: ${bad.join(', ')}`);
}

async function captureShot(page, aux, shot, mode) {
  await prepare(page);
  await assertClean(page, shot.name);
  const ref = await page.screenshot();
  writeFileSync(join(TMP, `${shot.name}.ref.png`), ref);
  await page.addScriptTag({ path: join(here, 'lib/dom-to-svg.js') });
  const res = await page.evaluate((o) => window.__gpuDeckDomToSvg(o), {
    title: `GPU Deck: ${shot.title} (${mode})`,
  });
  const src = await embedSourceFonts(aux, res.svg);
  const srcPath = join(SRC, `${shot.name}.svg`);
  writeFileSync(
    srcPath,
    `<?xml version="1.0" encoding="UTF-8"?>\n<!-- GPU Deck screenshot source: ${shot.name} (${mode}). Editable; re-render with node docs/screenshots/render.mjs ${shot.name} -->\n${src}\n`,
  );
  const out = await renderOne(shot.name, { browser: aux.context().browser() });
  const diff = comparePng(ref, out.png, {
    diffPath: join(TMP, `${shot.name}.diff.png`),
  });
  const flag =
    diff.ratio > MAX_DIFF || diff.blocks > MAX_BLOCKS
      ? '  <-- check the diff image'
      : '';
  console.log(
    `${shot.name.padEnd(20)} src ${kb(out.srcBytes)}  svg ${kb(out.svgBytes)}  diff ${(diff.ratio * 100).toFixed(2)}% / ${diff.blocks} blocks  (${res.stats.texts} texts, ${res.stats.svgs} svgs, ${res.stats.dropped} dropped)${flag}`,
  );
  return diff.ratio;
}
const kb = (n) => `${(n / 1024).toFixed(0)} KB`.padStart(7);

// ---------------------------------------------------------------- main
// Runs only as a script (importing this module for its helpers must not touch the database).
async function main() {
  const args = process.argv.slice(2);
  const only = args.filter((a) => !a.startsWith('--'));
  const skipSetup = args.includes('--skip-setup');
  const helpers = { psql, redis, env };
  const hook = (fn) => (!skipSetup && fn ? fn(helpers) : undefined);
  mkdirSync(TMP, { recursive: true });
  mkdirSync(SRC, { recursive: true });
  const browser = await chromium.launch();
  const started = new Date();
  let worst = 0;
  try {
    await hook(config.setup);
    const auxCtx = await browser.newContext({ viewport: VIEW });
    const aux = await auxCtx.newPage();
    for (const mode of ['dark', 'light']) {
      const shots = SHOTS.filter(
        (s) =>
          (s.mode ?? 'dark') === mode &&
          (!only.length || only.includes(s.name)),
      );
      if (!shots.length) continue;
      const ctx = await browser.newContext({
        viewport: VIEW,
        deviceScaleFactor: 1,
        colorScheme: mode,
      });
      await ctx.addInitScript(
        (m) => window.localStorage.setItem('gpu-deck:color-mode:v1', m),
        mode,
      );
      const page = await ctx.newPage();
      for (const shot of shots.filter((s) => s.anonymous)) {
        await page.goto(`${BASE}${shot.path}`);
        await settle(page);
        worst = Math.max(worst, await captureShot(page, aux, shot, mode));
      }
      const authed = shots.filter((s) => !s.anonymous);
      if (authed.length) {
        await page.goto(`${BASE}/login`);
        await page.getByLabel('Username').fill(USER);
        await page.getByLabel('Password', { exact: true }).fill(PASS);
        await page.getByRole('button', { name: 'Sign in' }).click();
        await page.waitForURL('**/dashboard');
      }
      for (const shot of authed) {
        await hook(config.before?.[shot.name]);
        const path =
          typeof shot.path === 'function' ? await shot.path(page) : shot.path;
        await page.goto(`${BASE}${path}`);
        await settle(page);
        if (shot.run) await shot.run(page);
        worst = Math.max(worst, await captureShot(page, aux, shot, mode));
        await hook(config.after?.[shot.name]);
      }
      await ctx.close();
    }
  } finally {
    try {
      revokeJoinTokens(started);
    } catch (err) {
      console.warn('could not revoke join tokens:', err.message);
    }
    try {
      await hook(config.teardown);
    } catch (err) {
      console.warn('config teardown failed:', err.message);
    }
    await browser.close();
  }
  console.log(
    `worst diff ${(worst * 100).toFixed(2)}% (reference screenshots and diff images in ${TMP})`,
  );
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
)
  await main();
