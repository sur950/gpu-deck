"""
Metrics repository: bulk ingest into the day-partitioned sample tables
(with a single retry after ``create_daily_partitions()`` on SQLSTATE 23514, no partition), 1-minute
rollups, time-series queries over raw samples / rollups, session samples,
usage rollups and partition / retention maintenance.

Heavy aggregations are plain SQL (``text``) — easier to read and to check
against ``libs/contracts/metrics.md`` §2 "Rollup rules".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    MetricSamplesEngine,
    MetricSamplesGpu,
    MetricSamplesSession,
)
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

NO_PARTITION_SQLSTATE = "23514"

PARTITIONED_TABLES = {
    "metric_samples_gpu": "raw",
    "metric_samples_engine": "raw",
    "metric_samples_session": "raw",
    "metric_rollups_gpu_1m": "rollup",
    "metric_rollups_engine_1m": "rollup",
    "log_chunks": "logs",
    "traces": "traces",
}

RECENT_TABLES = {
    "alert_events": ("last_seen_at", "created_at"),
    "swap_operations": ("created_at", "updated_at"),
    "rule_runs": ("evaluated_at", "created_at"),
}

GPU_ROLLUP_SQL = """
INSERT INTO metric_rollups_gpu_1m (
    ts, node_id, device_id, sample_count,
    utilization_pct_avg, utilization_pct_max,
    memory_used_bytes_avg, memory_used_bytes_max, memory_total_bytes,
    power_watts_avg, power_watts_max, energy_wh,
    temperature_c_avg, temperature_c_max, sm_clock_mhz_avg,
    error_count_sum, throttled_seconds)
SELECT
    date_bin('1 minute', ts, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket,
    node_id, device_id, COUNT(*) AS sample_count,
    AVG(utilization_pct), MAX(utilization_pct),
    AVG(memory_used_bytes)::BIGINT, MAX(memory_used_bytes), MAX(memory_total_bytes),
    AVG(power_watts), MAX(power_watts),
    AVG(power_watts) * (:interval * COUNT(*)) / 3600.0,
    AVG(temperature_c), MAX(temperature_c), AVG(sm_clock_mhz)::INTEGER,
    SUM(error_count)::INTEGER,
    COUNT(*) FILTER (WHERE throttle_reasons IS NOT NULL
                       AND cardinality(throttle_reasons) > 0) * :interval
FROM metric_samples_gpu
WHERE ts >= :start AND ts < :end
GROUP BY bucket, node_id, device_id
ON CONFLICT (device_id, ts) DO UPDATE SET
    sample_count = EXCLUDED.sample_count,
    utilization_pct_avg = EXCLUDED.utilization_pct_avg,
    utilization_pct_max = EXCLUDED.utilization_pct_max,
    memory_used_bytes_avg = EXCLUDED.memory_used_bytes_avg,
    memory_used_bytes_max = EXCLUDED.memory_used_bytes_max,
    memory_total_bytes = EXCLUDED.memory_total_bytes,
    power_watts_avg = EXCLUDED.power_watts_avg,
    power_watts_max = EXCLUDED.power_watts_max,
    energy_wh = EXCLUDED.energy_wh,
    temperature_c_avg = EXCLUDED.temperature_c_avg,
    temperature_c_max = EXCLUDED.temperature_c_max,
    sm_clock_mhz_avg = EXCLUDED.sm_clock_mhz_avg,
    error_count_sum = EXCLUDED.error_count_sum,
    throttled_seconds = EXCLUDED.throttled_seconds
"""

# Counter deltas are reset-aware: a decrease is treated as a restart and the
# post-reset value is added on top of what was accumulated before (metrics.md §2).
# requests_delta is the larger of the proxy's request counter delta and the engine's
# success + error deltas (traffic that bypassed the node proxy still counts).
# LAG runs over each instance's whole timeline (with a 2-minute lookback before
# :start) so the increase between the last sample of one minute and the first
# of the next is attributed to the later minute instead of being lost.
ENGINE_ROLLUP_SQL = """
WITH ordered_all AS (
    SELECT date_bin('1 minute', ts, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket,
           node_id, instance_id, deployment_id, ts,
           active_sessions, waiting_sessions,
           requests_total, requests_success_total, errors_total,
           prompt_tokens_total, generated_tokens_total, cached_tokens_total,
           prompt_tps, generation_tps, ttft_ms_avg, ttft_ms_p95, e2e_ms_avg, e2e_ms_p95,
           tpot_ms_avg, kv_cache_usage_pct, prefix_cache_hit_rate, health,
           LAG(requests_total) OVER w AS prev_requests,
           LAG(requests_success_total) OVER w AS prev_success,
           LAG(errors_total) OVER w AS prev_errors,
           LAG(prompt_tokens_total) OVER w AS prev_prompt,
           LAG(generated_tokens_total) OVER w AS prev_generated,
           LAG(cached_tokens_total) OVER w AS prev_cached
    FROM metric_samples_engine
    WHERE ts >= CAST(:start AS timestamptz) - INTERVAL '2 minutes' AND ts < :end
    WINDOW w AS (PARTITION BY instance_id ORDER BY ts, id)
), ordered AS (
    SELECT * FROM ordered_all WHERE ts >= :start
), agg AS (
    SELECT bucket, node_id, instance_id, deployment_id, COUNT(*) AS sample_count,
           AVG(active_sessions) AS active_avg, MAX(active_sessions) AS active_max,
           AVG(waiting_sessions) AS waiting_avg, MAX(waiting_sessions) AS waiting_max,
           SUM(CASE WHEN prev_requests IS NULL THEN 0
                    WHEN requests_total >= prev_requests THEN requests_total - prev_requests
                    ELSE requests_total END) AS requests_delta,
           SUM(CASE WHEN prev_success IS NULL THEN 0
                    WHEN requests_success_total >= prev_success
                        THEN requests_success_total - prev_success
                    ELSE requests_success_total END) AS success_delta,
           SUM(CASE WHEN prev_errors IS NULL THEN 0
                    WHEN errors_total >= prev_errors THEN errors_total - prev_errors
                    ELSE errors_total END) AS errors_delta,
           SUM(CASE WHEN prev_prompt IS NULL THEN 0
                    WHEN prompt_tokens_total >= prev_prompt THEN prompt_tokens_total - prev_prompt
                    ELSE prompt_tokens_total END) AS prompt_delta,
           SUM(CASE WHEN prev_generated IS NULL THEN 0
                    WHEN generated_tokens_total >= prev_generated
                        THEN generated_tokens_total - prev_generated
                    ELSE generated_tokens_total END) AS generated_delta,
           SUM(CASE WHEN prev_cached IS NULL THEN 0
                    WHEN cached_tokens_total >= prev_cached THEN cached_tokens_total - prev_cached
                    ELSE cached_tokens_total END) AS cached_delta,
           AVG(prompt_tps) AS prompt_tps_avg, AVG(generation_tps) AS generation_tps_avg,
           AVG(ttft_ms_avg) AS ttft_avg,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms_p95) AS ttft_p95,
           AVG(e2e_ms_avg) AS e2e_avg,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY e2e_ms_p95) AS e2e_p95,
           AVG(tpot_ms_avg) AS tpot_avg,
           AVG(kv_cache_usage_pct) AS kv_avg, MAX(kv_cache_usage_pct) AS kv_max,
           AVG(prefix_cache_hit_rate) AS prefix_hit,
           COUNT(*) FILTER (WHERE health = 'healthy') * :interval AS healthy_seconds
    FROM ordered
    GROUP BY bucket, node_id, instance_id, deployment_id
)
INSERT INTO metric_rollups_engine_1m (
    ts, node_id, instance_id, deployment_id, sample_count,
    active_sessions_avg, active_sessions_max, waiting_sessions_avg, waiting_sessions_max,
    requests_delta, requests_success_delta, errors_delta,
    prompt_tokens_delta, generated_tokens_delta, cached_tokens_delta,
    prompt_tps_avg, generation_tps_avg, ttft_ms_avg, ttft_ms_p95, e2e_ms_avg, e2e_ms_p95,
    tpot_ms_avg, kv_cache_usage_pct_avg, kv_cache_usage_pct_max, prefix_cache_hit_rate,
    error_rate_pct, healthy_seconds)
SELECT bucket, node_id, instance_id, deployment_id, sample_count,
       active_avg, active_max, waiting_avg, waiting_max,
       served, success_delta, errors_delta,
       prompt_delta, generated_delta, cached_delta,
       prompt_tps_avg, generation_tps_avg, ttft_avg, ttft_p95, e2e_avg, e2e_p95,
       tpot_avg, kv_avg, kv_max, prefix_hit,
       CASE WHEN served > 0 THEN COALESCE(errors_delta, 0)::REAL / served * 100 END,
       healthy_seconds
FROM (
    -- requests_total comes from the node proxy; engines that count their own finished
    -- requests (vLLM / SGLang success counters) also see traffic that bypasses the proxy
    -- (direct clients, benchmarks on the node). The larger of the two is what was served.
    SELECT agg.*, GREATEST(requests_delta, success_delta + COALESCE(errors_delta, 0)) AS served
    FROM agg
) counted
ON CONFLICT (instance_id, ts) DO UPDATE SET
    sample_count = EXCLUDED.sample_count,
    active_sessions_avg = EXCLUDED.active_sessions_avg,
    active_sessions_max = EXCLUDED.active_sessions_max,
    waiting_sessions_avg = EXCLUDED.waiting_sessions_avg,
    waiting_sessions_max = EXCLUDED.waiting_sessions_max,
    requests_delta = EXCLUDED.requests_delta,
    requests_success_delta = EXCLUDED.requests_success_delta,
    errors_delta = EXCLUDED.errors_delta,
    prompt_tokens_delta = EXCLUDED.prompt_tokens_delta,
    generated_tokens_delta = EXCLUDED.generated_tokens_delta,
    cached_tokens_delta = EXCLUDED.cached_tokens_delta,
    prompt_tps_avg = EXCLUDED.prompt_tps_avg,
    generation_tps_avg = EXCLUDED.generation_tps_avg,
    ttft_ms_avg = EXCLUDED.ttft_ms_avg,
    ttft_ms_p95 = EXCLUDED.ttft_ms_p95,
    e2e_ms_avg = EXCLUDED.e2e_ms_avg,
    e2e_ms_p95 = EXCLUDED.e2e_ms_p95,
    tpot_ms_avg = EXCLUDED.tpot_ms_avg,
    kv_cache_usage_pct_avg = EXCLUDED.kv_cache_usage_pct_avg,
    kv_cache_usage_pct_max = EXCLUDED.kv_cache_usage_pct_max,
    prefix_cache_hit_rate = EXCLUDED.prefix_cache_hit_rate,
    error_rate_pct = EXCLUDED.error_rate_pct,
    healthy_seconds = EXCLUDED.healthy_seconds
"""


def _is_no_partition(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(exc, "sqlstate", None)
    return sqlstate == NO_PARTITION_SQLSTATE or "no partition of relation" in str(exc)


class MetricRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def release_connection(self) -> None:
        await self.session.commit()

    # ---------- partitions ----------

    async def create_daily_partitions(self, days_ahead: int, days_back: int = 0) -> int:
        result = await self.session.execute(
            text("SELECT create_daily_partitions(:ahead, :back)"),
            {"ahead": days_ahead, "back": days_back},
        )
        return int(result.scalar_one())

    async def drop_old_partitions(self, table: str, retain_days: int) -> int:
        if table not in PARTITIONED_TABLES:
            raise ValueError(f"unknown partitioned table {table}")
        result = await self.session.execute(
            text("SELECT drop_old_partitions(:table, :days)"),
            {"table": table, "days": retain_days},
        )
        return int(result.scalar_one())

    # ---------- ingest ----------

    async def _insert_with_retry(self, table, rows: List[Dict[str, Any]], days_ahead: int) -> int:
        if not rows:
            return 0
        try:
            await self.session.execute(insert(table), rows)
        except DBAPIError as exc:
            if not _is_no_partition(exc):
                raise
            await self.session.rollback()
            logger.warning(f"ingest: no partition for {table.__tablename__}; creating and retrying")
            await self.create_daily_partitions(days_ahead, days_back=2)
            await self.session.commit()
            await self.session.execute(insert(table), rows)
        return len(rows)

    async def insert_gpu_samples(self, rows: List[Dict[str, Any]], days_ahead: int = 3) -> int:
        return await self._insert_with_retry(MetricSamplesGpu, rows, days_ahead)

    async def insert_engine_samples(self, rows: List[Dict[str, Any]], days_ahead: int = 3) -> int:
        return await self._insert_with_retry(MetricSamplesEngine, rows, days_ahead)

    async def insert_session_samples(self, rows: List[Dict[str, Any]], days_ahead: int = 3) -> int:
        return await self._insert_with_retry(MetricSamplesSession, rows, days_ahead)

    # ---------- rollups ----------

    async def rollup_minutes(self, start: datetime, end: datetime, interval_seconds: float) -> None:
        params = {"start": start, "end": end, "interval": float(interval_seconds)}
        await self.session.execute(text(GPU_ROLLUP_SQL), params)
        await self.session.execute(text(ENGINE_ROLLUP_SQL), params)

    # ---------- time series ----------

    async def query_series(
        self,
        table: str,
        entity_col: str,
        columns: Dict[str, str],
        start: datetime,
        end: datetime,
        step_seconds: int,
        filters: Dict[str, Any],
        entity_ids: Optional[Sequence[uuid.UUID]] = None,
        aggregate: str = "none",
        join_sql: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Buckets ``columns`` ({alias: "AGG(col)"}) by ``step_seconds`` for one table.
        ``aggregate=none`` keeps one series per entity; else entities are merged with
        the given SQL aggregate.
        """
        where = ["t.ts >= :start", "t.ts < :end"]
        params: Dict[str, Any] = {"start": start, "end": end, "step": step_seconds}
        for key, value in filters.items():
            if value is None:
                continue
            where.append(f"{key} = :{key.replace('.', '_')}")
            params[key.replace(".", "_")] = value
        if entity_ids:
            where.append(f"t.{entity_col} = ANY(:entity_ids)")
            params["entity_ids"] = list(entity_ids)
        selects = [f"{expr} AS {alias}" for alias, expr in columns.items()]
        bucket = (
            "date_bin(make_interval(secs => :step), t.ts, TIMESTAMPTZ '2000-01-01 00:00:00+00')"
        )
        if aggregate == "none":
            group = f"{bucket}, t.{entity_col}"
            entity_select = f"t.{entity_col} AS entity_id"
        else:
            group = bucket
            entity_select = "NULL::uuid AS entity_id"
        sql = (
            f"SELECT {bucket} AS bucket, {entity_select}, {', '.join(selects)} "
            f"FROM {table} t {join_sql} WHERE {' AND '.join(where)} "
            f"GROUP BY {group} ORDER BY bucket"
        )
        rows = (await self.session.execute(text(sql), params)).mappings().all()
        return [dict(r) for r in rows]

    async def query_session_series(
        self,
        instance_id: uuid.UUID,
        start: datetime,
        end: datetime,
        session_key: Optional[str] = None,
        request_id: Optional[uuid.UUID] = None,
        limit: int = 20000,
    ) -> List[Dict[str, Any]]:
        where = ["instance_id = :instance_id", "ts >= :start", "ts < :end"]
        params: Dict[str, Any] = {
            "instance_id": instance_id,
            "start": start,
            "end": end,
            "limit": limit,
        }
        if session_key:
            where.append("session_key = :session_key")
            params["session_key"] = session_key
        if request_id:
            where.append("request_id = :request_id")
            params["request_id"] = request_id
        sql = (
            "SELECT ts, session_key, request_id, slot_id, state, prompt_tokens, generated_tokens, "
            "cached_tokens, kv_cache_tokens, kv_cache_bytes, context_size, ttft_ms, elapsed_ms, "
            f"tokens_per_second FROM metric_samples_session WHERE {' AND '.join(where)} "
            "ORDER BY ts LIMIT :limit"
        )
        rows = (await self.session.execute(text(sql), params)).mappings().all()
        return [dict(r) for r in rows]

    async def latest_session_samples(self, instance_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Rows of the most recent scrape for one instance (fallback when Redis has none)."""
        sql = text("""
            SELECT * FROM metric_samples_session
            WHERE instance_id = :id AND ts = (
                SELECT MAX(ts) FROM metric_samples_session
                WHERE instance_id = :id AND ts > now() - interval '10 minutes')
            ORDER BY slot_id NULLS LAST, session_key
            """)
        rows = (await self.session.execute(sql, {"id": instance_id})).mappings().all()
        return [dict(r) for r in rows]

    async def query_usage_series(
        self,
        start: datetime,
        end: datetime,
        step_seconds: int,
        metrics: Sequence[str],
        filters: Dict[str, Any],
        group_by: str = "none",
    ) -> List[Dict[str, Any]]:
        column_exprs = {
            "requests": "SUM(request_count)",
            "errors": "SUM(error_count)",
            "input_tokens": "SUM(input_tokens)",
            "output_tokens": "SUM(output_tokens)",
            "cached_tokens": "SUM(cached_tokens)",
            "reasoning_tokens": "SUM(reasoning_tokens)",
            "media_units": "SUM(media_units)",
            "gpu_seconds": "SUM(gpu_seconds)",
            "cost_usd": "SUM(cost_usd)",
            "latency_ms_avg": "SUM(latency_ms_sum)::float / NULLIF(SUM(request_count), 0)",
            "ttft_ms_avg": "SUM(ttft_ms_sum)::float / NULLIF(SUM(request_count), 0)",
        }
        selects = [f"{column_exprs[m]} AS {m}" for m in metrics if m in column_exprs]
        if not selects:
            return []
        where = ["bucket >= :start", "bucket < :end"]
        params: Dict[str, Any] = {"start": start, "end": end, "step": step_seconds}
        for key, value in filters.items():
            if value is not None:
                where.append(f"{key} = :{key}")
                params[key] = value
        group_col = {
            "api_key": "api_key_id",
            "model": "model_id",
            "node": "node_id",
            "deployment": "deployment_id",
        }.get(group_by)
        b = "date_bin(make_interval(secs => :step), bucket, TIMESTAMPTZ '2000-01-01 00:00:00+00')"
        group_select = f"{group_col} AS group_id" if group_col else "NULL::uuid AS group_id"
        group_clause = f"{b}, {group_col}" if group_col else b
        sql = (
            f"SELECT {b} AS b, {group_select}, {', '.join(selects)} FROM usage_rollups_hourly "
            f"WHERE {' AND '.join(where)} GROUP BY {group_clause} ORDER BY b"
        )
        rows = (await self.session.execute(text(sql), params)).mappings().all()
        return [dict(r) for r in rows]

    async def cost_since(self, since: datetime) -> float:
        result = await self.session.execute(
            text("SELECT COALESCE(SUM(cost_usd), 0) FROM usage_events WHERE ts >= :since"),
            {"since": since},
        )
        return float(result.scalar_one())

    async def recent_rows(self, table: str, order_col: str, limit: int = 5) -> List[Dict[str, Any]]:
        if table not in RECENT_TABLES or order_col not in RECENT_TABLES[table]:
            raise ValueError(f"recent_rows: {table}.{order_col} not allowed")
        sql = text(f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT :limit")  # nosec
        rows = (await self.session.execute(sql, {"limit": limit})).mappings().all()
        return [dict(r) for r in rows]

    async def overview_counts(self) -> Dict[str, int]:
        sql = text("""
            SELECT
              (SELECT COUNT(*) FROM clusters WHERE deleted_at IS NULL) AS clusters,
              (SELECT COUNT(*) FROM nodes WHERE deleted_at IS NULL AND status <> 'removed')
                  AS nodes,
              (SELECT COUNT(*) FROM nodes WHERE deleted_at IS NULL
                  AND status IN ('online', 'draining', 'drained')) AS nodes_online,
              (SELECT COUNT(*) FROM devices d JOIN nodes n ON n.id = d.node_id
                  WHERE d.deleted_at IS NULL AND n.deleted_at IS NULL
                  AND n.status <> 'removed') AS devices,
              (SELECT COUNT(*) FROM devices d JOIN nodes n ON n.id = d.node_id
                  WHERE d.deleted_at IS NULL AND n.deleted_at IS NULL
                  AND n.status <> 'removed'
                  AND d.health IN ('unhealthy','degraded')) AS devices_unhealthy,
              (SELECT COUNT(*) FROM models WHERE deleted_at IS NULL) AS models,
              (SELECT COUNT(*) FROM deployments WHERE deleted_at IS NULL) AS deployments,
              (SELECT COUNT(*) FROM deployments WHERE deleted_at IS NULL AND status = 'running')
                  AS deployments_running,
              (SELECT COUNT(*) FROM instances WHERE state = 'ready') AS instances_ready,
              (SELECT COUNT(*) FROM api_keys WHERE deleted_at IS NULL AND status = 'active')
                  AS api_keys_active,
              (SELECT COUNT(*) FROM rules WHERE deleted_at IS NULL AND enabled) AS rules_enabled,
              (SELECT COUNT(*) FROM swap_operations WHERE state NOT IN
                  ('completed','failed','rolled_back','cancelled')) AS swaps_in_progress,
              (SELECT COUNT(*) FROM alert_events WHERE status = 'open') AS alerts_open
            """)
        row = (await self.session.execute(sql)).mappings().one()
        return {k: int(v) for k, v in dict(row).items()}

    async def device_ids_for_node(self, node_id: uuid.UUID) -> set:
        rows = await self.session.execute(
            text("SELECT id FROM devices WHERE node_id = :id AND deleted_at IS NULL"),
            {"id": node_id},
        )
        return {r[0] for r in rows.all()}

    async def instance_ids_for_node(self, node_id: uuid.UUID) -> set:
        rows = await self.session.execute(
            text("SELECT id FROM instances WHERE node_id = :id AND state <> 'stopped'"),
            {"id": node_id},
        )
        return {r[0] for r in rows.all()}

    async def last_minute_engine_rollups(
        self, instance_ids: Sequence[uuid.UUID]
    ) -> Dict[uuid.UUID, Dict[str, Any]]:
        """Latest rollup row (within 3 minutes) per instance: requests_delta, error_rate_pct."""
        if not instance_ids:
            return {}
        rows = await self.session.execute(
            text("""
                SELECT DISTINCT ON (instance_id) instance_id, ts, requests_delta, errors_delta,
                       error_rate_pct
                FROM metric_rollups_engine_1m
                WHERE instance_id = ANY(:ids) AND ts >= now() - interval '3 minutes'
                ORDER BY instance_id, ts DESC
                """),
            {"ids": list(instance_ids)},
        )
        return {r["instance_id"]: dict(r) for r in rows.mappings().all()}

    async def rollup_snapshot(
        self,
        node_ids: Optional[Sequence[str]] = None,
        deployment_id: Optional[uuid.UUID] = None,
        instance_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        """Requests/min and error rate of the newest complete minute + VRAM total."""
        where = [
            "ts = (SELECT MAX(ts) FROM metric_rollups_engine_1m "
            "WHERE ts >= now() - interval '3 minutes')"
        ]
        params: Dict[str, Any] = {}
        if node_ids is not None:
            where.append("node_id::text = ANY(:node_ids)")
            params["node_ids"] = list(node_ids)
        if deployment_id:
            where.append("deployment_id = :deployment_id")
            params["deployment_id"] = deployment_id
        if instance_id:
            where.append("instance_id = :instance_id")
            params["instance_id"] = instance_id
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT SUM(requests_delta) AS requests, SUM(errors_delta) AS errors "
                        f"FROM metric_rollups_engine_1m WHERE {' AND '.join(where)}"
                    ),
                    params,
                )
            )
            .mappings()
            .one()
        )
        # Devices of removed nodes keep their rows; only count devices on live nodes.
        dev_where = ["d.deleted_at IS NULL", "n.deleted_at IS NULL", "n.status <> 'removed'"]
        dev_params: Dict[str, Any] = {}
        if node_ids is not None:
            dev_where.append("d.node_id::text = ANY(:node_ids)")
            dev_params["node_ids"] = list(node_ids)
        vram = (
            await self.session.execute(
                text(
                    "SELECT COALESCE(SUM(d.vram_total_bytes), 0) FROM devices d "
                    "JOIN nodes n ON n.id = d.node_id "
                    f"WHERE {' AND '.join(dev_where)}"
                ),
                dev_params,
            )
        ).scalar_one()
        requests = row["requests"]
        errors = row["errors"]
        return {
            "requests_per_minute": float(requests) if requests is not None else None,
            "error_rate_pct": (
                float(errors) * 100.0 / float(requests)
                if requests and errors is not None
                else (0.0 if requests == 0 else None)
            ),
            "vram_total_bytes": int(vram or 0),
        }

    async def raw_retention_cutoff(self, hours: int) -> datetime:
        return datetime.now(timezone.utc) - timedelta(hours=hours)
