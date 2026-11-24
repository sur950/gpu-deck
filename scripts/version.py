#!/usr/bin/env python3
"""
One version for the whole repository.

``VERSION`` (repository root) is the source of truth. Every package manifest that carries a
version must match it; CI runs ``--check`` and fails on drift.

    python3 scripts/version.py              # print the version and every file that carries it
    python3 scripts/version.py --check      # exit 1 when any file differs from VERSION
    python3 scripts/version.py --set 1.2.3  # write VERSION and every file below

Only the version keys are rewritten (line-level regex edits), so other keys and the formatting
of each file stay as they are. Cargo.lock entries of the workspace's own crates are updated
in place (path packages have no checksum, so this is exactly what ``cargo update -w`` writes).

Files: VERSION, package.json (root, apps/web, apps/desktop), apps/desktop/src-tauri
(tauri.conf.json, Cargo.toml, Cargo.lock), apps/node-agent (workspace Cargo.toml, crate
Cargo.toml files with a literal version, Cargo.lock), apps/cli (pyproject.toml,
gpudeck/__init__.py), apps/terraform-provider/main.go (default of ``var version``; release
builds also set it with -ldflags), infra/helm/gpu-deck/Chart.yaml (version, appVersion),
libs/contracts/openapi/admin.yaml (info.version), apps/fastapi APP_VERSION (settings default,
libs/contracts/env.md row, both .env.example files).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
V = r"[0-9A-Za-z.+-]+"  # a version value inside a manifest


@dataclass
class Spot:
    """One version value in one file: ``pattern`` has groups (prefix)(value)(suffix)."""

    path: str
    pattern: str
    label: str
    flags: int = re.MULTILINE

    def _regex(self) -> "re.Pattern[str]":
        return re.compile(self.pattern, self.flags)

    def read(self) -> Optional[str]:
        file = ROOT / self.path
        if not file.is_file():
            return None
        match = self._regex().search(file.read_text(encoding="utf-8"))
        return match.group(2) if match else None

    def write(self, version: str) -> bool:
        file = ROOT / self.path
        if not file.is_file():
            return False
        text = file.read_text(encoding="utf-8")
        new, count = self._regex().subn(lambda m: m.group(1) + version + m.group(3), text, count=1)
        if count == 0:
            raise SystemExit(f"error: {self.path}: {self.label} not found")
        if new != text:
            file.write_text(new, encoding="utf-8")
        return True


def _json_version(path: str) -> Spot:
    # The first "version" key at two-space indent (top level of the object).
    return Spot(path, rf'^(  "version":\s*")({V})(")', "top-level \"version\"")


def _cargo_package(path: str, table: str = "package") -> Spot:
    # `version = "x"` inside [package] / [workspace.package] (stops at the next table header).
    return Spot(
        path,
        rf'(^\[{re.escape(table)}\]\n(?:(?!\[)[^\n]*\n)*?version\s*=\s*")({V})(")',
        f"[{table}] version",
    )


def _cargo_lock(path: str, crate: str) -> Spot:
    return Spot(path, rf'(^name = "{re.escape(crate)}"\nversion = ")({V})(")', f"lock entry {crate}")


def spots() -> List[Spot]:
    out: List[Spot] = [
        Spot("VERSION", rf"(\A)({V})(\s*\Z)", "VERSION"),
        _json_version("package.json"),
        _json_version("apps/web/package.json"),
        _json_version("apps/desktop/package.json"),
        _json_version("apps/desktop/src-tauri/tauri.conf.json"),
        _cargo_package("apps/desktop/src-tauri/Cargo.toml"),
        _cargo_lock("apps/desktop/src-tauri/Cargo.lock", "gpudeck-desktop"),
        _cargo_package("apps/node-agent/Cargo.toml", "workspace.package"),
    ]
    crates = sorted((ROOT / "apps/node-agent/crates").glob("*/Cargo.toml"))
    for manifest in crates:
        rel = manifest.relative_to(ROOT).as_posix()
        text = manifest.read_text(encoding="utf-8")
        name = re.search(r'^name\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if re.search(r'^version\s*=\s*"', text, re.MULTILINE):  # literal (not .workspace)
            out.append(_cargo_package(rel))
        if name:
            out.append(_cargo_lock("apps/node-agent/Cargo.lock", name.group(1)))
    out += [
        Spot("apps/cli/pyproject.toml", rf'(^\[project\]\n(?:(?!\[)[^\n]*\n)*?version\s*=\s*")({V})(")',
             "[project] version"),
        Spot("apps/cli/gpudeck/__init__.py", rf'(^__version__\s*=\s*")({V})(")', "__version__"),
        Spot("apps/terraform-provider/main.go", rf'(^var version = ")({V})(")', "var version"),
        Spot("infra/helm/gpu-deck/Chart.yaml", rf"(^version:\s*)({V})(\s*$)", "version"),
        Spot("infra/helm/gpu-deck/Chart.yaml", rf'(^appVersion:\s*")({V})(")', "appVersion"),
        Spot("libs/contracts/openapi/admin.yaml", rf"(^info:\n(?:  [^\n]*\n)*?  version:\s*)({V})(\s*$)",
             "info.version"),
        Spot("apps/fastapi/app/core/settings/app.py",
             rf'(APP_VERSION:\s*str\s*=\s*Field\(\s*default=")({V})(")', "APP_VERSION default"),
        Spot("libs/contracts/env.md", rf"(^\| `APP_VERSION` \| `)({V})(`)", "APP_VERSION row"),
        Spot("apps/fastapi/.env.example", rf"(^APP_VERSION=)({V})(\s*$)", "APP_VERSION"),
        Spot(".env.example", rf"(^APP_VERSION=)({V})(\s*$)", "APP_VERSION"),
    ]
    return out


def read_version_file() -> str:
    file = ROOT / "VERSION"
    if not file.is_file():
        raise SystemExit("error: VERSION file is missing")
    value = file.read_text(encoding="utf-8").strip()
    if not SEMVER.match(value):
        raise SystemExit(f"error: VERSION is not a semantic version: {value!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="fail when any file differs from VERSION")
    group.add_argument("--set", metavar="X.Y.Z", help="write this version everywhere")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    if args.set:
        version = args.set.removeprefix("v")
        if not SEMVER.match(version):
            raise SystemExit(f"error: not a semantic version: {args.set!r}")
        (ROOT / "VERSION").write_text(version + "\n", encoding="utf-8")
        for spot in spots():
            if not spot.write(version):
                print(f"skip  {spot.path} (missing)")
        print(f"set {version} in every manifest; run `python3 scripts/version.py --check`.")
        print("Rust lockfiles were edited in place; `cargo metadata --locked` verifies them.")
        return 0

    version = read_version_file()
    drift = 0
    for spot in spots():
        found = spot.read()
        if found is None:
            if (ROOT / spot.path).is_file():
                print(f"MISSING {spot.path}: {spot.label} not found")
                drift += 1
            continue
        ok = found == version
        drift += 0 if ok else 1
        if not args.quiet or not ok:
            print(f"{'ok     ' if ok else 'DRIFT  '} {spot.path} [{spot.label}] = {found}")
    if drift:
        sys.stdout.flush()
        print(f"\nerror: {drift} value(s) differ from VERSION ({version}). "
              f"Fix with: python3 scripts/version.py --set {version}", file=sys.stderr)
        return 1
    if args.check:
        print(f"all versions match {version}")
    else:
        print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
