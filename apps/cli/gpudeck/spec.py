"""Where the command tree comes from: the server's OpenAPI document, cached, or the bundled copy.

Order (``GPUDECK_SPEC=auto``, the default):

1. a cached copy of the server's ``GET /api/v1/openapi.json`` younger than the TTL
   (``GPUDECK_SPEC_TTL`` seconds, default 24 h; ``--refresh-spec`` skips it),
2. a fresh fetch (stored per server and API version in the cache directory),
3. an older cached copy for that server,
4. the bundled contract: ``libs/contracts/openapi/admin.yaml`` when running from the repository,
   otherwise ``gpudeck/_spec/admin.yaml`` that the wheel build copies in (never committed).

``GPUDECK_SPEC=bundled`` never touches the network; ``GPUDECK_SPEC=/path/spec.(yaml|json)`` uses
that file. A failed fetch is remembered for five minutes so an unreachable server does not slow
every command. The cache directory is ``$GPUDECK_CACHE_DIR`` or the platform user cache
(``~/Library/Caches/gpudeck``, ``$XDG_CACHE_HOME/gpudeck`` or ``~/.cache/gpudeck``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Profile
from .openapi import Spec, route_key

logger = logging.getLogger(__name__)

DEFAULT_TTL = 24 * 3600
FAILURE_TTL = 300
FETCH_TIMEOUT = 5.0
BUNDLED_RESOURCE = "_spec/admin.yaml"
SPEC_ENV = "GPUDECK_SPEC"
TTL_ENV = "GPUDECK_SPEC_TTL"


@dataclass
class LoadedSpec:
    spec: Spec
    source: str  # live | cache | stale-cache | bundled | file
    location: str  # URL or file path
    server: str | None = None
    fetched_at: float | None = None

    @property
    def version(self) -> str:
        return self.spec.version


# ----- locations ------------------------------------------------------------------------------


def cache_dir() -> Path:
    override = os.environ.get("GPUDECK_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "gpudeck"
    if sys.platform == "win32":  # pragma: no cover
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "gpudeck" / "Cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg).expanduser() if xdg else Path.home() / ".cache") / "gpudeck"


def repo_spec_path() -> Path:
    """The contract in a source checkout (apps/cli/gpudeck -> repo root)."""
    return Path(__file__).resolve().parents[3] / "libs" / "contracts" / "openapi" / "admin.yaml"


def bundled_spec_path() -> Path | None:
    repo = repo_spec_path()
    if repo.is_file():
        return repo
    packaged = Path(__file__).resolve().parent / BUNDLED_RESOURCE
    return packaged if packaged.is_file() else None


def ttl_seconds() -> float:
    raw = os.environ.get(TTL_ENV)
    if not raw:
        return DEFAULT_TTL
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("ignoring %s=%r (not a number)", TTL_ENV, raw)
        return DEFAULT_TTL


def _server_id(base_url: str) -> str:
    return hashlib.sha256(base_url.rstrip("/").encode()).hexdigest()[:16]


def _safe_version(version: str) -> str:
    return "".join(c if c.isalnum() or c in ".-+" else "_" for c in version) or "unknown"


# ----- documents ------------------------------------------------------------------------------


def read_document(path: Path) -> dict[str, Any]:
    """Parse a JSON or YAML OpenAPI file; YAML parses are cached as JSON next to the live specs."""
    text_start = path.read_bytes()[:1]
    if path.suffix == ".json" or text_start in (b"{", b"["):
        doc = json.loads(path.read_text())
        if not isinstance(doc, dict):
            raise ValueError(f"{path} is not an OpenAPI document")
        return doc
    st = path.stat()
    path_id = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    stamp = hashlib.sha256(f"{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:12]
    cached = cache_dir() / f"parsed-{path_id}-{stamp}.json"
    if cached.is_file():
        try:
            doc = json.loads(cached.read_text())
            if isinstance(doc, dict):
                return doc
        except ValueError:
            pass
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    try:
        doc = yaml.load(path.read_text(), Loader=loader)  # noqa: S506 - safe loader
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not an OpenAPI document")
    for old in cached.parent.glob(f"parsed-{path_id}-*.json") if cached.parent.is_dir() else []:
        old.unlink(missing_ok=True)
    _write_json(cached, doc)
    return doc


def _write_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(path)
    except OSError as exc:
        logger.debug("cannot write cache %s: %s", path, exc)


def load_bundled() -> LoadedSpec:
    path = bundled_spec_path()
    if path is None:
        raise FileNotFoundError(
            "no bundled OpenAPI spec (gpudeck/_spec/admin.yaml missing and not in a checkout)"
        )
    return LoadedSpec(Spec(read_document(path)), "bundled", str(path))


# ----- live -----------------------------------------------------------------------------------


def _cache_files(server: str) -> list[Path]:
    d = cache_dir()
    if not d.is_dir():
        return []
    files = list(d.glob(f"spec-{_server_id(server)}-*.json"))
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _read_cached(server: str) -> dict[str, Any] | None:
    for path in _cache_files(server):
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and isinstance(entry.get("spec"), dict):
            entry["_path"] = str(path)
            return entry
    return None


def _failure_marker(server: str) -> Path:
    return cache_dir() / f"spec-{_server_id(server)}.failed"


def _recent_failure(server: str) -> bool:
    marker = _failure_marker(server)
    try:
        return time.time() - marker.stat().st_mtime < FAILURE_TTL
    except OSError:
        return False


def op_keys_from_contract(live: Spec) -> dict[str, str]:
    """Map the live document's routes to contract operation keys (method + path match)."""
    try:
        contract = load_bundled().spec
    except (OSError, ValueError) as exc:
        logger.debug("no bundled spec to map operation keys: %s", exc)
        return {}
    index = {route_key(op.method, op.path): op.key for op in contract.operations()}
    keys: dict[str, str] = {}
    for op in live.operations():
        rk = route_key(op.method, op.path)
        if rk in index:
            keys[rk] = index[rk]
    return keys


def _minor(version: str) -> tuple[str, ...]:
    return tuple(version.split(".")[:2])


def fetch_live(profile: Profile) -> dict[str, Any]:
    """GET <endpoint>/api/v1/openapi.json, store it in the cache, return the cache entry."""
    import httpx

    from .http import USER_AGENT, verify_value

    url = profile.base_url + "/openapi.json"
    resp = httpx.get(
        url,
        verify=verify_value(profile),
        timeout=min(FETCH_TIMEOUT, profile.timeout_seconds),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )
    resp.raise_for_status()
    doc = resp.json()
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        raise ValueError(f"{url} did not return an OpenAPI document")
    spec = Spec(doc)
    entry = {
        "server": profile.base_url,
        "version": spec.version,
        "fetched_at": time.time(),
        "op_keys": op_keys_from_contract(spec),
        "spec": doc,
    }
    target = cache_dir() / f"spec-{_server_id(profile.base_url)}-{_safe_version(spec.version)}.json"
    for old in _cache_files(profile.base_url):
        if old != target:
            old.unlink(missing_ok=True)
    _write_json(target, entry)
    _failure_marker(profile.base_url).unlink(missing_ok=True)
    entry["_path"] = str(target)
    _warn_on_version_mismatch(spec.version, profile.base_url)
    return entry


def _warn_on_version_mismatch(server_version: str, server: str) -> None:
    try:
        bundled = load_bundled().version
    except (OSError, ValueError):
        return
    if server_version and bundled and _minor(server_version) != _minor(bundled):
        from . import output

        output.print_warning(
            f"server API {server_version} ({server}) differs from this CLI's bundled API "
            f"{bundled}; commands follow the server's spec. Upgrade with "
            "`uv tool upgrade gpudeck-cli` if something is missing."
        )


def _from_entry(entry: dict[str, Any], source: str) -> LoadedSpec:
    return LoadedSpec(
        Spec(entry["spec"], op_keys=entry.get("op_keys") or {}),
        source,
        str(entry.get("_path") or ""),
        server=str(entry.get("server") or ""),
        fetched_at=entry.get("fetched_at"),
    )


def load(profile: Profile, *, refresh: bool = False, allow_fetch: bool = True) -> LoadedSpec:
    """Return the spec to build commands from (see the module docstring for the order)."""
    mode = os.environ.get(SPEC_ENV, "auto").strip() or "auto"
    if mode == "bundled":
        return load_bundled()
    if mode != "auto":
        path = Path(mode).expanduser()
        return LoadedSpec(Spec(read_document(path)), "file", str(path))

    server = profile.base_url
    cached = None if refresh else _read_cached(server)
    if cached and time.time() - float(cached.get("fetched_at") or 0) < ttl_seconds():
        return _from_entry(cached, "cache")
    if allow_fetch and (refresh or not _recent_failure(server)):
        try:
            return _from_entry(fetch_live(profile), "live")
        except Exception as exc:  # noqa: BLE001 - any failure falls back to cache/bundled
            logger.debug("spec fetch from %s failed: %s", server, exc)
            if refresh:
                from . import output

                output.print_warning(f"could not fetch the API spec from {server}: {exc}")
            try:
                marker = _failure_marker(server)
                marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                marker.touch()
            except OSError:
                pass
    cached = cached or _read_cached(server)
    if cached:
        return _from_entry(cached, "stale-cache")
    return load_bundled()


__all__ = [
    "LoadedSpec",
    "bundled_spec_path",
    "cache_dir",
    "fetch_live",
    "load",
    "load_bundled",
    "read_document",
]
