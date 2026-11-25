"""
Placement input from the measured network: ``LatencyContext`` for the scheduler core.

- RTT per API-layer region to every node: the best (lowest) smoothed RTT among the region's
  API layers (``gpu-deck:latency:api:*``).
- Traffic shares per region: the deployment's requests over the last
  ``SCHEDULER_TRAFFIC_WINDOW_HOURS`` (counted by every API layer), else the whole fleet's,
  else every region with RTT data equally.
- The hub's RTT per node, for the link-cost estimate between pipeline stages.

Returns None when the feature is off; a context without measurements adds no RTT term (the
link cost then uses the topology labels only). Never raises.
"""

from __future__ import annotations

import uuid
from typing import Dict, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.latency import store
from app.services.scheduler.core import LatencyContext

logger = get_logger(__name__)


def _uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def build_context(
    live: Dict, traffic: Dict[str, int], fleet_traffic: Dict[str, int]
) -> LatencyContext:
    """Pure part of ``load_context``; inactive (no RTT term) when nothing was measured."""
    layers = live.get("api_layers") or {}
    rtt: Dict[str, Dict[uuid.UUID, float]] = {}
    for layer_id, nodes in (live.get("api") or {}).items():
        region = str((layers.get(layer_id) or {}).get("region") or "default")
        per_region = rtt.setdefault(region, {})
        for node_id, entry in (nodes or {}).items():
            nid = _uuid(node_id)
            value = (entry or {}).get("rtt_ms")
            if nid is None or value is None:
                continue
            value = float(value)
            if nid not in per_region or value < per_region[nid]:
                per_region[nid] = value
    rtt = {r: nodes for r, nodes in rtt.items() if nodes}
    source = "deployment"
    counts = {r: c for r, c in (traffic or {}).items() if c > 0 and r in rtt}
    if not counts:
        source = "fleet"
        counts = {r: c for r, c in (fleet_traffic or {}).items() if c > 0 and r in rtt}
    if not counts:
        source = "uniform"
        counts = {r: 1 for r in rtt}
    total = float(sum(counts.values())) or 1.0
    hub: Dict[uuid.UUID, float] = {}
    for node_id, entry in (live.get("hub") or {}).items():
        nid = _uuid(node_id)
        if nid is not None and (entry or {}).get("rtt_ms") is not None:
            hub[nid] = float(entry["rtt_ms"])
    return LatencyContext(
        rtt_ms=rtt,
        shares={r: c / total for r, c in counts.items()},
        source=source,
        hub_rtt_ms=hub,
        weight=float(settings.SCHEDULER_LATENCY_WEIGHT),
        tolerance_ms=float(settings.SCHEDULER_LATENCY_TOLERANCE_MS),
        scale_ms=float(settings.SCHEDULER_LATENCY_SCALE_MS),
        link_weight=float(settings.SCHEDULER_LINK_COST_WEIGHT),
    )


async def load_context(deployment_id: Optional[uuid.UUID]) -> Optional[LatencyContext]:
    if not settings.SCHEDULER_LATENCY_AWARE:
        return None
    try:
        live = await store.read_live()
        traffic = await store.read_traffic(str(deployment_id)) if deployment_id else {}
        fleet = await store.read_traffic(None)
        return build_context(live, traffic, fleet)
    except Exception as exc:  # noqa: BLE001 - placement must not depend on Redis
        logger.debug(f"placement latency context unavailable: {exc}")
        return None
