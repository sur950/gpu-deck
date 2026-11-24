"""
Minimal placement planner used by the rules engine (grow / move / make-room),
the layout differ and manual swaps when the scheduler service is not
available. Pure function over ``DeviceSnapshot``s so it is unit-testable.

Semantics (PlacementPreview in admin.yaml):
- need bytes = deployment slice VRAM, else an estimate from the model size;
- a device is usable when its node is online, it is not unhealthy and
  free VRAM (total - max(reserved, observed) - headroom) covers the need;
- ``spread`` prefers the device with the most free VRAM, ``pack`` the one
  with the least that still fits; ``gpu_count > 1`` needs that many devices
  on one node (tensor / pipeline parallel).

The scheduler service may replace this through ``PlacementPort`` in app/services/swap/ports.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.core.logger import get_logger
from app.services.rules.inputs import DeploymentSnapshot, DeviceSnapshot

logger = get_logger(__name__)

WEIGHT_OVERHEAD = 1.15
KV_ESTIMATE_BYTES = 512 * 1024 * 1024
DEFAULT_HEADROOM_PCT = 10


@dataclass
class PlacementCandidate:
    node_id: uuid.UUID
    device_ids: List[uuid.UUID]
    free_bytes: int
    slice: Optional[Dict[str, Any]] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "node_id": str(self.node_id),
            "device_ids": [str(d) for d in self.device_ids],
            "free_bytes": self.free_bytes,
            "slice": self.slice,
        }


@dataclass
class PlacementResult:
    feasible: bool
    placements: List[PlacementCandidate] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    reason: Optional[str] = None
    need_bytes: int = 0
    would_evict: List[uuid.UUID] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "feasible": self.feasible,
            "placements": [p.to_json() for p in self.placements],
            "rejected": self.rejected,
            "reason": self.reason,
            "need_bytes": self.need_bytes,
            "would_evict": [str(i) for i in self.would_evict],
        }


def estimate_vram_need(deployment: DeploymentSnapshot, slice_bytes: Optional[int] = None) -> int:
    """Bytes one replica needs on each device."""
    if slice_bytes:
        return int(slice_bytes)
    if deployment.slice_vram_bytes:
        return int(deployment.slice_vram_bytes)
    size = int(deployment.model_size_bytes or 0)
    if size <= 0:
        return 0  # unknown size: any device with free VRAM qualifies
    per_device = size / max(1, deployment.gpu_count)
    return int(per_device * WEIGHT_OVERHEAD + KV_ESTIMATE_BYTES)


def _headroom(device: DeviceSnapshot, headroom_pct: int) -> int:
    return int(device.vram_total_bytes * headroom_pct / 100)


def plan_placement(
    deployment: DeploymentSnapshot,
    devices: Sequence[DeviceSnapshot],
    count: int = 1,
    exclude_device_ids: Iterable[uuid.UUID] = (),
    strategy: Optional[str] = None,
    min_free_vram_bytes: Optional[int] = None,
    node_id: Optional[uuid.UUID] = None,
    device_ids: Optional[Sequence[uuid.UUID]] = None,
    slice_bytes: Optional[int] = None,
    headroom_pct: int = DEFAULT_HEADROOM_PCT,
    cluster_id: Optional[uuid.UUID] = None,
) -> PlacementResult:
    """Choose ``count`` placements for ``deployment``. Each placement = one replica."""
    strategy = strategy or deployment.placement_strategy or "spread"
    need = (
        int(min_free_vram_bytes)
        if min_free_vram_bytes
        else estimate_vram_need(deployment, slice_bytes)
    )
    excluded = {str(d) for d in exclude_device_ids}
    pinned = [str(d) for d in (device_ids or [])]
    gpu_count = max(1, int(deployment.gpu_count or 1))
    rejected: List[Dict[str, Any]] = []
    remaining: Dict[str, int] = {}
    usable: List[DeviceSnapshot] = []
    wanted_cluster = cluster_id or deployment.cluster_id

    selector = dict(getattr(deployment, "node_selector", None) or {})
    for dev in devices:
        reason = None
        if node_id is not None and str(dev.node_id) != str(node_id):
            continue
        if pinned and str(dev.id) not in pinned:
            continue
        if not selector_allows(dev, selector):
            continue  # the scheduler would never place the deployment there
        if (
            wanted_cluster is not None
            and dev.cluster_id is not None
            and str(dev.cluster_id) != str(wanted_cluster)
        ):
            continue
        if str(dev.id) in excluded:
            reason = "excluded"
        elif not dev.usable:
            reason = f"node {dev.node_status} / device {dev.health}"
        else:
            free = dev.free_bytes - _headroom(dev, headroom_pct)
            if need and free < need:
                reason = f"insufficient_vram: free {free} < need {need}"
            else:
                remaining[str(dev.id)] = free
                usable.append(dev)
        if reason:
            rejected.append(
                {
                    "node_id": str(dev.node_id),
                    "device_ids": [str(dev.id)],
                    "reason": reason,
                }
            )

    placements: List[PlacementCandidate] = []
    for _ in range(max(1, count)):
        candidate = _pick(usable, remaining, need, gpu_count, strategy)
        if candidate is None:
            break
        placements.append(candidate)
        for dev_id in candidate.device_ids:
            remaining[str(dev_id)] -= need
            if need == 0:
                # unknown need: do not reuse the same device twice in one plan
                remaining[str(dev_id)] = -1

    slice_spec = None
    if slice_bytes or deployment.slice_vram_bytes:
        slice_spec = {
            "vram_bytes": int(slice_bytes or deployment.slice_vram_bytes),
            "compute_pct": int(deployment.slice_compute_pct or 100),
        }
    for placement in placements:
        placement.slice = slice_spec

    feasible = len(placements) >= max(1, count)
    reason = None if feasible else ("no_capacity" if usable or rejected else "no_devices")
    if not feasible and any("insufficient_vram" in r["reason"] for r in rejected):
        reason = "insufficient_vram"
    return PlacementResult(
        feasible=feasible,
        placements=placements,
        rejected=rejected,
        reason=reason,
        need_bytes=need,
    )


def selector_allows(dev: DeviceSnapshot, selector: Dict[str, Any]) -> bool:
    """Same rules as the scheduler's ``node_selector`` (node_ids, device_ids, node labels)."""
    if not selector:
        return True
    node_ids = selector.get("node_ids")
    if node_ids and str(dev.node_id) not in {str(n) for n in node_ids}:
        return False
    device_ids = selector.get("device_ids")
    if isinstance(device_ids, (str, uuid.UUID)):
        device_ids = [device_ids]
    if device_ids and str(dev.id) not in {str(d) for d in device_ids}:
        return False
    labels = dev.node_labels or {}
    for key, want in selector.items():
        if key in ("node_ids", "device_ids"):
            continue
        have = labels.get(key)
        if isinstance(want, (list, tuple, set)):
            if str(have) not in {str(w) for w in want}:
                return False
        elif str(have) != str(want):
            return False
    return True


def _pick(
    usable: List[DeviceSnapshot],
    remaining: Dict[str, int],
    need: int,
    gpu_count: int,
    strategy: str,
) -> Optional[PlacementCandidate]:
    by_node: Dict[str, List[DeviceSnapshot]] = {}
    for dev in usable:
        if remaining[str(dev.id)] >= need and remaining[str(dev.id)] >= 0:
            by_node.setdefault(str(dev.node_id), []).append(dev)
    best: Optional[PlacementCandidate] = None
    best_key = None
    for node_devs in by_node.values():
        if len(node_devs) < gpu_count:
            continue
        ordered = sorted(
            node_devs, key=lambda d: remaining[str(d.id)], reverse=(strategy != "pack")
        )
        chosen = ordered[:gpu_count]
        free = min(remaining[str(d.id)] for d in chosen)
        key = free if strategy != "pack" else -free
        if best is None or key > best_key:
            best = PlacementCandidate(
                node_id=chosen[0].node_id,
                device_ids=[d.id for d in chosen],
                free_bytes=free,
            )
            best_key = key
    return best


def device_free_after_evictions(
    device: DeviceSnapshot, freed_bytes: int, headroom_pct: int = DEFAULT_HEADROOM_PCT
) -> int:
    return device.free_bytes + freed_bytes - _headroom(device, headroom_pct)
