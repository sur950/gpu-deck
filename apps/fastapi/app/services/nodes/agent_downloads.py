"""
Node-agent builds, the install script and the one-line install command.

Where a build comes from, first match wins:

1. ``AGENT_DIST_DIR`` (offline / air-gapped override, or a repo build)::

       <dir>/<version>/<target>/gpudeck-agent[.tar.gz]   (+ .sha256 sidecar or SHA256SUMS)
       <dir>/gpudeck-agent-<version>-<target>.tar.gz     (flat release layout + SHA256SUMS[.txt])

2. ``AGENT_CACHE_DIR``: builds fetched earlier from the release.
3. The GitHub release of the requested version (``AGENT_RELEASE_URL``,
   ``<url>/v<version>/gpudeck-agent-<version>-<target>.tar.gz``), verified
   against the release's ``SHA256SUMS.txt`` and cached in ``AGENT_CACHE_DIR``.
   ``latest`` means the control plane's own ``APP_VERSION`` when nothing local
   matches.

``target`` is a Rust target triple (``aarch64-apple-darwin``,
``x86_64-unknown-linux-gnu``, ``aarch64-unknown-linux-musl`` ...).

Access: an admin session, or a join token that is still usable (unrevoked,
unexpired, uses left) — the install script downloads with the same token it
then joins with. Served by the agent hub under ``/agent/v1/...`` (what GPU
machines use: they only need outbound 8443) and by the admin API under
``/api/v1/agent/...`` (same handlers, for the web UI and the CLI).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import (
    AuthenticationError,
    ExternalServiceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logger import get_logger
from app.core.settings import settings
from starlette.responses import FileResponse

logger = get_logger(__name__)

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
BINARY_NAMES = ("gpudeck-agent", "gpudeck-agent.exe")
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".zip")
# Targets the release workflow publishes (.github/workflows/release.yml).
RELEASE_TARGETS = (
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "x86_64-unknown-linux-musl",
    "aarch64-unknown-linux-musl",
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
)
_FLAT = re.compile(
    r"^gpudeck-agent-(?P<version>.+)-(?P<target>(?:x86_64|aarch64)-"
    r"(?:unknown-linux-(?:gnu|musl)|apple-darwin))\.tar\.gz$"
)
_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
_SUMS_TTL_SECONDS = 600.0
_SUMS_FAIL_TTL_SECONDS = 60.0

download_settings = settings  # AGENT_* download settings (core settings)
_sha_cache: Dict[Tuple[str, int, float], str] = {}
_sums_cache: Dict[str, Tuple[float, Optional[Dict[str, str]]]] = {}
_fetch_locks: Dict[str, asyncio.Lock] = {}
_transport: Any = None  # tests: an httpx transport for release fetches


@dataclass
class Artifact:
    version: str
    target: str
    path: Optional[Path]
    filename: str
    size_bytes: int
    sha256: str
    os: Optional[str]
    arch: Optional[str]
    built_at: Optional[datetime]
    source: str = "local"  # local | cache | release (not downloaded yet)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "target": self.target,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "os": self.os,
            "arch": self.arch,
            "built_at": self.built_at,
            "source": self.source,
            "download_path": f"/agent/downloads/{self.version}/{self.target}",
        }


def dist_dir() -> Path:
    if download_settings.AGENT_DIST_DIR:
        return Path(os.path.expanduser(download_settings.AGENT_DIST_DIR))
    return settings.agent_dist_dir()


def cache_dir() -> Path:
    raw = (download_settings.AGENT_CACHE_DIR or "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return Path(tempfile.gettempdir()) / "gpu-deck-agent-cache"


def release_enabled() -> bool:
    return bool((download_settings.AGENT_RELEASE_URL or "").strip())


def release_asset_name(version: str, target: str) -> str:
    return f"gpudeck-agent-{version}-{target}.tar.gz"


def target_platform(target: str) -> Tuple[Optional[str], Optional[str]]:
    """Rust triple -> (os, arch) in the node vocabulary (linux|macos, x86_64|aarch64)."""
    arch = (
        "aarch64"
        if target.startswith(("aarch64", "arm64"))
        else ("x86_64" if target.startswith(("x86_64", "amd64")) else None)
    )
    if "apple-darwin" in target:
        return "macos", arch
    if "linux" in target:
        return "linux", arch
    return None, arch


def _version_key(version: str) -> Tuple:
    parts = re.split(r"[.+-]", version.lstrip("v"))
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


def _pick_file(folder: Path) -> Optional[Path]:
    for name in BINARY_NAMES:
        if (folder / name).is_file():
            return folder / name
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.name.startswith("gpudeck-agent") and f.name.endswith(ARCHIVE_SUFFIXES):
            return f
    return None


def _sums_lookup(sums: Path, name: str) -> Optional[str]:
    if not sums.is_file():
        return None
    for line in sums.read_text().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name:
            value = parts[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", value):
                return value
    return None


def _declared_sha(folder: Path, file: Path) -> Optional[str]:
    for sidecar in (folder / f"{file.name}.sha256", folder / "sha256.txt"):
        if sidecar.is_file():
            value = sidecar.read_text().strip().split()[0:1]
            if value and re.fullmatch(r"[0-9a-f]{64}", value[0].lower()):
                return value[0].lower()
    # SHA256SUMS next to the file ("<sha>  <file>") or one level up ("<sha>  <target>/<file>").
    for sums, name in (
        (folder / "SHA256SUMS", file.name),
        (folder / "SHA256SUMS.txt", file.name),
        (folder.parent / "SHA256SUMS", f"{folder.name}/{file.name}"),
    ):
        found = _sums_lookup(sums, name)
        if found:
            return found
    return None


def file_sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    if key in _sha_cache:
        return _sha_cache[key]
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    _sha_cache[key] = digest.hexdigest()
    return _sha_cache[key]


def _make(version: str, target: str, file: Path, source: str) -> Artifact:
    st = file.stat()
    os_name, arch = target_platform(target)
    return Artifact(
        version=version,
        target=target,
        path=file,
        filename=file.name,
        size_bytes=st.st_size,
        sha256=_declared_sha(file.parent, file) or file_sha256(file),
        os=os_name,
        arch=arch,
        built_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
        source=source,
    )


def _nested(version: str, target: str) -> Optional[Artifact]:
    folder = dist_dir() / version / target
    if not folder.is_dir():
        return None
    file = _pick_file(folder)
    return _make(version, target, file, "local") if file is not None else None


def _flat(root: Path, version: str, target: str, source: str) -> Optional[Artifact]:
    file = root / release_asset_name(version, target)
    return _make(version, target, file, source) if file.is_file() else None


def _artifact(version: str, target: str) -> Optional[Artifact]:
    return (
        _nested(version, target)
        or _flat(dist_dir(), version, target, "local")
        or _flat(cache_dir(), version, target, "cache")
    )


def _flat_entries(root: Path) -> List[Tuple[str, str]]:
    if not root.is_dir():
        return []
    out = []
    for f in root.iterdir():
        m = _FLAT.match(f.name)
        if f.is_file() and m and _SAFE.match(m["version"]):
            out.append((m["version"], m["target"]))
    return out


def list_artifacts() -> List[Artifact]:
    """Builds available without a network fetch (local dist dir and the release cache)."""
    keys: List[Tuple[str, str]] = []
    root = dist_dir()
    if root.is_dir():
        for vdir in (d for d in root.iterdir() if d.is_dir()):
            if not _SAFE.match(vdir.name) or vdir.name == "latest":
                continue
            for tdir in (d for d in vdir.iterdir() if d.is_dir()):
                if _SAFE.match(tdir.name):
                    keys.append((vdir.name, tdir.name))
    keys += _flat_entries(root) + _flat_entries(cache_dir())
    out: List[Artifact] = []
    seen = set()
    for version, target in sorted(
        set(keys),
        key=lambda k: (_version_key(k[0]), [-ord(c) for c in k[1]]),
        reverse=True,
    ):
        art = _artifact(version, target)
        if art is not None and (version, target) not in seen:
            seen.add((version, target))
            out.append(art)
    return out


def latest_version() -> Optional[str]:
    versions = sorted({a.version for a in list_artifacts()}, key=_version_key, reverse=True)
    return versions[0] if versions else None


def _check_names(version: str, target: str) -> None:
    if not _SAFE.match(version) or not _SAFE.match(target):
        raise ValidationError("invalid version or target")


def resolve(version: str, target: str) -> Artifact:
    """A build available locally (dist dir or cache); never touches the network."""
    _check_names(version, target)
    if version == "latest":
        candidates = [a for a in list_artifacts() if a.target == target]
        if not candidates:
            raise ResourceNotFoundError(resource_type="Agent build")
        return candidates[0]
    art = _artifact(version, target)
    if art is None or art.path is None:
        raise ResourceNotFoundError(resource_type="Agent build")
    roots = [dist_dir().resolve(), cache_dir().resolve()]
    if not any(r in art.path.resolve().parents for r in roots):
        raise ValidationError("artifact outside the dist directory")
    return art


# ------------------------------------------------------------ release fetch


def _release_base() -> str:
    return (download_settings.AGENT_RELEASE_URL or "").strip().rstrip("/")


def _http_client(timeout: float):
    import httpx

    kwargs: Dict[str, Any] = {"timeout": httpx.Timeout(timeout, connect=10.0)}
    if _transport is not None:
        kwargs["transport"] = _transport
    return httpx.AsyncClient(follow_redirects=True, **kwargs)


def _parse_sums(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


async def release_checksums(version: str) -> Optional[Dict[str, str]]:
    """``SHA256SUMS.txt`` of the release ``v<version>`` (cached); None when unreachable."""
    if not release_enabled() or not _SEMVER.match(version):
        return None
    now = time.monotonic()
    hit = _sums_cache.get(version)
    if hit is not None:
        ttl = _SUMS_TTL_SECONDS if hit[1] is not None else _SUMS_FAIL_TTL_SECONDS
        if now - hit[0] < ttl:
            return hit[1]
    url = f"{_release_base()}/v{version}/SHA256SUMS.txt"
    sums: Optional[Dict[str, str]] = None
    try:
        async with _http_client(15.0) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            sums = _parse_sums(resp.text)
        else:
            logger.warning(f"agent release checksums {url}: HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001 - network failures are reported as "not available"
        logger.warning(f"agent release checksums {url}: {type(exc).__name__}: {exc}")
    _sums_cache[version] = (now, sums)
    return sums


async def fetch_release_build(version: str, target: str) -> Artifact:
    """Download ``gpudeck-agent-<version>-<target>.tar.gz`` from the release into the cache."""
    _check_names(version, target)
    if not release_enabled():
        raise ResourceNotFoundError(resource_type="Agent build")
    if target not in RELEASE_TARGETS or not _SEMVER.match(version):
        raise ResourceNotFoundError(resource_type="Agent build")
    sums = await release_checksums(version)
    if sums is None:
        raise ExternalServiceError(
            f"agent release v{version} is not reachable at {_release_base()}; "
            "put the build in AGENT_DIST_DIR for offline installs"
        )
    name = release_asset_name(version, target)
    want = sums.get(name)
    if not want:
        raise ResourceNotFoundError(resource_type="Agent build")
    folder = cache_dir()
    dest = folder / name
    lock = _fetch_locks.setdefault(name, asyncio.Lock())
    async with lock:
        if dest.is_file() and file_sha256(dest) == want:
            return _make(version, target, dest, "cache")
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".part", dir=folder)
        tmp = Path(tmp_name)
        url = f"{_release_base()}/v{version}/{name}"
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as fh:
                async with _http_client(300.0) as client:
                    async with client.stream("GET", url) as resp:
                        if resp.status_code != 200:
                            raise ExternalServiceError(f"GET {url}: HTTP {resp.status_code}")
                        async for chunk in resp.aiter_bytes(1 << 20):
                            size += len(chunk)
                            if size > _MAX_DOWNLOAD_BYTES:
                                raise ExternalServiceError(f"GET {url}: larger than allowed")
                            digest.update(chunk)
                            fh.write(chunk)
            got = digest.hexdigest()
            if got != want:
                raise ExternalServiceError(f"{name}: sha256 {got} does not match the release")
            os.chmod(tmp, 0o644)
            os.replace(tmp, dest)
            (folder / f"{name}.sha256").write_text(f"{want}  {name}\n")
        except ExternalServiceError:
            tmp.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            logger.warning(f"agent build fetch {url} failed: {type(exc).__name__}: {exc}")
            raise ExternalServiceError(f"could not download {name} from the release") from exc
        logger.info(f"agent build cached: {name} ({size} bytes) from {url}")
        return _make(version, target, dest, "cache")


async def resolve_or_fetch(version: str, target: str) -> Artifact:
    """A local build when there is one, else the matching release build (fetched + cached)."""
    try:
        return resolve(version, target)
    except ResourceNotFoundError:
        if not release_enabled():
            raise
    wanted = settings.APP_VERSION if version == "latest" else version
    return await fetch_release_build(wanted, target)


async def list_with_release() -> List[Artifact]:
    """Local and cached builds plus the not-yet-downloaded builds of this version's release."""
    items = list_artifacts()
    version = settings.APP_VERSION
    have = {(a.version, a.target) for a in items}
    sums = await release_checksums(version) if release_enabled() else None
    for target in RELEASE_TARGETS if sums else ():
        name = release_asset_name(version, target)
        if (version, target) in have or name not in sums:
            continue
        os_name, arch = target_platform(target)
        items.append(
            Artifact(
                version=version,
                target=target,
                path=None,
                filename=name,
                size_bytes=0,
                sha256=sums[name],
                os=os_name,
                arch=arch,
                built_at=None,
                source="release",
            )
        )
    return items


async def latest_available_version() -> Optional[str]:
    local = latest_version()
    if local:
        return local
    return settings.APP_VERSION if release_enabled() else None


# ------------------------------------------------------------------ access


async def join_token_usable(session, token: Optional[str]) -> bool:
    """True for a join token that could still join a node (unrevoked, unexpired, uses left)."""
    if not token:
        return False
    from app.db.postgres.repositories.node_repository import JoinTokenRepository
    from app.services.nodes.service import JOIN_TOKEN_PREFIX, hash_token

    if not token.startswith(JOIN_TOKEN_PREFIX):
        return False
    row = await JoinTokenRepository(session).get_by_hash(hash_token(token))
    if row is None or row.revoked_at is not None or row.used_at is not None:
        return False
    max_uses = getattr(row, "max_uses", None) or 1
    if (getattr(row, "use_count", None) or 0) >= max_uses:
        return False
    return row.expires_at >= datetime.now(timezone.utc)


def token_from_request(request) -> Optional[str]:
    header = request.headers.get("x-gpudeck-join-token")
    if header:
        return header.strip()
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer gdj_"):
        return auth[7:].strip()
    return request.query_params.get("token")


async def require_download_access(request, session, admin: Any = None) -> None:
    if admin is not None:
        return
    if await join_token_usable(session, token_from_request(request)):
        return
    raise AuthenticationError("a valid join token (or an admin session) is required")


# ----------------------------------------------------------- install script


def hub_agent_prefix() -> str:
    """``<AGENT_HUB_PUBLIC_URL>/agent/v1``: where the hub serves the installer and builds."""
    return f"{settings.AGENT_HUB_PUBLIC_URL.rstrip('/')}/agent/v1"


def download_base_url() -> str:
    """
    URL prefix GPU machines fetch ``install.sh`` and ``downloads/...`` from: the hub
    (``<hub>/agent/v1``) unless ``AGENT_DOWNLOAD_BASE_URL`` names another host. A value
    ending in the admin API prefix (``.../api/v1``) means the admin API's copies
    (``.../api/v1/agent``).
    """
    base = (download_settings.AGENT_DOWNLOAD_BASE_URL or "").strip().rstrip("/")
    if not base:
        return hub_agent_prefix()
    if base.endswith(settings.API_V1_PREFIX.rstrip("/")):
        return f"{base}/agent"
    return base


def install_script_url() -> str:
    return f"{download_base_url()}/install.sh"


def hub_uses_internal_ca() -> bool:
    """The hub presents a certificate from the internal CA (no AGENT_HUB_TLS_CERT_FILE)."""
    return not (settings.AGENT_HUB_TLS_CERT_FILE and settings.AGENT_HUB_TLS_KEY_FILE)


def pem_sha256(pem: str) -> str:
    """sha256 of the exact bytes ``GET /agent/v1/ca.pem`` returns (pinned by the one-liner)."""
    return hashlib.sha256(pem.encode()).hexdigest()


def install_script(
    token: str,
    *,
    hub_url: str,
    ca_fingerprint: Optional[str],
    version: Optional[str] = None,
    name: Optional[str] = None,
    base_url: Optional[str] = None,
    ca_pem: Optional[str] = None,
) -> str:
    """POSIX sh installer: download + verify sha256, install, join, run as a service."""
    q = shlex.quote
    base = (base_url or download_base_url()).rstrip("/")
    ca_block = (ca_pem or "").strip()
    lines = [
        "#!/bin/sh",
        "# GPU Deck node agent installer (generated; contains a join token).",
        "# Everything comes from the agent hub (outbound 8443 only).",
        "set -eu",
        f"TOKEN={q(token)}",
        f"HUB_URL={q(hub_url)}",
        f"CA_FINGERPRINT={q(ca_fingerprint or '')}",
        f"VERSION={q(version or 'latest')}",
        f"NODE_NAME={q(name or '')}",
        f"BASE_URL={q(base)}",
        'SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"',
        'OS="$(uname -s)"; ARCH="$(uname -m)"',
        "# macOS without root: per-user install under ~/.gpudeck + a user LaunchAgent (no sudo).",
        "# GPUDECK_SYSTEM=1 forces the system layout (/usr/local, needs sudo).",
        'USER_MODE=0; [ "$OS" = "Darwin" ] && [ "$(id -u)" -ne 0 ] &&'
        ' [ "${GPUDECK_SYSTEM:-0}" != "1" ] && USER_MODE=1',
        'GD_HOME="${GPUDECK_HOME:-$HOME/.gpudeck}"',
        'if [ "$USER_MODE" = "1" ]; then SUDO=""; DEF_BIN="$GD_HOME/bin/gpudeck-agent";'
        " else DEF_BIN=/usr/local/bin/gpudeck-agent; fi",
        'BIN="${GPUDECK_BIN:-$DEF_BIN}"',
        'case "$ARCH" in x86_64|amd64) ARCH=x86_64 ;; arm64|aarch64) ARCH=aarch64 ;;'
        ' *) echo "unsupported architecture $ARCH" >&2; exit 1 ;; esac',
        'case "$OS" in',
        '  Darwin) TARGETS="${ARCH}-apple-darwin" ;;',
        '  Linux) TARGETS="${ARCH}-unknown-linux-gnu ${ARCH}-unknown-linux-musl" ;;',
        '  *) echo "unsupported OS $OS" >&2; exit 1 ;;',
        "esac",
        'TMP="$(mktemp -d)"; trap \'rm -rf "$TMP"\' EXIT',
        "# The hub's internal CA (this script itself was fetched over a pinned connection).",
        "cat > \"$TMP/ca.pem\" <<'GPUDECK_CA'",
        ca_block,
        "GPUDECK_CA",
        "# Retries for flaky / high-latency links (SSH tunnels, cross-region); no overall timeout.",
        'CURL="curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 30"',
        "fetch() {  # internal CA first, then the system trust store (custom download host)",
        '  if [ -s "$TMP/ca.pem" ] && $CURL --cacert "$TMP/ca.pem"'
        ' -H "X-GPUDeck-Join-Token: $TOKEN" "$1" -o "$2" 2>/dev/null; then return 0; fi',
        '  $CURL -H "X-GPUDeck-Join-Token: $TOKEN" "$1" -o "$2"',
        "}",
        "TARGET=''",
        "for t in $TARGETS; do",
        '  if fetch "$BASE_URL/downloads/$VERSION/$t/sha256" "$TMP/sha256" 2>/dev/null;'
        ' then TARGET="$t"; break; fi',
        "done",
        '[ -n "$TARGET" ] || { echo "no agent build for $OS/$ARCH" >&2; exit 1; }',
        'echo "Downloading gpudeck-agent ($VERSION, $TARGET)"',
        'fetch "$BASE_URL/downloads/$VERSION/$TARGET" "$TMP/gpudeck-agent"',
        'WANT="$(cut -d" " -f1 "$TMP/sha256")"',
        'if command -v sha256sum >/dev/null 2>&1; then GOT="$(sha256sum "$TMP/gpudeck-agent"'
        ' | cut -d" " -f1)"; else GOT="$(shasum -a 256 "$TMP/gpudeck-agent" | cut -d" " -f1)"; fi',
        '[ "$WANT" = "$GOT" ] || { echo "checksum mismatch ($GOT != $WANT)" >&2; exit 1; }',
        'case "$(head -c 2 "$TMP/gpudeck-agent" | od -An -c | tr -d " ")" in',
        '  "037213") tar -xzf "$TMP/gpudeck-agent" -C "$TMP" && SRC="$(find "$TMP" -type f'
        ' -name gpudeck-agent ! -path "$TMP/gpudeck-agent" | head -1)" ;;',
        '  *) SRC="$TMP/gpudeck-agent" ;;',
        "esac",
        '$SUDO install -d -m 0755 "$(dirname "$BIN")"',
        '$SUDO install -m 0755 "$SRC" "$BIN"',
        'JOIN="--hub $HUB_URL --token $TOKEN"',
        '[ -n "$NODE_NAME" ] && JOIN="$JOIN --name $NODE_NAME"',
        '[ -n "$CA_FINGERPRINT" ] && JOIN="$JOIN --ca-fingerprint $CA_FINGERPRINT"',
        'if [ "$OS" = "Linux" ]; then',
        "  CONF=/etc/gpudeck/agent.toml; STATE=/var/lib/gpudeck",
        '  $SUDO useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin gpudeck'
        " 2>/dev/null || true",
        "  # GPU device groups that exist on this host (systemd refuses unknown groups)",
        '  GPU_GROUPS=""',
        "  for g in video render; do",
        '    if getent group "$g" >/dev/null 2>&1; then',
        '      $SUDO usermod -aG "$g" gpudeck 2>/dev/null || true; GPU_GROUPS="$GPU_GROUPS $g"',
        "    fi",
        "  done",
        "  $SUDO install -d -m 0755 /etc/gpudeck",
        '  $SUDO install -d -o gpudeck -g gpudeck -m 0700 "$STATE"',
        "  # Join as root (writes $CONF and the identity), then hand the state to the agent user.",
        "  # shellcheck disable=SC2086",
        '  $SUDO "$BIN" --config "$CONF" join $JOIN --state-dir "$STATE"',
        '  $SUDO chown -R gpudeck:gpudeck "$STATE"',
        "  $SUDO tee /etc/systemd/system/gpudeck-agent.service >/dev/null <<UNIT",
        "[Unit]",
        "Description=GPU Deck node agent",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "User=gpudeck",
        "Group=gpudeck",
        "SupplementaryGroups=$GPU_GROUPS",
        "ExecStart=$BIN --config $CONF run",
        "Restart=always",
        "RestartSec=5",
        # Engines outlive agent restarts / upgrades (re-adopted on start); only the agent stops.
        "KillMode=process",
        "LimitNOFILE=65536",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ReadWritePaths=$STATE",
        "StateDirectory=gpudeck gpudeck/tmp",
        "StateDirectoryMode=0700",
        # Engines (vLLM / torch inductor, triton) write to /tmp; strict makes it read-only. A
        # private /tmp that outlives agent restarts (PrivateTmp is wiped on every restart).
        "BindPaths=$STATE/tmp:/tmp",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "UNIT",
        "  $SUDO systemctl daemon-reload",
        "  $SUDO systemctl enable gpudeck-agent",
        "  # restart, not start: a re-install (new binary / identity) replaces a running",
        "  # agent; engines keep running and are re-adopted.",
        "  $SUDO systemctl restart gpudeck-agent",
        "else",
        '  LABEL="io.github.sur950.gpudeck-agent"',
        '  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"',
        '  if [ "$USER_MODE" = "1" ]; then',
        '    CONF="$GD_HOME/agent.toml"; STATE="$GD_HOME/state"',
        '    LOGF="$GD_HOME/logs/gpudeck-agent.log"',
        '    install -d -m 0750 "$GD_HOME" "$STATE" "$GD_HOME/logs"',
        "  else",
        "    CONF=/usr/local/etc/gpudeck/agent.toml; STATE=/usr/local/var/gpudeck",
        "    LOGF=/usr/local/var/log/gpudeck-agent.log",
        '    $SUDO install -d -o "$(id -un)" -m 0750 /usr/local/etc/gpudeck "$STATE"'
        " /usr/local/var/log",
        "  fi",
        "  # shellcheck disable=SC2086",
        '  "$BIN" --config "$CONF" join $JOIN --state-dir "$STATE"',
        '  mkdir -p "$HOME/Library/LaunchAgents"',
        "  # launchd starts agents with a minimal PATH: keep the installer's PATH so engines",
        "  # (llama-server, mlx_lm.server) and the container CLI (docker) resolve.",
        '  AGENT_PATH="${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"',
        '  cat > "$PLIST" <<PLIST',
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
        '<plist version="1.0"><dict>',
        "  <key>Label</key><string>$LABEL</string>",
        "  <key>ProgramArguments</key><array><string>$BIN</string><string>--config</string>"
        "<string>$CONF</string><string>run</string></array>",
        "  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$AGENT_PATH</string>"
        "<key>HOME</key><string>$HOME</string></dict>",
        "  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>",
        "  <key>AbandonProcessGroup</key><true/>",
        "  <key>ProcessType</key><string>Interactive</string>",
        "  <key>StandardOutPath</key><string>$LOGF</string>",
        "  <key>StandardErrorPath</key><string>$LOGF</string>",
        "</dict></plist>",
        "PLIST",
        '  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true',
        '  launchctl bootstrap "gui/$(id -u)" "$PLIST"',
        "fi",
        'echo "gpudeck-agent installed and joined; it appears under Fleet > Nodes shortly."',
        "",
    ]
    return "\n".join(lines)


def install_command(token: str, ca_pem: Optional[str] = None) -> str:
    """
    One-liner shown in the join dialog. By default it talks only to the hub.

    When the hub presents an internal-CA certificate the command first fetches the CA
    (``<hub>/agent/v1/ca.pem``), checks its sha256 against the value pinned here and only
    then fetches the installer with ``--cacert``; a hub with its own public certificate, or
    a custom ``AGENT_DOWNLOAD_BASE_URL``, is fetched with the system trust store.
    """
    base = download_base_url()
    script = f"{base}/install.sh"
    header = f"X-GPUDeck-Join-Token: {token}"
    if not ca_pem or not hub_uses_internal_ca() or base != hub_agent_prefix():
        return f"curl -fsSL -H '{header}' '{script}' | sh"
    pin = pem_sha256(ca_pem)
    return (
        "sh -c 'c=$(mktemp) && "
        f'curl -fsSk -o "$c" {base}/ca.pem && '
        f'echo "{pin}  $c" | (sha256sum -c - 2>/dev/null || shasum -a 256 -c -) >/dev/null && '
        f'curl -fsSL --cacert "$c" -H "{header}" {script} | sh'
        "'"
    )


# ------------------------------------------------------------ HTTP helpers


# Agent builds are 10+ MB and often fetched through a tunnel (``ssh -R``) or over a long
# link: 1 MiB reads keep few, large writes in flight (Starlette's default is 64 KiB, each
# a thread hop plus a send). Servers that support ``http.response.pathsend`` (zero-copy
# sendfile) get the path instead; Range requests let curl resume.
DOWNLOAD_CHUNK_BYTES = 1 << 20


class LargeChunkFileResponse(FileResponse):
    chunk_size = DOWNLOAD_CHUNK_BYTES


def file_response(art: Artifact):
    if art.path is None:
        raise ResourceNotFoundError(resource_type="Agent build")
    return LargeChunkFileResponse(
        art.path,
        media_type="application/octet-stream",
        filename=art.filename,
        headers={
            "X-Checksum-Sha256": art.sha256,
            "X-GPUDeck-Agent-Version": art.version,
            "X-GPUDeck-Agent-Target": art.target,
            "Cache-Control": "no-store",
        },
    )


def sha256_response(art: Artifact):
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(
        f"{art.sha256}  {art.filename}\n", headers={"Cache-Control": "no-store"}
    )


async def download_response(request, session, version: str, target: str, admin: Any = None):
    """Access check, then the build (fetched from the release on first use)."""
    await require_download_access(request, session, admin)
    await session.commit()  # release the pooled connection before a possible release fetch
    return file_response(await resolve_or_fetch(version, target))


async def download_sha256_response(request, session, version: str, target: str, admin: Any = None):
    await require_download_access(request, session, admin)
    await session.commit()
    try:
        return sha256_response(resolve(version, target))
    except ResourceNotFoundError:
        if not release_enabled():
            raise
    wanted = settings.APP_VERSION if version == "latest" else version
    _check_names(wanted, target)
    sums = await release_checksums(wanted)
    name = release_asset_name(wanted, target)
    if target not in RELEASE_TARGETS or not sums or name not in sums:
        raise ResourceNotFoundError(resource_type="Agent build")
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(f"{sums[name]}  {name}\n", headers={"Cache-Control": "no-store"})


async def install_script_response(request, session, version: Optional[str] = None):
    """Render the installer for the join token carried by the request (token required)."""
    from app.db.postgres.repositories.node_repository import JoinTokenRepository
    from app.services.nodes.service import hash_token
    from app.services.pki.service import PkiService
    from fastapi.responses import PlainTextResponse

    token = token_from_request(request)
    if not await join_token_usable(session, token):
        raise AuthenticationError("a valid, unused join token is required")
    row = await JoinTokenRepository(session).get_by_hash(hash_token(token))
    ca = await PkiService.from_session(session).ensure_ca()
    script = install_script(
        token,
        hub_url=settings.AGENT_HUB_PUBLIC_URL,
        ca_fingerprint=ca.fingerprint,
        version=version,
        name=row.node_name_hint if row is not None else None,
        ca_pem=ca.cert_pem,
    )
    return PlainTextResponse(
        script, media_type="text/x-shellscript", headers={"Cache-Control": "no-store"}
    )
