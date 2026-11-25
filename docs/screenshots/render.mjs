/**
 * Renders the editable source SVGs (docs/screenshots/src/<name>.svg) into the display SVGs the
 * README and the website use, without a dev stack:
 *
 *   docs/screenshots/<name>.svg   display SVG: Satoshi text outlined, JetBrains Mono embedded
 *                                 (subset), no <foreignObject>, scripts or external references
 *
 * No raster is written: the SVG is the only committed screenshot. renderOne() also returns a 1x
 * PNG of the display SVG in memory (drawn exactly as an <img>) for capture.mjs and desktop.mjs,
 * which compare it with the live page.
 *
 *   node docs/screenshots/render.mjs                    # every source SVG
 *   node docs/screenshots/render.mjs overview nodes     # some
 *
 * Fonts are downloaded once into ~/.cache/gpu-deck-screens/fonts (see lib/fonts.mjs).
 */
import { readdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  rasterize,
  readText,
  svgSize,
  toDisplaySvg,
} from './lib/svg-render.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const SRC = join(here, 'src');
const LIMIT = 1.5 * 1024 * 1024;

const pagesFor = new WeakMap();
async function pages(browser, width, height) {
  let m = pagesFor.get(browser);
  if (!m) pagesFor.set(browser, (m = new Map()));
  const key = `${width}x${height}`;
  if (!m.has(key)) {
    const mk = async (scale) =>
      (
        await browser.newContext({
          viewport: { width, height },
          deviceScaleFactor: scale,
        })
      ).newPage();
    m.set(key, { work: await mk(1), x1: await mk(1) });
  }
  return m.get(key);
}

/** Render one screen; returns { png (1x buffer), svgBytes, srcBytes }. */
export async function renderOne(name, { browser } = {}) {
  const srcPath = join(SRC, `${name}.svg`);
  const srcText = readText(srcPath);
  const { width, height } = svgSize(srcText);
  const p = await pages(browser, width, height);
  const { svg, outlined } = await toDisplaySvg(p.work, srcText);
  const svgPath = join(here, `${name}.svg`);
  writeFileSync(svgPath, svg);
  const problems = [];
  if (
    /<foreignObject|<script|\son[a-z]+=|(?:href|src)="(?:https?:)?\/\//i.test(
      svg,
    )
  )
    problems.push('unsafe content');
  if (Buffer.byteLength(svg) > LIMIT)
    problems.push(`over ${LIMIT / 1048576} MB`);
  if (problems.length)
    console.warn(`WARNING ${name}.svg: ${problems.join(', ')}`);
  const one = await rasterize(p.x1, svgPath, { width, height, scale: 1 });
  return {
    png: one.png,
    svgBytes: Buffer.byteLength(svg),
    srcBytes: Buffer.byteLength(srcText),
    outlined,
  };
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const args = process.argv.slice(2);
  const names = args.filter((a) => !a.startsWith('--'));
  const all = readdirSync(SRC)
    .filter((f) => f.endsWith('.svg'))
    .map((f) => basename(f, '.svg'))
    .sort();
  const todo = names.length ? names : all;
  const { chromium } = require('playwright-core');
  // SHOT_BROWSER_CHANNEL=chrome uses an installed Chrome instead of Playwright's bundled Chromium.
  const channel = process.env.SHOT_BROWSER_CHANNEL;
  const browser = await chromium.launch(channel ? { channel } : {});
  try {
    for (const name of todo) {
      const r = await renderOne(name, { browser });
      const kb = (n) => `${(n / 1024).toFixed(0)} KB`;
      console.log(
        `${name.padEnd(20)} src ${kb(r.srcBytes).padStart(7)}  svg ${kb(r.svgBytes).padStart(7)}  (${r.outlined} runs outlined)`,
      );
    }
  } finally {
    await browser.close();
  }
}
