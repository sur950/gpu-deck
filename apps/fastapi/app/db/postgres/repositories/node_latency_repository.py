"""
Node latency repository: the per-minute RTT history (``node_latency_samples``) and the
per-request timing breakdown kept in ``usage_events.metadata.timing``.

Plain SQL (``text``) like the other metric repositories; flushes, never commits.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

EPOCH = "TIMESTAMPTZ '2000-01-01 00:00:00+00'"

INSERT_SQL = text("""
    INSERT INTO node_latency_samples
        (ts, node_id, source, api_layer_id, region, mode, rtt_ms, jitter_ms, min_ms, max_ms,
         p95_ms)
    VALUES
        (:ts, :node_id, CAST(:source AS latency_source), :api_layer_id, :region, :mode,
         :rtt_ms, :jitter_ms, :min_ms, :max_ms, :p95_ms)
    ON CONFLICT (node_id, source, api_layer_id, ts) DO NOTHING
    """)

# Timing phases stored per request (app.services.serving.timing.RECORD_PHASES).
TIMING_PHASES = (
    "route",
    "queue",
    "connect",
    "rtt",
    "net",
    "engine",
    "upstream",
    "ttft",
    "total",
    "node_queue",
)


def _phase(name: str) -> str:
    return f"(e.metadata->'timing'->>'{name}_ms')::float8"


def _zero(name: str) -> str:
    return f"COALESCE({_phase(name)}, 0)"


# Time to the first byte split into parts that add up per request (then averaged, so the
# averages add up too): API layer (route + queue + connect), network, engine, the rest of the
# node's time, node + network not split (agents that do not report their timing), and the
# wait for the first byte after the response headers (the first token when streaming).
PART_SQL = {
    "part_api_ms": f"{_zero('route')} + {_zero('queue')} + {_zero('connect')}",
    "part_network_ms": _zero("net"),
    "part_engine_ms": f"CASE WHEN {_phase('net')} IS NOT NULL THEN {_zero('engine')} ELSE 0 END",
    "part_node_ms": (
        f"CASE WHEN {_phase('net')} IS NOT NULL "
        f"THEN GREATEST({_zero('upstream')} - {_zero('net')} - {_zero('engine')}, 0) ELSE 0 END"
    ),
    "part_unsplit_ms": f"CASE WHEN {_phase('net')} IS NULL THEN {_zero('upstream')} ELSE 0 END",
    "part_first_token_ms": (
        f"GREATEST({_zero('ttft')} - {_zero('route')} - {_zero('queue')} - {_zero('connect')} "
        f"- {_zero('upstream')}, 0)"
    ),
}


class NodeLatencyRepository:
    """RTT history rows and request-timing aggregates."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ writes

    async def insert_many(self, rows: Sequence[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        await self.session.execute(INSERT_SQL, [dict(r) for r in rows])
        await self.session.flush()
        return len(rows)

    async def purge_before(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            text("DELETE FROM node_latency_samples WHERE ts < :cutoff"), {"cutoff": cutoff}
        )
        await self.session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    # ------------------------------------------------------------------ reads

    @staticmethod
    def _node_filter(node_ids: Optional[Sequence[uuid.UUID]], params: Dict[str, Any]) -> str:
        if not node_ids:
            return ""
        params["node_ids"] = [uuid.UUID(str(n)) for n in node_ids]
        return " AND s.node_id = ANY(:node_ids)"

    async def series(
        self,
        node_ids: Optional[Sequence[uuid.UUID]],
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> List[Dict[str, Any]]:
        """One row per (node, source, API layer, bucket): average RTT and jitter."""
        params: Dict[str, Any] = {"start": start, "end": end, "step": int(step_seconds)}
        where = self._node_filter(node_ids, params)
        sql = text(f"""
            SELECT s.node_id, s.source::text AS source, s.api_layer_id,
                   MAX(s.region) AS region, MAX(s.mode) AS mode,
                   date_bin(make_interval(secs => :step), s.ts, {EPOCH}) AS bucket,
                   AVG(s.rtt_ms)::float8 AS rtt_ms, AVG(s.jitter_ms)::float8 AS jitter_ms,
                   MAX(s.max_ms)::float8 AS max_ms
            FROM node_latency_samples s
            WHERE s.ts >= :start AND s.ts < :end{where}
            GROUP BY s.node_id, s.source, s.api_layer_id, bucket
            ORDER BY s.node_id, s.source, s.api_layer_id, bucket
            """)
        result = await self.session.execute(sql, params)
        return [dict(r) for r in result.mappings().all()]

    async def summaries(
        self,
        node_ids: Optional[Sequence[uuid.UUID]],
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, Any]]:
        """Per (node, source, API layer) over the range: p50 / p95 / min / max / avg RTT."""
        params: Dict[str, Any] = {"start": start, "end": end}
        where = self._node_filter(node_ids, params)
        sql = text(f"""
            SELECT s.node_id, s.source::text AS source, s.api_layer_id,
                   MAX(s.region) AS region, MAX(s.mode) AS mode, COUNT(*) AS points,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY s.rtt_ms)::float8 AS p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY s.rtt_ms)::float8 AS p95_ms,
                   MIN(COALESCE(s.min_ms, s.rtt_ms))::float8 AS min_ms,
                   MAX(COALESCE(s.max_ms, s.rtt_ms))::float8 AS max_ms,
                   AVG(s.rtt_ms)::float8 AS avg_ms, AVG(s.jitter_ms)::float8 AS jitter_ms,
                   MAX(s.ts) AS last_ts
            FROM node_latency_samples s
            WHERE s.ts >= :start AND s.ts < :end{where}
            GROUP BY s.node_id, s.source, s.api_layer_id
            ORDER BY s.node_id, s.source, s.api_layer_id
            """)
        result = await self.session.execute(sql, params)
        return [dict(r) for r in result.mappings().all()]

    # ------------------------------------------------------------------ request timing

    @staticmethod
    def _event_filter(filters: Dict[str, Any], params: Dict[str, Any]) -> str:
        where = ["e.ts >= :start", "e.ts < :end", "e.metadata ? 'timing'"]
        for key in ("deployment_id", "node_id", "model_id", "api_key_id"):
            if filters.get(key):
                where.append(f"e.{key} = :{key}")
                params[key] = filters[key]
        if filters.get("stream") is not None:
            where.append("e.stream = :stream")
            params["stream"] = bool(filters["stream"])
        if filters.get("region"):
            where.append("e.metadata->'api_layer'->>'region' = :region")
            params["region"] = str(filters["region"])
        return " AND ".join(where)

    @staticmethod
    def _averages() -> str:
        cols = [f"AVG({_phase(p)}) AS {p}_ms" for p in TIMING_PHASES]
        cols.extend(f"AVG({expr}) AS {name}" for name, expr in PART_SQL.items())
        return ", ".join(cols)

    async def timing_series(
        self, filters: Dict[str, Any], start: datetime, end: datetime, step_seconds: int
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"start": start, "end": end, "step": int(step_seconds)}
        where = self._event_filter(filters, params)
        sql = text(f"""
            SELECT date_bin(make_interval(secs => :step), e.ts, {EPOCH}) AS bucket,
                   COUNT(*) AS requests, {self._averages()}
            FROM usage_events e
            WHERE {where}
            GROUP BY bucket ORDER BY bucket
            """)
        result = await self.session.execute(sql, params)
        return [dict(r) for r in result.mappings().all()]

    async def timing_summary(
        self, filters: Dict[str, Any], start: datetime, end: datetime
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"start": start, "end": end}
        where = self._event_filter(filters, params)
        pcts = []
        for phase in ("ttft", "total", "upstream", "net", "rtt", "engine"):
            for pct in (50, 95):
                pcts.append(
                    f"percentile_cont({pct / 100}) WITHIN GROUP (ORDER BY {_phase(phase)}) "
                    f"AS {phase}_p{pct}_ms"
                )
        sql = text(f"""
            SELECT COUNT(*) AS requests, {self._averages()}, {", ".join(pcts)}
            FROM usage_events e
            WHERE {where}
            """)
        row = (await self.session.execute(sql, params)).mappings().first()
        return dict(row) if row else {"requests": 0}

    async def timing_by_region(
        self, filters: Dict[str, Any], start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"start": start, "end": end}
        where = self._event_filter(filters, params)
        sql = text(f"""
            SELECT COALESCE(e.metadata->'api_layer'->>'region', '') AS region,
                   COUNT(*) AS requests, {self._averages()}
            FROM usage_events e
            WHERE {where}
            GROUP BY 1 ORDER BY requests DESC
            """)
        result = await self.session.execute(sql, params)
        return [dict(r) for r in result.mappings().all()]
