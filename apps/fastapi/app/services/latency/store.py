"""
Redis view of the measured network distances (every key under ``gpu-deck:latency:``).

Written by the processes that measure, read by the admin API (live view), the jobs process
(per-minute history) and placement:

    HSET gpu-deck:latency:api:<api layer id>  <node id>  {rtt_ms, jitter_ms, ..., mode, ts}
    HSET gpu-deck:latency:api-layers          <api layer id>  {id, region, ts, probe_seconds}
    HSET gpu-deck:latency:hub                 <node id>  {rtt_ms, jitter_ms, ..., ts}
    HINCRBY gpu-deck:latency:traffic:<deployment id | _all>  <region>|<YYYYMMDDHH>  <requests>

Entries carry their own ``ts`` (unix seconds); readers drop the stale ones, so a stopped API
layer or a disconnected agent disappears without a cleanup job. Every write is best effort.
"""

from __future__ import annotations

import json
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

ALL_DEPLOYMENTS = "_all"
HUB_STALE_FACTOR = 4.0


def _key(*parts: str) -> str:
    from app.db.redis.client import redis_key

    return redis_key("latency", *parts)


def api_layer_identity() -> Tuple[str, str]:
    """``(id, region)`` of this API layer (``API_LAYER_ID`` or the host name)."""
    ident = (settings.API_LAYER_ID or "").strip() or socket.gethostname() or "api-layer"
    region = (settings.API_LAYER_REGION or "").strip() or "default"
    return ident[:120], region[:64]


def api_stale_seconds() -> float:
    probe = float(settings.SERVING_RTT_PROBE_SECONDS or 0)
    return max(60.0, probe * 6.0)


def hub_stale_seconds() -> float:
    return max(30.0, float(settings.HUB_PING_INTERVAL_SECONDS) * HUB_STALE_FACTOR)


def hour_bucket(ts: Optional[datetime] = None) -> str:
    return (ts or datetime.now(timezone.utc)).strftime("%Y%m%d%H")


def _loads(raw: Any) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _fresh(entry: Optional[Dict[str, Any]], max_age: float, now: float) -> bool:
    if not entry:
        return False
    try:
        return now - float(entry.get("ts") or 0) <= max_age
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


async def publish_api_layer(snapshots: Dict[str, Dict[str, Any]], traffic: Dict[str, int]) -> None:
    """This API layer's RTT to every node, its liveness and the request counts since the
    last call (per deployment, for this layer's region)."""
    from app.db.redis.client import get_redis

    ident, region = api_layer_identity()
    now = time.time()
    ttl = int(api_stale_seconds() * 2)
    window_ttl = int((int(settings.SCHEDULER_TRAFFIC_WINDOW_HOURS) + 2) * 3600)
    try:
        client = await get_redis()
        pipe = client.pipeline(transaction=False)
        layer = {
            "id": ident,
            "region": region,
            "ts": now,
            "probe_seconds": float(settings.SERVING_RTT_PROBE_SECONDS),
            "nodes": len(snapshots),
        }
        pipe.hset(_key("api-layers"), ident, json.dumps(layer))
        pipe.expire(_key("api-layers"), ttl * 10)
        if snapshots:
            key = _key("api", ident)
            pipe.hset(key, mapping={n: json.dumps(s) for n, s in snapshots.items()})
            pipe.expire(key, ttl)
        bucket = hour_bucket()
        field = f"{region}|{bucket}"
        total = 0
        for deployment_id, count in traffic.items():
            if count <= 0:
                continue
            total += count
            key = _key("traffic", deployment_id)
            pipe.hincrby(key, field, int(count))
            pipe.expire(key, window_ttl)
        if total:
            pipe.hincrby(_key("traffic", ALL_DEPLOYMENTS), field, total)
            pipe.expire(_key("traffic", ALL_DEPLOYMENTS), window_ttl)
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"latency publish skipped: {exc}")


async def publish_hub(node_id: str, snapshot: Dict[str, Any]) -> None:
    from app.db.redis.client import get_redis

    try:
        client = await get_redis()
        await client.hset(_key("hub"), node_id, json.dumps(snapshot))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"hub RTT publish skipped: {exc}")


async def drop_hub(node_id: str) -> None:
    from app.db.redis.client import get_redis

    try:
        client = await get_redis()
        await client.hdel(_key("hub"), node_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"hub RTT drop skipped: {exc}")


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


async def read_live(now: Optional[float] = None) -> Dict[str, Any]:
    """
    Fresh measurements::

        {"api_layers": {id: {id, region, ts, ...}},
         "api": {api layer id: {node id: snapshot}},
         "hub": {node id: snapshot}}
    """
    from app.db.redis.client import get_redis

    current = time.time() if now is None else now
    out: Dict[str, Any] = {"api_layers": {}, "api": {}, "hub": {}}
    try:
        client = await get_redis()
        layers = await client.hgetall(_key("api-layers"))
        api_stale = api_stale_seconds()
        for ident, raw in (layers or {}).items():
            entry = _loads(raw)
            if _fresh(entry, api_stale, current):
                out["api_layers"][ident] = entry
        for ident in out["api_layers"]:
            rows = await client.hgetall(_key("api", ident))
            nodes = {}
            for node_id, raw in (rows or {}).items():
                entry = _loads(raw)
                if _fresh(entry, api_stale, current):
                    nodes[node_id] = entry
            out["api"][ident] = nodes
        hub_rows = await client.hgetall(_key("hub"))
        hub_stale = hub_stale_seconds()
        for node_id, raw in (hub_rows or {}).items():
            entry = _loads(raw)
            if _fresh(entry, hub_stale, current):
                out["hub"][node_id] = entry
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"latency live read failed: {exc}")
    return out


async def read_traffic(
    deployment_id: Optional[str], hours: Optional[int] = None, now: Optional[datetime] = None
) -> Dict[str, int]:
    """Requests per API-layer region over the last ``hours`` (``None`` id = whole fleet)."""
    from app.db.redis.client import get_redis

    window = int(hours or settings.SCHEDULER_TRAFFIC_WINDOW_HOURS)
    current = now or datetime.now(timezone.utc)
    buckets = {hour_bucket(current - timedelta(hours=h)) for h in range(max(1, window))}
    try:
        client = await get_redis()
        rows = await client.hgetall(_key("traffic", deployment_id or ALL_DEPLOYMENTS))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"traffic read failed: {exc}")
        return {}
    return traffic_by_region(rows or {}, buckets)


def traffic_by_region(rows: Mapping[Any, Any], buckets: Iterable[str]) -> Dict[str, int]:
    wanted = set(buckets)
    out: Dict[str, int] = {}
    for field, value in rows.items():
        region, _, bucket = str(field).rpartition("|")
        if not region or bucket not in wanted:
            continue
        try:
            out[region] = out.get(region, 0) + int(value)
        except (TypeError, ValueError):
            continue
    return out
