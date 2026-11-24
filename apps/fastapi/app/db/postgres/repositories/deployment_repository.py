"""
Repository for ``deployments`` plus the read-only joins the deployment views
need (model, engine version, active swaps). Flush only, never commit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    Deployments,
    Engines,
    EngineVersions,
    Models,
    SwapOperations,
)
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

DEPLOYMENT_SORT_FIELDS = {"name", "created_at", "updated_at", "status", "replicas"}
TERMINAL_SWAP_STATES = ("completed", "failed", "rolled_back", "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DeploymentRepository(BaseRepository[Deployments]):
    """``deployments`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(Deployments, session)

    async def get_active(self, deployment_id: uuid.UUID) -> Optional[Deployments]:
        result = await self.session.execute(
            select(Deployments).where(
                Deployments.id == deployment_id, Deployments.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def get_any(self, deployment_id: uuid.UUID) -> Optional[Deployments]:
        """Including soft-deleted rows (reconciler finishes deletes)."""
        return await self.get_by_id(deployment_id)

    async def get_by_name(self, name: str) -> Optional[Deployments]:
        result = await self.session.execute(
            select(Deployments).where(
                func.lower(Deployments.name) == name.lower(),
                Deployments.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_deployments(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        status: Optional[str] = None,
        model_id: Optional[uuid.UUID] = None,
        cluster_id: Optional[uuid.UUID] = None,
        engine: Optional[str] = None,
        priority: Optional[str] = None,
        origin: Optional[str] = None,
    ) -> Tuple[List[Deployments], int]:
        # Outer joins: external (observed) deployments have no model / engine version.
        base = (
            select(Deployments)
            .outerjoin(Models, Models.id == Deployments.model_id)
            .outerjoin(EngineVersions, EngineVersions.id == Deployments.engine_version_id)
            .outerjoin(Engines, Engines.id == EngineVersions.engine_id)
        )
        conditions = [Deployments.deleted_at.is_(None)]
        if origin:
            conditions.append(Deployments.origin == origin)
        if search:
            like = f"%{search}%"
            conditions.append(
                or_(
                    Deployments.name.ilike(like),
                    Models.name.ilike(like),
                    Deployments.external["models"].astext.ilike(like),
                )
            )
        if status:
            conditions.append(Deployments.status == status)
        if model_id:
            conditions.append(Deployments.model_id == model_id)
        if cluster_id:
            conditions.append(Deployments.cluster_id == cluster_id)
        if engine:
            conditions.append(Engines.name == engine)
        if priority:
            conditions.append(
                func.coalesce(Deployments.priority_override, Models.priority) == priority
            )
        base = base.where(and_(*conditions))
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        order = _order(sort)
        rows = (
            await self.session.execute(
                base.order_by(*order).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars()
        return list(rows), int(total)

    async def list_for_reconcile(self) -> List[Deployments]:
        """Live managed deployments plus soft-deleted ones still marked ``deleting``. External
        (observed) deployments are never reconciled."""
        result = await self.session.execute(
            select(Deployments)
            .where(
                or_(
                    Deployments.deleted_at.is_(None),
                    Deployments.status == "deleting",
                ),
                Deployments.origin == "managed",
            )
            .order_by(Deployments.created_at)
        )
        return list(result.scalars().all())

    async def context(
        self, deployment_ids: Sequence[uuid.UUID]
    ) -> Dict[uuid.UUID, Tuple[Models, EngineVersions, Engines]]:
        """Model, engine version and engine for each deployment id."""
        if not deployment_ids:
            return {}
        result = await self.session.execute(
            select(Deployments.id, Models, EngineVersions, Engines)
            .join(Models, Models.id == Deployments.model_id)
            .join(EngineVersions, EngineVersions.id == Deployments.engine_version_id)
            .join(Engines, Engines.id == EngineVersions.engine_id)
            .where(Deployments.id.in_(list(deployment_ids)))
        )
        return {row[0]: (row[1], row[2], row[3]) for row in result.all()}

    async def set_status(
        self, deployment_id: uuid.UUID, status: str, message: Optional[str] = None
    ) -> None:
        await self.session.execute(
            update(Deployments)
            .where(Deployments.id == deployment_id)
            .values(status=status, status_message=message, updated_at=_now())
        )
        await self.session.flush()

    async def soft_delete(self, deployment_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Deployments)
            .where(Deployments.id == deployment_id)
            .values(
                deleted_at=_now(),
                desired_running=False,
                status="deleting",
                status_message="unloading instances",
            )
        )
        await self.session.flush()

    async def count_for_model_version(self, model_version_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Deployments)
            .where(
                Deployments.model_version_id == model_version_id,
                Deployments.deleted_at.is_(None),
            )
        )
        return int(result.scalar_one())

    # ------------------------------------------------- swap_operations (read)

    async def active_swap_ids(
        self, deployment_ids: Sequence[uuid.UUID]
    ) -> Dict[uuid.UUID, List[uuid.UUID]]:
        if not deployment_ids:
            return {}
        result = await self.session.execute(
            select(SwapOperations.deployment_id, SwapOperations.id).where(
                SwapOperations.deployment_id.in_(list(deployment_ids)),
                SwapOperations.state.notin_(TERMINAL_SWAP_STATES),
                SwapOperations.dry_run.is_(False),
            )
        )
        out: Dict[uuid.UUID, List[uuid.UUID]] = {}
        for dep_id, swap_id in result.all():
            out.setdefault(dep_id, []).append(swap_id)
        return out

    async def list_swaps(
        self, deployment_id: uuid.UUID, page: int, page_size: int
    ) -> Tuple[List[SwapOperations], int]:
        base = select(SwapOperations).where(SwapOperations.deployment_id == deployment_id)
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                base.order_by(SwapOperations.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
        return list(rows), int(total)


def _order(sort: Optional[str]) -> List[Any]:
    if not sort:
        return [Deployments.created_at.desc(), Deployments.id]
    out = []
    for part in sort.split(","):
        part = part.strip()
        desc = part.startswith("-")
        name = part.lstrip("-")
        if name in DEPLOYMENT_SORT_FIELDS:
            col = getattr(Deployments, name)
            out.append(col.desc() if desc else col.asc())
    out.append(Deployments.id)
    return out
