"""
Cloud instance repository: rows of ``cloud_instances`` (migration 650),
the VMs GPU Deck provisions on AWS / Azure / GCP / DigitalOcean.

``CloudInstances`` comes from ``generated_models.py`` once the models are
regenerated against a database that ran migration 650; until then the
mapping below (same columns) is used, so the domain never depends on a
regeneration step.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Integer,
    Numeric,
    String,
    Uuid,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = get_logger(__name__)

CLOUD_PROVIDERS = ("aws", "azure", "gcp", "digitalocean")
CLOUD_INSTANCE_STATUSES = (
    "pending",
    "provisioning",
    "booting",
    "running",
    "draining",
    "stopping",
    "stopped",
    "starting",
    "terminating",
    "terminated",
    "error",
)
# Statuses that still (may) hold a VM on the provider side.
ACTIVE_STATUSES = tuple(s for s in CLOUD_INSTANCE_STATUSES if s not in ("terminated",))
# Statuses the lifecycle job advances on every tick.
WORKING_STATUSES = (
    "pending",
    "provisioning",
    "booting",
    "starting",
    "draining",
    "stopping",
    "terminating",
)
SORTABLE = {
    "name",
    "status",
    "provider",
    "region",
    "instance_type",
    "created_at",
    "updated_at",
}

try:  # pragma: no cover - depends on whether models were regenerated after 650
    from app.db.postgres.models.generated_models import CloudInstances  # type: ignore
except ImportError:  # pragma: no cover - exercised whenever 650 is not in the generated file

    class _CloudBase(DeclarativeBase):
        pass

    class CloudInstances(_CloudBase):  # type: ignore[no-redef]
        __tablename__ = "cloud_instances"

        id: Mapped[uuid.UUID] = mapped_column(
            Uuid, primary_key=True, server_default=text("gen_random_uuid()")
        )
        cluster_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
        node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
        credential_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
        join_token_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
        provider: Mapped[str] = mapped_column(
            Enum(*CLOUD_PROVIDERS, name="cloud_provider"), nullable=False
        )
        name: Mapped[str] = mapped_column(String(255), nullable=False)
        region: Mapped[str] = mapped_column(String(100), nullable=False)
        zone: Mapped[Optional[str]] = mapped_column(String(100))
        instance_type: Mapped[str] = mapped_column(String(100), nullable=False)
        image: Mapped[Optional[str]] = mapped_column(String(1024))
        disk_gb: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("200"))
        provider_instance_id: Mapped[Optional[str]] = mapped_column(String(255))
        provider_state: Mapped[Optional[str]] = mapped_column(String(64))
        status: Mapped[str] = mapped_column(
            Enum(*CLOUD_INSTANCE_STATUSES, name="cloud_instance_status"),
            nullable=False,
            server_default=text("'pending'::cloud_instance_status"),
        )
        status_message: Mapped[Optional[str]] = mapped_column(String(2048))
        pending_action: Mapped[Optional[str]] = mapped_column(String(32))
        public_ip: Mapped[Optional[Any]] = mapped_column(INET)
        private_ip: Mapped[Optional[Any]] = mapped_column(INET)
        gpu_model: Mapped[Optional[str]] = mapped_column(String(128))
        gpu_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
        gpu_vram_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
        vcpus: Mapped[Optional[int]] = mapped_column(Integer)
        memory_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
        price_per_hour_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(12, 6))
        price_source: Mapped[Optional[str]] = mapped_column(String(255))
        auto_terminate_idle_minutes: Mapped[Optional[int]] = mapped_column(Integer)
        idle_since: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
        labels: Mapped[dict] = mapped_column(
            JSONB, nullable=False, server_default=text("'{}'::jsonb")
        )
        tags: Mapped[dict] = mapped_column(
            JSONB, nullable=False, server_default=text("'{}'::jsonb")
        )
        options: Mapped[dict] = mapped_column(
            JSONB, nullable=False, server_default=text("'{}'::jsonb")
        )
        last_error: Mapped[Optional[dict]] = mapped_column(JSONB)
        created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
        launched_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
        joined_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
        drain_started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
        stopped_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
        terminated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
        last_polled_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
        created_at: Mapped[datetime.datetime] = mapped_column(
            DateTime(True), nullable=False, server_default=text("now()")
        )
        updated_at: Mapped[datetime.datetime] = mapped_column(
            DateTime(True), nullable=False, server_default=text("now()")
        )
        deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class CloudInstanceRepository(BaseRepository[CloudInstances]):
    def __init__(self, session: AsyncSession):
        super().__init__(CloudInstances, session)

    def _active(self):
        return select(CloudInstances).where(CloudInstances.deleted_at.is_(None))

    async def get_active(self, instance_id: uuid.UUID) -> Optional[CloudInstances]:
        result = await self.session.execute(
            self._active()
            .where(CloudInstances.id == instance_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_for_update(self, instance_id: uuid.UUID) -> Optional[CloudInstances]:
        """Row lock for one lifecycle step (SKIP LOCKED: a concurrent step owns it)."""
        result = await self.session.execute(
            self._active().where(CloudInstances.id == instance_id).with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    async def name_taken(self, cluster_id: uuid.UUID, name: str) -> bool:
        result = await self.session.execute(
            select(func.count())
            .select_from(CloudInstances)
            .where(
                CloudInstances.cluster_id == cluster_id,
                func.lower(CloudInstances.name) == name.lower(),
                CloudInstances.deleted_at.is_(None),
                CloudInstances.status != "terminated",
            )
        )
        return int(result.scalar_one()) > 0

    async def list(
        self,
        page: int,
        page_size: int,
        *,
        cluster_id: Optional[uuid.UUID] = None,
        provider: Optional[str] = None,
        status: Optional[Sequence[str]] = None,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        include_terminated: bool = True,
    ) -> Tuple[List[CloudInstances], int]:
        query = self._active()
        if cluster_id:
            query = query.where(CloudInstances.cluster_id == cluster_id)
        if provider:
            query = query.where(CloudInstances.provider == provider)
        if status:
            query = query.where(CloudInstances.status.in_(list(status)))
        elif not include_terminated:
            query = query.where(CloudInstances.status != "terminated")
        if search:
            like = f"%{search}%"
            query = query.where(
                CloudInstances.name.ilike(like)
                | CloudInstances.instance_type.ilike(like)
                | CloudInstances.provider_instance_id.ilike(like)
            )
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        field, desc = "created_at", True
        if sort:
            desc = sort.startswith("-")
            name = sort.lstrip("-")
            if name in SORTABLE:
                field = name
        column = getattr(CloudInstances, field)
        query = query.order_by(column.desc() if desc else column.asc(), CloudInstances.id)
        query = query.offset((page - 1) * page_size).limit(page_size)
        rows = (await self.session.execute(query)).scalars().all()
        return list(rows), int(total)

    async def list_working(self, limit: int = 200) -> List[CloudInstances]:
        """Rows the lifecycle job must advance (in-flight transitions)."""
        result = await self.session.execute(
            self._active()
            .where(CloudInstances.status.in_(WORKING_STATUSES))
            .order_by(CloudInstances.updated_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_due_for_poll(
        self, before: datetime.datetime, limit: int = 200
    ) -> List[CloudInstances]:
        """running / stopped rows not re-read from the provider since ``before``."""
        result = await self.session.execute(
            self._active()
            .where(
                CloudInstances.status.in_(("running", "stopped")),
                (CloudInstances.last_polled_at.is_(None))
                | (CloudInstances.last_polled_at < before),
            )
            .order_by(CloudInstances.last_polled_at.asc().nulls_first())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_idle_candidates(self) -> List[CloudInstances]:
        result = await self.session.execute(
            self._active().where(
                CloudInstances.status == "running",
                CloudInstances.node_id.is_not(None),
                CloudInstances.auto_terminate_idle_minutes.is_not(None),
            )
        )
        return list(result.scalars().all())

    async def count_active_for_cluster(self, cluster_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(CloudInstances)
            .where(
                CloudInstances.cluster_id == cluster_id,
                CloudInstances.deleted_at.is_(None),
                CloudInstances.status.in_(ACTIVE_STATUSES),
                CloudInstances.status != "error",
            )
        )
        return int(result.scalar_one())

    async def count_unmanaged_nodes(self, cluster_id: uuid.UUID) -> int:
        """Nodes of the cluster that are not backed by a live cloud_instances row."""
        result = await self.session.execute(
            text(
                "SELECT count(*) FROM nodes n WHERE n.cluster_id = :cid AND n.deleted_at IS NULL"
                " AND n.status <> 'removed' AND NOT EXISTS (SELECT 1 FROM cloud_instances c"
                " WHERE c.node_id = n.id AND c.deleted_at IS NULL AND c.status <> 'terminated')"
            ),
            {"cid": cluster_id},
        )
        return int(result.scalar_one())

    async def for_node(self, node_id: uuid.UUID) -> Optional[CloudInstances]:
        result = await self.session.execute(
            self._active()
            .where(CloudInstances.node_id == node_id, CloudInstances.status != "terminated")
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def stats(self, cluster_id: Optional[uuid.UUID] = None) -> Dict[str, int]:
        query = (
            select(CloudInstances.status, func.count())
            .where(CloudInstances.deleted_at.is_(None))
            .group_by(CloudInstances.status)
        )
        if cluster_id:
            query = query.where(CloudInstances.cluster_id == cluster_id)
        rows = (await self.session.execute(query)).all()
        return {str(status): int(count) for status, count in rows}
