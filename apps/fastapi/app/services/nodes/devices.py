"""
Devices service: accelerator inventory reads (``/devices``,
``/devices/{id}``, ``/devices/{id}/slices``, ``/device-slices``) and direct
slice management (``POST/PATCH/DELETE /devices/{id}/slices``). Devices are
written only by the hub's inventory reconcile.

Direct slices
-------------
* **Standalone** (no ``deployment_id``): a ``device_slices`` row without an
  instance. The scheduler counts it as reserved VRAM / compute (so deployments
  never land on it); an SSH GPU instance can take it with ``slice_id``.
  Resize / delete check capacity and that no GPU instance holds it.
* **Deployment slice** (``deployment_id`` of a sliced deployment): places one
  more replica of that deployment pinned to this device (``place_instance`` +
  ``start_instance``, replicas + 1). Resizing it changes the deployment's
  slice spec (rolling update of every replica — slices of one deployment are
  uniform); deleting it removes that replica (replicas - 1).
"""

from __future__ import annotations

import uuid
from typing import List, Optional, Tuple

from app.core.exceptions import ConflictError, ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import DeviceSlices
from app.db.postgres.repositories.device_repository import (
    DeviceRepository,
    DeviceSliceRepository,
)
from app.db.postgres.repositories.node_repository import NodeRepository
from app.schemas.fleet_schema import (
    DeviceResponse,
    DeviceSliceCreate,
    DeviceSliceResponse,
    DeviceSliceResult,
    DeviceSliceUpdate,
)
from app.services.nodes.service import decorate_devices
from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class DeviceService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.devices = DeviceRepository(session)
        self.slices = DeviceSliceRepository(session)
        self.nodes = NodeRepository(session)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db)
    ) -> "DeviceService":
        return DeviceService(db)

    async def list(
        self,
        page: int,
        page_size: int,
        sort: Optional[str] = None,
        cluster_id: Optional[uuid.UUID] = None,
        node_id: Optional[uuid.UUID] = None,
        vendor: Optional[str] = None,
        health: Optional[str] = None,
        free_vram_min_bytes: Optional[int] = None,
    ) -> Tuple[List[DeviceResponse], int]:
        pairs, total = await self.devices.list(
            page,
            page_size,
            sort,
            cluster_id,
            node_id,
            vendor,
            health,
            free_vram_min_bytes,
        )
        nodes = {node.id: node for _, node in pairs}
        return (
            await decorate_devices(self.devices, self.slices, [d for d, _ in pairs], nodes),
            total,
        )

    async def get(self, device_id: uuid.UUID) -> DeviceResponse:
        row = await self.devices.get_active(device_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Device")
        node = await self.nodes.get_by_id(row.node_id)
        return (await decorate_devices(self.devices, self.slices, [row], {node.id: node}))[0]

    async def list_slices(
        self, device_id: uuid.UUID, include_released: bool = False
    ) -> List[DeviceSliceResponse]:
        if await self.devices.get_active(device_id) is None:
            raise ResourceNotFoundError(resource_type="Device")
        rows = await self.slices.list_for_device(device_id, include_released)
        return [DeviceSliceResponse.model_validate(r) for r in rows]

    async def list_all_slices(
        self,
        page: int,
        page_size: int,
        deployment_id: Optional[uuid.UUID] = None,
        node_id: Optional[uuid.UUID] = None,
    ) -> Tuple[List[DeviceSliceResponse], int]:
        rows, total = await self.slices.list_active(page, page_size, deployment_id, node_id)
        return [DeviceSliceResponse.model_validate(r) for r in rows], total

    # ---------- direct slices ----------

    async def _device(self, device_id: uuid.UUID):
        row = await self.devices.get_active(device_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Device")
        return row

    async def _slice(self, device_id: uuid.UUID, slice_id: uuid.UUID) -> DeviceSlices:
        row = (
            await self.session.execute(
                select(DeviceSlices).where(
                    DeviceSlices.id == slice_id, DeviceSlices.device_id == device_id
                )
            )
        ).scalar_one_or_none()
        if row is None or row.status == "released":
            raise ResourceNotFoundError(resource_type="Device slice")
        return row

    async def _free(self, device, exclude_slice: Optional[DeviceSlices] = None) -> Tuple[int, int]:
        """(free VRAM bytes, free compute %) on ``device``: total - headroom - max(reserved,
        GPU Deck's observed use) - memory used outside GPU Deck (latest fresh sample; with a
        stale sample only the reservations count), as the scheduler sees it."""
        from app.services.scheduler.service import SchedulerService
        from app.services.scheduler.usage import free_bytes

        nodes = await SchedulerService.for_session(self.session).snapshot()
        for node in nodes:
            for view in node.devices:
                if view.id == device.id:
                    reserved = view.reserved_bytes
                    compute = view.compute_reserved_pct
                    if exclude_slice is not None:
                        reserved -= int(exclude_slice.vram_bytes)
                        compute -= int(exclude_slice.compute_pct)
                    total = int(device.vram_total_bytes or 0)
                    free = free_bytes(
                        total,
                        reserved,
                        view.observed_used_bytes,
                        view.external_used_bytes,
                        settings.SCHEDULER_VRAM_HEADROOM_PCT,
                    )
                    return max(free, 0), max(100 - compute, 0)
        return 0, 0

    async def _check_fits(self, device, vram: int, pct: int, exclude=None) -> None:
        free_vram, free_pct = await self._free(device, exclude)
        if vram > free_vram:
            raise ConflictError(
                f"NO_CAPACITY: slice needs {vram >> 20} MiB, {free_vram >> 20} MiB free "
                "(after headroom, GPU Deck reservations and memory used outside GPU Deck)"
            )
        if pct > free_pct:
            raise ConflictError(f"NO_CAPACITY: slice needs {pct}% compute, {free_pct}% free")

    async def create_slice(
        self,
        device_id: uuid.UUID,
        body: DeviceSliceCreate,
        actor_id: Optional[uuid.UUID],
    ) -> DeviceSliceResult:
        device = await self._device(device_id)
        if device.health == "unhealthy":
            raise ConflictError("device is unhealthy")
        if body.deployment_id is not None:
            return await self._create_deployment_slice(device, body, actor_id)
        if body.vram_bytes is None:
            raise ValidationError("vram_bytes is required for a standalone slice")
        pct = int(body.compute_pct or 100)
        await self._check_fits(device, int(body.vram_bytes), pct)
        row = await self.slices.create(
            device_id=device.id,
            name=body.name or f"direct:{str(device.id)[:8]}",
            vram_bytes=int(body.vram_bytes),
            compute_pct=pct,
            status="reserved",
        )
        _audit_slice("device.slice_created", row, actor_id)
        return DeviceSliceResult(slice=DeviceSliceResponse.model_validate(row))

    async def _create_deployment_slice(
        self, device, body: DeviceSliceCreate, actor_id: Optional[uuid.UUID]
    ) -> DeviceSliceResult:
        from app.services.deployments.service import DeploymentService

        svc = DeploymentService.for_session(self.session)
        dep = await svc.deployments.get_active(body.deployment_id)
        if dep is None:
            raise ResourceNotFoundError(resource_type="Deployment")
        if not dep.slice_vram_bytes:
            raise ConflictError(
                "deployment does not use GPU slices; set slice_vram_bytes on the deployment"
            )
        if body.vram_bytes is not None and int(body.vram_bytes) != int(dep.slice_vram_bytes):
            raise ConflictError(
                "slices of one deployment are uniform: resize the deployment's slice instead "
                f"(currently {int(dep.slice_vram_bytes)} bytes)"
            )
        if body.compute_pct is not None and int(body.compute_pct) != int(
            dep.slice_compute_pct or 100
        ):
            raise ConflictError("compute_pct differs from the deployment's slice_compute_pct")
        inst = await svc.place_instance(dep.id, node_id=device.node_id, device_ids=[device.id])
        replicas = int(dep.replicas) + 1
        await svc.deployments.update(
            dep.id,
            replicas=replicas,
            max_replicas=max(int(dep.max_replicas), replicas),
            desired_running=True,
        )
        rows = await self.slices.list_for_device(device.id, False)
        row = next((r for r in rows if r.instance_id == inst.id), None)
        if row is None:
            raise ConflictError("placement did not reserve a slice on this device")
        if body.name:
            await self.session.execute(
                update(DeviceSlices).where(DeviceSlices.id == row.id).values(name=body.name)
            )
            row.name = body.name
        command_id = await svc.start_instance(inst.id)
        _audit_slice("device.slice_created", row, actor_id, deployment_id=dep.id)
        await svc.publish()
        return DeviceSliceResult(
            slice=DeviceSliceResponse.model_validate(row),
            deployment_id=dep.id,
            instance_id=inst.id,
            command_ids=[command_id] if command_id else [],
        )

    async def update_slice(
        self,
        device_id: uuid.UUID,
        slice_id: uuid.UUID,
        body: DeviceSliceUpdate,
        actor_id: Optional[uuid.UUID],
    ) -> DeviceSliceResult:
        device = await self._device(device_id)
        row = await self._slice(device_id, slice_id)
        vram = int(body.vram_bytes if body.vram_bytes is not None else row.vram_bytes)
        pct = int(body.compute_pct if body.compute_pct is not None else row.compute_pct)
        resized = vram != int(row.vram_bytes) or pct != int(row.compute_pct)
        if row.gpu_instance_id is not None and resized:
            raise ConflictError(
                "slice is used by an SSH GPU instance; stop the instance before resizing"
            )
        if row.deployment_id is not None and resized:
            return await self._resize_deployment_slice(row, vram, pct, actor_id)
        if resized:
            await self._check_fits(device, vram, pct, exclude=row)
        values = {"vram_bytes": vram, "compute_pct": pct}
        if body.name is not None:
            values["name"] = body.name
        await self.session.execute(
            update(DeviceSlices).where(DeviceSlices.id == row.id).values(**values)
        )
        await self.session.flush()
        await self.session.refresh(row)
        _audit_slice("device.slice_resized", row, actor_id)
        return DeviceSliceResult(slice=DeviceSliceResponse.model_validate(row))

    async def _resize_deployment_slice(
        self, row: DeviceSlices, vram: int, pct: int, actor_id: Optional[uuid.UUID]
    ) -> DeviceSliceResult:
        from app.schemas.deployments_schema import DeploymentUpdate
        from app.services.deployments.service import DeploymentService

        svc = DeploymentService.for_session(self.session)
        await svc.update_deployment(
            row.deployment_id,
            DeploymentUpdate(slice_vram_bytes=vram, slice_compute_pct=pct),
        )
        _audit_slice(
            "device.slice_resized",
            row,
            actor_id,
            deployment_id=row.deployment_id,
            vram=vram,
        )
        return DeviceSliceResult(
            slice=DeviceSliceResponse.model_validate(row),
            deployment_id=row.deployment_id,
            instance_id=row.instance_id,
            rolling_update=True,
            message="the deployment's slice spec changed; every replica is replaced (rolling)",
        )

    async def delete_slice(
        self,
        device_id: uuid.UUID,
        slice_id: uuid.UUID,
        force: bool,
        actor_id: Optional[uuid.UUID],
    ) -> DeviceSliceResult:
        await self._device(device_id)
        row = await self._slice(device_id, slice_id)
        if row.gpu_instance_id is not None:
            if not force:
                raise ConflictError("slice is used by an SSH GPU instance; pass force=true")
            from app.services.gpu_instances.service import GpuInstanceService

            gsvc = GpuInstanceService.for_session(self.session)
            gpu = await gsvc.repo.get_active(row.gpu_instance_id)
            if gpu is not None:
                await gsvc.stop_row(gpu, force=True, reason="slice_deleted", actor_id=actor_id)
            await self._release(row)
            _audit_slice("device.slice_deleted", row, actor_id)
            return DeviceSliceResult(
                slice=DeviceSliceResponse.model_validate(row),
                gpu_instance_id=row.gpu_instance_id,
            )
        if row.instance_id is not None:
            from app.services.deployments.service import DeploymentService

            svc = DeploymentService.for_session(self.session)
            inst = await svc.instances.get_by_id(row.instance_id)
            if inst is not None and inst.state not in ("stopped",):
                if not force and inst.state in ("loading", "ready", "draining"):
                    raise ConflictError(
                        "slice serves a running replica; pass force=true to remove it"
                    )
                refs = await svc.remove_instance(inst.id, force=force)
                _audit_slice(
                    "device.slice_deleted",
                    row,
                    actor_id,
                    deployment_id=row.deployment_id,
                )
                return DeviceSliceResult(
                    slice=DeviceSliceResponse.model_validate(row),
                    deployment_id=row.deployment_id,
                    instance_id=row.instance_id,
                    command_ids=list(refs.command_ids or []),
                    message="replica removed; the slice is released when it has unloaded",
                )
        await self._release(row)
        _audit_slice("device.slice_deleted", row, actor_id)
        return DeviceSliceResult(slice=DeviceSliceResponse.model_validate(row))

    async def _release(self, row: DeviceSlices) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(DeviceSlices)
            .where(DeviceSlices.id == row.id)
            .values(status="released", released_at=now)
        )
        await self.session.flush()
        row.status = "released"
        row.released_at = now


def _audit_slice(event_type: str, row: DeviceSlices, actor_id, **extra) -> None:
    try:
        from app.services.audit import audit

        audit.record(
            event_type,
            actor_type="admin" if actor_id else "system",
            actor_id=actor_id,
            resource_type="device_slice",
            resource_id=row.id,
            resource_name=row.name,
            metadata={
                "device_id": str(row.device_id),
                "vram_bytes": int(row.vram_bytes),
                "compute_pct": int(row.compute_pct),
                **{k: str(v) for k, v in extra.items()},
            },
        )
    except Exception as exc:  # noqa: BLE001 - audit never breaks a request
        logger.debug(f"audit {event_type} skipped: {exc}")
