/**
 * Desktop app connect screen as editable SVG (dark + light).
 *
 * The connect screen is a plain web view (apps/desktop/src), so it is loaded from its Vite dev
 * server in Chromium with the Tauri IPC stubbed (two saved control planes, healthy probes) and run
 * through the same DOM -> SVG converter as the web app:
 *
 *   node docs/screenshots/desktop.mjs     # writes src/desktop-connect{,-light}.svg + display SVGs
 *
 * The README and the website show the display SVGs (the web view without the macOS title bar);
 * no PNG is committed.
 */
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderOne } from './render.mjs';
import { embedSourceFonts } from './lib/svg-render.mjs';
import { comparePng } from './lib/diff.mjs';
import { tmpdir } from 'node:os';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '../..');
const require = createRequire(import.meta.url);
const { chromium } = require('playwright-core');
const PORT = Number(process.env.DESKTOP_VITE_PORT ?? 1431);
const VIEW = { width: 1040, height: 760 }; // tauri.conf.json window size
const TMP = process.env.SHOT_TMP ?? join(tmpdir(), 'gpu-deck-shots');

const NOW = Date.now();
// Same two control planes the native capture uses (both the local dev stack).
const SERVERS = [
  {
    id: 'a6f0c1d2-0000-4000-8000-000000000001',
    name: 'Local dev',
    url: 'http://localhost:3180',
    apiUrl: 'http://localhost:8180',
    createdAt: NOW - 9 * 864e5,
    lastUsedAt: NOW - 20e3,
    insecure: true,
  },
  {
    id: 'a6f0c1d2-0000-4000-8000-000000000002',
    name: 'Bangalore DC',
    url: 'http://127.0.0.1:3180',
    apiUrl: 'http://127.0.0.1:8180',
    createdAt: NOW - 30 * 864e5,
    lastUsedAt: NOW - 3 * 36e5,
    insecure: true,
  },
];
const CHECKS = [
  'agent_hub',
  'api_layer',
  'celery',
  'database',
  'jobs_leader',
  'redis',
  'system',
];
const probe = (url, apiUrl, latencyMs, degraded) => {
  const origin = new URL(url).origin;
  return {
    origin,
    apiBase: apiUrl ? new URL(apiUrl).origin : origin,
    insecure: origin.startsWith('http:'),
    isGpuDeck: true,
    branding: {
      appName: 'GPU Deck',
      appByline: 'GPU control plane',
      brandColor: '#B3122E',
    },
    health: {
      status: degraded ? 'degraded' : 'healthy',
      version: '1.0.0',
      checks: CHECKS.map((name) => ({
        name,
        status:
          name === 'system'
            ? 'unknown'
            : degraded && name === 'celery'
              ? 'degraded'
              : 'healthy',
      })),
    },
    error: null,
    latencyMs,
  };
};

function tauriStub(data) {
  // Minimal stand-in for window.__TAURI_INTERNALS__ (invoke + event listen).
  let cb = 1;
  const probes = data.probes;
  window.__TAURI_INTERNALS__ = {
    metadata: {
      currentWindow: { label: 'connect' },
      currentWebview: { label: 'connect' },
    },
    transformCallback: () => cb++,
    unregisterCallback: () => undefined,
    convertFileSrc: (p) => p,
    invoke: async (cmd, args) => {
      switch (cmd) {
        case 'servers_list':
          return {
            servers: data.servers,
            lastServerId: data.servers[0].id,
            currentServerId: null,
            appVersion: '1.0.0',
            platform: 'macos',
          };
        case 'servers_probe':
          return probes[args.url];
        case 'connect_prefill':
          return null;
        case 'plugin:event|listen':
          return cb++;
        default:
          return null;
      }
    },
  };
}

async function waitForServer(url, ms = 30_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`vite did not start on ${url}`);
}

const vite = spawn(
  join(root, 'apps/desktop/node_modules/.bin/vite'),
  ['--port', String(PORT), '--strictPort', '--host', '127.0.0.1'],
  { cwd: join(root, 'apps/desktop'), stdio: 'ignore' },
);
const browser = await chromium.launch();
try {
  await waitForServer(`http://127.0.0.1:${PORT}/`);
  const aux = await (await browser.newContext({ viewport: VIEW })).newPage();
  mkdirSync(join(here, 'src'), { recursive: true });
  mkdirSync(TMP, { recursive: true });
  for (const mode of ['dark', 'light']) {
    const name = mode === 'dark' ? 'desktop-connect' : 'desktop-connect-light';
    const ctx = await browser.newContext({ viewport: VIEW, colorScheme: mode });
    const probes = Object.fromEntries(
      SERVERS.map((s, i) => [
        s.url,
        probe(s.url, s.apiUrl, [27, 31][i], false),
      ]),
    );
    await ctx.addInitScript(tauriStub, { servers: SERVERS, probes });
    const page = await ctx.newPage();
    await page.goto(`http://127.0.0.1:${PORT}/`);
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(1500);
    await page.addStyleTag({ content: '*{scrollbar-width:none!important}' });
    await page.waitForTimeout(1500); // entrance animations finish (disabling them would leave opacity:0)
    await page.mouse.move(VIEW.width - 1, VIEW.height - 1);
    const ref = await page.screenshot();
    await page.addScriptTag({ path: join(here, 'lib/dom-to-svg.js') });
    const res = await page.evaluate((o) => window.__gpuDeckDomToSvg(o), {
      title: `GPU Deck desktop: connect screen (${mode})`,
    });
    const src = await embedSourceFonts(aux, res.svg);
    writeFileSync(
      join(here, 'src', `${name}.svg`),
      `<?xml version="1.0" encoding="UTF-8"?>\n<!-- GPU Deck screenshot source: ${name} (desktop connect screen, ${mode}). Editable; re-render with node docs/screenshots/render.mjs ${name} -->\n${src}\n`,
    );
    const out = await renderOne(name, { browser });
    const diff = comparePng(ref, out.png, {
      diffPath: join(TMP, `${name}.diff.png`),
    });
    writeFileSync(join(TMP, `${name}.ref.png`), ref);
    writeFileSync(join(TMP, `${name}.svg.png`), out.png);
    console.log(
      `${name.padEnd(22)} src ${(out.srcBytes / 1024).toFixed(0)} KB  svg ${(out.svgBytes / 1024).toFixed(0)} KB  diff ${(diff.ratio * 100).toFixed(2)}% / ${diff.blocks} blocks`,
    );
    await ctx.close();
  }
} finally {
  await browser.close();
  vite.kill();
}
