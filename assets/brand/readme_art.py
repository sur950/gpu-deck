"""Build the README header and the ecosystem diagram, and the text versions that go with them.

The README is read on GitHub in a light or a dark theme, and GitHub picks the <picture> source
from the browser's colour scheme, which is not always GitHub's own theme. So each image here
carries its own background card: either variant stays readable on either page colour, and the
pair only decides which one blends in. Text is converted to outlines (Figtree and JetBrains Mono,
both SIL OFL, from website/assets/fonts/; the wordmark comes from the lockups that build.py
writes), so nothing depends on the fonts a viewer has installed.

Outputs:

  assets/brand/readme-header-{light,dark}.svg   the README hero: lockup on its own card
  assets/brand/ecosystem-{light,dark}.svg       what GPU Deck drives, serves and runs on
  README.md                                     the diagram's <picture> and its text version,
                                                between <!-- ecosystem:begin/end --> comments
  website/index.html                            the same lists as HTML (real text, responsive),
                                                between <!-- ecosystem:begin/end --> comments

The rows below (ROWS) are the single source for all three. List only what GPU Deck supports today
(docs/hardware/README.md, the engine catalog) and mark as verified (green dot) or partial (half
amber dot: it ran, with known gaps) only what docs/TESTING.md says ran on real hardware. Names
are plain text: no third-party logos.

Needs Python 3.10+ with fontTools, brotli (to read the WOFF2 fonts) and uharfbuzz (kerning):

    python3 -m venv /tmp/brand-venv && /tmp/brand-venv/bin/pip install -r scripts/requirements-art.txt
    /tmp/brand-venv/bin/python assets/brand/readme_art.py           # write everything
    /tmp/brand-venv/bin/python assets/brand/readme_art.py --check   # exit 1 when anything is stale
"""

from __future__ import annotations

import argparse
import html
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import uharfbuzz as hb
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
except ImportError:  # pragma: no cover
    sys.exit(
        "readme_art.py needs fontTools, brotli and uharfbuzz: "
        "pip install -r scripts/requirements-art.txt"
    )

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FONTS = ROOT / "website" / "assets" / "fonts"
README = ROOT / "README.md"
INDEX = ROOT / "website" / "index.html"


# ── What GPU Deck supports (the single source) ─────────────────────────────
@dataclass(frozen=True)
class Band:
    key: str
    label: str
    icon: str
    items: tuple[str, ...]
    verified: frozenset[str] = frozenset()  # ran on real hardware (docs/TESTING.md)
    note: str = ""  # a short line under the chips
    partial: frozenset[str] = frozenset()  # ran on real hardware with known gaps (docs/TESTING.md)

    @property
    def marked(self) -> frozenset[str]:
        return self.verified | self.partial


DRIVE = Band("drive", "Drive it with", "terminal",
             ("Web UI", "Desktop app", "CLI", "Terraform", "REST API", "Any AI gateway in front"))
APIS = Band("apis", "Compatible APIs", "braces", ("OpenAI", "Anthropic", "Ollama"),
            note="One serving port, streaming and tool calls")
MODELS = Band("models", "Model types", "box",
              ("Chat and thinking", "Vision", "Embeddings", "Rerank", "Speech-to-text", "Text-to-speech",
               "Image", "Video"))
CORE = ("Fleet", "Placement", "Rules", "Layouts", "Swaps", "Routing", "Metrics", "Alerts")
ENGINES = Band("engines", "Inference engines", "gauge",
               ("vLLM", "SGLang", "llama.cpp", "TensorRT-LLM", "MLX", "mlx-audio", "vLLM-Omni",
                "SGLang Diffusion", "whisper.cpp", "stable-diffusion.cpp", "Custom container"),
               frozenset({"llama.cpp", "MLX", "vLLM"}), partial=frozenset({"SGLang", "TensorRT-LLM"}))
ACCELERATORS = Band("accelerators", "Accelerators", "chip",
                    ("NVIDIA", "AMD", "Apple Silicon", "Hygon", "MetaX", "Moore Threads", "T-Head", "CPU only"),
                    frozenset({"Apple Silicon", "NVIDIA"}))
RUNS = Band("runs", "Where it runs", "server",
            ("On-prem bare metal", "VMs", "Kubernetes (Helm)", "AWS", "Azure", "Google Cloud",
             "DigitalOcean", "Air-gapped"))
# Rows of the diagram, top to bottom; None is GPU Deck itself. Boxes in one row share its width
# by the given weights.
ROWS: tuple[tuple[tuple[Band, float], ...] | None, ...] = (
    ((DRIVE, 1.0),),
    ((APIS, 0.34), (MODELS, 0.66)),
    None,
    ((ENGINES, 1.0),),
    ((ACCELERATORS, 0.45), (RUNS, 0.55)),
)
BANDS = (DRIVE, APIS, MODELS, ENGINES, ACCELERATORS, RUNS)
CORE_NOTE = "Control plane on one CPU server"
LEGEND = "Verified on real hardware"
LEGEND_PARTIAL = "Partial"
LEGEND_REST = "Everything else is implemented and tested against recorded fixtures, awaiting hardware reports."
CAPABILITIES = "MODELS · LAYOUTS · RULES · SWAPS"

# Stroke icons on a 24-unit grid, the same drawings as the website's sprite (website/index.html).
ICONS = {
    "terminal": '<rect x="3" y="4.5" width="18" height="15" rx="2.4"/><path d="m7 9.5 3 2.5-3 2.5M12.5 15h4.5"/>',
    "braces": ('<path d="M9 4.5c-1.9 0-2.8.9-2.8 2.8v2.2c0 1.3-.8 2.2-2.2 2.5 1.4.3 2.2 1.2 2.2 2.5v2.2'
               'c0 1.9.9 2.8 2.8 2.8M15 4.5c1.9 0 2.8.9 2.8 2.8v2.2c0 1.3.8 2.2 2.2 2.5-1.4.3-2.2 1.2-2.2 '
               '2.5v2.2c0 1.9-.9 2.8-2.8 2.8"/>'),
    "box": '<path d="M12 3 4 7.2v9.6L12 21l8-4.2V7.2Z"/><path d="M4 7.2 12 11.5l8-4.3M12 11.5V21"/>',
    "gauge": '<path d="M4.5 17a8.5 8.5 0 1 1 15 0"/><path d="m12 13.5 4-4.5"/><circle cx="12" cy="13.5" r="1.4"/>',
    "chip": ('<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx=".8"/>'
             '<path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>'),
    "server": ('<rect x="3.5" y="4" width="17" height="7" rx="1.8"/><rect x="3.5" y="13" width="17" height="7" rx="1.8"/>'
               '<path d="M7 7.5h.01M7 16.5h.01M11 7.5h6M11 16.5h6"/>'),
}

# ── Palette (docs/BRAND.md) ─────────────────────────────────────────────────
THEMES = {
    "light": {
        "card": "#F7F6F3", "card_line": "#E2E0DA", "band": "#FFFFFF", "band_line": "#E4E2DC",
        "chip": "#F4F3F0", "chip_line": "#E2E0DA", "text": "#111215", "text_2": "#5B5E66",
        "accent_text": "#B3122E", "glow": "#B3122E", "glow_a": 0.10, "grid": "#111215", "grid_a": 0.05,
        "core": "#111215", "core_line": "#111215", "ok": "#1F8A5B", "warn": "#C29100",
    },
    "dark": {
        "card": "#0E0F12", "card_line": "#2A2B30", "band": "#15161A", "band_line": "#2A2B30",
        "chip": "#1F2025", "chip_line": "#33343A", "text": "#F2F1EE", "text_2": "#A1A3AB",
        "accent_text": "#F2687B", "glow": "#D3263F", "glow_a": 0.22, "grid": "#FFFFFF", "grid_a": 0.045,
        "core": "#1B1C20", "core_line": "#D3263F", "ok": "#34A873", "warn": "#E8B83A",
    },
}
CORE_TEXT, CORE_TEXT_2 = "#F2F1EE", "#A1A3AB"
CORE_CHIP, CORE_CHIP_LINE = "#FFFFFF", "#FFFFFF"  # drawn at low opacity on the dark slab


# ── Text to outlines (shaped with HarfBuzz, glyphs defined once and reused) ─
class Face:
    """One font at one weight: shapes text and collects the glyphs it uses."""

    def __init__(self, key: str, path: Path, weight: float):
        font = TTFont(str(path))
        font = instancer.instantiateVariableFont(font, {"wght": weight})
        font.flavor = None
        buf = io.BytesIO()
        font.save(buf)
        self.key = key
        self.font = TTFont(io.BytesIO(buf.getvalue()))
        self.glyphs = self.font.getGlyphSet()
        self.order = self.font.getGlyphOrder()
        self.upm = self.font["head"].unitsPerEm
        self.cap = self.font["OS/2"].sCapHeight / self.upm
        self.hb_font = hb.Font(hb.Face(buf.getvalue()))
        self.used: dict[int, str] = {}

    def shape(self, text: str, tracking: float = 0.0) -> list[tuple[int, float]]:
        """(glyph id, x offset in font units) per glyph; tracking in em."""
        b = hb.Buffer()
        b.add_str(text)
        b.guess_segment_properties()
        hb.shape(self.hb_font, b, {"kern": True, "liga": True})
        out, x = [], 0.0
        for info, pos in zip(b.glyph_infos, b.glyph_positions):
            out.append((info.codepoint, x + pos.x_offset))
            x += pos.x_advance + tracking * self.upm
        self._width = x - (tracking * self.upm if out else 0)
        return out

    def width(self, text: str, size: float, tracking: float = 0.0) -> float:
        self.shape(text, tracking)
        return self._width * size / self.upm

    def run(self, text: str, size: float, x: float, baseline: float, fill: str,
            tracking: float = 0.0, opacity: float | None = None) -> str:
        glyphs = self.shape(text, tracking)
        uses = []
        for gid, gx in glyphs:
            name = self.order[gid]
            if not self.glyphs[name].width and name in ("space", "uni00A0"):
                continue
            self.used[gid] = name
            uses.append(f'<use href="#{self.key}{gid}" x="{gx:.0f}"/>')
        s = size / self.upm
        op = f' fill-opacity="{opacity}"' if opacity is not None else ""
        return (f'<g fill="{fill}"{op} transform="translate({x:.2f} {baseline:.2f}) scale({s:.5f})">'
                + "".join(uses) + "</g>")

    def defs(self) -> str:
        out = []
        for gid, name in sorted(self.used.items()):
            pen = SVGPathPen(self.glyphs, ntos=lambda v: f"{round(v)}")
            self.glyphs[name].draw(TransformPen(pen, (1, 0, 0, -1, 0, 0)))
            d = pen.getCommands()
            if d:
                out.append(f'<path id="{self.key}{gid}" d="{d}"/>')
        return "".join(out)


def lockup_parts(variant: str) -> tuple[str, float, float]:
    """The lockup's inner drawing (gradient ids made unique), and its width and height."""
    text = (HERE / f"lockup-{variant}.svg").read_text(encoding="utf-8")
    vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', text)
    inner = re.sub(r"^<svg[^>]*>|</svg>\s*$", "", text.strip())
    inner = re.sub(r"<title>.*?</title>", "", inner)
    inner = re.sub(r'id="([\w-]+)"', lambda m: f'id="ra-{variant}-{m.group(1)}"', inner)
    inner = re.sub(r'url\(#([\w-]+)\)', lambda m: f'url(#ra-{variant}-{m.group(1)})', inner)
    return inner, float(vb.group(1)), float(vb.group(2))


def icon(name: str, x: float, y: float, size: float, color: str) -> str:
    s = size / 24
    return (f'<g transform="translate({x:.2f} {y:.2f}) scale({s:.4f})" fill="none" stroke="{color}" '
            f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g>')


def svg_doc(width: float, height: float, title: str, desc: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:g} {height:g}" width="{width:g}" '
            f'height="{height:g}" role="img" aria-labelledby="t d"><title id="t">{html.escape(title)}</title>'
            f'<desc id="d">{html.escape(desc)}</desc>{body}</svg>\n')


# ── README header ───────────────────────────────────────────────────────────
HEADER_W, HEADER_H = 1000, 264
TILE = 96  # the lockup's tile, in px of the 1000-wide card


def heat_strip(theme: str, y: float) -> str:
    """A row of GPU cells shaded by load, the website's 'GPU load as crimson density' motif."""
    cell, gap, n = 10, 5, 52
    width = n * cell + (n - 1) * gap
    x0 = (HEADER_W - width) / 2
    seed = 7
    out = []
    for i in range(n):
        seed = (seed * 1103515245 + 12345) % 2**31
        load = (seed >> 8) % 100 / 100
        edge = min(i, n - 1 - i) / 9  # fade towards both ends
        a = max(0.0, min(1.0, edge)) * (0.16 + 0.84 * load ** 1.6)
        if theme == "light":
            a *= 0.85
        out.append(f'<rect x="{x0 + i * (cell + gap):.1f}" y="{y}" width="{cell}" height="{cell}" rx="2.5" '
                   f'fill="#D3263F" fill-opacity="{a:.2f}"/>')
    return "".join(out)


def header(theme: str, mono: Face) -> str:
    c = THEMES[theme]
    w, h = HEADER_W, HEADER_H
    inner, lw, lh = lockup_parts("light" if theme == "light" else "dark")
    scale = TILE / lh
    lx = (w - lw * scale) / 2
    ly = 44
    caps_size = 14
    caps_track = 0.24
    cw = mono.width(CAPABILITIES, caps_size, caps_track)
    caps = mono.run(CAPABILITIES, caps_size, (w - cw) / 2, ly + TILE + 42, c["accent_text"], caps_track)
    grid = []
    for gx in range(20, w, 24):
        grid.append(f"M{gx} 0V{h}")
    for gy in range(4, h, 24):
        grid.append(f"M0 {gy}H{w}")
    body = (
        "<defs>"
        f'<radialGradient id="hd-glow-{theme}" cx="50%" cy="36%" r="52%">'
        f'<stop offset="0" stop-color="{c["glow"]}" stop-opacity="{c["glow_a"]}"/>'
        f'<stop offset="1" stop-color="{c["glow"]}" stop-opacity="0"/></radialGradient>'
        f'<radialGradient id="hd-fade-{theme}" cx="50%" cy="42%" r="58%">'
        '<stop offset="0" stop-color="#fff" stop-opacity="1"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'
        f'<mask id="hd-mask-{theme}"><rect width="{w}" height="{h}" fill="url(#hd-fade-{theme})"/></mask>'
        f'<clipPath id="hd-clip-{theme}"><rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="20"/></clipPath>'
        f"{mono.defs()}</defs>"
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="20.5" fill="{c["card"]}" stroke="{c["card_line"]}"/>'
        f'<g clip-path="url(#hd-clip-{theme})">'
        f'<path d="{"".join(grid)}" stroke="{c["grid"]}" stroke-opacity="{c["grid_a"]}" mask="url(#hd-mask-{theme})"/>'
        f'<rect width="{w}" height="{h}" fill="url(#hd-glow-{theme})"/></g>'
        f'<g transform="translate({lx:.2f} {ly}) scale({scale:.4f})">{inner}</g>'
        + caps
        + heat_strip(theme, h - 44)
    )
    return body


# ── Ecosystem diagram ───────────────────────────────────────────────────────
ECO_W = 1000
PAD = 24
BOX_PAD = 16  # inside a box
BOX_HEAD = 46  # from a box's top edge to its first row of chips
BOX_GAP = 12
CHIP_H, CHIP_GAP, CHIP_PAD = 32, 8, 13
CORE_H = 96
CHIP_SIZE = 14.5
LABEL_SIZE = 12
LABEL_TRACK = 0.11


def flow(items: tuple[str, ...], face: Face, x0: float, x1: float,
         verified: frozenset[str]) -> list[list[tuple[str, float, float]]]:
    """Chips in reading order, wrapped into as few rows as fit, then balanced across those rows."""
    widths = [face.width(i, CHIP_SIZE) + 2 * CHIP_PAD + (14 if i in verified else 0) for i in items]

    def wrap(limit: float) -> list[list[int]]:
        rows: list[list[int]] = [[]]
        x = 0.0
        for n, w in enumerate(widths):
            if rows[-1] and x + w > limit:
                rows.append([])
                x = 0.0
            rows[-1].append(n)
            x += w + CHIP_GAP
        return rows

    full = x1 - x0
    count = len(wrap(full))
    lo, hi = max(widths), full
    for _ in range(40):  # the narrowest limit that still needs no more rows
        mid = (lo + hi) / 2
        if len(wrap(mid)) <= count:
            hi = mid
        else:
            lo = mid
    out = []
    for row in wrap(hi):
        x = x0
        out.append([])
        for n in row:
            out[-1].append((items[n], x, widths[n]))
            x += widths[n] + CHIP_GAP
    return out


def half_dot(cx: float, cy: float, colour: str) -> str:
    """The 'partial' mark: an amber ring, left half filled."""
    return (f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="3" fill="none" stroke="{colour}" stroke-width="1.2"/>'
            f'<path d="M{cx:.2f} {cy - 3.6:.2f}a3.6 3.6 0 0 0 0 7.2Z" fill="{colour}"/>')


def chips(rows, face: Face, y0: float, c: dict, verified: frozenset[str], *, on_core: bool = False,
          partial: frozenset[str] = frozenset()) -> str:
    out = []
    for r, row in enumerate(rows):
        y = y0 + r * (CHIP_H + CHIP_GAP)
        for text, x, w in row:
            if on_core:
                out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{CHIP_H}" rx="8" fill="{CORE_CHIP}" '
                           f'fill-opacity=".07" stroke="{CORE_CHIP_LINE}" stroke-opacity=".16"/>')
                color = CORE_TEXT
            else:
                out.append(f'<rect x="{x + .5:.2f}" y="{y + .5:.2f}" width="{w - 1:.2f}" height="{CHIP_H - 1}" rx="7.5" '
                           f'fill="{c["chip"]}" stroke="{c["chip_line"]}"/>')
                color = c["text"]
            tx = x + CHIP_PAD
            if text in verified:
                out.append(f'<circle cx="{tx + 3.5:.2f}" cy="{y + CHIP_H / 2:.2f}" r="3.5" fill="{c["ok"]}"/>')
                tx += 14
            elif text in partial:
                out.append(half_dot(tx + 3.5, y + CHIP_H / 2, c["warn"]))
                tx += 14
            baseline = y + CHIP_H / 2 + face.cap * CHIP_SIZE / 2
            out.append(face.run(text, CHIP_SIZE, tx, baseline, color))
    return "".join(out)


def ecosystem(theme: str, sans: Face, mono: Face, small: Face) -> tuple[str, float]:
    c = THEMES[theme]
    w = ECO_W
    x0, x1 = PAD, w - PAD
    parts = []
    y = PAD

    def box(b: Band, bx0: float, bx1: float, top: float, height: float, rows) -> None:
        parts.append(f'<rect x="{bx0 + .5:.2f}" y="{top + .5:.2f}" width="{bx1 - bx0 - 1:.2f}" height="{height - 1:.2f}" '
                     f'rx="12" fill="{c["band"]}" stroke="{c["band_line"]}"/>')
        head = top + BOX_PAD + 8
        parts.append(icon(b.icon, bx0 + BOX_PAD, head - 8, 16, c["text_2"]))
        parts.append(mono.run(b.label.upper(), LABEL_SIZE, bx0 + BOX_PAD + 24, head + mono.cap * LABEL_SIZE / 2,
                              c["text_2"], LABEL_TRACK))
        parts.append(chips(rows, sans, top + BOX_HEAD, c, b.verified, partial=b.partial))
        if b.note:
            ny = top + BOX_HEAD + len(rows) * (CHIP_H + CHIP_GAP) + 12
            parts.append(small.run(b.note, 12.5, bx0 + BOX_PAD, ny + small.cap * 12.5 / 2, c["text_2"]))

    for row in ROWS:
        if row is None:
            y = core(theme, c, sans, small, parts, x0, x1, y)
            continue
        total = x1 - x0 - BOX_GAP * (len(row) - 1)
        spans, bx = [], x0
        for band, weight in row:
            bw = total * weight
            spans.append((band, bx, bx + bw))
            bx += bw + BOX_GAP
        laid = [(band, a, b, flow(band.items, sans, a + BOX_PAD, b - BOX_PAD, band.marked)) for band, a, b in spans]
        height = max(BOX_HEAD + len(r) * CHIP_H + (len(r) - 1) * CHIP_GAP + BOX_PAD for *_, r in laid)
        for band, a, b, r in laid:
            box(band, a, b, y, height, r)
        y += height + BOX_GAP

    # legend
    y += 6
    parts.append(f'<circle cx="{x0 + 6}" cy="{y + 6:.2f}" r="3.5" fill="{c["ok"]}"/>')
    lx = x0 + 18
    parts.append(small.run(LEGEND, 12.5, lx, y + 6 + small.cap * 12.5 / 2, c["text"]))
    lx += small.width(LEGEND, 12.5) + 18
    parts.append(half_dot(lx + 3.5, y + 6, c["warn"]))
    lx += 14
    parts.append(small.run(LEGEND_PARTIAL, 12.5, lx, y + 6 + small.cap * 12.5 / 2, c["text"]))
    lx += small.width(LEGEND_PARTIAL, 12.5) + 12
    parts.append(small.run(LEGEND_REST, 12.5, lx, y + 6 + small.cap * 12.5 / 2, c["text_2"]))
    height = y + 12 + PAD

    defs = (
        "<defs>"
        f'<radialGradient id="eco-core-glow-{theme}" cx="18%" cy="0%" r="80%">'
        '<stop offset="0" stop-color="#D3263F" stop-opacity=".30"/>'
        '<stop offset="1" stop-color="#D3263F" stop-opacity="0"/></radialGradient>'
        f"{sans.defs()}{mono.defs()}{small.defs()}</defs>"
    )
    card = (f'<rect x=".5" y=".5" width="{w - 1}" height="{height - 1:.2f}" rx="20.5" fill="{c["card"]}" '
            f'stroke="{c["card_line"]}"/>')
    return defs + card + "".join(parts), height


def core(theme: str, c: dict, sans: Face, small: Face, parts: list[str], x0: float, x1: float, y: float) -> float:
    """GPU Deck itself: the lockup, what the control plane does, and a crimson edge."""
    inner, lw, lh = lockup_parts("dark")
    left = 232
    rows = flow(CORE, sans, x0 + left, x1 - 20, frozenset())
    block = len(rows) * CHIP_H + (len(rows) - 1) * CHIP_GAP
    h = max(CORE_H, block + 44)
    parts.append(f'<rect x="{x0}" y="{y:.2f}" width="{x1 - x0}" height="{h}" rx="14" fill="{c["core"]}"/>')
    parts.append(f'<rect x="{x0 + .5}" y="{y + .5:.2f}" width="{x1 - x0 - 1}" height="{h - 1}" rx="13.5" '
                 f'fill="url(#eco-core-glow-{theme})" stroke="{c["core_line"]}" '
                 f'stroke-opacity="{0.55 if theme == "dark" else 1}"/>')
    parts.append(f'<path d="M{x0 + 26} {y + .75:.2f}H{x1 - 26}" stroke="#D3263F" stroke-width="1.5" stroke-linecap="round"/>')
    mid = y + h / 2
    scale = 36 / lh
    parts.append(f'<g transform="translate({x0 + 20} {mid - 18 - 9:.2f}) scale({scale:.4f})">{inner}</g>')
    parts.append(small.run(CORE_NOTE, 12, x0 + 20, mid + 27, CORE_TEXT_2))
    parts.append(chips(rows, sans, mid - block / 2, c, frozenset(), on_core=True))
    return y + h + BOX_GAP


def describe() -> str:
    lines = []
    for row in ROWS:
        if row is None:
            lines.append(f"In the middle, GPU Deck ({CORE_NOTE.lower()}): {', '.join(CORE)}.")
            continue
        for b, _ in row:
            v = (f" Verified on real hardware: {', '.join(i for i in b.items if i in b.verified)}."
                 if b.verified else "")
            v += (f" Partial on real hardware: {', '.join(i for i in b.items if i in b.partial)}."
                  if b.partial else "")
            lines.append(f"{b.label}: {', '.join(b.items)}.{v}")
    return " ".join(lines)


# ── Text versions (README and website) ──────────────────────────────────────
def readme_block() -> str:
    alt = "What GPU Deck supports, with GPU Deck in the middle. " + describe()
    rows = []
    for b in BANDS:
        items = ", ".join(f"{i} (verified)" if i in b.verified else f"{i} (partial)" if i in b.partial else i
                          for i in b.items)
        rows.append(f"| {b.label} | {items} |")
    return (
        "<!-- ecosystem:begin (generated by assets/brand/readme_art.py; edit ROWS there) -->\n"
        "<p>\n"
        "  <picture>\n"
        '    <source media="(prefers-color-scheme: dark)" srcset="assets/brand/ecosystem-dark.svg">\n'
        f'    <img src="assets/brand/ecosystem-light.svg" alt="{html.escape(alt, quote=True)}" width="880">\n'
        "  </picture>\n"
        "</p>\n\n"
        "<details>\n<summary>The same as a table</summary>\n\n"
        "| | Supported |\n| --- | --- |\n"
        + "\n".join(rows)
        + f"\n| GPU Deck itself | {', '.join(CORE)} |\n\n"
        f"(verified) means {LEGEND.lower()}; (partial) means it ran on real hardware with known gaps. "
        f"{LEGEND_REST}\n\n"
        "</details>\n"
        "<!-- ecosystem:end -->"
    )


def html_block() -> str:
    """The website version: real text in lists, laid out by site.css (.eco)."""
    def box(b: Band) -> str:
        lis = "".join(
            f'<li class="ok">{html.escape(i)}<span class="sr-only"> (verified on real hardware)</span></li>'
            if i in b.verified else
            f'<li class="partial">{html.escape(i)}<span class="sr-only"> (partial: ran on real hardware with known gaps)</span></li>'
            if i in b.partial else f"<li>{html.escape(i)}</li>" for i in b.items)
        note = f'\n              <p class="eco-note">{html.escape(b.note)}</p>' if b.note else ""
        return (f'            <div class="eco-box eco-{b.key}">\n'
                f'              <h3 class="eco-label"><svg class="icon" aria-hidden="true"><use href="#i-{b.icon}"/></svg>'
                f"{html.escape(b.label)}</h3>\n"
                f'              <ul class="eco-chips">{lis}</ul>{note}\n'
                "            </div>")

    out = ["<!-- ecosystem:begin (generated by assets/brand/readme_art.py; edit ROWS there) -->",
           '        <div class="eco reveal">']
    for row in ROWS:
        if row is None:
            core = "".join(f"<li>{html.escape(i)}</li>" for i in CORE)
            out.append('          <div class="eco-core">\n'
                       '            <p class="eco-core-id"><img src="assets/brand/lockup-dark.svg" width="123" height="34" '
                       f'alt="GPU Deck"><span>{html.escape(CORE_NOTE)}</span></p>\n'
                       f'            <ul class="eco-chips">{core}</ul>\n'
                       "          </div>")
            continue
        key = "-".join(b.key for b, _ in row)
        out.append(f'          <div class="eco-row eco-row-{key}">\n' + "\n".join(box(b) for b, _ in row)
                   + "\n          </div>")
    out.append(f'          <p class="eco-legend"><span class="eco-dot" aria-hidden="true"></span><b>{html.escape(LEGEND)}.</b> '
               f'<span class="eco-dot partial" aria-hidden="true"></span><b>{html.escape(LEGEND_PARTIAL)}:</b> ran, with '
               f'known gaps. {html.escape(LEGEND_REST)} <a href="docs/hardware/">See the support matrix</a>.</p>')
    out.append("        </div>")
    out.append("        <!-- ecosystem:end -->")
    return "\n".join(out)


def splice(text: str, block: str, where: str) -> str:
    pattern = re.compile(r"<!-- ecosystem:begin.*?<!-- ecosystem:end -->", re.S)
    if not pattern.search(text):
        raise SystemExit(f"error: {where} has no <!-- ecosystem:begin --> ... <!-- ecosystem:end --> block")
    return pattern.sub(lambda _: block, text, count=1)


def outputs() -> dict[Path, str]:
    files: dict[Path, str] = {}
    for theme in ("light", "dark"):
        mono = Face("m", FONTS / "jetbrains-mono-latin-wght.woff2", 500)
        body = header(theme, mono)
        files[HERE / f"readme-header-{theme}.svg"] = svg_doc(
            HEADER_W, HEADER_H, "GPU Deck",
            "The GPU Deck logo, a crimson tile with a graphics card, and the name GPU Deck. "
            "Below it: models, layouts, rules, swaps.", body)
        sans = Face("s", FONTS / "figtree-latin-wght.woff2", 600)
        small = Face("t", FONTS / "figtree-latin-wght.woff2", 500)
        mono = Face("m", FONTS / "jetbrains-mono-latin-wght.woff2", 500)
        body, height = ecosystem(theme, sans, mono, small)
        files[HERE / f"ecosystem-{theme}.svg"] = svg_doc(
            ECO_W, round(height), "What GPU Deck supports", describe(), body)
    files[README] = splice(README.read_text(encoding="utf-8"), readme_block(), "README.md")
    files[INDEX] = splice(INDEX.read_text(encoding="utf-8"), html_block(), "website/index.html")
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when a generated file is out of date")
    args = ap.parse_args()
    stale = []
    for path, text in outputs().items():
        rel = path.relative_to(ROOT)
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(str(rel))
        else:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {rel} ({len(text.encode()) // 1024} KB)")
    if stale:
        print("out of date (run python3 assets/brand/readme_art.py): " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
