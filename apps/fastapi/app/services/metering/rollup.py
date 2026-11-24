"""
Hourly usage rollups and billable GPU-minute allocation (uptime-based, not per-request wall
time, which over-counts under concurrency).

For one hour bucket the job:

1. sums ``usage_events`` per (api key, model, node, deployment);
2. finds every instance-minute the instance was loaded — a row in the fleet's
   ``metric_rollups_engine_1m`` for that instance and minute, or, for
   instances without engine metrics, its ready/draining interval from
   ``instances`` (``ready_at`` .. ``stopped_at``/now, partial minutes
   prorated);
3. gives each such minute the instance's share of its GPUs
   (``device_shares``): per device, a slice claims ``compute_pct/100`` and a
   whole-GPU instance claims 1; when the claims of everything on one device in
   one minute (engine instances, observed engines and SSH GPU instances) add
   up to more than the device, the device minute is split by VRAM share, so one
   GPU never bills more than one GPU-minute per minute. Loaded-but-idle
   instances keep their share. Instances without device ids fall back to
   ``gpu_count x slice_compute_pct/100``. The minutes are allocated to API keys
   pro-rata by tokens processed on that instance in that minute (requests when
   no tokens, e.g. images); minutes without traffic go to the ``unallocated``
   bucket (``api_key_id`` NULL);
4. prices allocated GPU-minutes with the node's plan and rewrites the bucket
   in ``usage_rollups_hourly`` (delete + insert: idempotent, re-run every
   ``METERING_ROLLUP_INTERVAL_MINUTES`` for the current and previous hour).

SSH GPU instances write one ``usage_events`` row per metered window
with ``endpoint='gpu_instance'``; their ``gpu_seconds`` (window x GPUs x
slice) are added to the bucket's GPU time as-is (priced with the node plan,
like engine minutes) and they never count as requests or latency. Their claim
is taken off the device capacity left for engine instances in the same minute.

It also refreshes ``api_keys.total_requests`` / ``last_used_at`` and
``deployments`` / ``instances.last_request_at``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.repositories.usage_event_repository import UsageEventRepository
from app.db.postgres.repositories.usage_rollup_repository import UsageRollupRepository
from app.services.billing.pricing import PlanResolver, gpu_cost, load_resolver
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

Key = Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]  # api_key, model, node, dep
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)


def hour_floor(ts: datetime) -> datetime:
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _s(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def minute_coverage(
    start: datetime, stop: datetime, window_start: datetime, window_end: datetime
) -> List[Tuple[datetime, float]]:
    """(minute, fraction of the minute covered) for [start, stop) clipped to the window."""
    lo = max(start, window_start)
    hi = min(stop, window_end)
    out: List[Tuple[datetime, float]] = []
    if hi <= lo:
        return out
    minute = lo.replace(second=0, microsecond=0)
    while minute < hi:
        nxt = minute + MINUTE
        covered = (min(nxt, hi) - max(minute, lo)).total_seconds()
        if covered > 0:
            out.append((minute, round(covered / 60.0, 6)))
        minute = nxt
    return out


def _dec(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal(default)
    except (ArithmeticError, ValueError):
        return Decimal(default)


def _instance_claim(
    info: Dict[str, Any], shape: Dict[str, Any], device_totals: Dict[str, int]
) -> Tuple[List[str], Decimal, Dict[str, Decimal]]:
    """(devices, claim per device 0..1, VRAM weight per device) of one engine instance."""
    devices = [str(d) for d in (info.get("device_ids") or [])]
    res = info.get("resources") or {}
    if not isinstance(res, dict):
        res = {}
    slice_ = res.get("slice") if isinstance(res.get("slice"), dict) else None
    pct = (slice_ or {}).get("compute_pct") or shape.get("slice_compute_pct")
    claim = min(Decimal(1), max(Decimal(0), _dec(pct, "100") / Decimal(100)))
    per_device: Optional[int] = None
    if slice_ and slice_.get("vram_bytes"):
        per_device = int(slice_["vram_bytes"])
    elif isinstance(res.get("vram_bytes_used"), int) and devices:
        per_device = int(res["vram_bytes_used"]) // len(devices)
    elif isinstance(res.get("vram_bytes_per_device"), int):
        per_device = int(res["vram_bytes_per_device"])
    weights = {
        d: Decimal(max(1, per_device if per_device else device_totals.get(d) or 1)) for d in devices
    }
    return devices, claim, weights


def ssh_claims(
    intervals: Iterable[Dict[str, Any]], start: datetime, horizon: datetime
) -> Dict[Tuple[datetime, str], Decimal]:
    """SSH GPU instance claim per (minute, device): coverage x slice fraction (or 1)."""
    out: Dict[Tuple[datetime, str], Decimal] = defaultdict(Decimal)
    for iv in intervals:
        running = iv.get("running_at")
        if running is None:
            continue
        stop = iv.get("stopped_at") if iv.get("state") not in ("running", "stopping") else None
        stop = stop if stop and stop > running else horizon
        pct = iv.get("slice_compute_pct") if iv.get("slice_vram_bytes") else None
        claim = _dec(pct, "100") / Decimal(100)
        for minute, fraction in minute_coverage(running, min(stop, horizon), start, horizon):
            for dev in iv.get("device_ids") or []:
                out[(minute, str(dev))] += claim * Decimal(str(fraction))
    return dict(out)


def device_shares(
    up_minutes: List[Dict[str, Any]],
    instances: Dict[str, Dict[str, Any]],
    shapes: Dict[str, Dict[str, Any]],
    device_totals: Dict[str, int],
    reserved: Optional[Dict[Tuple[datetime, str], Decimal]] = None,
) -> List[Dict[str, Any]]:
    """
    Annotate each up-minute row with ``gpu_minutes``: its share of the devices it ran on.

    Per (minute, device) the instances' claims (slice fraction or 1, times the covered
    fraction of the minute) are billed as-is while they fit into the device (minus what
    SSH GPU instances already bill, ``reserved``); otherwise the device minute is split by
    VRAM share (each instance capped at its own claim). Rows of instances without device
    ids are left alone (``allocate`` falls back to ``gpu_count x slice``).
    """
    reserved = reserved or {}
    out = [dict(r) for r in up_minutes]
    entries: Dict[Tuple[datetime, str], List[Tuple[int, Decimal, Decimal]]] = defaultdict(list)
    for idx, row in enumerate(out):
        info = instances.get(str(row["instance_id"]))
        if not info:
            continue
        shape = shapes.get(_s(row.get("deployment_id")) or "", {})
        devices, claim, weights = _instance_claim(info, shape, device_totals)
        if not devices:
            continue
        fraction = Decimal(str(row.get("fraction", 1.0)))
        row["gpu_minutes"] = Decimal(0)
        for dev in devices:
            entries[(row["minute"], dev)].append((idx, claim * fraction, weights[dev] * fraction))
    for key, items in entries.items():
        capacity = max(Decimal(0), Decimal(1) - reserved.get(key, Decimal(0)))
        demand = sum(c for _, c, _ in items)
        if demand <= capacity:
            for idx, c, _ in items:
                out[idx]["gpu_minutes"] += c
            continue
        total_weight = sum(w for _, _, w in items) or Decimal(1)
        for idx, c, w in items:
            out[idx]["gpu_minutes"] += min(c, capacity * w / total_weight)
    return out


def allocate(
    up_minutes: Iterable[Dict[str, Any]],
    token_minutes: Iterable[Dict[str, Any]],
    shapes: Dict[str, Dict[str, Any]],
) -> Dict[Key, Decimal]:
    """Pure allocation: GPU-minutes per (api_key, model, node, deployment)."""
    weights: Dict[Tuple[datetime, str], Dict[Optional[str], Tuple[int, int]]] = defaultdict(dict)
    for row in token_minutes:
        key = (row["minute"], str(row["instance_id"]))
        weights[key][_s(row.get("api_key_id"))] = (
            int(row.get("tokens") or 0),
            int(row.get("requests") or 0),
        )
    out: Dict[Key, Decimal] = defaultdict(Decimal)
    for row in up_minutes:
        dep = _s(row.get("deployment_id"))
        shape = shapes.get(dep or "", {})
        if row.get("gpu_minutes") is not None:  # device-share prorated (device_shares)
            minutes = Decimal(str(row["gpu_minutes"]))
        else:
            gpus = Decimal(max(1, int(shape.get("gpu_count") or 1)))
            pct = Decimal(int(shape.get("slice_compute_pct") or 100)) / Decimal(100)
            minutes = gpus * pct * Decimal(str(row.get("fraction", 1.0)))
        model = _s(shape.get("model_id"))
        node = _s(row.get("node_id"))
        per_key = weights.get((row["minute"], str(row["instance_id"])))
        if per_key:
            total_tokens = sum(t for t, _ in per_key.values())
            total_requests = sum(r for _, r in per_key.values())
            use_tokens = total_tokens > 0
            total = total_tokens if use_tokens else total_requests
            if total > 0:
                for api_key, (tokens, requests) in per_key.items():
                    share = Decimal(tokens if use_tokens else requests) / Decimal(total)
                    if share:
                        out[(api_key, model, node, dep)] += minutes * share
                continue
        out[(None, model, node, dep)] += minutes
    return dict(out)


async def collect_up_minutes(
    repo: UsageRollupRepository, start: datetime, end: datetime, now: datetime
) -> List[Dict[str, Any]]:
    rows = [dict(r, fraction=1.0) for r in await repo.uptime_minutes(start, end)]
    with_metrics = {str(r["instance_id"]) for r in rows}
    horizon = min(end, now)
    for iv in await repo.instance_intervals(start, end):
        if str(iv["instance_id"]) in with_metrics:
            continue
        stop = iv.get("stopped_at") if iv.get("state") not in ("ready", "draining") else None
        stop = stop if stop and stop > iv["ready_at"] else horizon
        for minute, fraction in minute_coverage(iv["ready_at"], min(stop, horizon), start, horizon):
            rows.append(
                {
                    "minute": minute,
                    "instance_id": iv["instance_id"],
                    "node_id": iv["node_id"],
                    "deployment_id": iv["deployment_id"],
                    "fraction": fraction,
                }
            )
    return rows


def merge_bucket(
    event_rows: List[Dict[str, Any]],
    gpu_minutes: Dict[Key, Decimal],
    resolver: Optional[PlanResolver],
) -> List[Dict[str, Any]]:
    """Combine event sums and allocated GPU-minutes into rollup rows (with cost)."""
    rows: Dict[Key, Dict[str, Any]] = {}
    for e in event_rows:
        key: Key = (
            _s(e.get("api_key_id")),
            _s(e.get("model_id")),
            _s(e.get("node_id")),
            _s(e.get("deployment_id")),
        )
        rows[key] = {
            "request_count": int(e.get("request_count") or 0),
            "success_count": int(e.get("success_count") or 0),
            "error_count": int(e.get("error_count") or 0),
            "input_tokens": int(e.get("input_tokens") or 0),
            "output_tokens": int(e.get("output_tokens") or 0),
            "cached_tokens": int(e.get("cached_tokens") or 0),
            "reasoning_tokens": int(e.get("reasoning_tokens") or 0),
            "media_units": Decimal(str(e.get("media_units") or 0)),
            "latency_ms_sum": int(e.get("latency_ms_sum") or 0),
            "ttft_ms_sum": int(e.get("ttft_ms_sum") or 0),
            "token_cost": Decimal(str(e.get("token_cost_usd") or 0)),
            # SSH GPU instance windows (endpoint gpu_instance): billed GPU time as-is.
            "gpu_minutes": Decimal(str(e.get("instance_gpu_seconds") or 0)) / Decimal(60),
        }
    for key, minutes in gpu_minutes.items():
        row = rows.setdefault(
            key,
            {
                "request_count": 0,
                "success_count": 0,
                "error_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
                "media_units": Decimal(0),
                "latency_ms_sum": 0,
                "ttft_ms_sum": 0,
                "token_cost": Decimal(0),
                "gpu_minutes": Decimal(0),
            },
        )
        row["gpu_minutes"] += minutes
    out = []
    for (api_key, model, node, dep), row in rows.items():
        plan = resolver.resolve(node) if resolver is not None else None
        cost = row["token_cost"] + gpu_cost(plan, row["gpu_minutes"])
        out.append(
            {
                "api_key_id": api_key,
                "model_id": model,
                "node_id": node,
                "deployment_id": dep,
                "request_count": row["request_count"],
                "success_count": row["success_count"],
                "error_count": row["error_count"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cached_tokens": row["cached_tokens"],
                "reasoning_tokens": row["reasoning_tokens"],
                "media_units": row["media_units"].quantize(Decimal("0.001")),
                "gpu_seconds": (row["gpu_minutes"] * 60).quantize(Decimal("0.001")),
                "latency_ms_sum": row["latency_ms_sum"],
                "ttft_ms_sum": row["ttft_ms_sum"],
                "cost_usd": cost.quantize(Decimal("0.000001")),
            }
        )
    return out


async def rollup_hour(
    session: AsyncSession, bucket: datetime, now: Optional[datetime] = None
) -> int:
    """Rebuild one hour of ``usage_rollups_hourly``; returns rows written."""
    bucket = hour_floor(bucket)
    end = bucket + HOUR
    now = now or datetime.now(timezone.utc)
    events = UsageEventRepository(session)
    rollups = UsageRollupRepository(session)
    event_rows = await events.bucket_sums(bucket, end)
    up = await collect_up_minutes(rollups, bucket, end, now)
    shapes = await rollups.deployment_shapes(list({str(r["deployment_id"]) for r in up}))
    for e in event_rows:  # deployment row gone (purged): take the model from its events
        dep = _s(e.get("deployment_id"))
        if dep and e.get("model_id") and not (shapes.get(dep) or {}).get("model_id"):
            shapes.setdefault(dep, {})["model_id"] = e["model_id"]
    info = await rollups.instance_gpu_info(list({str(r["instance_id"]) for r in up}))
    devices = {str(d) for i in info.values() for d in (i.get("device_ids") or [])}
    horizon = min(end, now)
    ssh = await rollups.gpu_instance_intervals(bucket, end)
    devices |= {str(d) for iv in ssh for d in (iv.get("device_ids") or [])}
    totals = await rollups.device_vram_totals(list(devices))
    up = device_shares(up, info, shapes, totals, ssh_claims(ssh, bucket, horizon))
    tokens = await events.token_minutes(bucket, end)
    gpu_minutes = allocate(up, tokens, shapes)
    resolver = await load_resolver(session)
    rows = merge_bucket(event_rows, gpu_minutes, resolver)
    written = await rollups.replace_bucket(bucket, rows)
    await rollups.update_last_seen(await events.last_seen(bucket, end))
    await rollups.refresh_total_requests(list({r["api_key_id"] for r in rows if r["api_key_id"]}))
    return written


async def rollup_recent(session: AsyncSession, now: Optional[datetime] = None) -> Dict[str, int]:
    """Current hour and the previous one (late events, final allocation)."""
    now = now or datetime.now(timezone.utc)
    current = hour_floor(now)
    out = {}
    for bucket in (current - HOUR, current):
        out[bucket.isoformat()] = await rollup_hour(session, bucket, now)
    return out


async def rollup_range(session: AsyncSession, start: datetime, end: datetime) -> int:
    """Backfill helper (CLI / admin scripts)."""
    bucket = hour_floor(start)
    total = 0
    while bucket < end:
        total += await rollup_hour(session, bucket)
        bucket += HOUR
    return total
