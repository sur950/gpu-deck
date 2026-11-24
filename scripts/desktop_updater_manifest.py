#!/usr/bin/env python3
"""
Writes the Tauri updater manifest (latest.json) for the desktop app from the signed release
files that scripts/collect_desktop_bundles.sh copied into one folder.

    python3 scripts/desktop_updater_manifest.py DIR VERSION NOTES_FILE BASE_URL > latest.json

BASE_URL is where the files are downloaded from, e.g.
https://github.com/sur950/gpu-deck/releases/download/v1.0.0 . Installed apps read the manifest
from https://github.com/sur950/gpu-deck/releases/latest/download/latest.json (the
GPUDECK_UPDATER_ENDPOINTS default in the release workflow).

Platform keys follow the Tauri v2 updater: ``<os>-<arch>`` plus ``<os>-<arch>-<installer>``.
Only files with a ``.sig`` next to them are listed; exits 1 when there are none.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

# (file suffix, platform keys) — the first match per key wins.
RULES: List[Tuple[str, List[str]]] = [
    ("_universal.app.tar.gz", ["darwin-aarch64", "darwin-x86_64", "darwin-aarch64-app", "darwin-x86_64-app"]),
    ("_aarch64.app.tar.gz", ["darwin-aarch64", "darwin-aarch64-app"]),
    ("_x64.app.tar.gz", ["darwin-x86_64", "darwin-x86_64-app"]),
    ("_x64-setup.exe", ["windows-x86_64", "windows-x86_64-nsis"]),
    ("_x64_en-US.msi", ["windows-x86_64-msi"]),
    ("_amd64.AppImage", ["linux-x86_64", "linux-x86_64-appimage"]),
    ("_aarch64.AppImage", ["linux-aarch64", "linux-aarch64-appimage"]),
]


def main() -> int:
    if len(sys.argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    folder, version, notes_file, base_url = Path(sys.argv[1]), sys.argv[2].lstrip("v"), sys.argv[3], sys.argv[4]
    platforms: Dict[str, Dict[str, str]] = {}
    for suffix, keys in RULES:
        for artifact in sorted(folder.glob(f"*{suffix}")):
            sig = artifact.with_name(artifact.name + ".sig")
            if not sig.is_file():
                continue
            entry = {
                "signature": sig.read_text(encoding="utf-8").strip(),
                "url": f"{base_url.rstrip('/')}/{artifact.name}",
            }
            for key in keys:
                platforms.setdefault(key, entry)
    if not platforms:
        print("error: no signed desktop update files (*.sig) found", file=sys.stderr)
        return 1
    notes = Path(notes_file).read_text(encoding="utf-8").strip() if notes_file != "-" else ""
    manifest = {
        "version": version,
        "notes": notes,
        "pub_date": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platforms": platforms,
    }
    json.dump(manifest, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
