"""
Node and join-token repositories.

``NodeRepository`` covers list/detail/labels/status transitions and the
liveness columns the hub updates; ``JoinTokenRepository`` the one-time
``gdj_`` tokens (hash only, TTL, used/revoked).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    Devices,
    DeviceSlices,
    Instances,
    JoinTokens,
    Nodes,
)
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import case, func, literal, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

NODE_SORTABLE = {
    "name",
    "status",
    "created_at",
    "updated_at",
    "last_heartbeat_at",
    "hostname",
}
ACTIVE_STATUSES = ("pending", "online", "offline", "unhealthy", "draining", "drained")
# Statuses of a node whose agent is connected (drain is a scheduling state, not connectivity).
CONNECTED_STATUSES = ("online", "unhealthy", "draining", "drained")
# Statuses that count as "online" on the dashboard / cluster cards.
ONLINE_STATUSES = ("online", "draining", "drained")


def _connected_status():
    """SQL: the status a reconnecting node returns to (its pending drain, else online)."""
    status_type = Nodes.status.type
    return case(
        (
            Nodes.drained_at.is_not(None) & Nodes.drain_requested_at.is_not(None),
            literal("drained", status_type),
        ),
        (Nodes.drain_requested_at.is_not(None), literal("draining", status_type)),
        else_=literal("online", status_type),
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NodeRepository(BaseRepository[Nodes]):
    def __init__(self, session: AsyncSession):
        super().__init__(Nodes, session)

    async def get_active(self, node_id: uuid.UUID) -> Optional[Nodes]:
        result = await self.session.execute(
            select(Nodes).where(Nodes.id == node_id, Nodes.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, cluster_id: uuid.UUID, name: str) -> Optional[Nodes]:
        result = await self.session.execute(
            select(Nodes).where(
                Nodes.cluster_id == cluster_id,
                func.lower(Nodes.name) == name.lower(),
                Nodes.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        cluster_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        os: Optional[str] = None,
        labels: Optional[Sequence[str]] = None,
        include_removed: bool = False,
    ) -> Tuple[List[Nodes], int]:
        query = select(Nodes).where(Nodes.deleted_at.is_(None))
        if not include_removed:
            query = query.where(Nodes.status != "removed")
        if search:
            query = query.where(
                Nodes.name.ilike(f"%{search}%") | Nodes.hostname.ilike(f"%{search}%")
            )
        if cluster_id:
            query = query.where(Nodes.cluster_id == cluster_id)
        if status:
            query = query.where(Nodes.status == status)
        if os:
            query = query.where(Nodes.os == os)
        for label in labels or []:
            if "=" in label:
                key, value = label.split("=", 1)
                query = query.where(Nodes.labels.op("@>")(func.cast({key: value}, JSONB)))
            else:
                query = query.where(Nodes.labels.has_key(label))
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        field = (sort or "-created_at").strip()
        desc = field.startswith("-")
        field = field.lstrip("-")
        if field not in NODE_SORTABLE:
            field, desc = "created_at", True
        column = getattr(Nodes, field)
        query = query.order_by(column.desc() if desc else column.asc())
        rows = (
            await self.session.execute(query.offset((page - 1) * page_size).limit(page_size))
        ).scalars()
        return list(rows.all()), total

    async def list_active(self, cluster_id: Optional[uuid.UUID] = None) -> List[Nodes]:
        query = (
            select(Nodes)
            .where(Nodes.deleted_at.is_(None), Nodes.status != "removed")
            .order_by(Nodes.name)
        )
        if cluster_id:
            query = query.where(Nodes.cluster_id == cluster_id)
        return list((await self.session.execute(query)).scalars().all())

    async def counts_for(self, node_ids: List[uuid.UUID]) -> Dict[uuid.UUID, Dict[str, int]]:
        """device_count / instance_count per node."""
        out = {nid: {"device_count": 0, "instance_count": 0} for nid in node_ids}
        if not node_ids:
            return out
        dev = (
            select(Devices.node_id, func.count())
            .where(Devices.node_id.in_(node_ids), Devices.deleted_at.is_(None))
            .group_by(Devices.node_id)
        )
        inst = (
            select(Instances.node_id, func.count())
            .where(Instances.node_id.in_(node_ids), Instances.state != "stopped")
            .group_by(Instances.node_id)
        )
        for node_id, count in (await self.session.execute(dev)).all():
            out[node_id]["device_count"] = count
        for node_id, count in (await self.session.execute(inst)).all():
            out[node_id]["instance_count"] = count
        return out

    async def set_status(
        self,
        node_id: uuid.UUID,
        status: str,
        message: Optional[str] = None,
        **extra: Any,
    ) -> None:
        values: Dict[str, Any] = {"status": status, "status_message": message, **extra}
        await self.session.execute(update(Nodes).where(Nodes.id == node_id).values(**values))

    async def touch_heartbeat(self, node_id: uuid.UUID, at: Optional[datetime] = None) -> bool:
        """Record a heartbeat; a node marked offline by the stale sweep comes back online
        (its socket stayed up — e.g. the hub was briefly too busy). True when it recovered.
        """
        await self.session.execute(
            update(Nodes).where(Nodes.id == node_id).values(last_heartbeat_at=at or utcnow())
        )
        result = await self.session.execute(
            update(Nodes)
            .where(Nodes.id == node_id, Nodes.status == "offline")
            .values(
                status=_connected_status(),
                status_message=None,
                disconnected_at=None,
            )
        )
        return bool(result.rowcount)

    async def mark_connected(self, node_id: uuid.UUID, **hello_fields: Any) -> None:
        """hello accepted: online, or back to draining / drained when a drain is pending
        (``drain_requested_at``; a disconnected drained node is ``offline``)."""
        node = await self.get_by_id(node_id)
        if node is None:
            return
        if node.drain_requested_at is not None:
            status = "drained" if node.drained_at is not None else "draining"
        elif node.status in ("draining", "drained"):
            status = node.status
        else:
            status = "online"
        values = dict(
            status=status,
            status_message=None,
            connected_at=utcnow(),
            disconnected_at=None,
            last_heartbeat_at=utcnow(),
            **hello_fields,
        )
        await self.session.execute(update(Nodes).where(Nodes.id == node_id).values(**values))

    async def mark_disconnected(self, node_id: uuid.UUID, reason: str) -> None:
        """
        Socket closed: ``offline``. Drain is a scheduling state, not connectivity: a draining /
        drained node goes offline too (so it shows detached and raises the offline alert); the
        drain itself stays in ``drain_requested_at`` / ``drained_at`` and ``mark_connected``
        restores it.
        """
        node = await self.get_by_id(node_id)
        if node is None or node.status == "removed":
            if node is not None:
                await self.session.execute(
                    update(Nodes).where(Nodes.id == node_id).values(disconnected_at=utcnow())
                )
            return
        if node.status in ("draining", "drained") and node.drain_requested_at is None:
            # Legacy rows drained before drain_requested_at was kept: remember the drain.
            await self.session.execute(
                update(Nodes)
                .where(Nodes.id == node_id)
                .values(
                    drain_requested_at=utcnow(),
                    drained_at=utcnow() if node.status == "drained" else None,
                )
            )
        await self.session.execute(
            update(Nodes)
            .where(Nodes.id == node_id)
            .values(status="offline", status_message=reason, disconnected_at=utcnow())
        )

    async def find_stale(self, offline_after_seconds: int) -> List[Nodes]:
        """Online nodes whose last heartbeat is older than the cut-off."""
        cutoff = utcnow().timestamp() - offline_after_seconds
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
        result = await self.session.execute(
            select(Nodes).where(
                Nodes.deleted_at.is_(None),
                Nodes.status.in_(CONNECTED_STATUSES),
                (Nodes.last_heartbeat_at.is_(None)) | (Nodes.last_heartbeat_at < cutoff_dt),
            )
        )
        return list(result.scalars().all())

    async def claim_offline_alerts(
        self, disconnected_before: datetime, disconnected_after: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Offline nodes disconnected before the cut-off that have not been alerted for this
        disconnect yet; marks them (``metadata.offline_alerted_for`` = ``disconnected_at``)
        in the same statement, so the hub and the jobs process never alert twice.
        """
        result = await self.session.execute(
            text(
                "UPDATE nodes SET metadata = COALESCE(metadata, '{}'::jsonb) || "
                "jsonb_build_object('offline_alerted_for', disconnected_at::text) "
                "WHERE status = 'offline' AND deleted_at IS NULL "
                "AND disconnected_at IS NOT NULL AND disconnected_at < :cutoff "
                "AND (CAST(:since AS timestamptz) IS NULL OR disconnected_at >= :since) "
                "AND (metadata->>'offline_alerted_for') IS DISTINCT FROM disconnected_at::text "
                "RETURNING id, name, cluster_id, disconnected_at, last_heartbeat_at, "
                "status_message, drain_requested_at, drained_at"
            ),
            {"cutoff": disconnected_before, "since": disconnected_after},
        )
        return [dict(r) for r in result.mappings().all()]

    async def merge_metadata(self, node_id: uuid.UUID, patch: Dict[str, Any]) -> None:
        node = await self.get_by_id(node_id)
        if node is None:
            return
        merged = dict(node.metadata_ or {})
        merged.update(patch)
        await self.session.execute(
            update(Nodes).where(Nodes.id == node_id).values({Nodes.metadata_: merged})
        )
        node.metadata_ = merged

    async def soft_remove(self, node_id: uuid.UUID) -> None:
        """Node removed: the node, its devices and their direct reservations go away (the
        dashboard, topology and GPU lists only show devices of live nodes)."""
        now = utcnow()
        await self.session.execute(
            update(Nodes)
            .where(Nodes.id == node_id)
            .values(status="removed", deleted_at=now, disconnected_at=now)
        )
        device_ids = select(Devices.id).where(Devices.node_id == node_id)
        await self.session.execute(
            update(DeviceSlices)
            .where(DeviceSlices.device_id.in_(device_ids), DeviceSlices.status != "released")
            .values(status="released", released_at=now, updated_at=now)
        )
        await self.session.execute(
            update(Devices)
            .where(Devices.node_id == node_id, Devices.deleted_at.is_(None))
            .values(deleted_at=now, health="unknown", updated_at=now)
        )


class JoinTokenRepository(BaseRepository[JoinTokens]):
    def __init__(self, session: AsyncSession):
        super().__init__(JoinTokens, session)

    async def get_by_hash(self, token_hash: str) -> Optional[JoinTokens]:
        result = await self.session.execute(
            select(JoinTokens).where(JoinTokens.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        page: int,
        page_size: int,
        cluster_id: Optional[uuid.UUID] = None,
        active_only: bool = True,
    ) -> Tuple[List[JoinTokens], int]:
        query = select(JoinTokens)
        if cluster_id:
            query = query.where(JoinTokens.cluster_id == cluster_id)
        if active_only:
            query = query.where(
                JoinTokens.used_at.is_(None),
                JoinTokens.revoked_at.is_(None),
                JoinTokens.expires_at > utcnow(),
            )
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                query.order_by(JoinTokens.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
        return list(rows.all()), total

    async def mark_used(self, token_id: uuid.UUID, node_id: uuid.UUID) -> bool:
        """
        Atomic claim of one use: succeeds only while the token is unrevoked, unexpired
        and has uses left (``use_count < max_uses``). The last allowed use also sets
        ``used_at`` (the token is then used up).
        """
        now = utcnow()
        result = await self.session.execute(
            update(JoinTokens)
            .where(
                JoinTokens.id == token_id,
                JoinTokens.used_at.is_(None),
                JoinTokens.revoked_at.is_(None),
                JoinTokens.expires_at > now,
                JoinTokens.use_count < JoinTokens.max_uses,
            )
            .values(
                use_count=JoinTokens.use_count + 1,
                last_used_at=now,
                used_by_node_id=node_id,
                used_at=case((JoinTokens.use_count + 1 >= JoinTokens.max_uses, now), else_=None),
            )
        )
        return result.rowcount == 1

    async def revoke(self, token_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(JoinTokens)
            .where(JoinTokens.id == token_id, JoinTokens.used_at.is_(None))
            .values(revoked_at=utcnow())
        )
        return result.rowcount == 1
