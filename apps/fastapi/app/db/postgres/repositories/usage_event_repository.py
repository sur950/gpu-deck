"""
Usage events repository (``usage_events``, partitioned by day).

Writes come only from the metering Celery task (``insert``). Reads serve the
usage drill-down (``list_events``), event-level summaries (``summary`` — used
when a grouping or filter is not available in the hourly rollups) and the
rollup job (``bucket_sums``, ``token_minutes``).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

EVENT_COLUMNS = (
    "ts",
    "request_id",
    "api_key_id",
    "model_id",
    "model_name",
    "deployment_id",
    "instance_id",
    "node_id",
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
    "metadata",
)

_CASTS = {
    "format": "CAST(:format AS usage_format)",
    "status": "CAST(:status AS usage_status)",
    "ip_address": "CAST(:ip_address AS INET)",
    "metadata": "CAST(:metadata AS JSONB)",
}

_INSERT_SQL = text(
    f"INSERT INTO usage_events ({', '.join(EVENT_COLUMNS)}) VALUES ("
    + ", ".join(_CASTS.get(c, f":{c}") for c in EVENT_COLUMNS)
    + ") RETURNING id"
)

# group_by key -> (SQL expression for the group value, SQL expression for its label)
EVENT_GROUPS = {
    "api_key": ("e.api_key_id::text", "MAX(ak.name)"),
    "model": ("COALESCE(e.model_id::text, e.model_name)", "MAX(e.model_name)"),
    "node": ("e.node_id::text", "MAX(n.name)"),
    "deployment": ("e.deployment_id::text", "MAX(d.name)"),
    "cluster": ("COALESCE(e.cluster_id, n.cluster_id)::text", "MAX(c.name)"),
    "format": ("e.format::text", "MAX(e.format::text)"),
    "endpoint": ("e.endpoint", "MAX(e.endpoint)"),
    "day": (
        "to_char(date_trunc('day', e.ts AT TIME ZONE 'UTC'), 'YYYY-MM-DD\"T\"00:00:00\"Z\"')",
        None,
    ),
    "hour": (
        "to_char(date_trunc('hour', e.ts AT TIME ZONE 'UTC'), 'YYYY-MM-DD\"T\"HH24:00:00\"Z\"')",
        None,
    ),
}

_EVENT_JOINS = (
    "LEFT JOIN api_keys ak ON ak.id = e.api_key_id "
    "LEFT JOIN nodes n ON n.id = e.node_id "
    "LEFT JOIN deployments d ON d.id = e.deployment_id "
    "LEFT JOIN clusters c ON c.id = COALESCE(e.cluster_id, n.cluster_id)"
)

_SORTS = {
    "ts": "e.ts",
    "latency_ms": "e.latency_ms",
    "cost_usd": "e.cost_usd",
    "input_tokens": "e.input_tokens",
    "output_tokens": "e.output_tokens",
    "model_name": "e.model_name",
}


def event_where(filters: Dict[str, Any], alias: str = "e") -> Tuple[str, Dict[str, Any]]:
    """WHERE clause + params for the usage filters of admin.yaml (from/to required)."""
    clauses = [f"{alias}.ts >= :from_ts", f"{alias}.ts < :to_ts"]
    params: Dict[str, Any] = {"from_ts": filters["from"], "to_ts": filters["to"]}
    for key in ("api_key_id", "model_id", "node_id", "deployment_id", "request_id"):
        if filters.get(key):
            clauses.append(f"{alias}.{key} = :{key}")
            params[key] = filters[key]
    if filters.get("cluster_id"):
        clauses.append(
            f"({alias}.cluster_id = :cluster_id OR {alias}.node_id IN "
            "(SELECT id FROM nodes WHERE cluster_id = :cluster_id))"
        )
        params["cluster_id"] = filters["cluster_id"]
    if filters.get("format"):
        clauses.append(f"{alias}.format = CAST(:format AS usage_format)")
        params["format"] = filters["format"]
    if filters.get("status"):
        clauses.append(f"{alias}.status = CAST(:status AS usage_status)")
        params["status"] = filters["status"]
    if filters.get("endpoint"):
        clauses.append(f"{alias}.endpoint = :endpoint")
        params["endpoint"] = filters["endpoint"]
    return " AND ".join(clauses), params


# usage_events rows that are serving requests (not SSH GPU instance metering windows).
GPU_INSTANCE_ENDPOINT = "gpu_instance"
_REQ = f"endpoint IS DISTINCT FROM '{GPU_INSTANCE_ENDPOINT}'"


class UsageEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------ writes

    async def insert(self, row: Dict[str, Any]) -> int:
        """Insert one event; creates the day partition and retries once on SQLSTATE 23514."""
        params = {c: row.get(c) for c in EVENT_COLUMNS}
        if params["metadata"] is not None and not isinstance(params["metadata"], str):
            params["metadata"] = json.dumps(params["metadata"], default=str)
        try:
            async with self.session.begin_nested():
                result = await self.session.execute(_INSERT_SQL, params)
                return int(result.scalar_one())
        except DBAPIError as exc:
            if _sqlstate(exc) != "23514":
                raise
            logger.info("usage_events partition missing; creating daily partitions")
            await self.session.execute(text("SELECT create_daily_partitions(3, 1)"))
            async with self.session.begin_nested():
                result = await self.session.execute(_INSERT_SQL, params)
                return int(result.scalar_one())

    # ------------------------------------------------------------------- reads

    async def list_events(
        self, filters: Dict[str, Any], page: int, page_size: int, sort: Optional[str]
    ) -> Tuple[List[Dict[str, Any]], int]:
        where, params = event_where(filters)
        field = (sort or "-ts").lstrip("-")
        direction = "DESC" if (sort or "-ts").startswith("-") else "ASC"
        order = _SORTS.get(field, "e.ts")
        total = (
            await self.session.execute(
                text(f"SELECT COUNT(*) FROM usage_events e WHERE {where}"), params
            )
        ).scalar_one()
        result = await self.session.execute(
            text(
                f"SELECT e.*, e.ip_address::text AS ip_text, ak.name AS api_key_name, "
                f"n.name AS node_name FROM usage_events e "
                f"LEFT JOIN api_keys ak ON ak.id = e.api_key_id "
                f"LEFT JOIN nodes n ON n.id = e.node_id "
                f"WHERE {where} ORDER BY {order} {direction}, e.id {direction} "
                f"LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )
        rows = []
        for r in result.mappings().all():
            row = dict(r)
            row["ip_address"] = row.pop("ip_text", None)
            rows.append(row)
        return rows, int(total)

    async def summary(self, filters: Dict[str, Any], group_by: List[str]) -> List[Dict[str, Any]]:
        """Totals grouped by any of EVENT_GROUPS, straight from the events."""
        where, params = event_where(filters)
        selects, groups = [], []
        for idx, key in enumerate(group_by):
            expr, label = EVENT_GROUPS[key]
            selects.append(f"{expr} AS g{idx}")
            selects.append(f"{label or f'MAX({expr})'} AS l{idx}")
            groups.append(f"g{idx}")
        sql = (
            f"SELECT {', '.join(selects + [_EVENT_AGGREGATES])} FROM usage_events e {_EVENT_JOINS} "
            f"WHERE {where}"
            + (
                f" GROUP BY {', '.join(groups)} ORDER BY cost_usd DESC, request_count DESC"
                if groups
                else ""
            )
        )
        result = await self.session.execute(text(sql), params)
        return [summary_row(dict(r), group_by) for r in result.mappings().all()]

    async def bucket_sums(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """Per (api_key, model, node, deployment) sums for the rollup of [start, end)."""
        result = await self.session.execute(
            text(
                # endpoint 'gpu_instance' rows are metered windows of SSH GPU instances
                #: billed as GPU time (instance_gpu_seconds), never counted as
                # requests or latency.
                "SELECT api_key_id, model_id, node_id, deployment_id, "
                f"SUM(CASE WHEN {_REQ} THEN request_count ELSE 0 END) AS request_count, "
                f"SUM(CASE WHEN {_REQ} AND status = 'success' THEN request_count ELSE 0 END) "
                "AS success_count, "
                f"SUM(CASE WHEN {_REQ} AND status <> 'success' THEN request_count ELSE 0 END) "
                "AS error_count, "
                "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
                "SUM(cached_tokens) AS cached_tokens, SUM(reasoning_tokens) AS reasoning_tokens, "
                "SUM(media_units) AS media_units, "
                f"SUM(CASE WHEN {_REQ} THEN gpu_seconds ELSE 0 END) AS busy_gpu_seconds, "
                f"SUM(CASE WHEN {_REQ} THEN 0 ELSE gpu_seconds END) AS instance_gpu_seconds, "
                f"SUM(CASE WHEN {_REQ} THEN COALESCE(latency_ms, 0) ELSE 0 END) AS latency_ms_sum, "
                "SUM(COALESCE(ttft_ms, 0)) AS ttft_ms_sum, SUM(cost_usd) AS token_cost_usd, "
                "MAX(ts) AS last_ts "
                "FROM usage_events WHERE ts >= :start AND ts < :end "
                "GROUP BY api_key_id, model_id, node_id, deployment_id"
            ),
            {"start": start, "end": end},
        )
        return [dict(r) for r in result.mappings().all()]

    async def token_minutes(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """Tokens processed per (minute, instance, api key) — the GPU-minute allocation weights."""
        result = await self.session.execute(
            text(
                "SELECT date_trunc('minute', ts) AS minute, instance_id, api_key_id, "
                "SUM(input_tokens + output_tokens) AS tokens, SUM(request_count) AS requests "
                "FROM usage_events WHERE ts >= :start AND ts < :end AND instance_id IS NOT NULL "
                "GROUP BY 1, 2, 3"
            ),
            {"start": start, "end": end},
        )
        return [dict(r) for r in result.mappings().all()]

    async def last_seen(self, start: datetime, end: datetime) -> Dict[str, List[Dict[str, Any]]]:
        """Latest request time per key / deployment / instance in [start, end)."""
        out: Dict[str, List[Dict[str, Any]]] = {}
        for column in ("api_key_id", "deployment_id", "instance_id"):
            result = await self.session.execute(
                text(
                    f"SELECT {column} AS id, MAX(ts) AS last_ts FROM usage_events "
                    f"WHERE ts >= :start AND ts < :end AND {column} IS NOT NULL GROUP BY {column}"
                ),
                {"start": start, "end": end},
            )
            out[column] = [dict(r) for r in result.mappings().all()]
        return out

    async def drop_old_partitions(self, retain_days: int) -> int:
        result = await self.session.execute(
            text("SELECT drop_old_partitions('usage_events', :days)"),
            {"days": retain_days},
        )
        return int(result.scalar_one() or 0)


_EVENT_AGGREGATES = (
    "COALESCE(SUM(e.request_count), 0) AS request_count, "
    "COALESCE(SUM(CASE WHEN e.status = 'success' THEN e.request_count ELSE 0 END), 0) "
    "AS success_count, "
    "COALESCE(SUM(CASE WHEN e.status <> 'success' THEN e.request_count ELSE 0 END), 0) "
    "AS error_count, "
    "COALESCE(SUM(e.input_tokens), 0) AS input_tokens, "
    "COALESCE(SUM(e.output_tokens), 0) AS output_tokens, "
    "COALESCE(SUM(e.cached_tokens), 0) AS cached_tokens, "
    "COALESCE(SUM(e.reasoning_tokens), 0) AS reasoning_tokens, "
    "COALESCE(SUM(e.media_units), 0) AS media_units, "
    "COALESCE(SUM(e.gpu_seconds), 0) AS gpu_seconds, "
    "AVG(e.latency_ms) AS latency_ms_avg, AVG(e.ttft_ms) AS ttft_ms_avg, "
    "COALESCE(SUM(e.cost_usd), 0) AS cost_usd"
)


def totals_from_row(r: Dict[str, Any]) -> Dict[str, Any]:
    gpu_seconds = float(r.get("gpu_seconds") or 0)
    return {
        "request_count": int(r.get("request_count") or 0),
        "success_count": int(r.get("success_count") or 0),
        "error_count": int(r.get("error_count") or 0),
        "input_tokens": int(r.get("input_tokens") or 0),
        "output_tokens": int(r.get("output_tokens") or 0),
        "cached_tokens": int(r.get("cached_tokens") or 0),
        "reasoning_tokens": int(r.get("reasoning_tokens") or 0),
        "media_units": round(float(r.get("media_units") or 0), 3),
        "gpu_seconds": round(gpu_seconds, 3),
        "gpu_minutes": round(gpu_seconds / 60.0, 4),
        "latency_ms_avg": (
            round(float(r["latency_ms_avg"]), 2) if r.get("latency_ms_avg") is not None else None
        ),
        "ttft_ms_avg": (
            round(float(r["ttft_ms_avg"]), 2) if r.get("ttft_ms_avg") is not None else None
        ),
        "cost_usd": round(float(r.get("cost_usd") or 0), 8),
    }


def summary_row(r: Dict[str, Any], group_by: List[str]) -> Dict[str, Any]:
    group: Dict[str, str] = {}
    labels: Dict[str, str] = {}
    for idx, key in enumerate(group_by):
        value = r.get(f"g{idx}")
        group[key] = "" if value is None else str(value)
        label = r.get(f"l{idx}")
        labels[key] = "" if label is None else str(label)
        if key == "api_key" and value is None:
            labels[key] = "unallocated"
    return {"group": group, "labels": labels, "totals": totals_from_row(r)}


def _sqlstate(exc: DBAPIError) -> Optional[str]:
    orig = getattr(exc, "orig", None)
    for attr in ("sqlstate", "pgcode"):
        code = getattr(orig, attr, None) or getattr(getattr(orig, "__cause__", None), attr, None)
        if code:
            return str(code)
    return "23514" if "no partition of relation" in str(exc) else None
