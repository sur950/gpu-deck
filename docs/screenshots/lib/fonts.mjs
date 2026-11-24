/**
 * Font files for the screenshot pipeline, downloaded once into a local cache (never committed):
 *
 *   - Satoshi (ITF Free Font License: free to use, NOT redistributable) from the Fontshare CDN,
 *     the same files the web app loads. Only used by render.mjs to convert Satoshi text into
 *     outlines; the font itself never ends up in an SVG.
 *   - JetBrains Mono (SIL OFL 1.1, embedding allowed) from Google Fonts, the same files next/font
 *     bundles into the web app. Subset per screen and embedded as base64 @font-face.
 *
 * Cache: $GPU_DECK_FONT_CACHE or ~/.cache/gpu-deck-screens/fonts.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import subsetFont from 'subset-font';

const CACHE =
  process.env.GPU_DECK_FONT_CACHE ??
  join(homedir(), '.cache', 'gpu-deck-screens', 'fonts');
const UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36';
const SATOSHI_CSS =
  'https://api.fontshare.com/v2/css?f[]=satoshi@300,400,500,700,900&display=swap';
const JBM_CSS =
  'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@100..800&display=swap';

async function cached(name, url, headers = {}) {
  mkdirSync(CACHE, { recursive: true });
  const path = join(CACHE, name);
  if (existsSync(path)) return readFileSync(path);
  const res = await fetch(url, { headers: { 'User-Agent': UA, ...headers } });
  if (!res.ok) throw new Error(`download failed ${res.status}: ${url}`);
  const buf = Buffer.from(await res.arrayBuffer());
  writeFileSync(path, buf);
  return buf;
}

/** { weight: Buffer(ttf) } for Satoshi, or null when offline. */
export async function satoshiFonts() {
  try {
    const css = (
      await cached('satoshi.css', SATOSHI_CSS, { Referer: 'http://localhost/' })
    ).toString();
    const out = {};
    for (const block of css.split('@font-face').slice(1)) {
      const w = /font-weight:\s*(\d+)/.exec(block)?.[1];
      const ttf = /url\('([^']+\.ttf)'\)/.exec(block)?.[1];
      if (!w || !ttf) continue;
      out[w] = await cached(
        `satoshi-${w}.ttf`,
        ttf.startsWith('//') ? `https:${ttf}` : ttf,
        { Referer: 'http://localhost/' },
      );
    }
    return Object.keys(out).length ? out : null;
  } catch (err) {
    console.warn(
      `warning: Satoshi unavailable (${err.message}); Satoshi text stays as <text>`,
    );
    return null;
  }
}

/** [{ name, ranges: [[lo, hi]], buf(woff2, variable wght 100-800) }] for JetBrains Mono. */
export async function jetbrainsMonoFonts() {
  const css = (await cached('jetbrains-mono.css', JBM_CSS)).toString();
  const out = [];
  for (const m of css.matchAll(
    /\/\*\s*([\w-]+)\s*\*\/\s*@font-face\s*{([^}]*)}/g,
  )) {
    const url = /url\(([^)]+)\)/.exec(m[2])?.[1];
    const range = /unicode-range:\s*([^;]+);/.exec(m[2])?.[1];
    if (!url || !range) continue;
    const ranges = range.split(',').map((r) => {
      const [lo, hi] = r.trim().replace(/^U\+/i, '').split('-');
      return [parseInt(lo, 16), parseInt(hi ?? lo, 16)];
    });
    out.push({
      name: m[1],
      ranges,
      buf: await cached(`jetbrains-mono-${m[1]}.woff2`, url),
    });
  }
  return out;
}

const inRanges = (cp, ranges) =>
  ranges.some(([lo, hi]) => cp >= lo && cp <= hi);

/**
 * @font-face CSS embedding subsets of JetBrains Mono for the given usage
 * ({ weight: "chars" }), one static instance per weight, base64 WOFF2.
 */
export async function jetbrainsMonoCss(usage) {
  const files = await jetbrainsMonoFonts();
  let css = '';
  for (const [weight, chars] of Object.entries(usage)) {
    const cps = [...new Set([...chars].map((c) => c.codePointAt(0)))].filter(
      (cp) => cp > 32,
    );
    if (!cps.length) continue;
    for (const f of files) {
      const mine = cps.filter((cp) => inRanges(cp, f.ranges));
      if (!mine.length) continue;
      const sub = await subsetFont(f.buf, String.fromCodePoint(...mine, 32), {
        targetFormat: 'woff2',
        variationAxes: {
          wght: Math.min(800, Math.max(100, Number(weight) || 400)),
        },
      });
      const range = mine
        .map((cp) => `U+${cp.toString(16).toUpperCase()}`)
        .join(',');
      css += `@font-face{font-family:'JetBrains Mono';font-weight:${weight};font-style:normal;unicode-range:U+20,${range};src:url(data:font/woff2;base64,${sub.toString('base64')}) format('woff2')}\n`;
    }
  }
  return css;
}
