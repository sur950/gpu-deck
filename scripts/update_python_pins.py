#!/usr/bin/env python3
"""
Rewrite exact Python pins (``name==version``) to the newest release on PyPI.

Used by ``scripts/update-deps.sh``; standard library only. Reads requirements files and
``pyproject.toml`` files (any ``"name==version"`` string, including ``[build-system]``) and
replaces each pin in place, keeping comments and layout.

  - Final releases only, unless the current pin is itself a pre-release (the OpenTelemetry
    instrumentation packages only publish ``0.NNbN`` versions).
  - A release must be at least ``--min-age-days`` old (default 1, as pnpm's minimumReleaseAge),
    so a version published minutes ago is not picked up.
  - ``HOLDS`` keeps a package below a version, with the reason printed in the summary.

    python3 scripts/update_python_pins.py apps/fastapi/requirements.txt apps/cli/pyproject.toml
    python3 scripts/update_python_pins.py --check FILE...   # report only, exit 1 if outdated
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

# name (normalised) -> (exclusive upper bound, reason)
HOLDS: dict[str, tuple[str, str]] = {
    "python-dateutil": (
        "3",
        "celery pins python-dateutil==2.9.0.post0 and botocore needs <3.0.0",
    ),
    "typer": (
        "0.26",
        "typer 0.26+ vendors Click and drops the Click-level extension the CLI's spec-driven "
        "command tree (apps/cli/gpudeck/registry.py) is built on",
    ),
}

PIN = re.compile(r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[^\]]*\])?==(?P<ver>[^\s\"',;#]+)")
VERSION = re.compile(r"^(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?(?:\.dev(\d+))?$")


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(version: str) -> tuple | None:
    m = VERSION.match(version)
    if not m:
        return None
    release = tuple(int(x) for x in m.group(1).split("."))
    release += (0,) * (6 - len(release))
    pre = {"a": 0, "b": 1, "rc": 2}.get(m.group(2) or "", 3)
    return (
        release,
        pre,
        int(m.group(3) or 0),
        int(m.group(4) or -1),
        -1 if m.group(5) is None else -int(m.group(5)) - 1,
    )


def is_pre(version: str) -> bool:
    m = VERSION.match(version)
    return bool(m and (m.group(2) or m.group(5)))


def latest(name: str, current: str, min_age: dt.timedelta) -> str | None:
    url = f"https://pypi.org/pypi/{name}/json"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https host)
        releases = json.load(resp)["releases"]
    cutoff = dt.datetime.now(dt.timezone.utc) - min_age
    allow_pre = is_pre(current)
    hold = HOLDS.get(normalise(name))
    bound = parse(hold[0]) if hold else None
    best: tuple | None = None
    best_version = None
    for version, files in releases.items():
        key = parse(version)
        if key is None or not files or all(f.get("yanked") for f in files):
            continue
        if is_pre(version) and not allow_pre:
            continue
        if bound is not None and key >= bound:
            continue
        uploaded = min(
            dt.datetime.fromisoformat(f["upload_time_iso_8601"].replace("Z", "+00:00"))
            for f in files
        )
        if uploaded > cutoff:
            continue
        if best is None or key > best:
            best, best_version = key, version
    return best_version


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--check", action="store_true", help="report only; exit 1 when outdated")
    ap.add_argument("--min-age-days", type=float, default=1.0)
    args = ap.parse_args()
    min_age = dt.timedelta(days=args.min_age_days)

    pins: dict[str, str] = {}
    texts = {path: path.read_text() for path in args.files}
    for text in texts.values():
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            for m in PIN.finditer(code):
                pins.setdefault(m.group("name"), m.group("ver"))

    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        found = dict(zip(pins, pool.map(lambda n: latest(n, pins[n], min_age), pins)))

    def newer(new: str | None, old: str) -> bool:
        a, b = (parse(new) if new else None), parse(old)
        return a is not None and (b is None or a > b)

    # Never downgrade: a pin newer than every release old enough stays.
    changes = {n: (pins[n], v) for n, v in found.items() if v and newer(v, pins[n])}
    for name, (old, new) in sorted(changes.items(), key=lambda kv: kv[0].lower()):
        print(f"  {name:40} {old} -> {new}")
    for name in sorted(pins, key=str.lower):
        hold = HOLDS.get(normalise(name))
        if hold:
            print(f"  {name:40} held below {hold[0]}: {hold[1]}")
    if args.check:
        return 1 if changes else 0

    def bump(m: re.Match) -> str:
        new = changes.get(m.group("name"))
        if not new or m.group("ver") != new[0]:
            return m.group(0)
        return f"{m.group('name')}{m.group('extras') or ''}=={new[1]}"

    for path, text in texts.items():
        out = []
        for line in text.splitlines(keepends=True):
            code, sep, comment = line.partition("#")
            out.append(PIN.sub(bump, code) + sep + comment)
        new_text = "".join(out)
        if new_text != text:
            path.write_text(new_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
