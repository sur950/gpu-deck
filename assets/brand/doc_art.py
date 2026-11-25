"""Build the brand-styled diagrams of the documentation, a light and a dark SVG of each.

The docs embed them with <picture> (dark source for prefers-color-scheme: dark, light <img> as the
fallback), so GitHub shows the variant that matches the reader's colour scheme and the website
(scripts/build_docs.py) follows its own theme switch. Like the README art, every image carries its
own card, so either variant stays readable on either page colour. Text is converted to outlines
with the website's fonts (Figtree and JetBrains Mono, SIL OFL), through the helpers in
readme_art.py, so nothing depends on fonts the viewer has installed.

Outputs docs/diagrams/<name>-{light,dark}.svg for every diagram in DIAGRAMS. Keep the content in
step with the docs that embed them (the status marks come from docs/TESTING.md, the support marks
from docs/hardware/README.md) and run:

    python3 -m venv /tmp/brand-venv && /tmp/brand-venv/bin/pip install -r scripts/requirements-art.txt
    /tmp/brand-venv/bin/python assets/brand/doc_art.py           # write the SVGs
    /tmp/brand-venv/bin/python assets/brand/doc_art.py --check   # exit 1 when anything is stale
"""

from __future__ import annotations

import argparse
import html
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = ROOT / "docs" / "diagrams"
sys.path.insert(0, str(HERE))

from readme_art import FONTS, ICONS as BRAND_ICONS, Face  # noqa: E402

W = 880  # every diagram is 880 units wide (the README width); the website scales it to its column
PAD = 24
TYPE_SCALE = 1.1  # every text size below is drawn this much larger, so it stays legible at ~83 % in the docs column

# ── Palette (docs/BRAND.md): warm neutrals, crimson the only accent, status colours for state ──
THEMES = {
    "light": {
        "card": "#F7F6F3", "card_line": "#E2E0DA", "dots": "#111215", "dots_a": 0.08,
        "box": "#FFFFFF", "box_line": "#E2E0DA", "chip": "#F4F3F0", "chip_line": "#E2E0DA",
        "text": "#111215", "text2": "#5B5E66", "wire": "#7D808A", "life": "#C9C6BF",
        "accent": "#B3122E", "accent_text": "#B3122E",
        "slab": "#111215", "slab_line": "#111215",
        "ok": "#1F8A5B", "warn": "#C29100", "warn_text": "#8F6A00", "err_text": "#A8381A",
        "series": ("#B3122E", "#3B6EB9", "#C38323", "#00A291", "#753A7A"), "other": "#A9A7A0",
    },
    "dark": {
        "card": "#0E0F12", "card_line": "#2A2B30", "dots": "#FFFFFF", "dots_a": 0.055,
        "box": "#15161A", "box_line": "#2A2B30", "chip": "#1F2025", "chip_line": "#33343A",
        "text": "#F2F1EE", "text2": "#A1A3AB", "wire": "#7D808A", "life": "#3A3B41",
        "accent": "#D3263F", "accent_text": "#F2687B",
        "slab": "#1B1C20", "slab_line": "#D3263F",
        "ok": "#34A873", "warn": "#E8B83A", "warn_text": "#E8B83A", "err_text": "#F7875A",
        "series": ("#D3263F", "#5286D3", "#B8833B", "#20A594", "#935799"), "other": "#5A5C63",
    },
}
SLAB_TEXT, SLAB_TEXT2 = "#F2F1EE", "#A1A3AB"  # text on the GPU Deck slab (dark in both themes)
INK, WHITE = "#111215", "#FFFFFF"

# Stroke icons on a 24-unit grid; the first six are the website's (readme_art.ICONS).
ICONS = dict(BRAND_ICONS)
ICONS.update({
    "user": '<circle cx="12" cy="8.5" r="3.6"/><path d="M5 19.5c1.2-3.5 3.9-5.2 7-5.2s5.8 1.7 7 5.2"/>',
    "db": ('<ellipse cx="12" cy="6" rx="7" ry="2.8"/>'
           '<path d="M5 6v12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8"/>'),
    "shield": '<path d="M12 3.5 5 6.2v5.3c0 4.3 2.9 7.6 7 9 4.1-1.4 7-4.7 7-9V6.2Z"/><path d="m9 12 2.2 2.2L15.2 10"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "check": '<path d="m5.5 12.5 4 4L18.5 7.5"/>',
    "gpu": ('<path d="M3.5 5v15"/><rect x="6" y="7" width="15" height="10" rx="1.6"/>'
            '<circle cx="10.5" cy="12" r="2.4"/><circle cx="16.5" cy="12" r="2.4"/><path d="M9 17v2.5h7V17"/>'),
    "plug": '<path d="M9 3.5v5M15 3.5v5M6.5 8.5h11v3a5.5 5.5 0 0 1-11 0ZM12 17v3.5"/>',
    "network": ('<circle cx="12" cy="5.5" r="2.2"/><circle cx="5.5" cy="18.5" r="2.2"/><circle cx="18.5" cy="18.5" r="2.2"/>'
                '<path d="M12 7.7V12m0 0-5 4.6M12 12l5 4.6"/>'),
    "list": '<path d="M9.5 7h10M9.5 12h10M9.5 17h10M5 7h.01M5 12h.01M5 17h.01"/>',
    "swap": '<path d="M4 8.5h14.5L15 5M20 15.5H5.5L9 19"/>',
    "alert": '<path d="M12 4 3 19.5h18Z"/><path d="M12 10v4.5M12 17h.01"/>',
    "coin": ('<circle cx="12" cy="12" r="8.5"/><path d="M14.8 9.4c-.6-.9-1.6-1.4-2.8-1.4-1.6 0-2.8.8-2.8 2 0 2.9 5.8 1.5 5.8 4.4 '
             '0 1.2-1.3 2-2.9 2-1.3 0-2.5-.6-3-1.6M12 6.2V8m0 8.4v1.8"/>'),
    "share": '<rect x="3.5" y="7" width="17" height="10" rx="1.6"/><path d="M11 7v10M15.5 7v10"/>',
    "history": '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 9"/><path d="M4.5 4.5V9H9M12 8v4l2.8 1.8"/>',
    "bolt": '<path d="M13 3 5.5 13.5H12L11 21l7.5-10.5H12Z"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3"/>',
})


# ── Fonts (text becomes outlines; glyphs are defined once per file and reused) ──
class Fonts:
    def __init__(self) -> None:
        sans = FONTS / "figtree-latin-wght.woff2"
        self.title = Face("a", sans, 640)
        self.body = Face("b", sans, 500)
        self.mono = Face("c", FONTS / "jetbrains-mono-latin-wght.woff2", 500)

    def reset(self) -> None:
        for f in (self.title, self.body, self.mono):
            f.used = {}

    def defs(self) -> str:
        return self.title.defs() + self.body.defs() + self.mono.defs()


def luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def on(fill: str) -> str:
    """Ink or white, whichever reads better on the fill."""
    lum = luminance(fill)
    return INK if (lum + 0.05) / (luminance(INK) + 0.05) > 1.05 / (lum + 0.05) else WHITE


# ── Drawing ─────────────────────────────────────────────────────────────────
class Canvas:
    def __init__(self, theme: str, fonts: Fonts) -> None:
        self.theme = theme
        self.c = THEMES[theme]
        self.f = fonts
        self.parts: list[str] = []
        self.defs: list[str] = []
        fonts.reset()

    def add(self, s: str) -> None:
        self.parts.append(s)

    def face(self, name: str) -> Face:
        return getattr(self.f, name)

    # text --------------------------------------------------------------
    def width(self, s: str, size: float, face: str = "body", track: float = 0.0) -> float:
        return self.face(face).width(s, size * TYPE_SCALE, track)

    def text(self, s: str, x: float, cy: float, size: float = 13, face: str = "body", fill: str | None = None,
             anchor: str = "start", track: float = 0.0, upper: bool = False, opacity: float | None = None) -> float:
        """Draw one line with its capitals centred on cy; returns the width."""
        f = self.face(face)
        s = s.upper() if upper else s
        for gid, _ in f.shape(s, track):
            if gid == 0:
                raise SystemExit(f"doc_art.py: the {face} font has no glyph for a character in {s!r}")
        size *= TYPE_SCALE
        w = f.width(s, size, track)
        if anchor == "middle":
            x -= w / 2
        elif anchor == "end":
            x -= w
        self.add(f.run(s, size, x, cy + f.cap * size / 2, fill or self.c["text"], track, opacity))
        return w

    def eyebrow(self, s: str, x: float, cy: float, fill: str | None = None, anchor: str = "start") -> float:
        return self.text(s, x, cy, 10.5, "mono", fill or self.c["text2"], anchor, 0.11, upper=True)

    def wrap(self, s: str, size: float, maxw: float, face: str = "body") -> list[str]:
        lines: list[str] = []
        for para in s.split("\n"):
            cur = ""
            for word in para.split(" "):
                trial = f"{cur} {word}".strip()
                if cur and self.width(trial, size, face) > maxw:
                    lines.append(cur)
                    cur = word
                else:
                    cur = trial
            lines.append(cur)
        return lines

    def para(self, s: str, x: float, top: float, maxw: float, size: float = 12.5, lh: float = 18,
             face: str = "body", fill: str | None = None, anchor: str = "start") -> float:
        """A wrapped paragraph whose first line box starts at top; returns the bottom."""
        lines = self.wrap(s, size, maxw, face)
        for i, line in enumerate(lines):
            self.text(line, x, top + i * lh + lh / 2, size, face, fill or self.c["text2"], anchor)
        return top + len(lines) * lh

    # shapes ------------------------------------------------------------
    def rect(self, x: float, y: float, w: float, h: float, rx: float = 10, fill: str = "none",
             stroke: str | None = None, sw: float = 1, dash: str | None = None,
             fo: float | None = None, so: float | None = None) -> None:
        if stroke:  # keep the stroke inside the box and on the pixel grid
            x, y, w, h = x + sw / 2, y + sw / 2, w - sw, h - sw
        a = f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{rx}" fill="{fill}"'
        if fo is not None:
            a += f' fill-opacity="{fo}"'
        if stroke:
            a += f' stroke="{stroke}" stroke-width="{sw}"'
            if so is not None:
                a += f' stroke-opacity="{so}"'
            if dash:
                a += f' stroke-dasharray="{dash}"'
        self.add(a + "/>")

    def box(self, x: float, y: float, w: float, h: float, rx: float = 12) -> None:
        self.rect(x, y, w, h, rx, self.c["box"], self.c["box_line"])

    def slab(self, x: float, y: float, w: float, h: float, rx: float = 14) -> None:
        """GPU Deck itself: the dark slab with the crimson top edge (as in the ecosystem diagram)."""
        c = self.c
        gid = f"slab-glow-{len(self.defs)}"
        self.defs.append(f'<radialGradient id="{gid}" cx="12%" cy="0%" r="90%">'
                         '<stop offset="0" stop-color="#D3263F" stop-opacity=".28"/>'
                         '<stop offset="1" stop-color="#D3263F" stop-opacity="0"/></radialGradient>')
        self.rect(x, y, w, h, rx, c["slab"])
        self.rect(x, y, w, h, rx, f"url(#{gid})", c["slab_line"], so=0.55 if self.theme == "dark" else 1)
        self.add(f'<path d="M{x + 22} {y + .75:.2f}H{x + w - 22}" stroke="#D3263F" stroke-width="1.5" stroke-linecap="round"/>')

    def chip(self, s: str, x: float, cy: float, h: float = 28, size: float = 12.5, face: str = "body",
             icon: str | None = None, w: float | None = None, on_slab: bool = False, fill: str | None = None,
             stroke: str | None = None, color: str | None = None, center: bool = False) -> float:
        pad = 11
        iw = 20 if icon else 0
        tw = self.width(s, size, face)
        w = w or tw + 2 * pad + iw
        y = cy - h / 2
        if on_slab:
            self.add(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h}" rx="8" fill="#FFFFFF" '
                     'fill-opacity=".07" stroke="#FFFFFF" stroke-opacity=".16"/>')
            color = color or SLAB_TEXT
        else:
            self.rect(x, y, w, h, 8, fill or self.c["chip"], stroke or self.c["chip_line"])
            color = color or self.c["text"]
        tx = x + (w - tw - iw) / 2 if center else x + pad
        if icon:
            self.icon(icon, tx, cy - 7.5, 15, SLAB_TEXT2 if on_slab else self.c["text2"])
            tx += iw
        self.text(s, tx, cy, size, face, color)
        return w

    def icon(self, name: str, x: float, y: float, size: float, color: str, sw: float = 1.7) -> None:
        s = size / 24
        self.add(f'<g transform="translate({x:.2f} {y:.2f}) scale({s:.4f})" fill="none" stroke="{color}" '
                 f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g>')

    def badge(self, n: str, cx: float, cy: float, r: float = 10, fill: str | None = None) -> None:
        fill = fill or self.c["accent"]
        self.add(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r if len(n) == 1 else r + 1.5}" fill="{fill}"/>')
        self.text(n, cx, cy, 11 if len(n) == 1 else 9.5, "mono", WHITE, "middle", -0.04 if len(n) > 1 else 0)

    def gpu_cells(self, x: float, y: float, loads: tuple[float, ...], cell: float = 12, gap: float = 5) -> None:
        """GPU load as crimson density (the website's motif)."""
        for i, a in enumerate(loads):
            self.add(f'<rect x="{x + i * (cell + gap):.2f}" y="{y:.2f}" width="{cell}" height="{cell}" rx="2.5" '
                     f'fill="#D3263F" fill-opacity="{a * (0.9 if self.theme == "light" else 1):.2f}"/>')

    # wires -------------------------------------------------------------
    def wire(self, pts: list[tuple[float, float]], color: str | None = None, sw: float = 1.5,
             dash: str | None = None, head: str = "end", r: float = 9) -> None:
        color = color or self.c["wire"]
        hl = 7.5  # arrowhead length
        p = [list(q) for q in pts]

        def pull(a: list[float], b: list[float]) -> None:  # shorten segment end b towards a by the head length
            dx, dy = b[0] - a[0], b[1] - a[1]
            d = math.hypot(dx, dy) or 1
            b[0] -= dx / d * hl * 0.8
            b[1] -= dy / d * hl * 0.8

        tip_end, tip_start = tuple(p[-1]), tuple(p[0])
        if head in ("end", "both"):
            pull(p[-2], p[-1])
        if head in ("start", "both"):
            pull(p[1], p[0])
        d = [f"M{p[0][0]:.2f} {p[0][1]:.2f}"]
        for i in range(1, len(p) - 1):
            a, b, nxt = p[i - 1], p[i], p[i + 1]
            l1 = math.hypot(b[0] - a[0], b[1] - a[1]) or 1
            l2 = math.hypot(nxt[0] - b[0], nxt[1] - b[1]) or 1
            rr = min(r, l1 / 2, l2 / 2)
            s = (b[0] - (b[0] - a[0]) / l1 * rr, b[1] - (b[1] - a[1]) / l1 * rr)
            e = (b[0] + (nxt[0] - b[0]) / l2 * rr, b[1] + (nxt[1] - b[1]) / l2 * rr)
            d.append(f"L{s[0]:.2f} {s[1]:.2f}Q{b[0]:.2f} {b[1]:.2f} {e[0]:.2f} {e[1]:.2f}")
        d.append(f"L{p[-1][0]:.2f} {p[-1][1]:.2f}")
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<path d="{"".join(d)}" fill="none" stroke="{color}" stroke-width="{sw}" '
                 f'stroke-linecap="round" stroke-linejoin="round"{extra}/>')
        if head in ("end", "both"):
            self.arrowhead(pts[-2], tip_end, color, hl)
        if head in ("start", "both"):
            self.arrowhead(pts[1], tip_start, color, hl)

    def arrowhead(self, a: tuple[float, float], tip: tuple[float, float], color: str, hl: float = 7.5) -> None:
        ang = math.atan2(tip[1] - a[1], tip[0] - a[0])
        hw = hl * 0.58
        bx, by = tip[0] - math.cos(ang) * hl, tip[1] - math.sin(ang) * hl
        px, py = -math.sin(ang) * hw, math.cos(ang) * hw
        self.add(f'<path d="M{tip[0]:.2f} {tip[1]:.2f}L{bx + px:.2f} {by + py:.2f}L{bx - px:.2f} {by - py:.2f}Z" '
                 f'fill="{color}"/>')

    def label_on_wire(self, s: str, cx: float, cy: float, size: float = 11.5, face: str = "body",
                      color: str | None = None) -> None:
        """Text over a wire, with a card-coloured pad behind it."""
        w = self.width(s, size, face)
        self.rect(cx - w / 2 - 6, cy - 9, w + 12, 18, 9, self.c["card"])
        self.text(s, cx, cy, size, face, color or self.c["text2"], "middle")

    # document ----------------------------------------------------------
    def document(self, height: float, eyebrow: str, title: str, desc: str) -> str:
        c = self.c
        h = math.ceil(height)
        head = (f'<rect x=".5" y=".5" width="{W - 1}" height="{h - 1}" rx="18.5" fill="{c["card"]}" stroke="{c["card_line"]}"/>'
                f'<rect x="1" y="1" width="{W - 2}" height="{h - 2}" rx="18" fill="url(#dots)"/>')
        mark = self.parts
        self.parts = []
        self.eyebrow(eyebrow, PAD, 30, c["accent_text"])
        body = head + "".join(self.parts) + "".join(mark)
        defs = (f'<defs><pattern id="dots" width="20" height="20" patternUnits="userSpaceOnUse">'
                f'<circle cx="10" cy="10" r=".9" fill="{c["dots"]}" fill-opacity="{c["dots_a"]}"/></pattern>'
                + "".join(self.defs) + self.f.defs() + "</defs>")
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img" '
                f'aria-labelledby="t d"><title id="t">{html.escape(title)}</title><desc id="d">{html.escape(desc)}</desc>'
                f"{defs}{body}</svg>\n")


def head_text(cv: Canvas, icon: str | None, title: str, x: float, cy: float, size: float = 14.5,
              color: str | None = None, icon_color: str | None = None) -> float:
    """An icon and a title on one line; returns the x after the title."""
    if icon:
        cv.icon(icon, x, cy - 8.5, 17, icon_color or cv.c["text2"])
        x += 25
    return x + cv.text(title, x, cy, size, "title", color)


# ── 1. System overview ──────────────────────────────────────────────────────
def system_overview(cv: Canvas) -> tuple[float, str, str, str]:
    c = cv.c
    top = 56
    lx, lw = PAD, 196
    mx, mw = 280, 304
    rx_, rw = 664, W - PAD - 664
    bh, gap = 124, 18

    # who calls it
    for i, (icon, title, body) in enumerate((
        ("braces", "Clients", "Your apps, SDKs or an AI gateway, in OpenAI, Anthropic or Ollama format"),
        ("user", "Admins", "Web UI, desktop app, CLI and Terraform"),
    )):
        y = top + i * (bh + gap)
        cv.box(lx, y, lw, bh)
        head_text(cv, icon, title, lx + 16, y + 24, 14)
        cv.para(body, lx + 16, y + 40, lw - 32)

    # the control plane
    sh = 2 * bh + gap
    cv.slab(mx, top, mw, sh)
    cv.text("GPU Deck control plane", mx + 18, top + 25, 15, "title", SLAB_TEXT)
    cv.text("One CPU server: Docker Compose or Helm", mx + 18, top + 45, 12, "body", SLAB_TEXT2)
    cw = (mw - 36 - 8) / 2
    rows = (("Serving API :8181", "Agent hub :8443"), ("Tunnels :8444", "Jobs, workers"),
            ("Web UI :3180", "Admin API :8180"))
    chip_y = [top + 82, top + 120, top + 158]
    for (a, b), y in zip(rows, chip_y):
        cv.chip(a, mx + 18, y, w=cw, on_slab=True, size=12)
        cv.chip(b, mx + 18 + cw + 8, y, w=cw, on_slab=True, size=12)
    cv.add(f'<path d="M{mx + 18} {top + 188}H{mx + mw - 18}" stroke="#FFFFFF" stroke-opacity=".12"/>')
    cv.chip("PostgreSQL", mx + 18, top + 214, w=cw, on_slab=True, icon="db", size=12)
    cv.chip("Redis", mx + 18 + cw + 8, top + 214, w=cw, on_slab=True, icon="db", size=12)
    cv.icon("chip", mx + 18, top + sh - 25.5, 15, SLAB_TEXT2)
    cv.text("No GPU, CUDA or drivers here", mx + 40, top + sh - 18, 12, "body", SLAB_TEXT2)

    # client wires into the slab
    for y, label in ((chip_y[0], "API key"), (chip_y[2], "session")):
        cv.wire([(lx + lw, y), (mx, y)])
        cv.text(label, (lx + lw + mx) / 2, y - 11, 10.5, "mono", c["text2"], "middle")

    # GPU machines dial out
    loads = ((0.95, 0.7, 0.35, 0.85, 0.5, 0.2), (0.3, 0.8, 0.6, 0.15, 0.9, 0.45))
    for i, sub in enumerate(("Linux (systemd)", "macOS or Kubernetes")):
        y = top + i * (bh + gap)
        cv.box(rx_, y, rw, bh)
        head_text(cv, "gpu", "GPU machine", rx_ + 16, y + 24, 14)
        cv.text(sub, rx_ + 16, y + 45, 12, "body", c["text"])
        cv.para("Node agent; engines bound to 127.0.0.1", rx_ + 16, y + 54, rw - 32, 12, 17)
        cv.gpu_cells(rx_ + 16, y + 98, loads[i])
        wy = y + 44
        cv.wire([(rx_, wy), (mx + mw, wy)], c["accent"], 1.75)
        cv.text("dials out", (rx_ + mx + mw) / 2, wy - 11, 10.5, "mono", c["accent_text"], "middle")
        cv.text("mTLS", (rx_ + mx + mw) / 2, wy + 13, 10.5, "mono", c["text2"], "middle")

    y = top + sh + 26
    y = cv.para("GPU machines open no inbound port by default: the agent dials out to the agent hub (8443) and the "
                "API layer's tunnels (8444), and inference requests come back over that connection. On a private "
                "network, direct mode lets the API layer reach the agent on 9443 instead.", PAD, y, W - 2 * PAD)
    desc = ("Apps and AI gateways call the GPU Deck control plane's serving API with an API key, and admins use the "
            "web UI, desktop app, CLI or Terraform. The control plane runs on one CPU server with Docker Compose or "
            "Helm: serving API, agent hub, agent tunnels, jobs and workers, web UI, admin API, PostgreSQL and Redis, "
            "and needs no GPU. GPU machines run only the node agent and the engines on localhost; the agent dials "
            "out to the control plane over mutual TLS, so a GPU machine opens no inbound port by default.")
    return y + PAD - 4, "System overview", "GPU Deck system overview", desc


# ── 2. Node join sequence ───────────────────────────────────────────────────
def node_join(cv: Canvas) -> tuple[float, str, str, str]:
    c = cv.c
    xs = {"you": 130, "cp": 400, "gpu": 712}
    top = 54
    ph = 52
    parts = (("you", "user", "You", "web UI or CLI"),
             ("cp", "server", "Control plane", "admin API + agent hub"),
             ("gpu", "gpu", "GPU machine", "installer, then the agent"))
    row0, rh = top + ph + 38, 40
    steps = (
        ("you", "cp", "Create a join token: Nodes, Add node", "solid"),
        ("cp", "you", "Token and install command, shown once", "dash"),
        ("you", "gpu", "Run the install command", "solid"),
        ("gpu", "cp", "Fetch the CA (pinned sha256) and the agent", "solid"),
        ("gpu", "gpu", "Generate a key and a CSR", "self"),
        ("gpu", "cp", "Join with the token and the CSR", "solid"),
        ("cp", "gpu", "Signed node certificate; token used up", "dash"),
        ("gpu", "cp", "Dial out over mTLS (WebSocket), hello", "accent"),
        ("cp", "gpu", "Config push: the node is online", "dash"),
        ("gpu", "cp", "Heartbeats, metrics, inventory", "accent"),
        ("cp", "gpu", "Commands: load, unload, drain, swap", "dash"),
    )
    bottom = row0 + len(steps) * rh + 6
    # lifelines first, so everything else sits on top
    for key, *_ in parts:
        cv.add(f'<path d="M{xs[key]} {top + ph}V{bottom}" stroke="{c["life"]}" stroke-width="1.25" stroke-dasharray="3 4"/>')
    # the repeating part
    ly = row0 + 9 * rh - rh / 2 - 6
    lh_ = 2 * rh + 6
    cv.rect(xs["cp"] - 150, ly, xs["gpu"] - xs["cp"] + 190, lh_, 10, c["box"], c["box_line"], fo=0.6)
    cv.eyebrow("while", xs["cp"] - 136, ly + lh_ / 2 - 8)
    cv.eyebrow("connected", xs["cp"] - 136, ly + lh_ / 2 + 8)
    for key, icon, title, sub in parts:
        x = xs[key] - 95
        if key == "cp":
            cv.slab(x, top, 190, ph, 12)
            cv.icon(icon, x + 14, top + 17.5, 17, SLAB_TEXT2)
            cv.text(title, x + 39, top + 19, 14, "title", SLAB_TEXT)
            cv.text(sub, x + 39, top + 37, 11.5, "body", SLAB_TEXT2)
        else:
            cv.box(x, top, 190, ph)
            cv.icon(icon, x + 14, top + 17.5, 17, c["text2"])
            cv.text(title, x + 39, top + 19, 14, "title")
            cv.text(sub, x + 39, top + 37, 11.5, "body", c["text2"])
    for i, (a, b, label, kind) in enumerate(steps):
        y = row0 + i * rh
        n = str(i + 1)
        if kind == "self":
            x = xs[a]
            cv.wire([(x, y - 8), (x + 40, y - 8), (x + 40, y + 8), (x, y + 8)], r=6)
            w = cv.width(label, 12.5)
            cv.badge(n, x - w - 34, y)
            cv.text(label, x - w - 16, y, 12.5, "body", c["text"])
            continue
        color = c["accent"] if kind == "accent" else c["wire"]
        cv.wire([(xs[a], y), (xs[b], y)], color, 1.6 if kind == "accent" else 1.4,
                dash="5 4" if kind == "dash" else None)
        lo, hi = sorted((xs[a], xs[b]))
        if a == "you" and b == "gpu":
            lo = xs["cp"]
        w = cv.width(label, 12.5) + 26
        x0 = (lo + hi) / 2 - w / 2
        cv.rect(x0 - 6, y - 21, w + 12, 18, 9, c["card"])
        cv.badge(n, x0 + 9, y - 12, 8.5)
        cv.text(label, x0 + 24, y - 12, 12.5, "body", c["text"])
    y = cv.para("The token is stored only as a hash and expires; the certificate rotates on its own before it "
                "expires, and revoking it closes the node's socket at once.", PAD, bottom + 10, W - 2 * PAD)
    desc = ("How a GPU machine joins. 1: you create a join token in the web UI (Nodes, Add node). 2: the control "
            "plane shows the token and the install command once. 3: you run the command on the GPU machine. 4: it "
            "fetches the control plane's CA, checks it against the pinned sha256, and fetches the agent. 5: the agent "
            "generates a key and a certificate signing request. 6: it joins with the token and the request. 7: the "
            "control plane signs the node certificate and the token is used up. 8: the agent dials out over mutual "
            "TLS on a WebSocket and says hello. 9: a config push follows and the node is online. 10 and 11, repeating: "
            "heartbeats, metrics and inventory go up; commands such as load, unload, drain and swap come back on the "
            "same socket.")
    return y + PAD - 4, "Joining a GPU machine", "How a GPU machine joins GPU Deck", desc


# ── 3. Request path ─────────────────────────────────────────────────────────
def request_path(cv: Canvas) -> tuple[float, str, str, str]:
    c = cv.c
    top, h = 56, 186
    g = 30
    xs = [PAD]
    widths = [148, 268, 160, W - PAD - (PAD + 148 + 268 + 160 + 3 * g)]
    for w in widths[:-1]:
        xs.append(xs[-1] + w + g)
    flow_y = top + 26
    # 1 client
    x, w = xs[0], widths[0]
    cv.box(x, top, w, h)
    head_text(cv, "braces", "Client", x + 16, flow_y, 14)
    cv.para("Your app, an SDK or an AI gateway, with a gd- API key", x + 16, top + 44, w - 32)
    # 2 the API layer (GPU Deck)
    x, w = xs[1], widths[1]
    cv.slab(x, top, w, h)
    cv.text("API layer :8181", x + 18, flow_y, 14.5, "title", SLAB_TEXT)
    checks = ("IP rules", "API key, from a cache", "Model: routing table in memory",
              "Pick a replica: the nearest one that is not busy (or round robin, weights)")
    y = top + 50
    for i, s in enumerate(checks):
        lines = cv.wrap(s, 12.5, w - 64)
        cv.add(f'<circle cx="{x + 29}" cy="{y + 9:.2f}" r="9" fill="#FFFFFF" fill-opacity=".08" stroke="#FFFFFF" stroke-opacity=".2"/>')
        cv.text(str(i + 1), x + 29, y + 9, 10.5, "mono", SLAB_TEXT2, "middle")
        for j, line in enumerate(lines):
            cv.text(line, x + 46, y + 9 + j * 17, 12.5, "body", SLAB_TEXT)
        y += 17 * len(lines) + 11
    # 3 node agent
    x, w = xs[2], widths[2]
    cv.box(x, top, w, h)
    head_text(cv, "lock", "Node agent", x + 16, flow_y, 14)
    cv.para("Over its outbound mTLS tunnel (or the 9443 proxy in direct mode)", x + 16, top + 44, w - 32)
    # 4 engine
    x, w = xs[3], widths[3]
    cv.box(x, top, w, h)
    head_text(cv, "gauge", "Engine", x + 16, flow_y, 14)
    cv.para("On 127.0.0.1, on its GPUs or a slice", x + 16, top + 44, w - 32)
    cv.gpu_cells(x + 16, top + h - 34, (0.9, 0.75, 0.55, 0.3))
    # request wires
    for i in range(3):
        cv.wire([(xs[i] + widths[i], flow_y), (xs[i + 1], flow_y)], c["accent"], 1.75)
    # response back under the boxes
    ry = top + h + 30
    ex = xs[3] + widths[3] / 2
    cx_ = xs[0] + widths[0] / 2
    cv.wire([(ex, top + h), (ex, ry), (cx_, ry), (cx_, top + h)], c["wire"], 1.4, dash="5 4")
    cv.label_on_wire("Tokens stream back frame by frame; the model name is rewritten", (xs[2] + xs[1] + widths[1]) / 2 + 20, ry, 12)
    # usage counters branch
    mx = xs[1] + widths[1] / 2
    by = ry + 34
    cv.wire([(mx - 60, ry), (mx - 60, by)], c["wire"], 1.4, dash="2 4")
    bw = widths[1] + 40
    bx = xs[1] - 20
    cv.rect(bx, by, bw, 76, 12, c["box"], c["box_line"], dash="4 4")
    head_text(cv, "coin", "Usage counters, off the hot path", bx + 16, by + 22, 13.5)
    cv.para("After the last frame a usage event is queued; a worker writes it and rolls it up hourly.",
            bx + 16, by + 34, bw - 32, 12)
    # facts
    fx = xs[2]
    fy = by + 4
    for i, (icon, s) in enumerate((("bolt", "No database read on the request path"),
                                   ("swap", "Failover to another replica before the first byte"),
                                   ("shield", "Engines are never reachable from the network"))):
        cv.icon(icon, fx, fy + i * 24 - 1, 15, c["accent_text"])
        cv.text(s, fx + 24, fy + i * 24 + 6.5, 12.5, "body", c["text"])
    end = by + 76
    desc = ("A serving request. The client (an app, SDK or AI gateway with a gd- API key) calls the API layer on "
            "port 8181, which checks IP rules, looks the API key up in a cache, resolves the model in its in-memory "
            "routing table and picks a replica (the nearest one that is not busy, or round robin or weights). The "
            "request goes to the node "
            "agent over its outbound mutual-TLS tunnel (or the 9443 proxy in direct mode) and on to the engine on "
            "127.0.0.1, which runs on its GPUs or a slice. Tokens stream back frame by frame with the model name "
            "rewritten. After the last frame a usage event is queued and a worker writes and rolls it up hourly. "
            "There is no database read on the request path, failover to another replica happens before the first "
            "byte, and engines are never reachable from the network.")
    return end + PAD, "Request path", "The path of a serving request", desc


# ── 4. Safe swap ────────────────────────────────────────────────────────────
def safe_swap(cv: Canvas) -> tuple[float, str, str, str]:
    c = cv.c
    steps = (("Plan", "reserve VRAM, save the rollback"), ("Route off", "no new requests reach it"),
             ("Drain", "in-flight requests finish"), ("Unload", "the old instance"),
             ("Load", "the new instance"), ("Health", "ready, then one probe"), ("Route on", "traffic flows again"))
    n = len(steps)
    span = (W - 2 * PAD) / n
    cxs = [PAD + span * (i + 0.5) for i in range(n)]
    ty = 104
    # phase brackets
    for a, b, label in ((1, 3, "old instance out"), (4, 6, "new instance in")):
        x0, x1 = cxs[a] - 30, cxs[b] + 30
        cv.add(f'<path d="M{x0:.2f} 74V68H{x1:.2f}V74" fill="none" stroke="{c["life"]}" stroke-width="1.25"/>')
        w = cv.width(label.upper(), 10.5, "mono", 0.11) + 14
        cv.rect((x0 + x1) / 2 - w / 2, 59, w, 18, 9, c["card"])
        cv.eyebrow(label, (x0 + x1) / 2, 68, anchor="middle")
    cv.add(f'<path d="M{cxs[0]:.2f} {ty}H{cxs[-1]:.2f}" stroke="{c["box_line"]}" stroke-width="2"/>')
    for i, (title, sub) in enumerate(steps):
        x = cxs[i]
        last = i == n - 1
        cv.add(f'<circle cx="{x:.2f}" cy="{ty}" r="16" fill="{c["accent"] if last else c["box"]}" '
               f'stroke="{c["accent"]}" stroke-width="1.5"/>')
        cv.text(str(i + 1), x, ty, 12, "mono", WHITE if last else c["accent_text"], "middle")
        cv.text(title, x, ty + 34, 13.5, "title", anchor="middle")
        cv.para(sub, x, ty + 44, span - 14, 12, 16, anchor="middle")
    # two cards
    y0, ch = 200, 132
    cw = (W - 2 * PAD - 16) / 2
    cv.box(PAD, y0, cw, ch)
    head_text(cv, "swap", "Make before break", PAD + 18, y0 + 24, 14)
    cv.para("When the GPU has room, the new instance is loaded and healthy before the old one drains, so "
            "capacity never drops.", PAD + 18, y0 + 38, cw - 36)
    x = PAD + 18
    for i, s in enumerate(("1", "2", "5", "6", "3", "4", "7")):
        x += cv.chip(s, x, y0 + ch - 24, h=24, size=12, face="mono", w=28, center=True,
                     fill=c["accent"] if s in "56" else None, stroke=c["accent"] if s in "56" else None,
                     color=WHITE if s in "56" else None) + 6
    bx = PAD + cw + 16
    cv.rect(bx, y0, cw, ch, 12, c["box"], c["box_line"])
    cv.add(f'<path d="M{bx + 1} {y0 + 12}V{y0 + ch - 12}" stroke="{c["warn"]}" stroke-width="2.5" stroke-linecap="round"/>')
    head_text(cv, "alert", "If a step fails", bx + 18, y0 + 24, 14, icon_color=c["warn_text"])
    cv.para("The old instance is routed back on, or reloaded from its saved spec. If that fails too, the swap "
            "ends failed, the deployment is degraded, a critical alert fires and auto-heal can take over.",
            bx + 18, y0 + 38, cw - 36)
    y = cv.para("Every step is saved as it runs, so a new jobs leader resumes a swap where it stopped. Rules and "
                "layouts both run their actions this way.", PAD, y0 + ch + 18, W - 2 * PAD)
    desc = ("The seven steps of a safe swap: 1 plan (reserve VRAM, save the rollback), 2 route off (no new requests "
            "reach the old instance), 3 drain (in-flight requests finish), 4 unload the old instance, 5 load the new "
            "instance, 6 health (ready, then one probe), 7 route on (traffic flows again). Steps 2 to 4 take the old "
            "instance out, 5 to 7 bring the new one in. Make before break: when the GPU has room the order is 1, 2, "
            "5, 6, 3, 4, 7, so capacity never drops. If a step fails, the old instance is routed back on or reloaded "
            "from its saved spec; if that fails too the swap ends failed, the deployment is degraded, a critical "
            "alert fires and auto-heal can take over. Every step is saved, so a new jobs leader resumes the swap.")
    return y + PAD - 4, "Safe swap", "The steps of a safe swap", desc


# ── 5. Rule evaluation loop ─────────────────────────────────────────────────
def rule_loop(cv: Canvas) -> tuple[float, str, str, str]:
    c = cv.c
    types = ("Time schedule", "Idle unload", "High load grow", "Low traffic shrink", "Make room by priority", "Auto heal")
    x = PAD
    cv.eyebrow("rule types", x, 64)
    x += cv.width("RULE TYPES", 10.5, "mono", 0.11) + 12
    size, gap = 12.5, 7.0
    while x + sum(cv.width(t, size) + 22 for t in types) + gap * (len(types) - 1) > W - PAD:
        size -= 0.25
    for t in types:
        x += cv.chip(t, x, 64, h=26, size=size) + gap
    top, h = 98, 150
    g = 26
    widths = [150, 164, 186]
    xs = [PAD]
    for w in widths:
        xs.append(xs[-1] + w + g)
    widths.append(W - PAD - xs[3])
    cy = top + 26
    cards = (("clock", "Tick", "Every 10 s, on the one jobs process that holds the rules lease"),
             ("list", "Each rule", "Every enabled rule in priority order, when its own interval is due"),
             ("gauge", "Read the inputs", "Last request, requests per minute, queue and KV cache, free VRAM, health"))
    for (icon, title, body), x, w in zip(cards, xs, widths):
        cv.box(x, top, w, h)
        head_text(cv, icon, title, x + 16, cy, 14)
        cv.para(body, x + 16, top + 44, w - 32)
    for i in range(3):
        cv.wire([(xs[i] + widths[i], cy), (xs[i + 1], cy)])
    # decide
    x, w = xs[3], widths[3]
    cv.slab(x, top, w, h)
    cv.text("Decide and record why", x + 18, cy, 14.5, "title", SLAB_TEXT)
    outs = (("no_match", None), ("cooldown", None), ("skipped", None), ("dry_run", None), ("fired", "accent"),
            ("error", "err"))
    ow = (w - 36 - 8) / 2
    for i, (s, kind) in enumerate(outs):
        ox = x + 18 + (i % 2) * (ow + 8)
        oy = top + 60 + (i // 2) * 32
        if kind == "accent":
            cv.add(f'<rect x="{ox:.2f}" y="{oy - 13:.2f}" width="{ow:.2f}" height="26" rx="8" fill="#D3263F"/>')
            cv.text(s, ox + 11, oy, 12, "mono", WHITE)
        else:
            cv.chip(s, ox, oy, h=26, size=12, face="mono", w=ow, on_slab=True,
                    color="#F7875A" if kind == "err" else None)
    fired_x = x + 18 + ow / 2
    fired_y = top + 60 + 2 * 32 - 13  # top of the "fired" chip
    # swap manager and history
    y2, h2 = top + h + 44, 100
    sx, sw = xs[3], widths[3]
    cv.box(sx, y2, sw, h2)
    head_text(cv, "swap", "Swap manager", sx + 16, y2 + 24, 14)
    cv.para("Runs each action as a safe swap: drain, unload, load, check health, route", sx + 16, y2 + 38, sw - 32)
    cv.wire([(fired_x, fired_y + 26), (fired_x, top + h - 1), (fired_x, y2)], c["accent"], 1.75)
    cv.text("fired", fired_x + 8, (top + h + y2) / 2, 10.5, "mono", c["accent_text"])
    hx, hw = xs[1], xs[3] - g - xs[1]
    cv.box(hx, y2, hw, h2)
    head_text(cv, "history", "History", hx + 16, y2 + 24, 14)
    cv.para("Every outcome with its inputs and the reason: rule runs, the audit log and alerts", hx + 16, y2 + 38, hw - 32)
    cv.wire([(sx, y2 + h2 / 2), (hx + hw, y2 + h2 / 2)])
    # back to the next tick
    lx = xs[0] + widths[0] / 2
    cv.wire([(hx, y2 + h2 / 2), (lx, y2 + h2 / 2), (lx, top + h)], c["wire"], 1.4, dash="5 4")
    cv.label_on_wire("next tick", (lx + hx) / 2 + 4, y2 + h2 / 2, 11.5, "mono")
    y = cv.para("A dry run evaluates and plans the same way and records what would happen, without sending a "
                "command. Cooldown is per rule and deployment, and at most two swaps run at once by default.",
                PAD, y2 + h2 + 18, W - 2 * PAD)
    desc = ("The rule evaluation loop for the six rule types (time schedule, idle unload, high load grow, low traffic "
            "shrink, make room by priority, auto heal). Every 10 seconds the jobs process that holds the rules lease "
            "ticks; each enabled rule is evaluated in priority order when its own interval is due; it reads its "
            "inputs (last request, requests per minute, queue and KV cache, free VRAM, health) and decides one "
            "outcome, recording why: no_match, cooldown, skipped, dry_run, fired or error. A fired rule hands its "
            "actions to the swap manager, which runs each as a safe swap. Every outcome goes to the history (rule "
            "runs, audit log, alerts), then the next tick starts. A dry run plans the same way without sending a "
            "command; cooldown is per rule and deployment, and at most two swaps run at once by default.")
    return y + PAD - 4, "Rule evaluation loop", "How rules are evaluated", desc


# ── 7. Support at a glance ──────────────────────────────────────────────────
V, P, Y, A, N, NA = "verified", "partial", "pinned", "add", "none", "na"
ACCELERATORS = ("NVIDIA", "AMD", "Apple\nSilicon", "Hygon", "MetaX", "Moore\nThreads", "T-Head", "CPU\nonly")
MONITORING = (V, Y, V, Y, Y, Y, Y, NA)
ENGINES = (  # docs/hardware/README.md, with the status from docs/TESTING.md
    ("llama.cpp", (V, Y, V, Y, A, A, A, Y)),
    ("vLLM", (V, A, N, A, A, A, N, N)),
    ("vLLM-Omni", (Y, N, N, N, N, N, N, N)),
    ("SGLang", (P, A, N, N, N, N, N, N)),
    ("SGLang Diffusion", (Y, A, A, N, N, N, N, N)),
    ("TensorRT-LLM", (P, N, N, N, N, N, N, N)),
    ("MLX", (N, N, V, N, N, N, N, N)),
    ("mlx-audio", (N, N, Y, N, N, N, N, N)),
    ("whisper.cpp", (A, A, Y, N, N, N, N, Y)),
    ("stable-diffusion.cpp", (A, Y, Y, N, N, N, N, Y)),
    ("Custom engine", (A, A, A, A, A, A, A, A)),
)
LEGEND = ((V, "Verified on real hardware"), (P, "Partial"), (Y, "Built in, awaiting reports"),
          (A, "Allowed: add your own build or image"), (N, "Not supported"))


def cell(cv: Canvas, kind: str, cx: float, cy: float) -> None:
    c = cv.c
    s = 22
    x, y = cx - s / 2, cy - s / 2
    if kind == V:
        cv.rect(x, y, s, s, 6, c["ok"])
        cv.icon("check", x + 3, y + 3, 16, WHITE, 2.4)
    elif kind == P:
        cv.rect(x, y, s, s, 6, c["warn"])
        ink = on(c["warn"])
        cv.add(f'<circle cx="{cx}" cy="{cy}" r="5.5" fill="none" stroke="{ink}" stroke-width="1.6"/>'
               f'<path d="M{cx} {cy - 5.5}a5.5 5.5 0 0 0 0 11Z" fill="{ink}"/>')
    elif kind == Y:
        cv.rect(x, y, s, s, 6, c["text2"], c["text2"], fo=0.12, so=0.55)
        cv.icon("check", x + 4, y + 4, 14, c["text2"], 2.2)
    elif kind == A:
        cv.rect(x, y, s, s, 6, "none", c["text2"], 1.25, dash="3 2.5", so=0.7)
        cv.add(f'<path d="M{cx - 4} {cy}h8M{cx} {cy - 4}v8" stroke="{c["text2"]}" stroke-width="1.5" stroke-linecap="round"/>')
    elif kind == N:
        cv.add(f'<circle cx="{cx}" cy="{cy}" r="2.2" fill="{c["text2"]}" fill-opacity=".45"/>')
    else:
        cv.add(f'<path d="M{cx - 5} {cy}h10" stroke="{c["text2"]}" stroke-opacity=".5" stroke-width="1.5"/>')


def support(cv: Canvas) -> tuple[float, str, str, str]:
    c = cv.c
    lx, lw = PAD, 176
    cx0 = lx + lw
    colw = (W - PAD - cx0) / len(ACCELERATORS)
    centers = [cx0 + colw * (i + 0.5) for i in range(len(ACCELERATORS))]
    head_y = 66
    for x, name in zip(centers, ACCELERATORS):
        lines = name.split("\n")
        for j, line in enumerate(lines):
            cv.text(line, x, head_y + (j - (len(lines) - 1) / 2) * 16 + 8, 12.5, "title", anchor="middle")
    rh = 34
    y = 104
    # monitoring row on its own band
    cv.rect(PAD, y, W - 2 * PAD, rh, 8, c["box"], c["box_line"])
    cv.text("Device monitoring", lx + 12, y + rh / 2, 13, "title")
    for x, k in zip(centers, MONITORING):
        cell(cv, k, x, y + rh / 2)
    y += rh + 10
    cv.eyebrow("engines", lx + 12, y + 8)
    y += 20
    for i, (name, row) in enumerate(ENGINES):
        if i % 2 == 0:
            cv.rect(PAD, y, W - 2 * PAD, rh, 8, c["box"], fo=0.7)
        cv.text(name, lx + 12, y + rh / 2, 13, "body")
        for x, k in zip(centers, row):
            cell(cv, k, x, y + rh / 2)
        y += rh
    # NVIDIA column highlight: what ran on a real NVIDIA GPU
    # legend
    y += 22
    x = PAD + 4
    for kind, label in LEGEND:
        w = 22 + 8 + cv.width(label, 11.5)
        if x + w > W - PAD:
            x = PAD + 4
            y += 30
        cell(cv, kind, x + 11, y)
        cv.text(label, x + 30, y, 11.5, "body", c["text2"])
        x += w + 16
    y = cv.para("Verified and partial marks are what ran on an Apple Silicon Mac and on an NVIDIA Hopper GPU; "
                "everything else is implemented and tested against recorded fixtures.", PAD, y + 20, W - 2 * PAD)
    rows = []
    for name, row in ENGINES:
        rows.append(f"{name}: " + ", ".join(f"{a.replace(chr(10), ' ')} {k}" for a, k in zip(ACCELERATORS, row)
                                           if k != N) + ".")
    desc = ("Which inference engine runs on which accelerator, and what has run on real hardware. Device monitoring: "
            "NVIDIA and Apple Silicon verified; AMD, Hygon, MetaX, Moore Threads and T-Head built in, awaiting "
            "reports. " + " ".join(rows) + " Marks: verified on real hardware, partial, built in (pinned build or "
            "image) awaiting reports, add your own build or image; combinations not listed are not supported.")
    return y + PAD - 4, "Support at a glance", "Accelerator and engine support at a glance", desc


# ── 8. Prepare a GPU machine ────────────────────────────────────────────────
def prepare(cv: Canvas) -> tuple[float, str, str, str]:
    c = cv.c
    top = 56
    n = 4
    g = 14
    cw = (W - 2 * PAD - (n - 1) * g) / n
    ch = 244
    cards = (
        ("gpu", "Driver", "Install the vendor driver and check that it lists every GPU.", ("nvidia-smi", "amd-smi")),
        ("box", "Container runtime", "Optional, for the vLLM, SGLang and TensorRT-LLM images: Docker or Podman "
         "plus the NVIDIA Container Toolkit.", ("--gpus all",)),
        ("network", "Network", "Outbound only: 8443 and 8444-8445 to the control plane, 443 to model hubs. Direct "
         "mode adds inbound 9443 from the API layer.", ()),
        ("plug", "Join", "Run the install command from Nodes, Add node. It pins the control plane's CA and starts "
         "the agent as a service.", ("install.sh",)),
    )
    for i, (icon, title, body, chips) in enumerate(cards):
        x = PAD + i * (cw + g)
        cv.box(x, top, cw, ch)
        cv.badge(str(i + 1), x + 26, top + 26, 11)
        cv.icon(icon, x + cw - 38, top + 14, 22, c["text2"])
        cv.text(title, x + 18, top + 62, 14.5, "title")
        cv.para(body, x + 18, top + 76, cw - 36)
        cx = x + 18
        for s in chips:
            cx += cv.chip(s, cx, top + ch - 28, h=26, size=11.5, face="mono") + 6
        if i < n - 1:
            cv.wire([(x + cw + 1, top + 26), (x + cw + g - 1, top + 26)], c["accent"], 1.5)
    # what the agent does afterwards
    y = top + ch + 16
    cv.slab(PAD, y, W - 2 * PAD, 96)
    cv.text("Then the agent does the rest", PAD + 18, y + 24, 14.5, "title", SLAB_TEXT)
    x = PAD + 18
    for s in ("Pinned engine builds", "Model cache", "Host firewall", "Certificates and rotation", "Metrics and logs"):
        x += cv.chip(s, x, y + 62, h=28, icon="check", on_slab=True) + 8
    y = cv.para("Only when you need them: MIG profiles before the machine joins, the MPS daemon for compute-share "
                "slices, Python 3.12 for SGLang Diffusion, the CUDA toolkit for native SGLang.", PAD, y + 96 + 16,
                W - 2 * PAD)
    desc = ("Preparing a GPU machine in four steps. 1, driver: install the vendor driver and check that it lists "
            "every GPU (nvidia-smi, amd-smi). 2, container runtime, optional and only for the vLLM, SGLang and "
            "TensorRT-LLM images: Docker or Podman plus the NVIDIA Container Toolkit. 3, network: outbound only, "
            "8443 and 8444-8445 to the control plane and 443 to model hubs; direct mode adds inbound 9443 from the "
            "API layer. 4, join: run the install command from Nodes, Add node; it pins the control plane's CA and "
            "starts the agent as a service. Then the agent does the rest: pinned engine builds, the model cache, "
            "the host firewall, certificates and their rotation, metrics and logs. Only when needed: MIG profiles "
            "before joining, the MPS daemon for compute-share slices, Python 3.12 for SGLang Diffusion, the CUDA "
            "toolkit for native SGLang.")
    return y + PAD - 4, "Prepare a GPU machine", "Preparing a GPU machine for GPU Deck", desc


# ── 9. Network latency ──────────────────────────────────────────────────────
def network_latency(cv: Canvas) -> tuple[float, str, str, str]:
    c = cv.c
    top = 56
    # The client side: one entry point that sends each client to the nearest API layer.
    gw_h = 52
    cv.box(PAD, top, W - 2 * PAD, gw_h)
    x = head_text(cv, "network", "AI gateway or geo-DNS", PAD + 16, top + gw_h / 2, 14)
    cv.text("sends each client to the API layer of its region", x + 14, top + gw_h / 2, 12.5, "body", c["text2"])
    # Two regions, each an API layer next to its GPU machines.
    gap = 28
    col = (W - 2 * PAD - gap) / 2
    ry, rh = top + gw_h + 34, 262
    slab_h = 64
    node_h = 96
    node_w = (col - 32 - 14) / 2
    mids = []
    for i, (name, label) in enumerate((("region-a", "most of the traffic"), ("region-b", "the rest"))):
        rx = PAD + i * (col + gap)
        cv.rect(rx, ry, col, rh, 14, "none", c["box_line"], 1.2, dash="5 5")
        cv.text(f"API_LAYER_REGION={name}", rx + 16, ry + 18, 10.5, "mono", c["accent_text"])
        cv.text(label, rx + col - 16, ry + 18, 11.5, "body", c["text2"], "end")
        sx, sy, sw = rx + 16, ry + 38, col - 32
        cv.slab(sx, sy, sw, slab_h)
        cv.text("API layer", sx + 18, sy + 22, 14.5, "title", SLAB_TEXT)
        cv.text("probes every node, routes to the nearest replica", sx + 18, sy + 44, 12, "body", SLAB_TEXT2)
        mids.append((sx, sy, sw))
        ny = sy + slab_h + 44
        for j in range(2):
            nx = sx + j * (node_w + 14)
            cv.box(nx, ny, node_w, node_h)
            head_text(cv, "gpu", "GPU machine", nx + 14, ny + 22, 13)
            cv.para("agent tunnel, engines on 127.0.0.1", nx + 14, ny + 34, node_w - 28, 11.5, 16)
            cv.gpu_cells(nx + 14, ny + node_h - 24, (0.85, 0.6, 0.35) if j == 0 else (0.5, 0.3, 0.15), 11, 4)
            tx = nx + node_w / 2
            cv.wire([(tx, sy + slab_h), (tx, ny)], c["accent"], 1.6)
        cv.label_on_wire("near: short round trip", sx + sw / 2, sy + slab_h + 22, 11.5)
        # gateway -> this API layer
        cv.wire([(sx + sw / 2, top + gw_h), (sx + sw / 2, sy)], c["accent"], 1.75)
    # Spill-over: region A's API layer may use region B's machines when its own are busy.
    (ax, ay, aw), (bx, by_, bw) = mids
    gx = PAD + col + gap / 2
    node_mid = by_ + slab_h + 44 + node_h / 2
    cv.wire([(ax + aw, ay + slab_h / 2), (gx, ay + slab_h / 2), (gx, node_mid), (bx, node_mid)],
            c["wire"], 1.4, dash="5 4")
    cv.wire([(gx, node_mid), (gx, ry + rh - 8)], c["wire"], 1.4, dash="5 4", head="none")
    cv.label_on_wire("far: only when the near replicas are busy", gx, ry + rh + 2, 11.5)
    # Server-Timing: where one request's time went.
    ty = ry + rh + 40
    cv.eyebrow("Server-Timing on every response", PAD, ty)
    parts = (("route", 0.06), ("queue", 0.07), ("connect", 0.09), ("net", 0.18), ("engine", 0.3),
             ("first token", 0.3))
    bx0, bw0, bh = PAD, W - 2 * PAD, 30
    y = ty + 16
    x = bx0
    for k, (name, share) in enumerate(parts):
        w = bw0 * share
        fo = 0.14 + 0.16 * k
        cv.rect(x + 1, y, w - 2, bh, 6, c["accent"], fo=fo)
        cv.text(name, x + w / 2, y + bh / 2, 11.5, "mono", c["text"] if fo < 0.5 else WHITE, "middle")
        x += w
    end = cv.para("Each API layer measures its own round trip to every node over the path it uses (tunnel or "
                  "direct); routing prefers the nearest healthy replica until it gets busy, and placement puts "
                  "replicas near the API layers that carry a deployment's traffic.", PAD, y + bh + 12,
                  W - 2 * PAD, 12.5)
    desc = ("Network latency. An AI gateway or geo-DNS sends each client to the API layer of its region. Two "
            "regions, region-a with most of the traffic and region-b with the rest, each have an API layer next "
            "to their GPU machines; the API layer probes every node and routes to the nearest replica, a short "
            "round trip. Only when the near replicas are busy does region-a's API layer use region-b's machines, "
            "a long round trip. Every response carries a Server-Timing header with route, queue, connect, net, "
            "engine and first-token time. Each API layer measures its own round trip to every node over the path "
            "it uses (tunnel or direct); routing prefers the nearest healthy replica until it gets busy, and "
            "placement puts replicas near the API layers that carry a deployment's traffic.")
    return end + PAD - 6, "Network latency", "Keep the API layer close to the GPUs", desc


DIAGRAMS = {
    "system-overview": system_overview,
    "node-join": node_join,
    "request-path": request_path,
    "safe-swap": safe_swap,
    "rule-loop": rule_loop,
    "support-matrix": support,
    "prepare-gpu-machine": prepare,
    "network-latency": network_latency,
}


def outputs(only: set[str] | None = None) -> dict[Path, str]:
    fonts = Fonts()
    files: dict[Path, str] = {}
    for name, draw in DIAGRAMS.items():
        if only and name not in only:
            continue
        for theme in ("light", "dark"):
            cv = Canvas(theme, fonts)
            height, eyebrow, title, desc = draw(cv)
            files[OUT / f"{name}-{theme}.svg"] = cv.document(height, eyebrow, title, desc)
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when a generated file is out of date")
    ap.add_argument("names", nargs="*", help="only these diagrams (default: all)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stale = []
    files = outputs(set(args.names) or None)
    for path, text in files.items():
        rel = path.relative_to(ROOT)
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(str(rel))
        else:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {rel} ({len(text.encode()) // 1024} KB)")
    if not args.names:
        known = set(files)
        for extra in sorted(OUT.glob("*.svg")):
            if extra not in known:
                if args.check:
                    stale.append(f"{extra.relative_to(ROOT)} (not generated)")
                else:
                    extra.unlink()
                    print(f"removed {extra.relative_to(ROOT)}")
    if stale:
        print("out of date (run python3 assets/brand/doc_art.py): " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
