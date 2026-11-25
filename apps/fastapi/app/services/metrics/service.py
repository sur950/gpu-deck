"""
Metrics query service: every admin.yaml ``/metrics/*`` read.

- ``gpu`` / ``inference``: time series from ``metric_samples_*`` (step < 60 s
  and within raw retention) or ``metric_rollups_*_1m``; one series per device /
  instance (``aggregate=none``) or merged (``sum|avg|max``).
- ``sessions``: per-session series for one instance; ``sessions_live``: the
  latest slot snapshot per instance from the live cache (DB fallback).
- ``usage``: ``usage_rollups_hourly`` (written by metering) by api key/model/node/deployment.
- ``overview``: counts, live totals, cost today/month, recent alerts/swaps/rule runs.

Values are floats or null ("no data"); buckets with no rows are null points.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db, get_session_maker
from app.db.postgres.repositories.metric_repository import MetricRepository
from app.services.metrics import catalog, live_cache
from app.services.metrics.catalog import MetricSpec
from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

EPOCH_2000 = datetime(2000, 1, 1, tzinfo=timezone.utc)
_STEP_RE = re.compile(r"^\s*(\d+)\s*(s|m|h|d)\s*$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
AGGREGATES = {"sum": "SUM", "avg": "AVG", "max": "MAX"}

TABLES = {
    ("gpu", "raw"): "metric_samples_gpu",
    ("gpu", "rollup"): "metric_rollups_gpu_1m",
    ("engine", "raw"): "metric_samples_engine",
    ("engine", "rollup"): "metric_rollups_engine_1m",
}
SESSION_FIELDS = [
    "kv_cache_tokens",
    "kv_cache_bytes",
    "prompt_tokens",
    "generated_tokens",
    "cached_tokens",
    "context_size",
    "ttft_ms",
    "elapsed_ms",
    "tokens_per_second",
]


# ==========================================
# Parsing helpers
# ==========================================


def parse_step(step: Optional[str], default: str = "1m") -> Tuple[int, str]:
    raw = (step or default).strip()
    match = _STEP_RE.match(raw)
    if not match:
        raise ValidationError(f"invalid step {raw!r}: use e.g. 15s, 1m, 5m, 1h, 1d")
    seconds = int(match.group(1)) * _UNITS[match.group(2)]
    if seconds < 1:
        raise ValidationError("step must be at least 1s")
    return seconds, raw


def parse_relative(raw: str) -> Optional[int]:
    """
    Seconds of a relative time ``-15m`` / ``now-1h`` / ``now - 2 d``, else None.

    Hand-written (no regex) so the cost stays linear in the input length.
    """
    body = raw.strip()
    if body.startswith("now"):
        body = body[3:].lstrip()
    if not body.startswith("-"):
        return None
    body = body[1:].strip()
    if len(body) < 2 or body[-1] not in _UNITS:
        return None
    number = body[:-1].rstrip()
    if not (number.isascii() and number.isdigit()):
        return None
    return int(number) * _UNITS[body[-1]]


def parse_time(value: Optional[str], default: datetime) -> datetime:
    """ISO 8601 (``Z`` allowed), ``now``, or relative ``-15m`` / ``now-1h``."""
    if value is None or str(value).strip() == "":
        return default
    raw = str(value).strip()
    if raw == "now":
        return datetime.now(timezone.utc)
    rel = parse_relative(raw)
    if rel is not None:
        return datetime.now(timezone.utc) - timedelta(seconds=rel)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"invalid timestamp {raw!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def time_window(
    from_: Optional[str],
    to: Optional[str],
    step: Optional[str],
    default_step: str = "1m",
) -> Tuple[datetime, datetime, int, str]:
    end = parse_time(to, datetime.now(timezone.utc))
    start = parse_time(from_, end - timedelta(hours=1))
    if start >= end:
        raise ValidationError("'from' must be before 'to'")
    step_seconds, step_label = parse_step(step, default_step)
    if (end - start).total_seconds() / step_seconds > settings.METRICS_QUERY_MAX_POINTS:
        raise ValidationError(
            f"too many points: widen step (max {settings.METRICS_QUERY_MAX_POINTS} per series)"
        )
    return start, end, step_seconds, step_label


def bucket_grid(start: datetime, end: datetime, step_seconds: int) -> List[datetime]:
    offset = (start - EPOCH_2000).total_seconds()
    first = EPOCH_2000 + timedelta(seconds=(offset // step_seconds) * step_seconds)
    out = []
    current = first
    while current < end:
        out.append(current)
        current += timedelta(seconds=step_seconds)
    return out


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _substitute(final: str, exprs: Dict[str, str]) -> str:
    out = final
    for alias, expr in exprs.items():
        out = re.sub(rf"\b{re.escape(alias)}\b", f"({expr})", out)
    return out


def _bucket_key(value: Any) -> datetime:
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ==========================================
# Series query builder
# ==========================================


def build_series_sql(
    table: str,
    entity_col: str,
    specs: Sequence[MetricSpec],
    source: str,
    aggregate: str,
    where: List[str],
    join_sql: str = "",
    label_cols: Sequence[str] = ("node_id",),
) -> str:
    """
    Two-level aggregation: per (bucket, entity) inner values, then per bucket
    (``aggregate != none``) or per (bucket, entity) outer values ``m{i}``.
    """
    bucket = "date_bin(make_interval(secs => :step), t.ts, TIMESTAMPTZ '2000-01-01 00:00:00+00')"
    inner = [f"{bucket} AS bucket", f"t.{entity_col} AS entity_id"]
    inner += [f"(array_agg(t.{c}))[1] AS {c}" for c in label_cols]
    outer = []
    for i, spec in enumerate(specs):
        exprs = spec.rollup if source == "rollup" else spec.raw
        assert exprs is not None
        inner.append(f"{_substitute(spec.final, exprs)} AS m{i}_v")
        if spec.ratio_of_sums:
            for alias, expr in exprs.items():
                inner.append(f"{expr} AS m{i}_{alias}")
        if aggregate == "none":
            outer.append(f"MAX(m{i}_v) AS m{i}")
        elif spec.ratio_of_sums:
            outer.append(f"{_substitute(spec.final, {a: f'SUM(m{i}_{a})' for a in exprs})} AS m{i}")
        else:
            outer.append(f"{AGGREGATES[aggregate]}(m{i}_v) AS m{i}")
    inner_sql = (
        f"SELECT {', '.join(inner)} FROM {table} t {join_sql} "
        f"WHERE {' AND '.join(where)} GROUP BY 1, 2"
    )
    if aggregate == "none":
        labels = ", ".join(f"(array_agg({c}))[1] AS {c}" for c in label_cols)
        select = f"bucket, entity_id{', ' + labels if labels else ''}, {', '.join(outer)}"
        group = "bucket, entity_id"
    else:
        select = f"bucket, {', '.join(outer)}"
        group = "bucket"
    return f"WITH per AS ({inner_sql}) SELECT {select} FROM per GROUP BY {group} ORDER BY bucket"


class MetricsService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = MetricRepository(session)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "MetricsService":
        return MetricsService(db)

    # ---------- catalog ----------

    @staticmethod
    def catalog() -> List[Dict[str, Any]]:
        return catalog.catalog_entries()

    # ---------- generic series ----------

    async def _series(
        self,
        scope: str,
        specs: List[MetricSpec],
        start: datetime,
        end: datetime,
        step_seconds: int,
        aggregate: str,
        where: List[str],
        params: Dict[str, Any],
        join_sql: str,
        label_cols: Sequence[str],
        entity_label: str,
    ) -> List[Dict[str, Any]]:
        if aggregate not in ("none", "sum", "avg", "max"):
            raise ValidationError("aggregate must be one of none, sum, avg, max")
        raw_cutoff = datetime.now(timezone.utc) - timedelta(
            hours=settings.METRICS_RAW_RETENTION_HOURS
        )
        prefer_rollup = step_seconds >= 60 or start < raw_cutoff
        groups: Dict[str, List[MetricSpec]] = {"raw": [], "rollup": []}
        for spec in specs:
            if spec.rollup and (prefer_rollup or not spec.raw):
                groups["rollup"].append(spec)
            elif spec.raw:
                groups["raw"].append(spec)
        grid = bucket_grid(start, end, step_seconds)
        base_params = {
            **params,
            "start": start,
            "end": end,
            "step": float(step_seconds),
        }
        series: List[Dict[str, Any]] = []
        for source, group_specs in groups.items():
            if not group_specs:
                continue
            table = TABLES[(scope, source)]
            sql = build_series_sql(
                table,
                "device_id" if scope == "gpu" else "instance_id",
                group_specs,
                source,
                aggregate,
                ["t.ts >= :start", "t.ts < :end"] + where,
                join_sql,
                label_cols,
            )
            rows = (await self.session.execute(text(sql), base_params)).mappings().all()
            series.extend(
                _rows_to_series(rows, group_specs, grid, aggregate, label_cols, entity_label)
            )
        order = {spec.name: i for i, spec in enumerate(specs)}
        series.sort(key=lambda s: (order.get(s["metric"], 0), sorted(s["labels"].items())))
        return series

    async def gpu(
        self,
        from_: Optional[str],
        to: Optional[str],
        step: Optional[str],
        metrics: Optional[List[str]],
        cluster_id: Optional[uuid.UUID],
        node_id: Optional[uuid.UUID],
        device_ids: Optional[List[uuid.UUID]],
        aggregate: str = "none",
    ) -> Dict[str, Any]:
        start, end, step_seconds, step_label = time_window(from_, to, step)
        resolved = catalog.resolve("gpu", metrics, catalog.DEFAULT_GPU)
        if resolved.unknown:
            raise ValidationError(f"unknown gpu metrics: {', '.join(resolved.unknown)}")
        where: List[str] = []
        params: Dict[str, Any] = {}
        join_sql = ""
        if cluster_id:
            join_sql = "JOIN nodes n ON n.id = t.node_id"
            where.append("n.cluster_id = :cluster_id")
            params["cluster_id"] = cluster_id
        if node_id:
            where.append("t.node_id = :node_id")
            params["node_id"] = node_id
        if device_ids:
            where.append("t.device_id = ANY(:device_ids)")
            params["device_ids"] = list(device_ids)
        series = await self._series(
            "gpu",
            resolved.specs,
            start,
            end,
            step_seconds,
            aggregate or "none",
            where,
            params,
            join_sql,
            ("node_id",),
            "device_id",
        )
        if (aggregate or "none") == "none":
            await self._label_devices(series)
        return {
            "from": _iso(start),
            "to": _iso(end),
            "step": step_label,
            "series": series,
        }

    async def inference(
        self,
        from_: Optional[str],
        to: Optional[str],
        step: Optional[str],
        metrics: Optional[List[str]],
        model_id: Optional[uuid.UUID],
        deployment_id: Optional[uuid.UUID],
        instance_ids: Optional[List[uuid.UUID]],
        node_id: Optional[uuid.UUID],
        aggregate: str = "none",
    ) -> Dict[str, Any]:
        start, end, step_seconds, step_label = time_window(from_, to, step)
        resolved = catalog.resolve("engine", metrics, catalog.DEFAULT_INFERENCE)
        if resolved.unknown:
            raise ValidationError(f"unknown inference metrics: {', '.join(resolved.unknown)}")
        where: List[str] = []
        params: Dict[str, Any] = {}
        join_sql = ""
        if model_id:
            join_sql = "JOIN deployments d ON d.id = t.deployment_id"
            where.append("d.model_id = :model_id")
            params["model_id"] = model_id
        if deployment_id:
            where.append("t.deployment_id = :deployment_id")
            params["deployment_id"] = deployment_id
        if instance_ids:
            where.append("t.instance_id = ANY(:instance_ids)")
            params["instance_ids"] = list(instance_ids)
        if node_id:
            where.append("t.node_id = :node_id")
            params["node_id"] = node_id
        series = await self._series(
            "engine",
            resolved.specs,
            start,
            end,
            step_seconds,
            aggregate or "none",
            where,
            params,
            join_sql,
            ("node_id", "deployment_id"),
            "instance_id",
        )
        if (aggregate or "none") == "none":
            await self._label_instances(series)
        return {
            "from": _iso(start),
            "to": _iso(end),
            "step": step_label,
            "series": series,
        }

    async def _label_devices(self, series: List[Dict[str, Any]]) -> None:
        ids = list({s["labels"]["device_id"] for s in series if s["labels"].get("device_id")})
        if not ids:
            return
        rows = (
            await self.session.execute(
                text(
                    "SELECT d.id::text AS id, d.name, d.vendor::text AS vendor, d.index, "
                    "n.name AS node_name FROM devices d JOIN nodes n ON n.id = d.node_id "
                    "WHERE d.id::text = ANY(:ids)"
                ),
                {"ids": ids},
            )
        ).mappings()
        info = {r["id"]: r for r in rows.all()}
        for s in series:
            r = info.get(s["labels"].get("device_id"))
            if r:
                s["labels"].update(
                    device_name=r["name"],
                    vendor=r["vendor"],
                    index=str(r["index"]),
                    node=r["node_name"],
                )

    async def _label_instances(self, series: List[Dict[str, Any]]) -> None:
        ids = list({s["labels"]["instance_id"] for s in series if s["labels"].get("instance_id")})
        if not ids:
            return
        rows = (
            await self.session.execute(
                # Observed (external) deployments have no catalog model: LEFT JOIN, and the
                # served model name stands in for the model so their series still get labels.
                text(
                    "SELECT i.id::text AS id, d.name AS deployment, "
                    "COALESCE(m.name, i.served_model_name) AS model, "
                    "i.served_model_name AS served_model, n.name AS node_name FROM instances i "
                    "JOIN deployments d ON d.id = i.deployment_id "
                    "LEFT JOIN models m ON m.id = d.model_id "
                    "LEFT JOIN nodes n ON n.id = i.node_id "
                    "WHERE i.id::text = ANY(:ids)"
                ),
                {"ids": ids},
            )
        ).mappings()
        info = {r["id"]: r for r in rows.all()}
        for s in series:
            r = info.get(s["labels"].get("instance_id"))
            if r:
                extra = {
                    "deployment": r["deployment"],
                    "model": r["model"],
                    "served_model": r["served_model"],
                    "node": r["node_name"],
                }
                s["labels"].update({k: v for k, v in extra.items() if v is not None})

    # ---------- sessions ----------

    async def sessions(
        self,
        instance_id: uuid.UUID,
        from_: Optional[str],
        to: Optional[str],
        step: Optional[str],
        session_key: Optional[str],
        request_id: Optional[uuid.UUID],
    ) -> Dict[str, Any]:
        start, end, step_seconds, step_label = time_window(from_, to, step)
        where = ["instance_id = :instance_id", "ts >= :start", "ts < :end"]
        params: Dict[str, Any] = {
            "instance_id": instance_id,
            "start": start,
            "end": end,
            "step": float(step_seconds),
        }
        if session_key:
            where.append("session_key = :session_key")
            params["session_key"] = session_key
        if request_id:
            where.append("request_id = :request_id")
            params["request_id"] = request_id
        cols = ", ".join(f"MAX({f}) AS {f}" for f in SESSION_FIELDS)
        sql = (
            "SELECT date_bin(make_interval(secs => :step), ts,"
            " TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket,"
            " session_key, (array_agg(slot_id ORDER BY ts DESC))[1] AS slot_id,"
            " (array_agg(request_id ORDER BY ts DESC))[1] AS request_id,"
            f" {cols} FROM metric_samples_session WHERE {' AND '.join(where)}"
            " GROUP BY 1, 2 ORDER BY 1"
        )
        rows = (await self.session.execute(text(sql), params)).mappings().all()
        grid = bucket_grid(start, end, step_seconds)
        by_key: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            entry = by_key.setdefault(
                row["session_key"],
                {
                    "slot_id": row["slot_id"],
                    "request_id": row["request_id"],
                    "values": {},
                },
            )
            if row["request_id"] and not entry["request_id"]:
                entry["request_id"] = row["request_id"]
            entry["values"][_bucket_key(row["bucket"])] = row
        series = []
        units = {s.name: s.unit for s in catalog.SESSION_METRICS}
        for key in list(by_key)[:200]:
            entry = by_key[key]
            labels = {"instance_id": str(instance_id), "session_key": key}
            if entry["slot_id"] is not None:
                labels["slot_id"] = str(entry["slot_id"])
            if entry["request_id"]:
                labels["request_id"] = str(entry["request_id"])
            for field_name in SESSION_FIELDS:
                points = []
                for b in grid:
                    row = entry["values"].get(b)
                    points.append([_iso(b), _num(row[field_name]) if row else None])
                series.append(
                    {
                        "labels": dict(labels),
                        "metric": field_name,
                        "unit": units.get(field_name, ""),
                        "points": points,
                    }
                )
        return {
            "from": _iso(start),
            "to": _iso(end),
            "step": step_label,
            "series": series,
        }

    async def sessions_live(
        self,
        deployment_id: Optional[uuid.UUID],
        node_id: Optional[uuid.UUID],
        instance_id: Optional[uuid.UUID],
    ) -> List[Dict[str, Any]]:
        where = ["state IN ('loading','ready','draining')"]
        params: Dict[str, Any] = {}
        if deployment_id:
            where.append("deployment_id = :deployment_id")
            params["deployment_id"] = deployment_id
        if node_id:
            where.append("node_id = :node_id")
            params["node_id"] = node_id
        if instance_id:
            where.append("id = :instance_id")
            params["instance_id"] = instance_id
        ids = [
            r[0]
            for r in (
                await self.session.execute(
                    text(
                        f"SELECT id FROM instances WHERE {' AND '.join(where)} ORDER BY created_at"
                    ),
                    params,
                )
            ).all()
        ]
        if instance_id and not ids:
            exists = (
                await self.session.execute(
                    text("SELECT 1 FROM instances WHERE id = :id"), {"id": instance_id}
                )
            ).first()
            if exists is None:
                raise ResourceNotFoundError(resource_type="Instance")
        snapshots = await live_cache.get_many(ids, "sessions")
        engines = await live_cache.get_many(ids, "engine")
        out = []
        for iid, snap, eng in zip(ids, snapshots, engines):
            fresh = live_cache.is_fresh(snap)
            sessions = (snap or {}).get("sessions") if fresh else None
            ts = (snap or {}).get("ts") if fresh else None
            if sessions is None:
                rows = await self.repo.latest_session_samples(iid)
                sessions = rows
                ts = rows[0]["ts"] if rows else None
            eng = eng if live_cache.is_fresh(eng) else {}
            sessions = [_session_sample(s, iid) for s in sessions or []]
            active = eng.get("active_sessions")
            waiting = eng.get("waiting_sessions")
            out.append(
                {
                    "ts": ts or datetime.now(timezone.utc),
                    "instance_id": iid,
                    "active": int(
                        active
                        if active is not None
                        else sum(1 for s in sessions if s["state"] in ("processing", "generating"))
                    ),
                    "waiting": int(
                        waiting
                        if waiting is not None
                        else sum(1 for s in sessions if s["state"] == "waiting")
                    ),
                    "slots_total": eng.get("slots_total"),
                    "sessions": sessions,
                }
            )
        return out

    # ---------- usage ----------

    async def usage(
        self,
        from_: Optional[str],
        to: Optional[str],
        step: Optional[str],
        metrics: Optional[List[str]],
        api_key_id: Optional[uuid.UUID],
        model_id: Optional[uuid.UUID],
        node_id: Optional[uuid.UUID],
        deployment_id: Optional[uuid.UUID],
        group_by: str = "none",
    ) -> Dict[str, Any]:
        end = parse_time(to, datetime.now(timezone.utc))
        start = parse_time(from_, end - timedelta(days=1))
        if start >= end:
            raise ValidationError("'from' must be before 'to'")
        step_seconds, step_label = parse_step(step, "1h")
        step_seconds = max(step_seconds, 3600)  # hourly source table
        if (end - start).total_seconds() / step_seconds > settings.METRICS_QUERY_MAX_POINTS:
            raise ValidationError("too many points: widen step")
        resolved = catalog.resolve("usage", metrics, catalog.DEFAULT_USAGE)
        if resolved.unknown:
            raise ValidationError(f"unknown usage metrics: {', '.join(resolved.unknown)}")
        group_col = {
            "none": None,
            "api_key": "api_key_id",
            "model": "model_id",
            "node": "node_id",
            "deployment": "deployment_id",
        }.get(group_by or "none", "__bad__")
        if group_col == "__bad__":
            raise ValidationError("group_by must be none, api_key, model, node or deployment")
        where = ["bucket >= :start", "bucket < :end"]
        params: Dict[str, Any] = {
            "start": start,
            "end": end,
            "step": float(step_seconds),
        }
        for col, value in (
            ("api_key_id", api_key_id),
            ("model_id", model_id),
            ("node_id", node_id),
            ("deployment_id", deployment_id),
        ):
            if value:
                where.append(f"{col} = :{col}")
                params[col] = value
        b = "date_bin(make_interval(secs => :step), bucket, TIMESTAMPTZ '2000-01-01 00:00:00+00')"
        selects = [f"{spec.rollup['v']} AS m{i}" for i, spec in enumerate(resolved.specs)]
        group_select = f"{group_col}::text AS group_id" if group_col else "NULL::text AS group_id"
        group_clause = f"1, {group_col}" if group_col else "1"
        sql = (
            f"SELECT {b} AS b, {group_select}, {', '.join(selects)} FROM usage_rollups_hourly "
            f"WHERE {' AND '.join(where)} GROUP BY {group_clause} ORDER BY 1"
        )
        rows = (await self.session.execute(text(sql), params)).mappings().all()
        grid = bucket_grid(start, end, step_seconds)
        groups: Dict[Optional[str], Dict[datetime, Any]] = {}
        for row in rows:
            groups.setdefault(row["group_id"], {})[_bucket_key(row["b"])] = row
        if not groups and group_col is None:
            groups[None] = {}
        series = []
        for i, spec in enumerate(resolved.specs):
            for group_id, values in groups.items():
                labels = {"group_by": group_by or "none"}
                if group_col:
                    labels[group_col] = group_id or "unknown"
                series.append(
                    {
                        "labels": labels,
                        "metric": spec.name,
                        "unit": spec.unit,
                        "points": [
                            [_iso(g), _num(values[g][f"m{i}"]) if g in values else None]
                            for g in grid
                        ],
                    }
                )
        return {
            "from": _iso(start),
            "to": _iso(end),
            "step": step_label,
            "series": series,
        }

    # ---------- overview ----------

    async def overview(self) -> Dict[str, Any]:
        counts = await self.repo.overview_counts()
        gpu = list((await live_cache.get_all("gpu")).values())
        engine = list((await live_cache.get_all("engine")).values())
        snapshot = await fleet_rollup_snapshot(None, self.session)
        live = live_cache.overview_live(
            gpu,
            engine,
            vram_total_bytes=snapshot["vram_total_bytes"],
            requests_per_minute=snapshot["requests_per_minute"],
            error_rate_pct=snapshot["error_rate_pct"],
        )
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        return {
            "counts": counts,
            "live": live,
            "cost_today_usd": await self.repo.cost_since(day_start),
            "cost_month_usd": await self.repo.cost_since(month_start),
            "recent_alerts": await self.repo.recent_rows("alert_events", "last_seen_at"),
            "recent_swaps": await self.repo.recent_rows("swap_operations", "created_at"),
            "recent_rule_runs": await self.repo.recent_rows("rule_runs", "evaluated_at"),
        }


def _rows_to_series(
    rows: Sequence[Any],
    specs: Sequence[MetricSpec],
    grid: List[datetime],
    aggregate: str,
    label_cols: Sequence[str],
    entity_label: str,
) -> List[Dict[str, Any]]:
    if aggregate != "none":
        values = {_bucket_key(r["bucket"]): r for r in rows}
        return [
            {
                "labels": {"aggregate": aggregate},
                "metric": spec.name,
                "unit": spec.unit,
                "points": [
                    [_iso(b), _num(values[b][f"m{i}"]) if b in values else None] for b in grid
                ],
            }
            for i, spec in enumerate(specs)
        ]
    entities: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        entity = entities.setdefault(
            str(row["entity_id"]),
            {
                "labels": {c: str(row[c]) for c in label_cols if row[c] is not None},
                "values": {},
            },
        )
        entity["values"][_bucket_key(row["bucket"])] = row
    out = []
    for i, spec in enumerate(specs):
        for entity_id, entity in entities.items():
            out.append(
                {
                    "labels": {entity_label: entity_id, **entity["labels"]},
                    "metric": spec.name,
                    "unit": spec.unit,
                    "points": [
                        (
                            [_iso(b), _num(entity["values"][b][f"m{i}"])]
                            if b in entity["values"]
                            else [_iso(b), None]
                        )
                        for b in grid
                    ],
                }
            )
    return out


def _session_sample(sample: Dict[str, Any], instance_id: Any) -> Dict[str, Any]:
    fields = (
        "slot_id",
        "session_key",
        "request_id",
        "state",
        "prompt_tokens",
        "generated_tokens",
        "cached_tokens",
        "kv_cache_tokens",
        "kv_cache_bytes",
        "context_size",
        "ttft_ms",
        "elapsed_ms",
        "tokens_per_second",
        "extra",
    )
    out = {k: sample.get(k) for k in fields}
    out["ts"] = sample.get("ts")
    out["instance_id"] = str(sample.get("instance_id") or instance_id)
    out["state"] = out["state"] or "idle"
    out["session_key"] = str(out["session_key"] or "")
    return out


async def fleet_rollup_snapshot(
    flt: Any = None, session: Optional[AsyncSession] = None
) -> Dict[str, Any]:
    """Requests/min + error rate of the last complete minute and VRAM total (scoped)."""
    node_ids = None
    instance_id = deployment_id = None
    if flt is not None:
        node_ids = list(flt.node_ids) if getattr(flt, "node_ids", None) is not None else None
        instance_id = getattr(flt, "instance_id", None)
        deployment_id = getattr(flt, "deployment_id", None)

    async def _run(s: AsyncSession) -> Dict[str, Any]:
        return await MetricRepository(s).rollup_snapshot(node_ids, deployment_id, instance_id)

    try:
        if session is not None:
            return await _run(session)
        async with get_session_maker()() as own:
            return await _run(own)
    except Exception as exc:  # noqa: BLE001 - live views degrade to nulls
        logger.debug(f"rollup snapshot failed: {exc}")
        return {
            "requests_per_minute": None,
            "error_rate_pct": None,
            "vram_total_bytes": 0,
        }
