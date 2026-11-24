"""
Certificate repository: every cert the internal CA issued (node agent
and service certs) plus the revocation list.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import NodeCertificates
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class NodeCertificateRepository(BaseRepository[NodeCertificates]):
    def __init__(self, session: AsyncSession):
        super().__init__(NodeCertificates, session)

    async def get_by_fingerprint(self, fingerprint: str) -> Optional[NodeCertificates]:
        result = await self.session.execute(
            select(NodeCertificates).where(NodeCertificates.fingerprint_sha256 == fingerprint)
        )
        return result.scalar_one_or_none()

    async def get_by_serial(self, serial: str) -> Optional[NodeCertificates]:
        result = await self.session.execute(
            select(NodeCertificates).where(NodeCertificates.serial == serial)
        )
        return result.scalar_one_or_none()

    async def list_for_node(self, node_id: uuid.UUID) -> List[NodeCertificates]:
        result = await self.session.execute(
            select(NodeCertificates)
            .where(NodeCertificates.node_id == node_id)
            .order_by(NodeCertificates.issued_at.desc())
        )
        return list(result.scalars().all())

    async def active_for_node(self, node_id: uuid.UUID) -> List[NodeCertificates]:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(NodeCertificates).where(
                NodeCertificates.node_id == node_id,
                NodeCertificates.revoked_at.is_(None),
                NodeCertificates.not_after > now,
            )
        )
        return list(result.scalars().all())

    async def list(
        self,
        page: int,
        page_size: int,
        kind: Optional[str] = None,
        revoked: Optional[bool] = None,
        expiring_within_days: Optional[int] = None,
    ) -> Tuple[List[NodeCertificates], int]:
        query = select(NodeCertificates)
        if kind:
            query = query.where(NodeCertificates.kind == kind)
        if revoked is True:
            query = query.where(NodeCertificates.revoked_at.is_not(None))
        elif revoked is False:
            query = query.where(NodeCertificates.revoked_at.is_(None))
        if expiring_within_days is not None:
            horizon = datetime.now(timezone.utc) + timedelta(days=expiring_within_days)
            query = query.where(NodeCertificates.not_after <= horizon)
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                query.order_by(NodeCertificates.issued_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
        return list(rows.all()), total

    async def revoke(self, cert_id: uuid.UUID, reason: Optional[str]) -> Optional[NodeCertificates]:
        await self.session.execute(
            update(NodeCertificates)
            .where(NodeCertificates.id == cert_id, NodeCertificates.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc), revocation_reason=reason)
        )
        return await self.get_by_id(cert_id)

    async def revoke_all_for_node(self, node_id: uuid.UUID, reason: str) -> int:
        result = await self.session.execute(
            update(NodeCertificates)
            .where(
                NodeCertificates.node_id == node_id,
                NodeCertificates.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc), revocation_reason=reason)
        )
        return result.rowcount

    async def revoked(self) -> List[NodeCertificates]:
        """Revoked but not yet expired certificates (what the CRL must carry)."""
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(NodeCertificates).where(
                NodeCertificates.revoked_at.is_not(None),
                NodeCertificates.not_after > now,
            )
        )
        return list(result.scalars().all())

    async def expiring_node_certs(self, within_days: int) -> List[NodeCertificates]:
        """Active node_agent certs expiring within the window (rotation candidates)."""
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=within_days)
        result = await self.session.execute(
            select(NodeCertificates).where(
                NodeCertificates.kind == "node_agent",
                NodeCertificates.revoked_at.is_(None),
                NodeCertificates.not_after > now,
                NodeCertificates.not_after <= horizon,
            )
        )
        return list(result.scalars().all())
