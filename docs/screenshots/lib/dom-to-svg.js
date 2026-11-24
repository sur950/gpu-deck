/**
 * DOM -> pure-vector SVG converter for GPU Deck screenshots. Runs inside the page (injected by
 * capture.mjs with page.addScriptTag) and exposes window.__gpuDeckDomToSvg(options).
 *
 * The output has no <foreignObject>, no scripts and no external references, so GitHub's README
 * renderer and plain <img> tags display it. What it produces:
 *   - every box (backgrounds, gradients, borders, radii, box shadows) as <rect>/<path>;
 *   - every text run as <text> at its measured baseline, one element per text node and line;
 *   - inline SVG (lucide icons, recharts charts, the logo) copied with computed styles inlined;
 *   - <img>/<canvas> as data-URI <image>;
 *   - overflow clipping as <clipPath>, opacity stacking contexts as <g opacity>;
 *   - colours resolved to literal values; colours that equal a theme token (--brand, --card, ...)
 *     are written as classes (f-<token> for fill, s-<token> for stroke) defined once in <style>,
 *     so recolouring a token is a one-line edit.
 * Invisible nodes (display:none, visibility:hidden, opacity:0, zero-size, clipped away or outside
 * the viewport) are dropped.
 *
 * Returns { svg, fonts: { "<family>|<weight>|<style>": "chars" }, stats }. Fonts are not embedded
 * here: capture.mjs embeds the ones licensed for it and render.mjs outlines the rest.
 */
(() => {
  const SVGNS = 'http://www.w3.org/2000/svg';

  // ------------------------------------------------------------------ colours
  const cnv = document.createElement('canvas');
  cnv.width = cnv.height = 1;
  const c2d = cnv.getContext('2d', { willReadFrequently: true });
  const colorCache = new Map();

  function splitTop(str, sep = ',') {
    const out = [];
    let depth = 0;
    let cur = '';
    for (const ch of str) {
      if (ch === '(') depth++;
      else if (ch === ')') depth--;
      if (depth === 0 && (sep === ' ' ? /\s/.test(ch) : ch === sep)) {
        if (cur.trim()) out.push(cur.trim());
        cur = '';
      } else cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  }

  /** Any CSS colour -> {r,g,b,a} in sRGB bytes, or null when fully transparent / none. */
  function parseColor(str) {
    if (
      !str ||
      str === 'none' ||
      str === 'transparent' ||
      str.startsWith('url(')
    )
      return null;
    if (colorCache.has(str)) return colorCache.get(str);
    let res = null;
    const m =
      /^rgba?\((\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)(?:,\s*([\d.]+))?\)$/.exec(
        str,
      );
    if (m) {
      res = {
        r: Math.round(+m[1]),
        g: Math.round(+m[2]),
        b: Math.round(+m[3]),
        a: m[4] === undefined ? 1 : +m[4],
      };
    } else {
      // color(srgb ..), oklab(..), oklch(..), lab(..), color-mix results: take alpha from the
      // "/ a" part and let the canvas convert the opaque colour to sRGB bytes.
      if (!CSS.supports('color', str)) {
        colorCache.set(str, null);
        return null;
      }
      let a = 1;
      let opaque = str;
      const am = /\/\s*([\d.]+)(%?)\s*\)$/.exec(str);
      if (am) {
        a = am[2] ? +am[1] / 100 : +am[1];
        opaque = str.slice(0, am.index).trimEnd() + ')';
      }
      c2d.clearRect(0, 0, 1, 1);
      c2d.fillStyle = '#000';
      c2d.fillStyle = opaque;
      c2d.fillRect(0, 0, 1, 1);
      const d = c2d.getImageData(0, 0, 1, 1).data;
      res = { r: d[0], g: d[1], b: d[2], a };
    }
    if (res && res.a <= 0.002) res = null;
    colorCache.set(str, res);
    return res;
  }
  const hex2 = (n) => n.toString(16).padStart(2, '0');
  const toHex = (c) => `#${hex2(c.r)}${hex2(c.g)}${hex2(c.b)}`;
  const rnd = (n, p = 2) => {
    const f = 10 ** p;
    const v = Math.round(n * f) / f;
    return Object.is(v, -0) ? 0 : v;
  };
  const num = (s) => parseFloat(s) || 0;

  // ------------------------------------------------------------------ theme tokens
  // Order = preference when several tokens resolve to the same colour.
  const TOKEN_ORDER = [
    'brand',
    'brand-text',
    'brand-strong',
    'background',
    'foreground',
    'card',
    'card-foreground',
    'popover',
    'popover-foreground',
    'surface',
    'muted',
    'muted-foreground',
    'secondary',
    'secondary-foreground',
    'accent',
    'accent-foreground',
    'border',
    'input',
    'primary',
    'primary-foreground',
    'ring',
    'success',
    'warning',
    'destructive',
    'error',
    'info',
    'chart-1',
    'chart-2',
    'chart-3',
    'chart-4',
    'chart-5',
    'sidebar',
    'sidebar-foreground',
  ];
  function readTokens() {
    const names = new Set();
    for (const ss of document.styleSheets) {
      let rules;
      try {
        rules = ss.cssRules;
      } catch {
        continue;
      }
      const walk = (list) => {
        for (const r of list) {
          if (r.cssRules && !r.style) walk(r.cssRules);
          if (!r.style) continue;
          for (let i = 0; i < r.style.length; i++) {
            const p = r.style[i];
            if (p.startsWith('--')) names.add(p);
          }
          if (r.cssRules) walk(r.cssRules);
        }
      };
      walk(rules);
    }
    const root = getComputedStyle(document.documentElement);
    const byKey = new Map();
    const ordered = [...names]
      .map((n) => n.slice(2))
      .sort((a, b) => {
        const ia = TOKEN_ORDER.indexOf(a);
        const ib = TOKEN_ORDER.indexOf(b);
        return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib) || a.localeCompare(b);
      });
    for (const name of ordered) {
      if (
        !/^[a-z][a-z0-9-]*$/.test(name) ||
        /^(tw|font|text|radius|spacing|shadow|ease|animate|blur|breakpoint|container|leading|tracking|default)/.test(
          name,
        )
      )
        continue;
      const v = root.getPropertyValue('--' + name).trim();
      if (!v) continue;
      let c = null;
      const trip = /^(\d+)\s+(\d+)\s+(\d+)$/.exec(v);
      if (trip) c = { r: +trip[1], g: +trip[2], b: +trip[3], a: 1 };
      else if (/^(#|rgb|hsl|oklch|oklab|lab|lch|color\()/.test(v))
        c = parseColor(v);
      if (!c || c.a < 1) continue;
      const key = toHex(c);
      if (!byKey.has(key)) byKey.set(key, name);
    }
    return byKey;
  }

  // ------------------------------------------------------------------ output state
  let tokens = new Map();
  const usedTokens = new Map(); // "f-brand" -> "fill:#..."
  const defs = [];
  let idSeq = 0;
  const nid = (p) => `${p}${(idSeq++).toString(36)}`;
  const fonts = {};
  const stats = { boxes: 0, texts: 0, svgs: 0, images: 0, dropped: 0 };
  const esc = (s) =>
    String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

  /** Paint attributes for a colour: class for theme tokens, literal otherwise. */
  function paint(kind, color, attrs) {
    const prop = kind === 'f' ? 'fill' : 'stroke';
    if (!color) {
      attrs[prop] = 'none';
      return attrs;
    }
    const hex = toHex(color);
    const tok = tokens.get(hex);
    if (tok) {
      const cls = `${kind}-${tok}`;
      usedTokens.set(cls, `${prop}:${hex}`);
      attrs.class = attrs.class ? `${attrs.class} ${cls}` : cls;
    } else attrs[prop] = hex;
    if (color.a < 1) attrs[`${prop}-opacity`] = rnd(color.a, 3);
    return attrs;
  }
  function el(tag, attrs, inner) {
    let s = `<${tag}`;
    for (const [k, v] of Object.entries(attrs))
      if (v !== undefined && v !== null && v !== '') s += ` ${k}="${esc(v)}"`;
    return inner === undefined ? `${s}/>` : `${s}>${inner}</${tag}>`;
  }

  // ------------------------------------------------------------------ geometry
  function radii(cs, w, h) {
    const corner = (v) => {
      const p = v.split(' ');
      const rx = p[0].endsWith('%') ? (num(p[0]) / 100) * w : num(p[0]);
      const ry = p[1]
        ? p[1].endsWith('%')
          ? (num(p[1]) / 100) * h
          : num(p[1])
        : p[0].endsWith('%')
          ? (num(p[0]) / 100) * h
          : rx;
      return [rx, ry];
    };
    let r = [
      corner(cs.borderTopLeftRadius),
      corner(cs.borderTopRightRadius),
      corner(cs.borderBottomRightRadius),
      corner(cs.borderBottomLeftRadius),
    ];
    // CSS: scale all radii down when adjacent radii overflow a side.
    const f = Math.min(
      1,
      w / (r[0][0] + r[1][0] || 1),
      w / (r[3][0] + r[2][0] || 1),
      h / (r[0][1] + r[3][1] || 1),
      h / (r[1][1] + r[2][1] || 1),
    );
    if (f < 1) r = r.map(([a, b]) => [a * f, b * f]);
    return r;
  }
  const zeroRadii = (r) => r.every(([a, b]) => a < 0.01 && b < 0.01);
  function insetRadii(r, t, ri, b, l) {
    return [
      [Math.max(0, r[0][0] - l), Math.max(0, r[0][1] - t)],
      [Math.max(0, r[1][0] - ri), Math.max(0, r[1][1] - t)],
      [Math.max(0, r[2][0] - ri), Math.max(0, r[2][1] - b)],
      [Math.max(0, r[3][0] - l), Math.max(0, r[3][1] - b)],
    ];
  }
  /** Rounded-rect path data (or null if the box is a plain/uniform rect). */
  function shapeAttrs(x, y, w, h, r) {
    if (w <= 0 || h <= 0) return null;
    if (!r || zeroRadii(r))
      return {
        tag: 'rect',
        a: { x: rnd(x), y: rnd(y), width: rnd(w), height: rnd(h) },
      };
    const [a, b, c, d] = r;
    if (
      r.every(
        ([rx, ry]) => Math.abs(rx - a[0]) < 0.01 && Math.abs(ry - a[0]) < 0.01,
      )
    ) {
      return {
        tag: 'rect',
        a: {
          x: rnd(x),
          y: rnd(y),
          width: rnd(w),
          height: rnd(h),
          rx: rnd(a[0]),
        },
      };
    }
    return { tag: 'path', a: { d: roundedPath(x, y, w, h, r) } };
  }
  function roundedPath(x, y, w, h, r) {
    const [a, b, c, d] = r;
    const R = rnd;
    return (
      `M${R(x + a[0])} ${R(y)}H${R(x + w - b[0])}` +
      (b[0] || b[1]
        ? `A${R(b[0])} ${R(b[1])} 0 0 1 ${R(x + w)} ${R(y + b[1])}`
        : '') +
      `V${R(y + h - c[1])}` +
      (c[0] || c[1]
        ? `A${R(c[0])} ${R(c[1])} 0 0 1 ${R(x + w - c[0])} ${R(y + h)}`
        : '') +
      `H${R(x + d[0])}` +
      (d[0] || d[1]
        ? `A${R(d[0])} ${R(d[1])} 0 0 1 ${R(x)} ${R(y + h - d[1])}`
        : '') +
      `V${R(y + a[1])}` +
      (a[0] || a[1]
        ? `A${R(a[0])} ${R(a[1])} 0 0 1 ${R(x + a[0])} ${R(y)}`
        : '') +
      'Z'
    );
  }
  const shapeEl = (s, extra) => el(s.tag, { ...s.a, ...extra });

  // ------------------------------------------------------------------ gradients
  function parseStops(parts, lengthPx) {
    const stops = [];
    for (const p of parts) {
      const toks = splitTop(p, ' ');
      const colTok = toks.find((t) => !/^-?[\d.]+(px|%)?$/.test(t));
      const posToks = toks.filter((t) => t !== colTok);
      const color = parseColor(colTok);
      for (const pt of posToks.length ? posToks : [null]) {
        let off = null;
        if (pt)
          off = pt.endsWith('%')
            ? num(pt) / 100
            : lengthPx
              ? num(pt) / lengthPx
              : 0;
        stops.push({ color, off });
      }
    }
    // Fill in missing offsets per CSS.
    if (stops.length && stops[0].off === null) stops[0].off = 0;
    if (stops.length && stops[stops.length - 1].off === null)
      stops[stops.length - 1].off = 1;
    for (let i = 1; i < stops.length; i++) {
      if (stops[i].off === null) {
        let j = i;
        while (stops[j].off === null) j++;
        const a = stops[i - 1].off;
        const b = stops[j].off;
        for (let k = i; k < j; k++)
          stops[k].off = a + ((b - a) * (k - i + 1)) / (j - i + 1);
      }
      if (stops[i].off < stops[i - 1].off) stops[i].off = stops[i - 1].off;
    }
    return stops;
  }
  function stopEls(stops, asMask) {
    if (asMask)
      stops = stops.map((s) => ({
        ...s,
        color: s.color ? { r: 255, g: 255, b: 255, a: s.color.a } : null,
      }));
    // CSS interpolates in premultiplied alpha, SVG does not: a transparent stop takes the colour of
    // its neighbours (split in two when they differ) so fades do not pass through grey.
    const out = [];
    stops.forEach((s, i) => {
      if (s.color && s.color.a > 0) return out.push(s);
      const prev = [...stops.slice(0, i)]
        .reverse()
        .find((t) => t.color && t.color.a > 0);
      const next = stops.slice(i + 1).find((t) => t.color && t.color.a > 0);
      const clear = (c) => (c ? { ...c, a: 0 } : null);
      if (prev && next && toHex(prev.color) !== toHex(next.color)) {
        out.push(
          { off: s.off, color: clear(prev.color) },
          { off: s.off, color: clear(next.color) },
        );
      } else out.push({ off: s.off, color: clear((prev ?? next)?.color) });
    });
    stops = out;
    return stops
      .map((s) => {
        const a = { offset: rnd(s.off, 4) };
        if (s.color) {
          a['stop-color'] = toHex(s.color);
          if (s.color.a < 1) a['stop-opacity'] = rnd(Math.max(0, s.color.a), 3);
        } else {
          a['stop-color'] = '#000';
          a['stop-opacity'] = 0;
        }
        return el('stop', a);
      })
      .join('');
  }
  /** One background-image layer over box (x,y,w,h) -> fill reference id, or null. */
  function gradientDef(img, x, y, w, h, asMask = false) {
    const m = /^(repeating-)?(linear|radial)-gradient\((.*)\)$/s.exec(
      img.trim(),
    );
    if (!m || m[1]) return null;
    const parts = splitTop(m[3]);
    const id = nid('g');
    if (m[2] === 'linear') {
      let angle = 180;
      if (/^(to |-?[\d.]+(deg|turn|rad))/.test(parts[0])) {
        const d = parts.shift();
        if (d.startsWith('to ')) {
          const dirs = d.slice(3).split(' ');
          const map = { top: 0, right: 90, bottom: 180, left: 270 };
          if (dirs.length === 1) angle = map[dirs[0]];
          else {
            // Corner: angle such that the gradient line is perpendicular to the other diagonal.
            const vx = dirs.includes('right') ? 1 : -1;
            const vy = dirs.includes('bottom') ? 1 : -1;
            angle = (Math.atan2(vx * h, -vy * w) * 180) / Math.PI;
          }
        } else if (d.endsWith('turn')) angle = num(d) * 360;
        else if (d.endsWith('rad')) angle = (num(d) * 180) / Math.PI;
        else angle = num(d);
      }
      const t = (angle * Math.PI) / 180;
      const sx = Math.sin(t);
      const sy = -Math.cos(t);
      const len = Math.abs(w * sx) + Math.abs(h * sy);
      const cx = x + w / 2;
      const cy = y + h / 2;
      const stops = parseStops(parts, len);
      defs.push(
        el(
          'linearGradient',
          {
            id,
            gradientUnits: 'userSpaceOnUse',
            x1: rnd(cx - (sx * len) / 2),
            y1: rnd(cy - (sy * len) / 2),
            x2: rnd(cx + (sx * len) / 2),
            y2: rnd(cy + (sy * len) / 2),
          },
          stopEls(stops, asMask),
        ),
      );
      return id;
    }
    // radial-gradient([circle|ellipse] [size] [at X Y], stops)
    let shape = 'ellipse';
    let size = 'farthest-corner';
    let px = 0.5;
    let py = 0.5;
    let explicit = null;
    if (
      !parseColor(splitTop(parts[0], ' ')[0]) ||
      /^(circle|ellipse|closest|farthest|at )/.test(parts[0])
    ) {
      const d = parts.shift();
      const [shapePart, atPart] = d.split(/\s*\bat\b\s*/);
      if (/circle/.test(shapePart)) shape = 'circle';
      const sz = shapePart.replace(/circle|ellipse/, '').trim();
      if (/closest-side|closest-corner|farthest-side|farthest-corner/.test(sz))
        size = sz;
      else if (sz)
        explicit = sz
          .split(/\s+/)
          .map((v, i) =>
            v.endsWith('%') ? (num(v) / 100) * (i ? h : w) : num(v),
          );
      if (atPart) {
        const ap = atPart.split(/\s+/);
        const pos = (v, dim) =>
          v === 'left' || v === 'top'
            ? 0
            : v === 'right' || v === 'bottom'
              ? 1
              : v === 'center'
                ? 0.5
                : v.endsWith('%')
                  ? num(v) / 100
                  : num(v) / dim;
        px = pos(ap[0], w);
        py = pos(ap[1] ?? 'center', h);
      }
    }
    const cx = x + px * w;
    const cy = y + py * h;
    const dx = Math.max(px, 1 - px) * w;
    const dy = Math.max(py, 1 - py) * h;
    let rx;
    let ry;
    if (explicit) {
      rx = explicit[0];
      ry = explicit[1] ?? explicit[0];
    } else if (shape === 'circle') {
      const closeX = Math.min(px, 1 - px) * w;
      const closeY = Math.min(py, 1 - py) * h;
      rx = ry =
        size === 'closest-side'
          ? Math.min(closeX, closeY)
          : size === 'farthest-side'
            ? Math.max(dx, dy)
            : size === 'closest-corner'
              ? Math.hypot(closeX, closeY)
              : Math.hypot(dx, dy);
    } else if (size === 'farthest-side') {
      rx = dx;
      ry = dy;
    } else if (size === 'closest-side') {
      rx = Math.min(px, 1 - px) * w;
      ry = Math.min(py, 1 - py) * h;
    } else {
      rx = dx * Math.SQRT2;
      ry = dy * Math.SQRT2;
    }
    const stops = parseStops(parts, rx);
    const gt =
      Math.abs(rx - ry) > 0.01
        ? `translate(${rnd(cx)} ${rnd(cy)}) scale(1 ${rnd(ry / rx, 4)}) translate(${rnd(-cx)} ${rnd(-cy)})`
        : undefined;
    defs.push(
      el(
        'radialGradient',
        {
          id,
          gradientUnits: 'userSpaceOnUse',
          cx: rnd(cx),
          cy: rnd(cy),
          r: rnd(rx),
          gradientTransform: gt,
        },
        stopEls(stops, asMask),
      ),
    );
    return id;
  }
  /** Background images, honouring background-size / position / repeat via <pattern>. */
  function backgroundLayers(cs, x, y, w, h, shape) {
    const out = [];
    const imgs = splitTop(cs.backgroundImage).reverse(); // last layer paints first
    const sizes = splitTop(cs.backgroundSize).reverse();
    const posX = splitTop(cs.backgroundPositionX).reverse();
    const posY = splitTop(cs.backgroundPositionY).reverse();
    const reps = splitTop(cs.backgroundRepeat).reverse();
    imgs.forEach((img, i) => {
      if (img === 'none') return;
      const size = sizes[i % sizes.length] || 'auto';
      let tw = w;
      let th = h;
      if (
        size !== 'auto' &&
        size !== 'auto auto' &&
        size !== 'cover' &&
        size !== 'contain'
      ) {
        const [a, b = 'auto'] = size.split(' ');
        tw = a === 'auto' ? w : a.endsWith('%') ? (num(a) / 100) * w : num(a);
        th = b === 'auto' ? h : b.endsWith('%') ? (num(b) / 100) * h : num(b);
      }
      const rep = reps[i % reps.length] || 'repeat';
      const tiled = (tw < w - 0.5 || th < h - 0.5) && rep !== 'no-repeat';
      const pxv = posX[i % posX.length] || '0%';
      const pyv = posY[i % posY.length] || '0%';
      const ox = pxv.endsWith('%') ? (num(pxv) / 100) * (w - tw) : num(pxv);
      const oy = pyv.endsWith('%') ? (num(pyv) / 100) * (h - th) : num(pyv);
      if (!tiled) {
        const gid = gradientDef(img, x + ox, y + oy, tw, th);
        if (gid) out.push(shapeEl(shape, { fill: `url(#${gid})` }));
        return;
      }
      const gid = gradientDef(img, 0, 0, tw, th);
      if (!gid) return;
      const pid = nid('p');
      defs.push(
        el(
          'pattern',
          {
            id: pid,
            patternUnits: 'userSpaceOnUse',
            x: rnd(x + ox),
            y: rnd(y + oy),
            width: rnd(tw),
            height: rnd(th),
          },
          el('rect', { width: rnd(tw), height: rnd(th), fill: `url(#${gid})` }),
        ),
      );
      out.push(shapeEl(shape, { fill: `url(#${pid})` }));
    });
    return out.join('');
  }

  // ------------------------------------------------------------------ shadows
  const blurFilters = new Map();
  function blurFilter(std) {
    const k = rnd(std, 2);
    if (!blurFilters.has(k)) {
      const id = nid('b');
      blurFilters.set(k, id);
      defs.push(
        el(
          'filter',
          { id, x: '-50%', y: '-50%', width: '200%', height: '200%' },
          el('feGaussianBlur', { stdDeviation: k }),
        ),
      );
    }
    return blurFilters.get(k);
  }
  function parseShadows(str) {
    if (!str || str === 'none') return [];
    return splitTop(str).map((s) => {
      const toks = splitTop(s, ' ');
      const inset = toks.includes('inset');
      const colTok = toks.find(
        (t) => t !== 'inset' && !/^-?[\d.]+(px)?$/.test(t),
      );
      const nums = toks.filter((t) => /^-?[\d.]+(px)?$/.test(t)).map(num);
      return {
        inset,
        color: parseColor(colTok),
        x: nums[0] || 0,
        y: nums[1] || 0,
        blur: nums[2] || 0,
        spread: nums[3] || 0,
      };
    });
  }
  function shadowEls(cs, x, y, w, h, r) {
    let out = '';
    const list = parseShadows(cs.boxShadow).reverse();
    for (const s of list) {
      if (!s.color) continue;
      if (!s.inset) {
        const sp = s.spread;
        const rr = r.map(([a, b]) => [a ? a + sp : 0, b ? b + sp : 0]);
        const shp = shapeAttrs(
          x + s.x - sp,
          y + s.y - sp,
          w + 2 * sp,
          h + 2 * sp,
          rr,
        );
        if (!shp) continue;
        const a = paint('f', s.color, {});
        if (s.blur > 0) a.filter = `url(#${blurFilter(s.blur / 2)})`;
        if (s.blur > 0 || s.x || s.y) {
          // CSS paints outer shadows only outside the border box (matters under translucent boxes).
          const m =
            s.blur * 2 + Math.abs(s.x) + Math.abs(s.y) + Math.abs(sp) + 2;
          const cid = nid('c');
          defs.push(
            el(
              'clipPath',
              { id: cid },
              el('path', {
                d:
                  `M${rnd(x - m)} ${rnd(y - m)}h${rnd(w + 2 * m)}v${rnd(h + 2 * m)}h${rnd(-w - 2 * m)}Z` +
                  roundedPath(x, y, w, h, r),
                'clip-rule': 'evenodd',
              }),
            ),
          );
          out += el('g', { 'clip-path': `url(#${cid})` }, shapeEl(shp, a));
          continue;
        }
        if (s.blur === 0 && !s.x && !s.y) {
          // A ring (spread only): draw as a frame so transparent boxes stay transparent.
          const inner = zeroRadii(r)
            ? `M${rnd(x)} ${rnd(y)}h${rnd(w)}v${rnd(h)}h${rnd(-w)}Z`
            : roundedPath(x, y, w, h, r);
          const outer =
            shp.tag === 'rect'
              ? roundedPath(shp.a.x, shp.a.y, shp.a.width, shp.a.height, rr)
              : shp.a.d;
          out += el('path', { d: outer + inner, 'fill-rule': 'evenodd', ...a });
        } else out += shapeEl(shp, a);
      } else if (s.blur === 0) {
        const sp = s.spread;
        const outer = roundedPath(x, y, w, h, r);
        const ir = r.map(([a, b]) => [
          Math.max(0, a - sp),
          Math.max(0, b - sp),
        ]);
        const inner = roundedPath(
          x + sp + s.x,
          y + sp + s.y,
          w - 2 * sp,
          h - 2 * sp,
          ir,
        );
        out += el('path', {
          d: outer + inner,
          'fill-rule': 'evenodd',
          ...paint('f', s.color, {}),
        });
      }
    }
    return out;
  }

  // ------------------------------------------------------------------ boxes
  function boxEls(node, cs, rect) {
    const { x, y, width: w, height: h } = rect;
    if (w <= 0 || h <= 0) return '';
    const r = radii(cs, w, h);
    let out = '';
    if (cs.boxShadow !== 'none') out += shadowEls(cs, x, y, w, h, r);
    const bt =
      cs.borderTopStyle !== 'none' && cs.borderTopStyle !== 'hidden'
        ? num(cs.borderTopWidth)
        : 0;
    const br =
      cs.borderRightStyle !== 'none' && cs.borderRightStyle !== 'hidden'
        ? num(cs.borderRightWidth)
        : 0;
    const bb =
      cs.borderBottomStyle !== 'none' && cs.borderBottomStyle !== 'hidden'
        ? num(cs.borderBottomWidth)
        : 0;
    const bl =
      cs.borderLeftStyle !== 'none' && cs.borderLeftStyle !== 'hidden'
        ? num(cs.borderLeftWidth)
        : 0;
    // background-clip: border-box (default) / padding-box / content-box
    let bx = x;
    let by = y;
    let bw = w;
    let bh = h;
    let brd = r;
    if (
      cs.backgroundClip === 'padding-box' ||
      cs.backgroundClip === 'content-box'
    ) {
      bx += bl;
      by += bt;
      bw -= bl + br;
      bh -= bt + bb;
      brd = insetRadii(r, bt, br, bb, bl);
    }
    const bg = parseColor(cs.backgroundColor);
    const shape = shapeAttrs(bx, by, bw, bh, brd);
    if (bg && shape) out += shapeEl(shape, paint('f', bg, {}));
    if (cs.backgroundImage !== 'none' && shape)
      out += backgroundLayers(cs, bx, by, bw, bh, shape);
    if (bt || br || bb || bl)
      out += borderEls(cs, x, y, w, h, r, bt, br, bb, bl);
    if (out) stats.boxes++;
    return out;
  }
  function borderEls(cs, x, y, w, h, r, bt, br, bb, bl) {
    const ct = parseColor(cs.borderTopColor);
    const cr = parseColor(cs.borderRightColor);
    const cb = parseColor(cs.borderBottomColor);
    const cl = parseColor(cs.borderLeftColor);
    const same = (a, b) =>
      (!a && !b) ||
      (a && b && a.r === b.r && a.g === b.g && a.b === b.b && a.a === b.a);
    const uniform =
      bt === br &&
      br === bb &&
      bb === bl &&
      same(ct, cr) &&
      same(cr, cb) &&
      same(cb, cl) &&
      cs.borderTopStyle === cs.borderLeftStyle &&
      cs.borderTopStyle === cs.borderRightStyle &&
      cs.borderTopStyle === cs.borderBottomStyle;
    const dashed =
      cs.borderTopStyle === 'dashed' || cs.borderTopStyle === 'dotted';
    if (uniform) {
      if (!ct) return '';
      const sw = bt;
      if (dashed) {
        const ir = r.map(([a, b]) => [
          Math.max(0, a - sw / 2),
          Math.max(0, b - sw / 2),
        ]);
        const s = shapeAttrs(x + sw / 2, y + sw / 2, w - sw, h - sw, ir);
        if (!s) return '';
        const dash =
          cs.borderTopStyle === 'dotted'
            ? `${rnd(sw)} ${rnd(sw)}`
            : `${rnd(sw * 3)} ${rnd(sw * 3)}`;
        return shapeEl(s, {
          ...paint('s', ct, { fill: 'none' }),
          'stroke-width': rnd(sw),
          'stroke-dasharray': dash,
        });
      }
      // Ring between the outer and inner border edges (exact for any radii).
      const ir = insetRadii(r, sw, sw, sw, sw);
      const inner =
        w - 2 * sw > 0 && h - 2 * sw > 0
          ? roundedPath(x + sw, y + sw, w - 2 * sw, h - 2 * sw, ir)
          : '';
      return el('path', {
        d: roundedPath(x, y, w, h, r) + inner,
        'fill-rule': 'evenodd',
        ...paint('f', ct, {}),
      });
    }
    // Per side: trapezoids are overkill for UI hairlines; rectangles along each edge, clipped to
    // the rounded outline when the box has radii.
    let out = '';
    const side = (c, sx, sy, sw, sh) => {
      if (c && sw > 0 && sh > 0)
        out += el('rect', {
          x: rnd(sx),
          y: rnd(sy),
          width: rnd(sw),
          height: rnd(sh),
          ...paint('f', c, {}),
        });
    };
    side(ct, x, y, w, bt);
    side(cb, x, y + h - bb, w, bb);
    side(cl, x, y + bt, bl, h - bt - bb);
    side(cr, x + w - br, y + bt, br, h - bt - bb);
    if (out && !zeroRadii(r)) {
      const id = nid('c');
      defs.push(
        el('clipPath', { id }, el('path', { d: roundedPath(x, y, w, h, r) })),
      );
      out = el('g', { 'clip-path': `url(#${id})` }, out);
    }
    return out;
  }

  // ------------------------------------------------------------------ text
  const metricCache = new Map();
  function fontMetrics(cs) {
    const font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    if (!metricCache.has(font)) {
      c2d.font = font;
      const m = c2d.measureText('Hxg');
      // Blink lays text out with ascent / descent rounded to whole pixels.
      metricCache.set(font, {
        asc: Math.round(m.fontBoundingBoxAscent),
        desc: Math.round(m.fontBoundingBoxDescent),
        ell: c2d.measureText('…').width,
      });
    }
    return metricCache.get(font);
  }
  function primaryFamily(cs) {
    return splitTop(cs.fontFamily)[0].replace(/^["']|["']$/g, '');
  }
  function transformText(s, tt) {
    if (tt === 'uppercase') return s.toUpperCase();
    if (tt === 'lowercase') return s.toLowerCase();
    if (tt === 'capitalize')
      return s.replace(/(^|\s)(\p{L})/gu, (m, a, b) => a + b.toUpperCase());
    return s;
  }
  function noteFont(cs, text) {
    const key = `${primaryFamily(cs)}|${cs.fontWeight}|${cs.fontStyle}`;
    fonts[key] = (fonts[key] || '') + text;
  }
  const textClasses = new Map();
  function textClass(cs) {
    const fam = cs.fontFamily;
    const parts = [
      `font-family:${fam.replace(/"/g, "'")}`,
      `font-size:${cs.fontSize}`,
      `font-weight:${cs.fontWeight}`,
    ];
    if (cs.fontStyle !== 'normal') parts.push(`font-style:${cs.fontStyle}`);
    if (cs.letterSpacing !== 'normal' && num(cs.letterSpacing))
      parts.push(`letter-spacing:${rnd(num(cs.letterSpacing), 3)}px`);
    if (cs.fontVariantNumeric !== 'normal')
      parts.push(`font-variant-numeric:${cs.fontVariantNumeric}`);
    const ffs = cs.fontFeatureSettings;
    if (ffs && ffs !== 'normal')
      parts.push(`font-feature-settings:${ffs.replace(/"/g, "'")}`);
    if (/pre/.test(cs.whiteSpace)) parts.push('white-space:pre');
    const k = parts.join(';');
    if (!textClasses.has(k))
      textClasses.set(k, `t${textClasses.size.toString(36)}`);
    return textClasses.get(k);
  }
  /** Clip box (padding box) of the nearest ellipsis container, if text there overflows. */
  function ellipsisLimit(node) {
    for (
      let e = node.parentElement;
      e && e !== document.body;
      e = e.parentElement
    ) {
      const cs = getComputedStyle(e);
      if (cs.display === 'inline') continue;
      if (cs.textOverflow === 'ellipsis' && cs.overflowX !== 'visible') {
        if (e.scrollWidth <= e.clientWidth + 0.5) return null;
        const r = e.getBoundingClientRect();
        return r.right - num(cs.borderRightWidth) - num(cs.paddingRight);
      }
      return null;
    }
    return null;
  }
  function textEls(node, cs) {
    const raw = node.data;
    if (!raw || !raw.trim()) return '';
    const color = parseColor(
      cs.webkitTextFillColor && cs.webkitTextFillColor !== cs.color
        ? cs.webkitTextFillColor
        : cs.color,
    );
    if (!color) return '';
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = [...range.getClientRects()].filter(
      (r) => r.width > 0 && r.height > 0,
    );
    if (!rects.length) return '';
    const pre = /pre/.test(cs.whiteSpace);
    const lines = [];
    const charRect = (i) => {
      range.setStart(node, i);
      range.setEnd(node, i + 1);
      const rs = range.getClientRects();
      return rs.length ? rs[rs.length - 1] : null;
    };
    const lim = ellipsisLimit(node);
    if (rects.length === 1 && lim === null) {
      lines.push({ start: 0, end: raw.length, rect: rects[0] });
    } else {
      // Assign characters to line boxes by their vertical position.
      let cur = null;
      for (let i = 0; i < raw.length; i++) {
        const cr = charRect(i);
        if (!cr || (cr.width === 0 && /\s/.test(raw[i]))) continue;
        if (lim !== null && cr.right > lim + 0.5) break;
        const mid = cr.top + cr.height / 2;
        if (!cur || mid > cur.rect.bottom || mid < cur.rect.top) {
          cur = {
            start: i,
            end: i + 1,
            rect: {
              left: cr.left,
              top: cr.top,
              bottom: cr.bottom,
              right: cr.right,
              height: cr.height,
            },
          };
          lines.push(cur);
        } else {
          cur.end = i + 1;
          cur.rect.right = Math.max(cur.rect.right, cr.right);
        }
      }
      if (lim !== null && lines.length) {
        // Drop characters until the ellipsis fits, as the browser does.
        const m = fontMetrics(cs);
        const last = lines[lines.length - 1];
        while (last.end > last.start) {
          const cr = charRect(last.end - 1);
          if (cr && cr.right + m.ell <= lim + 0.5) break;
          last.end--;
        }
        last.ellipsis = true;
      }
    }
    const m = fontMetrics(cs);
    const cls = textClass(cs);
    let out = '';
    const decoLine =
      cs.textDecorationLine && cs.textDecorationLine.includes('underline');
    for (const ln of lines) {
      let s = raw.slice(ln.start, ln.end);
      let lead = 0;
      if (!pre) {
        const t = s.replace(/[\t\n\r ]+/g, ' ');
        lead = t.length - t.trimStart().length;
        s = t.trim();
      }
      if (ln.ellipsis) s = s.replace(/\s+$/, '') + '…';
      if (!s) continue;
      // Start x = left edge of the first visible character.
      let x = ln.rect.left;
      if (lead) {
        let i = ln.start;
        while (i < ln.end && /\s/.test(raw[i])) i++;
        const cr = charRect(i);
        if (cr) x = cr.left;
      }
      const top = ln.rect.top;
      const y =
        top + m.asc + Math.max(0, (ln.rect.height - (m.asc + m.desc)) / 2);
      s = transformText(s, cs.textTransform);
      noteFont(cs, s);
      out += el(
        'text',
        paint('f', color, { x: rnd(x), y: rnd(y), class: cls }),
        esc(s),
      );
      if (decoLine) {
        const fs = num(cs.fontSize);
        out += el('rect', {
          x: rnd(x),
          y: rnd(y + fs * 0.12),
          width: rnd(ln.rect.right - x),
          height: rnd(Math.max(1, fs / 15)),
          ...paint('f', parseColor(cs.textDecorationColor) || color, {}),
        });
      }
      stats.texts++;
    }
    return out;
  }
  function inputTextEls(inp, cs, rect) {
    let value = inp.value;
    let color = parseColor(cs.color);
    let pcs = cs;
    if (!value && inp.placeholder) {
      value = inp.placeholder;
      pcs = getComputedStyle(inp, '::placeholder');
      color = parseColor(pcs.color);
    }
    if (
      !value ||
      !color ||
      inp.type === 'hidden' ||
      inp.type === 'checkbox' ||
      inp.type === 'radio'
    )
      return '';
    if (inp.type === 'password' && inp.value) value = '•'.repeat(value.length);
    const left =
      rect.left +
      num(cs.borderLeftWidth) +
      num(cs.paddingLeft) -
      inp.scrollLeft;
    const cTop = rect.top + num(cs.borderTopWidth) + num(cs.paddingTop);
    const cH =
      rect.height -
      num(cs.borderTopWidth) -
      num(cs.borderBottomWidth) -
      num(cs.paddingTop) -
      num(cs.paddingBottom);
    const m = fontMetrics(pcs);
    const lines = inp.tagName === 'TEXTAREA' ? value.split('\n') : [value];
    const lh = cs.lineHeight === 'normal' ? m.asc + m.desc : num(cs.lineHeight);
    let out = '';
    const cls = textClass(pcs);
    lines.forEach((line, i) => {
      if (!line) return;
      const y =
        inp.tagName === 'TEXTAREA'
          ? cTop + i * lh + (lh - (m.asc + m.desc)) / 2 + m.asc - inp.scrollTop
          : cTop + (cH - (m.asc + m.desc)) / 2 + m.asc;
      noteFont(pcs, line);
      out += el(
        'text',
        paint('f', color, {
          x: rnd(left),
          y: rnd(y),
          class: cls,
          style: 'white-space:pre',
        }),
        esc(line),
      );
    });
    stats.texts++;
    // Clip to the content box.
    const id = nid('c');
    const cl = rect.left + num(cs.borderLeftWidth) + num(cs.paddingLeft);
    const cr = rect.right - num(cs.borderRightWidth) - num(cs.paddingRight);
    defs.push(
      el(
        'clipPath',
        { id },
        el('rect', {
          x: rnd(cl),
          y: rnd(rect.top),
          width: rnd(cr - cl),
          height: rnd(rect.height),
        }),
      ),
    );
    return el('g', { 'clip-path': `url(#${id})` }, out);
  }

  // ------------------------------------------------------------------ inline SVG
  const GEOM_ATTRS = [
    'd',
    'x',
    'y',
    'width',
    'height',
    'cx',
    'cy',
    'r',
    'rx',
    'ry',
    'x1',
    'y1',
    'x2',
    'y2',
    'points',
    'transform',
    'gradientUnits',
    'gradientTransform',
    'offset',
    'patternUnits',
    'patternContentUnits',
    'patternTransform',
    'clipPathUnits',
    'maskUnits',
    'maskContentUnits',
    'viewBox',
    'fx',
    'fy',
    'fr',
    'spreadMethod',
    'preserveAspectRatio',
    'pathLength',
    'vector-effect',
  ];
  const SHAPES = new Set([
    'path',
    'rect',
    'circle',
    'ellipse',
    'line',
    'polyline',
    'polygon',
    'use',
  ]);
  const CONTAINERS = new Set([
    'g',
    'svg',
    'defs',
    'clipPath',
    'mask',
    'pattern',
    'linearGradient',
    'radialGradient',
    'symbol',
    'a',
  ]);
  function copySvg(svg, rect, cs, collectText) {
    const prefix = nid('s') + '-';
    const idMap = new Map();
    svg.querySelectorAll('[id]').forEach((e) => idMap.set(e.id, prefix + e.id));
    const fixUrl = (v) =>
      v &&
      v.replace(/url\(["']?#([^"')]+)["']?\)/g, (m, id) =>
        idMap.has(id) ? `url(#${idMap.get(id)})` : 'none',
      );
    const texts = [];
    const walk = (node, inDefs) => {
      let out = '';
      for (const ch of node.children) {
        const tag = ch.tagName;
        if (tag === 'text') {
          if (!inDefs) texts.push(ch);
          continue;
        }
        if (
          tag === 'foreignObject' ||
          tag === 'title' ||
          tag === 'desc' ||
          tag === 'style' ||
          tag === 'script'
        )
          continue;
        const ccs = getComputedStyle(ch);
        const isDefLike =
          inDefs ||
          tag === 'defs' ||
          tag === 'clipPath' ||
          tag === 'mask' ||
          tag === 'linearGradient' ||
          tag === 'radialGradient' ||
          tag === 'pattern' ||
          tag === 'symbol';
        if (
          !isDefLike &&
          (ccs.display === 'none' ||
            ccs.visibility === 'hidden' ||
            num(ccs.opacity) === 0)
        ) {
          stats.dropped++;
          continue;
        }
        const a = {};
        if (ch.id) a.id = idMap.get(ch.id);
        for (const g of GEOM_ATTRS)
          if (ch.hasAttribute(g)) a[g] = ch.getAttribute(g);
        const href = ch.getAttribute('href') || ch.getAttribute('xlink:href');
        if (href && href.startsWith('#'))
          a.href = idMap.has(href.slice(1))
            ? `#${idMap.get(href.slice(1))}`
            : undefined;
        if (tag === 'stop') {
          const sc = parseColor(ccs.stopColor);
          a['stop-color'] = sc ? toHex(sc) : '#000';
          const so =
            (sc ? sc.a : 0) *
            num(ccs.stopOpacity === '' ? '1' : ccs.stopOpacity);
          if (so < 1) a['stop-opacity'] = rnd(so, 3);
          out += el('stop', a);
          continue;
        }
        if (num(ccs.opacity) < 1) a.opacity = rnd(num(ccs.opacity), 3);
        if (ccs.clipPath && ccs.clipPath !== 'none')
          a['clip-path'] = fixUrl(ccs.clipPath);
        if (ccs.mask && ccs.mask !== 'none' && ccs.mask.startsWith('url'))
          a.mask = fixUrl(ccs.mask);
        if (ccs.transform && ccs.transform !== 'none' && !a.transform)
          a.transform = ccs.transform.replace(/,/g, ' ');
        if (SHAPES.has(tag)) {
          if (ccs.fill.startsWith('url')) a.fill = fixUrl(ccs.fill);
          else paint('f', parseColor(ccs.fill), a);
          if (num(ccs.fillOpacity) < 1)
            a['fill-opacity'] = rnd(
              num(ccs.fillOpacity) * (+a['fill-opacity'] || 1),
              3,
            );
          if (ccs.fillRule === 'evenodd') a['fill-rule'] = 'evenodd';
          if (ccs.stroke !== 'none') {
            if (ccs.stroke.startsWith('url')) a.stroke = fixUrl(ccs.stroke);
            else {
              const sc = parseColor(ccs.stroke);
              if (sc) paint('s', sc, a);
            }
            if (
              (a.stroke !== 'none' && a.stroke !== undefined) ||
              (a.class || '').includes('s-')
            ) {
              a['stroke-width'] = rnd(num(ccs.strokeWidth), 3);
              if (num(ccs.strokeOpacity) < 1)
                a['stroke-opacity'] = rnd(
                  num(ccs.strokeOpacity) * (+a['stroke-opacity'] || 1),
                  3,
                );
              if (ccs.strokeLinecap !== 'butt')
                a['stroke-linecap'] = ccs.strokeLinecap;
              if (ccs.strokeLinejoin !== 'miter')
                a['stroke-linejoin'] = ccs.strokeLinejoin;
              if (ccs.strokeDasharray !== 'none')
                a['stroke-dasharray'] = ccs.strokeDasharray
                  .replace(/px/g, '')
                  .replace(/,/g, ' ');
              if (num(ccs.strokeDashoffset))
                a['stroke-dashoffset'] = num(ccs.strokeDashoffset);
              if (ccs.vectorEffect === 'non-scaling-stroke')
                a['vector-effect'] = 'non-scaling-stroke';
            }
          }
          if (a.stroke === 'none') delete a.stroke;
          if (tag === 'path' && !a.d) continue;
          out += el(tag, a);
        } else if (CONTAINERS.has(tag)) {
          const t = tag === 'a' ? 'g' : tag;
          if (t === 'svg') {
            // nested svg: keep viewport attributes
            for (const g of [
              'x',
              'y',
              'width',
              'height',
              'viewBox',
              'preserveAspectRatio',
            ])
              if (ch.hasAttribute(g)) a[g] = ch.getAttribute(g);
          }
          const inner = walk(ch, isDefLike);
          if (inner || isDefLike) out += el(t, a, inner);
        } else if (tag === 'image') {
          // Rare in the app; keep data URIs only.
          if (href && href.startsWith('data:'))
            out += el('image', { ...a, href });
        }
      }
      return out;
    };
    const body = walk(svg, false);
    // Chrome pixel-snaps replaced content (inline <svg>) to whole device pixels.
    rect = {
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      width: Math.round(rect.left + rect.width) - Math.round(rect.left),
      height: Math.round(rect.top + rect.height) - Math.round(rect.top),
    };
    // Viewport transform: viewBox + preserveAspectRatio (default xMidYMid meet).
    const vb =
      svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width
        ? svg.viewBox.baseVal
        : null;
    let tf = `translate(${rnd(rect.left)} ${rnd(rect.top)})`;
    if (vb) {
      const par = (
        svg.getAttribute('preserveAspectRatio') || 'xMidYMid meet'
      ).split(' ');
      let sx = rect.width / vb.width;
      let sy = rect.height / vb.height;
      let tx = 0;
      let ty = 0;
      if (par[0] !== 'none') {
        const s = par[1] === 'slice' ? Math.max(sx, sy) : Math.min(sx, sy);
        const al = par[0];
        tx = al.includes('xMid')
          ? (rect.width - vb.width * s) / 2
          : al.includes('xMax')
            ? rect.width - vb.width * s
            : 0;
        ty = al.includes('YMid')
          ? (rect.height - vb.height * s) / 2
          : al.includes('YMax')
            ? rect.height - vb.height * s
            : 0;
        sx = sy = s;
      }
      tf =
        `translate(${rnd(rect.left + tx - vb.x * sx)} ${rnd(rect.top + ty - vb.y * sy)})` +
        (Math.abs(sx - 1) > 1e-4 || Math.abs(sy - 1) > 1e-4
          ? ` scale(${rnd(sx, 5)}${Math.abs(sx - sy) > 1e-5 ? ' ' + rnd(sy, 5) : ''})`
          : '');
    }
    // CSS transform on the <svg> element itself (e.g. a rotated chevron).
    if (cs.transform && cs.transform !== 'none') {
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const mm = /matrix\(([^)]+)\)/.exec(cs.transform);
      if (mm) {
        const [a, b, c, d] = mm[1].split(',').map(Number);
        tf =
          `translate(${rnd(cx)} ${rnd(cy)}) matrix(${rnd(a, 4)} ${rnd(b, 4)} ${rnd(c, 4)} ${rnd(d, 4)} 0 0) translate(${rnd(-cx)} ${rnd(-cy)}) ` +
          tf;
      }
    }
    let out = '';
    const ov = cs.overflow;
    let clipAttr;
    if (ov !== 'visible' && !(ov === '' || ov === 'visible visible')) {
      const id = nid('c');
      defs.push(
        el(
          'clipPath',
          { id },
          el('rect', {
            x: rnd(rect.left),
            y: rnd(rect.top),
            width: rnd(rect.width),
            height: rnd(rect.height),
          }),
        ),
      );
      clipAttr = `url(#${id})`;
    }
    // The clip is in page coordinates, so it wraps the transformed group (not on the same <g>).
    if (body)
      out += clipAttr
        ? el('g', { 'clip-path': clipAttr }, el('g', { transform: tf }, body))
        : el('g', { transform: tf }, body);
    stats.svgs++;
    // SVG text: re-emit at measured positions in page coordinates.
    for (const t of texts) collectText(t);
    return out;
  }
  function svgTextEls(t) {
    // Each <text> may contain <tspan>s; walk its text nodes and emit one <text> per run of
    // characters that share a line, using the browser's glyph positions.
    let out = '';
    const n = t.getNumberOfChars();
    if (!n) return '';
    const ctm = t.getScreenCTM();
    if (!ctm) return '';
    const sc = Math.hypot(ctm.a, ctm.b);
    let idx = 0;
    const walker = document.createTreeWalker(t, NodeFilter.SHOW_TEXT);
    for (let tn = walker.nextNode(); tn; tn = walker.nextNode()) {
      const pe = tn.parentElement;
      const cs = getComputedStyle(pe);
      const len = tn.data.length;
      const visible =
        cs.display !== 'none' &&
        cs.visibility !== 'hidden' &&
        num(cs.opacity) > 0;
      const color = parseColor(cs.fill);
      // Characters after whitespace collapsing are what getNumberOfChars counts; map them.
      const collapsed = tn.data.replace(/[\t\n\r ]+/g, ' ');
      let runStart = null;
      let runText = '';
      let runY = null;
      const flush = () => {
        if (runStart && runText.trim() && visible && color) {
          const fs = num(cs.fontSize) * sc;
          const fake = {
            fontFamily: cs.fontFamily,
            fontSize: `${rnd(fs, 3)}px`,
            fontWeight: cs.fontWeight,
            fontStyle: cs.fontStyle,
            letterSpacing: cs.letterSpacing,
            fontVariantNumeric: cs.fontVariantNumeric,
            fontFeatureSettings: cs.fontFeatureSettings,
            whiteSpace: 'pre',
          };
          const s = runText.replace(/\s+$/, '');
          const lead = s.length - s.trimStart().length;
          noteFont(fake, s.trimStart());
          const p = runStart;
          out += el(
            'text',
            paint('f', color, {
              x: rnd(p.x + lead * 0),
              y: rnd(p.y),
              class: textClass(fake),
            }),
            esc(s.trimStart()),
          );
          stats.texts++;
        }
        runStart = null;
        runText = '';
      };
      for (let i = 0; i < collapsed.length && idx < n; i++, idx++) {
        let pt;
        try {
          pt = t.getStartPositionOfChar(idx);
        } catch {
          break;
        }
        const sp = new DOMPoint(pt.x, pt.y).matrixTransform(ctm);
        if (runY !== null && Math.abs(sp.y - runY) > 0.5) flush();
        if (!runStart) {
          if (/\s/.test(collapsed[i])) continue;
          runStart = { x: sp.x, y: sp.y };
          runY = sp.y;
        }
        runText += collapsed[i];
      }
      flush();
      runY = null;
    }
    return out;
  }

  // ------------------------------------------------------------------ images
  function imageEl(node, rect) {
    let href = null;
    try {
      if (node.tagName === 'CANVAS') href = node.toDataURL('image/png');
      else if (node.complete && node.naturalWidth) {
        const c = document.createElement('canvas');
        c.width = node.naturalWidth;
        c.height = node.naturalHeight;
        c.getContext('2d').drawImage(node, 0, 0);
        href = c.toDataURL('image/png');
      }
    } catch {
      href = null;
    }
    if (!href) return '';
    stats.images++;
    const fit = getComputedStyle(node).objectFit;
    const par =
      fit === 'cover'
        ? 'xMidYMid slice'
        : fit === 'fill' || node.tagName === 'CANVAS'
          ? 'none'
          : 'xMidYMid meet';
    return el('image', {
      x: rnd(rect.left),
      y: rnd(rect.top),
      width: rnd(rect.width),
      height: rnd(rect.height),
      href,
      preserveAspectRatio: par,
    });
  }

  // ------------------------------------------------------------------ pseudo-elements
  const PSEUDO_PROPS = [
    'display',
    'position',
    'top',
    'right',
    'bottom',
    'left',
    'width',
    'height',
    'min-width',
    'min-height',
    'margin-top',
    'margin-right',
    'margin-bottom',
    'margin-left',
    'padding-top',
    'padding-right',
    'padding-bottom',
    'padding-left',
    'background-color',
    'background-image',
    'background-size',
    'background-position',
    'background-repeat',
    'border-top-width',
    'border-right-width',
    'border-bottom-width',
    'border-left-width',
    'border-top-style',
    'border-right-style',
    'border-bottom-style',
    'border-left-style',
    'border-top-color',
    'border-right-color',
    'border-bottom-color',
    'border-left-color',
    'border-top-left-radius',
    'border-top-right-radius',
    'border-bottom-right-radius',
    'border-bottom-left-radius',
    'opacity',
    'transform',
    'transform-origin',
    'z-index',
    'color',
    'font-family',
    'font-size',
    'font-weight',
    'font-style',
    'letter-spacing',
    'line-height',
    'box-shadow',
    'box-sizing',
    'flex-shrink',
    'flex-grow',
    'align-self',
    'vertical-align',
    'white-space',
    'text-transform',
    'pointer-events',
    'inset',
  ];
  /** Replace visible ::before/::after with real spans so they get boxes and rects. */
  function materializePseudo() {
    const style = document.createElement('style');
    style.textContent =
      '[data-shot-pb]::before{content:none!important}[data-shot-pa]::after{content:none!important}';
    document.head.appendChild(style);
    for (const host of document.body.querySelectorAll('*')) {
      if (
        host.namespaceURI === SVGNS ||
        /^(INPUT|IMG|TEXTAREA|SELECT|BR|HR|IFRAME|CANVAS)$/.test(host.tagName)
      )
        continue;
      for (const which of ['before', 'after']) {
        const ps = getComputedStyle(host, `::${which}`);
        const content = ps.content;
        if (
          !content ||
          content === 'none' ||
          content === 'normal' ||
          ps.display === 'none'
        )
          continue;
        if (num(ps.opacity) === 0 || ps.visibility === 'hidden') continue;
        const textM = /^"(.*)"$/s.exec(content);
        const text = textM
          ? textM[1].replace(/\\([0-9a-fA-F]{1,6}) ?/g, (m, h) =>
              String.fromCodePoint(parseInt(h, 16)),
            )
          : '';
        const hasPaint =
          parseColor(ps.backgroundColor) ||
          ps.backgroundImage !== 'none' ||
          ps.boxShadow !== 'none' ||
          num(ps.borderTopWidth) ||
          num(ps.borderBottomWidth) ||
          num(ps.borderLeftWidth) ||
          num(ps.borderRightWidth) ||
          text.trim();
        if (!hasPaint) continue;
        const span = document.createElement('span');
        span.setAttribute('data-shot-pseudo', which);
        let css = '';
        for (const p of PSEUDO_PROPS) {
          const v = ps.getPropertyValue(p);
          if (v) css += `${p}:${v};`;
        }
        span.style.cssText = css + 'transition:none;animation:none;';
        span.textContent = text;
        if (which === 'before') host.insertBefore(span, host.firstChild);
        else host.appendChild(span);
        host.setAttribute(
          which === 'before' ? 'data-shot-pb' : 'data-shot-pa',
          '',
        );
      }
    }
  }

  // ------------------------------------------------------------------ painting order
  function createsContext(cs, node) {
    if (cs.position === 'fixed' || cs.position === 'sticky') return true;
    if (
      cs.zIndex !== 'auto' &&
      (cs.position !== 'static' || isFlexOrGridItem(node))
    )
      return true;
    if (num(cs.opacity) < 1) return true;
    if (
      cs.transform !== 'none' ||
      cs.filter !== 'none' ||
      cs.isolation === 'isolate' ||
      cs.mixBlendMode !== 'normal'
    )
      return true;
    if (/transform|opacity/.test(cs.willChange)) return true;
    if (
      (cs.maskImage && cs.maskImage !== 'none') ||
      (cs.webkitMaskImage && cs.webkitMaskImage !== 'none')
    )
      return true;
    return false;
  }
  function isFlexOrGridItem(node) {
    const p = node.parentElement;
    if (!p) return false;
    const d = getComputedStyle(p).display;
    return /flex|grid/.test(d);
  }
  const clipsOf = new WeakMap();
  /** Clip entry for an element whose overflow clips its descendants. */
  function ownClip(node, cs, rect) {
    const ox = cs.overflowX;
    const oy = cs.overflowY;
    if (
      (ox === 'visible' && oy === 'visible') ||
      node === document.body ||
      node === document.documentElement
    )
      return null;
    const bt = num(cs.borderTopWidth);
    const bl = num(cs.borderLeftWidth);
    const br = num(cs.borderRightWidth);
    const bb = num(cs.borderBottomWidth);
    let x = rect.left + bl;
    let y = rect.top + bt;
    let w = rect.width - bl - br;
    let h = rect.height - bt - bb;
    // overflow-x: visible + overflow-y: hidden etc.: clip only that axis.
    if (ox === 'visible') {
      x = -1e5;
      w = 2e5;
    }
    if (oy === 'visible') {
      y = -1e5;
      h = 2e5;
    }
    const r =
      ox !== 'visible' && oy !== 'visible'
        ? insetRadii(radii(cs, rect.width, rect.height), bt, br, bb, bl)
        : null;
    return { x, y, w, h, r };
  }
  const intersect = (a, b) => {
    const x = Math.max(a.x, b.x);
    const y = Math.max(a.y, b.y);
    const r = Math.min(a.x + a.w, b.x + b.w);
    const bt = Math.min(a.y + a.h, b.y + b.h);
    return { x, y, w: Math.max(0, r - x), h: Math.max(0, bt - y) };
  };
  const clipIds = new Map();
  function clipId(c) {
    const key = JSON.stringify(c);
    if (!clipIds.has(key)) {
      const id = nid('c');
      clipIds.set(key, id);
      const vx = Math.max(c.x, -10);
      const vy = Math.max(c.y, -10);
      const vw = Math.min(c.x + c.w, innerWidth + 10) - vx;
      const vh = Math.min(c.y + c.h, innerHeight + 10) - vy;
      const s = c.r
        ? shapeAttrs(c.x, c.y, c.w, c.h, c.r)
        : shapeAttrs(vx, vy, vw, vh, null);
      defs.push(el('clipPath', { id }, s ? shapeEl(s, {}) : ''));
    }
    return clipIds.get(key);
  }

  /**
   * Items are {chain, svg} where chain is the list of group wrappers (clips, opacity) the item
   * sits in, from the root. Emitting compares chains and opens/closes <g> as needed.
   */
  const items = [];
  const VIEW = () => ({ x: 0, y: 0, w: innerWidth, h: innerHeight });
  function push(chain, svg, bbox, visRect) {
    if (!svg) return;
    if (bbox && visRect) {
      const i = intersect(bbox, visRect);
      if (i.w <= 0 || i.h <= 0) {
        stats.dropped++;
        return;
      }
    }
    items.push({ chain, svg });
  }
  const rectOf = (r) => ({ x: r.left, y: r.top, w: r.width, h: r.height });

  function paintContextBox(node, chain, vis) {
    // node is a stacking context root (or the document root).
    const cs = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    if (num(cs.opacity) < 1)
      chain = [...chain, { k: 'o', v: rnd(num(cs.opacity), 3), id: nid('o') }];
    chain = effects(node, cs, rect, chain);
    if (cs.visibility !== 'hidden')
      push(chain, boxEls(node, cs, rect), rectOf(rect), vis);
    const negs = [];
    const flow = [];
    const pos = [];
    const inner = descend(node, cs, rect, chain, vis);
    collect(node, inner.chain, inner.vis, negs, flow, pos);
    negs.sort((a, b) => a.z - b.z);
    pos.sort((a, b) => a.z - b.z);
    for (const n of negs) paintContext(n.node, n.chain, n.vis);
    for (const f of flow) f();
    for (const p of pos) paintContext(p.node, p.chain, p.vis);
  }
  /** CSS filter: blur() and mask-image gradients -> <filter> / <mask> group wrappers. */
  function effects(node, cs, rect, chain) {
    const bm = /^blur\(([\d.]+)px\)$/.exec(cs.filter);
    if (bm && num(bm[1]) > 0) {
      const std = num(bm[1]);
      const id = nid('f');
      const m = std * 3;
      defs.push(
        el(
          'filter',
          {
            id,
            filterUnits: 'userSpaceOnUse',
            x: rnd(rect.left - m),
            y: rnd(rect.top - m),
            width: rnd(rect.width + 2 * m),
            height: rnd(rect.height + 2 * m),
          },
          el('feGaussianBlur', { stdDeviation: rnd(std, 2) }),
        ),
      );
      chain = [...chain, { k: 'f', id }];
    }
    const mi = cs.maskImage || cs.webkitMaskImage;
    if (mi && mi !== 'none') {
      const gid = gradientDef(
        splitTop(mi)[0],
        rect.left,
        rect.top,
        rect.width,
        rect.height,
        true,
      );
      if (gid) {
        const id = nid('m');
        defs.push(
          el(
            'mask',
            {
              id,
              maskUnits: 'userSpaceOnUse',
              x: rnd(rect.left),
              y: rnd(rect.top),
              width: rnd(rect.width),
              height: rnd(rect.height),
            },
            el('rect', {
              x: rnd(rect.left),
              y: rnd(rect.top),
              width: rnd(rect.width),
              height: rnd(rect.height),
              fill: `url(#${gid})`,
            }),
          ),
        );
        chain = [...chain, { k: 'm', id }];
      }
    }
    return chain;
  }
  function descend(node, cs, rect, chain, vis) {
    const c = ownClip(node, cs, rect);
    if (!c) return { chain, vis };
    return {
      chain: [...chain, { k: 'c', id: clipId(c) }],
      vis: intersect(vis, c),
    };
  }
  function collect(parent, chain, vis, negs, flow, pos) {
    for (const ch of parent.childNodes) {
      if (ch.nodeType === 3) {
        const pcs = getComputedStyle(parent);
        if (pcs.visibility === 'hidden') continue;
        flow.push(() => {
          const r = document.createRange();
          r.selectNodeContents(ch);
          const b = r.getBoundingClientRect();
          push(chain, textEls(ch, pcs), rectOf(b), vis);
        });
        continue;
      }
      if (ch.nodeType !== 1) continue;
      if (ch.hasAttribute('data-shot-skip')) continue;
      const cs = getComputedStyle(ch);
      if (cs.display === 'none' || cs.contentVisibility === 'hidden') continue;
      if (num(cs.opacity) === 0) {
        stats.dropped++;
        continue;
      }
      const tag = ch.tagName;
      const isSvg = ch.namespaceURI === SVGNS && tag.toLowerCase() === 'svg';
      const fixed = cs.position === 'fixed';
      const cChain = fixed ? chain.filter((e) => e.k === 'o') : chain;
      const cVis = fixed ? VIEW() : vis;
      if (createsContext(cs, ch) || cs.position !== 'static') {
        const z = cs.zIndex === 'auto' ? 0 : parseInt(cs.zIndex, 10) || 0;
        const entry = { node: ch, chain: cChain, vis: cVis, z };
        (z < 0 ? negs : pos).push(entry);
        continue;
      }
      if (isSvg) {
        flow.push(() => paintReplaced(ch, cs, chain, vis));
        continue;
      }
      if (
        tag === 'IMG' ||
        tag === 'CANVAS' ||
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'VIDEO'
      ) {
        flow.push(() => paintReplaced(ch, cs, chain, vis));
        continue;
      }
      if (ch.namespaceURI === SVGNS) continue;
      flow.push(() => {
        if (cs.visibility !== 'hidden') {
          const r = ch.getBoundingClientRect();
          push(chain, boxEls(ch, cs, r), rectOf(r), vis);
        }
      });
      const r = ch.getBoundingClientRect();
      const inner = descend(ch, cs, r, chain, vis);
      // Children of a fully clipped-away element are still collected (positioned ones may escape),
      // the visibility test drops what is not on screen.
      collect(ch, inner.chain, inner.vis, negs, flow, pos);
    }
  }
  function paintReplaced(node, cs, chain, vis) {
    if (cs.visibility === 'hidden') return;
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const bb = rectOf(rect);
    const tag = node.tagName;
    if (node.namespaceURI === SVGNS) {
      push(chain, boxEls(node, cs, rect), bb, vis);
      const textOut = [];
      const body = copySvg(node, rect, cs, (t) => textOut.push(svgTextEls(t)));
      push(
        chain,
        body + textOut.join(''),
        { x: bb.x - 4, y: bb.y - 4, w: bb.w + 8, h: bb.h + 8 },
        vis,
      );
      return;
    }
    push(chain, boxEls(node, cs, rect), bb, vis);
    if (tag === 'IMG' || tag === 'CANVAS')
      push(chain, imageEl(node, rect), bb, vis);
    else if (tag === 'INPUT' || tag === 'TEXTAREA')
      push(chain, inputTextEls(node, cs, rect), bb, vis);
  }
  // Stacking contexts whose root is an <svg> or replaced element are painted atomically.
  function paintContext(node, chain, vis) {
    const cs = getComputedStyle(node);
    if (
      node.namespaceURI === SVGNS ||
      /^(IMG|CANVAS|INPUT|TEXTAREA)$/.test(node.tagName)
    ) {
      if (num(cs.opacity) < 1)
        chain = [
          ...chain,
          { k: 'o', v: rnd(num(cs.opacity), 3), id: nid('o') },
        ];
      paintReplaced(node, cs, chain, vis);
      return;
    }
    paintContextBox(node, chain, vis);
  }

  function emit() {
    let out = '';
    let open = [];
    for (const it of items) {
      let i = 0;
      while (
        i < open.length &&
        i < it.chain.length &&
        open[i].id === it.chain[i].id
      )
        i++;
      for (let j = open.length - 1; j >= i; j--) out += '</g>';
      open = open.slice(0, i);
      for (let j = i; j < it.chain.length; j++) {
        const e = it.chain[j];
        out +=
          e.k === 'c'
            ? `<g clip-path="url(#${e.id})">`
            : e.k === 'f'
              ? `<g filter="url(#${e.id})">`
              : e.k === 'm'
                ? `<g mask="url(#${e.id})">`
                : `<g opacity="${e.v}">`;
        open.push(e);
      }
      out += it.svg;
    }
    for (let j = open.length - 1; j >= 0; j--) out += '</g>';
    return out;
  }

  window.__gpuDeckDomToSvg = function (opts = {}) {
    materializePseudo();
    tokens = readTokens();
    const W = innerWidth;
    const H = innerHeight;
    // Page canvas colour (html/body background propagates to the viewport).
    const rootBg = parseColor(
      getComputedStyle(document.documentElement).backgroundColor,
    ) ||
      parseColor(getComputedStyle(document.body).backgroundColor) || {
        r: 255,
        g: 255,
        b: 255,
        a: 1,
      };
    const canvasRect = el('rect', paint('f', rootBg, { width: W, height: H }));
    paintContext(document.documentElement, [], VIEW());
    const body = emit();
    const tokenCss = [...usedTokens.entries()]
      .sort()
      .map(([c, v]) => `.${c}{${v}}`)
      .join('\n');
    const smooth = getComputedStyle(document.body).webkitFontSmoothing;
    const textCss =
      (smooth && smooth !== 'auto'
        ? `text{-webkit-font-smoothing:${smooth}}\n`
        : '') +
      [...textClasses.entries()].map(([k, c]) => `.${c}{${k}}`).join('\n');
    const title = opts.title ? `<title>${esc(opts.title)}</title>` : '';
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">` +
      title +
      `\n<style>\n/* theme tokens: f-<token> = fill, s-<token> = stroke (edit a colour here to recolour it everywhere) */\n${tokenCss}\n/* text styles */\n${textCss}\n/*@fonts*/\n</style>\n` +
      `<defs>${defs.join('')}</defs>\n` +
      canvasRect +
      `<g id="screen">${body}</g></svg>`;
    return {
      svg,
      fonts,
      stats,
      tokens: Object.fromEntries([...tokens.entries()].map(([k, v]) => [v, k])),
    };
  };
})();
