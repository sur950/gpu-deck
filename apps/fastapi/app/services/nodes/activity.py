"""
What a GPU / node is really doing, from live data rather than GPU Deck's own
instances only: a GPU that runs models outside GPU Deck (observed servers or
any other process) is busy, not idle; a node whose agent is gone is detached.

``activity`` values (devices and nodes):

* ``detached``  the node is offline / removed or its heartbeat is stale (red in the UI)
* ``stale``     the node is connected but the device has no fresh sample
* ``gpu_deck``  GPU Deck instances run here and nothing else holds memory
* ``mixed``     GPU Deck instances and processes outside GPU Deck
* ``external``  only processes outside GPU Deck (or utilization / memory with no GPU Deck
                instance), e.g. "Running (external) · 3 processes"
* ``idle``      no GPU Deck instance, no outside process, low utilization

Old agents (no per-process list) degrade to utilization / memory beyond reservations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

# Utilization above this counts as busy.
BUSY_UTIL_PCT = 5.0
# Memory in use beyond GPU Deck's reservations that still counts as idle (driver / display).
IDLE_BASELINE_BYTES = 1024**3
DETACHED_STATUSES = {"offline", "removed"}


def _ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def node_detached(node: Any, now: Optional[datetime] = None) -> bool:
    """Offline / removed, or no heartbeat for longer than the offline threshold (the hub has
    not flipped it yet, e.g. the tunnel just dropped). Pending nodes never joined: not
    detached."""
    if node is None:
        return False
    status = str(getattr(node, "status", "") or "")
    if status in DETACHED_STATUSES:
        return True
    if status == "pending":
        return False
    beat = _ts(getattr(node, "last_heartbeat_at", None))
    if beat is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - beat).total_seconds() > max(settings.NODE_OFFLINE_AFTER_SECONDS, 1) * 2


def outside_processes(processes: Iterable[Dict[str, Any]], managed: Set[str]) -> int:
    return sum(1 for p in processes if str(p.get("instance_id") or "") not in managed)


def device_activity(
    *,
    detached: bool,
    fresh: bool,
    managed_instances: int,
    sample: Optional[Dict[str, Any]],
    managed: Set[str],
    reserved_bytes: int,
) -> Tuple[str, int]:
    """``(activity, processes outside GPU Deck)`` for one device."""
    procs = [p for p in ((sample or {}).get("processes") or []) if isinstance(p, dict)]
    outside = outside_processes(procs, managed)
    if detached:
        return "detached", outside
    if not fresh or not sample:
        return "stale", outside
    deck = managed_instances > 0 or any(str(p.get("instance_id") or "") in managed for p in procs)
    util = float(sample.get("utilization_pct") or 0)
    used = sample.get("memory_used_bytes")
    busy_outside = outside > 0
    if not busy_outside and sample.get("processes") is None and not deck:
        # Old agent: only totals. Utilization or memory beyond reservations means someone runs.
        busy_outside = util > BUSY_UTIL_PCT or (
            used is not None and int(used) > int(reserved_bytes) + IDLE_BASELINE_BYTES
        )
    if deck and busy_outside:
        return "mixed", outside
    if deck:
        return "gpu_deck", outside
    if busy_outside or util > BUSY_UTIL_PCT:
        return "external", outside
    return "idle", outside


def node_activity(device_activities: Iterable[str], detached: bool) -> str:
    """Roll device activities up to the node (most significant wins)."""
    if detached:
        return "detached"
    seen = set(device_activities)
    for kind in ("mixed", "external", "gpu_deck", "stale"):
        if kind in seen:
            if kind == "external" and "gpu_deck" in seen:
                return "mixed"
            return kind
    return "idle"


async def nodes_live_activity(session: Any, nodes: Iterable[Any]) -> Dict[Any, Dict[str, Any]]:
    """``{node_id: {"activity", "outside_processes", "detached"}}`` from the live GPU samples.
    Best effort: without Redis every node falls back to its status."""
    from app.services.metrics import live_cache
    from app.services.scheduler.usage import managed_instance_ids

    nodes = list(nodes)
    try:
        samples = await live_cache.get_all("gpu")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"live gpu samples unavailable: {exc}")
        samples = {}
    managed = await managed_instance_ids(session) if samples else set()
    managed_per_node: Dict[str, int] = {}
    try:
        from sqlalchemy import text

        rows = await session.execute(
            text(
                "SELECT node_id::text, COUNT(*) FROM instances WHERE origin = 'managed' "
                "AND state <> 'stopped' GROUP BY node_id"
            )
        )
        managed_per_node = {r[0]: int(r[1]) for r in rows.all()}
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"managed instance counts unavailable: {exc}")
    by_node: Dict[str, list] = {}
    for sample in samples.values():
        if isinstance(sample, dict) and sample.get("node_id"):
            by_node.setdefault(str(sample["node_id"]), []).append(sample)
    out: Dict[Any, Dict[str, Any]] = {}
    for node in nodes:
        detached = node_detached(node)
        acts = []
        outside = 0
        for s in by_node.get(str(node.id), []):
            act, n = device_activity(
                detached=detached,
                fresh=live_cache.is_fresh(s),
                managed_instances=managed_per_node.get(str(node.id), 0),
                sample=s,
                managed=managed,
                reserved_bytes=0,
            )
            acts.append(act)
            outside += n
        out[node.id] = {
            "activity": node_activity(acts, detached),
            "outside_processes": outside,
            "detached": detached,
        }
    return out
