"""
Repositories for Anthropic Message Batches (``batches``, ``batch_items``).

 Items are claimed with ``FOR UPDATE SKIP LOCKED`` so several
jobs workers can process one batch concurrently without double-running an
item.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import Batches, BatchItems
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import and_, func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

ITEM_COUNT_KEYS = ("processing", "succeeded", "errored", "canceled", "expired")


class BatchRepository(BaseRepository[Batches]):
    def __init__(self, session: AsyncSession):
        super().__init__(Batches, session)

    async def get_by_external_id(self, external_id: str) -> Optional[Batches]:
        result = await self.session.execute(
            select(Batches).where(Batches.external_id == external_id)
        )
        return result.scalar_one_or_none()

    async def get_by_any_id(self, batch_id: str) -> Optional[Batches]:
        """Admin lookups accept the uuid or the public ``msgbatch_`` id."""
        try:
            return await self.get_by_id(uuid.UUID(str(batch_id)))
        except ValueError:
            return await self.get_by_external_id(str(batch_id))

    async def list_for_key(
        self,
        api_key_id: Optional[str],
        limit: int,
        before_id: Optional[str] = None,
        after_id: Optional[str] = None,
    ) -> Tuple[List[Batches], bool]:
        """Anthropic cursor pagination, newest first. ``after_id`` pages to older batches."""
        query = select(Batches)
        if api_key_id:
            query = query.where(Batches.api_key_id == uuid.UUID(str(api_key_id)))
        ascending = False
        cursor_id = after_id or before_id
        if cursor_id:
            cursor = await self.get_by_external_id(cursor_id)
            if cursor is not None:
                key = (cursor.created_at, cursor.id)
                if after_id:
                    query = query.where(
                        or_(
                            Batches.created_at < key[0],
                            and_(Batches.created_at == key[0], Batches.id < key[1]),
                        )
                    )
                else:
                    ascending = True
                    query = query.where(
                        or_(
                            Batches.created_at > key[0],
                            and_(Batches.created_at == key[0], Batches.id > key[1]),
                        )
                    )
        if ascending:
            query = query.order_by(Batches.created_at.asc(), Batches.id.asc())
        else:
            query = query.order_by(Batches.created_at.desc(), Batches.id.desc())
        result = await self.session.execute(query.limit(limit + 1))
        rows = list(result.scalars().all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        if ascending:
            rows.reverse()
        return rows, has_more

    async def list_admin(
        self,
        page: int,
        page_size: int,
        api_key_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
    ) -> Tuple[List[Batches], int]:
        conditions = []
        if api_key_id:
            conditions.append(Batches.api_key_id == api_key_id)
        if status:
            conditions.append(Batches.status == status)
        total = (
            await self.session.execute(select(func.count()).select_from(Batches).where(*conditions))
        ).scalar_one()
        result = await self.session.execute(
            select(Batches)
            .where(*conditions)
            .order_by(Batches.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total)

    async def active_ids(self, limit: int = 100) -> List[uuid.UUID]:
        """Batches with work left (in_progress / canceling), oldest first."""
        result = await self.session.execute(
            select(Batches.id)
            .where(Batches.status.in_(("in_progress", "canceling")))
            .order_by(Batches.created_at.asc())
            .limit(limit)
        )
        return [r[0] for r in result.all()]

    async def refresh_counts(self, batch_id: uuid.UUID) -> Optional[Batches]:
        """Recompute request_counts from the items; end the batch when nothing is left."""
        result = await self.session.execute(
            select(BatchItems.status, func.count())
            .where(BatchItems.batch_id == batch_id)
            .group_by(BatchItems.status)
        )
        by_status: Dict[str, int] = {str(s): int(c) for s, c in result.all()}
        counts = {
            "processing": by_status.get("pending", 0) + by_status.get("processing", 0),
            "succeeded": by_status.get("succeeded", 0),
            "errored": by_status.get("errored", 0),
            "canceled": by_status.get("canceled", 0),
            "expired": by_status.get("expired", 0),
        }
        values: Dict[str, Any] = {"request_counts": counts}
        if counts["processing"] == 0:
            values["status"] = "ended"
            values["ended_at"] = datetime.now(timezone.utc)
        await self.session.execute(
            update(Batches)
            .where(Batches.id == batch_id, Batches.status != "ended")
            .values(**values)
        )
        await self.session.flush()
        return await self.get_by_id(batch_id)

    async def archive_older_than(self, cutoff: datetime) -> int:
        """Ended batches past the results retention: drop items, mark archived."""
        result = await self.session.execute(
            text(
                "WITH old AS (SELECT id FROM batches WHERE status = 'ended' "
                "AND archived_at IS NULL AND ended_at < :cutoff) "
                "UPDATE batches SET archived_at = NOW() WHERE id IN (SELECT id FROM old) "
                "RETURNING id"
            ),
            {"cutoff": cutoff},
        )
        ids = [r[0] for r in result.all()]
        if ids:
            await self.session.execute(
                text("DELETE FROM batch_items WHERE batch_id = ANY(:ids)"), {"ids": ids}
            )
        await self.session.flush()
        return len(ids)


class BatchItemRepository(BaseRepository[BatchItems]):
    def __init__(self, session: AsyncSession):
        super().__init__(BatchItems, session)

    async def bulk_create(  # type: ignore[override]
        self, records: List[Dict[str, Any]]
    ) -> List[BatchItems]:
        """Plain multi-row INSERT (no per-row refresh: batches hold up to 10k items)."""
        if not records:
            return []
        rows = [{"id": uuid.uuid4(), **r} for r in records]
        for start in range(0, len(rows), 1000):
            await self.session.execute(insert(BatchItems), rows[start : start + 1000])
        await self.session.flush()
        return []

    async def cancel_pending(self, batch_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(BatchItems)
            .where(BatchItems.batch_id == batch_id, BatchItems.status == "pending")
            .values(status="canceled", finished_at=datetime.now(timezone.utc))
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def expire_pending(self, batch_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(BatchItems)
            .where(
                BatchItems.batch_id == batch_id,
                BatchItems.status.in_(("pending", "processing")),
            )
            .values(status="expired", finished_at=datetime.now(timezone.utc))
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def claim(self, batch_id: uuid.UUID, limit: int) -> List[BatchItems]:
        """Atomically move up to ``limit`` pending items to processing and return them."""
        sub = (
            select(BatchItems.id)
            .where(BatchItems.batch_id == batch_id, BatchItems.status == "pending")
            .order_by(BatchItems.created_at.asc(), BatchItems.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(
            update(BatchItems)
            .where(BatchItems.id.in_(sub.scalar_subquery()))
            .values(status="processing", started_at=datetime.now(timezone.utc))
            .returning(BatchItems)
        )
        rows = list(result.scalars().all())
        await self.session.flush()
        return rows

    async def requeue_stale(self, batch_id: uuid.UUID, older_than: datetime) -> int:
        """Items stuck in processing (worker died) go back to pending."""
        result = await self.session.execute(
            update(BatchItems)
            .where(
                BatchItems.batch_id == batch_id,
                BatchItems.status == "processing",
                BatchItems.started_at < older_than,
            )
            .values(status="pending", started_at=None)
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def finish(
        self,
        item_id: uuid.UUID,
        status: str,
        *,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> None:
        await self.session.execute(
            update(BatchItems)
            .where(BatchItems.id == item_id, BatchItems.status == "processing")
            .values(
                status=status,
                result=result,
                error=error,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finished_at=datetime.now(timezone.utc),
            )
        )
        await self.session.flush()

    async def page(
        self, batch_id: uuid.UUID, after: Optional[uuid.UUID], size: int
    ) -> Sequence[BatchItems]:
        query = select(BatchItems).where(BatchItems.batch_id == batch_id)
        if after is not None:
            query = query.where(BatchItems.id > after)
        result = await self.session.execute(query.order_by(BatchItems.id.asc()).limit(size))
        return list(result.scalars().all())
