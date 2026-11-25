"""Build every GPU Deck brand asset from one set of drawing rules.

The mark is a graphics card drawn as one silhouette: the mounting bracket, a shroud with a cut
top-right corner (the signature), two fans and the edge connector. No letters. It sits white on
the crimson tile (the app icon form, shared with Container Deck's family) or stands alone in one
colour.
It ships in three optical sizes on a 64-unit grid (docs/BRAND.md):

  large   (128 px and up)   fans with seven swept blades, connector with contact fingers
  medium  (24 - 96 px)      fans with three swept blades, solid connector; edges on even
                            units, so it is pixel-exact at 32 px
  small   (16 px)           drawn on the 16 px grid itself (every edge on a whole pixel)

Outputs. SVG is the master format and the only one committed under assets/brand/; a raster is
written only where a platform requires one:

  assets/brand/*.svg               marks, glyphs, wordmarks, lockups, app icons, social preview
  assets/brand/social-preview.png  the one committed raster here: GitHub's social preview and the
                                   website's Open Graph image (neither accepts SVG)
  assets/brand/.build/png/         scratch tile PNGs (git-ignored), the source of the copies below
  apps/web/public/                 favicon.ico, favicon.svg (the tile), apple-touch-icon.png, logo*.svg, brand/*
  apps/web/components/shared/gpu-deck-glyph.ts   the glyph's path data for the inline React mark
  website/ (when present)          favicons + PWA icons at the root, assets/brand/{lockups,mark}.svg
                                   (assets/brand/og-image.png is copied from social-preview.png by
                                   website/sync-screenshots.sh at build time, never committed)

Each app is deployed separately, so apps/web/public and website/ each keep their own copy of the
few rasters a browser needs (favicon.ico, apple-touch-icon, the PWA icons).

The desktop icons (app icon, tray states, connect-screen mark) are built from these files by
apps/desktop/scripts/make-icons.py (`pnpm --filter desktop icons`).

Needs: Python 3.10+, Pillow, NumPy, fontTools, `rsvg-convert` (librsvg) on PATH, and the Satoshi
font from https://www.fontshare.com/fonts/satoshi (ITF Free Font License; never commit the font
files) plus JetBrains Mono (OFL) from https://github.com/JetBrains/JetBrainsMono. Text is
converted to outlines, so the SVGs render the same everywhere without the fonts installed.

  python3 assets/brand/build.py --satoshi ~/Downloads/Satoshi_Complete/Fonts/OTF \
      --jetbrains-mono ~/Downloads/JetBrainsMono-Medium.ttf
"""

from __future__ import annotations

import argparse
import io
import math
import shutil
import subprocess
import sys
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
WEB_PUBLIC = ROOT / "apps" / "web" / "public"
WEBSITE = ROOT / "website"

# ── Palette (docs/BRAND.md; the Container Deck family) ──────────────────────
CRIMSON = "#B3122E"  # the accent on light, canonical BRAND_COLOR
CRIMSON_DARK = "#D3263F"  # the accent on dark
INK = "#111215"  # text on light
PAPER = "#F2F1EE"  # text on dark
WINDOW = "#F4F3F0"  # window surface, light
WINDOW_DARK = "#0E0F12"  # window surface, dark
MUTED = "#5B5E66"  # secondary text on light
MUTED_DARK = "#A1A3AB"  # secondary text on dark
TILE_TOP, TILE_BOTTOM = "#D3263F", "#A10F28"  # the crimson tile, lit from above
GLYPH_TOP, GLYPH_BOTTOM = "#FFFFFF", "#F7E6E9"  # the white glyph on the tile, faintly warmed
LIFT = "#4A0712"  # the glyph's contact shadow on the tile
LIGHT_TILE_TOP, LIGHT_TILE_BOTTOM = "#FFFFFF", "#F1EFEA"  # light tile variant (crimson glyph)
STATUS_OK, STATUS_WARN = "#1F8A5B", "#C29100"  # status only (social preview fleet dots)
CHART = ("#B3122E", "#3B6EB9", "#C38323", "#00A291", "#753A7A")  # chart slots 1-5, light


# ── The glyph: a graphics card ──────────────────────────────────────────────
# Geometry per optical size, in 64-unit space:
#   br   = bracket (x0, x1, y0, y1, tab end x, tab height, corner radius)
#   body = shroud (x0, y0, x1, y1, top-right chamfer, bottom-right radius, left radius)
#   fans = fan centres, r = fan opening radius, hub = hub radius
#   conn = edge connector (y0, y1, segments) - the short segment is the key
TIERS = {
    "md": dict(
        br=(6, 10, 14, 52, 16, 2, 1), body=(16, 18, 58, 46, 8, 4, 2),
        fans=((27, 32), (47, 32)), r=9, hub=2.4, conn=(48, 52, ((22, 28), (30, 48))),
        blades=dict(n=3, gap=2.6, twist=80.0, rim=1.2),
    ),
    "sm": dict(  # the 16 px drawing, x4: fans are openings with a hub pixel
        br=(4, 8, 12, 56, 16, 4, 0.8), body=(16, 16, 60, 48, 8, 3, 1.2),
        fans=((28, 32), (48, 32)), r=8, hub=4, conn=(52, 56, ((24, 32), (36, 52))), blades=None,
    ),
    "tile_md": dict(  # glyph inside the tile, pixel-exact at 32 px
        br=(12, 14, 18, 48, 20, 2, 0.8), body=(20, 22, 52, 42, 6, 3, 1.5),
        fans=((28, 32), (42, 32)), r=6, hub=2, conn=(44, 48, ((24, 28), (30, 44))), blades=None,
    ),
    "tray": dict(  # menu-bar template: medium geometry, fans as openings with a hub
        br=(6, 10, 14, 52, 16, 2, 1), body=(16, 18, 58, 46, 8, 4, 2),
        fans=((27, 32), (47, 32)), r=9, hub=3, conn=(48, 52, ((22, 28), (30, 48))), blades=None,
    ),
    "tile_sm": dict(  # glyph inside the tile, the 16 px drawing, x4
        br=(8, 12, 16, 52, 16, 4, 0.6), body=(16, 16, 56, 44, 8, 2, 1),
        fans=((26, 30), (42, 30)), r=6, hub=0, conn=(48, 52, ((20, 28), (32, 48))), blades=None,
    ),
}
# The large optical size: seven swept blades with a wide hub.
LARGE_BLADES = dict(n=7, gap=1.7, twist=60.0, rim=1.7)
LARGE_HUB = 3.0
# Bounding box of the md / large glyph (x 6-58, y 14-52) with 2 units of air.
GLYPH_VIEW = "4 12 56 42"
GLYPH_W, GLYPH_H = 56, 42


def _circle(cx: float, cy: float, r: float) -> str:
    return f"M{cx - r:g} {cy:g}A{r:g} {r:g} 0 1 0 {cx + r:g} {cy:g}A{r:g} {r:g} 0 1 0 {cx - r:g} {cy:g}Z"


def _shroud(x0, y0, x1, y1, ch, r_br, r_l) -> str:
    return (
        f"M{x0 + r_l:g} {y0:g}H{x1 - ch:g}L{x1:g} {y0 + ch:g}V{y1 - r_br:g}"
        f"A{r_br:g} {r_br:g} 0 0 1 {x1 - r_br:g} {y1:g}H{x0 + r_l:g}"
        f"A{r_l:g} {r_l:g} 0 0 1 {x0:g} {y1 - r_l:g}V{y0 + r_l:g}A{r_l:g} {r_l:g} 0 0 1 {x0 + r_l:g} {y0:g}Z"
    )


def _bracket(x0, x1, y0, y1, tab_x1, tab_h, r) -> str:
    return (
        f"M{x0:g} {y0 + r:g}A{r:g} {r:g} 0 0 1 {x0 + r:g} {y0:g}H{tab_x1 - r:g}"
        f"A{r:g} {r:g} 0 0 1 {tab_x1:g} {y0 + r:g}V{y0 + tab_h:g}H{x1:g}V{y1 - r:g}"
        f"A{r:g} {r:g} 0 0 1 {x1 - r:g} {y1:g}H{x0 + r:g}A{r:g} {r:g} 0 0 1 {x0:g} {y1 - r:g}Z"
    )


def _blades(cx: float, cy: float, r_in: float, r_out: float, n: int = 7, gap: float = 1.7, twist: float = 60.0) -> str:
    """Swept fan blades with constant-width gaps (gap in grid units), clockwise sweep."""
    out = []
    pitch = 2 * math.pi / n
    k = math.radians(twist) / (r_out - r_in)
    steps = 10
    for i in range(n):
        th0 = i * pitch - math.pi / 2
        lead, trail = [], []
        for s in range(steps + 1):
            r = r_in + (r_out - r_in) * s / steps
            base = th0 + k * (r - r_in)
            lead.append((r, base))
            trail.append((r, base + pitch - gap / r))
        pt = lambda r, a: (cx + r * math.cos(a), cy + r * math.sin(a))  # noqa: E731
        d = "M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in (pt(r, a) for r, a in lead))
        ex, ey = pt(*trail[-1])
        d += f"A{r_out:g} {r_out:g} 0 0 1 {ex:.2f} {ey:.2f}"
        d += "".join(f"L{x:.2f} {y:.2f}" for x, y in (pt(r, a) for r, a in reversed(trail[:-1])))
        out.append(d + "Z")
    return " ".join(out)


def glyph(fill: str, tier: str = "md", detail: bool = False) -> str:
    """The card in one colour. detail=True is the large optical size (blades, contact fingers)."""
    g = TIERS[tier]
    s = f'<path fill="{fill}" d="{_bracket(*g["br"])}"/>'
    holes = "".join(_circle(cx, cy, g["r"]) for cx, cy in g["fans"])
    s += f'<path fill-rule="evenodd" fill="{fill}" d="{_shroud(*g["body"])}{holes}"/>'
    blades = LARGE_BLADES if detail else g["blades"]
    hub = LARGE_HUB if detail else g["hub"]
    if hub:
        # Hubs and blades are separate paths: overlapping sub-paths of opposite winding in one
        # path would cancel under the nonzero rule and punch a hole in the hub.
        s += f'<path fill="{fill}" d="' + "".join(_circle(cx, cy, hub) for cx, cy in g["fans"]) + '"/>'
    if blades:
        d = " ".join(
            _blades(cx, cy, hub - 0.6, g["r"] - blades["rim"], blades["n"], blades["gap"], blades["twist"])
            for cx, cy in g["fans"]
        )
        s += f'<path fill="{fill}" d="{d}"/>'
    y0, y1, segs = g["conn"]
    if detail:
        d = ""
        for a, b in segs:
            d += f"M{a + 0.4:g} {y0:g}H{b - 0.4:g}V{y0 + 1.2:g}H{a + 0.4:g}Z"
            n = max(2, round((b - a) / 2.4))
            pitch = (b - a) / n
            for i in range(n):
                x = a + i * pitch + 0.3
                d += f"M{x:.2f} {y0:g}H{x + pitch - 0.9:.2f}V{y1 - 0.4:g}Q{x + pitch - 0.9:.2f} {y1:g} {x + pitch - 1.3:.2f} {y1:g}H{x + 0.4:.2f}Q{x:.2f} {y1:g} {x:.2f} {y1 - 0.4:g}Z"
        s += f'<path fill="{fill}" d="{d}"/>'
    else:
        s += f'<path fill="{fill}" d="' + "".join(f"M{a:g} {y0:g}H{b:g}V{y1:g}H{a:g}Z" for a, b in segs) + '"/>'
    return s


def tile(prefix: str, inner: str, full_bleed: bool = False, light: bool = False, glow: bool = False) -> str:
    """The crimson tile (64x64, radius 14, #D3263F to #A10F28) with a hairline edge, and `inner`
    on top. glow=True adds the soft top light of the large sizes."""
    top, bottom = (LIGHT_TILE_TOP, LIGHT_TILE_BOTTOM) if light else (TILE_TOP, TILE_BOTTOM)
    edge = "#000000" if light else "#FFFFFF"
    rx = 0 if full_bleed else 14
    defs = (
        f'<linearGradient id="{prefix}t" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>'
    )
    if glow and not light:
        defs += (
            f'<radialGradient id="{prefix}glow" cx="0.5" cy="0.08" r="0.75"><stop offset="0" stop-color="#FFFFFF" '
            'stop-opacity="0.11"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/></radialGradient>'
        )
    out = f'<defs>{defs}</defs><rect width="64" height="64" rx="{rx}" fill="url(#{prefix}t)"/>'
    if glow and not light:
        out += f'<rect width="64" height="64" rx="{rx}" fill="url(#{prefix}glow)"/>'
    if not full_bleed:
        out += (
            f'<rect x="0.5" y="0.5" width="63" height="63" rx="13.5" fill="none" stroke="{edge}" '
            f'stroke-opacity="{0.08 if light else 0.2}"/>'
        )
    return out + inner


def tile_mark(prefix: str, size: str = "large", full_bleed: bool = False, fg: bool = True, light: bool = False) -> str:
    """The tile with the glyph at the right optical size: large / small (24-96 px) / 16.
    White glyph on crimson; the light variant is a crimson glyph on a white tile."""
    large = size not in ("16", "small")
    if not fg:
        return tile(prefix, "", full_bleed, light, glow=large)
    color = CRIMSON if light else "#FFFFFF"
    if size == "16":
        inner = glyph(color, "tile_sm")
    elif size == "small":
        inner = glyph(color, "tile_md")
    else:
        if not light:
            # The large sizes get the app icon's warmed white and a soft contact shadow.
            inner = (
                f'<defs><linearGradient id="{prefix}g" gradientUnits="userSpaceOnUse" x1="0" y1="14" x2="0" y2="52">'
                f'<stop offset="0" stop-color="{GLYPH_TOP}"/><stop offset="1" stop-color="{GLYPH_BOTTOM}"/></linearGradient>'
                f'<filter id="{prefix}lift" x="-20%" y="-20%" width="140%" height="160%" color-interpolation-filters="sRGB">'
                f'<feDropShadow dx="0" dy="0.7" stdDeviation="0.7" flood-color="{LIFT}" flood-opacity="0.35"/></filter></defs>'
                f'<g transform="translate(6.4 6.4) scale(0.8)" filter="url(#{prefix}lift)">'
                f'{glyph(f"url(#{prefix}g)", "md", detail=True)}</g>'
            )
        else:
            inner = f'<g transform="translate(6.4 6.4) scale(0.8)">{glyph(color, "md", detail=True)}</g>'
    return tile(prefix, inner, full_bleed, light, glow=large)


def svg(width: float, height: float, body: str, view: str | None = None, title: str = "GPU Deck") -> str:
    vb = view or f"0 0 {width:g} {height:g}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{width:g}" '
        f'height="{height:g}" role="img" aria-label="{title}"><title>{title}</title>{body}</svg>\n'
    )


def glyph_file(fill: str, detail: bool) -> str:
    return svg(GLYPH_W, GLYPH_H, glyph(fill, "md", detail), view=GLYPH_VIEW)


def glyph_adaptive() -> str:
    """Square, medium-size glyph that follows the viewer's colour scheme (favicon.svg)."""
    style = (
        "<style>path{fill:%s}@media (prefers-color-scheme:dark){path{fill:%s}}</style>" % (INK, PAPER)
    )
    body = style + glyph("currentColor", "md").replace('fill="currentColor"', "")
    return svg(64, 64, body, view="0 1 64 64")


# ── Text to outlines ────────────────────────────────────────────────────────
class Outliner:
    def __init__(self, path: Path):
        self.font = TTFont(str(path))
        self.glyphs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.upm = self.font["head"].unitsPerEm
        self.cap = self.font["OS/2"].sCapHeight / self.upm

    def path(self, text: str, size: float, x: float, baseline: float, tracking: float = 0.0):
        """SVG path data for `text` at `size` px, starting at (x, baseline). Returns (d, width)."""
        pen = SVGPathPen(self.glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
        scale = size / self.upm
        cursor = x
        for ch in text:
            name = self.cmap[ord(ch)]
            self.glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, cursor, baseline)))
            cursor += self.font["hmtx"][name][0] * scale + tracking * size
        width = cursor - x - (tracking * size if text else 0)
        return pen.getCommands(), width

    def bounds(self, text: str, size: float, tracking: float = 0.0):
        pen = BoundsPen(self.glyphs)
        scale = size / self.upm
        cursor = 0.0
        for ch in text:
            name = self.cmap[ord(ch)]
            self.glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, cursor, 0)))
            cursor += self.font["hmtx"][name][0] * scale + tracking * size
        return pen.bounds


NAME = "GPU Deck"
NAME_TRACKING = -0.02  # em


def wordmark(bold: Outliner, fill: str) -> str:
    size = 100.0
    x0, y0, x1, y1 = bold.bounds(NAME, size, NAME_TRACKING)
    d, _ = bold.path(NAME, size, -x0, -y0, NAME_TRACKING)
    return svg(round(x1 - x0, 1), round(y1 - y0, 1), f'<path d="{d}" fill="{fill}"/>')


def lockup(bold: Outliner, fill: str, prefix: str) -> str:
    """Horizontal: the crimson tile (medium glyph), then the name with its caps centred on it."""
    t = 42.0  # tile side
    cap = 15.5  # cap height, about three eighths of the tile
    size = cap / bold.cap
    gap = 11.0
    x0, y0, x1, y1 = bold.bounds(NAME, size, NAME_TRACKING)
    baseline = t / 2 + cap / 2
    text_x = t + gap - x0
    d, w = bold.path(NAME, size, text_x, baseline, NAME_TRACKING)
    right = text_x + x1 + 1
    body = f'<g transform="scale({t / 64:.5f})">{tile_mark(prefix, "small")}</g><path d="{d}" fill="{fill}"/>'
    return svg(round(right, 1), t, body, view=f"0 0 {right:.1f} {t:g}")


def lockup_stacked(bold: Outliner, fill: str, prefix: str) -> str:
    """Stacked: the large tile centred over the name."""
    t = 64.0
    cap = 13.0
    size = cap / bold.cap
    x0, _, x1, _ = bold.bounds(NAME, size, NAME_TRACKING)
    tw = x1 - x0
    width = max(t, tw) + 4
    baseline = t + 14 + cap  # 14 units between the tile and the caps
    d, _ = bold.path(NAME, size, (width - tw) / 2 - x0, baseline, NAME_TRACKING)
    height = baseline + 4
    body = (
        f'<g transform="translate({(width - t) / 2:.2f} 0)">{tile_mark(prefix)}</g>'
        f'<path d="{d}" fill="{fill}"/>'
    )
    return svg(round(width, 1), round(height, 1), body, view=f"0 0 {width:.1f} {height:.1f}")


# ── App icon (macOS grid: 824 body on 1024, soft shadow) ────────────────────
def app_icon(fg: bool = True, light: bool = False) -> str:
    """The crimson tile with the white glyph (Container Deck's icon recipe: top glow, 22 % white
    edge, warmed white glyph with a crimson-black contact shadow). light=True: white tile, crimson
    glyph."""
    p = "ail-" if light else "ai-"
    top, bottom = (LIGHT_TILE_TOP, LIGHT_TILE_BOTTOM) if light else (TILE_TOP, TILE_BOTTOM)
    g_top, g_bottom = (CRIMSON_DARK, "#A10F28") if light else (GLYPH_TOP, GLYPH_BOTTOM)
    s = 824 / 64
    defs = (
        f'<linearGradient id="{p}t" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/>'
        f'<stop offset="1" stop-color="{bottom}"/></linearGradient>'
        f'<radialGradient id="{p}glow" cx="0.5" cy="0.08" r="0.75"><stop offset="0" stop-color="#FFFFFF" '
        f'stop-opacity="{0.0 if light else 0.11}"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/></radialGradient>'
        f'<linearGradient id="{p}g" gradientUnits="userSpaceOnUse" x1="0" y1="14" x2="0" y2="52">'
        f'<stop offset="0" stop-color="{g_top}"/><stop offset="1" stop-color="{g_bottom}"/></linearGradient>'
        f'<filter id="{p}sh" x="-20%" y="-20%" width="140%" height="140%" color-interpolation-filters="sRGB">'
        f'<feDropShadow dx="0" dy="14" stdDeviation="18" flood-color="#000" flood-opacity="{0.16 if light else 0.28}"/></filter>'
        f'<filter id="{p}gs" x="-20%" y="-20%" width="140%" height="160%" color-interpolation-filters="sRGB">'
        f'<feDropShadow dx="0" dy="1.2" stdDeviation="1.1" flood-color="{"#000" if light else LIFT}" '
        f'flood-opacity="{0.10 if light else 0.35}"/></filter>'
    )
    edge = "#000000" if light else "#FFFFFF"
    body = (
        f"<defs>{defs}</defs>"
        f'<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#{p}t)" filter="url(#{p}sh)"/>'
        f'<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#{p}glow)"/>'
        f'<rect x="101.5" y="101.5" width="821" height="821" rx="183.5" fill="none" stroke="{edge}" '
        f'stroke-opacity="{0.10 if light else 0.22}" stroke-width="3"/>'
    )
    if fg:
        body += (
            f'<g transform="translate(100 100) scale({s:.4f}) translate(6.4 6.4) scale(0.8)" filter="url(#{p}gs)">'
            + glyph(f"url(#{p}g)", "md", detail=True)
            + "</g>"
        )
    return svg(1024, 1024, body)


# ── Social preview (1280x640) ───────────────────────────────────────────────
def social_preview(bold: Outliner, medium: Outliner, mono: Outliner, fg: bool = True) -> str:
    """1280x640, Container Deck's social card layout: the warm window surface, the app tile, the
    name, the tagline and a crimson line on the left; a GPU Deck window cropped by the right edge."""
    W, H = 1280, 640
    left = 96
    parts = [
        "<defs>",
        '<radialGradient id="sp-warm" cx="0.86" cy="0.62" r="0.62"><stop offset="0" stop-color="#B3122E" '
        'stop-opacity="0.16"/><stop offset="0.55" stop-color="#B3122E" stop-opacity="0.05"/>'
        '<stop offset="1" stop-color="#B3122E" stop-opacity="0"/></radialGradient>',
        '<linearGradient id="sp-bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#F7F6F3"/>'
        f'<stop offset="1" stop-color="{WINDOW}"/></linearGradient>',
        '<filter id="sp-win" x="-10%" y="-10%" width="130%" height="130%" color-interpolation-filters="sRGB">'
        '<feDropShadow dx="0" dy="24" stdDeviation="30" flood-color="#3C141C" flood-opacity="0.20"/></filter>',
        '<filter id="sp-tile" x="-30%" y="-30%" width="160%" height="170%" color-interpolation-filters="sRGB">'
        '<feDropShadow dx="0" dy="10" stdDeviation="12" flood-color="#5A0A18" flood-opacity="0.28"/></filter>',
        "</defs>",
        f'<rect width="{W}" height="{H}" fill="url(#sp-bg)"/>',
        f'<rect width="{W}" height="{H}" fill="url(#sp-warm)"/>',
    ]
    if not fg:
        return svg(W, H, "".join(parts))

    # Right: a GPU Deck window, cropped by the right and bottom edges.
    wx, wy, ww, wh = 676, 150, 700, 560
    parts.append(
        f'<g filter="url(#sp-win)"><rect x="{wx}" y="{wy}" width="{ww}" height="{wh}" rx="18" fill="#FFFFFF"/></g>'
        f'<rect x="{wx + 0.5}" y="{wy + 0.5}" width="{ww - 1}" height="{wh - 1}" rx="17.5" fill="none" '
        'stroke="#111215" stroke-opacity="0.10"/>'
    )
    # sidebar
    sw = 176
    parts.append(
        f'<path d="M{wx + 18} {wy}H{wx + sw}V{wy + wh}H{wx}V{wy + 18}A18 18 0 0 1 {wx + 18} {wy}Z" fill="#ECEAE5"/>'
        f'<path d="M{wx + sw} {wy}V{wy + wh}" stroke="#111215" stroke-opacity="0.08"/>'
    )
    for i, c in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        parts.append(f'<circle cx="{wx + 22 + i * 18}" cy="{wy + 22}" r="5.5" fill="{c}"/>')
    parts.append(f'<g transform="translate({wx + 18} {wy + 46}) scale({24 / 64:.4f})">{tile_mark("sp-s", "small")}</g>')
    d, _ = bold.path(NAME, 14.5, wx + 50, wy + 63, NAME_TRACKING)
    parts.append(f'<path d="{d}" fill="{INK}"/>')
    nav = ["Dashboard", "Fleet", "Deployments", "Layouts", "Rules", "Metrics", "Alerts"]
    for i, item in enumerate(nav):
        y = wy + 104 + i * 34
        active = item == "Fleet"
        if active:
            parts.append(f'<rect x="{wx + 10}" y="{y - 20}" width="{sw - 20}" height="30" rx="7" fill="{CRIMSON}" fill-opacity="0.09"/>')
            parts.append(f'<rect x="{wx + 10}" y="{y - 13}" width="3" height="16" rx="1.5" fill="{CRIMSON}"/>')
        parts.append(
            f'<rect x="{wx + 22}" y="{y - 11}" width="12" height="12" rx="3.5" fill="none" '
            f'stroke="{CRIMSON if active else MUTED}" stroke-width="1.6" stroke-opacity="{1 if active else 0.75}"/>'
        )
        d, _ = medium.path(item, 13.5, wx + 44, y, 0)
        parts.append(f'<path d="{d}" fill="{INK if active else MUTED}"/>')

    # content: title, then three node cards with four GPUs each
    cx0 = wx + sw + 28
    d, _ = bold.path("Fleet", 22, cx0, wy + 50, -0.01)
    parts.append(f'<path d="{d}" fill="{INK}"/>')
    d, _ = mono.path("9 GPUS · 3 NODES · 7 DEPLOYMENTS", 11, cx0, wy + 74, 0.08)
    parts.append(f'<path d="{d}" fill="{MUTED}"/>')
    # primary button (crimson)
    card_w = 360
    bx = cx0 + card_w - 112
    parts.append(f'<rect x="{bx}" y="{wy + 30}" width="112" height="32" rx="8" fill="{CRIMSON}"/>')
    d, w = medium.path("Deploy model", 13, 0, 0, 0)
    d, _ = medium.path("Deploy model", 13, bx + (112 - w) / 2, wy + 50.5, 0)
    parts.append(f'<path d="{d}" fill="#FFFFFF"/>')
    fleet = [
        ("gpu-node-a", "Ready", STATUS_OK, [[(0, 0.62), (1, 0.18)], [(0, 0.62), (2, 0.2)], [(2, 0.55)], [(1, 0.4), (3, 0.3)]]),
        ("gpu-node-b", "Draining", STATUS_WARN, [[(0, 0.8)], [(1, 0.46), (3, 0.22)], [(2, 0.7)], []]),
        ("gpu-node-c", "Ready", STATUS_OK, [[(4, 0.36), (0, 0.32)], [(3, 0.58)], [(1, 0.5), (4, 0.24)], [(0, 0.28)]]),
    ]
    for r, (node, state, dot, gpus) in enumerate(fleet):
        y = wy + 100 + r * 146
        parts.append(
            f'<rect x="{cx0}" y="{y}" width="{card_w}" height="130" rx="12" fill="#FFFFFF" stroke="#111215" stroke-opacity="0.09"/>'
        )
        d, _ = mono.path(node, 12.5, cx0 + 18, y + 28, 0.01)
        parts.append(f'<path d="{d}" fill="{INK}"/>')
        _, pw = medium.path(state, 11.5, 0, 0, 0)
        px = cx0 + card_w - 18 - pw - 26
        parts.append(f'<rect x="{px}" y="{y + 14}" width="{pw + 26}" height="20" rx="10" fill="{dot}" fill-opacity="0.12"/>')
        parts.append(f'<circle cx="{px + 10}" cy="{y + 24}" r="3.5" fill="{dot}"/>')
        d, _ = medium.path(state, 11.5, px + 18, y + 28, 0)
        parts.append(f'<path d="{d}" fill="{dot}"/>')
        for g, slices in enumerate(gpus[:3]):
            gx = cx0 + 18 + g * 112
            gy = y + 46
            parts.append(f'<rect x="{gx}" y="{gy}" width="100" height="66" rx="9" fill="{WINDOW}"/>')
            parts.append(f'<g transform="translate({gx + 10} {gy + 9}) scale(0.3)">{glyph(MUTED, "md")}</g>')
            d, _ = mono.path(f"GPU {g}", 10, gx + 48, gy + 21, 0)
            parts.append(f'<path d="{d}" fill="{MUTED}"/>')
            bx, by, bw, bh = gx + 10, gy + 42, 80, 10
            parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="3" fill="#111215" fill-opacity="0.07"/>')
            cur = bx
            for i, (si, frac) in enumerate(slices):
                sw_ = bw * frac - (2 if i < len(slices) - 1 else 0)
                parts.append(f'<rect x="{cur:.1f}" y="{by}" width="{sw_:.1f}" height="{bh}" rx="3" fill="{CHART[si]}"/>')
                cur += bw * frac

    # Left: the tile, name, tagline, the crimson line, capabilities.
    ts = 120
    parts.append(f'<g filter="url(#sp-tile)"><g transform="translate({left} 92) scale({ts / 64})">{tile_mark("sp-")}</g></g>')
    size = 92
    base = 92 + ts + 56 + bold.cap * size
    d, _ = bold.path(NAME, size, left - 4, base, NAME_TRACKING)
    parts.append(f'<path d="{d}" fill="{INK}"/>')
    tag_y = base + 62
    for i, line in enumerate(("The control plane for", "your own GPUs.")):
        d, _ = medium.path(line, 34, left - 1, tag_y + i * 44, -0.01)
        parts.append(f'<path d="{d}" fill="#3F4249"/>')
    cr_y = tag_y + 44 + 66
    d, _ = bold.path("Open source, Apache-2.0", 25, left - 1, cr_y, -0.005)
    parts.append(f'<path d="{d}" fill="{CRIMSON}"/>')
    d, _ = mono.path("MODELS · LAYOUTS · RULES · SWAPS", 13, left, cr_y + 36, 0.14)
    parts.append(f'<path d="{d}" fill="{MUTED}"/>')
    return svg(W, H, "".join(parts))


# ── Web app glyph paths (apps/web/components/shared/gpu-deck-glyph.ts) ─────
def glyph_paths_ts() -> str:
    """The glyph's path data for the inline React mark, so the web app needs no request and
    cannot drift from these assets."""
    import re

    def paths(tier: str) -> str:
        items = re.findall(r'<path (fill-rule="evenodd" )?fill="#000" d="([^"]+)"', glyph("#000", tier))
        rows = [
            "  { d: '%s'%s }," % (d, ", evenOdd: true" if evenodd else "") for evenodd, d in items
        ]
        return "\n".join(rows)

    return (
        "/**\n"
        " * GENERATED by assets/brand/build.py: do not edit by hand (docs/BRAND.md).\n"
        " *\n"
        " * The GPU Deck glyph, a graphics card, as path data on a 64-unit grid. Paths are\n"
        " * separate on purpose: the hubs and blades overlap with opposite winding and would\n"
        " * cancel out if merged into one path.\n"
        " */\n\n"
        "export interface GlyphPath {\n  d: string;\n  evenOdd?: boolean;\n}\n\n"
        "/** Medium optical size (24 px and up in the UI): fans with three swept blades. Box x 6-58, y 14-52. */\n"
        "export const GLYPH_MEDIUM: readonly GlyphPath[] = [\n" + paths("md") + "\n];\n\n"
        "/** Small optical size: the 16 px drawing, every edge on a whole pixel in a 0 0 64 64 box. */\n"
        "export const GLYPH_SMALL: readonly GlyphPath[] = [\n" + paths("sm") + "\n];\n\n"
        "/** Inside the 64-unit tile, 24-96 px: fans as rings with a hub, pixel-exact at 32 px. */\n"
        "export const GLYPH_TILE: readonly GlyphPath[] = [\n" + paths("tile_md") + "\n];\n\n"
        "/** Inside the 64-unit tile at 16-20 px: the 16 px drawing. */\n"
        "export const GLYPH_TILE_SMALL: readonly GlyphPath[] = [\n" + paths("tile_sm") + "\n];\n"
    )


# ── Rendering ───────────────────────────────────────────────────────────────
def png_bytes(svg_text: str, size: int, height: int | None = None) -> Image.Image:
    out = subprocess.run(
        ["rsvg-convert", "-w", str(size), "-h", str(height or size)],
        input=svg_text.encode(),
        capture_output=True,
        check=True,
    )
    return Image.open(io.BytesIO(out.stdout)).convert("RGBA")


def _box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Box blur along both axes (edge-clamped), via cumulative sums."""
    for axis in (0, 1):
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis)
        hi = np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
        lo = np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)
        a = (hi - lo) / (2 * r + 1)
    return a


def smooth_png(full_svg: str, bg_svg: str, width: int, height: int | None = None) -> Image.Image:
    """Render without gradient banding.

    rsvg-convert quantises dark gradients to 8 bits, which shows as stripes on
    large icons. The background-only render is blurred back into a continuous
    ramp (alpha-weighted, so edges don't bleed), dithered, and the foreground
    is added back as the difference between the full and background renders.
    """
    full = np.asarray(png_bytes(full_svg, width, height), dtype=np.float64)
    bg = np.asarray(png_bytes(bg_svg, width, height), dtype=np.float64)
    alpha = bg[..., 3:4] / 255.0
    r = max(2, round(max(full.shape[:2]) / 48))
    num, den = bg[..., :3] * alpha, alpha
    for _ in range(3):
        num, den = _box_blur(num, r), _box_blur(den, r)
    smooth = np.where(den > 1e-6, num / np.maximum(den, 1e-6), bg[..., :3])
    rng = np.random.default_rng(950)
    noise = rng.random(smooth.shape) - rng.random(smooth.shape)  # triangular, +-1 LSB
    rgb = smooth + (full[..., :3] - bg[..., :3]) + noise
    # Only the opaque interior is re-graded; edges, shadows and transparency stay as rendered.
    rgb = np.where(bg[..., 3:4] >= 255, rgb, full[..., :3])
    out = np.concatenate([np.clip(np.rint(rgb), 0, 255), full[..., 3:4]], axis=-1)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def icon_png(size: int) -> Image.Image:
    """The tile icon at `size` px, at the optical size that renders crisply there."""
    if size <= 20:
        return png_bytes(svg(64, 64, tile_mark("i16-", "16")), size)
    if size <= 96:
        return png_bytes(svg(64, 64, tile_mark("ism-", "small")), size)
    return smooth_png(svg(64, 64, tile_mark("ilg-")), svg(64, 64, tile_mark("ilg-", fg=False)), size)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--satoshi", required=True, type=Path, help="folder with Satoshi-Bold.otf and Satoshi-Medium.otf")
    ap.add_argument("--jetbrains-mono", required=True, type=Path, help="JetBrainsMono-Medium.ttf")
    args = ap.parse_args()
    if not shutil.which("rsvg-convert"):
        print("rsvg-convert not found (brew install librsvg / apt install librsvg2-bin)", file=sys.stderr)
        return 1
    bold = Outliner(args.satoshi / "Satoshi-Bold.otf")
    medium = Outliner(args.satoshi / "Satoshi-Medium.otf")
    mono = Outliner(args.jetbrains_mono)

    out = HERE
    files = {
        "mark.svg": svg(64, 64, tile_mark("gd-")),
        "mark-small.svg": svg(64, 64, tile_mark("gds-", "small")),
        "mark-16.svg": svg(16, 16, tile_mark("gd16-", "16"), view="0 0 64 64"),
        "mark-light.svg": svg(64, 64, tile_mark("gdl-", light=True)),
        "glyph-on-light.svg": glyph_file(INK, detail=True),
        "glyph-on-dark.svg": glyph_file(PAPER, detail=True),
        "glyph-mono.svg": glyph_file("currentColor", detail=True),
        "glyph-small.svg": glyph_file("currentColor", detail=False),
        "glyph-16.svg": svg(16, 16, glyph("currentColor", "sm"), view="0 0 64 64"),
        "glyph-adaptive.svg": glyph_adaptive(),
        "glyph-tray.svg": svg(GLYPH_W, GLYPH_H, glyph("#000000", "tray"), view=GLYPH_VIEW),
        "wordmark-light.svg": wordmark(bold, INK),
        "wordmark-dark.svg": wordmark(bold, PAPER),
        "lockup-light.svg": lockup(bold, INK, "ll-"),
        "lockup-dark.svg": lockup(bold, PAPER, "ld-"),
        "lockup-stacked-light.svg": lockup_stacked(bold, INK, "lsl-"),
        "lockup-stacked-dark.svg": lockup_stacked(bold, PAPER, "lsd-"),
        "app-icon-1024.svg": app_icon(),
        "app-icon-light-1024.svg": app_icon(light=True),
        "social-preview.svg": social_preview(bold, medium, mono),
    }
    for name, text in files.items():
        write(out / name, text)

    smooth_png(files["social-preview.svg"], social_preview(bold, medium, mono, fg=False), 1280, 640).save(
        out / "social-preview.png", optimize=True
    )

    # Tile icon PNGs at the sizes the favicon and the PWA manifest need, each at its optical size.
    png = out / ".build" / "png"
    png.mkdir(parents=True, exist_ok=True)
    images = {}
    for size in [16, 32, 48, 192, 512]:
        images[size] = icon_png(size)
        images[size].save(png / f"icon-{size}.png", optimize=True)
    images[48].save(png / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=[images[16], images[32]])
    # Full-bleed variants: iOS rounds apple-touch icons itself; maskable PWA icons keep the
    # glyph inside the 80 % safe circle.
    smooth_png(
        svg(64, 64, tile_mark("gda-", full_bleed=True)), svg(64, 64, tile_mark("gda-", full_bleed=True, fg=False)), 180
    ).convert("RGB").save(png / "apple-touch-icon.png", optimize=True)
    mask_bg = tile("gdm-", "", full_bleed=True)
    maskable = svg(
        64, 64, mask_bg + f'<g transform="translate(7.04 7.04) scale(0.78)">{glyph("#FFFFFF", "md", detail=True)}</g>'
    )
    smooth_png(maskable, svg(64, 64, mask_bg), 512).convert("RGB").save(png / "icon-maskable-512.png", optimize=True)

    # Web app (apps/web/public).
    brand = WEB_PUBLIC / "brand"
    brand.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(png / "favicon.ico", WEB_PUBLIC / "favicon.ico")
    shutil.copyfile(out / "mark-small.svg", WEB_PUBLIC / "favicon.svg")
    shutil.copyfile(png / "apple-touch-icon.png", WEB_PUBLIC / "apple-touch-icon.png")
    shutil.copyfile(out / "lockup-light.svg", WEB_PUBLIC / "logo.svg")
    shutil.copyfile(out / "lockup-dark.svg", WEB_PUBLIC / "logo-dark.svg")
    shutil.copyfile(out / "mark.svg", brand / "gpudeck-mark.svg")
    shutil.copyfile(png / "icon-192.png", brand / "gpudeck-icon-192.png")
    shutil.copyfile(png / "icon-512.png", brand / "gpudeck-icon-512.png")
    shutil.copyfile(png / "icon-maskable-512.png", brand / "gpudeck-icon-maskable-512.png")
    write(ROOT / "apps" / "web" / "components" / "shared" / "gpu-deck-glyph.ts", glyph_paths_ts())

    # Website (static site in website/): the same file names it already links to.
    if WEBSITE.is_dir():
        for name in ("favicon.ico", "apple-touch-icon.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png"):
            shutil.copyfile(png / name, WEBSITE / name)
        shutil.copyfile(out / "mark-small.svg", WEBSITE / "favicon.svg")
        site_brand = WEBSITE / "assets" / "brand"
        site_brand.mkdir(parents=True, exist_ok=True)
        for name in ("lockup-light.svg", "lockup-dark.svg", "lockup-stacked-light.svg", "lockup-stacked-dark.svg", "mark.svg", "glyph-mono.svg"):
            shutil.copyfile(out / name, site_brand / name)
    print("brand assets written to", out.relative_to(ROOT), "and", WEB_PUBLIC.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
