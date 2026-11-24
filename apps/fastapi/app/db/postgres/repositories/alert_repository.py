"""
Repositories for alerts: ``alert_channels``, ``alert_events``, ``alert_deliveries``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    AlertChannels,
    AlertDeliveries,
    AlertEvents,
)
from app.db.postgres.repositories.base_repository import BaseRepository
from app.db.postgres.repositories.rule_repository import parse_sort
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

_CHANNEL_SORTABLE = {"name", "type", "created_at", "updated_at", "enabled"}
_EVENT_SORTABLE = {"created_at", "last_seen_at", "first_seen_at", "severity", "status"}


class AlertChannelRepository(BaseRepository[AlertChannels]):
    """Slack / email / webhook destinations."""

    def __init__(self, session: AsyncSession):
        super().__init__(AlertChannels, session)

    async def get_active(self, channel_id: uuid.UUID) -> Optional[AlertChannels]:
        result = await self.session.execute(
            select(AlertChannels).where(
                AlertChannels.id == channel_id, AlertChannels.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def name_exists(self, name: str, exclude_id: Optional[uuid.UUID] = None) -> bool:
        query = select(AlertChannels.id).where(
            func.lower(AlertChannels.name) == name.lower(),
            AlertChannels.deleted_at.is_(None),
        )
        if exclude_id is not None:
            query = query.where(AlertChannels.id != exclude_id)
        return (await self.session.execute(query.limit(1))).scalar_one_or_none() is not None

    async def list(
        self,
        page: int = 1,
        page_size: int = 50,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        channel_type: Optional[str] = None,
    ) -> Tuple[List[AlertChannels], int]:
        conditions = [AlertChannels.deleted_at.is_(None)]
        if search:
            conditions.append(AlertChannels.name.ilike(f"%{search}%"))
        if channel_type:
            conditions.append(AlertChannels.type == channel_type)
        where = and_(*conditions)
        total = (
            await self.session.execute(select(func.count()).select_from(AlertChannels).where(where))
        ).scalar_one()
        field, desc = parse_sort(sort, _CHANNEL_SORTABLE)
        column = getattr(AlertChannels, field)
        query = (
            select(AlertChannels)
            .where(where)
            .order_by(column.desc() if desc else column.asc(), AlertChannels.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list((await self.session.execute(query)).scalars().all()), total

    async def list_enabled(self) -> List[AlertChannels]:
        query = select(AlertChannels).where(
            AlertChannels.deleted_at.is_(None), AlertChannels.enabled.is_(True)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def names(self, channel_ids: List[uuid.UUID]) -> Dict[uuid.UUID, str]:
        if not channel_ids:
            return {}
        rows = await self.session.execute(
            select(AlertChannels.id, AlertChannels.name).where(AlertChannels.id.in_(channel_ids))
        )
        return {row[0]: row[1] for row in rows.all()}

    async def soft_delete(self, channel_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(AlertChannels)
            .where(AlertChannels.id == channel_id, AlertChannels.deleted_at.is_(None))
            .values(deleted_at=datetime.now(timezone.utc), enabled=False)
        )
        await self.session.flush()
        return result.rowcount > 0

    async def record_test(
        self, channel_id: uuid.UUID, ok: bool, error: Optional[str], at: datetime
    ) -> None:
        await self.session.execute(
            update(AlertChannels)
            .where(AlertChannels.id == channel_id)
            .values(last_test_at=at, last_test_ok=ok, last_error=(error or None))
        )
        await self.session.flush()


class AlertEventRepository(BaseRepository[AlertEvents]):
    """Alert events with dedupe support."""

    def __init__(self, session: AsyncSession):
        super().__init__(AlertEvents, session)

    async def find_open_duplicate(
        self, dedupe_key: str, window_seconds: int, now: datetime
    ) -> Optional[AlertEvents]:
        query = (
            select(AlertEvents)
            .where(
                AlertEvents.dedupe_key == dedupe_key,
                AlertEvents.status == "open",
                AlertEvents.last_seen_at >= now - timedelta(seconds=window_seconds),
            )
            .order_by(AlertEvents.last_seen_at.desc())
            .limit(1)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def bump_occurrence(self, event: AlertEvents, now: datetime, payload: Dict) -> None:
        event.occurrences = (event.occurrences or 1) + 1
        event.last_seen_at = now
        if payload:
            event.payload = payload
        await self.session.flush()

    async def list(
        self,
        page: int = 1,
        page_size: int = 50,
        sort: Optional[str] = None,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        event_type_prefix: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[uuid.UUID] = None,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
    ) -> Tuple[List[AlertEvents], int]:
        conditions: List[Any] = []
        if severity:
            conditions.append(AlertEvents.severity == severity)
        if status:
            conditions.append(AlertEvents.status == status)
        if event_type_prefix:
            conditions.append(AlertEvents.event_type.like(f"{event_type_prefix}%"))
        if resource_type:
            conditions.append(AlertEvents.resource_type == resource_type)
        if resource_id is not None:
            conditions.append(AlertEvents.resource_id == resource_id)
        if from_ts is not None:
            conditions.append(AlertEvents.last_seen_at >= from_ts)
        if to_ts is not None:
            conditions.append(AlertEvents.last_seen_at < to_ts)
        where = and_(*conditions) if conditions else True
        total = (
            await self.session.execute(select(func.count()).select_from(AlertEvents).where(where))
        ).scalar_one()
        field, desc = parse_sort(sort, _EVENT_SORTABLE, default="-last_seen_at")
        column = getattr(AlertEvents, field)
        query = (
            select(AlertEvents)
            .where(where)
            .order_by(column.desc() if desc else column.asc(), AlertEvents.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list((await self.session.execute(query)).scalars().all()), total

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(AlertEvents).where(AlertEvents.last_seen_at < cutoff)
        )
        await self.session.flush()
        return result.rowcount or 0


class AlertDeliveryRepository(BaseRepository[AlertDeliveries]):
    """One row per (event, channel) delivery chain."""

    def __init__(self, session: AsyncSession):
        super().__init__(AlertDeliveries, session)

    async def for_event(self, event_id: uuid.UUID) -> List[AlertDeliveries]:
        query = (
            select(AlertDeliveries)
            .where(AlertDeliveries.alert_event_id == event_id)
            .order_by(AlertDeliveries.created_at)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def for_events(
        self, event_ids: List[uuid.UUID]
    ) -> Dict[uuid.UUID, List[AlertDeliveries]]:
        if not event_ids:
            return {}
        query = (
            select(AlertDeliveries)
            .where(AlertDeliveries.alert_event_id.in_(event_ids))
            .order_by(AlertDeliveries.created_at)
        )
        out: Dict[uuid.UUID, List[AlertDeliveries]] = {eid: [] for eid in event_ids}
        for row in (await self.session.execute(query)).scalars().all():
            out.setdefault(row.alert_event_id, []).append(row)
        return out

    async def get_or_create(self, event_id: uuid.UUID, channel_id: uuid.UUID) -> AlertDeliveries:
        query = select(AlertDeliveries).where(
            AlertDeliveries.alert_event_id == event_id,
            AlertDeliveries.channel_id == channel_id,
        )
        existing = (await self.session.execute(query)).scalar_one_or_none()
        if existing is not None:
            return existing
        return await self.create(alert_event_id=event_id, channel_id=channel_id, status="pending")

    async def reset_for_resend(self, delivery: AlertDeliveries) -> None:
        delivery.status = "pending"
        delivery.attempts = 0
        delivery.error_message = None
        delivery.response_code = None
        delivery.sent_at = None
        await self.session.flush()

    async def list_pending(self, max_attempts: int, limit: int = 200) -> List[AlertDeliveries]:
        query = (
            select(AlertDeliveries)
            .where(
                AlertDeliveries.status.in_(["pending", "failed"]),
                AlertDeliveries.attempts < max_attempts,
            )
            .order_by(AlertDeliveries.created_at)
            .limit(limit)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def record_attempt(
        self,
        delivery: AlertDeliveries,
        ok: bool,
        response_code: Optional[int],
        error: Optional[str],
        at: datetime,
        max_attempts: int,
    ) -> None:
        delivery.attempts = (delivery.attempts or 0) + 1
        delivery.response_code = response_code
        if ok:
            delivery.status = "sent"
            delivery.sent_at = at
            delivery.error_message = None
        else:
            delivery.error_message = (error or "delivery failed")[:2048]
            delivery.status = "failed" if delivery.attempts >= max_attempts else "pending"
        await self.session.flush()

    async def suppress(self, delivery: AlertDeliveries, reason: str) -> None:
        delivery.status = "suppressed"
        delivery.error_message = reason[:2048]
        await self.session.flush()
