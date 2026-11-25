"""
Metering service.

Hot path: ``record_request`` builds the usage payload for one serving request
and hands it to Celery (``enqueue_usage_event``) from a worker thread, after
the response has been sent. Never raises.

Admin path: ``MeteringService`` serves the usage screens (events drill-down,
grouped summaries, CSV export) from ``usage_events`` / ``usage_rollups_hourly``.
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from app.db.postgres.connection import get_db
from app.db.postgres.repositories.usage_event_repository import UsageEventRepository
from app.db.postgres.repositories.usage_rollup_repository import UsageRollupRepository
from app.services.serving.usage import Usage
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

GROUP_KEYS = (
    "api_key",
    "model",
    "node",
    "deployment",
    "cluster",
    "format",
    "endpoint",
    "day",
    "hour",
)


def build_usage_payload(
    ctx, usage: Usage, status: str, http_status: int, error_code=None
) -> Dict[str, Any]:
    target = ctx.target
    model = target.model if target else None
    inst = target.instance if target else None
    meta = dict(ctx.metadata)
    if usage.estimated:
        meta["estimated"] = True
    meta.update(usage.extra)
    timing = _timing_metadata(ctx)
    if timing:
        meta["timing"] = timing
        from app.services.latency.store import api_layer_identity

        layer_id, region = api_layer_identity()
        meta["api_layer"] = {"id": layer_id, "region": region}
    return {
        "ts": ctx.started_ts.isoformat(),
        "request_id": ctx.request_id,
        "api_key_id": ctx.api_key.id if ctx.api_key else None,
        "model_id": model.model_id if model else None,
        "model_name": ctx.model or (model.name if model else "unknown"),
        "deployment_id": (inst.deployment_id if inst and inst.deployment_id else None)
        or (model.deployment_id if model else None),
        "instance_id": inst.instance_id if inst and not inst.direct else None,
        "node_id": inst.node_id if inst and not inst.direct else None,
        "cluster_id": model.cluster_id if model else None,
        "format": ctx.fmt,
        "endpoint": ctx.endpoint,
        "stream": bool(ctx.stream),
        "request_count": 1,
        "input_tokens": int(usage.input_tokens or 0),
        "output_tokens": int(usage.output_tokens or 0),
        "cached_tokens": int(usage.cached_tokens or 0),
        "reasoning_tokens": int(usage.reasoning_tokens or 0),
        "media_units": float(usage.media_units or 0),
        "latency_ms": ctx.elapsed_ms(),
        "ttft_ms": ctx.ttft_ms,
        "gpu_count": int(model.gpu_count if model else 1),
        "slice_compute_pct": model.slice_compute_pct if model else None,
        "concurrency_at_start": int(ctx.concurrency_at_start),
        "status": status,
        "http_status": int(http_status),
        "error_code": error_code,
        "ip_address": ctx.client_ip,
        "metadata": meta or None,
    }


def _timing_metadata(ctx) -> Dict[str, float]:
    """Where the time went (``app.services.serving.timing``), for the request log."""
    breakdown = getattr(ctx, "timing_breakdown", None)
    if breakdown is None:
        return {}
    from app.services.serving.timing import record

    try:
        return record(breakdown(final=True))
    except Exception:  # noqa: BLE001
        return {}


def _annotate_span(timing: Dict[str, Any]) -> None:
    """Put the breakdown on the request's server span (``gpudeck.timing.*`` attributes)."""
    if not timing:
        return
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording():
            span.set_attributes({f"gpudeck.timing.{k}": float(v) for k, v in timing.items()})
    except Exception:  # noqa: BLE001
        pass


async def record_request(ctx, usage: Usage, status: str, http_status: int, error_code=None) -> None:
    """Enqueue the usage event off the event loop (BackgroundTask / after stream end)."""
    if getattr(ctx, "metered", False):
        return
    ctx.metered = True
    try:
        payload = build_usage_payload(ctx, usage, status, http_status, error_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"usage payload build failed: {exc}")
        return
    from app.services.serving.metrics import observe_usage

    _annotate_span((payload.get("metadata") or {}).get("timing") or {})
    observe_usage(payload)
    try:
        from app.workers.tasks.metering_tasks import enqueue_usage_event

        await asyncio.to_thread(enqueue_usage_event, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"usage enqueue failed: {exc}")


ROLLUP_GROUP_KEYS = {"api_key", "model", "node", "deployment", "cluster", "day", "hour"}
EVENT_ONLY_FILTERS = ("format", "status", "endpoint", "request_id")
TIMESERIES_METRICS = {
    "requests": ("request_count", "requests"),
    "input_tokens": ("input_tokens", "tokens"),
    "output_tokens": ("output_tokens", "tokens"),
    "cached_tokens": ("cached_tokens", "tokens"),
    "reasoning_tokens": ("reasoning_tokens", "tokens"),
    "errors": ("error_count", "requests"),
    "gpu_minutes": ("gpu_minutes", "minutes"),
    "cost_usd": ("cost_usd", "usd"),
    "latency_ms_avg": ("latency_ms_avg", "ms"),
}


class MeteringService:
    """Admin read side of metering (admin.yaml Metering tag)."""

    def __init__(self, events: UsageEventRepository, rollups: UsageRollupRepository) -> None:
        self.events = events
        self.rollups = rollups

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "MeteringService":
        return MeteringService(UsageEventRepository(db), UsageRollupRepository(db))

    @staticmethod
    def default_range(start: Optional[datetime], end: Optional[datetime], hours: int = 1):
        now = datetime.now(timezone.utc)
        end = end or now
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        start = start or (end - timedelta(hours=hours))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start >= end:
            from app.core.exceptions import ValidationError

            raise ValidationError("'from' must be before 'to'")
        return start, end

    async def list_events(self, filters: Dict[str, Any], page: int, page_size: int, sort: str):
        return await self.events.list_events(filters, page, page_size, sort)

    @staticmethod
    def uses_rollups(filters: Dict[str, Any], group_by: List[str]) -> bool:
        """Rollups carry billable GPU time; events are needed for format/status/endpoint."""
        return set(group_by) <= ROLLUP_GROUP_KEYS and not any(
            filters.get(k) for k in EVENT_ONLY_FILTERS
        )

    async def summary(self, filters: Dict[str, Any], group_by: List[str]) -> Dict[str, Any]:
        group_by = [g for g in (group_by or []) if g in GROUP_KEYS]
        seen: List[str] = []
        for g in group_by:
            if g not in seen:
                seen.append(g)
        group_by = seen
        if self.uses_rollups(filters, group_by):
            rows = await self.rollups.grouped(filters, group_by)
        else:
            rows = await self.events.summary(filters, group_by)
        totals = aggregate_totals(rows)
        return {
            "from": filters["from"].isoformat(),
            "to": filters["to"].isoformat(),
            "group_by": group_by,
            "rows": rows if group_by else [],
            "totals": totals,
        }

    async def export_csv(self, filters: Dict[str, Any], mode: str, group_by: List[str]) -> str:
        buf = io.StringIO()
        if mode == "summary":
            data = await self.summary(filters, group_by or ["api_key", "model"])
            keys = data["group_by"]
            writer = csv.writer(buf)
            writer.writerow([*keys, *(f"{k}_label" for k in keys), *TOTAL_FIELDS])
            for row in data["rows"]:
                t = row["totals"]
                writer.writerow(
                    [
                        *(row["group"].get(k, "") for k in keys),
                        *(row["labels"].get(k, "") for k in keys),
                    ]
                    + [t.get(f) for f in TOTAL_FIELDS]
                )
            return buf.getvalue()
        rows, _ = await self.events.list_events(filters, 1, EXPORT_MAX_ROWS, "-ts")
        writer = csv.DictWriter(buf, fieldnames=EVENT_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in EVENT_CSV_FIELDS})
        return buf.getvalue()

    async def usage_timeseries(
        self,
        filters: Dict[str, Any],
        step: str = "1h",
        metrics: Optional[List[str]] = None,
        group_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """``GET /metrics/usage`` payload (TimeSeriesResponse) from the hourly rollups.

        Provided for the metrics route: ``step`` is ``1h`` or ``1d`` (rollups are
        hourly); ``group_by`` one of none/api_key/model/node/deployment.
        """
        bucket_key = "day" if step in ("1d", "24h", "day") else "hour"
        wanted = [
            m
            for m in (metrics or ["requests", "input_tokens", "output_tokens", "cost_usd"])
            if m in TIMESERIES_METRICS
        ]
        groups = [bucket_key] + ([group_by] if group_by and group_by != "none" else [])
        rows = await self.rollups.grouped(filters, groups, order="group")
        series: Dict[tuple, Dict[str, Any]] = {}
        for row in rows:
            ts = row["group"][bucket_key]
            label_key = row["group"].get(group_by or "", "") if len(groups) > 1 else ""
            for metric in wanted:
                field, unit = TIMESERIES_METRICS[metric]
                entry = series.setdefault(
                    (metric, label_key),
                    {
                        "labels": {"metric": metric},
                        "metric": f"usage_{metric}",
                        "unit": unit,
                        "points": [],
                    },
                )
                if len(groups) > 1:
                    entry["labels"][group_by] = label_key
                    entry["labels"][f"{group_by}_name"] = row["labels"].get(group_by, "")
                entry["points"].append([ts, row["totals"].get(field)])
        return {
            "from": filters["from"].isoformat(),
            "to": filters["to"].isoformat(),
            "step": "1d" if bucket_key == "day" else "1h",
            "series": list(series.values()),
        }


TOTAL_FIELDS = [
    "request_count",
    "success_count",
    "error_count",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "media_units",
    "gpu_seconds",
    "gpu_minutes",
    "latency_ms_avg",
    "ttft_ms_avg",
    "cost_usd",
]
EXPORT_MAX_ROWS = 100_000
EVENT_CSV_FIELDS = [
    "id",
    "ts",
    "request_id",
    "api_key_id",
    "api_key_name",
    "model_id",
    "model_name",
    "deployment_id",
    "instance_id",
    "node_id",
    "node_name",
    "cluster_id",
    "format",
    "endpoint",
    "stream",
    "request_count",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "media_units",
    "latency_ms",
    "ttft_ms",
    "gpu_seconds",
    "gpu_count",
    "status",
    "http_status",
    "error_code",
    "price_plan_id",
    "cost_usd",
    "ip_address",
]


def aggregate_totals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals = {
        "request_count": 0,
        "success_count": 0,
        "error_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "media_units": 0.0,
        "gpu_seconds": 0.0,
        "gpu_minutes": 0.0,
        "latency_ms_avg": None,
        "ttft_ms_avg": None,
        "cost_usd": 0.0,
    }
    lat_sum = ttft_sum = 0.0
    lat_n = ttft_n = 0
    for row in rows:
        t = row["totals"]
        for key in (
            "request_count",
            "success_count",
            "error_count",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "reasoning_tokens",
        ):
            totals[key] += int(t.get(key) or 0)
        for key in ("media_units", "gpu_seconds", "gpu_minutes", "cost_usd"):
            totals[key] = round(totals[key] + float(t.get(key) or 0), 6)
        n = int(t.get("request_count") or 0)
        if t.get("latency_ms_avg") is not None:
            lat_sum += float(t["latency_ms_avg"]) * n
            lat_n += n
        if t.get("ttft_ms_avg") is not None:
            ttft_sum += float(t["ttft_ms_avg"]) * n
            ttft_n += n
    if lat_n:
        totals["latency_ms_avg"] = round(lat_sum / lat_n, 2)
    if ttft_n:
        totals["ttft_ms_avg"] = round(ttft_sum / ttft_n, 2)
    return totals
