"""
Placement scheduler core (pure functions, no I/O).

Input: a snapshot of nodes and their devices (``NodeView`` / ``DeviceView``)
with what is already reserved on each device, and a ``PlacementRequest``
describing one deployment's GPU shape. Output: a ``PlacementPlan`` with one
``Assignment`` per replica, or the reasons nothing fits.

Rules
-----
* A node qualifies when it is ``online``, in the requested cluster, matches
  the label selector / ``node_ids`` list, and is not excluded.
* ``node_selector.device_ids`` restricts the devices a replica may use (direct
  pinning from the UI / slice API); a node with none of them is rejected.
* A device qualifies when its vendor is allowed (engine vendors ∩ deployment
  filter), it is not ``unhealthy`` and it has enough free VRAM:
  ``free = total - max(reserved, observed_used) - headroom``.
* Whole-device placements share a device by VRAM (several models on one GPU,
  as llama.cpp router mode does); slice placements also need
  ``compute_reserved + compute_pct <= 100``.
* ``gpu_count > 1`` (TP × PP) takes ``gpu_count`` devices of one vendor on
  one node; when no node has enough and the engine supports pipeline
  parallelism across nodes, the replica is split into ``pp_size`` stages of
  ``tp_size`` devices, stages on different nodes (``Assignment.shards``).
* ``spread`` prefers the emptiest devices and nodes without replicas of this
  deployment; ``pack`` prefers the fullest device that still fits and nodes
  already running it. Ties break on node name, then device index, so the
  result is deterministic.
* Each placed replica is subtracted from the snapshot before the next one is
  placed, so a multi-replica plan never double-books a device.

``plan_evictions`` answers "which lower-priority instances would have to go
for this to fit" (make-room previews); it never evicts ``always_on``.

Network distance (``PlacementRequest.latency``, filled by ``SchedulerService.plan`` from the
RTT every API layer measures and the requests it served per region):

* **Traffic-weighted RTT.** For a candidate node ``n`` with the replicas already placed or
  running ``S``, ``cost(n) = sum over regions r of share(r) * min(rtt(r, n), min over s in S
  of rtt(r, s))``: the expected network round trip of the deployment's traffic once ``n``
  serves it too (so a second replica goes where it helps the traffic the first one serves
  worst). ``rtt(r, n)`` is the best RTT of the API layers of region ``r`` to ``n``; a node
  without a measurement counts as the farthest measured one. The node loses
  ``SCHEDULER_LATENCY_WEIGHT * min(1, max(0, cost - best - tolerance) / scale)`` points,
  ``best`` being the lowest cost among the eligible nodes, so nodes within the tolerance are
  equal and the VRAM rules above decide.
* **Link cost** of pipeline stages on different nodes, for each stage against the previous
  one: other cluster 3, other zone label 2, other rack label 1, plus the RTT difference of
  the two nodes from the hub divided by the scale (a lower bound of their distance);
  ``SCHEDULER_LINK_COST_WEIGHT`` points per unit.
* No measurement, or the feature off: no term, the behaviour above is unchanged. The
  assignment's ``reasons`` say what the term did.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.services.scheduler.usage import free_bytes as usage_free_bytes

logger = get_logger(__name__)

PRIORITY_RANK = {"evictable": 0, "normal": 1, "always_on": 2}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class InstanceUse:
    """A live instance holding VRAM on a device (for eviction planning)."""

    instance_id: uuid.UUID
    deployment_id: uuid.UUID
    priority: str = "normal"
    vram_bytes: int = 0
    compute_pct: int = 0
    last_request_at: Optional[datetime] = None
    state: str = "ready"


@dataclass
class DeviceView:
    id: uuid.UUID
    node_id: uuid.UUID
    index: int
    vendor: str
    vram_total_bytes: int
    name: str = ""
    reserved_bytes: int = 0
    observed_used_bytes: Optional[int] = None
    # Part of observed_used_bytes held by processes GPU Deck does not run (external servers,
    # anything else); None = the per-process list is unknown.
    external_used_bytes: Optional[int] = None
    compute_reserved_pct: int = 0
    health: str = "healthy"
    numa_node: Optional[int] = None
    is_unified_memory: bool = False
    instances: List[InstanceUse] = field(default_factory=list)

    def free_bytes(self, headroom_pct: int) -> int:
        """total - headroom - max(reserved, GPU Deck's observed use) - memory used outside GPU
        Deck (``app.services.scheduler.usage``): never plan into another process's memory.
        """
        return usage_free_bytes(
            self.vram_total_bytes,
            self.reserved_bytes,
            self.observed_used_bytes,
            self.external_used_bytes,
            headroom_pct,
        )

    def usable(self) -> bool:
        return self.health != "unhealthy" and self.vram_total_bytes > 0


@dataclass
class NodeView:
    id: uuid.UUID
    name: str
    status: str = "online"
    cluster_id: Optional[uuid.UUID] = None
    os: Optional[str] = None
    arch: Optional[str] = None
    labels: Dict[str, Any] = field(default_factory=dict)
    devices: List[DeviceView] = field(default_factory=list)
    instance_count: int = 0


@dataclass
class LatencyContext:
    """Network input for placement (``app.services.latency.placement``)."""

    #: region -> node id -> RTT in ms from that region's API layers (best layer)
    rtt_ms: Dict[str, Dict[uuid.UUID, float]] = field(default_factory=dict)
    #: region -> share of the deployment's requests (sums to 1)
    shares: Dict[str, float] = field(default_factory=dict)
    #: where the shares come from: deployment | fleet | uniform
    source: str = "uniform"
    #: node id -> agent hub RTT in ms (link-cost estimate between nodes)
    hub_rtt_ms: Dict[uuid.UUID, float] = field(default_factory=dict)
    weight: float = 30.0
    tolerance_ms: float = 5.0
    scale_ms: float = 50.0
    link_weight: float = 10.0

    @property
    def active(self) -> bool:
        return bool(self.shares) and any(self.rtt_ms.get(r) for r in self.shares)


ZONE_LABELS = ("zone", "topology.kubernetes.io/zone", "topology.gpudeck.io/zone")
RACK_LABELS = ("rack", "topology.gpudeck.io/rack")


@dataclass
class PlacementRequest:
    need_bytes_per_device: int
    replicas: int = 1
    gpu_count: int = 1
    tp_size: int = 1
    pp_size: int = 1
    strategy: str = "spread"
    slice_vram_bytes: Optional[int] = None
    slice_compute_pct: Optional[int] = None
    cluster_id: Optional[uuid.UUID] = None
    node_selector: Dict[str, Any] = field(default_factory=dict)
    vendors: Optional[Sequence[str]] = None  # engine-supported vendors (None = any)
    vendor_filter: Sequence[str] = field(default_factory=list)  # deployment filter
    supported_os: Optional[Sequence[str]] = None
    exclude_node_ids: Sequence[uuid.UUID] = field(default_factory=list)
    existing_per_node: Dict[uuid.UUID, int] = field(default_factory=dict)
    allow_cross_node_pp: bool = False
    headroom_pct: int = 10
    max_device_combos: int = 64
    pinned_node_id: Optional[uuid.UUID] = None
    pinned_device_ids: Sequence[uuid.UUID] = field(default_factory=list)
    # Engines that pre-allocate a share of the device (vLLM gpu-memory-utilization,
    # SGLang mem-fraction-static) hold max(estimate, fraction × total) per device.
    min_device_fraction: Optional[float] = None
    # TensorRT-LLM: the fraction applies to what is left above these bytes (0 = whole device).
    fraction_base_bytes: int = 0
    # Taken off the fraction claim: a whole-GPU SSH instance tolerates the driver's idle
    # footprint (an idle H200 reports ~600 MiB used) but not another workload.
    fraction_slack_bytes: int = 0
    # Nodes ruled out up front (engine not installable there, ...) with the reason.
    blocked_nodes: Dict[uuid.UUID, str] = field(default_factory=dict)
    # Traffic-aware placement: whose traffic to weigh RTT by, and the network input.
    deployment_id: Optional[uuid.UUID] = None
    latency: Optional[LatencyContext] = None

    @property
    def per_device_bytes(self) -> int:
        """Bytes one device must have free: the slice size when slicing, else the estimate."""
        if self.slice_vram_bytes:
            return int(self.slice_vram_bytes)
        return max(int(self.need_bytes_per_device), 0)

    def need_for(self, device: "DeviceView") -> int:
        """Bytes this request takes on ``device``."""
        need = self.per_device_bytes
        if self.min_device_fraction and not self.slice_vram_bytes:
            base = max(int(self.fraction_base_bytes or 0), 0)
            claim = base + int(
                max(int(device.vram_total_bytes) - base, 0) * float(self.min_device_fraction)
            )
            need = max(need, claim - max(int(self.fraction_slack_bytes or 0), 0))
        return need

    @property
    def compute_pct(self) -> int:
        return int(self.slice_compute_pct or 100) if self.slice_vram_bytes else 0


@dataclass
class Shard:
    node_id: uuid.UUID
    device_ids: List[uuid.UUID]
    pp_rank: int


@dataclass
class Assignment:
    node_id: uuid.UUID
    device_ids: List[uuid.UUID]
    score: float
    free_after_bytes: int
    slice: Optional[Dict[str, int]] = None
    shards: List[Shard] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    @property
    def cross_node(self) -> bool:
        return len({s.node_id for s in self.shards}) > 1

    def all_device_ids(self) -> List[uuid.UUID]:
        if self.shards:
            return [d for s in self.shards for d in s.device_ids]
        return list(self.device_ids)


@dataclass
class Rejection:
    node_id: uuid.UUID
    reason: str
    device_ids: List[uuid.UUID] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "node_id": str(self.node_id),
            "device_ids": [str(d) for d in self.device_ids],
            "reason": self.reason,
        }


@dataclass
class PlacementPlan:
    feasible: bool
    assignments: List[Assignment] = field(default_factory=list)
    rejected: List[Rejection] = field(default_factory=list)
    reason: Optional[str] = None


# ---------------------------------------------------------------- filters


_RESERVED_SELECTOR_KEYS = ("node_ids", "device_ids")


def selector_device_ids(selector: Optional[Dict[str, Any]]) -> set:
    """``node_selector.device_ids`` as a set of strings (empty = no restriction)."""
    raw = (selector or {}).get("device_ids") or []
    if isinstance(raw, (str, uuid.UUID)):
        raw = [raw]
    return {str(d) for d in raw}


def _selector_matches(node: NodeView, selector: Dict[str, Any]) -> Optional[str]:
    """None when the node matches, else the reason it does not."""
    if not selector:
        return None
    node_ids = selector.get("node_ids")
    if node_ids:
        if str(node.id) not in {str(n) for n in node_ids}:
            return "not in node_selector.node_ids"
    for key, want in selector.items():
        if key in _RESERVED_SELECTOR_KEYS:
            continue
        have = (node.labels or {}).get(key)
        if isinstance(want, (list, tuple, set)):
            if str(have) not in {str(w) for w in want}:
                return f"label {key}={have!r} not in {list(want)!r}"
        elif str(have) != str(want):
            return f"label {key}={have!r} != {want!r}"
    return None


def _node_rejection(node: NodeView, req: PlacementRequest) -> Optional[str]:
    if node.status != "online":
        return f"node is {node.status}"
    if req.pinned_node_id is not None and node.id != req.pinned_node_id:
        return "not the requested node"
    if node.id in set(req.exclude_node_ids):
        return "excluded"
    if node.id in req.blocked_nodes:
        return req.blocked_nodes[node.id]
    if req.cluster_id is not None and node.cluster_id != req.cluster_id:
        return "different cluster"
    if req.supported_os and node.os and node.os not in req.supported_os:
        return f"engine does not run on {node.os}"
    return _selector_matches(node, req.node_selector)


def _allowed_vendor(vendor: str, req: PlacementRequest) -> bool:
    if req.vendors is not None and vendor not in req.vendors:
        return False
    if req.vendor_filter and vendor not in req.vendor_filter:
        return False
    return True


def _device_fits(dev: DeviceView, req: PlacementRequest) -> Optional[str]:
    """None when the device can take one shard of the request."""
    if not dev.usable():
        return "device unhealthy" if dev.health == "unhealthy" else "device has no VRAM"
    if not _allowed_vendor(dev.vendor, req):
        return f"vendor {dev.vendor} not allowed"
    need = req.need_for(dev)
    free = dev.free_bytes(req.headroom_pct)
    if req.min_device_fraction and not req.slice_vram_bytes:
        # A fraction-of-device engine ignores the headroom (the fraction is its headroom).
        free = dev.free_bytes(0)
    if need > free:
        return f"needs {need >> 20} MiB, {max(free, 0) >> 20} MiB free"
    if req.slice_vram_bytes and dev.compute_reserved_pct + req.compute_pct > 100:
        return f"compute slices full ({dev.compute_reserved_pct}% reserved)"
    return None


# ---------------------------------------------------------------- scoring


def _score(
    devices: Sequence[DeviceView], node: NodeView, req: PlacementRequest
) -> Tuple[float, int]:
    """(score, min free bytes after placing) — higher score is better."""
    free_after = [d.free_bytes(req.headroom_pct) - req.need_for(d) for d in devices]
    totals = [max(d.vram_total_bytes, 1) for d in devices]
    frac = sum(f / t for f, t in zip(free_after, totals)) / max(len(devices), 1)
    existing = req.existing_per_node.get(node.id, 0)
    if req.strategy == "pack":
        score = (1.0 - frac) * 100.0 + existing * 10.0 + node.instance_count * 1.0
    else:
        score = frac * 100.0 - existing * 25.0 - node.instance_count * 1.0
    numa = {d.numa_node for d in devices}
    if len(devices) > 1 and len(numa) == 1 and None not in numa:
        score += 2.0  # tensor-parallel peers on one NUMA node / PCIe root
    return round(score, 4), min(free_after) if free_after else 0


def _device_order(devices: Sequence[DeviceView], req: PlacementRequest) -> List[DeviceView]:
    """Spread: most free first; pack: least free first. Index breaks ties."""
    if req.strategy == "pack":
        return sorted(devices, key=lambda d: (d.free_bytes(req.headroom_pct), d.index))
    return sorted(devices, key=lambda d: (-d.free_bytes(req.headroom_pct), d.index))


def _device_groups(fitting: Sequence[DeviceView]) -> List[List[DeviceView]]:
    """Candidate device pools: same vendor + NUMA node first, then same vendor."""
    groups: List[List[DeviceView]] = []
    by_vendor: Dict[str, List[DeviceView]] = {}
    for d in fitting:
        by_vendor.setdefault(d.vendor, []).append(d)
    for vendor in sorted(by_vendor):
        pool = by_vendor[vendor]
        by_numa: Dict[Any, List[DeviceView]] = {}
        for d in pool:
            by_numa.setdefault(d.numa_node, []).append(d)
        if len(by_numa) > 1:
            for numa in sorted(by_numa, key=lambda k: (k is None, k if k is not None else 0)):
                groups.append(by_numa[numa])
        groups.append(pool)
    return groups


def _node_candidates(
    node: NodeView, req: PlacementRequest, count: int
) -> Tuple[List[Tuple[float, int, List[DeviceView]]], List[Rejection]]:
    """Every way (bounded) to take ``count`` devices on ``node``."""
    rejections: List[Rejection] = []
    devices = node.devices
    if req.pinned_device_ids:
        pinned = {str(d) for d in req.pinned_device_ids}
        devices = [d for d in devices if str(d.id) in pinned]
    allowed = selector_device_ids(req.node_selector)
    if allowed:
        devices = [d for d in devices if str(d.id) in allowed]
        if not devices:
            return [], [Rejection(node.id, "no device in node_selector.device_ids")]
    fitting: List[DeviceView] = []
    for dev in devices:
        why = _device_fits(dev, req)
        if why is None:
            fitting.append(dev)
        else:
            rejections.append(Rejection(node.id, why, [dev.id]))
    if req.pinned_device_ids and len(fitting) < len(req.pinned_device_ids):
        return [], rejections
    if len(fitting) < count:
        if not rejections:
            rejections.append(
                Rejection(node.id, f"needs {count} device(s), node has {len(devices)}")
            )
        return [], rejections
    candidates: List[Tuple[float, int, List[DeviceView]]] = []
    seen = set()
    for group in _device_groups(fitting):
        if len(group) < count:
            continue
        ordered = _device_order(group, req)
        for start in range(0, len(ordered) - count + 1):
            picked = ordered[start : start + count]
            key = tuple(sorted(str(d.id) for d in picked))
            if key in seen:
                continue
            seen.add(key)
            score, free_after = _score(picked, node, req)
            candidates.append((score, free_after, picked))
            if len(candidates) >= max(req.max_device_combos, 1):
                break
        if len(candidates) >= max(req.max_device_combos, 1):
            break
    return candidates, rejections


def _apply(devices: Iterable[DeviceView], node: NodeView, req: PlacementRequest) -> None:
    for d in devices:
        need = req.need_for(d)
        d.reserved_bytes += need
        if d.observed_used_bytes is not None:
            d.observed_used_bytes += need
        if req.slice_vram_bytes:
            d.compute_reserved_pct += req.compute_pct
    node.instance_count += 1
    req.existing_per_node[node.id] = req.existing_per_node.get(node.id, 0) + 1


def _slice_of(req: PlacementRequest) -> Optional[Dict[str, int]]:
    if not req.slice_vram_bytes:
        return None
    return {"vram_bytes": int(req.slice_vram_bytes), "compute_pct": req.compute_pct}


def _place_one(
    nodes: List[NodeView], req: PlacementRequest
) -> Tuple[Optional[Assignment], List[Rejection]]:
    count = max(req.gpu_count, 1)
    best: Optional[Tuple[float, int, str, List[DeviceView], NodeView]] = None
    rejections: List[Rejection] = []
    eligible = [n for n in nodes if _node_rejection(n, req) is None]
    network = latency_terms(eligible, req)
    for node in nodes:
        why = _node_rejection(node, req)
        if why is not None:
            rejections.append(Rejection(node.id, why))
            continue
        cands, rej = _node_candidates(node, req, count)
        rejections.extend(rej)
        penalty = network[node.id].penalty if node.id in network else 0.0
        for score, free_after, picked in cands:
            key = (round(score - penalty, 4), free_after, node.name)
            if best is None or _better(key, best[:3], picked, best[3]):
                best = (key[0], free_after, node.name, picked, node)
    if best is not None:
        score, free_after, _, picked, node = best
        _apply(picked, node, req)
        term = network.get(node.id)
        return (
            Assignment(
                node_id=node.id,
                device_ids=[d.id for d in picked],
                score=score,
                free_after_bytes=free_after,
                slice=_slice_of(req),
                reasons=[term.reason] if term is not None and term.reason else [],
            ),
            rejections,
        )
    if req.allow_cross_node_pp and req.pp_size > 1 and req.pinned_node_id is None:
        assignment = _place_cross_node(nodes, req)
        if assignment is not None:
            return assignment, rejections
    return None, rejections


# ---------------------------------------------------------------- network terms


@dataclass
class NetworkTerm:
    cost_ms: Optional[float]
    penalty: float
    reason: str


def _fmt_ms(value: float) -> str:
    return f"{value:.0f} ms" if value >= 10 else f"{value:.1f} ms"


def _region_mix(lat: LatencyContext) -> str:
    parts = sorted(lat.shares.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = ", ".join(f"{r} {share * 100:.0f}%" for r, share in parts[:3])
    origin = {
        "deployment": "this deployment's traffic",
        "fleet": "all traffic (no requests for this deployment yet)",
        "uniform": "every API-layer region equally (no traffic yet)",
    }.get(lat.source, lat.source)
    return f"{shown}; weighted by {origin}"


def latency_terms(
    eligible: Sequence[NodeView], req: PlacementRequest
) -> Dict[uuid.UUID, NetworkTerm]:
    """Per eligible node: traffic-weighted RTT cost, score penalty and the reason text."""
    lat = req.latency
    if lat is None or not lat.active or not eligible:
        return {}
    regions = {r: s for r, s in lat.shares.items() if s > 0 and lat.rtt_ms.get(r)}
    total = sum(regions.values())
    if total <= 0:
        return {}
    shares = {r: s / total for r, s in regions.items()}
    placed = [n for n, c in req.existing_per_node.items() if c > 0]

    def rtt(region: str, node_id: uuid.UUID) -> float:
        known = lat.rtt_ms[region]
        if node_id in known:
            return known[node_id]
        return max(known.values())

    measured = {n.id for n in eligible if any(n.id in lat.rtt_ms[r] for r in shares)}
    if not measured:
        return {}
    costs: Dict[uuid.UUID, float] = {}
    for node in eligible:
        cost = 0.0
        for region, share in shares.items():
            served = min((rtt(region, s) for s in placed), default=None)
            here = rtt(region, node.id)
            cost += share * (here if served is None else min(here, served))
        costs[node.id] = cost
    best = min(costs.values())
    mix = _region_mix(LatencyContext(shares=shares, source=lat.source))
    out: Dict[uuid.UUID, NetworkTerm] = {}
    for node in eligible:
        cost = costs[node.id]
        excess = max(0.0, cost - best - max(lat.tolerance_ms, 0.0))
        penalty = lat.weight * min(1.0, excess / max(lat.scale_ms, 1e-6)) if excess else 0.0
        unmeasured = "" if node.id in measured else " (no RTT measured: counted as the farthest)"
        if penalty <= 0:
            reason = (
                f"network: {_fmt_ms(cost)} expected round trip to the API layers ({mix}); "
                f"within {_fmt_ms(lat.tolerance_ms)} of the nearest candidate{unmeasured}"
            )
        else:
            reason = (
                f"network: {_fmt_ms(cost)} expected round trip to the API layers ({mix}), "
                f"{_fmt_ms(cost - best)} more than the nearest candidate: "
                f"-{penalty:.1f} points{unmeasured}"
            )
        out[node.id] = NetworkTerm(cost_ms=round(cost, 3), penalty=round(penalty, 4), reason=reason)
    return out


def _label(node: NodeView, keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = (node.labels or {}).get(key)
        if value not in (None, ""):
            return str(value)
    return None


def link_cost(a: NodeView, b: NodeView, lat: Optional[LatencyContext]) -> Tuple[float, str]:
    """Inter-node link cost of two pipeline stages and why (0 = same node)."""
    if a.id == b.id:
        return 0.0, "same node"
    cost = 0.0
    why: List[str] = []
    if a.cluster_id is not None and b.cluster_id is not None and a.cluster_id != b.cluster_id:
        cost += 3.0
        why.append("other cluster")
    za, zb = _label(a, ZONE_LABELS), _label(b, ZONE_LABELS)
    if za is not None and zb is not None:
        if za != zb:
            cost += 2.0
            why.append(f"zone {za} vs {zb}")
        else:
            why.append(f"same zone {za}")
    ra, rb = _label(a, RACK_LABELS), _label(b, RACK_LABELS)
    if ra is not None and rb is not None and ra != rb:
        cost += 1.0
        why.append(f"rack {ra} vs {rb}")
    if lat is not None and a.id in lat.hub_rtt_ms and b.id in lat.hub_rtt_ms:
        apart = abs(lat.hub_rtt_ms[a.id] - lat.hub_rtt_ms[b.id])
        extra = min(3.0, apart / max(lat.scale_ms, 1e-6))
        if extra > 0.01:
            cost += extra
            why.append(f"at least {_fmt_ms(apart)} apart (hub RTT)")
    return round(cost, 4), ", ".join(why) or "same cluster, no topology labels"


def _better(
    key: Tuple[float, int, str],
    best_key: Tuple[float, int, str],
    picked: Sequence[DeviceView],
    best_picked: Sequence[DeviceView],
) -> bool:
    """Higher score wins; then name, then lowest device indices (determinism)."""
    if key[0] != best_key[0]:
        return key[0] > best_key[0]
    if key[2] != best_key[2]:
        return key[2] < best_key[2]
    return [d.index for d in picked] < [d.index for d in best_picked]


def _place_cross_node(nodes: List[NodeView], req: PlacementRequest) -> Optional[Assignment]:
    """Split a replica into pp_size stages of tp_size devices, stages on different nodes."""
    stage_req = copy.copy(req)
    stage_req.gpu_count = max(req.tp_size, 1)
    stage_req.existing_per_node = dict(req.existing_per_node)
    shards: List[Shard] = []
    applied: List[Tuple[List[DeviceView], NodeView]] = []
    scores: List[float] = []
    frees: List[int] = []
    reasons: List[str] = ["pipeline stages span nodes"]
    lat = req.latency
    link_weight = lat.link_weight if lat is not None else 10.0
    for rank in range(req.pp_size):
        best = None
        eligible = [n for n in nodes if _node_rejection(n, stage_req) is None]
        # The head stage takes the traffic: the network term applies to it; later stages
        # pay the link cost to the previous stage.
        network = latency_terms(eligible, stage_req) if rank == 0 else {}
        prev = applied[-1][1] if applied else None
        links: Dict[uuid.UUID, Tuple[float, str]] = {}
        for node in eligible:
            if prev is not None:
                links[node.id] = link_cost(prev, node, lat)
            penalty = network[node.id].penalty if node.id in network else 0.0
            if prev is not None:
                penalty += link_weight * links[node.id][0]
            cands, _ = _node_candidates(node, stage_req, stage_req.gpu_count)
            for score, free_after, picked in cands:
                key = (round(score - penalty, 4), free_after, node.name)
                if best is None or _better(key, best[:3], picked, best[3]):
                    best = (key[0], free_after, node.name, picked, node)
        if best is None:
            # roll back the stages placed so far
            for picked, node in applied:
                for d in picked:
                    d.reserved_bytes -= stage_req.need_for(d)
                    if d.observed_used_bytes is not None:
                        d.observed_used_bytes -= stage_req.need_for(d)
                    if stage_req.slice_vram_bytes:
                        d.compute_reserved_pct -= stage_req.compute_pct
                node.instance_count -= 1
            return None
        score, free_after, _, picked, node = best
        if rank == 0 and node.id in network and network[node.id].reason:
            reasons.append(network[node.id].reason)
        if prev is not None and node.id in links:
            cost, why = links[node.id]
            reasons.append(
                f"stage {rank + 1} on {node.name}: link cost {cost:g} to stage {rank} ({why})"
            )
        for d in picked:
            d.reserved_bytes += stage_req.need_for(d)
            if d.observed_used_bytes is not None:
                d.observed_used_bytes += stage_req.need_for(d)
            if stage_req.slice_vram_bytes:
                d.compute_reserved_pct += stage_req.compute_pct
        node.instance_count += 1
        applied.append((picked, node))
        shards.append(Shard(node.id, [d.id for d in picked], rank))
        scores.append(score)
        frees.append(free_after)
    head = shards[0]
    req.existing_per_node[head.node_id] = req.existing_per_node.get(head.node_id, 0) + 1
    return Assignment(
        node_id=head.node_id,
        device_ids=list(head.device_ids),
        score=round(sum(scores) / len(scores), 4) - 5.0,
        free_after_bytes=min(frees),
        slice=_slice_of(req),
        shards=shards,
        reasons=reasons,
    )


# ---------------------------------------------------------------- public API


def place(nodes: Sequence[NodeView], req: PlacementRequest) -> PlacementPlan:
    """Plan ``req.replicas`` replicas. Pure: the input snapshot is not modified."""
    work = copy.deepcopy(list(nodes))
    work.sort(key=lambda n: (n.name, str(n.id)))
    req = copy.copy(req)
    req.existing_per_node = dict(req.existing_per_node)
    if req.gpu_count != max(req.tp_size, 1) * max(req.pp_size, 1):
        return PlacementPlan(
            False,
            reason=f"gpu_count {req.gpu_count} must equal tp_size × pp_size "
            f"({req.tp_size}×{req.pp_size})",
        )
    if req.slice_vram_bytes and req.gpu_count > 1:
        return PlacementPlan(False, reason="GPU slices are single-device; set gpu_count=1")
    if not work:
        return PlacementPlan(False, reason="no nodes")
    plan = PlacementPlan(True)
    for i in range(max(req.replicas, 0)):
        assignment, rejections = _place_one(work, req)
        if assignment is None:
            plan.feasible = False
            plan.rejected = _dedupe(rejections)
            plan.reason = f"replica {i + 1} of {req.replicas} does not fit: " + (
                _summarize(plan.rejected) or "no eligible device"
            )
            return plan
        plan.assignments.append(assignment)
    return plan


def plan_evictions(
    nodes: Sequence[NodeView], req: PlacementRequest, requester_priority: str = "normal"
) -> List[InstanceUse]:
    """
    Smallest set of strictly lower-priority instances whose removal makes one
    replica fit, preferring evictable before normal and the longest idle
    first. Empty when nothing needs to go or nothing would help.
    """
    single = copy.copy(req)
    single.replicas = 1
    if place(nodes, single).feasible:
        return []
    rank = PRIORITY_RANK.get(requester_priority, 1)
    best: Optional[List[InstanceUse]] = None
    for node in sorted(nodes, key=lambda n: (n.name, str(n.id))):
        if _node_rejection(node, single) is not None:
            continue
        victims_pool: List[Tuple[InstanceUse, DeviceView]] = []
        for dev in node.devices:
            for inst in dev.instances:
                if PRIORITY_RANK.get(inst.priority, 1) < rank and inst.priority != "always_on":
                    victims_pool.append((inst, dev))
        victims_pool.sort(
            key=lambda p: (
                PRIORITY_RANK.get(p[0].priority, 1),
                p[0].last_request_at or _EPOCH,
                -p[0].vram_bytes,
                str(p[0].instance_id),
            )
        )
        trial = copy.deepcopy(node)
        chosen: List[InstanceUse] = []
        chosen_ids = set()
        for inst, _ in victims_pool:
            if inst.instance_id in chosen_ids:
                continue
            chosen.append(inst)
            chosen_ids.add(inst.instance_id)
            for dev in trial.devices:
                for held in dev.instances:
                    if held.instance_id == inst.instance_id:
                        dev.reserved_bytes = max(dev.reserved_bytes - held.vram_bytes, 0)
                        if dev.observed_used_bytes is not None:
                            dev.observed_used_bytes = max(
                                dev.observed_used_bytes - held.vram_bytes, 0
                            )
                        dev.compute_reserved_pct = max(
                            dev.compute_reserved_pct - held.compute_pct, 0
                        )
            pinned = copy.copy(single)
            pinned.pinned_node_id = trial.id
            if place([trial], pinned).feasible:
                if best is None or (len(chosen), _bytes(chosen)) < (
                    len(best),
                    _bytes(best),
                ):
                    best = list(chosen)
                break
    return best or []


def _bytes(items: Sequence[InstanceUse]) -> int:
    return sum(i.vram_bytes for i in items)


def _dedupe(rejections: List[Rejection]) -> List[Rejection]:
    seen = set()
    out = []
    for r in rejections:
        key = (r.node_id, tuple(r.device_ids), r.reason)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _summarize(rejections: Sequence[Rejection]) -> str:
    reasons: Dict[str, int] = {}
    for r in rejections:
        reasons[r.reason] = reasons.get(r.reason, 0) + 1
    top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return "; ".join(f"{reason} ({n})" for reason, n in top)
