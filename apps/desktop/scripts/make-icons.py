"""Build the desktop icon sources from the brand assets in assets/brand (docs/BRAND.md).

Outputs:
  src-tauri/icons/app-icon.png          1024x1024 source for `tauri icon`, rendered from
                                        assets/brand/app-icon-1024.svg (git-ignored)
  src-tauri/icons/tray/tray-*.png       macOS menu-bar template images (black + alpha, 54x36 = 27x18 pt
                                        @2x): the card glyph, plus a status badge whose shape carries the
                                        state, because macOS tints template images and drops colour:
                                        idle (no badge), ok (solid dot), degraded (ring), down (dot with a
                                        bar cut out)
  src-tauri/icons/tray/tray-*-color.png Windows / Linux tray icons (64x64): the crimson app tile with a
                                        status dot in the status colours, set in a transparent gap so
                                        it reads on light and dark task bars
  public/gpudeck-mark.svg               the connect-screen mark: the crimson tile at 24-96 px
                                        (assets/brand/mark-small.svg)

Run: python3 scripts/make-icons.py  (needs Pillow and `rsvg-convert`; output is committed, so only on
brand changes). `pnpm --filter desktop icons` runs this and then `tauri icon`.
"""

import io
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent.parent
BRAND = HERE.parent.parent / "assets" / "brand"
SS = 8  # supersampling for the drawn badges

# Status colours from the web theme (success / warning / error, light steps): Windows / Linux only.
STATUS = {"ok": (52, 168, 115, 255), "degraded": (232, 184, 58, 255), "down": (245, 113, 58, 255)}


def render(svg: Path, width: int, height: int) -> Image.Image:
    out = subprocess.run(
        ["rsvg-convert", "-w", str(width), "-h", str(height), str(svg)],
        capture_output=True,
        check=True,
    )
    return Image.open(io.BytesIO(out.stdout)).convert("RGBA")


def _cut(img: Image.Image, box) -> None:
    """Make an ellipse fully transparent (the air around a badge)."""
    mask = Image.new("L", img.size, 255)
    ImageDraw.Draw(mask).ellipse(box, fill=0)
    alpha = Image.composite(img.getchannel("A"), Image.new("L", img.size, 0), mask)
    img.putalpha(alpha)


def template_icon(state: str | None) -> Image.Image:
    """54x36 template image: the glyph (viewBox 56x42 units) filling the height at the left, the
    badge at the bottom right, clear of the fans. Every state has the same size, so the menu-bar
    item never changes width."""
    w, h = 54 * SS, 36 * SS
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    glyph = render(BRAND / "glyph-tray.svg", 48 * SS, h)  # 56:42 == 48:36
    canvas.alpha_composite(glyph)
    if state:
        cx, cy, r_dot, r_air = 46.5 * SS, 30.5 * SS, 5.0 * SS, 7.5 * SS
        _cut(canvas, (cx - r_air, cy - r_air, cx + r_air, cy + r_air))
        draw = ImageDraw.Draw(canvas)
        black = (0, 0, 0, 255)
        draw.ellipse((cx - r_dot, cy - r_dot, cx + r_dot, cy + r_dot), fill=black)
        if state == "degraded":  # ring
            r_in = 2.5 * SS
            _cut(canvas, (cx - r_in, cy - r_in, cx + r_in, cy + r_in))
        elif state == "down":  # dot with a bar cut out: "stopped"
            bar = Image.new("L", canvas.size, 255)
            ImageDraw.Draw(bar).rectangle((cx - 3.0 * SS, cy - 0.95 * SS, cx + 3.0 * SS, cy + 0.95 * SS), fill=0)
            alpha = Image.composite(canvas.getchannel("A"), Image.new("L", canvas.size, 0), bar)
            canvas.putalpha(alpha)
    # Template images are black + alpha only.
    black = Image.new("RGBA", canvas.size, (0, 0, 0, 255))
    black.putalpha(canvas.getchannel("A"))
    return black.resize((54, 36), Image.LANCZOS)


def color_icon(state: str | None) -> Image.Image:
    """64x64 Windows / Linux tray icon: the app tile with a status dot."""
    size = 64 * SS
    canvas = render(BRAND / "mark-small.svg", size, size)
    if state:
        r_air, r_dot = 17 * SS, 12 * SS
        cx = cy = size - 15 * SS
        _cut(canvas, (cx - r_air, cy - r_air, cx + r_air, cy + r_air))
        ImageDraw.Draw(canvas).ellipse((cx - r_dot, cy - r_dot, cx + r_dot, cy + r_dot), fill=STATUS[state])
    return canvas.resize((64, 64), Image.LANCZOS)


def main() -> int:
    if not shutil.which("rsvg-convert"):
        print("rsvg-convert not found (brew install librsvg / apt install librsvg2-bin)", file=sys.stderr)
        return 1
    icons = HERE / "src-tauri" / "icons"
    tray = icons / "tray"
    tray.mkdir(parents=True, exist_ok=True)
    render(BRAND / "app-icon-1024.svg", 1024, 1024).save(icons / "app-icon.png", optimize=True)
    for name, state in {"idle": None, "ok": "ok", "degraded": "degraded", "down": "down"}.items():
        template_icon(state).save(tray / f"tray-{name}.png", optimize=True)
        color_icon(state).save(tray / f"tray-{name}-color.png", optimize=True)
    (HERE / "public").mkdir(exist_ok=True)
    shutil.copyfile(BRAND / "mark-small.svg", HERE / "public" / "gpudeck-mark.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
