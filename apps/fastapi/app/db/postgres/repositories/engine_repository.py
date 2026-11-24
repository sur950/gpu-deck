"""
Repositories for the engine registry: engines, engine_versions, engine_compat_checks.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    Deployments,
    EngineCompatChecks,
    Engines,
    EngineVersions,
)
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class EngineRepository(BaseRepository[Engines]):
    """``engines`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(Engines, session)

    async def get_active(self, engine_id: uuid.UUID) -> Optional[Engines]:
        result = await self.session.execute(
            select(Engines).where(Engines.id == engine_id, Engines.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[Engines]:
        result = await self.session.execute(
            select(Engines).where(Engines.name == name, Engines.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def list_engines(
        self,
        *,
        enabled: Optional[bool] = None,
        model_type: Optional[str] = None,
        vendor: Optional[str] = None,
    ) -> List[Engines]:
        conditions = [Engines.deleted_at.is_(None)]
        if enabled is not None:
            conditions.append(Engines.is_enabled.is_(enabled))
        if model_type:
            conditions.append(Engines.supported_model_types.any(model_type))
        if vendor:
            conditions.append(Engines.supported_vendors.any(vendor))
        result = await self.session.execute(
            select(Engines).where(and_(*conditions)).order_by(Engines.id)
        )
        return list(result.scalars().all())


class EngineVersionRepository(BaseRepository[EngineVersions]):
    """``engine_versions`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(EngineVersions, session)

    async def get_active(self, version_id: uuid.UUID) -> Optional[EngineVersions]:
        result = await self.session.execute(
            select(EngineVersions).where(
                EngineVersions.id == version_id, EngineVersions.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def list_for_engine(self, engine_id: uuid.UUID) -> List[EngineVersions]:
        result = await self.session.execute(
            select(EngineVersions)
            .where(
                EngineVersions.engine_id == engine_id,
                EngineVersions.deleted_at.is_(None),
            )
            .order_by(EngineVersions.is_default.desc(), EngineVersions.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_for_engines(
        self, engine_ids: Sequence[uuid.UUID]
    ) -> Dict[uuid.UUID, List[EngineVersions]]:
        if not engine_ids:
            return {}
        result = await self.session.execute(
            select(EngineVersions)
            .where(
                EngineVersions.engine_id.in_(list(engine_ids)),
                EngineVersions.deleted_at.is_(None),
            )
            .order_by(EngineVersions.is_default.desc(), EngineVersions.created_at.desc())
        )
        out: Dict[uuid.UUID, List[EngineVersions]] = {}
        for v in result.scalars().all():
            out.setdefault(v.engine_id, []).append(v)
        return out

    async def list_all_with_engine(
        self, only_available: bool = True
    ) -> List[Tuple[EngineVersions, Engines]]:
        conditions = [EngineVersions.deleted_at.is_(None), Engines.deleted_at.is_(None)]
        if only_available:
            conditions.append(EngineVersions.status != "disabled")
        result = await self.session.execute(
            select(EngineVersions, Engines)
            .join(Engines, Engines.id == EngineVersions.engine_id)
            .where(and_(*conditions))
            .order_by(
                Engines.id,
                EngineVersions.is_default.desc(),
                EngineVersions.created_at.desc(),
            )
        )
        return [(row[0], row[1]) for row in result.all()]

    async def get_with_engine(
        self, version_id: uuid.UUID
    ) -> Optional[Tuple[EngineVersions, Engines]]:
        result = await self.session.execute(
            select(EngineVersions, Engines)
            .join(Engines, Engines.id == EngineVersions.engine_id)
            .where(EngineVersions.id == version_id, EngineVersions.deleted_at.is_(None))
        )
        row = result.first()
        return (row[0], row[1]) if row else None

    async def get_by_label(self, engine_id: uuid.UUID, version: str) -> Optional[EngineVersions]:
        result = await self.session.execute(
            select(EngineVersions).where(
                EngineVersions.engine_id == engine_id,
                EngineVersions.version == version,
                EngineVersions.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def clear_default(self, engine_id: uuid.UUID) -> None:
        await self.session.execute(
            update(EngineVersions)
            .where(
                EngineVersions.engine_id == engine_id,
                EngineVersions.deleted_at.is_(None),
            )
            .values(is_default=False)
        )
        await self.session.flush()

    async def soft_delete(self, version_id: uuid.UUID) -> None:
        await self.session.execute(
            update(EngineVersions)
            .where(EngineVersions.id == version_id)
            .values(deleted_at=datetime.now(timezone.utc), is_default=False)
        )
        await self.session.flush()

    async def deployment_count(self, version_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Deployments)
            .where(
                Deployments.engine_version_id == version_id,
                Deployments.deleted_at.is_(None),
            )
        )
        return int(result.scalar_one())

    @staticmethod
    def port_range_value(lo: int, hi: int) -> Range:
        return Range(lo, hi, bounds="[)")


class EngineCompatCheckRepository(BaseRepository[EngineCompatChecks]):
    """``engine_compat_checks`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(EngineCompatChecks, session)

    async def list_checks(
        self,
        *,
        page: int,
        page_size: int,
        model_version_id: Optional[uuid.UUID] = None,
        engine_version_id: Optional[uuid.UUID] = None,
        result: Optional[str] = None,
    ) -> Tuple[List[Tuple[EngineCompatChecks, EngineVersions, Engines]], int]:
        conditions = []
        if model_version_id:
            conditions.append(EngineCompatChecks.model_version_id == model_version_id)
        if engine_version_id:
            conditions.append(EngineCompatChecks.engine_version_id == engine_version_id)
        if result:
            conditions.append(EngineCompatChecks.result == result)
        base = (
            select(EngineCompatChecks, EngineVersions, Engines)
            .join(
                EngineVersions,
                EngineVersions.id == EngineCompatChecks.engine_version_id,
            )
            .join(Engines, Engines.id == EngineVersions.engine_id)
        )
        if conditions:
            base = base.where(and_(*conditions))
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                base.order_by(EngineCompatChecks.checked_at.desc(), EngineCompatChecks.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return [(r[0], r[1], r[2]) for r in rows], int(total)

    async def record(self, **values: Any) -> EngineCompatChecks:
        return await self.create(**values)
