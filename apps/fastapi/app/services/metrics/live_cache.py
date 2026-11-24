"""
Latest-sample cache in Redis.

The hub ingest writes the newest sample per device / instance / node into
five hashes (field = entity id, value = protocol sample JSON + ``node_id``):

| Hash                         | Field         | Value                                   |
| ---------------------------- | ------------- | --------------------------------------- |
| ``gpu-deck:live:gpu``        | device_id     | ``GpuSample`` + node_id                 |
| ``gpu-deck:live:engine``     | instance_id   | ``EngineSample`` + node_id              |
| ``gpu-deck:live:sessions``   | instance_id   | ``{ts, node_id, sessions: [...]}``      |
| ``gpu-deck:live:node``       | node_id       | heartbeat ``{ts, load, instances, ...}``|
| ``gpu-deck:live:agent``      | node_id       | ``metrics_batch.agent`` + ts            |

Readers: ``live`` fields on devices/instances, ``/metrics/sessions/live``,
the Prometheus exposition, ``/metrics/overview`` and the ``/live/*`` streams.
A sample older than ``METRICS_LIVE_STALE_SECONDS`` reads as null; the hub
sweeper drops fields older than ``LIVE_TTL_SECONDS``. Every read/write is best
effort (Redis down = no live data, never an error).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Union

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.redis.client import get_redis, redis_key

logger = get_logger(__name__)

LIVE_TTL_SECONDS = 120
KINDS = ("gpu", "engine", "sessions", "node", "agent")


def hash_key(kind: str) -> str:
    return redis_key("live", kind)


# Back-compat helpers (field ids for the hashes above).
def gpu_key(device_id: Union[str, Any]) -> str:
    return str(device_id)


def engine_key(instance_id: Union[str, Any]) -> str:
    return str(instance_id)


def sessions_key(instance_id: Union[str, Any]) -> str:
    return str(instance_id)


def node_key(node_id: Union[str, Any]) -> str:
    return str(node_id)


def parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_seconds(sample: Optional[Dict[str, Any]]) -> Optional[float]:
    ts = parse_ts((sample or {}).get("ts"))
    if ts is None:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds()


def is_fresh(sample: Optional[Dict[str, Any]], stale_seconds: Optional[float] = None) -> bool:
    age = age_seconds(sample)
    if age is None:
        return False
    limit = stale_seconds if stale_seconds is not None else settings.METRICS_LIVE_STALE_SECONDS
    return age <= limit


def _dumps(value: Any) -> str:
    from app.services.agent_hub.protocol import dumps

    return dumps(value)


async def put_many(kind: str, items: Dict[str, Dict[str, Any]]) -> None:
    if not items:
        return
    try:
        client = await get_redis()
        await client.hset(hash_key(kind), mapping={k: _dumps(v) for k, v in items.items()})
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"live cache write {kind} failed: {exc}")


async def put(kind: str, entity_id: Any, value: Dict[str, Any]) -> None:
    await put_many(kind, {str(entity_id): value})


async def get_many(ids: Iterable[Any], kind: str = "gpu") -> List[Optional[Dict[str, Any]]]:
    """Samples for the given ids in order (None when absent)."""
    fields = [str(i) for i in ids]
    if not fields:
        return []
    try:
        client = await get_redis()
        values = await client.hmget(hash_key(kind), fields)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"live cache read {kind} failed: {exc}")
        return [None] * len(fields)
    return [_loads(v) for v in values]


async def get_one(kind: str, entity_id: Any) -> Optional[Dict[str, Any]]:
    return (await get_many([entity_id], kind))[0]


async def get_all(kind: str) -> Dict[str, Dict[str, Any]]:
    try:
        client = await get_redis()
        raw = await client.hgetall(hash_key(kind))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"live cache scan {kind} failed: {exc}")
        return {}
    out = {}
    for key, value in raw.items():
        parsed = _loads(value)
        if parsed is not None:
            out[key] = parsed
    return out


async def drop_stale(max_age_seconds: float = LIVE_TTL_SECONDS) -> int:
    """Remove fields whose sample is older than the TTL (hub sweeper)."""
    removed = 0
    try:
        client = await get_redis()
        for kind in KINDS:
            raw = await client.hgetall(hash_key(kind))
            stale = [k for k, v in raw.items() if not is_fresh(_loads(v), max_age_seconds)]
            if stale:
                removed += await client.hdel(hash_key(kind), *stale)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"live cache cleanup failed: {exc}")
    return removed


async def drop_node(node_id: Any) -> None:
    """Node went offline: forget its device/instance samples right away."""
    nid = str(node_id)
    try:
        client = await get_redis()
        for kind in ("gpu", "engine", "sessions"):
            raw = await client.hgetall(hash_key(kind))
            fields = [k for k, v in raw.items() if (_loads(v) or {}).get("node_id") == nid]
            if fields:
                await client.hdel(hash_key(kind), *fields)
        await client.hdel(hash_key("node"), nid)
        await client.hdel(hash_key("agent"), nid)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"live cache drop node failed: {exc}")


def _loads(value: Any) -> Optional[Dict[str, Any]]:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


# ==========================================
# Projections used by API responses
# ==========================================


def device_live(sample: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not is_fresh(sample):
        return {
            "ts": None,
            "utilization_pct": None,
            "memory_used_bytes": None,
            "power_watts": None,
            "temperature_c": None,
        }
    assert sample is not None
    return {
        "ts": sample.get("ts"),
        "utilization_pct": sample.get("utilization_pct"),
        "memory_used_bytes": sample.get("memory_used_bytes"),
        "power_watts": sample.get("power_watts"),
        "temperature_c": sample.get("temperature_c"),
    }


def instance_live(
    sample: Optional[Dict[str, Any]], rollup: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    s = sample if is_fresh(sample) and sample else {}
    rollup = rollup or {}
    rpm = rollup.get("requests_delta")
    return {
        "ts": s.get("ts"),
        "active_sessions": s.get("active_sessions"),
        "waiting_sessions": s.get("waiting_sessions"),
        "kv_cache_usage_pct": s.get("kv_cache_usage_pct"),
        "generation_tps": s.get("generation_tps"),
        "ttft_ms_p95": s.get("ttft_ms_p95"),
        "requests_per_minute": float(rpm) if rpm is not None else None,
        "error_rate_pct": rollup.get("error_rate_pct"),
    }


def overview_live(
    gpu: Iterable[Dict[str, Any]],
    engine: Iterable[Dict[str, Any]],
    vram_total_bytes: int = 0,
    requests_per_minute: Optional[float] = None,
    error_rate_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """``OverviewLive`` from fresh samples (callers pass already-filtered samples)."""
    gpu = [s for s in gpu if is_fresh(s)]
    engine = [s for s in engine if is_fresh(s)]
    utils = [float(s["utilization_pct"]) for s in gpu if s.get("utilization_pct") is not None]
    powers = [float(s["power_watts"]) for s in gpu if s.get("power_watts") is not None]
    ttfts = [float(s["ttft_ms_p95"]) for s in engine if s.get("ttft_ms_p95") is not None]
    tps = [float(s["generation_tps"]) for s in engine if s.get("generation_tps") is not None]
    sample_total = sum(int(s.get("memory_total_bytes") or 0) for s in gpu)
    return {
        "ts": datetime.now(timezone.utc),
        "gpu_utilization_pct_avg": round(sum(utils) / len(utils), 2) if utils else None,
        "vram_used_bytes": sum(int(s.get("memory_used_bytes") or 0) for s in gpu),
        "vram_total_bytes": int(vram_total_bytes or sample_total),
        "power_watts": round(sum(powers), 2) if powers else None,
        "active_sessions": sum(int(s.get("active_sessions") or 0) for s in engine),
        "waiting_sessions": sum(int(s.get("waiting_sessions") or 0) for s in engine),
        "requests_per_minute": requests_per_minute,
        "generation_tps": round(sum(tps), 2) if tps else None,
        "ttft_ms_p95": max(ttfts) if ttfts else None,
        "error_rate_pct": error_rate_pct,
    }
