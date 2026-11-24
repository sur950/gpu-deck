#!/usr/bin/env bash
# Build the macOS .dmg from an already bundled "GPU Deck.app".
#
# Tauri's bundle_dmg.sh calls `hdiutil create -srcfolder`, which recent macOS releases deprecate
# and (on macOS 27) fail with "no mountable file systems" / "Device not configured". This script
# stages the app next to an /Applications symlink and uses `diskutil image create from` (the
# documented replacement), falling back to hdiutil on older systems.
#
#   scripts/make-dmg.sh [target-triple]      # default: host (target/release)
set -euo pipefail

cd "$(dirname "$0")/.."
TARGET_DIR="src-tauri/target/${1:+$1/}release/bundle"
APP="$TARGET_DIR/macos/GPU Deck.app"
[ -d "$APP" ] || { echo "missing $APP (run: pnpm tauri build --bundles app)" >&2; exit 1; }

VERSION=$(node -p "require('./src-tauri/tauri.conf.json').version")
case "${1:-$(uname -m)}" in
  *universal*) ARCH=universal ;;
  *x86_64*) ARCH=x64 ;;
  *) ARCH=aarch64 ;;
esac
OUT_DIR="$TARGET_DIR/dmg"
OUT="$OUT_DIR/GPU Deck_${VERSION}_${ARCH}.dmg"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$OUT_DIR"
rm -f "$OUT"
ditto "$APP" "$STAGE/GPU Deck.app"
ln -s /Applications "$STAGE/Applications"

if diskutil image create from "$STAGE" --volumeName "GPU Deck" --format UDZO "$OUT" >/dev/null 2>&1; then
  :
else
  hdiutil create -srcfolder "$STAGE" -volname "GPU Deck" -fs HFS+ -format UDZO -ov "$OUT" >/dev/null
fi
echo "$OUT"
