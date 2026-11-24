"""
Device memory accounting shared by the scheduler, direct slices, the rules /
swap placement and the device views.

A GPU's memory is taken by GPU Deck (reservations: instances and slices) and,
possibly, by processes GPU Deck does not run (external servers, notebooks,
anything else on the host). The latest fresh device sample says how much is
really in use; with the agent's per-process list (NVML) the part used
outside GPU Deck is known exactly (processes not tagged with a managed
instance). Then::

    occupied = max(reserved, used - external) + external      # per-process list known
    occupied = max(reserved, used)                             # only the total is known
    occupied = reserved                                        # no fresh sample (flagged)
    free     = total - headroom - occupied

so placement never plans into memory another process is using.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from app.core.logger import get_logger

logger = get_logger(__name__)


def occupied_bytes(reserved: int, used: Optional[int], external: Optional[int]) -> int:
    """Bytes taken on a device (see module docstring)."""
    reserved = max(int(reserved or 0), 0)
    if used is None:
        return reserved
    used = max(int(used), 0)
    if external is None:
        return max(reserved, used)
    ext = min(max(int(external), 0), used)
    return max(reserved, used - ext) + ext


def free_bytes(
    total: int,
    reserved: int,
    used: Optional[int],
    external: Optional[int],
    headroom_pct: int,
) -> int:
    """Free bytes after headroom; may be negative when over-committed."""
    headroom = int(int(total) * max(int(headroom_pct), 0) / 100)
    return int(total) - headroom - occupied_bytes(reserved, used, external)


@dataclass
class DeviceUsage:
    """What the latest device sample says about one GPU."""

    used_bytes: Optional[int] = None
    external_bytes: Optional[int] = None
    fresh: bool = False
    ts: Optional[str] = None
    processes: List[Dict[str, Any]] = field(default_factory=list)


def usage_from_sample(
    sample: Optional[Dict[str, Any]], managed_instance_ids: Set[str], fresh: bool
) -> DeviceUsage:
    """``DeviceUsage`` from a live GPU sample. A stale sample yields no usage (the callers fall
    back to reservations and flag it)."""
    if not sample:
        return DeviceUsage()
    procs = [p for p in (sample.get("processes") or []) if isinstance(p, dict)]
    usage = DeviceUsage(fresh=fresh, ts=sample.get("ts"), processes=procs)
    if not fresh:
        return usage
    if sample.get("memory_used_bytes") is not None:
        usage.used_bytes = int(sample["memory_used_bytes"])
    if sample.get("processes") is not None:
        usage.external_bytes = sum(
            int(p.get("used_memory_bytes") or 0)
            for p in procs
            if str(p.get("instance_id") or "") not in managed_instance_ids
        )
    return usage


async def managed_instance_ids(session: Any) -> Set[str]:
    """Ids of live managed instances (their GPU processes are GPU Deck's)."""
    from sqlalchemy import text

    try:
        rows = await session.execute(
            text("SELECT id::text FROM instances WHERE origin = 'managed' AND state <> 'stopped'")
        )
        return {r[0] for r in rows.all()}
    except Exception as exc:  # noqa: BLE001 - accounting stays conservative without it
        logger.debug(f"managed instance ids unavailable: {exc}")
        return set()


async def device_usage(
    session: Any, device_ids: Sequence[Any], managed: Optional[Set[str]] = None
) -> Dict[str, DeviceUsage]:
    """``{device_id: DeviceUsage}`` from the live metrics cache (best effort)."""
    from app.services.deployments import live

    ids: List[Any] = list(device_ids)
    if not ids:
        return {}
    samples = await live.samples("gpu", ids)
    if managed is None and any(s and s.get("processes") for s in samples):
        managed = await managed_instance_ids(session)
    out: Dict[str, DeviceUsage] = {}
    for dev_id, sample in zip(ids, samples):
        out[str(dev_id)] = usage_from_sample(sample, managed or set(), live.is_fresh(sample))
    return out
