"""
Latency service: the admin read side of the measured network distances and the
per-minute history job.

- ``live``: the current RTT of every node from the agent hub and from every API layer
  (Redis ``gpu-deck:latency:*``, written by the measuring processes).
- ``history``: RTT time series and p50 / p95 / min / max per (node, source, API layer)
  from ``node_latency_samples``.
- ``request_timing``: where request time went (route, queue, connect, network, engine,
  first token) from the ``timing`` the API layer stores with every usage event.
- ``snapshot_to_db`` / ``purge``: run by the jobs process every minute / hour.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.repositories.node_latency_repository import (
    PART_SQL,
    TIMING_PHASES,
    NodeLatencyRepository,
)
from app.services.latency import store
from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

STAT_KEYS = (
    "rtt_ms",
    "jitter_ms",
    "last_ms",
    "min_ms",
    "max_ms",
    "p50_ms",
    "p95_ms",
    "samples",
    "failures",
    "updated_at",
)
TIMING_KEYS = (*(f"{p}_ms" for p in TIMING_PHASES), *PART_SQL)


def _stat(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not entry:
        return None
    return {k: entry.get(k) for k in STAT_KEYS}


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _missing_table(exc: Exception) -> bool:
    return "node_latency_samples" in str(exc) and "does not exist" in str(exc)


class LatencyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = NodeLatencyRepository(session)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "LatencyService":
        return LatencyService(db)

    async def _node_names(self, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        valid = []
        for value in ids:
            try:
                valid.append(uuid.UUID(str(value)))
            except ValueError:
                continue
        if not valid:
            return {}
        rows = await self.session.execute(
            text(
                "SELECT id::text AS id, name, status::text AS status FROM nodes "
                "WHERE id = ANY(:ids) AND deleted_at IS NULL"
            ),
            {"ids": valid},
        )
        return {r["id"]: dict(r) for r in rows.mappings().all()}

    # ------------------------------------------------------------------ live

    async def live(self, node_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
        data = await store.read_live()
        layers = data["api_layers"]
        wanted = str(node_id) if node_id else None
        node_ids = set(data["hub"])
        for nodes in data["api"].values():
            node_ids.update(nodes)
        if wanted:
            node_ids = {n for n in node_ids if n == wanted} | {wanted}
        names = await self._node_names(sorted(node_ids))
        out_nodes: List[Dict[str, Any]] = []
        for nid in sorted(node_ids, key=lambda n: (names.get(n, {}).get("name") or n)):
            if nid not in names:
                continue  # removed node (or not a node id at all)
            per_layer = []
            for layer_id, nodes in data["api"].items():
                entry = nodes.get(nid)
                if not entry:
                    continue
                layer = layers.get(layer_id) or {}
                stat = _stat(entry) or {}
                per_layer.append(
                    {
                        "api_layer_id": layer_id,
                        "region": layer.get("region"),
                        "mode": entry.get("mode"),
                        **stat,
                    }
                )
            per_layer.sort(key=lambda e: (e.get("rtt_ms") is None, e.get("rtt_ms") or 0))
            rtts = [e["rtt_ms"] for e in per_layer if e.get("rtt_ms") is not None]
            out_nodes.append(
                {
                    "node_id": nid,
                    "node_name": names[nid].get("name"),
                    "status": names[nid].get("status"),
                    "hub": _stat(data["hub"].get(nid)),
                    "api_layers": per_layer,
                    "best_rtt_ms": min(rtts) if rtts else None,
                }
            )
        return {
            "generated_at": _iso(datetime.now(timezone.utc)),
            "api_layers": [
                {
                    "id": layer_id,
                    "region": layer.get("region"),
                    "seen_at": _iso(
                        datetime.fromtimestamp(float(layer.get("ts") or 0), timezone.utc)
                    ),
                    "probe_seconds": layer.get("probe_seconds"),
                    "nodes": layer.get("nodes"),
                }
                for layer_id, layer in sorted(layers.items())
            ],
            "nodes": out_nodes,
        }

    # ------------------------------------------------------------------ history

    async def history(
        self,
        node_ids: Optional[Sequence[uuid.UUID]],
        from_: Optional[str],
        to: Optional[str],
        step: Optional[str],
    ) -> Dict[str, Any]:
        from app.services.metrics.service import bucket_grid, time_window

        start, end, step_seconds, step_label = time_window(from_, to, step, default_step="1m")
        if step_seconds < 60:  # history is per minute
            step_seconds, step_label = 60, "1m"
        try:
            async with self.session.begin_nested():
                rows = await self.repo.series(node_ids, start, end, step_seconds)
                summaries = await self.repo.summaries(node_ids, start, end)
        except ProgrammingError as exc:
            if not _missing_table(exc):
                raise
            rows, summaries = [], []  # migration 202 not applied yet
        names = await self._node_names(
            sorted({str(r["node_id"]) for r in rows} | {str(s["node_id"]) for s in summaries})
        )
        grid = bucket_grid(start, end, step_seconds)
        grouped: Dict[tuple, Dict[str, Any]] = {}
        for row in rows:
            key = (str(row["node_id"]), row["source"], row["api_layer_id"] or "")
            entry = grouped.setdefault(key, {"row": row, "values": {}})
            bucket = row["bucket"]
            if bucket.tzinfo is None:
                bucket = bucket.replace(tzinfo=timezone.utc)
            entry["values"][bucket] = row
        series: List[Dict[str, Any]] = []
        for (nid, source, layer_id), entry in grouped.items():
            labels = {
                "node_id": nid,
                "source": source,
                "api_layer_id": layer_id,
            }
            first = entry["row"]
            if names.get(nid, {}).get("name"):
                labels["node"] = names[nid]["name"]
            if first.get("region"):
                labels["region"] = first["region"]
            if first.get("mode"):
                labels["mode"] = first["mode"]
            for metric in ("rtt_ms", "jitter_ms"):
                series.append(
                    {
                        "labels": dict(labels),
                        "metric": metric,
                        "unit": "ms",
                        "points": [
                            [
                                _iso(b),
                                (
                                    _num(entry["values"][b][metric])
                                    if b in entry["values"]
                                    else None
                                ),
                            ]
                            for b in grid
                        ],
                    }
                )
        return {
            "from": _iso(start),
            "to": _iso(end),
            "step": step_label,
            "series": series,
            "summaries": [
                {
                    "node_id": str(s["node_id"]),
                    "node_name": names.get(str(s["node_id"]), {}).get("name"),
                    "source": s["source"],
                    "api_layer_id": s["api_layer_id"] or None,
                    "region": s.get("region"),
                    "mode": s.get("mode"),
                    "points": int(s.get("points") or 0),
                    "p50_ms": _num(s.get("p50_ms")),
                    "p95_ms": _num(s.get("p95_ms")),
                    "min_ms": _num(s.get("min_ms")),
                    "max_ms": _num(s.get("max_ms")),
                    "avg_ms": _num(s.get("avg_ms")),
                    "jitter_ms": _num(s.get("jitter_ms")),
                    "last_at": _iso(s.get("last_ts")),
                }
                for s in summaries
            ],
        }

    # ------------------------------------------------------------------ request timing

    async def request_timing(
        self,
        filters: Dict[str, Any],
        from_: Optional[str],
        to: Optional[str],
        step: Optional[str],
    ) -> Dict[str, Any]:
        from app.services.metrics.service import bucket_grid, time_window

        start, end, step_seconds, step_label = time_window(from_, to, step, default_step="5m")
        if step_seconds < 60:
            step_seconds, step_label = 60, "1m"
        rows = await self.repo.timing_series(filters, start, end, step_seconds)
        summary = await self.repo.timing_summary(filters, start, end)
        regions = await self.repo.timing_by_region(filters, start, end)
        by_bucket = {}
        for row in rows:
            bucket = row["bucket"]
            if bucket.tzinfo is None:
                bucket = bucket.replace(tzinfo=timezone.utc)
            by_bucket[bucket] = row
        grid = bucket_grid(start, end, step_seconds)
        series = []
        for key in (*TIMING_KEYS, "requests"):
            series.append(
                {
                    "labels": {"phase": key.replace("_ms", "")},
                    "metric": key,
                    "unit": "requests" if key == "requests" else "ms",
                    "points": [
                        [_iso(b), _num(by_bucket[b][key]) if b in by_bucket else None] for b in grid
                    ],
                }
            )
        return {
            "from": _iso(start),
            "to": _iso(end),
            "step": step_label,
            "series": series,
            "summary": {
                k: (int(v or 0) if k == "requests" else _num(v)) for k, v in summary.items()
            },
            "regions": [
                {
                    k: (int(v or 0) if k == "requests" else (v if k == "region" else _num(v)))
                    for k, v in r.items()
                }
                for r in regions
            ],
        }


# ---------------------------------------------------------------------------
# History job (jobs process)
# ---------------------------------------------------------------------------


def snapshot_rows(live: Dict[str, Any], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Rows for ``node_latency_samples`` from the live Redis view (one per path)."""
    ts = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    rows: List[Dict[str, Any]] = []

    def add(node_id: str, source: str, entry: Dict[str, Any], **extra: Any) -> None:
        try:
            node_uuid = uuid.UUID(str(node_id))
        except ValueError:
            return  # dev engine / synthetic node ids are not stored
        if entry.get("rtt_ms") is None:
            return
        rows.append(
            {
                "ts": ts,
                "node_id": node_uuid,
                "source": source,
                "api_layer_id": extra.get("api_layer_id", ""),
                "region": extra.get("region"),
                "mode": entry.get("mode") if source == "api_layer" else None,
                "rtt_ms": float(entry["rtt_ms"]),
                "jitter_ms": entry.get("jitter_ms"),
                "min_ms": entry.get("min_ms"),
                "max_ms": entry.get("max_ms"),
                "p95_ms": entry.get("p95_ms"),
            }
        )

    for node_id, entry in (live.get("hub") or {}).items():
        add(node_id, "hub", entry)
    layers = live.get("api_layers") or {}
    for layer_id, nodes in (live.get("api") or {}).items():
        region = (layers.get(layer_id) or {}).get("region")
        for node_id, entry in nodes.items():
            add(node_id, "api_layer", entry, api_layer_id=layer_id[:120], region=region)
    return rows


async def snapshot_to_db(session: AsyncSession) -> int:
    rows = snapshot_rows(await store.read_live())
    return await NodeLatencyRepository(session).insert_many(rows)


async def purge(session: AsyncSession, now: Optional[datetime] = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        days=max(1, int(settings.LATENCY_RETENTION_DAYS))
    )
    return await NodeLatencyRepository(session).purge_before(cutoff)
