"""
Repositories for the rules engine: ``rules`` and ``rule_runs``.

All SQL for the two tables lives here (flush, never commit).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import RuleRuns, Rules
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import String, and_, cast, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

_SORTABLE = {
    "name",
    "type",
    "priority",
    "created_at",
    "updated_at",
    "last_fired_at",
    "enabled",
}


def parse_sort(sort: Optional[str], allowed: set, default: str = "-created_at") -> Tuple[str, bool]:
    """``-field`` -> (field, desc). Unknown fields fall back to the default."""
    value = (sort or default).strip()
    desc = value.startswith("-")
    field = value.lstrip("-+")
    if field not in allowed:
        value = default
        desc = value.startswith("-")
        field = value.lstrip("-+")
    return field, desc


class RuleRepository(BaseRepository[Rules]):
    """CRUD and listing for the ``rules`` table (soft delete via deleted_at)."""

    def __init__(self, session: AsyncSession):
        super().__init__(Rules, session)

    async def get_active(self, rule_id: uuid.UUID) -> Optional[Rules]:
        result = await self.session.execute(
            select(Rules).where(Rules.id == rule_id, Rules.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def name_exists(self, name: str, exclude_id: Optional[uuid.UUID] = None) -> bool:
        query = select(Rules.id).where(
            func.lower(Rules.name) == name.lower(), Rules.deleted_at.is_(None)
        )
        if exclude_id is not None:
            query = query.where(Rules.id != exclude_id)
        result = await self.session.execute(query.limit(1))
        return result.scalar_one_or_none() is not None

    async def list(
        self,
        page: int = 1,
        page_size: int = 50,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        rule_type: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Tuple[List[Rules], int]:
        conditions = [Rules.deleted_at.is_(None)]
        if search:
            conditions.append(Rules.name.ilike(f"%{search}%"))
        if rule_type:
            conditions.append(Rules.type == rule_type)
        if enabled is not None:
            conditions.append(Rules.enabled == enabled)
        where = and_(*conditions)
        total = (
            await self.session.execute(select(func.count()).select_from(Rules).where(where))
        ).scalar_one()
        field, desc = parse_sort(sort, _SORTABLE)
        column = getattr(Rules, field)
        query = (
            select(Rules)
            .where(where)
            .order_by(column.desc() if desc else column.asc(), Rules.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self.session.execute(query)).scalars().all())
        return rows, total

    async def list_enabled(self, rule_type: Optional[str] = None) -> List[Rules]:
        """Enabled, non-deleted rules in evaluation order (priority asc, created_at asc)."""
        query = select(Rules).where(Rules.deleted_at.is_(None), Rules.enabled.is_(True))
        if rule_type:
            query = query.where(Rules.type == rule_type)
        query = query.order_by(Rules.priority.asc(), Rules.created_at.asc())
        return list((await self.session.execute(query)).scalars().all())

    async def list_by_type(self, rule_type: str, include_disabled: bool = True) -> List[Rules]:
        query = select(Rules).where(Rules.deleted_at.is_(None), Rules.type == rule_type)
        if not include_disabled:
            query = query.where(Rules.enabled.is_(True))
        return list((await self.session.execute(query.order_by(Rules.created_at)).scalars().all()))

    async def rules_referencing_layout(self, layout_id: uuid.UUID) -> List[Rules]:
        query = select(Rules).where(
            Rules.deleted_at.is_(None),
            Rules.type == "time_schedule",
            Rules.params["layout_id"].astext == str(layout_id),
        )
        return list((await self.session.execute(query)).scalars().all())

    async def soft_delete(self, rule_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(Rules)
            .where(Rules.id == rule_id, Rules.deleted_at.is_(None))
            .values(deleted_at=datetime.now(timezone.utc), enabled=False)
        )
        await self.session.flush()
        return result.rowcount > 0

    async def mark_evaluated(self, rule_id: uuid.UUID, at: datetime) -> None:
        await self.session.execute(
            update(Rules).where(Rules.id == rule_id).values(last_evaluated_at=at)
        )

    async def mark_fired(self, rule_id: uuid.UUID, at: datetime) -> None:
        await self.session.execute(
            update(Rules)
            .where(Rules.id == rule_id)
            .values(last_fired_at=at, fire_count=Rules.fire_count + 1, last_evaluated_at=at)
        )


class RuleRunRepository(BaseRepository[RuleRuns]):
    """History rows for rule evaluations."""

    def __init__(self, session: AsyncSession):
        super().__init__(RuleRuns, session)

    async def latest_for_rules(self, rule_ids: List[uuid.UUID]) -> Dict[uuid.UUID, RuleRuns]:
        """Most recent run per rule (for Rule.last_run)."""
        if not rule_ids:
            return {}
        query = (
            select(RuleRuns)
            .distinct(RuleRuns.rule_id)
            .where(RuleRuns.rule_id.in_(rule_ids))
            .order_by(RuleRuns.rule_id, RuleRuns.evaluated_at.desc())
        )
        rows = (await self.session.execute(query)).scalars().all()
        return {row.rule_id: row for row in rows}

    async def last_no_match_at(self, rule_id: uuid.UUID) -> Optional[datetime]:
        query = (
            select(RuleRuns.evaluated_at)
            .where(RuleRuns.rule_id == rule_id, RuleRuns.outcome == "no_match")
            .order_by(RuleRuns.evaluated_at.desc())
            .limit(1)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def last_fire_for_deployment(
        self, rule_id: uuid.UUID, deployment_id: uuid.UUID
    ) -> Optional[datetime]:
        """Fallback cooldown source when Redis is unavailable."""
        query = (
            select(RuleRuns.evaluated_at)
            .where(
                RuleRuns.rule_id == rule_id,
                RuleRuns.outcome.in_(["fired", "dry_run"]),
                cast(RuleRuns.decision, JSONB)["deployment_ids"].contains([str(deployment_id)]),
            )
            .order_by(RuleRuns.evaluated_at.desc())
            .limit(1)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list(
        self,
        page: int = 1,
        page_size: int = 50,
        rule_id: Optional[uuid.UUID] = None,
        rule_type: Optional[str] = None,
        outcome: Optional[str] = None,
        fired: Optional[bool] = None,
        deployment_id: Optional[uuid.UUID] = None,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
    ) -> Tuple[List[RuleRuns], int]:
        conditions: List[Any] = []
        if rule_id is not None:
            conditions.append(RuleRuns.rule_id == rule_id)
        if rule_type:
            conditions.append(RuleRuns.rule_type == rule_type)
        if outcome:
            conditions.append(RuleRuns.outcome == outcome)
        if fired is not None:
            conditions.append(RuleRuns.fired == fired)
        if deployment_id is not None:
            conditions.append(
                or_(
                    cast(RuleRuns.decision, JSONB)["deployment_ids"].contains([str(deployment_id)]),
                    cast(RuleRuns.actions, JSONB).contains([{"target_id": str(deployment_id)}]),
                )
            )
        if from_ts is not None:
            conditions.append(RuleRuns.evaluated_at >= from_ts)
        if to_ts is not None:
            conditions.append(RuleRuns.evaluated_at < to_ts)
        where = and_(*conditions) if conditions else True
        total = (
            await self.session.execute(select(func.count()).select_from(RuleRuns).where(where))
        ).scalar_one()
        query = (
            select(RuleRuns)
            .where(where)
            .order_by(RuleRuns.evaluated_at.desc(), RuleRuns.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self.session.execute(query)).scalars().all())
        return rows, total

    async def rule_names(self, rule_ids: List[uuid.UUID]) -> Dict[uuid.UUID, str]:
        if not rule_ids:
            return {}
        rows = await self.session.execute(
            select(Rules.id, cast(Rules.name, String)).where(Rules.id.in_(rule_ids))
        )
        return {row[0]: row[1] for row in rows.all()}

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self.session.execute(delete(RuleRuns).where(RuleRuns.evaluated_at < cutoff))
        await self.session.flush()
        return result.rowcount or 0
