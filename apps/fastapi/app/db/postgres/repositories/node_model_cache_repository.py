"""
Repository for the warm model cache (``node_model_cache``) plus the read-only
joins the cache views need (nodes, models, versions, files, running instances).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    Clusters,
    Deployments,
    Instances,
    ModelFiles,
    Models,
    ModelVersions,
    NodeModelCache,
    Nodes,
)
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

LIVE_INSTANCE_STATES = ("pending", "loading", "ready", "draining")
CACHE_SORT_FIELDS = {"created_at", "updated_at", "last_used_at", "size_bytes", "state"}


class NodeModelCacheRepository(BaseRepository[NodeModelCache]):
    """``node_model_cache`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(NodeModelCache, session)

    async def list_items(
        self,
        *,
        page: int,
        page_size: int,
        sort: Optional[str] = None,
        node_id: Optional[uuid.UUID] = None,
        model_id: Optional[uuid.UUID] = None,
        model_version_id: Optional[uuid.UUID] = None,
        state: Optional[str] = None,
        pinned: Optional[bool] = None,
        unused_for_hours: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = []
        if node_id:
            conditions.append(NodeModelCache.node_id == node_id)
        if model_id:
            conditions.append(ModelVersions.model_id == model_id)
        if model_version_id:
            conditions.append(NodeModelCache.model_version_id == model_version_id)
        if state:
            conditions.append(NodeModelCache.state == state)
        if pinned is not None:
            conditions.append(NodeModelCache.pinned.is_(pinned))
        if unused_for_hours is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=unused_for_hours)
            conditions.append(
                (NodeModelCache.last_used_at.is_(None)) | (NodeModelCache.last_used_at < cutoff)
            )
        base = (
            select(
                NodeModelCache,
                Nodes.name.label("node_name"),
                Models.id.label("model_id"),
                Models.name.label("model_name"),
                ModelVersions.version.label("version"),
                ModelFiles.path.label("path"),
            )
            .join(Nodes, Nodes.id == NodeModelCache.node_id)
            .join(ModelVersions, ModelVersions.id == NodeModelCache.model_version_id)
            .join(Models, Models.id == ModelVersions.model_id)
            .join(ModelFiles, ModelFiles.id == NodeModelCache.model_file_id)
        )
        if conditions:
            base = base.where(and_(*conditions))
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        field = (sort or "-updated_at").strip()
        desc = field.startswith("-")
        name = field.lstrip("-+")
        if name not in CACHE_SORT_FIELDS:
            name, desc = "updated_at", True
        col = getattr(NodeModelCache, name)
        query = (
            base.order_by(
                col.desc().nulls_last() if desc else col.asc().nulls_last(),
                NodeModelCache.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(query)).all()
        in_use = await self.in_use_versions([r[0].node_id for r in rows])
        out = []
        for item, node_name, mid, model_name, version, path in rows:
            out.append(
                {
                    "id": item.id,
                    "node_id": item.node_id,
                    "node_name": node_name,
                    "model_id": mid,
                    "model_name": model_name,
                    "model_version_id": item.model_version_id,
                    "version": version,
                    "model_file_id": item.model_file_id,
                    "path": path,
                    "local_path": item.local_path,
                    "size_bytes": item.size_bytes or 0,
                    "state": item.state,
                    "progress_pct": float(item.progress_pct or 0),
                    "sha256_verified": bool(item.sha256_verified),
                    "pinned": bool(item.pinned),
                    "pinned_reason": item.pinned_reason,
                    "in_use": (item.node_id, item.model_version_id) in in_use,
                    "error_message": item.error_message,
                    "last_used_at": item.last_used_at,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
            )
        return out, int(total)

    async def in_use_versions(self, node_ids: Sequence[uuid.UUID]) -> set:
        """``{(node_id, model_version_id)}`` used by a live instance."""
        if not node_ids:
            return set()
        result = await self.session.execute(
            select(Instances.node_id, Deployments.model_version_id)
            .select_from(Instances)
            .join(Deployments, Deployments.id == Instances.deployment_id)
            .where(
                Instances.node_id.in_(list(set(node_ids))),
                Instances.state.in_(LIVE_INSTANCE_STATES),
            )
        )
        return {(row[0], row[1]) for row in result.all()}

    async def rows_for_version(
        self, node_id: uuid.UUID, model_version_id: uuid.UUID
    ) -> List[NodeModelCache]:
        result = await self.session.execute(
            select(NodeModelCache).where(
                NodeModelCache.node_id == node_id,
                NodeModelCache.model_version_id == model_version_id,
            )
        )
        return list(result.scalars().all())

    async def ready_file_ids(self, node_id: uuid.UUID, model_version_id: uuid.UUID) -> set:
        result = await self.session.execute(
            select(NodeModelCache.model_file_id).where(
                NodeModelCache.node_id == node_id,
                NodeModelCache.model_version_id == model_version_id,
                NodeModelCache.state == "ready",
            )
        )
        return {row[0] for row in result.all()}

    async def nodes_with_version_ready(self, model_version_id: uuid.UUID) -> set:
        result = await self.session.execute(
            select(NodeModelCache.node_id)
            .where(
                NodeModelCache.model_version_id == model_version_id,
                NodeModelCache.state == "ready",
            )
            .distinct()
        )
        return {row[0] for row in result.all()}

    async def upsert_queued(
        self,
        node_id: uuid.UUID,
        model_version_id: uuid.UUID,
        files: Sequence[ModelFiles],
        cache_dir: str,
        pin: bool,
        pin_reason: Optional[str],
    ) -> List[NodeModelCache]:
        existing = {
            r.model_file_id: r for r in await self.rows_for_version(node_id, model_version_id)
        }
        out = []
        for f in files:
            row = existing.get(f.id)
            if row is None:
                row = await self.create(
                    node_id=node_id,
                    model_version_id=model_version_id,
                    model_file_id=f.id,
                    local_path=f"{cache_dir.rstrip('/')}/{model_version_id}/{f.path}",
                    size_bytes=f.size_bytes or 0,
                    state="queued",
                    progress_pct=0,
                    pinned=pin,
                    pinned_reason=pin_reason if pin else None,
                )
            else:
                changes: Dict[str, Any] = {}
                if row.state in ("evicted", "error"):
                    changes.update(state="queued", progress_pct=0, error_message=None)
                if pin and not row.pinned:
                    changes.update(pinned=True, pinned_reason=pin_reason)
                if changes:
                    row = await self.update(row.id, **changes)
            out.append(row)
        return out

    async def set_state_for_version(
        self, node_id: uuid.UUID, model_version_id: uuid.UUID, state: str
    ) -> int:
        result = await self.session.execute(
            update(NodeModelCache)
            .where(
                NodeModelCache.node_id == node_id,
                NodeModelCache.model_version_id == model_version_id,
                NodeModelCache.state != "evicted",
            )
            .values(state=state)
        )
        await self.session.flush()
        return result.rowcount

    async def set_state_for_ids(self, item_ids: Sequence[uuid.UUID], state: str) -> int:
        if not item_ids:
            return 0
        result = await self.session.execute(
            update(NodeModelCache).where(NodeModelCache.id.in_(list(item_ids))).values(state=state)
        )
        await self.session.flush()
        return result.rowcount

    async def set_pinned(self, item_id: uuid.UUID, pinned: bool, reason: Optional[str]) -> None:
        await self.session.execute(
            update(NodeModelCache)
            .where(NodeModelCache.id == item_id)
            .values(pinned=pinned, pinned_reason=reason if pinned else None)
        )
        await self.session.flush()

    async def set_pinned_for_version(
        self,
        node_id: uuid.UUID,
        model_version_id: uuid.UUID,
        pinned: bool,
        reason: Optional[str],
    ) -> None:
        await self.session.execute(
            update(NodeModelCache)
            .where(
                NodeModelCache.node_id == node_id,
                NodeModelCache.model_version_id == model_version_id,
            )
            .values(pinned=pinned, pinned_reason=reason if pinned else None)
        )
        await self.session.flush()

    async def touch_last_used(self, node_id: uuid.UUID, model_version_id: uuid.UUID) -> None:
        await self.session.execute(
            update(NodeModelCache)
            .where(
                NodeModelCache.node_id == node_id,
                NodeModelCache.model_version_id == model_version_id,
            )
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await self.session.flush()

    async def count_unverified_errors(self, model_version_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(NodeModelCache)
            .where(
                NodeModelCache.model_version_id == model_version_id,
                NodeModelCache.state == "error",
            )
        )
        return int(result.scalar_one())

    async def summary_rows(self, cluster_id: Optional[uuid.UUID] = None) -> List[Dict[str, Any]]:
        """One dict per node with its cache rows aggregated per version."""
        nodes_q = select(Nodes).where(Nodes.deleted_at.is_(None), Nodes.status != "removed")
        if cluster_id:
            nodes_q = nodes_q.join(Clusters, Clusters.id == Nodes.cluster_id).where(
                Nodes.cluster_id == cluster_id
            )
        nodes = (await self.session.execute(nodes_q.order_by(Nodes.name))).scalars().all()
        if not nodes:
            return []
        node_ids = [n.id for n in nodes]
        rows = (
            await self.session.execute(
                select(
                    NodeModelCache.node_id,
                    Models.id,
                    Models.name,
                    ModelVersions.id,
                    ModelVersions.version,
                    func.sum(NodeModelCache.size_bytes),
                    func.bool_or(NodeModelCache.pinned),
                    func.max(NodeModelCache.last_used_at),
                    func.min(NodeModelCache.state),
                    func.count(),
                    func.sum(
                        case(
                            (
                                NodeModelCache.pinned.is_(True),
                                NodeModelCache.size_bytes,
                            ),
                            else_=0,
                        )
                    ),
                )
                .join(ModelVersions, ModelVersions.id == NodeModelCache.model_version_id)
                .join(Models, Models.id == ModelVersions.model_id)
                .where(
                    NodeModelCache.node_id.in_(node_ids),
                    NodeModelCache.state != "evicted",
                )
                .group_by(
                    NodeModelCache.node_id,
                    Models.id,
                    Models.name,
                    ModelVersions.id,
                    ModelVersions.version,
                )
            )
        ).all()
        in_use = await self.in_use_versions(node_ids)
        by_node: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
        for (
            nid,
            mid,
            mname,
            vid,
            ver,
            size,
            pinned,
            last_used,
            state,
            cnt,
            pinned_bytes,
        ) in rows:
            by_node.setdefault(nid, []).append(
                {
                    "model_id": mid,
                    "model_name": mname,
                    "model_version_id": vid,
                    "version": ver,
                    "size_bytes": int(size or 0),
                    "state": state,
                    "pinned": bool(pinned),
                    "in_use": (nid, vid) in in_use,
                    "last_used_at": last_used,
                    "item_count": int(cnt),
                    "pinned_bytes": int(pinned_bytes or 0),
                }
            )
        out = []
        for n in nodes:
            items = by_node.get(n.id, [])
            free_disk = None
            for disk in n.disks or []:
                if isinstance(disk, dict) and disk.get("is_cache_dir"):
                    free_disk = disk.get("free_bytes")
            if free_disk is None and n.disks:
                first = n.disks[0] if isinstance(n.disks, list) and n.disks else None
                free_disk = first.get("free_bytes") if isinstance(first, dict) else None
            out.append(
                {
                    "node_id": n.id,
                    "node_name": n.name,
                    "cache_dir": n.cache_dir,
                    "limit_bytes": n.cache_limit_bytes,
                    "used_bytes": sum(i["size_bytes"] for i in items),
                    "free_disk_bytes": free_disk,
                    "pinned_bytes": sum(i["pinned_bytes"] for i in items),
                    "item_count": sum(i["item_count"] for i in items),
                    "by_model": [
                        {k: v for k, v in i.items() if k not in ("item_count", "pinned_bytes")}
                        for i in items
                    ],
                }
            )
        return out

    async def lru_candidates(self, node_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Unpinned, not-in-use versions on a node ordered by last use (oldest first)."""
        rows = (
            await self.session.execute(
                select(
                    NodeModelCache.model_version_id,
                    func.sum(NodeModelCache.size_bytes),
                    func.max(NodeModelCache.last_used_at),
                    func.max(NodeModelCache.updated_at),
                )
                .where(
                    NodeModelCache.node_id == node_id,
                    NodeModelCache.state == "ready",
                    NodeModelCache.pinned.is_(False),
                )
                .group_by(NodeModelCache.model_version_id)
            )
        ).all()
        in_use = await self.in_use_versions([node_id])
        out = [
            {
                "model_version_id": vid,
                "size_bytes": int(size or 0),
                "last_used_at": last_used or updated,
            }
            for vid, size, last_used, updated in rows
            if (node_id, vid) not in in_use
        ]
        out.sort(key=lambda r: (r["last_used_at"] or datetime.min.replace(tzinfo=timezone.utc)))
        return out

    async def get_node(self, node_id: uuid.UUID) -> Optional[Nodes]:
        result = await self.session.execute(
            select(Nodes).where(Nodes.id == node_id, Nodes.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()
