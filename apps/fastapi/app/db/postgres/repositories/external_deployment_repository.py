"""
Repository for external (observed) deployments and their single instance
(``deployments.origin = 'external'``, migration 300). Rows are created and
updated only from the agent's ``inventory.observed_engines``; nothing here
issues commands. Flush only, never commit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import Deployments, Instances, Nodes
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExternalDeploymentRepository:
    """External deployments + instances of one node (the inventory sync) and the purge."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def node(self, node_id: uuid.UUID) -> Optional[Nodes]:
        return (
            await self.session.execute(select(Nodes).where(Nodes.id == node_id))
        ).scalar_one_or_none()

    async def for_node(self, node_id: uuid.UUID) -> Dict[uuid.UUID, Deployments]:
        """External deployments of the node, soft-deleted ones included (they are revived)."""
        rows = await self.session.execute(
            select(Deployments).where(
                Deployments.origin == "external",
                Deployments.external["node_id"].astext == str(node_id),
            )
        )
        return {d.id: d for d in rows.scalars().all()}

    async def origin_of(self, deployment_id: uuid.UUID) -> Optional[str]:
        return (
            await self.session.execute(
                select(Deployments.origin).where(Deployments.id == deployment_id)
            )
        ).scalar_one_or_none()

    async def instance_origin_of(self, instance_id: uuid.UUID) -> Optional[str]:
        return (
            await self.session.execute(select(Instances.origin).where(Instances.id == instance_id))
        ).scalar_one_or_none()

    async def name_taken(self, name: str, exclude_id: uuid.UUID) -> bool:
        found = await self.session.execute(
            select(Deployments.id).where(
                func.lower(Deployments.name) == name.lower(),
                Deployments.deleted_at.is_(None),
                Deployments.id != exclude_id,
            )
        )
        return found.first() is not None

    async def upsert_deployment(
        self, deployment_id: uuid.UUID, values: Dict[str, Any], create: Dict[str, Any]
    ) -> None:
        """Insert (``create`` + ``values``) or update ``values`` of an external deployment."""
        stmt = insert(Deployments).values(
            id=deployment_id,
            origin="external",
            replicas=1,
            min_replicas=0,
            max_replicas=1,
            desired_running=True,
            **{**create, **values},
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Deployments.id],
            set_={**values, "updated_at": _now()},
            where=Deployments.origin == "external",
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def upsert_instance(
        self, instance_id: uuid.UUID, deployment_id: uuid.UUID, values: Dict[str, Any]
    ) -> None:
        now = _now()
        stmt = insert(Instances).values(
            id=instance_id,
            deployment_id=deployment_id,
            origin="external",
            traffic_weight=0,
            started_at=now,
            ready_at=now,
            state_changed_at=now,
            **values,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Instances.id],
            set_={**values, "updated_at": now},
            where=Instances.origin == "external",
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def mark_stale(self, deployment: Deployments, stale_since: datetime) -> None:
        ext = dict(deployment.external or {})
        ext.setdefault("stale_since", stale_since.isoformat())
        ext["health"] = "unknown"
        now = _now()
        await self.session.execute(
            update(Deployments)
            .where(Deployments.id == deployment.id, Deployments.origin == "external")
            .values(
                external=ext,
                status="stopped",
                status_message="no longer reported by the node agent",
                updated_at=now,
            )
        )
        await self.session.execute(
            update(Instances)
            .where(
                Instances.deployment_id == deployment.id,
                Instances.origin == "external",
                Instances.state != "stopped",
            )
            .values(
                state="stopped",
                health="unknown",
                state_message="no longer reported by the node agent",
                state_changed_at=now,
                stopped_at=now,
            )
        )
        await self.session.flush()

    async def soft_delete(self, deployment_ids: Sequence[uuid.UUID]) -> int:
        if not deployment_ids:
            return 0
        now = _now()
        result = await self.session.execute(
            update(Deployments)
            .where(
                Deployments.id.in_(list(deployment_ids)),
                Deployments.origin == "external",
                Deployments.deleted_at.is_(None),
            )
            .values(deleted_at=now, status="stopped", updated_at=now)
        )
        await self.session.execute(
            update(Instances)
            .where(
                Instances.deployment_id.in_(list(deployment_ids)),
                Instances.origin == "external",
                Instances.state != "stopped",
            )
            .values(state="stopped", health="unknown", stopped_at=now, state_changed_at=now)
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def stale_or_orphaned(self, older_than: datetime) -> List[uuid.UUID]:
        """Live external deployments stale since before ``older_than`` or on a removed node."""
        rows = await self.session.execute(
            select(Deployments.id, Deployments.external, Nodes.status, Nodes.deleted_at)
            .outerjoin(
                Nodes,
                Nodes.id == Deployments.external["node_id"].astext.cast(Nodes.id.type),
            )
            .where(Deployments.origin == "external", Deployments.deleted_at.is_(None))
        )
        out: List[uuid.UUID] = []
        for dep_id, ext, node_status, node_deleted in rows.all():
            stale = (ext or {}).get("stale_since")
            gone = node_status is None or node_status == "removed" or node_deleted is not None
            try:
                old = stale is not None and datetime.fromisoformat(str(stale)) < older_than
            except ValueError:
                old = True
            if gone or old:
                out.append(dep_id)
        return out
