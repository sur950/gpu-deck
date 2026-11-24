#!/usr/bin/env bash
# Copies the display SVGs of the product screenshots from docs/screenshots/ into
# website/assets/screenshots/. The site serves the SVGs directly (inline, gallery and lightbox):
# vector, sharp at any zoom, and about 40 KB per screen on the wire (GitHub Pages compresses
# SVG), which is lighter than the WebP renders they replaced. No raster is made or committed.
# It also copies assets/brand/social-preview.png to website/assets/brand/og-image.png (Open Graph
# does not accept SVG), so the card has one committed copy; that file is git-ignored too.
# (docs/screenshots/src/*.svg are the editable sources; see docs/DEVELOPMENT.md, "Screenshots".)
#
# website/assets/screenshots/ is generated and git-ignored: .github/workflows/pages.yml (and the
# `website` job of ci.yml) run this before scripts/build_docs.py. Run it locally after
# docs/screenshots/ is regenerated (node docs/screenshots/capture.mjs) to preview the site.
# index.html declares the sizes (1440 x 900, desktop-connect 1040 x 760), so the script warns
# when a screenshot changes size.
#
#   website/sync-screenshots.sh            # copy
#   website/sync-screenshots.sh --check    # exit 1 when website/ is behind docs/screenshots/
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/../docs/screenshots"
dst="$here/assets/screenshots"
og_src="$here/../assets/brand/social-preview.png"
og_dst="$here/assets/brand/og-image.png"
check=0
[[ "${1:-}" == "--check" ]] && check=1

[[ -d "$src" ]] || { echo "error: $src not found" >&2; exit 1; }
mkdir -p "$dst"

stale=0
count=0
shopt -s nullglob
for svg in "$src"/*.svg; do
  name="$(basename "$svg" .svg)"
  if [[ $check -eq 1 ]]; then
    cmp -s "$svg" "$dst/$name.svg" || { echo "stale: $name.svg"; stale=1; }
    continue
  fi
  expected="1440 900"
  [[ "$name" == desktop-connect* ]] && expected="1040 760"
  got="$(head -c 600 "$svg" | sed -n 's/.*<svg[^>]* width="\([0-9]*\)" height="\([0-9]*\)".*/\1 \2/p' | head -n 1)"
  if [[ "$got" != "$expected" ]]; then
    echo "warning: $name.svg is ${got/ /x}, index.html declares ${expected/ /x}; update its width/height" >&2
  fi
  cp "$svg" "$dst/$name.svg"
  count=$((count + 1))
done

if [[ $check -eq 1 ]]; then
  cmp -s "$og_src" "$og_dst" || { echo "stale: assets/brand/og-image.png"; stale=1; }
  [[ $stale -eq 0 ]] && echo "website screenshots are up to date"
  exit $stale
fi
mkdir -p "$(dirname "$og_dst")"
cp "$og_src" "$og_dst"
# Rasters from earlier builds (PNG / WebP) are no longer used.
rm -f "$dst"/*.png "$dst"/*.webp
echo "synced $count screenshots into website/assets/screenshots (SVG)"
