"""
Read-mostly repository used by the rules engine, the layout differ and the
swap planner: deployments with their model/instances, devices with reserved
and observed VRAM, 1-minute engine rollups, live engine samples and a few
targeted writes (instance state during a swap, slice reservations).

The metrics and deployments / scheduler services provide the richer implementations of these
tables; this repository reads the schema directly (rules.md section 1
"Inputs") so the rules engine works without them and they can be swapped later.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    Deployments,
    Devices,
    DeviceSlices,
    Engines,
    EngineVersions,
    Instances,
    ModelFiles,
    Models,
    ModelVersions,
    NodeModelCache,
    Nodes,
)
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

LIVE_INSTANCE_STATES = ("pending", "loading", "ready", "draining")


class ClusterStateRepository:
    """Snapshot reads of the fleet / deployment state plus instance writes used by swaps."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------ deployments

    async def deployments_with_models(
        self, deployment_ids: Optional[Sequence[uuid.UUID]] = None
    ) -> List[Dict[str, Any]]:
        query = (
            select(
                Deployments,
                Models.name.label("model_name"),
                Models.type.label("model_type"),
                Models.priority.label("model_priority"),
                ModelVersions.total_size_bytes.label("model_size_bytes"),
                Engines.name.label("engine_name"),
            )
            .join(Models, Models.id == Deployments.model_id)
            .join(ModelVersions, ModelVersions.id == Deployments.model_version_id)
            .join(EngineVersions, EngineVersions.id == Deployments.engine_version_id)
            .join(Engines, Engines.id == EngineVersions.engine_id)
            # External (observed) deployments are never acted on by rules / swaps / layouts.
            .where(Deployments.deleted_at.is_(None), Deployments.origin == "managed")
        )
        if deployment_ids is not None:
            query = query.where(Deployments.id.in_(list(deployment_ids)))
        rows = (await self.session.execute(query.order_by(Deployments.created_at))).all()
        out = []
        for row in rows:
            dep: Deployments = row[0]
            out.append(
                {
                    "deployment": dep,
                    "model_name": row.model_name,
                    "model_type": row.model_type,
                    "model_priority": row.model_priority,
                    "model_size_bytes": int(row.model_size_bytes or 0),
                    "engine_name": row.engine_name,
                }
            )
        return out

    async def get_deployment(self, deployment_id: uuid.UUID) -> Optional[Deployments]:
        result = await self.session.execute(
            select(Deployments).where(
                Deployments.id == deployment_id,
                Deployments.deleted_at.is_(None),
                Deployments.origin == "managed",
            )
        )
        return result.scalar_one_or_none()

    async def set_deployment_status(
        self, deployment_id: uuid.UUID, status: str, message: Optional[str] = None
    ) -> None:
        await self.session.execute(
            update(Deployments)
            .where(Deployments.id == deployment_id, Deployments.origin == "managed")
            .values(status=status, status_message=(message or None))
        )

    # ------------------------------------------------------------------ instances

    async def live_instances(
        self, deployment_ids: Optional[Sequence[uuid.UUID]] = None
    ) -> List[Instances]:
        query = (
            select(Instances)
            .where(Instances.state.in_(LIVE_INSTANCE_STATES), Instances.origin == "managed")
            .order_by(Instances.created_at)
        )
        if deployment_ids is not None:
            query = query.where(Instances.deployment_id.in_(list(deployment_ids)))
        return list((await self.session.execute(query)).scalars().all())

    async def get_instance(self, instance_id: uuid.UUID) -> Optional[Instances]:
        result = await self.session.execute(
            select(Instances).where(Instances.id == instance_id, Instances.origin == "managed")
        )
        return result.scalar_one_or_none()

    async def update_instance(self, instance_id: uuid.UUID, **fields: Any) -> None:
        await self.session.execute(
            update(Instances)
            .where(Instances.id == instance_id, Instances.origin == "managed")
            .values(**fields)
        )

    async def create_instance(
        self,
        deployment_id: uuid.UUID,
        node_id: uuid.UUID,
        device_ids: List[uuid.UUID],
        traffic_weight: int = 100,
        served_model_name: Optional[str] = None,
        resources: Optional[Dict[str, Any]] = None,
        instance_id: Optional[uuid.UUID] = None,
    ) -> Instances:
        inst = Instances(
            id=instance_id or uuid.uuid4(),
            deployment_id=deployment_id,
            node_id=node_id,
            device_ids=list(device_ids),
            state="pending",
            health="unknown",
            traffic_weight=traffic_weight,
            served_model_name=served_model_name,
            resources=resources or {},
        )
        self.session.add(inst)
        await self.session.flush()
        return inst

    # ------------------------------------------------------------------ devices / slices

    async def devices_with_usage(self) -> List[Dict[str, Any]]:
        """Every non-deleted device with its node, reserved VRAM and last observed usage."""
        rows = await self.session.execute(text("""
                SELECT d.id, d.node_id, n.name AS node_name, n.status AS node_status,
                       n.labels AS node_labels, n.cluster_id, d.vendor, d.index, d.name,
                       d.vram_total_bytes, d.health, d.is_unified_memory,
                       COALESCE((SELECT SUM(s.vram_bytes) FROM device_slices s
                                  WHERE s.device_id = d.id AND s.status <> 'released'), 0)
                           AS reserved_bytes,
                       (SELECT m.memory_used_bytes FROM metric_samples_gpu m
                         WHERE m.device_id = d.id ORDER BY m.ts DESC LIMIT 1) AS observed_used_bytes
                  FROM devices d JOIN nodes n ON n.id = d.node_id
                 WHERE d.deleted_at IS NULL AND n.deleted_at IS NULL
                 ORDER BY n.name, d.index
                """))
        return [dict(r) for r in rows.mappings().all()]

    async def reserve_slices(
        self,
        instance_id: uuid.UUID,
        deployment_id: uuid.UUID,
        device_ids: Sequence[uuid.UUID],
        vram_bytes: int,
        compute_pct: int = 100,
    ) -> List[uuid.UUID]:
        ids = []
        for device_id in device_ids:
            row = DeviceSlices(
                id=uuid.uuid4(),
                device_id=device_id,
                instance_id=instance_id,
                deployment_id=deployment_id,
                vram_bytes=max(1, int(vram_bytes)),
                compute_pct=max(1, min(100, int(compute_pct))),
                status="reserved",
            )
            self.session.add(row)
            ids.append(row.id)
        await self.session.flush()
        return ids

    async def set_slices_status(self, instance_id: uuid.UUID, status: str) -> None:
        values: Dict[str, Any] = {"status": status}
        if status == "released":
            values["released_at"] = datetime.now(timezone.utc)
        await self.session.execute(
            update(DeviceSlices)
            .where(
                DeviceSlices.instance_id == instance_id,
                DeviceSlices.status != "released",
            )
            .values(**values)
        )

    # ------------------------------------------------------------------ nodes

    async def get_node(self, node_id: uuid.UUID) -> Optional[Nodes]:
        result = await self.session.execute(select(Nodes).where(Nodes.id == node_id))
        return result.scalar_one_or_none()

    async def get_devices(self, device_ids: Sequence[uuid.UUID]) -> List[Devices]:
        if not device_ids:
            return []
        result = await self.session.execute(select(Devices).where(Devices.id.in_(list(device_ids))))
        return list(result.scalars().all())

    # ------------------------------------------------------------------ metrics

    async def engine_rollups(
        self, deployment_ids: Sequence[uuid.UUID], since: datetime, until: datetime
    ) -> List[Dict[str, Any]]:
        if not deployment_ids:
            return []
        rows = await self.session.execute(
            text("""
                SELECT ts, deployment_id, instance_id, sample_count,
                       requests_delta, errors_delta, waiting_sessions_avg, waiting_sessions_max,
                       active_sessions_avg, kv_cache_usage_pct_avg, kv_cache_usage_pct_max,
                       error_rate_pct, healthy_seconds
                  FROM metric_rollups_engine_1m
                 WHERE deployment_id = ANY(:ids) AND ts >= :since AND ts < :until
                 ORDER BY ts
                """),
            {"ids": [str(i) for i in deployment_ids], "since": since, "until": until},
        )
        return [dict(r) for r in rows.mappings().all()]

    async def latest_engine_samples(
        self, deployment_ids: Sequence[uuid.UUID]
    ) -> List[Dict[str, Any]]:
        """Most recent raw sample per live instance (current minute / live sessions / health)."""
        if not deployment_ids:
            return []
        rows = await self.session.execute(
            text("""
                SELECT DISTINCT ON (instance_id) instance_id, deployment_id, ts,
                       active_sessions, waiting_sessions, requests_total, errors_total,
                       kv_cache_usage_pct, health
                  FROM metric_samples_engine
                 WHERE deployment_id = ANY(:ids) AND ts >= :since
                 ORDER BY instance_id, ts DESC
                """),
            {
                "ids": [str(i) for i in deployment_ids],
                "since": datetime.now(timezone.utc) - timedelta(minutes=10),
            },
        )
        return [dict(r) for r in rows.mappings().all()]

    async def current_minute_requests(self, deployment_ids: Sequence[uuid.UUID]) -> Dict[str, int]:
        """Requests in the incomplete current minute: max(counter) - min(counter)."""
        if not deployment_ids:
            return {}
        minute_start = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        rows = await self.session.execute(
            text("""
                SELECT deployment_id,
                       SUM(GREATEST(max_req - min_req, 0)) AS requests
                  FROM (SELECT deployment_id, instance_id,
                               MAX(requests_total) AS max_req, MIN(requests_total) AS min_req
                          FROM metric_samples_engine
                         WHERE deployment_id = ANY(:ids) AND ts >= :since
                         GROUP BY deployment_id, instance_id) t
                 GROUP BY deployment_id
                """),
            {"ids": [str(i) for i in deployment_ids], "since": minute_start},
        )
        return {str(r["deployment_id"]): int(r["requests"] or 0) for r in rows.mappings().all()}

    async def unhealthy_streak(self, instance_id: uuid.UUID, limit: int = 100) -> int:
        """Consecutive trailing samples with health=unhealthy (most recent first)."""
        rows = await self.session.execute(
            text(
                "SELECT health FROM metric_samples_engine WHERE instance_id = :iid "
                "AND ts >= :since ORDER BY ts DESC LIMIT :limit"
            ),
            {
                "iid": str(instance_id),
                "since": datetime.now(timezone.utc) - timedelta(minutes=30),
                "limit": limit,
            },
        )
        streak = 0
        for (health,) in rows.all():
            if health == "unhealthy":
                streak += 1
            else:
                break
        return streak

    # ------------------------------------------------------------------ model / cache

    async def model_bundle(self, deployment_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Everything needed to build LoadModelArgs / PrefetchModelArgs for a deployment."""
        dep = await self.get_deployment(deployment_id)
        if dep is None:
            return None
        model = (
            await self.session.execute(select(Models).where(Models.id == dep.model_id))
        ).scalar_one_or_none()
        version = (
            await self.session.execute(
                select(ModelVersions).where(ModelVersions.id == dep.model_version_id)
            )
        ).scalar_one_or_none()
        files = list(
            (
                await self.session.execute(
                    select(ModelFiles)
                    .where(ModelFiles.model_version_id == dep.model_version_id)
                    .order_by(ModelFiles.path)
                )
            )
            .scalars()
            .all()
        )
        engine_version = (
            await self.session.execute(
                select(EngineVersions).where(EngineVersions.id == dep.engine_version_id)
            )
        ).scalar_one_or_none()
        engine = None
        if engine_version is not None:
            engine = (
                await self.session.execute(
                    select(Engines).where(Engines.id == engine_version.engine_id)
                )
            ).scalar_one_or_none()
        return {
            "deployment": dep,
            "model": model,
            "version": version,
            "files": files,
            "engine_version": engine_version,
            "engine": engine,
        }

    async def cached_bytes_on_node(self, node_id: uuid.UUID, model_version_id: uuid.UUID) -> int:
        rows = await self.session.execute(
            select(NodeModelCache.size_bytes).where(
                NodeModelCache.node_id == node_id,
                NodeModelCache.model_version_id == model_version_id,
                NodeModelCache.state == "ready",
            )
        )
        return int(sum(r[0] or 0 for r in rows.all()))
