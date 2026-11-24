"""
Native engine artifact selection.

``engine_versions.artifacts`` maps a platform key to one pinned build::

    "<os>/<arch>"                 CPU (Linux) or Metal (macOS) build, the fallback
    "<os>/<arch>/<accelerator>"   GPU build: cuda | rocm | vulkan

Each entry is ``{url, sha256, kind, entrypoint?, size_bytes?, parts?,
strip_components?, pip_extras?, python?, build?}`` (the agent protocol
``Artifact``) plus selection-only keys that are not sent to the agent:
``min_driver_version`` (NVIDIA driver, e.g. ``"525.60"``) and
``min_runtime_version`` (ROCm / CUDA user space reported by the device).

For a node the GPU builds are tried in vendor order (NVIDIA: cuda, vulkan;
AMD / Hygon: rocm, vulkan; Moore Threads: vulkan) and the first one whose
driver / runtime minimum the node meets wins; the CPU build is the last
resort. Unpinned entries (missing or all-zero sha256) are never selected: the
agent then uses an operator-installed engine (``engine_paths``).

Host compatibility (glibc). Prebuilt Linux engines need the glibc of the
system that built them, so an entry may carry:

- ``min_glibc`` (sent to the agent): oldest host glibc the build runs on;
- ``fallbacks``: same-accelerator builds for older systems (an older release
  built on Ubuntu 22.04), each a full entry with its own minimums;
- ``container``: ``{image, entrypoint, version}``, the engine's official image
  (tag + index digest) for hosts no native build fits.

The agent makes the final choice on the node (``engine_compat.rs``). When the
node reported its glibc (``hello.capabilities.glibc_version``) the control plane
filters too: an accelerator whose native builds the node cannot run and whose
image it cannot run (no container runtime) is skipped for the next one (e.g.
CUDA -> Vulkan), and :func:`incompatibility` explains a platform with no fit.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger

logger = get_logger(__name__)

AGENT_KEYS: Tuple[str, ...] = (
    "url",
    "sha256",
    "kind",
    "entrypoint",
    "size_bytes",
    "parts",
    "strip_components",
    "pip_extras",
    "python",
    "build",
    "min_glibc",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UNPINNED = "0" * 64

ACCELERATOR_ORDER: Dict[str, Tuple[str, ...]] = {
    "nvidia": ("cuda", "vulkan"),
    "amd": ("rocm", "vulkan"),
    "hygon": ("rocm", "vulkan"),
    "moorethreads": ("vulkan",),
}


def version_tuple(value: Optional[str]) -> Optional[Tuple[int, ...]]:
    """``"550.54.15"`` -> ``(550, 54, 15)``; None when there is no leading number."""
    if not value:
        return None
    nums = re.findall(r"\d+", str(value))
    return tuple(int(n) for n in nums[:4]) if nums else None


def meets(have: Optional[str], need: Optional[str]) -> bool:
    if not need:
        return True
    want, got = version_tuple(need), version_tuple(have)
    if want is None:
        return True
    if got is None:
        return False
    return got >= want


def candidate_keys(os_name: str, arch: str, vendor: Optional[str] = None) -> List[str]:
    base = f"{os_name}/{arch}"
    accels = ACCELERATOR_ORDER.get(vendor or "", ()) if os_name == "linux" else ()
    return [f"{base}/{a}" for a in accels] + [base]


def is_pinned(entry: Any) -> bool:
    if not isinstance(entry, dict) or not entry.get("url") or not entry.get("kind"):
        return False
    sha = str(entry.get("sha256") or "")
    return bool(_SHA256.match(sha)) and sha != _UNPINNED


def for_agent(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {k: entry[k] for k in AGENT_KEYS if k in entry and entry[k] is not None}


def glibc_meets(have: Optional[str], need: Optional[str]) -> bool:
    """A host with glibc ``have`` runs a build needing ``need`` (no need = yes)."""
    if not need:
        return True
    return meets(have, need) if have else False


def _inherit(entry: Dict[str, Any], option: Dict[str, Any], key: str) -> Any:
    return option[key] if key in option else entry.get(key)


def _options(
    entry: Dict[str, Any], driver_version: Optional[str], runtime_version: Optional[str]
) -> List[Dict[str, Any]]:
    """The entry and its fallbacks that are pinned and meet the node's driver / runtime."""
    out = []
    for option in [entry, *(entry.get("fallbacks") or [])]:
        if not is_pinned(option):
            continue
        if not meets(driver_version, _inherit(entry, option, "min_driver_version")):
            continue
        if not meets(runtime_version, _inherit(entry, option, "min_runtime_version")):
            continue
        out.append(option)
    return out


def _container(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    c = entry.get("container")
    if not isinstance(c, dict) or "@sha256:" not in str(c.get("image") or ""):
        return None
    return {k: c[k] for k in ("image", "entrypoint", "version") if c.get(k)}


def _agent_artifact(
    options: List[Dict[str, Any]], container: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    out = for_agent(options[0])
    if len(options) > 1:
        out["fallbacks"] = [for_agent(o) for o in options[1:]]
    if container:
        out["container"] = container
    return out


def select(
    artifacts: Optional[Dict[str, Any]],
    os_name: Optional[str],
    arch: Optional[str],
    vendor: Optional[str] = None,
    driver_version: Optional[str] = None,
    runtime_version: Optional[str] = None,
    glibc_version: Optional[str] = None,
    container_runtime: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    The best pinned build for the node, in agent shape (with its ``fallbacks`` and
    ``container``), or None. ``glibc_version`` None = unknown (older agent, macOS): no glibc
    filtering, the agent decides.
    """
    if not artifacts or not os_name or not arch:
        return None
    check_glibc = os_name == "linux" and bool(glibc_version)
    for key in candidate_keys(os_name, arch, vendor):
        entry = artifacts.get(key)
        if not isinstance(entry, dict):
            continue
        options = _options(entry, driver_version, runtime_version)
        if not options:
            continue
        container = _container(entry)
        if not check_glibc:
            return _agent_artifact(options, container)
        runnable = [o for o in options if glibc_meets(glibc_version, o.get("min_glibc"))]
        if runnable:
            return _agent_artifact(runnable, container)
        if container and container_runtime:
            # No native build runs on this glibc: the agent runs the official image.
            return _agent_artifact(options, container)
    return None


def incompatibility(
    artifacts: Optional[Dict[str, Any]],
    os_name: Optional[str],
    arch: Optional[str],
    glibc_version: Optional[str],
    container_runtime: bool,
) -> Optional[str]:
    """Why no pinned build of the platform runs on a node with ``glibc_version`` (None = some does,
    or there is no build for the platform at all)."""
    if os_name != "linux" or not glibc_version or not arch:
        return None
    base = f"{os_name}/{arch}"
    entries = [
        e
        for k, e in (artifacts or {}).items()
        if isinstance(e, dict) and (k == base or str(k).startswith(base + "/"))
    ]
    options = [o for e in entries for o in [e, *(e.get("fallbacks") or [])] if is_pinned(o)]
    if not options or any(glibc_meets(glibc_version, o.get("min_glibc")) for o in options):
        return None
    if container_runtime and any(_container(e) for e in entries):
        return None
    need = min((version_tuple(o.get("min_glibc")) or (0,) for o in options), default=(0,))
    need_s = ".".join(str(n) for n in need)
    ways = []
    if any(_container(e) for e in entries):
        ways.append("install Docker or Podman on the node (the agent then runs the official image)")
    ways.append("install the engine on the node and set engine_paths in agent.toml")
    return (
        f"the pinned {base} builds need glibc >= {need_s} (Ubuntu 24.04, Debian 13 or newer); "
        f"the node has glibc {glibc_version}. Options: " + "; ".join(ways)
    )


def platforms(artifacts: Optional[Dict[str, Any]]) -> List[str]:
    """``os/arch`` pairs with at least one pinned build."""
    out = []
    for key, entry in (artifacts or {}).items():
        base = "/".join(str(key).split("/")[:2])
        if is_pinned(entry) and base not in out:
            out.append(base)
    return out


def node_host(capabilities: Optional[Dict[str, Any]]) -> Tuple[Optional[str], bool]:
    """(glibc_version, has a container runtime) from ``nodes.capabilities`` (the hello)."""
    caps = capabilities if isinstance(capabilities, dict) else {}
    runtimes = caps.get("runtimes") or []
    glibc = caps.get("glibc_version")
    return (str(glibc) if glibc else None), (
        "container" in runtimes or bool(caps.get("container_runtime"))
    )


def first_device(
    devices: Sequence[Any],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(vendor, driver_version, runtime_version) of the first device (rows or dicts)."""
    for d in devices or ():
        get = d.get if isinstance(d, dict) else (lambda k, _d=d: getattr(_d, k, None))
        vendor = get("vendor")
        if vendor:
            return str(vendor), get("driver_version"), get("runtime_version")
    return None, None, None
