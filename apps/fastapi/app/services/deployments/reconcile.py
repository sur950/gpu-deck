"""
Deployment reconciliation: desired replicas -> instances.

One tick (jobs process, every ``DEPLOYMENT_RECONCILE_INTERVAL_SECONDS``, also
run inline after create / scale / start / update):

1. ``apply_command_outcomes`` — terminal ``agent_commands`` rows for our
   instances move them along the state machine (load succeeded -> ready with
   port/pid, load failed -> error, unload succeeded -> stopped) and release /
   activate GPU slices. Agent inventory and events (agent hub ingest) update the
   same rows independently; both paths are idempotent.
2. ``expire_stuck`` — pending/loading/unloading rows older than
   ``DEPLOYMENT_LOAD_TIMEOUT_SECONDS`` with no terminal command become error.
3. per deployment (skipped while a swap is active on it):
   * errored instances older than ``DEPLOYMENT_ERROR_GRACE_SECONDS`` are
     cleaned up (force unload, then stopped) and stop counting; younger ones
     of the current spec still count so the auto-heal rules can restart them
     first (errored instances of an older spec are cleaned up at once);
   * crash-loop guard: ``DEPLOYMENT_CRASHLOOP_ERRORS`` errors inside
     ``DEPLOYMENT_CRASHLOOP_WINDOW_SECONDS`` -> status ``failed``, no new
     instances until start / update resets it;
   * per PD role: instances whose ``spec_hash`` differs from the deployment
     are replaced one at a time (surge 1: start a fresh one, stop a stale one
     when a fresh one is ready; if there is no room for a surge, stop one
     stale replica first); extra replicas are stopped (pending first, then
     loading, then the newest ready); missing ones are placed by the
     scheduler and started; ``restart_requested_at`` marks from a rolling
     restart are worked off one at a time;
   * the deployment status is derived from the result.
4. the routing table is republished when anything changed.

A Redis lock keeps two jobs processes from reconciling at once.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.models.generated_models import Instances
from app.services.deployments.service import DeploymentService, role_targets, spec_hash
from app.services.deployments.states import StatusInput, derive_status

logger = get_logger(__name__)

TERMINAL_COMMAND_STATES = ("succeeded", "failed", "rejected", "timed_out", "cancelled")
LOAD_COMMANDS = ("load_model", "restart_instance")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _setting(name: str, default: int) -> int:
    value = getattr(settings, name, None)
    return int(default if value is None else value)


@dataclass
class TickSummary:
    deployments: int = 0
    placed: int = 0
    started: List[uuid.UUID] = field(default_factory=list)
    stopped: List[uuid.UUID] = field(default_factory=list)
    applied: int = 0
    expired: int = 0
    skipped_swaps: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.placed or self.started or self.stopped or self.applied or self.expired)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "deployments": self.deployments,
            "placed": self.placed,
            "started": len(self.started),
            "stopped": len(self.stopped),
            "applied": self.applied,
            "expired": self.expired,
            "skipped_swaps": self.skipped_swaps,
            "errors": self.errors[:10],
        }


class Reconciler:
    """Drives instances toward each deployment's desired state."""

    def __init__(self, svc: DeploymentService):
        self.svc = svc
        self.instances = svc.instances
        self.deployments = svc.deployments

    # ------------------------------------------------------------ the tick

    async def tick(self) -> TickSummary:
        summary = TickSummary()
        summary.applied = await self.apply_command_outcomes()
        summary.expired = await self.expire_stuck()
        await self._purge_stale_external()
        deps = await self.deployments.list_for_reconcile()
        summary.deployments = len(deps)
        swaps = await self.deployments.active_swap_ids([d.id for d in deps])
        for dep in deps:
            if swaps.get(dep.id):
                summary.skipped_swaps += 1
                continue
            try:
                cmds = await self.reconcile_deployment(dep.id, summary=summary)
                summary.started.extend(cmds)
            except Exception as exc:  # noqa: BLE001 - one bad deployment must not stop the rest
                logger.error(f"reconcile {dep.name} failed: {type(exc).__name__}: {exc}")
                summary.errors.append(f"{dep.name}: {exc}")
                try:
                    await self.svc.session.rollback()
                except Exception:  # noqa: BLE001
                    pass
        await self.svc.session.commit()
        if summary.changed:
            await self.svc.publish()
        return summary

    async def _purge_stale_external(self) -> None:
        """Bookkeeping only: soft-delete external (observed) deployments the agent stopped
        reporting a day ago or whose node is gone. Nothing is sent to any node."""
        try:
            from app.services.deployments.external import purge_stale

            purged = await purge_stale(self.svc.session)
            if purged:
                logger.info(f"reconcile: removed {purged} stale external deployment(s)")
        except Exception as exc:  # noqa: BLE001 - never blocks the managed reconcile
            logger.warning(f"reconcile: external purge failed: {exc}")
            try:
                await self.svc.session.rollback()
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------- command outcomes

    async def apply_command_outcomes(self) -> int:
        rows = await self.instances.all_live()
        errored = await self.instances.stuck(("error",), utcnow() + timedelta(days=1))
        rows = rows + [r for r in errored if (r.resources or {}).get("cleanup_command_id")]
        wanted: Dict[uuid.UUID, Instances] = {}
        cmd_ids: List[uuid.UUID] = []
        for inst in rows:
            res = inst.resources or {}
            for key in ("last_command_id", "start_command_id", "cleanup_command_id"):
                raw = res.get(key)
                if raw and res.get(f"{key}_applied") != raw:
                    try:
                        cid = uuid.UUID(str(raw))
                    except ValueError:
                        continue
                    cmd_ids.append(cid)
                    wanted[inst.id] = inst
        if not cmd_ids:
            return 0
        commands = await self.instances.commands_by_ids(cmd_ids)
        applied = 0
        for inst in wanted.values():
            res = dict(inst.resources or {})
            changed = False
            start_id = res.get("start_command_id")
            start_cmd = commands.get(uuid.UUID(start_id)) if start_id else None
            if (
                start_cmd is not None
                and start_cmd.state in TERMINAL_COMMAND_STATES
                and res.get("start_command_id_applied") != start_id
            ):
                res["start_command_id_applied"] = start_id
                changed = True
                if start_cmd.state != "succeeded" and inst.state in (
                    "pending",
                    "loading",
                ):
                    await self._fail(
                        inst,
                        f"start_engine {start_cmd.state}: {start_cmd.error_message or ''}",
                    )
                    inst.state = "error"
            cleanup_id = res.get("cleanup_command_id")
            cleanup_cmd = commands.get(uuid.UUID(cleanup_id)) if cleanup_id else None
            if (
                cleanup_cmd is not None
                and cleanup_cmd.state in TERMINAL_COMMAND_STATES
                and res.get("cleanup_command_id_applied") != cleanup_id
            ):
                res["cleanup_command_id_applied"] = cleanup_id
                changed = True
                await self.instances.set_state(inst.id, "stopped", "cleaned up after error")
                await self.svc.slices.set_status_for_instance(inst.id, "released")
            last_id = res.get("last_command_id")
            cmd = commands.get(uuid.UUID(last_id)) if last_id else None
            if (
                cmd is not None
                and cmd.state in TERMINAL_COMMAND_STATES
                and res.get("last_command_id_applied") != last_id
            ):
                res["last_command_id_applied"] = last_id
                changed = True
                await self._apply(inst, cmd)
            if changed:
                current = await self.instances.get_by_id(inst.id)
                merged = dict((current.resources if current else None) or {})
                merged.update({k: v for k, v in res.items() if k.endswith("_applied")})
                await self.instances.set_fields(inst.id, resources=merged)
                applied += 1
        return applied

    async def _apply(self, inst: Instances, cmd: Any) -> None:
        result = cmd.result or {}
        if cmd.command_type in LOAD_COMMANDS:
            if cmd.state == "succeeded":
                if inst.state in ("pending", "loading", "error"):
                    values: Dict[str, Any] = {
                        "health": "healthy",
                        "last_health_at": utcnow(),
                    }
                    for key in ("port", "pid"):
                        if isinstance(result.get(key), int):
                            values[key] = result[key]
                    if result.get("served_model_name"):
                        values["served_model_name"] = result["served_model_name"]
                    if result.get("engine_process_id"):
                        try:
                            values["engine_process_id"] = uuid.UUID(
                                str(result["engine_process_id"])
                            )
                        except ValueError:
                            pass
                    if result.get("container_id"):
                        values["container_id"] = str(result["container_id"])[:128]
                    await self.instances.set_state(inst.id, "ready", None, **values)
                    if isinstance(result.get("vram_bytes_used"), int):
                        await self.instances.merge_resources(
                            inst.id, {"vram_bytes_used": result["vram_bytes_used"]}
                        )
                await self.svc.slices.set_status_for_instance(inst.id, "active")
                await self._mark_cache_used(inst)
            elif inst.state in ("pending", "loading"):
                await self._fail(
                    inst,
                    f"{cmd.command_type} {cmd.state}: {cmd.error_message or cmd.error_code or ''}",
                )
        elif cmd.command_type == "unload_model":
            if cmd.state == "succeeded":
                await self.instances.set_state(inst.id, "stopped", None)
                await self.svc.slices.set_status_for_instance(inst.id, "released")
            elif inst.state == "unloading":
                if cmd.error_code == "drain_timeout":
                    # Sessions did not finish; force it now.
                    await self.instances.set_state(
                        inst.id, "ready", "drain timed out; forcing unload"
                    )
                    await self.instances.release_connection()
                    await self.svc.stop_instance(inst.id, force=True, drain_timeout_seconds=0)
                else:
                    await self._fail(
                        inst,
                        f"unload {cmd.state}: {cmd.error_message or cmd.error_code or ''}",
                    )

    async def _fail(self, inst: Instances, message: str) -> None:
        await self.instances.set_state(inst.id, "error", message.strip()[:2048], health="unhealthy")
        await self.instances.merge_resources(inst.id, {"errored_at": utcnow().isoformat()})
        await self.svc.slices.set_status_for_instance(inst.id, "released")
        logger.warning(f"instance {inst.id} -> error: {message}")

    async def _mark_cache_used(self, inst: Instances) -> None:
        try:
            dep = await self.deployments.get_any(inst.deployment_id)
            if dep is None:
                return
            from app.db.postgres.repositories.node_model_cache_repository import (
                NodeModelCacheRepository,
            )

            repo = NodeModelCacheRepository(self.svc.session)
            await repo.touch_last_used(inst.node_id, dep.model_version_id)
            ctx = await self.svc.context(dep)
            if ctx.priority == "always_on":
                await repo.set_pinned_for_version(
                    inst.node_id, dep.model_version_id, True, "always_on model"
                )
        except Exception as exc:  # noqa: BLE001 - bookkeeping only
            logger.debug(f"cache bookkeeping skipped for {inst.id}: {exc}")

    # -------------------------------------------------------------- expiry

    async def expire_stuck(self) -> int:
        timeout = _setting("DEPLOYMENT_LOAD_TIMEOUT_SECONDS", 900)
        cutoff = utcnow() - timedelta(seconds=timeout + 60)
        rows = await self.instances.stuck(("loading", "unloading"), cutoff)
        pending_cutoff = utcnow() - timedelta(seconds=timeout)
        rows += [
            r
            for r in await self.instances.stuck(("pending",), pending_cutoff)
            if (r.resources or {}).get("last_command_id")
        ]
        for inst in rows:
            await self._fail(inst, f"{inst.state} for more than {timeout}s")
        return len(rows)

    # ------------------------------------------------------ one deployment

    async def reconcile_deployment(
        self,
        deployment_id: uuid.UUID,
        *,
        drain_timeout_seconds: Optional[int] = None,
        summary: Optional[TickSummary] = None,
    ) -> List[uuid.UUID]:
        """Bring one deployment toward its desired state. Returns command ids sent."""
        dep = await self.deployments.get_any(deployment_id)
        if dep is None:
            return []
        cmds: List[uuid.UUID] = []
        live = await self.instances.for_deployment(
            dep.id, ("pending", "loading", "ready", "draining", "unloading", "error")
        )
        now = utcnow()
        grace = timedelta(seconds=_setting("DEPLOYMENT_ERROR_GRACE_SECONDS", 60))
        current_hash = spec_hash(dep)

        # errored instances: clean up after the grace period. An errored instance of an older
        # spec is never healed into the new one (an update replaces it), so it is cleaned up
        # at once and never counts as a replica: counting it made the reconciler stop the
        # update's own replacement as an "excess" replica while the failed one stayed.
        counted_errors: List[Instances] = []
        for inst in [i for i in live if i.state == "error"]:
            res = inst.resources or {}
            if res.get("cleanup_command_id"):
                continue
            if (
                inst.state_changed_at
                and now - inst.state_changed_at < grace
                and dep.deleted_at is None
                and res.get("spec_hash") == current_hash
            ):
                counted_errors.append(inst)
                continue
            cmd = await self._cleanup(inst)
            if cmd:
                cmds.append(cmd)
        if dep.deleted_at is not None:
            for inst in [i for i in live if i.state in ("pending", "loading", "ready", "draining")]:
                cmd = await self.svc.stop_instance(
                    inst.id, drain_timeout_seconds=drain_timeout_seconds
                )
                if cmd:
                    cmds.append(cmd)
            remaining = await self.instances.for_deployment(dep.id)
            if not remaining:
                await self.deployments.set_status(dep.id, "stopped", "deleted")
            return cmds

        crash = await self._crash_looping(dep)
        targets = role_targets(dep)
        unplaceable: Optional[str] = None
        for role, target in targets.items():
            group = [i for i in live if (i.pd_role or None) == role]
            sent, reason = await self._reconcile_group(
                dep,
                role,
                target,
                group,
                counted_errors,
                current_hash,
                crash,
                drain_timeout_seconds,
                summary,
            )
            cmds.extend(sent)
            unplaceable = unplaceable or reason

        states = [i.state for i in await self.instances.for_deployment(dep.id)]
        status = derive_status(
            StatusInput(
                deleted=False,
                desired_running=bool(dep.desired_running),
                target=sum(targets.values()),
                states=states,
                unplaceable=unplaceable,
                crash_looping=crash,
            )
        )
        message = None
        if crash:
            message = "crash loop: too many instance failures; fix the cause and start again"
        elif unplaceable:
            message = unplaceable
        if status != dep.status or message != dep.status_message:
            await self.deployments.set_status(dep.id, status, message)
        return cmds

    async def _reconcile_group(
        self,
        dep: Any,
        role: Optional[str],
        target: int,
        group: Sequence[Instances],
        counted_errors: Sequence[Instances],
        current_hash: str,
        crash: bool,
        drain_timeout_seconds: Optional[int],
        summary: Optional[TickSummary],
    ) -> "tuple[List[uuid.UUID], Optional[str]]":
        cmds: List[uuid.UUID] = []
        active = [i for i in group if i.state in ("pending", "loading", "ready")]
        held = [i for i in counted_errors if (i.pd_role or None) == role]
        fresh = [i for i in active if (i.resources or {}).get("spec_hash") == current_hash]
        stale = [i for i in active if (i.resources or {}).get("spec_hash") != current_hash]
        fresh_ready = [i for i in fresh if i.state == "ready"]
        fresh_loading = [i for i in fresh if i.state in ("pending", "loading")]

        # 1. stale replicas that never became ready are useless: stop them.
        for inst in [i for i in stale if i.state != "ready"]:
            cmds += await self._stop(inst, drain_timeout_seconds, summary)
        stale = [i for i in stale if i.state == "ready"]

        # 2. too many fresh replicas: stop the extras.
        excess = len(fresh) + len(held) - target
        if excess > 0:
            for inst in _scale_down_order(fresh)[:excess]:
                cmds += await self._stop(inst, drain_timeout_seconds, summary)
            for inst in stale:
                cmds += await self._stop(inst, drain_timeout_seconds, summary)
            return cmds, None

        # 3. stale ready replicas: retire one per ready fresh replica beyond target.
        retire = max(0, min(len(stale), len(fresh_ready) + len(stale) - target))
        for inst in sorted(stale, key=lambda i: i.created_at)[:retire]:
            cmds += await self._stop(inst, drain_timeout_seconds, summary)
        stale = sorted(stale, key=lambda i: i.created_at)[retire:]

        # 4. rolling restart marks (one at a time, only when everything else is ready).
        if not stale and not fresh_loading:
            marked = [i for i in fresh_ready if (i.resources or {}).get("restart_requested_at")]
            if marked:
                from app.schemas.deployments_schema import InstanceDrainRequest

                timeout = int((marked[0].resources or {}).get("restart_drain_timeout") or 120)
                refs = await self.svc.restart_instance(
                    marked[0].id, InstanceDrainRequest(timeout_seconds=timeout)
                )
                cmds += refs.command_ids
                return cmds, None

        # 5. missing replicas.
        missing = target - len(fresh) - len(held)
        if missing <= 0 or crash:
            return cmds, None
        if stale:
            missing = min(missing, max(0, 1 - len(fresh_loading)))  # surge of one
            if missing <= 0:
                return cmds, None
        ctx = await self.svc.context(dep)
        estimate = self.svc.estimate(ctx)
        # Spread counts the replicas that stay: not the ones stopped above, and not the oldest
        # stale replica, which this surge replaces (retired first in step 3). Counting it piled
        # every replacement of a rolling update onto the node(s) without old replicas.
        existing = list(fresh) + stale[1:]
        blocked = await self.svc.blocked_nodes(ctx)
        req = self.svc.placement_request(
            ctx, estimate, missing, existing=existing, blocked_nodes=blocked
        )
        plan = await self.svc.scheduler.plan(req)
        assignments = plan.assignments
        reason = None
        if not plan.feasible:
            # place what fits, one by one
            assignments = []
            single = self.svc.placement_request(
                ctx, estimate, 1, existing=existing, blocked_nodes=blocked
            )
            nodes = await self.svc.scheduler.snapshot(ctx.dep.cluster_id)
            for _ in range(missing):
                one = await self.svc.scheduler.plan(single, nodes=nodes)
                if not one.feasible:
                    reason = one.reason or plan.reason
                    break
                assignments.append(one.assignments[0])
                _subtract(nodes, one.assignments[:1], single)
            if reason and not assignments:
                await _request_room(dep.id, reason)
            if not assignments and stale and not fresh_loading:
                # No room for a surge: take one stale replica down first (max unavailable 1).
                cmds += await self._stop(stale[0], drain_timeout_seconds, summary)
                return cmds, None
        for assignment in assignments:
            inst = await self.svc.create_instance_for(ctx, assignment, estimate, pd_role=role)
            if summary is not None:
                summary.placed += 1
            try:
                cmds.append(await self.svc.start_instance(inst.id))
            except Exception as exc:  # noqa: BLE001
                await self._fail(inst, f"start failed: {exc}")
        return cmds, reason

    async def _stop(
        self,
        inst: Instances,
        drain_timeout_seconds: Optional[int],
        summary: Optional[TickSummary],
    ) -> List[uuid.UUID]:
        cmd = await self.svc.stop_instance(inst.id, drain_timeout_seconds=drain_timeout_seconds)
        if summary is not None:
            summary.stopped.append(inst.id)
        return [cmd] if cmd else []

    async def _cleanup(self, inst: Instances) -> Optional[uuid.UUID]:
        """Force-unload an errored instance on its node (frees whatever is left)."""
        await self.svc.slices.set_status_for_instance(inst.id, "released")
        if not (inst.resources or {}).get("last_command_id"):
            await self.instances.set_state(inst.id, "stopped", inst.state_message or "error")
            return None
        await self.instances.release_connection()
        try:
            cmd = await self.svc.hub.send_command(
                inst.node_id,
                "unload_model",
                {
                    "instance_id": str(inst.id),
                    "force": True,
                    "drain_timeout_seconds": 0,
                    "kill_grace_seconds": 5,
                    "stop_engine_if_empty": inst.engine_process_id is not None
                    and await self.instances.live_on_process(
                        inst.engine_process_id, exclude_id=inst.id
                    )
                    == 0,
                },
                instance_id=inst.id,
                timeout_seconds=120,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"cleanup of {inst.id} not sent: {exc}")
            await self.instances.set_state(
                inst.id,
                "stopped",
                f"{inst.state_message or 'error'} (cleanup not sent)",
            )
            return None
        await self.instances.merge_resources(inst.id, {"cleanup_command_id": str(cmd)})
        return cmd

    async def _crash_looping(self, dep: Any) -> bool:
        window = _setting("DEPLOYMENT_CRASHLOOP_WINDOW_SECONDS", 600)
        limit = _setting("DEPLOYMENT_CRASHLOOP_ERRORS", 3)
        since = utcnow() - timedelta(seconds=window)
        reset = await _crashloop_reset(dep.id)
        if reset and reset > since:
            since = reset
        return await self.instances.recent_errors(dep.id, since) >= limit


def _scale_down_order(instances: Sequence[Instances]) -> List[Instances]:
    """Victims first: pending, then loading, then the newest ready replica."""
    rank = {"pending": 0, "loading": 1, "ready": 2}
    return sorted(
        instances,
        key=lambda i: (
            rank.get(i.state, 3),
            -(i.created_at.timestamp() if i.created_at else 0),
        ),
    )


def _subtract(nodes: List[Any], assignments: Sequence[Any], req: Any) -> List[Any]:
    """Account for assignments placed in this tick but not yet in the snapshot."""
    by_id = {d.id: d for n in nodes for d in n.devices}
    for a in assignments:
        for dev_id in a.all_device_ids():
            dev = by_id.get(dev_id)
            if dev is not None:
                dev.reserved_bytes += req.need_for(dev)
                if req.slice_vram_bytes:
                    dev.compute_reserved_pct += req.compute_pct
        for n in nodes:
            if n.id == a.node_id:
                n.instance_count += 1
                req.existing_per_node[n.id] = req.existing_per_node.get(n.id, 0) + 1
    return nodes


async def _request_room(deployment_id: uuid.UUID, reason: str) -> None:
    """
    Placement failed: ask the make_room_by_priority rules to free capacity
    (``app.services.rules.service.make_room_for``), at most once a minute per
    deployment. Best effort; the next tick places once room is made.
    """
    if "needs" not in reason and "compute slices full" not in reason:
        return  # not a capacity problem (labels, vendors, engine availability)
    try:
        from app.services.rules.service import make_room_for
    except Exception:  # noqa: BLE001 - rules module not present
        return
    try:
        from app.db.redis.client import get_redis, redis_key

        client = await get_redis()
        key = redis_key("deployments", str(deployment_id), "make_room")
        if not await client.set(key, "1", nx=True, ex=60):
            return
    except Exception:  # noqa: BLE001 - no Redis: still try once
        pass
    try:
        run_ids = await make_room_for(deployment_id)
        if run_ids:
            logger.info(
                f"make-room rules fired for deployment {deployment_id}: {len(run_ids)} run(s)"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"make_room_for({deployment_id}) failed: {exc}")


async def _crashloop_reset(deployment_id: uuid.UUID) -> Optional[datetime]:
    try:
        from app.db.redis.client import get_redis, redis_key

        client = await get_redis()
        raw = await client.get(redis_key("deployments", str(deployment_id), "crashloop_reset"))
    except Exception:  # noqa: BLE001
        return None
    return _parse(raw)


async def run_reconcile_tick(hub: Any = None) -> Dict[str, Any]:
    """Jobs-process entry: one locked tick with its own session."""
    from app.db.postgres.connection import get_session_maker

    lock = await _acquire_lock()
    if lock is False:
        return {"skipped": "another reconciler holds the lock"}
    try:
        async with get_session_maker()() as session:
            svc = DeploymentService.for_session(session, hub=hub)
            summary = await Reconciler(svc).tick()
            return summary.as_dict()
    finally:
        await _release_lock(lock)


_LOCK_PARTS = ("deployments", "reconcile", "lock")


async def _acquire_lock() -> Any:
    try:
        from app.db.redis.client import get_redis, redis_key

        client = await get_redis()
        token = str(uuid.uuid4())
        ttl = max(_setting("DEPLOYMENT_RECONCILE_INTERVAL_SECONDS", 15) * 4, 60)
        ok = await client.set(redis_key(*_LOCK_PARTS), token, nx=True, ex=ttl)
        return token if ok else False
    except Exception as exc:  # noqa: BLE001 - no Redis: run unlocked
        logger.debug(f"reconcile lock unavailable: {exc}")
        return None


async def _release_lock(token: Any) -> None:
    if not token:
        return
    try:
        from app.db.redis.client import get_redis, redis_key

        client = await get_redis()
        key = redis_key(*_LOCK_PARTS)
        if await client.get(key) == token:
            await client.delete(key)
    except Exception:  # noqa: BLE001
        pass
