"""
Repository for audit_logs (append-only; no updates, no soft delete).
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import AuditLogs
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import delete, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SORTABLE = {"created_at", "event_type", "actor_type", "outcome", "resource_type"}


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def event_type_condition(event_type: str):
    """SQL condition for the ``event_type`` filter (admin.yaml ``GET /audit``).

    - ``rule`` (bare category, no dot) -> ``rule`` exactly or anything under ``rule.``
    - ``rule.`` (trailing dot) -> prefix match ``rule.%``
    - ``rule.*`` / ``*.failed`` / ``api_key.*ed`` (``*`` glob anywhere) -> LIKE
    - ``rule.created`` -> exact match
    Several values may be comma-separated (OR).
    """
    values = [v.strip() for v in event_type.split(",") if v.strip()]
    conds = []
    for value in values:
        if "*" in value:
            pattern = "%".join(_like_escape(part) for part in value.split("*"))
            conds.append(AuditLogs.event_type.like(pattern, escape="\\"))
        elif value.endswith("."):
            conds.append(AuditLogs.event_type.like(f"{_like_escape(value)}%", escape="\\"))
        elif "." not in value:
            conds.append(
                or_(
                    AuditLogs.event_type == value,
                    AuditLogs.event_type.like(f"{_like_escape(value)}.%", escape="\\"),
                )
            )
        else:
            conds.append(AuditLogs.event_type == value)
    if not conds:
        return true()
    return conds[0] if len(conds) == 1 else or_(*conds)


class AuditLogRepository(BaseRepository[AuditLogs]):
    """Insert and query audit rows."""

    def __init__(self, session: AsyncSession):
        super().__init__(AuditLogs, session)

    async def create_log(self, **fields: Any) -> AuditLogs:
        # The ORM column for "metadata" is metadata_ (sqlacodegen renames the reserved name).
        if "metadata" in fields:
            fields["metadata_"] = fields.pop("metadata") or {}
        return await self.create(**fields)

    def _filters(
        self,
        *,
        event_type: Optional[str] = None,
        actor_type: Optional[str] = None,
        actor_id: Optional[uuid.UUID] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[uuid.UUID] = None,
        outcome: Optional[str] = None,
        request_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        from_: Optional[datetime] = None,
        to: Optional[datetime] = None,
    ) -> list:
        conds = []
        if event_type:
            conds.append(event_type_condition(event_type))
        if actor_type:
            conds.append(AuditLogs.actor_type == actor_type)
        if actor_id is not None:
            conds.append(AuditLogs.actor_id == actor_id)
        if resource_type:
            conds.append(AuditLogs.resource_type == resource_type)
        if resource_id is not None:
            conds.append(AuditLogs.resource_id == resource_id)
        if outcome:
            conds.append(AuditLogs.outcome == outcome)
        if request_id is not None:
            conds.append(AuditLogs.request_id == request_id)
        if search:
            pattern = f"%{search}%"
            conds.append(
                or_(
                    AuditLogs.message.ilike(pattern),
                    AuditLogs.resource_name.ilike(pattern),
                )
            )
        if from_ is not None:
            conds.append(AuditLogs.created_at >= from_)
        if to is not None:
            conds.append(AuditLogs.created_at < to)
        return conds

    async def search(
        self, *, skip: int, limit: int, sort: Optional[str] = None, **filters: Any
    ) -> Tuple[List[AuditLogs], int]:
        conds = self._filters(**filters)
        total = (
            await self.session.execute(select(func.count()).select_from(AuditLogs).where(*conds))
        ).scalar_one()
        rows = await self.session.execute(
            select(AuditLogs).where(*conds).order_by(*_order(sort)).offset(skip).limit(limit)
        )
        return list(rows.scalars().all()), int(total)

    async def iter_for_export(self, *, max_rows: int, batch: int = 1000, **filters: Any):
        """Yield rows oldest-first in batches (bounded by max_rows)."""
        conds = self._filters(**filters)
        offset = 0
        while offset < max_rows:
            size = min(batch, max_rows - offset)
            rows = await self.session.execute(
                select(AuditLogs)
                .where(*conds)
                .order_by(AuditLogs.created_at.asc(), AuditLogs.id)
                .offset(offset)
                .limit(size)
            )
            chunk = list(rows.scalars().all())
            if not chunk:
                return
            for row in chunk:
                yield row
            if len(chunk) < size:
                return
            offset += size

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self.session.execute(delete(AuditLogs).where(AuditLogs.created_at < cutoff))
        await self.session.flush()
        return result.rowcount

    @staticmethod
    def to_dict(row: AuditLogs) -> Dict[str, Any]:
        return {
            "id": row.id,
            "event_type": row.event_type,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "actor_name": row.actor_name,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "resource_name": row.resource_name,
            "before_state": row.before_state,
            "after_state": row.after_state,
            "outcome": row.outcome,
            "message": row.message,
            "ip_address": str(row.ip_address) if row.ip_address is not None else None,
            "user_agent": row.user_agent,
            "request_id": row.request_id,
            "metadata": row.metadata_ or {},
            "created_at": row.created_at,
        }


def _order(sort: Optional[str]):
    sort = sort or "-created_at"
    desc = sort.startswith("-")
    field = sort.lstrip("-+")
    if field not in SORTABLE:
        field, desc = "created_at", True
    column = getattr(AuditLogs, field)
    return [column.desc() if desc else column.asc(), AuditLogs.id]
