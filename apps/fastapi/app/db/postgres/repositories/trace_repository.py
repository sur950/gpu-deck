"""
Trace index repository: span rows in the day-partitioned ``traces``
table (the subset of OpenTelemetry spans the UI needs).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import Traces
from app.db.postgres.repositories.metric_repository import _is_no_partition
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class TraceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def release_connection(self) -> None:
        await self.session.commit()

    async def insert_spans(self, rows: List[Dict[str, Any]], days_ahead: int = 3) -> int:
        if not rows:
            return 0
        try:
            await self.session.execute(insert(Traces), rows)
        except DBAPIError as exc:
            if not _is_no_partition(exc):
                raise
            await self.session.rollback()
            await self.session.execute(
                text("SELECT create_daily_partitions(:ahead, 2)"), {"ahead": days_ahead}
            )
            await self.session.commit()
            await self.session.execute(insert(Traces), rows)
        return len(rows)

    async def search_roots(
        self,
        start: datetime,
        end: datetime,
        page: int,
        page_size: int,
        filters: Dict[str, Any],
        error: Optional[bool] = None,
        min_duration_ms: Optional[float] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where = ["ts >= :start", "ts < :end", "parent_span_id IS NULL"]
        params: Dict[str, Any] = {"start": start, "end": end}
        for key, value in filters.items():
            if value is None:
                continue
            if key == "format":
                where.append("format = CAST(:format AS usage_format)")
            else:
                where.append(f"{key} = :{key}")
            params[key] = value
        if error is not None:
            where.append("error = :error")
            params["error"] = error
        if min_duration_ms is not None:
            where.append("duration_ms >= :min_duration_ms")
            params["min_duration_ms"] = min_duration_ms
        clause = " AND ".join(where)
        total = (
            await self.session.execute(text(f"SELECT COUNT(*) FROM traces WHERE {clause}"), params)
        ).scalar_one()
        params.update(limit=page_size, offset=(page - 1) * page_size)
        rows = (
            await self.session.execute(
                text(
                    f"SELECT * FROM traces WHERE {clause} ORDER BY ts DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        ).mappings()
        return [dict(r) for r in rows.all()], int(total)

    async def spans_for_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        rows = (
            await self.session.execute(
                text("SELECT * FROM traces WHERE trace_id = :trace_id ORDER BY ts, id"),
                {"trace_id": trace_id},
            )
        ).mappings()
        return [dict(r) for r in rows.all()]

    async def spans_for_request(self, request_id: uuid.UUID) -> List[Dict[str, Any]]:
        rows = (
            await self.session.execute(
                text("SELECT * FROM traces WHERE request_id = :rid ORDER BY ts, id"),
                {"rid": request_id},
            )
        ).mappings()
        return [dict(r) for r in rows.all()]
