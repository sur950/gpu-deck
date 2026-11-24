"""
Hourly usage rollups (``usage_rollups_hourly``).

One row per (bucket hour, api key, model, node, deployment). ``gpu_seconds``
holds the *billable* GPU time allocated from instance uptime
(uptime-based, see metering/rollup.py); rows with ``api_key_id IS NULL`` are the
``unallocated`` bucket (GPU time with no traffic). ``cost_usd`` = token cost
of the events (priced at write time) + allocated GPU time priced with the
node's plan when the rollup ran.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.repositories.usage_event_repository import summary_row
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

ROLLUP_COLUMNS = (
    "bucket",
    "api_key_id",
    "model_id",
    "node_id",
    "deployment_id",
    "request_count",
    "success_count",
    "error_count",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "media_units",
    "gpu_seconds",
    "latency_ms_sum",
    "ttft_ms_sum",
    "cost_usd",
)

ROLLUP_GROUPS = {
    "api_key": ("r.api_key_id::text", "MAX(ak.name)"),
    "model": ("r.model_id::text", "MAX(COALESCE(m.display_name, m.name))"),
    "node": ("r.node_id::text", "MAX(n.name)"),
    "deployment": ("r.deployment_id::text", "MAX(d.name)"),
    "cluster": ("COALESCE(n.cluster_id, d.cluster_id)::text", "MAX(c.name)"),
    "day": (
        "to_char(date_trunc('day', r.bucket AT TIME ZONE 'UTC'), 'YYYY-MM-DD\"T\"00:00:00\"Z\"')",
        None,
    ),
    "hour": (
        "to_char(r.bucket AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:00:00\"Z\"')",
        None,
    ),
}

_JOINS = (
    "LEFT JOIN api_keys ak ON ak.id = r.api_key_id "
    "LEFT JOIN models m ON m.id = r.model_id "
    "LEFT JOIN nodes n ON n.id = r.node_id "
    "LEFT JOIN deployments d ON d.id = r.deployment_id "
    "LEFT JOIN clusters c ON c.id = COALESCE(n.cluster_id, d.cluster_id)"
)

_AGGREGATES = (
    "COALESCE(SUM(r.request_count), 0) AS request_count, "
    "COALESCE(SUM(r.success_count), 0) AS success_count, "
    "COALESCE(SUM(r.error_count), 0) AS error_count, "
    "COALESCE(SUM(r.input_tokens), 0) AS input_tokens, "
    "COALESCE(SUM(r.output_tokens), 0) AS output_tokens, "
    "COALESCE(SUM(r.cached_tokens), 0) AS cached_tokens, "
    "COALESCE(SUM(r.reasoning_tokens), 0) AS reasoning_tokens, "
    "COALESCE(SUM(r.media_units), 0) AS media_units, "
    "COALESCE(SUM(r.gpu_seconds), 0) AS gpu_seconds, "
    "SUM(r.latency_ms_sum)::float / NULLIF(SUM(r.request_count), 0) AS latency_ms_avg, "
    "SUM(r.ttft_ms_sum)::float / NULLIF(SUM(r.request_count), 0) AS ttft_ms_avg, "
    "COALESCE(SUM(r.cost_usd), 0) AS cost_usd"
)

# Filters the rollups can answer (anything else -> event-level summary).
ROLLUP_FILTERS = {"api_key_id", "model_id", "node_id", "deployment_id", "cluster_id"}


def rollup_where(filters: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    clauses = [
        "r.bucket >= date_trunc('hour', CAST(:from_ts AS timestamptz))",
        "r.bucket < :to_ts",
    ]
    params: Dict[str, Any] = {"from_ts": filters["from"], "to_ts": filters["to"]}
    for key in ("api_key_id", "model_id", "node_id", "deployment_id"):
        values = filters.get(key)
        if values:
            if isinstance(values, (list, tuple, set)):
                clauses.append(f"r.{key} = ANY(:{key})")
                params[key] = list(values)
            else:
                clauses.append(f"r.{key} = :{key}")
                params[key] = values
    cluster = filters.get("cluster_id")
    if cluster:
        many = isinstance(cluster, (list, tuple, set))
        op = "= ANY(:cluster_id)" if many else "= :cluster_id"
        clauses.append(f"COALESCE(n.cluster_id, d.cluster_id) {op}")
        params["cluster_id"] = list(cluster) if many else cluster
    return " AND ".join(clauses), params


class UsageRollupRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def replace_bucket(self, bucket: datetime, rows: Iterable[Dict[str, Any]]) -> int:
        """Idempotent rebuild of one hour: delete then insert in the caller's transaction."""
        await self.session.execute(
            text("DELETE FROM usage_rollups_hourly WHERE bucket = :b"), {"b": bucket}
        )
        payload = [{c: row.get(c) for c in ROLLUP_COLUMNS} | {"bucket": bucket} for row in rows]
        if payload:
            await self.session.execute(
                text(
                    f"INSERT INTO usage_rollups_hourly ({', '.join(ROLLUP_COLUMNS)}) VALUES ("
                    + ", ".join(f":{c}" for c in ROLLUP_COLUMNS)
                    + ")"
                ),
                payload,
            )
        await self.session.flush()
        return len(payload)

    async def grouped(
        self, filters: Dict[str, Any], group_by: List[str], *, order: str = "cost"
    ) -> List[Dict[str, Any]]:
        """Summary rows ``{group, labels, totals}`` grouped by ROLLUP_GROUPS keys."""
        rows = await self._grouped_raw(filters, group_by, order=order)
        return [summary_row(r, group_by) for r in rows]

    async def _grouped_raw(
        self, filters: Dict[str, Any], group_by: List[str], *, order: str = "cost"
    ) -> List[Dict[str, Any]]:
        where, params = rollup_where(filters)
        selects, groups = [], []
        for idx, key in enumerate(group_by):
            expr, label = ROLLUP_GROUPS[key]
            selects.append(f"{expr} AS g{idx}")
            selects.append(f"{label or f'MAX({expr})'} AS l{idx}")
            groups.append(f"g{idx}")
        order_sql = "cost_usd DESC, request_count DESC" if order == "cost" else ", ".join(groups)
        sql = (
            f"SELECT {', '.join(selects + [_AGGREGATES])} FROM usage_rollups_hourly r {_JOINS} "
            f"WHERE {where}"
            + (
                f" GROUP BY {', '.join(groups)} ORDER BY {order_sql or 'cost_usd DESC'}"
                if groups
                else ""
            )
        )
        result = await self.session.execute(text(sql), params)
        return [dict(r) for r in result.mappings().all()]

    async def priced_lines(
        self, filters: Dict[str, Any], group_by: List[str]
    ) -> List[Dict[str, Any]]:
        """Raw sums per (group..., node) for billing reports priced with current plans."""
        rows = await self._grouped_raw(filters, list(group_by) + ["node"], order="group")
        out = []
        node_idx = len(group_by)
        for r in rows:
            out.append(
                {
                    "group": [r.get(f"g{i}") for i in range(len(group_by))],
                    "labels": [r.get(f"l{i}") for i in range(len(group_by))],
                    "node_id": r.get(f"g{node_idx}"),
                    "raw": r,
                }
            )
        return out

    async def totals(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        rows = await self.grouped(filters, [])
        return rows[0]["totals"] if rows else summary_row({}, [])["totals"]

    async def update_last_seen(self, last_seen: Dict[str, List[Dict[str, Any]]]) -> None:
        """api_keys.last_used_at / deployments / instances.last_request_at (monotonic)."""
        targets = {
            "api_key_id": ("api_keys", "last_used_at"),
            "deployment_id": ("deployments", "last_request_at"),
            "instance_id": ("instances", "last_request_at"),
        }
        for column, (table, field) in targets.items():
            rows = [r for r in last_seen.get(column) or [] if r.get("id") is not None]
            if not rows:
                continue
            await self.session.execute(
                text(
                    f"UPDATE {table} SET {field} = GREATEST(COALESCE({field}, :ts), :ts) "
                    f"WHERE id = :id"
                ),
                [{"id": r["id"], "ts": r["last_ts"]} for r in rows],
            )
        await self.session.flush()

    async def refresh_total_requests(self, api_key_ids: List[Any]) -> None:
        """api_keys.total_requests = all-time rollup request count (idempotent)."""
        ids = [i for i in api_key_ids if i is not None]
        if not ids:
            return
        await self.session.execute(
            text(
                "UPDATE api_keys ak SET total_requests = s.total FROM ("
                "SELECT api_key_id, SUM(request_count) AS total FROM usage_rollups_hourly "
                "WHERE api_key_id = ANY(:ids) GROUP BY api_key_id) s WHERE ak.id = s.api_key_id"
            ),
            {"ids": ids},
        )
        await self.session.flush()

    async def uptime_minutes(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """Instance-minutes with an engine metrics rollup (the instance was loaded)."""
        result = await self.session.execute(
            text(
                "SELECT DISTINCT date_trunc('minute', ts) AS minute, instance_id, node_id, "
                "deployment_id FROM metric_rollups_engine_1m WHERE ts >= :start AND ts < :end"
            ),
            {"start": start, "end": end},
        )
        return [dict(r) for r in result.mappings().all()]

    async def instance_intervals(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """Fallback uptime source: ready/draining intervals from ``instances``."""
        result = await self.session.execute(
            text(
                "SELECT i.id AS instance_id, i.node_id, i.deployment_id, i.state::text AS state, "
                "i.ready_at, i.stopped_at FROM instances i WHERE i.ready_at IS NOT NULL "
                "AND i.ready_at < :end AND (i.state IN ('ready', 'draining') "
                "OR (i.stopped_at IS NOT NULL AND i.stopped_at > :start "
                "AND i.stopped_at > i.ready_at))"
            ),
            {"start": start, "end": end},
        )
        return [dict(r) for r in result.mappings().all()]

    async def instance_gpu_info(self, instance_ids: List[Any]) -> Dict[str, Dict[str, Any]]:
        """Devices, VRAM bookkeeping and origin of instances (device-share proration)."""
        ids = [i for i in instance_ids if i is not None]
        if not ids:
            return {}
        result = await self.session.execute(
            text("SELECT id, device_ids, resources FROM instances " "WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        return {str(r["id"]): dict(r) for r in result.mappings().all()}

    async def device_vram_totals(self, device_ids: List[Any]) -> Dict[str, int]:
        ids = [i for i in device_ids if i is not None]
        if not ids:
            return {}
        result = await self.session.execute(
            text("SELECT id, vram_total_bytes FROM devices WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        return {str(r["id"]): int(r["vram_total_bytes"] or 0) for r in result.mappings().all()}

    async def gpu_instance_intervals(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """SSH GPU instances that ran in [start, end): their claim on each device."""
        result = await self.session.execute(
            text(
                "SELECT id, device_ids, state::text AS state, running_at, stopped_at, "
                "slice_vram_bytes, slice_compute_pct FROM gpu_instances "
                "WHERE running_at IS NOT NULL AND running_at < :end "
                "AND cardinality(device_ids) > 0 "
                "AND (state IN ('running', 'stopping') OR (stopped_at IS NOT NULL "
                "AND stopped_at > :start))"
            ),
            {"start": start, "end": end},
        )
        return [dict(r) for r in result.mappings().all()]

    async def deployment_shapes(self, deployment_ids: List[Any]) -> Dict[str, Dict[str, Any]]:
        ids = [i for i in deployment_ids if i is not None]
        if not ids:
            return {}
        result = await self.session.execute(
            text(
                "SELECT id, model_id, cluster_id, gpu_count, slice_compute_pct FROM deployments "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        return {str(r["id"]): dict(r) for r in result.mappings().all()}

    async def delete_before(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            text("DELETE FROM usage_rollups_hourly WHERE bucket < :c"), {"c": cutoff}
        )
        return int(result.rowcount or 0)


def first(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return rows[0] if rows else None
