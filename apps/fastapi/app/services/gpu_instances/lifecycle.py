"""
SSH GPU instance lifecycle driver.

Entry points (each opens its own session unless one is passed):

* ``on_command_terminal(command_id)`` — called by the hub right after a
  ``start_ssh_instance`` / ``stop_ssh_instance`` command finishes.
* ``sync_from_inventory(session, node_id, items)`` / ``apply_status`` — the
  agent's ``inventory.ssh_instances`` and ``ssh_instance_state_changed``
  events (connections for idle detection, crashes, adoption after restarts).
* ``run_tick()`` — the ``gpu_instances.tick`` job: queued placements, command
  outcomes (backstop), TTL and idle auto-stop, lost instances, GPU-minute
  metering into ``usage_events`` through the metering writer (``endpoint=gpu_instance``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import AgentCommands, GpuInstances
from app.db.postgres.repositories.gpu_instance_repository import GpuInstanceRepository
from app.services.gpu_instances.config import gpu_instance_settings as cfg
from app.services.gpu_instances.service import (
    GpuInstanceService,
    _adopted,
    alert,
    audit,
    publish,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

TERMINAL_COMMAND_STATES = ("succeeded", "failed", "rejected", "timed_out", "cancelled")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- commands


async def _command(session: AsyncSession, command_id: Optional[uuid.UUID]):
    if command_id is None:
        return None
    result = await session.execute(select(AgentCommands).where(AgentCommands.id == command_id))
    return result.scalar_one_or_none()


async def apply_start_outcome(session: AsyncSession, row: GpuInstances, cmd) -> bool:
    """Running on success, error on failure. True when the row changed."""
    repo = GpuInstanceRepository(session)
    if (
        row.state == "running"
        and cmd is not None
        and cmd.state == "succeeded"
        and not row.host_keys
        and (cmd.result or {}).get("host_keys")
    ):
        # The agent's inventory can report the instance running before the command result
        # lands; only the result carries the host public keys (known_hosts line).
        await repo.set_values(row.id, host_keys=cmd.result["host_keys"])
        return True
    if row.state != "starting" or cmd is None or cmd.state not in TERMINAL_COMMAND_STATES:
        return False
    if cmd.state == "succeeded":
        await mark_running(session, row, cmd.result or {})
        return True
    message = cmd.error_message or cmd.error_code or f"start_ssh_instance {cmd.state}"
    await repo.set_slices_status(row.id, "released", return_slice_ids=_adopted(row))
    await repo.set_values(
        row.id,
        state="error",
        health="unknown",
        state_message=message[:2000],
        stopped_at=utcnow(),
    )
    await _send_cleanup(session, row)
    audit(
        "gpu_instance.failed",
        actor_type="system",
        resource_type="gpu_instance",
        resource_id=row.id,
        resource_name=row.name,
        metadata={"code": cmd.error_code, "message": message[:500]},
    )
    await alert(row, f"GPU instance {row.name} failed to start", message)
    return True


async def mark_running(session: AsyncSession, row: GpuInstances, result: Dict[str, Any]) -> None:
    repo = GpuInstanceRepository(session)
    now = utcnow()
    values: Dict[str, Any] = dict(
        state="running",
        health="healthy",
        state_message="running",
        running_at=row.running_at or now,
        metered_until=row.metered_until or now,
        host_address=result.get("host_address") or row.host_address,
        host_port=result.get("host_port") or row.host_port,
        host_key_fingerprint=result.get("host_key_fingerprint") or row.host_key_fingerprint,
        container_id=result.get("container_id") or row.container_id,
        gpu_attached=bool(result.get("gpu_attached", row.gpu_attached)),
    )
    if result.get("ssh_user"):
        values["ssh_user"] = result["ssh_user"]
    if result.get("host_keys"):
        values["host_keys"] = result["host_keys"]
    if result.get("warnings"):
        values["warnings"] = list(result["warnings"])
    if row.expires_at is None and _dt(result.get("expires_at")):
        values["expires_at"] = _dt(result.get("expires_at"))
    await repo.set_values(row.id, **values)
    await repo.set_slices_status(row.id, "active")
    audit(
        "gpu_instance.running",
        actor_type="system",
        resource_type="gpu_instance",
        resource_id=row.id,
        resource_name=row.name,
        metadata={
            "node_id": str(row.node_id) if row.node_id else None,
            "host_port": values.get("host_port"),
            "gpu_attached": values.get("gpu_attached"),
        },
    )


async def apply_stop_outcome(session: AsyncSession, row: GpuInstances, cmd) -> bool:
    if row.state != "stopping" or cmd is None or cmd.state not in TERMINAL_COMMAND_STATES:
        return False
    message = "stopped"
    if cmd.state != "succeeded":
        message = f"stopped (agent: {cmd.error_message or cmd.error_code or cmd.state})"
    await mark_stopped(session, row, message)
    return True


async def mark_stopped(
    session: AsyncSession, row: GpuInstances, message: str, state: str = "stopped"
) -> None:
    repo = GpuInstanceRepository(session)
    now = utcnow()
    await meter(session, row, until=now)
    await repo.set_slices_status(row.id, "released", return_slice_ids=_adopted(row))
    await repo.set_values(
        row.id,
        state=state,
        health="unknown",
        state_message=message[:2000],
        stopped_at=now,
        active_connections=0,
    )
    audit(
        f"gpu_instance.{state}",
        actor_type="system",
        resource_type="gpu_instance",
        resource_id=row.id,
        resource_name=row.name,
        metadata={"reason": row.stop_reason, "message": message[:500]},
    )


async def _send_cleanup(session: AsyncSession, row: GpuInstances) -> None:
    """Best effort ``stop_ssh_instance`` after a failure (idempotent on the agent)."""
    if row.node_id is None:
        return
    try:
        svc = GpuInstanceService.for_session(session)
        await svc.hub.send_command(
            row.node_id,
            "stop_ssh_instance",
            {"ssh_instance_id": str(row.id), "force": True, "remove_volumes": True},
            timeout_seconds=int(cfg.GPU_INSTANCE_STOP_TIMEOUT_SECONDS),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"gpu instance cleanup skipped: {exc}")


async def on_command_terminal(command_id: uuid.UUID) -> bool:
    """Hub hook: apply a finished start/stop command to its GPU instance."""
    from app.db.postgres.connection import get_session_maker

    async with get_session_maker()() as session:
        repo = GpuInstanceRepository(session)
        row = await repo.by_start_or_stop_command(command_id)
        if row is None:
            return False
        cmd = await _command(session, command_id)
        changed = False
        if row.start_command_id == command_id:
            changed = await apply_start_outcome(session, row, cmd)
        elif row.stop_command_id == command_id:
            changed = await apply_stop_outcome(session, row, cmd)
        await session.commit()
    if changed:
        await publish(row)
    return changed


# --------------------------------------------------------------- inventory


async def apply_status(
    session: AsyncSession, node_id: uuid.UUID, item: Dict[str, Any]
) -> Optional[GpuInstances]:
    """One ``SshInstanceStatus`` from the agent."""
    try:
        instance_id = uuid.UUID(str(item.get("ssh_instance_id")))
    except (TypeError, ValueError):
        return None
    repo = GpuInstanceRepository(session)
    row = await repo.get_by_id(instance_id)
    if row is None or row.node_id != node_id:
        logger.debug(f"inventory: node={node_id} reports unknown ssh instance {instance_id}")
        return None
    values: Dict[str, Any] = {
        "active_connections": int(item.get("active_connections") or 0),
        "connections_total": max(
            int(item.get("connections_total") or 0), int(row.connections_total or 0)
        ),
    }
    if _dt(item.get("last_connection_at")):
        values["last_connection_at"] = _dt(item["last_connection_at"])
    if item.get("health"):
        values["health"] = str(item["health"])
    for key in ("host_address", "host_port", "host_key_fingerprint", "container_id"):
        if item.get(key):
            values[key] = item[key]
    await repo.set_values(row.id, **values)
    reported = item.get("state")
    if reported == "running" and row.state == "starting":
        await mark_running(session, row, item)
    elif (
        reported == "stopped"
        and row.state == "running"
        and row.expires_at is not None
        and row.expires_at <= utcnow() + timedelta(seconds=30)
    ):
        # The agent enforces the TTL itself (it may fire before the control-plane tick, or
        # while the control plane is down): a normal end of life, not a failure.
        await mark_stopped(session, row, "ttl expired (stopped by the agent)")
    elif reported in ("error", "stopped") and row.state in ("starting", "running"):
        message = item.get("message") or f"agent reports {reported}"
        await mark_stopped(session, row, f"agent: {message}", state="error")
        await alert(row, f"GPU instance {row.name} stopped unexpectedly", message)
    elif reported == "stopped" and row.state == "stopping":
        await mark_stopped(session, row, "stopped")
    return row


async def sync_from_inventory(
    session: AsyncSession, node_id: uuid.UUID, items: List[Dict[str, Any]]
) -> Dict[str, int]:
    """Authoritative list from ``inventory.ssh_instances`` (no commit)."""
    seen = set()
    updated = 0
    for item in items or []:
        row = await apply_status(session, node_id, item)
        if row is not None:
            seen.add(row.id)
            updated += 1
    lost = 0
    repo = GpuInstanceRepository(session)
    grace = timedelta(seconds=cfg.GPU_INSTANCE_NOT_REPORTED_GRACE_SECONDS)
    for row in await repo.for_node(node_id, ("running",)):
        if row.id in seen:
            continue
        if row.running_at and utcnow() - row.running_at < grace:
            continue
        await mark_stopped(session, row, "not reported by agent inventory", state="error")
        await alert(row, f"GPU instance {row.name} lost", "not reported by the node agent")
        lost += 1
    return {"updated": updated, "lost": lost}


# ---------------------------------------------------------------- metering


async def meter(
    session: AsyncSession, row: GpuInstances, until: Optional[datetime] = None
) -> Decimal:
    """Write the GPU time since ``metered_until`` as one usage event; returns GPU-seconds."""
    if row.state not in ("running", "stopping") or not row.running_at:
        return Decimal(0)
    devices = len(row.device_ids or [])
    if devices == 0:
        return Decimal(0)  # CPU-only containers are not GPU-metered
    start = row.metered_until or row.running_at
    end = until or utcnow()
    seconds = (end - start).total_seconds()
    if seconds <= 0:
        return Decimal(0)
    pct = int(row.slice_compute_pct or 100) if row.slice_vram_bytes else 100
    gpu_seconds = (Decimal(str(seconds)) * devices * Decimal(pct) / Decimal(100)).quantize(
        Decimal("0.001")
    )
    gpu_cost = Decimal(0)
    plan_id = None
    try:
        from app.services.billing.pricing import gpu_cost as price_gpu
        from app.services.billing.pricing import load_resolver

        resolver = await load_resolver(session)
        plan = resolver.resolve(
            str(row.node_id) if row.node_id else None,
            str(row.cluster_id) if row.cluster_id else None,
        )
        gpu_cost = price_gpu(plan, gpu_seconds / Decimal(60))
        plan_id = getattr(plan, "id", None)
    except Exception as exc:  # noqa: BLE001 - pricing is informational here
        logger.debug(f"gpu instance pricing skipped: {exc}")
    payload = {
        "ts": end.isoformat(),
        "request_id": str(uuid.uuid5(row.id, start.isoformat())),
        "model_name": f"gpu-instance:{row.name}",
        "node_id": str(row.node_id) if row.node_id else None,
        "cluster_id": str(row.cluster_id) if row.cluster_id else None,
        "format": "openai",
        "endpoint": "gpu_instance",
        "stream": False,
        "request_count": 1,
        "latency_ms": int(seconds * 1000),
        "gpu_count": devices,
        "slice_compute_pct": pct if row.slice_vram_bytes else None,
        "concurrency_at_start": 0,
        "status": "success",
        "http_status": 200,
        "metadata": {
            "kind": "gpu_instance",
            "gpu_instance_id": str(row.id),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "gpu_minutes": float(gpu_seconds / Decimal(60)),
            "gpu_cost_usd": float(gpu_cost),
            "price_plan_id": str(plan_id) if plan_id else None,
        },
    }
    try:
        from app.services.metering.writer import write_usage_event

        await write_usage_event(session, payload)
    except Exception as exc:  # noqa: BLE001 - metering failure must not stop the lifecycle
        logger.warning(f"gpu instance metering failed for {row.name}: {exc}")
        return Decimal(0)
    await GpuInstanceRepository(session).set_values(
        row.id,
        metered_until=end,
        gpu_seconds_total=Decimal(str(row.gpu_seconds_total or 0)) + gpu_seconds,
    )
    return gpu_seconds


# -------------------------------------------------------------------- tick


async def tick(session: AsyncSession, hub: Any = None) -> Dict[str, int]:
    """One lifecycle pass (commits per instance so one failure does not block the rest)."""
    repo = GpuInstanceRepository(session)
    svc = GpuInstanceService.for_session(session, hub=hub)
    now = utcnow()
    summary = {
        "placed": 0,
        "running": 0,
        "stopped": 0,
        "failed": 0,
        "expired": 0,
        "metered": 0,
    }
    meter_every = timedelta(minutes=max(1, cfg.GPU_INSTANCE_METER_INTERVAL_MINUTES))
    rows = await repo.in_states(("requested", "starting", "running", "stopping"))
    for row in rows:
        try:
            if row.state == "requested":
                if row.queue_until and row.queue_until < now:
                    await repo.set_values(
                        row.id,
                        state="error",
                        state_message=f"no capacity before the deadline ({row.state_message})",
                        stopped_at=now,
                    )
                    await alert(
                        row,
                        f"GPU instance {row.name} could not be placed",
                        row.state_message,
                    )
                    summary["failed"] += 1
                elif row.queue_until:
                    if await svc.try_start(row) is None:
                        summary["placed"] += 1
            elif row.state == "starting":
                cmd = await _command(session, row.start_command_id)
                if await apply_start_outcome(session, row, cmd):
                    summary["running" if cmd.state == "succeeded" else "failed"] += 1
            elif row.state == "stopping":
                cmd = await _command(session, row.stop_command_id)
                if row.stop_command_id is None or (
                    row.node_id and await repo.node(row.node_id) is None
                ):
                    await mark_stopped(session, row, "stopped (node gone)")
                    summary["stopped"] += 1
                elif await apply_stop_outcome(session, row, cmd):
                    summary["stopped"] += 1
            elif row.state == "running":
                node = await repo.node(row.node_id)
                if node is None or node.deleted_at is not None or node.status == "removed":
                    await mark_stopped(session, row, "node removed", state="error")
                    summary["failed"] += 1
                elif row.expires_at and row.expires_at <= now:
                    await svc.stop_row(row, reason="ttl_expired")
                    summary["expired"] += 1
                elif (
                    row.idle_timeout_seconds
                    and not row.active_connections
                    and (
                        (row.last_connection_at or row.running_at or now)
                        + timedelta(seconds=int(row.idle_timeout_seconds))
                        <= now
                    )
                ):
                    await svc.stop_row(row, reason="idle_timeout")
                    summary["expired"] += 1
                elif row.running_at and now - (row.metered_until or row.running_at) >= meter_every:
                    if await meter(session, row, until=now):
                        summary["metered"] += 1
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.error(f"gpu instance tick failed for {row.name}: {exc}")
    return summary


async def run_tick() -> Dict[str, int]:
    """Jobs-process entry with its own session and a Redis lock (one ticker fleet-wide)."""
    from app.db.postgres.connection import get_session_maker

    lock = await _lock()
    if lock is False:
        return {"skipped": 1}
    try:
        async with get_session_maker()() as session:
            return await tick(session)
    finally:
        await _unlock(lock)


async def _lock() -> Any:
    try:
        from app.db.redis.client import get_redis, redis_key

        client = await get_redis()
        token = str(uuid.uuid4())
        ttl = max(cfg.GPU_INSTANCE_TICK_SECONDS * 4, 60)
        ok = await client.set(redis_key("gpu_instances", "tick", "lock"), token, nx=True, ex=ttl)
        return token if ok else False
    except Exception as exc:  # noqa: BLE001 - no Redis: run unlocked
        logger.debug(f"gpu instance tick lock unavailable: {exc}")
        return None


async def _unlock(token: Any) -> None:
    if not token:
        return
    try:
        from app.db.redis.client import get_redis, redis_key

        client = await get_redis()
        key = redis_key("gpu_instances", "tick", "lock")
        if await client.get(key) in (token, token.encode()):
            await client.delete(key)
    except Exception:  # noqa: BLE001
        pass
