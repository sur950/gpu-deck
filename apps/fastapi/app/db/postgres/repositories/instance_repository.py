"""
Repositories for ``instances`` and ``device_slices``, plus the read-only
``agent_commands`` lookups the reconciler uses to apply command outcomes.
Flush only, never commit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    AgentCommands,
    Deployments,
    DeviceSlices,
    Engines,
    EngineVersions,
    Instances,
    Models,
    Nodes,
)
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

LIVE_STATES = ("pending", "loading", "ready", "draining", "unloading")
ACTIVE_STATES = ("pending", "loading", "ready")
INSTANCE_SORT_FIELDS = {
    "created_at",
    "state",
    "state_changed_at",
    "ready_at",
    "traffic_weight",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InstanceRepository(BaseRepository[Instances]):
    """``instances`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(Instances, session)

    def _joined(self):
        # Outer joins: external (observed) deployments have no catalog model / engine version;
        # their model name is the first model id the agent reported.
        model_name = func.coalesce(Models.name, Deployments.external["models"][0].astext)
        return (
            select(Instances, Deployments.name, model_name, Nodes.name, Engines.name)
            .join(Deployments, Deployments.id == Instances.deployment_id)
            .outerjoin(Models, Models.id == Deployments.model_id)
            .join(Nodes, Nodes.id == Instances.node_id)
            .outerjoin(EngineVersions, EngineVersions.id == Deployments.engine_version_id)
            .outerjoin(Engines, Engines.id == EngineVersions.engine_id)
        )

    async def list_instances(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        sort: Optional[str] = None,
        deployment_id: Optional[uuid.UUID] = None,
        deployment_ids: Optional[Sequence[uuid.UUID]] = None,
        node_id: Optional[uuid.UUID] = None,
        state: Optional[str] = None,
        health: Optional[str] = None,
        include_stopped: bool = False,
        origin: Optional[str] = None,
    ) -> Tuple[List[Tuple[Instances, str, str, str, str]], int]:
        conditions = []
        if origin:
            conditions.append(Instances.origin == origin)
        if deployment_id:
            conditions.append(Instances.deployment_id == deployment_id)
        if deployment_ids is not None:
            conditions.append(Instances.deployment_id.in_(list(deployment_ids) or [uuid.uuid4()]))
        if node_id:
            conditions.append(Instances.node_id == node_id)
        if state:
            conditions.append(Instances.state == state)
        elif not include_stopped:
            conditions.append(Instances.state != "stopped")
        if health:
            conditions.append(Instances.health == health)
        base = self._joined()
        if conditions:
            base = base.where(and_(*conditions))
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                base.order_by(*_order(sort)).offset((page - 1) * page_size).limit(page_size)
            )
        ).all()
        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows], int(total)

    async def get_joined(
        self, instance_id: uuid.UUID
    ) -> Optional[Tuple[Instances, str, str, str, str]]:
        row = (
            await self.session.execute(self._joined().where(Instances.id == instance_id))
        ).first()
        return (row[0], row[1], row[2], row[3], row[4]) if row else None

    async def for_deployment(
        self, deployment_id: uuid.UUID, states: Sequence[str] = LIVE_STATES
    ) -> List[Instances]:
        result = await self.session.execute(
            select(Instances)
            .where(
                Instances.deployment_id == deployment_id,
                Instances.state.in_(list(states)),
            )
            .order_by(Instances.created_at)
        )
        return list(result.scalars().all())

    async def for_deployments(
        self, deployment_ids: Sequence[uuid.UUID], states: Sequence[str] = LIVE_STATES
    ) -> Dict[uuid.UUID, List[Instances]]:
        if not deployment_ids:
            return {}
        result = await self.session.execute(
            select(Instances)
            .where(
                Instances.deployment_id.in_(list(deployment_ids)),
                Instances.state.in_(list(states)),
            )
            .order_by(Instances.created_at)
        )
        out: Dict[uuid.UUID, List[Instances]] = {}
        for row in result.scalars().all():
            out.setdefault(row.deployment_id, []).append(row)
        return out

    async def set_state(
        self,
        instance_id: uuid.UUID,
        state: str,
        message: Optional[str] = None,
        **values: Any,
    ) -> None:
        now = _now()
        values = dict(values)
        values.update(state=state, state_changed_at=now, updated_at=now)
        if message is not None:
            values["state_message"] = message[:2048]
        if state == "loading":
            values.setdefault("started_at", now)
        elif state == "ready":
            values.setdefault("ready_at", now)
        elif state in ("stopped", "error"):
            values.setdefault("stopped_at", now)
            if state == "stopped":
                values.setdefault("health", "unknown")
        await self.session.execute(
            update(Instances).where(Instances.id == instance_id).values(**values)
        )
        await self.session.flush()

    async def set_fields(self, instance_id: uuid.UUID, **values: Any) -> None:
        values["updated_at"] = _now()
        await self.session.execute(
            update(Instances).where(Instances.id == instance_id).values(**values)
        )
        await self.session.flush()

    async def merge_resources(self, instance_id: uuid.UUID, extra: Dict[str, Any]) -> None:
        row = await self.get_by_id(instance_id)
        if row is None:
            return
        merged = dict(row.resources or {})
        merged.update(extra)
        await self.set_fields(instance_id, resources=merged)

    async def router_process_for(
        self,
        node_id: uuid.UUID,
        engine_version_id: uuid.UUID,
        device_ids: Sequence[uuid.UUID],
    ) -> Optional[uuid.UUID]:
        """Engine process id of a live router-mode instance on the same node/devices."""
        result = await self.session.execute(
            select(Instances.engine_process_id, Instances.device_ids)
            .join(Deployments, Deployments.id == Instances.deployment_id)
            .where(
                Instances.node_id == node_id,
                Instances.engine_process_id.is_not(None),
                Instances.state.in_(("loading", "ready", "draining")),
                Deployments.engine_version_id == engine_version_id,
            )
            .order_by(Instances.created_at)
        )
        wanted = sorted(str(d) for d in device_ids)
        for proc_id, devs in result.all():
            if sorted(str(d) for d in devs or []) == wanted:
                return proc_id
        return None

    async def live_on_process(
        self, engine_process_id: uuid.UUID, exclude_id: Optional[uuid.UUID] = None
    ) -> int:
        conditions = [
            Instances.engine_process_id == engine_process_id,
            Instances.state.in_(("pending", "loading", "ready", "draining")),
        ]
        if exclude_id is not None:
            conditions.append(Instances.id != exclude_id)
        result = await self.session.execute(
            select(func.count()).select_from(Instances).where(and_(*conditions))
        )
        return int(result.scalar_one())

    async def recent_errors(self, deployment_id: uuid.UUID, since: datetime) -> int:
        """Instances of the deployment that failed since ``since`` (kept after cleanup)."""
        errored_at = Instances.resources["errored_at"].astext
        result = await self.session.execute(
            select(func.count())
            .select_from(Instances)
            .where(
                Instances.deployment_id == deployment_id,
                errored_at.is_not(None),
                errored_at >= since.astimezone(timezone.utc).isoformat(),
            )
        )
        return int(result.scalar_one())

    async def stuck(self, states: Sequence[str], older_than: datetime) -> List[Instances]:
        """Managed instances stuck in ``states`` (external ones are never touched)."""
        result = await self.session.execute(
            select(Instances).where(
                Instances.state.in_(list(states)),
                Instances.state_changed_at < older_than,
                Instances.origin == "managed",
            )
        )
        return list(result.scalars().all())

    async def all_live(self) -> List[Instances]:
        """Live managed instances (the reconciler's view; external ones are observed only)."""
        result = await self.session.execute(
            select(Instances).where(Instances.state.in_(LIVE_STATES), Instances.origin == "managed")
        )
        return list(result.scalars().all())

    async def prune_stopped(self, before: datetime) -> int:
        """Delete stopped rows whose stop time is older than ``before``."""
        result = await self.session.execute(
            delete(Instances).where(
                Instances.state == "stopped",
                func.coalesce(Instances.stopped_at, Instances.updated_at) < before,
            )
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def commands_by_ids(
        self, command_ids: Sequence[uuid.UUID]
    ) -> Dict[uuid.UUID, AgentCommands]:
        if not command_ids:
            return {}
        result = await self.session.execute(
            select(AgentCommands).where(AgentCommands.id.in_(list(set(command_ids))))
        )
        return {row.id: row for row in result.scalars().all()}


class DeviceSliceRepository(BaseRepository[DeviceSlices]):
    """``device_slices`` table (GPU slicing reservations)."""

    def __init__(self, session: AsyncSession):
        super().__init__(DeviceSlices, session)

    async def reserve(
        self,
        instance_id: uuid.UUID,
        deployment_id: uuid.UUID,
        device_ids: Sequence[uuid.UUID],
        vram_bytes: int,
        compute_pct: int,
        name: Optional[str] = None,
    ) -> List[DeviceSlices]:
        rows = []
        for dev in device_ids:
            rows.append(
                await self.create(
                    device_id=dev,
                    instance_id=instance_id,
                    deployment_id=deployment_id,
                    name=name,
                    vram_bytes=int(vram_bytes),
                    compute_pct=int(compute_pct),
                    status="reserved",
                )
            )
        return rows

    async def set_status_for_instance(self, instance_id: uuid.UUID, status: str) -> int:
        values: Dict[str, Any] = {"status": status, "updated_at": _now()}
        if status == "released":
            values["released_at"] = _now()
        result = await self.session.execute(
            update(DeviceSlices)
            .where(
                DeviceSlices.instance_id == instance_id,
                DeviceSlices.status != "released",
            )
            .values(**values)
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def for_instance(self, instance_id: uuid.UUID) -> List[DeviceSlices]:
        result = await self.session.execute(
            select(DeviceSlices).where(
                DeviceSlices.instance_id == instance_id,
                DeviceSlices.status != "released",
            )
        )
        return list(result.scalars().all())


def _order(sort: Optional[str]) -> List[Any]:
    if not sort:
        return [Instances.created_at.desc(), Instances.id]
    out = []
    for part in sort.split(","):
        part = part.strip()
        desc = part.startswith("-")
        name = part.lstrip("-")
        if name in INSTANCE_SORT_FIELDS:
            col = getattr(Instances, name)
            out.append(col.desc() if desc else col.asc())
    out.append(Instances.id)
    return out
