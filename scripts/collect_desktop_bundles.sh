#!/usr/bin/env bash
# Copies the desktop installers (and their updater signatures) out of the Tauri bundle folder
# under release names without spaces: GPU-Deck_<version>_<arch>.<ext>.
#
# Usage: scripts/collect_desktop_bundles.sh BUNDLE_DIR OUT_DIR VERSION
#   BUNDLE_DIR  apps/desktop/src-tauri/target[/<triple>]/release/bundle
#
# macOS: dmg/*.dmg (scripts/make-dmg.sh) and macos/*.app.tar.gz[.sig] (updater archive)
# Windows: msi/*.msi[.sig], nsis/*-setup.exe[.sig]
# Linux: appimage/*.AppImage[.sig], deb/*.deb, rpm/*.rpm
# Works in Git Bash on the Windows runner.
set -euo pipefail

BUNDLE="${1:?usage: collect_desktop_bundles.sh BUNDLE_DIR OUT_DIR VERSION}"
OUT="${2:?usage: collect_desktop_bundles.sh BUNDLE_DIR OUT_DIR VERSION}"
VERSION="${3:?usage: collect_desktop_bundles.sh BUNDLE_DIR OUT_DIR VERSION}"
VERSION="${VERSION#v}"
mkdir -p "$OUT"
shopt -s nullglob

copied=0
put() {  # put SRC DEST_NAME
  cp "$1" "$OUT/$2"
  echo "  $2"
  copied=$((copied + 1))
}

echo "desktop bundles from $BUNDLE:"
for f in "$BUNDLE"/dmg/*.dmg "$BUNDLE"/msi/*.msi "$BUNDLE"/msi/*.msi.sig \
         "$BUNDLE"/nsis/*-setup.exe "$BUNDLE"/nsis/*-setup.exe.sig \
         "$BUNDLE"/appimage/*.AppImage "$BUNDLE"/appimage/*.AppImage.sig \
         "$BUNDLE"/deb/*.deb "$BUNDLE"/rpm/*.rpm; do
  name="$(basename "$f")"
  put "$f" "${name// /-}"
done

# The macOS updater archive is named after the app only ("GPU Deck.app.tar.gz"); add the
# version and architecture so releases never overwrite each other.
case "$BUNDLE" in
  *universal-apple-darwin*) arch=universal ;;
  *x86_64-apple-darwin*) arch=x64 ;;
  *) arch=aarch64 ;;
esac
for f in "$BUNDLE"/macos/*.app.tar.gz "$BUNDLE"/macos/*.app.tar.gz.sig; do
  name="$(basename "$f")"
  stem="${name%%.app.tar.gz*}"
  put "$f" "${stem// /-}_${VERSION}_${arch}.app.tar.gz${name##*.app.tar.gz}"
done

if [[ "$copied" -eq 0 ]]; then
  echo "error: no desktop bundles found in $BUNDLE" >&2
  exit 1
fi
