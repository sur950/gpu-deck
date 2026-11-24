"""
Log chunk repository: inserts batched engine/agent log lines into the
day-partitioned ``log_chunks`` table and serves the cursor-paginated query.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import LogChunks
from app.db.postgres.repositories.metric_repository import _is_no_partition
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class LogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def release_connection(self) -> None:
        await self.session.commit()

    async def insert_chunks(self, rows: List[Dict[str, Any]], days_ahead: int = 3) -> int:
        if not rows:
            return 0
        try:
            await self.session.execute(insert(LogChunks), rows)
        except DBAPIError as exc:
            if not _is_no_partition(exc):
                raise
            await self.session.rollback()
            await self.session.execute(
                text("SELECT create_daily_partitions(:ahead, 2)"), {"ahead": days_ahead}
            )
            await self.session.commit()
            await self.session.execute(insert(LogChunks), rows)
        return len(rows)

    async def query(
        self,
        start: datetime,
        end: datetime,
        limit: int,
        node_id: Optional[uuid.UUID] = None,
        instance_id: Optional[uuid.UUID] = None,
        deployment_id: Optional[uuid.UUID] = None,
        sources: Optional[Sequence[str]] = None,
        level: Optional[str] = None,
        stream: Optional[str] = None,
        request_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        cursor: Optional[Tuple[datetime, int]] = None,
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        where = ["l.ts >= :start", "l.ts < :end"]
        params: Dict[str, Any] = {"start": start, "end": end, "limit": limit + 1}
        if node_id:
            where.append("l.node_id = :node_id")
            params["node_id"] = node_id
        if instance_id:
            where.append("l.instance_id = :instance_id")
            params["instance_id"] = instance_id
        if deployment_id:
            where.append("l.deployment_id = :deployment_id")
            params["deployment_id"] = deployment_id
        if sources:
            where.append("l.source = ANY(CAST(:sources AS log_source[]))")
            params["sources"] = list(sources)
        if level:
            where.append("l.level = ANY(CAST(:levels AS log_level[]))")
            params["levels"] = _levels_at_least(level)
        if stream:
            where.append("l.stream = CAST(:stream AS log_stream)")
            params["stream"] = stream
        if request_id:
            where.append("l.request_id = :request_id")
            params["request_id"] = request_id
        if search:
            where.append("l.content ILIKE :search")
            params["search"] = f"%{search}%"
        if cursor:
            op = ">" if ascending else "<"
            where.append(f"(l.ts, l.id) {op} (:cursor_ts, :cursor_id)")
            params["cursor_ts"], params["cursor_id"] = cursor
        order = "ASC" if ascending else "DESC"
        sql = text(
            "SELECT l.*, n.name AS node_name FROM log_chunks l "
            "LEFT JOIN nodes n ON n.id = l.node_id "
            f"WHERE {' AND '.join(where)} ORDER BY l.ts {order}, l.id {order} LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).mappings().all()
        return [dict(r) for r in rows]


def _levels_at_least(level: str) -> List[str]:
    order = ["debug", "info", "warn", "error"]
    if level not in order:
        return order
    return order[order.index(level) :]
