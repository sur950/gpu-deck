/**
 * Turns an editable source SVG (docs/screenshots/src/<name>.svg: text as <text>, Satoshi by
 * family name only) into the display SVG (docs/screenshots/<name>.svg) and raster images.
 *
 *   - Satoshi text -> outlines: every glyph once in <defs>, each run a <g> of <use> references
 *     placed at the browser's own glyph positions (HarfBuzz picks the glyphs and ligatures with
 *     the same features the web app uses). The run keeps its class (theme colour) and an
 *     aria-label with the original string. Satoshi may not be redistributed, so it is never
 *     embedded as a font.
 *   - JetBrains Mono text stays text; its @font-face (base64 WOFF2 subset) is rebuilt from the
 *     characters actually used, so edited strings always render.
 *   - Rasters: the display SVG is drawn exactly as an <img> (what GitHub does) at 1x / 2x and
 *     written as PNG and WebP.
 *
 * Everything runs in Chromium through playwright-core.
 */
import { readFileSync } from 'node:fs';
import * as hb from 'harfbuzzjs';
import { jetbrainsMonoCss, satoshiFonts } from './fonts.mjs';

let satoshi; // { weight: { font, face, upem, cmap:Set } }
async function loadSatoshi() {
  if (satoshi !== undefined) return satoshi;
  const files = await satoshiFonts();
  if (!files) return (satoshi = null);
  satoshi = {};
  for (const [w, buf] of Object.entries(files)) {
    const blob = new hb.Blob(buf);
    const face = new hb.Face(blob, 0);
    const font = new hb.Font(face);
    satoshi[w] = {
      font,
      upem: face.upem,
      cmap: new Set(face.collectUnicodes()),
      buf,
    };
  }
  return satoshi;
}
function satoshiFor(weight) {
  const ws = Object.keys(satoshi).map(Number);
  const want = Number(weight) || 400;
  // CSS font matching for weights > 500: heavier first, then lighter.
  const best = ws.sort(
    (a, b) =>
      Math.abs(a - want) - Math.abs(b - want) || (want >= 500 ? b - a : a - b),
  )[0];
  return { w: best, ...satoshi[best] };
}

/** Browser side: collect text runs to outline and JetBrains Mono usage. */
function collectRuns() {
  const fam = (cs) =>
    cs.fontFamily
      .split(',')[0]
      .trim()
      .replace(/^["']|["']$/g, '');
  const runs = [];
  const mono = {};
  const texts = [...document.querySelectorAll('svg text')].filter(
    (t) => !t.closest('defs,clipPath,mask,pattern,symbol'),
  );
  texts.forEach((t, ti) => {
    const tcs = getComputedStyle(t);
    if (tcs.display === 'none') return;
    const nodes = [];
    const w = document.createTreeWalker(t, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) nodes.push(n);
    // Reproduce the browser's whitespace handling to map characters to indices.
    let collapsed = '';
    const owners = [];
    for (const n of nodes) {
      const cs = getComputedStyle(n.parentElement);
      const pre = /pre/.test(cs.whiteSpace);
      for (const ch of n.data) {
        let c = ch;
        if (!pre && /[\t\n\r ]/.test(c)) {
          c = ' ';
          if (collapsed.endsWith(' ') || collapsed === '') continue;
        }
        collapsed += c;
        owners.push(n.parentElement);
      }
    }
    if (!/pre/.test(tcs.whiteSpace)) {
      while (collapsed.endsWith(' ')) {
        collapsed = collapsed.slice(0, -1);
        owners.pop();
      }
    }
    const n = t.getNumberOfChars();
    if (
      fam(tcs) === 'JetBrains Mono' ||
      nodes.some(
        (x) => fam(getComputedStyle(x.parentElement)) === 'JetBrains Mono',
      )
    ) {
      for (let i = 0; i < collapsed.length; i++) {
        const cs = getComputedStyle(owners[i]);
        if (fam(cs) !== 'JetBrains Mono') continue;
        mono[cs.fontWeight] = (mono[cs.fontWeight] || '') + collapsed[i];
      }
    }
    if (
      !nodes.some((x) => fam(getComputedStyle(x.parentElement)) === 'Satoshi')
    )
      return;
    if ([...collapsed].length !== n && collapsed.length !== n) {
      console.warn('char count mismatch, kept as text:', collapsed);
      return;
    }
    // Chunks: consecutive characters with the same owner element and contiguous positions.
    const chunks = [];
    let cur = null;
    for (let i = 0; i < collapsed.length; i++) {
      const own = owners[i];
      const sp = t.getStartPositionOfChar(i);
      const contiguous =
        cur &&
        cur.owner === own &&
        Math.abs(t.getEndPositionOfChar(i - 1).x - sp.x) < 0.05 &&
        Math.abs(t.getEndPositionOfChar(i - 1).y - sp.y) < 0.05;
      if (!contiguous) {
        const cs = getComputedStyle(own);
        cur = {
          owner: own,
          text: '',
          xs: [],
          y: sp.y,
          family: fam(cs),
          stack: cs.fontFamily,
          size: parseFloat(cs.fontSize),
          weight: cs.fontWeight,
          style: cs.fontStyle,
          spacing:
            cs.letterSpacing === 'normal'
              ? 0
              : parseFloat(cs.letterSpacing) || 0,
          numeric: cs.fontVariantNumeric,
          features: cs.fontFeatureSettings,
          fill: cs.fill,
          fillOpacity: cs.fillOpacity,
          cls: own === t ? null : own.getAttribute('class'),
        };
        chunks.push(cur);
      }
      cur.text += collapsed[i];
      cur.xs.push(sp.x);
      cur.end = t.getEndPositionOfChar(i).x;
    }
    t.setAttribute('data-run', String(ti));
    runs.push({
      id: ti,
      cls: t.getAttribute('class'),
      transform: t.getAttribute('transform'),
      label: collapsed,
      chunks: chunks.map(({ owner, ...c }) => c),
    });
  });
  return { runs, mono };
}

/** Browser side: swap the outlined runs in and rebuild fonts; returns the serialized SVG. */
function applyOutlines({ repl, glyphDefs, monoCss }) {
  const svg = document.querySelector('svg');
  for (const [id, markup] of Object.entries(repl)) {
    const t = svg.querySelector(`text[data-run="${id}"]`);
    if (!t) continue;
    const tpl = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    tpl.innerHTML = markup;
    t.replaceWith(...tpl.childNodes);
  }
  svg
    .querySelectorAll('[data-run]')
    .forEach((e) => e.removeAttribute('data-run'));
  // Fonts: drop old JetBrains Mono faces, add the fresh subsets.
  const styles = [...svg.querySelectorAll('style')];
  for (const s of styles)
    s.textContent = s.textContent.replace(
      /@font-face\s*{[^}]*JetBrains Mono[^}]*}\s*/g,
      '',
    );
  const style =
    styles[0] ??
    svg.insertBefore(
      document.createElementNS('http://www.w3.org/2000/svg', 'style'),
      svg.firstChild,
    );
  style.textContent =
    style.textContent.replace('/*@fonts*/', '') +
    (monoCss ? `/* JetBrains Mono (SIL OFL 1.1), subset */\n${monoCss}` : '');
  if (glyphDefs) {
    const d = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    d.setAttribute('id', 'satoshi-glyphs');
    d.innerHTML = glyphDefs;
    svg.insertBefore(d, style.nextSibling);
  }
  return new XMLSerializer().serializeToString(svg);
}

const FEATURE_MAP = {
  'tabular-nums': 'tnum',
  'proportional-nums': 'pnum',
  'lining-nums': 'lnum',
  'oldstyle-nums': 'onum',
  'slashed-zero': 'zero',
  'diagonal-fractions': 'frac',
  ordinal: 'ordn',
};
function featuresFor(chunk) {
  const f = [];
  if (chunk.numeric && chunk.numeric !== 'normal')
    for (const v of chunk.numeric.split(/\s+/))
      if (FEATURE_MAP[v]) f.push(new hb.Feature(FEATURE_MAP[v], 1));
  if (chunk.features && chunk.features !== 'normal') {
    for (const part of chunk.features.split(',')) {
      const m = /["']([a-z0-9]{4})["']\s*(on|off|\d+)?/.exec(part.trim());
      if (m)
        f.push(
          new hb.Feature(
            m[1],
            m[2] === 'off' ? 0 : m[2] && m[2] !== 'on' ? Number(m[2]) : 1,
          ),
        );
    }
  }
  // Chrome turns optional ligatures off when letter-spacing is set.
  if (chunk.spacing)
    for (const tag of ['liga', 'clig', 'dlig', 'hlig', 'calt'])
      f.push(new hb.Feature(tag, 0));
  return f;
}
const r2 = (n) => Math.round(n * 100) / 100;
const escXml = (s) =>
  s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

function outlineRuns(runs) {
  const glyphs = new Map(); // key -> path
  const repl = {};
  let outlined = 0;
  for (const run of runs) {
    let markup = '';
    for (const ch of run.chunks) {
      if (ch.family !== 'Satoshi') {
        markup += `<text x="${r2(ch.xs[0])}" y="${r2(ch.y)}" style="font-family:${escXml(ch.stack.replace(/"/g, "'"))};font-size:${ch.size}px;font-weight:${ch.weight}" fill="${ch.fill}">${escXml(ch.text)}</text>`;
        continue;
      }
      const sf = satoshiFor(ch.weight);
      const s = ch.size / sf.upem;
      const buf = new hb.Buffer();
      buf.addText(ch.text);
      buf.guessSegmentProperties();
      buf.setClusterLevel(1);
      hb.shape(sf.font, buf, featuresFor(ch));
      const infos = buf.getGlyphInfos();
      const pos = buf.getGlyphPositions();
      // UTF-16 index of each code point = cluster values from addText.
      const x0 = ch.xs[0];
      let uses = '';
      let fallback = '';
      let pen = null; // fallback pen when a cluster has several glyphs (marks)
      infos.forEach((g, i) => {
        const cl = g.cluster;
        const charStr = String.fromCodePoint(ch.text.codePointAt(cl));
        if (g.codepoint === 0 || !sf.cmap.has(charStr.codePointAt(0))) {
          if (charStr.trim()) {
            fallback += `<text x="${r2(ch.xs[cl])}" y="${r2(ch.y)}" style="font-family:${escXml(ch.stack.replace(/"/g, "'"))};font-size:${ch.size}px;font-weight:${ch.weight}" fill="${ch.fill}"${ch.fillOpacity !== '1' ? ` fill-opacity="${ch.fillOpacity}"` : ''}>${escXml(charStr)}</text>`;
          }
          return;
        }
        const first = i === 0 || infos[i - 1].cluster !== cl;
        const px = first ? ch.xs[cl] : pen;
        pen = px + pos[i].xAdvance * s;
        const key = `${sf.w}-${g.codepoint}`;
        if (!glyphs.has(key)) glyphs.set(key, sf.font.glyphToPath(g.codepoint));
        if (!glyphs.get(key)) return; // space
        const ux = Math.round((px - x0) / s + pos[i].xOffset);
        const uy = pos[i].yOffset ? ` y="${-pos[i].yOffset}"` : '';
        uses += `<use href="#sg${key}"${ux ? ` x="${ux}"` : ''}${uy}/>`;
      });
      buf.destroy?.();
      const attrs = [];
      if (ch.cls) attrs.push(`class="${escXml(ch.cls)}"`);
      attrs.push(`fill="${ch.fill}"`);
      if (ch.fillOpacity !== '1')
        attrs.push(`fill-opacity="${ch.fillOpacity}"`);
      // Satoshi ships no italic: browsers slant the upright glyphs (Skia skews by 1/4), so do we.
      const skew = ch.style === 'normal' ? 0 : 0.25 * s;
      attrs.push(
        `transform="matrix(${+s.toFixed(6)} 0 ${+skew.toFixed(6)} ${-+s.toFixed(6)} ${r2(x0)} ${r2(ch.y)})"`,
      );
      if (uses) markup += `<g ${attrs.join(' ')}>${uses}</g>`;
      markup += fallback;
      outlined++;
    }
    const outer = [`aria-label="${escXml(run.label)}"`];
    if (run.cls) outer.push(`class="${escXml(run.cls)}"`);
    if (run.transform) outer.push(`transform="${escXml(run.transform)}"`);
    repl[run.id] = `<g ${outer.join(' ')}>${markup}</g>`;
  }
  const glyphDefs = [...glyphs.entries()]
    .filter(([, d]) => d)
    .map(([k, d]) => `<path id="sg${k}" d="${d}"/>`)
    .join('');
  return { repl, glyphDefs, outlined };
}

/** Load an SVG string into a page (inline in HTML) with Satoshi available for measuring. */
async function loadInPage(page, svgText) {
  const sat = await loadSatoshi();
  const faces = sat
    ? Object.entries(sat)
        .map(
          ([w, f]) =>
            `@font-face{font-family:'Satoshi';font-weight:${w};src:url(data:font/ttf;base64,${f.buf.toString('base64')}) format('truetype')}`,
        )
        .join('\n')
    : '';
  await page.setContent(
    `<!doctype html><html><head><style>${faces}\nhtml,body{margin:0}</style></head><body>${svgText.replace(/^<\?xml[^>]*>/, '')}</body></html>`,
    { waitUntil: 'load' },
  );
  if (sat)
    await page.evaluate(
      async (ws) =>
        Promise.all(ws.map((w) => document.fonts.load(`${w} 16px Satoshi`))),
      Object.keys(sat),
    );
  await page.evaluate(() => document.fonts.ready);
}

/** Source SVG -> source SVG with fresh JetBrains Mono subsets (Satoshi untouched). */
export async function embedSourceFonts(page, svgText) {
  await loadInPage(page, svgText);
  const { mono } = await page.evaluate(collectRuns);
  const monoCss = await jetbrainsMonoCss(mono);
  return page.evaluate(applyOutlines, { repl: {}, glyphDefs: '', monoCss });
}

/** Source SVG -> display SVG (Satoshi outlined, JetBrains Mono embedded). */
export async function toDisplaySvg(page, svgText) {
  const sat = await loadSatoshi();
  await loadInPage(page, svgText);
  const { runs, mono } = await page.evaluate(collectRuns);
  const monoCss = await jetbrainsMonoCss(mono);
  const { repl, glyphDefs, outlined } = sat
    ? outlineRuns(runs)
    : { repl: {}, glyphDefs: '', outlined: 0 };
  const svg = await page.evaluate(applyOutlines, { repl, glyphDefs, monoCss });
  return { svg: `<?xml version="1.0" encoding="UTF-8"?>\n${svg}\n`, outlined };
}

/**
 * Draw an SVG file exactly as an <img> and return { png, webp } buffers at the given scale.
 * page must have viewport = SVG size and deviceScaleFactor = scale.
 */
export async function rasterize(
  page,
  svgPath,
  { width, height, scale = 1, webpQuality = 0.86 },
) {
  await page.setContent(
    `<!doctype html><html><head><style>html,body{margin:0;background:transparent}img{display:block}</style></head><body><img id="i" width="${width}" height="${height}" src="data:image/svg+xml;base64,${readFileSync(svgPath).toString('base64')}"></body></html>`,
    { waitUntil: 'load' },
  );
  await page.evaluate(() => document.getElementById('i').decode());
  const png = await page.screenshot({
    clip: { x: 0, y: 0, width, height },
    omitBackground: false,
  });
  const webp = await page.evaluate(
    async ({ width, height, scale, q }) => {
      const img = document.getElementById('i');
      const c = document.createElement('canvas');
      c.width = Math.round(width * scale);
      c.height = Math.round(height * scale);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      return c.toDataURL('image/webp', q).split(',')[1];
    },
    { width, height, scale, q: webpQuality },
  );
  return { png, webp: Buffer.from(webp, 'base64') };
}

export function svgSize(svgText) {
  const m = /<svg[^>]*\swidth="([\d.]+)"[^>]*\sheight="([\d.]+)"/.exec(svgText);
  return m
    ? { width: Math.round(+m[1]), height: Math.round(+m[2]) }
    : { width: 1440, height: 900 };
}
export const readText = (p) => readFileSync(p, 'utf8');
