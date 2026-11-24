"""
Builders for the agent command arguments deployments send (pure functions over row
objects; ORM rows or simple namespaces both work).

* ``engine_spec``   -> ``common.json#/$defs/EngineSpec``
* ``model_spec``    -> ``common.json#/$defs/ModelSpec``
* ``launch_spec``   -> ``common.json#/$defs/LaunchSpec`` (flags merged as engine
  defaults < model defaults < preset < deployment flags)
* ``load_model_args`` / ``start_engine_args`` / ``unload_model_args`` ->
  ``command.json`` LoadModelArgs / StartEngineArgs / UnloadModelArgs

Only keys allowed by the contract are emitted (the schemas are
``additionalProperties: false``).
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.services.engines.presets import (
    merge_launch_flags,
    preset_concurrency,
    preset_flags,
)

logger = get_logger(__name__)

_TEMPLATE_KEYS = ("command", "env", "working_dir", "router_mode")
_HEALTH_KEYS = (
    "path",
    "method",
    "interval_seconds",
    "timeout_seconds",
    "healthy_statuses",
    "startup_timeout_seconds",
    "failure_threshold",
)
_METRICS_KEYS = (
    "path",
    "format",
    "slots_path",
    "scrape_interval_seconds",
    "derive_from_proxy",
    "metric_prefix",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def default_cache_root(node: Any) -> str:
    """Node cache root (``nodes.cache_dir`` or the agent default per OS)."""
    if getattr(node, "cache_dir", None):
        return str(node.cache_dir).rstrip("/")
    if getattr(node, "os", None) == "macos":
        return "/usr/local/var/gpudeck/cache"
    return "/var/lib/gpudeck/cache"


def served_model_name(model_name: str, deployment_id: uuid.UUID) -> str:
    """Name the engine answers to; unique per deployment so router-mode engines never clash."""
    base = re.sub(r"[^A-Za-z0-9._:-]+", "-", model_name).strip("-") or "model"
    return f"{base}--{str(deployment_id)[:8]}"


def is_router_mode(engine_version: Any) -> bool:
    return bool((getattr(engine_version, "launch_template", None) or {}).get("router_mode"))


def _port_range(value: Any) -> List[int]:
    lower = getattr(value, "lower", None)
    upper = getattr(value, "upper", None)
    if lower is None or upper is None:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return [int(value[0]), int(value[1])]
        return [10240, 10340]
    if getattr(value, "bounds", "[)")[1] == "]":
        upper = upper + 1
    return [int(lower), int(upper)]


def select_artifact(
    engine_version: Any,
    node_os: Optional[str],
    node_arch: Optional[str],
    installed: bool = False,
    devices: Sequence[Any] = (),
    capabilities: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Native artifact for the node's platform, GPU and glibc (``app.services.engines.artifacts``),
    or None when the node already has the engine installed, there is no pinned build for
    the platform, or the entry is unpinned: the agent then uses its local binary.
    """
    if installed or not node_os or not node_arch:
        return None
    from app.services.engines import artifacts as arts

    vendor, driver, runtime = arts.first_device(devices)
    glibc, container = arts.node_host(capabilities)
    return arts.select(
        getattr(engine_version, "artifacts", None) or {},
        node_os,
        node_arch,
        vendor,
        driver,
        runtime,
        glibc,
        container,
    )


def engine_spec(
    engine: Any,
    engine_version: Any,
    node_os: Optional[str] = None,
    node_arch: Optional[str] = None,
    installed: bool = False,
    devices: Sequence[Any] = (),
    capabilities: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    template = dict(getattr(engine_version, "launch_template", None) or {})
    spec_template = {k: template[k] for k in _TEMPLATE_KEYS if k in template}
    spec_template.setdefault("command", ["serve", "--port", "{{port}}"])
    spec_template["env"] = {str(k): str(v) for k, v in (spec_template.get("env") or {}).items()}
    health = {
        k: v
        for k, v in (getattr(engine_version, "health_config", None) or {}).items()
        if k in _HEALTH_KEYS
    }
    metrics = {
        k: v
        for k, v in (getattr(engine_version, "metrics_config", None) or {}).items()
        if k in _METRICS_KEYS
    }
    return {
        "engine_version_id": str(engine_version.id),
        "name": engine.name,
        "version": engine_version.version,
        # An engine the operator installed on the node (``engine_paths`` / PATH, reported as a
        # native capability) runs natively even when the catalog default is the container.
        "runtime": "native" if installed else engine.runtime,
        "image": getattr(engine_version, "image", None),
        "artifact": select_artifact(
            engine_version, node_os, node_arch, installed, devices, capabilities
        ),
        "launch_template": spec_template,
        "health": health,
        "metrics": metrics,
        "port_range": _port_range(getattr(engine_version, "port_range", None)),
        "capabilities": dict(getattr(engine, "capabilities", None) or {}),
    }


def file_ref(f: Any) -> Dict[str, Any]:
    ref: Dict[str, Any] = {
        "model_file_id": str(f.id),
        "path": f.path,
        "format": f.format,
        "size_bytes": int(f.size_bytes or 0),
        "sha256": f.sha256 if f.sha256 and _SHA256.match(str(f.sha256)) else None,
        "is_required": bool(f.is_required),
    }
    return ref


def model_spec(
    model: Any,
    version: Any,
    files: Sequence[Any],
    served_name: str,
    cache_root: str,
    source: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    required = [f for f in files if f.is_required] or list(files)
    return {
        "model_id": str(model.id),
        "model_version_id": str(version.id),
        "name": model.name,
        "served_model_name": served_name,
        "type": model.type,
        "format": version.format,
        "files": [file_ref(f) for f in required],
        "cache_dir": f"{cache_root}/{version.id}",
        "source": source,
        "config": {k: v for k, v in (version.config or {}).items() if not str(k).startswith("_")},
        "trust_remote_code": bool(model.trust_remote_code),
        "allow_pickle": bool(model.allow_pickle),
    }


def device_assignments(
    devices: Sequence[Any], slice_spec: Optional[Dict[str, int]] = None
) -> List[Dict[str, Any]]:
    out = []
    for d in devices:
        item: Dict[str, Any] = {
            "device_id": str(d.id),
            "index": int(d.index),
            "vendor": d.vendor,
        }
        item["slice"] = (
            {
                "vram_bytes": int(slice_spec["vram_bytes"]),
                "compute_pct": int(slice_spec["compute_pct"]),
            }
            if slice_spec
            else None
        )
        out.append(item)
    return out


def effective_flags(
    deployment: Any, model: Any, engine: Any, engine_version: Any
) -> Dict[str, Any]:
    preset = getattr(deployment, "preset", None)
    return merge_launch_flags(
        getattr(engine_version, "default_launch_flags", None) or {},
        getattr(model, "default_launch_flags", None) or {},
        preset_flags(engine.name, preset),
        getattr(deployment, "launch_flags", None) or {},
    )


def launch_spec(
    deployment: Any,
    model: Any,
    engine: Any,
    engine_version: Any,
    pd_role: Optional[str] = None,
    draft_model_path: Optional[str] = None,
) -> Dict[str, Any]:
    flags = effective_flags(deployment, model, engine, engine_version)
    spec = dict(getattr(deployment, "spec_decoding", None) or {})
    if draft_model_path and spec.get("enabled"):
        spec["draft_model_path"] = draft_model_path
    traffic = getattr(deployment, "traffic_config", None) or {}
    max_conc = traffic.get("max_concurrent_per_instance")
    if not isinstance(max_conc, int) or max_conc < 1:
        max_conc = preset_concurrency(getattr(deployment, "preset", None), flags, engine.name)
        max_conc = max_conc if getattr(deployment, "preset", None) or max_conc > 1 else None
    return {
        "flags": flags,
        "env": {str(k): str(v) for k, v in (getattr(deployment, "env_vars", None) or {}).items()},
        "context_size": getattr(deployment, "context_size", None)
        or getattr(model, "default_context_size", None),
        "tool_parser": getattr(deployment, "tool_parser", None),
        "control_vectors": list(getattr(deployment, "control_vectors", None) or []),
        "tp_size": int(getattr(deployment, "tp_size", 1) or 1),
        "pp_size": int(getattr(deployment, "pp_size", 1) or 1),
        "preset": getattr(deployment, "preset", None),
        "pd_split": bool(getattr(deployment, "pd_split", False)),
        "pd_role": pd_role,
        "pd_config": dict(getattr(deployment, "pd_config", None) or {}),
        "spec_decoding": spec,
        "kv_sharing": bool(getattr(deployment, "kv_sharing", False)),
        "kv_sharing_config": dict(getattr(deployment, "kv_sharing_config", None) or {}),
        "max_concurrency": max_conc,
    }


def load_model_args(
    *,
    instance_id: uuid.UUID,
    deployment: Any,
    engine: Dict[str, Any],
    model: Dict[str, Any],
    devices: List[Dict[str, Any]],
    launch: Dict[str, Any],
    engine_process_id: Optional[uuid.UUID] = None,
    draft_model: Optional[Dict[str, Any]] = None,
    port: int = 0,
) -> Dict[str, Any]:
    health = engine.get("health") or {}
    return {
        "instance_id": str(instance_id),
        "deployment_id": str(deployment.id),
        "engine": engine,
        "model": model,
        "devices": devices,
        "port": int(port),
        "engine_process_id": str(engine_process_id) if engine_process_id else None,
        "launch": launch,
        "draft_model": draft_model,
        "health_timeout_seconds": int(health.get("startup_timeout_seconds") or 600),
    }


def start_engine_args(
    engine_process_id: uuid.UUID,
    engine: Dict[str, Any],
    devices: List[Dict[str, Any]],
    launch: Dict[str, Any],
) -> Dict[str, Any]:
    """Shared router process: engine-level flags only (per-model flags go with load_model)."""
    return {
        "engine_process_id": str(engine_process_id),
        "engine": engine,
        "devices": devices,
        "port": 0,
        "launch": {
            "flags": dict(launch.get("flags") or {}),
            "env": dict(launch.get("env") or {}),
            "context_size": launch.get("context_size"),
            "tp_size": 1,
            "pp_size": 1,
            "max_concurrency": launch.get("max_concurrency"),
        },
    }


def unload_model_args(
    instance_id: uuid.UUID,
    *,
    force: bool = False,
    drain_timeout_seconds: int = 120,
    stop_engine_if_empty: bool = False,
) -> Dict[str, Any]:
    return {
        "instance_id": str(instance_id),
        "force": bool(force),
        "drain_timeout_seconds": max(int(drain_timeout_seconds), 0),
        "kill_grace_seconds": 15,
        "stop_engine_if_empty": bool(stop_engine_if_empty),
    }
