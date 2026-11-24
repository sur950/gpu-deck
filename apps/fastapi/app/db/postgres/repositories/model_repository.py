"""
Repositories for the model catalog: models, model_versions, model_files.

All SQL for the catalog lives here; services never build queries. Flush only,
never commit (``get_db`` commits once per request).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    Deployments,
    ModelFiles,
    Models,
    ModelVersions,
    NodeModelCache,
)
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

MODEL_SORT_FIELDS = {
    "name",
    "created_at",
    "updated_at",
    "type",
    "priority",
    "source",
    "family",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ModelRepository(BaseRepository[Models]):
    """``models`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(Models, session)

    async def get_active(self, model_id: uuid.UUID) -> Optional[Models]:
        result = await self.session.execute(
            select(Models).where(Models.id == model_id, Models.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[Models]:
        result = await self.session.execute(
            select(Models).where(
                func.lower(Models.name) == name.lower(), Models.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def find_by_repo(self, source: str, repo_id: str) -> Optional[Models]:
        result = await self.session.execute(
            select(Models).where(
                Models.source == source,
                Models.repo_id == repo_id,
                Models.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def registered_ids_for_repos(
        self, source: str, repo_ids: Sequence[str]
    ) -> Dict[str, uuid.UUID]:
        if not repo_ids:
            return {}
        result = await self.session.execute(
            select(Models.repo_id, Models.id).where(
                Models.source == source,
                Models.repo_id.in_(list(repo_ids)),
                Models.deleted_at.is_(None),
            )
        )
        return {row[0]: row[1] for row in result.all()}

    async def list_models(
        self,
        *,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        type_: Optional[str] = None,
        source: Optional[str] = None,
        priority: Optional[str] = None,
        family: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> Tuple[List[Models], int]:
        conditions = [Models.deleted_at.is_(None)]
        if search:
            pattern = f"%{search}%"
            conditions.append(
                or_(
                    Models.name.ilike(pattern),
                    Models.display_name.ilike(pattern),
                    Models.repo_id.ilike(pattern),
                )
            )
        if type_:
            conditions.append(Models.type == type_)
        if source:
            conditions.append(Models.source == source)
        if priority:
            conditions.append(Models.priority == priority)
        if family:
            conditions.append(func.lower(Models.family) == family.lower())
        if tag:
            conditions.append(Models.tags.any(tag))
        where = and_(*conditions)
        total = (
            await self.session.execute(select(func.count()).select_from(Models).where(where))
        ).scalar_one()
        query = select(Models).where(where)
        query = query.order_by(*_order(Models, sort, MODEL_SORT_FIELDS))
        query = query.offset((page - 1) * page_size).limit(page_size)
        rows = (await self.session.execute(query)).scalars().all()
        return list(rows), int(total)

    async def soft_delete(self, model_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Models).where(Models.id == model_id).values(deleted_at=_now())
        )
        await self.session.flush()

    async def deployment_count(self, model_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Deployments)
            .where(Deployments.model_id == model_id, Deployments.deleted_at.is_(None))
        )
        return int(result.scalar_one())

    async def deployment_counts(self, model_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, int]:
        if not model_ids:
            return {}
        result = await self.session.execute(
            select(Deployments.model_id, func.count())
            .where(
                Deployments.model_id.in_(list(model_ids)),
                Deployments.deleted_at.is_(None),
            )
            .group_by(Deployments.model_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}


class ModelVersionRepository(BaseRepository[ModelVersions]):
    """``model_versions`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(ModelVersions, session)

    async def get_active(self, version_id: uuid.UUID) -> Optional[ModelVersions]:
        result = await self.session.execute(
            select(ModelVersions).where(
                ModelVersions.id == version_id, ModelVersions.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def list_for_model(self, model_id: uuid.UUID) -> List[ModelVersions]:
        result = await self.session.execute(
            select(ModelVersions)
            .where(ModelVersions.model_id == model_id, ModelVersions.deleted_at.is_(None))
            .order_by(ModelVersions.is_default.desc(), ModelVersions.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_default(self, model_id: uuid.UUID) -> Optional[ModelVersions]:
        result = await self.session.execute(
            select(ModelVersions).where(
                ModelVersions.model_id == model_id,
                ModelVersions.is_default.is_(True),
                ModelVersions.deleted_at.is_(None),
            )
        )
        version = result.scalar_one_or_none()
        if version is None:
            versions = await self.list_for_model(model_id)
            version = versions[0] if versions else None
        return version

    async def defaults_for_models(
        self, model_ids: Sequence[uuid.UUID]
    ) -> Dict[uuid.UUID, ModelVersions]:
        if not model_ids:
            return {}
        result = await self.session.execute(
            select(ModelVersions)
            .where(
                ModelVersions.model_id.in_(list(model_ids)),
                ModelVersions.deleted_at.is_(None),
            )
            .order_by(ModelVersions.is_default.desc(), ModelVersions.created_at.desc())
        )
        out: Dict[uuid.UUID, ModelVersions] = {}
        for v in result.scalars().all():
            out.setdefault(v.model_id, v)
        return out

    async def version_counts(self, model_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, int]:
        if not model_ids:
            return {}
        result = await self.session.execute(
            select(ModelVersions.model_id, func.count())
            .where(
                ModelVersions.model_id.in_(list(model_ids)),
                ModelVersions.deleted_at.is_(None),
            )
            .group_by(ModelVersions.model_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def get_by_label(self, model_id: uuid.UUID, version: str) -> Optional[ModelVersions]:
        result = await self.session.execute(
            select(ModelVersions).where(
                ModelVersions.model_id == model_id,
                ModelVersions.version == version,
                ModelVersions.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def clear_default(self, model_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ModelVersions)
            .where(ModelVersions.model_id == model_id, ModelVersions.deleted_at.is_(None))
            .values(is_default=False)
        )
        await self.session.flush()

    async def soft_delete(self, version_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ModelVersions)
            .where(ModelVersions.id == version_id)
            .values(deleted_at=_now(), is_default=False)
        )
        await self.session.flush()

    async def deployment_count(self, version_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Deployments)
            .where(
                Deployments.model_version_id == version_id,
                Deployments.deleted_at.is_(None),
            )
        )
        return int(result.scalar_one())

    async def set_status(
        self, version_id: uuid.UUID, status: str, message: Optional[str] = None
    ) -> None:
        await self.session.execute(
            update(ModelVersions)
            .where(ModelVersions.id == version_id)
            .values(status=status, status_message=message)
        )
        await self.session.flush()


class ModelFileRepository(BaseRepository[ModelFiles]):
    """``model_files`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(ModelFiles, session)

    async def list_for_version(self, version_id: uuid.UUID) -> List[ModelFiles]:
        result = await self.session.execute(
            select(ModelFiles)
            .where(ModelFiles.model_version_id == version_id)
            .order_by(ModelFiles.path)
        )
        return list(result.scalars().all())

    async def list_for_versions(
        self, version_ids: Sequence[uuid.UUID]
    ) -> Dict[uuid.UUID, List[ModelFiles]]:
        if not version_ids:
            return {}
        result = await self.session.execute(
            select(ModelFiles).where(ModelFiles.model_version_id.in_(list(version_ids)))
        )
        out: Dict[uuid.UUID, List[ModelFiles]] = {}
        for f in result.scalars().all():
            out.setdefault(f.model_version_id, []).append(f)
        return out

    async def file_counts(self, version_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, int]:
        if not version_ids:
            return {}
        result = await self.session.execute(
            select(ModelFiles.model_version_id, func.count())
            .where(ModelFiles.model_version_id.in_(list(version_ids)))
            .group_by(ModelFiles.model_version_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def cached_node_counts(self, file_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, int]:
        if not file_ids:
            return {}
        result = await self.session.execute(
            select(
                NodeModelCache.model_file_id,
                func.count(func.distinct(NodeModelCache.node_id)),
            )
            .where(
                NodeModelCache.model_file_id.in_(list(file_ids)),
                NodeModelCache.state == "ready",
            )
            .group_by(NodeModelCache.model_file_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def set_sha256(self, file_id: uuid.UUID, sha256: Optional[str]) -> None:
        await self.session.execute(
            update(ModelFiles).where(ModelFiles.id == file_id).values(sha256=sha256)
        )
        await self.session.flush()


def _order(model: Any, sort: Optional[str], allowed: set) -> list:
    """Shared ORDER BY builder: ``-field`` descending, whitelist, default -created_at."""
    field = (sort or "-created_at").strip()
    desc = field.startswith("-")
    name = field.lstrip("-+")
    if name not in allowed:
        name, desc = "created_at", True
    column = getattr(model, name)
    return [column.desc() if desc else column.asc(), model.id.asc()]
