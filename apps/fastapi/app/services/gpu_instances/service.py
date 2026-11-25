"""
SSH GPU instances on demand.

A GPU instance is a container running ``sshd`` with whole GPUs or one GPU
slice attached, started by the node agent (``start_ssh_instance``,
protocol.md §8) and reached by the user directly on the node's private
address. Lifecycle::

    requested -> placing -> starting -> running -> stopping -> stopped
                     \\-------------\\-----------\\-----------> error

* **Placement** uses the deployment scheduler (``SchedulerService``) on the
  real-VRAM snapshot (reservations plus memory observed in use, also outside GPU
  Deck): a whole GPU needs the device free apart from the driver's idle footprint
  (``min_device_fraction=1``, ``WHOLE_GPU_IDLE_SLACK_BYTES``), a slice needs its
  VRAM + compute share. The
  reservation is written to ``device_slices`` (``gpu_instance_id``) so
  deployments see it, and released when the instance stops.
* Nodes whose agent cannot run containers (``capabilities.ssh_instances``
  false) are skipped; Apple devices only when asked for explicitly (containers
  cannot use Metal: the agent reports ``gpu_attached=false``).
* Commands go through the session-bound agent hub, so the command row commits
  with the state change. Outcomes are applied by the hub right after the
  command finishes (``lifecycle.on_command_terminal``) and, as a backstop, by
  the ``gpu_instances.tick`` job, which also enforces TTL / idle auto-stop,
  retries queued requests and meters GPU minutes.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.exceptions import ConflictError, ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import DeviceSlices, GpuInstances
from app.db.postgres.repositories.gpu_instance_repository import (
    HOLDING_STATES,
    LIVE_STATES,
    GpuInstanceRepository,
)
from app.db.postgres.repositories.placement_inventory_repository import (
    PlacementInventoryRepository,
)
from app.schemas.gpu_instances_schema import (
    GpuInstance,
    GpuInstanceConnection,
    GpuInstanceCreate,
    GpuInstanceUpdate,
)
from app.services.gpu_instances.config import gpu_instance_settings as cfg
from app.services.gpu_instances.keys import parse_keys
from app.services.scheduler.core import NodeView, PlacementRequest, place
from app.services.scheduler.service import SchedulerService
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

NON_APPLE_VENDORS = ("nvidia", "amd", "hygon", "metax", "moorethreads", "thead")
# Memory a "free" GPU may still show in use (driver / display reserve) for a whole-GPU request.
WHOLE_GPU_IDLE_SLACK_BYTES = 1024**3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def audit(event_type: str, **kwargs: Any) -> None:
    try:
        from app.services.audit import audit as _audit

        _audit.record(event_type, **kwargs)
    except Exception as exc:  # noqa: BLE001 - audit never breaks a request
        logger.debug(f"audit {event_type} skipped: {exc}")


async def alert(row: GpuInstances, title: str, message: Optional[str]) -> None:
    try:
        from app.services.alerts import alerts

        await alerts.emit(
            "gpu_instance.failed",
            "warning",
            title,
            message=message,
            resource_type="gpu_instance",
            resource_id=row.id,
            resource_name=row.name,
            payload={"node_id": str(row.node_id) if row.node_id else None},
            dedupe_key=f"gpu_instance.failed:{row.id}",
        )
    except Exception as exc:  # noqa: BLE001 - alerts never break the lifecycle
        logger.debug(f"gpu instance alert skipped: {exc}")


async def publish(row: GpuInstances) -> None:
    try:
        from app.services.metrics.live import publish_live_event

        await publish_live_event(
            "instance",
            {
                "kind": "gpu_instance",
                "gpu_instance_id": str(row.id),
                "name": row.name,
                "state": row.state,
                "node_id": str(row.node_id) if row.node_id else None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"gpu instance live event skipped: {exc}")


def connection_details(row: GpuInstances, node_address: Optional[str] = None) -> Dict[str, Any]:
    host = row.host_address
    if not host or host in ("0.0.0.0", "::"):
        host = node_address or host
    port = row.host_port
    ready = row.state == "running" and bool(host) and bool(port)
    command = None
    if host and port:
        target = f"[{host}]" if _is_ipv6(host) else host
        command = f"ssh -p {port} {row.ssh_user}@{target}"
    known: List[str] = []
    for key in row.host_keys or []:
        public = (key or {}).get("public_key")
        if host and port and public:
            known.append(f"[{host}]:{port} {public}")
    return GpuInstanceConnection(
        host=host,
        port=port,
        user=row.ssh_user,
        host_key_fingerprint=row.host_key_fingerprint,
        host_keys=list(row.host_keys or []),
        ssh_command=command,
        known_hosts=known,
        ready=ready,
    ).model_dump()


def _is_ipv6(host: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except ValueError:
        return False


def to_response(row: GpuInstances, node_name: Optional[str] = None, node_address=None):
    keys = [
        {
            "type": k.get("type"),
            "fingerprint": k.get("fingerprint"),
            "comment": k.get("comment"),
        }
        for k in (row.authorized_keys or [])
    ]
    return GpuInstance(
        id=row.id,
        name=row.name,
        description=row.description,
        state=row.state,
        state_message=row.state_message,
        health=row.health or "unknown",
        image=row.image,
        cluster_id=row.cluster_id,
        node_id=row.node_id,
        node_name=node_name,
        gpu_count=int(row.gpu_count),
        slice_vram_bytes=row.slice_vram_bytes,
        slice_compute_pct=row.slice_compute_pct,
        device_vendor=row.device_vendor,
        node_selector=dict(row.node_selector or {}),
        device_ids=list(row.device_ids or []),
        limits=dict(row.limits or {}),
        authorized_keys=keys,
        ssh_user=row.ssh_user,
        container_port=int(row.container_port),
        allowed_source_cidrs=list(row.allowed_source_cidrs or []),
        env=dict(row.env or {}),
        volumes=list(row.volumes or []),
        labels=dict(row.labels or {}),
        ttl_seconds=row.ttl_seconds,
        idle_timeout_seconds=row.idle_timeout_seconds,
        expires_at=row.expires_at,
        connection=connection_details(row, node_address),
        gpu_attached=bool(row.gpu_attached),
        warnings=list(row.warnings or []),
        container_id=row.container_id,
        active_connections=int(row.active_connections or 0),
        connections_total=int(row.connections_total or 0),
        last_connection_at=row.last_connection_at,
        stop_reason=row.stop_reason,
        start_command_id=row.start_command_id,
        stop_command_id=row.stop_command_id,
        gpu_seconds_total=float(row.gpu_seconds_total or 0),
        state_changed_at=row.state_changed_at,
        started_at=row.started_at,
        running_at=row.running_at,
        stopped_at=row.stopped_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class GpuInstanceService:
    """CRUD, placement and lifecycle actions for SSH GPU instances."""

    def __init__(self, session: AsyncSession, hub: Any = None):
        self.session = session
        self.repo = GpuInstanceRepository(session)
        self.inventory = PlacementInventoryRepository(session)
        self.scheduler = SchedulerService(session, self.inventory)
        self._hub = hub

    @classmethod
    def for_session(cls, session: AsyncSession, hub: Any = None) -> "GpuInstanceService":
        return cls(session, hub)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "GpuInstanceService":
        return GpuInstanceService(db)

    @property
    def hub(self):
        if self._hub is None:
            from app.services.deployments.agent_hub_port import get_agent_hub

            self._hub = get_agent_hub(self.session)
        return self._hub

    # ------------------------------------------------------------------ reads

    async def _or_404(self, instance_id: uuid.UUID) -> GpuInstances:
        row = await self.repo.get_active(instance_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="GPU instance")
        return row

    async def out(self, row: GpuInstances) -> GpuInstance:
        node = await self.repo.node(row.node_id)
        return to_response(row, node.name if node else None, node.proxy_address if node else None)

    async def list(self, page: int, page_size: int, **filters: Any):
        rows, total = await self.repo.list(page, page_size, **filters)
        nodes = await self.inventory.nodes_by_ids([r.node_id for r in rows if r.node_id])
        items = []
        for r in rows:
            node = nodes.get(r.node_id) if r.node_id else None
            items.append(
                to_response(r, node.name if node else None, node.proxy_address if node else None)
            )
        return items, total

    async def get(self, instance_id: uuid.UUID) -> GpuInstance:
        return await self.out(await self._or_404(instance_id))

    async def connection(self, instance_id: uuid.UUID) -> Dict[str, Any]:
        row = await self._or_404(instance_id)
        node = await self.repo.node(row.node_id)
        return connection_details(row, node.proxy_address if node else None)

    # ----------------------------------------------------------------- create

    async def create(self, body: GpuInstanceCreate, actor_id: Optional[uuid.UUID]) -> GpuInstance:
        try:
            return await self._create(body, actor_id)
        except (ConflictError, ValidationError, ResourceNotFoundError):
            # get_db commits after the route returns an error envelope: never keep a
            # half-created instance or its reservations.
            await self.session.rollback()
            raise

    async def _create(self, body: GpuInstanceCreate, actor_id: Optional[uuid.UUID]) -> GpuInstance:
        if await self.repo.get_by_name(body.name):
            raise ConflictError(f"A GPU instance named {body.name!r} already exists")
        image = (body.image or cfg.GPU_INSTANCE_DEFAULT_IMAGE).strip()
        allowed = cfg.allowed_image_prefixes()
        if allowed and not any(image.startswith(p) for p in allowed):
            raise ValidationError(f"image {image!r} is not in GPU_INSTANCE_ALLOWED_IMAGES")
        keys = parse_keys(body.authorized_keys)
        ttl = body.ttl_seconds
        if ttl is None and cfg.GPU_INSTANCE_DEFAULT_TTL_SECONDS > 0:
            ttl = cfg.GPU_INSTANCE_DEFAULT_TTL_SECONDS
        if ttl is not None and ttl > cfg.GPU_INSTANCE_MAX_TTL_SECONDS:
            raise ValidationError(f"ttl_seconds exceeds {cfg.GPU_INSTANCE_MAX_TTL_SECONDS}")
        container_port = body.container_port or (
            cfg.GPU_INSTANCE_DEFAULT_CONTAINER_PORT if not body.image else 22
        )
        now = utcnow()
        row = await self.repo.create(
            id=uuid.uuid4(),
            name=body.name,
            description=body.description,
            cluster_id=body.cluster_id,
            state="requested",
            state_message="waiting for placement",
            image=image,
            gpu_count=int(body.gpu_count),
            slice_vram_bytes=body.slice_vram_bytes,
            slice_compute_pct=(body.slice_compute_pct or 100 if body.slice_vram_bytes else None),
            device_vendor=body.device_vendor,
            node_selector=dict(body.node_selector or {}),
            device_ids=[],
            limits=body.limits.model_dump(exclude_none=True),
            authorized_keys=keys,
            ssh_user=body.ssh_user,
            container_port=int(container_port),
            requested_ssh_port=body.ssh_port,
            allowed_source_cidrs=list(body.allowed_source_cidrs),
            env=dict(body.env),
            volumes=[v.model_dump(exclude_none=True) for v in body.volumes],
            labels=dict(body.labels),
            ttl_seconds=ttl,
            idle_timeout_seconds=body.idle_timeout_seconds,
            queue_until=(
                now + timedelta(seconds=body.wait_for_capacity_seconds)
                if body.wait_for_capacity_seconds
                else None
            ),
            metadata_={"slice_id": str(body.slice_id)} if body.slice_id else {},
            created_by=actor_id,
        )
        audit(
            "gpu_instance.created",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="gpu_instance",
            resource_id=row.id,
            resource_name=row.name,
            after_state={
                "image": image,
                "gpu_count": row.gpu_count,
                "slice_vram_bytes": row.slice_vram_bytes,
                "ttl_seconds": ttl,
                "key_fingerprints": [k["fingerprint"] for k in keys],
            },
        )
        reason = await self.try_start(row)
        if reason is not None and not body.wait_for_capacity_seconds:
            raise ConflictError(f"NO_CAPACITY: {reason}")
        row = await self._or_404(row.id)
        await publish(row)
        return await self.out(row)

    # -------------------------------------------------------------- placement

    async def try_start(self, row: GpuInstances) -> Optional[str]:
        """Place + reserve + send ``start_ssh_instance``. Returns the failure reason or None."""
        await self.repo.set_values(row.id, state="placing", state_message="placing")
        placed, reason = await self._place(row)
        if placed is None:
            await self.repo.set_values(
                row.id,
                state="requested",
                state_message=f"waiting for capacity: {reason}",
            )
            return reason
        node_id, devices, slice_spec, adopted = placed
        if adopted is None:
            reservations = [
                (
                    d.id,
                    (int(slice_spec["vram_bytes"]) if slice_spec else int(d.vram_total_bytes or 1)),
                    int(slice_spec["compute_pct"]) if slice_spec else 100,
                )
                for d in devices
            ]
            if reservations:
                await self.repo.reserve(row.id, f"ssh:{row.name}", reservations)
        elif not await self.repo.adopt_slice(adopted, row.id):
            await self.repo.set_values(row.id, state="requested", state_message="slice taken")
            return "the requested slice is no longer free"
        expires_at = utcnow() + timedelta(seconds=row.ttl_seconds) if row.ttl_seconds else None
        args = self.start_args(row, devices, slice_spec, expires_at)
        command_id = await self.hub.send_command(
            node_id,
            "start_ssh_instance",
            args,
            timeout_seconds=int(cfg.GPU_INSTANCE_START_TIMEOUT_SECONDS),
        )
        await self.repo.set_values(
            row.id,
            state="starting",
            state_message="start requested",
            node_id=node_id,
            device_ids=[d.id for d in devices],
            start_command_id=command_id,
            stop_command_id=None,
            stop_reason=None,
            started_at=utcnow(),
            expires_at=expires_at,
            queue_until=None,
            host_address=None,
            host_port=None,
            host_key_fingerprint=None,
            host_keys=[],
            container_id=None,
            warnings=[],
            metadata_={
                **(row.metadata_ or {}),
                "adopted_slice_id": str(adopted) if adopted else None,
            },
        )
        logger.info(
            f"gpu instance {row.name} placed on node={node_id} devices={len(devices)} "
            f"command={command_id}"
        )
        return None

    def start_args(
        self,
        row: GpuInstances,
        devices: Sequence[Any],
        slice_spec: Optional[Dict[str, int]],
        expires_at: Optional[datetime],
    ) -> Dict[str, Any]:
        from app.services.deployments.launch import device_assignments

        limits = {k: v for k, v in (row.limits or {}).items() if v is not None}
        args: Dict[str, Any] = {
            "ssh_instance_id": str(row.id),
            "name": row.name,
            "image": row.image,
            "devices": device_assignments(devices, slice_spec),
            "limits": limits,
            "authorized_keys": [k["key"] for k in row.authorized_keys or []],
            "ssh_user": row.ssh_user,
            "ssh_port": int(row.requested_ssh_port or 0),
            "container_port": int(row.container_port),
            "allowed_source_cidrs": list(row.allowed_source_cidrs or []),
            "env": {str(k): str(v) for k, v in (row.env or {}).items()},
            "volumes": list(row.volumes or []),
            "labels": {
                **{str(k): str(v) for k, v in (row.labels or {}).items()},
                "gpudeck.io/gpu-instance": row.name,
            },
            "health_timeout_seconds": max(
                30, min(int(cfg.GPU_INSTANCE_START_TIMEOUT_SECONDS), 600)
            ),
        }
        if expires_at is not None:
            args["ttl_seconds"] = max(60, int((expires_at - utcnow()).total_seconds()))
        return args

    async def _blocked_nodes(self, nodes: Sequence[NodeView]) -> Dict[uuid.UUID, str]:
        rows = await self.inventory.nodes_by_ids([n.id for n in nodes])
        blocked: Dict[uuid.UUID, str] = {}
        for node_id, node in rows.items():
            caps = node.capabilities or {}
            if not caps.get("ssh_instances") or (
                isinstance(caps.get("commands"), list)
                and "start_ssh_instance" not in caps["commands"]
            ):
                blocked[node_id] = "agent cannot run SSH instances (no container runtime)"
                continue
            if await self.repo.count_running_on_node(node_id) >= cfg.GPU_INSTANCE_MAX_PER_NODE:
                blocked[node_id] = "GPU_INSTANCE_MAX_PER_NODE reached"
        return blocked

    async def _place(
        self, row: GpuInstances
    ) -> Tuple[Optional[Tuple[uuid.UUID, List[Any], Optional[Dict[str, int]], Any]], str]:
        """((node_id, devices, slice_spec, adopted_slice_id), reason)."""
        slice_id = (row.metadata_ or {}).get("slice_id")
        if slice_id:
            return await self._place_on_slice(row, uuid.UUID(slice_id))
        # Real VRAM (like deployments): memory used outside GPU Deck counts as taken, so a
        # whole GPU running someone else's server, or a slice that does not fit next to it,
        # is not placed.
        nodes = await self.scheduler.snapshot(row.cluster_id)
        if not nodes:
            return None, "no nodes"
        blocked = await self._blocked_nodes(nodes)
        if int(row.gpu_count) == 0:
            return self._place_cpu_only(nodes, row, blocked)
        slice_spec = (
            {
                "vram_bytes": int(row.slice_vram_bytes),
                "compute_pct": int(row.slice_compute_pct or 100),
            }
            if row.slice_vram_bytes
            else None
        )
        vendor_filter = [row.device_vendor] if row.device_vendor else list(NON_APPLE_VENDORS)
        req = PlacementRequest(
            need_bytes_per_device=0,
            replicas=1,
            gpu_count=int(row.gpu_count),
            tp_size=int(row.gpu_count),
            pp_size=1,
            strategy="spread",
            slice_vram_bytes=slice_spec["vram_bytes"] if slice_spec else None,
            slice_compute_pct=slice_spec["compute_pct"] if slice_spec else None,
            cluster_id=row.cluster_id,
            node_selector=dict(row.node_selector or {}),
            vendor_filter=vendor_filter,
            headroom_pct=0,
            min_device_fraction=None if slice_spec else 1.0,
            fraction_slack_bytes=0 if slice_spec else WHOLE_GPU_IDLE_SLACK_BYTES,
            blocked_nodes=blocked,
        )
        plan = place(nodes, req)
        if not plan.feasible or not plan.assignments:
            return None, plan.reason or "no placement"
        assignment = plan.assignments[0]
        devices = await self.inventory.devices_by_ids(list(assignment.device_ids))
        return (assignment.node_id, devices, slice_spec, None), ""

    def _place_cpu_only(self, nodes: Sequence[NodeView], row: GpuInstances, blocked):
        from app.services.scheduler.core import _selector_matches

        candidates = []
        for n in nodes:
            if n.status != "online" or n.id in blocked:
                continue
            if row.cluster_id is not None and n.cluster_id != row.cluster_id:
                continue
            if _selector_matches(n, dict(row.node_selector or {})) is not None:
                continue
            candidates.append(n)
        if not candidates:
            return None, "no online node with a container runtime matches"
        best = sorted(candidates, key=lambda n: (n.instance_count, n.name))[0]
        return (best.id, [], None, None), ""

    async def _place_on_slice(self, row: GpuInstances, slice_id: uuid.UUID):
        result = await self.session.execute(select(DeviceSlices).where(DeviceSlices.id == slice_id))
        sl = result.scalar_one_or_none()
        if sl is None or sl.status == "released":
            raise ResourceNotFoundError(resource_type="Device slice")
        if (
            sl.instance_id
            or sl.deployment_id
            or (sl.gpu_instance_id and sl.gpu_instance_id != row.id)
        ):
            return None, "slice is bound to another workload"
        devices = await self.inventory.devices_by_ids([sl.device_id])
        if not devices:
            return None, "slice device is gone"
        node, _ = await self.inventory.node_with_devices(devices[0].node_id)
        if node is None or node.status != "online":
            return None, "slice node is not online"
        if not (node.capabilities or {}).get("ssh_instances"):
            return None, "agent cannot run SSH instances (no container runtime)"
        spec = {"vram_bytes": int(sl.vram_bytes), "compute_pct": int(sl.compute_pct)}
        await self.repo.set_values(
            row.id,
            slice_vram_bytes=spec["vram_bytes"],
            slice_compute_pct=spec["compute_pct"],
        )
        return (node.id, devices, spec, sl.id), ""

    # ---------------------------------------------------------------- actions

    async def update(
        self,
        instance_id: uuid.UUID,
        body: GpuInstanceUpdate,
        actor_id: Optional[uuid.UUID],
    ) -> GpuInstance:
        row = await self._or_404(instance_id)
        changes = body.model_dump(exclude_unset=True)
        values: Dict[str, Any] = {}
        if "description" in changes:
            values["description"] = changes["description"]
        if "labels" in changes and changes["labels"] is not None:
            values["labels"] = changes["labels"]
        if "idle_timeout_seconds" in changes:
            idle = changes["idle_timeout_seconds"]
            if idle is not None and 0 < idle < 60:
                raise ValidationError("idle_timeout_seconds must be 0 or >= 60")
            values["idle_timeout_seconds"] = idle or None
        if "ttl_seconds" in changes:
            ttl = changes["ttl_seconds"]
            if ttl and ttl < 60:
                raise ValidationError("ttl_seconds must be 0 or >= 60")
            if ttl and ttl > cfg.GPU_INSTANCE_MAX_TTL_SECONDS:
                raise ValidationError(f"ttl_seconds exceeds {cfg.GPU_INSTANCE_MAX_TTL_SECONDS}")
            values["ttl_seconds"] = ttl or None
            if row.state in HOLDING_STATES:
                values["expires_at"] = utcnow() + timedelta(seconds=ttl) if ttl else None
        if changes.get("authorized_keys") is not None:
            if row.state not in ("stopped", "error", "requested"):
                raise ConflictError("authorized keys can only change while the instance is stopped")
            values["authorized_keys"] = parse_keys(changes["authorized_keys"])
        if values:
            await self.repo.set_values(row.id, **values)
            audit(
                "gpu_instance.updated",
                actor_type="admin",
                actor_id=actor_id,
                resource_type="gpu_instance",
                resource_id=row.id,
                resource_name=row.name,
                after_state={k: v for k, v in changes.items() if k != "authorized_keys"},
            )
        return await self.get(row.id)

    async def stop(
        self,
        instance_id: uuid.UUID,
        *,
        force: bool = False,
        remove_volumes: bool = True,
        reason: str = "user",
        actor_id: Optional[uuid.UUID] = None,
    ) -> GpuInstance:
        row = await self._or_404(instance_id)
        await self.stop_row(
            row,
            force=force,
            remove_volumes=remove_volumes,
            reason=reason,
            actor_id=actor_id,
        )
        return await self.get(row.id)

    async def stop_row(
        self,
        row: GpuInstances,
        *,
        force: bool = False,
        remove_volumes: bool = True,
        reason: str = "user",
        actor_id: Optional[uuid.UUID] = None,
    ) -> None:
        if row.state in ("stopped", "error") or (row.state == "stopping" and not force):
            return
        if row.state in ("requested", "placing") or row.node_id is None:
            await self.repo.set_slices_status(row.id, "released", return_slice_ids=_adopted(row))
            await self.repo.set_values(
                row.id,
                state="stopped",
                state_message=f"stopped before start ({reason})",
                stop_reason=reason,
                stopped_at=utcnow(),
            )
        else:
            if row.state == "starting" and row.start_command_id:
                await _cancel_quietly(row.start_command_id)
            command_id = await self.hub.send_command(
                row.node_id,
                "stop_ssh_instance",
                {
                    "ssh_instance_id": str(row.id),
                    "force": bool(force),
                    "kill_grace_seconds": 10,
                    "remove_volumes": bool(remove_volumes),
                },
                timeout_seconds=int(cfg.GPU_INSTANCE_STOP_TIMEOUT_SECONDS),
            )
            await self.repo.set_values(
                row.id,
                state="stopping",
                state_message=f"stop requested ({reason})",
                stop_reason=reason,
                stop_command_id=command_id,
            )
        audit(
            "gpu_instance.stop_requested",
            actor_type="admin" if actor_id else "system",
            actor_id=actor_id,
            resource_type="gpu_instance",
            resource_id=row.id,
            resource_name=row.name,
            metadata={"reason": reason, "force": force},
        )
        await publish(row)

    async def start(self, instance_id: uuid.UUID, actor_id: Optional[uuid.UUID]) -> GpuInstance:
        """Start a stopped / failed instance again (new placement)."""
        row = await self._or_404(instance_id)
        if row.state not in ("stopped", "error", "requested"):
            raise ConflictError(f"instance is {row.state}")
        await self.repo.set_slices_status(row.id, "released", return_slice_ids=_adopted(row))
        await self.repo.set_values(
            row.id,
            state="requested",
            state_message="restart requested",
            node_id=None,
            device_ids=[],
            stopped_at=None,
            active_connections=0,
        )
        row = await self._or_404(row.id)
        reason = await self.try_start(row)
        if reason is not None:
            await self.session.rollback()
            raise ConflictError(f"NO_CAPACITY: {reason}")
        audit(
            "gpu_instance.started",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="gpu_instance",
            resource_id=row.id,
            resource_name=row.name,
        )
        return await self.get(row.id)

    async def delete(
        self, instance_id: uuid.UUID, force: bool, actor_id: Optional[uuid.UUID]
    ) -> None:
        row = await self._or_404(instance_id)
        if row.state in LIVE_STATES and row.state != "requested":
            if not force:
                raise ConflictError(f"instance is {row.state}; stop it first or pass force=true")
            await self.stop_row(row, force=True, reason="deleted", actor_id=actor_id)
        await self.repo.set_slices_status(row.id, "released", return_slice_ids=_adopted(row))
        await self.repo.set_values(row.id, deleted_at=utcnow())
        audit(
            "gpu_instance.deleted",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="gpu_instance",
            resource_id=row.id,
            resource_name=row.name,
            metadata={"force": force, "state": row.state},
        )


def _adopted(row: GpuInstances) -> List[uuid.UUID]:
    value = (row.metadata_ or {}).get("adopted_slice_id")
    return [uuid.UUID(value)] if value else []


async def _cancel_quietly(command_id: uuid.UUID) -> None:
    try:
        from app.services.agent_hub.service import AgentHub

        await AgentHub.cancel_command(command_id)
    except Exception as exc:  # noqa: BLE001 - stop_ssh_instance cleans up anyway
        logger.debug(f"cancel start command {command_id} skipped: {exc}")
