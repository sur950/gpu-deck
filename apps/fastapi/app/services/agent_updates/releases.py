"""
Agent releases for self-update (agent protocol §14): signed manifests and the binaries nodes
stage.

A release publishes ``gpudeck-agent-<version>-manifest.json`` (per target: the archive and its
sha256, the ``gpudeck-agent`` binary inside and its sha256) and ``...-manifest.json.sig``
(base64 ed25519 signature over the exact file bytes, made by the release workflow with the
release signing key). Only manifests that verify with ``AGENT_RELEASE_PUBLIC_KEYS`` are offered;
nodes verify again with the keys pinned in their signed security policy.

Where a manifest comes from, first match wins:

1. ``AGENT_DIST_DIR`` (air-gapped / offline): ``<dir>/<version>/<name>`` or ``<dir>/<name>``;
2. ``AGENT_CACHE_DIR``: ``<dir>/<name>`` (fetched earlier);
3. ``AGENT_RELEASE_URL`` (when set): ``<url>/v<version>/<name>``, then cached.

The binary a node stages is taken from the release archive the control plane already serves
(``agent_downloads.resolve_or_fetch``: local first, the release when allowed), checked against
the manifest (archive sha256, then the binary's own sha256) and extracted once to
``<AGENT_CACHE_DIR>/updates/<version>/<target>/gpudeck-agent``. A nested dist build
(``<dir>/<version>/<target>/gpudeck-agent``) with the right sha256 is used as is.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import ExternalServiceError, ResourceNotFoundError
from app.core.logger import get_logger
from app.core.settings import settings
from app.services.nodes import agent_downloads as dl

logger = get_logger(__name__)

_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TARGET = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$")
_FLAT_MANIFEST = re.compile(r"^gpudeck-agent-(?P<version>.+)-manifest\.json$")
MAX_MANIFEST_BYTES = 256 * 1024
MAX_BINARY_BYTES = 512 * 1024 * 1024
_TARGET_TTL_SECONDS = 60.0
_RELEASE_TTL_SECONDS = 600.0
_RELEASE_FAIL_TTL_SECONDS = 60.0

_release_cache: Dict[str, Tuple[float, Optional["SignedManifest"]]] = {}
_target_cache: Dict[str, Tuple[float, Optional[str], Optional[str]]] = {}
_extract_locks: Dict[str, asyncio.Lock] = {}


def manifest_name(version: str) -> str:
    return f"gpudeck-agent-{version}-manifest.json"


def is_version(value: Optional[str]) -> bool:
    return bool(value) and bool(_SEMVER.fullmatch(str(value)))


def version_key(value: str) -> Tuple:
    """Semver order; a release sorts after its pre-releases."""
    core, _, pre = str(value).lstrip("v").partition("-")
    nums = []
    for part in core.split("."):
        try:
            nums.append(int(part))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return (tuple(nums[:3]), pre == "", pre)


def compare(a: Optional[str], b: Optional[str]) -> int:
    """-1 / 0 / 1 (unparsable versions compare as 0.0.0)."""
    ka, kb = version_key(a or "0.0.0"), version_key(b or "0.0.0")
    return (ka > kb) - (ka < kb)


@dataclass
class SignedManifest:
    version: str
    raw: bytes
    signature: str
    doc: Dict[str, Any]
    source: str

    def artifact(self, target: str) -> Optional[Dict[str, Any]]:
        for a in self.doc.get("artifacts") or []:
            if a.get("target") == target:
                return a
        return None

    def targets(self) -> List[str]:
        return [a["target"] for a in self.doc.get("artifacts") or []]

    def wire(self) -> Dict[str, str]:
        """``common.json#/$defs/SignedReleaseManifest`` for ``stage_update``."""
        return {
            "manifest": base64.b64encode(self.raw).decode(),
            "signature": self.signature,
            "algorithm": "ed25519",
        }


def public_keys() -> List[str]:
    return settings.get_release_public_keys()


def verify_signature(raw: bytes, signature_b64: str, keys: List[str]) -> bool:
    """ed25519 over exactly ``raw`` with any of ``keys`` (base64 32-byte public keys)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        sig = base64.b64decode("".join(signature_b64.split()), validate=True)
    except ValueError:
        return False
    for key in keys:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(sig, raw)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def parse_manifest(raw: bytes) -> Dict[str, Any]:
    """The manifest document, shape-checked (``common.json#/$defs/ReleaseManifest``)."""
    doc = json.loads(raw)
    if not isinstance(doc, dict) or doc.get("schema") != 1 or doc.get("product") != "gpudeck-agent":
        raise ValueError("not an agent release manifest")
    if not is_version(doc.get("version")):
        raise ValueError("manifest version is not a version")
    arts = doc.get("artifacts")
    if not isinstance(arts, list) or not arts:
        raise ValueError("manifest lists no artifacts")
    for a in arts:
        if not isinstance(a, dict) or not _TARGET.fullmatch(str(a.get("target") or "")):
            raise ValueError("manifest artifact without a valid target")
        if not _HEX64.fullmatch(str(a.get("binary_sha256") or "")):
            raise ValueError(f"artifact {a.get('target')}: invalid binary_sha256")
        size = a.get("binary_size_bytes")
        if not isinstance(size, int) or size <= 0 or size > MAX_BINARY_BYTES:
            raise ValueError(f"artifact {a.get('target')}: invalid binary_size_bytes")
        if a.get("sha256") is not None and not _HEX64.fullmatch(str(a["sha256"])):
            raise ValueError(f"artifact {a.get('target')}: invalid sha256")
    return doc


def _read_small(path: Path, limit: int = MAX_MANIFEST_BYTES) -> Optional[bytes]:
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _load(path: Path, version: str, source: str) -> Optional[SignedManifest]:
    raw = _read_small(path)
    sig = _read_small(Path(f"{path}.sig"), 4096)
    if raw is None or sig is None:
        return None
    keys = public_keys()
    if not verify_signature(raw, sig.decode(errors="replace"), keys):
        logger.warning(f"agent release manifest {path}: signature does not verify; ignored")
        return None
    try:
        doc = parse_manifest(raw)
    except ValueError as exc:
        logger.warning(f"agent release manifest {path}: {exc}; ignored")
        return None
    if doc["version"] != version:
        logger.warning(f"agent release manifest {path} is for {doc['version']}; ignored")
        return None
    return SignedManifest(version, raw, sig.decode().strip(), doc, source)


def load_local(version: str) -> Optional[SignedManifest]:
    """A verified manifest from AGENT_DIST_DIR or AGENT_CACHE_DIR (no network)."""
    if not is_version(version) or not public_keys():
        return None
    name = manifest_name(version)
    candidates: List[Tuple[Path, str]] = []
    try:
        candidates.append((dl._confined(dl.dist_dir(), version, name), "local"))
        candidates.append((dl._confined(dl.dist_dir(), name), "local"))
        candidates.append((dl._confined(dl.cache_dir(), name), "cache"))
    except Exception:  # noqa: BLE001 - an unsafe version string never matches a file
        return None
    for path, source in candidates:
        found = _load(path, version, source)
        if found is not None:
            return found
    return None


def local_versions() -> List[str]:
    """Versions with a manifest file in the dist or cache directory (not yet verified)."""
    found = set()
    for root in (dl.dist_dir(), dl.cache_dir()):
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            m = _FLAT_MANIFEST.match(entry.name)
            if entry.is_file() and m and is_version(m["version"]):
                found.add(m["version"])
            elif entry.is_dir() and is_version(entry.name):
                if (entry / manifest_name(entry.name)).is_file():
                    found.add(entry.name)
    return sorted(found, key=version_key, reverse=True)


async def fetch_release(version: str) -> Optional[SignedManifest]:
    """The manifest of release ``v<version>`` from AGENT_RELEASE_URL (verified, then cached)."""
    if not dl.release_enabled() or not is_version(version) or not public_keys():
        return None
    now = time.monotonic()
    hit = _release_cache.get(version)
    if hit is not None:
        ttl = _RELEASE_TTL_SECONDS if hit[1] is not None else _RELEASE_FAIL_TTL_SECONDS
        if now - hit[0] < ttl:
            return hit[1]
    name = manifest_name(version)
    base = f"{dl._release_base()}/v{version}/{name}"
    result: Optional[SignedManifest] = None
    try:
        async with dl._http_client(20.0) as client:
            raw_resp = await client.get(base)
            sig_resp = await client.get(f"{base}.sig")
        if raw_resp.status_code == 200 and sig_resp.status_code == 200:
            raw = raw_resp.content[: MAX_MANIFEST_BYTES + 1]
            sig = sig_resp.text.strip()
            if len(raw) <= MAX_MANIFEST_BYTES and verify_signature(raw, sig, public_keys()):
                doc = parse_manifest(raw)
                if doc["version"] == version:
                    result = SignedManifest(version, raw, sig, doc, "release")
                    _cache_manifest(name, raw, sig)
            else:
                logger.warning(f"agent release manifest {base}: signature does not verify")
        else:
            logger.info(
                f"agent release manifest {base}: HTTP {raw_resp.status_code}/{sig_resp.status_code}"
            )
    except Exception as exc:  # noqa: BLE001 - unreachable release = not available
        logger.warning(f"agent release manifest {base}: {type(exc).__name__}: {exc}")
    _release_cache[version] = (now, result)
    return result


def _cache_manifest(name: str, raw: bytes, sig: str) -> None:
    try:
        folder = dl.cache_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for fname, data in ((name, raw), (f"{name}.sig", (sig + "\n").encode())):
            dest = dl._confined(folder, fname)
            fd, tmp = tempfile.mkstemp(prefix=".manifest-", dir=folder)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, 0o644)
            os.replace(tmp, dest)
    except Exception as exc:  # noqa: BLE001 - the cache is an optimisation
        logger.warning(f"could not cache {name}: {exc}")


async def get_manifest(version: str) -> Optional[SignedManifest]:
    return load_local(version) or await fetch_release(version)


async def target_version(force: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """
    ``(version, None)`` nodes should run, or ``(None, reason)`` when updates are off or no
    signed release is available. Cached for a minute.
    """
    now = time.monotonic()
    hit = _target_cache.get("t")
    if hit is not None and not force and now - hit[0] < _TARGET_TTL_SECONDS:
        return hit[1], hit[2]
    version, reason = await _compute_target()
    _target_cache["t"] = (now, version, reason)
    return version, reason


def cached_target() -> Tuple[Optional[str], Optional[str]]:
    """Last computed target (no I/O); ``(None, None)`` before the first computation."""
    hit = _target_cache.get("t")
    return (hit[1], hit[2]) if hit else (None, None)


def reset_caches() -> None:
    _target_cache.clear()
    _release_cache.clear()


async def _compute_target() -> Tuple[Optional[str], Optional[str]]:
    if not public_keys():
        return None, "agent updates are off: AGENT_RELEASE_PUBLIC_KEYS is not set"
    pinned = (settings.AGENT_UPDATE_TARGET_VERSION or "").strip()
    if pinned:
        if not is_version(pinned):
            return None, f"AGENT_UPDATE_TARGET_VERSION {pinned!r} is not a version"
        if await get_manifest(pinned) is None:
            return None, f"no signed release manifest for {pinned}"
        return pinned, None
    versions = [v for v in local_versions() if load_local(v) is not None]
    if dl.release_enabled() and await fetch_release(settings.APP_VERSION) is not None:
        versions.append(settings.APP_VERSION)
    if not versions:
        return (
            None,
            "no signed agent release is available (AGENT_DIST_DIR, AGENT_RELEASE_URL)",
        )
    return max(versions, key=version_key), None


async def overview() -> Dict[str, Any]:
    target, reason = await target_version()
    keys = public_keys()
    manifest = await get_manifest(target) if target else None
    return {
        "enabled": target is not None,
        "reason": reason,
        "target_version": target,
        "release_keys": len(keys),
        "prestage": bool(settings.AGENT_UPDATE_PRESTAGE),
        "health_timeout_seconds": int(settings.AGENT_UPDATE_HEALTH_TIMEOUT_SECONDS),
        "targets": manifest.targets() if manifest else [],
        "source": manifest.source if manifest else None,
        "available_versions": local_versions(),
    }


# ------------------------------------------------------------------ binaries


def _sha256_file(path: Path) -> str:
    return dl.file_sha256(path)


def _extract(archive: Path, dest: Path, want: str, size: int) -> None:
    """Extract the ``gpudeck-agent`` member of a release tarball to ``dest`` (verified)."""
    import hashlib

    with tarfile.open(archive, "r:gz") as tar:
        member = next(
            (
                m
                for m in tar.getmembers()
                if m.isfile() and os.path.basename(m.name) == "gpudeck-agent"
            ),
            None,
        )
        if member is None:
            raise ExternalServiceError(f"{archive.name} contains no gpudeck-agent binary")
        if member.size != size or member.size > MAX_BINARY_BYTES:
            raise ExternalServiceError(f"{archive.name}: gpudeck-agent size does not match")
        src = tar.extractfile(member)
        if src is None:
            raise ExternalServiceError(f"{archive.name}: unreadable gpudeck-agent")
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".gpudeck-agent-", dir=dest.parent)
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as fh:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    digest.update(chunk)
                    fh.write(chunk)
            if digest.hexdigest() != want:
                raise ExternalServiceError(
                    f"{archive.name}: gpudeck-agent sha256 does not match the signed manifest"
                )
            os.chmod(tmp, 0o755)
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


async def binary_for(version: str, target: str) -> Path:
    """The verified ``gpudeck-agent`` binary of ``version`` for ``target`` (extracted once)."""
    manifest = await get_manifest(version)
    if manifest is None:
        raise ResourceNotFoundError(resource_type="Agent release manifest")
    art = manifest.artifact(target)
    if art is None:
        raise ResourceNotFoundError(resource_type="Agent build")
    want = str(art["binary_sha256"])
    size = int(art["binary_size_bytes"])
    out = dl._confined(dl.cache_dir(), "updates", version, target, "gpudeck-agent")
    lock = _extract_locks.setdefault(f"{version}/{target}", asyncio.Lock())
    async with lock:
        if out.is_file() and await asyncio.to_thread(_sha256_file, out) == want:
            return out
        try:
            nested = dl._confined(dl.dist_dir(), version, target, "gpudeck-agent")
        except Exception:  # noqa: BLE001
            nested = None
        if nested is not None and nested.is_file():
            if await asyncio.to_thread(_sha256_file, nested) == want:
                return nested
        try:
            archive = dl._flat(dl.dist_dir(), version, target, "local") or dl._flat(
                dl.cache_dir(), version, target, "cache"
            )
        except Exception:  # noqa: BLE001
            archive = None
        if archive is None:
            archive = await dl.fetch_release_build(version, target)
        if archive.path is None:
            raise ResourceNotFoundError(resource_type="Agent build")
        expected_archive = art.get("sha256")
        if expected_archive and archive.sha256 != expected_archive:
            raise ExternalServiceError(
                f"{archive.filename}: sha256 does not match the signed release manifest"
            )
        await asyncio.to_thread(_extract, archive.path, out, want, size)
        logger.info(f"agent update binary ready: {version} {target} ({size} bytes)")
        return out


def binary_response(path: Path, version: str, target: str):
    return dl.LargeChunkFileResponse(
        path,
        media_type="application/octet-stream",
        filename="gpudeck-agent",
        headers={
            "X-GPUDeck-Agent-Version": version,
            "X-GPUDeck-Agent-Target": target,
            "Cache-Control": "no-store",
        },
    )
