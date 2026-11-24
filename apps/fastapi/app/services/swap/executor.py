"""
Swap state machine (rules.md section 3 "Swap safety sequence").

One ``swap_operations`` row = one plan of ordered steps::

    plan -> route_off -> drain -> unload -> load -> health -> route_on

(``load_first`` order for move/swap when VRAM allows: plan -> load -> health ->
route_off -> drain -> unload -> route_on; restart uses the agent's
``restart_instance``). Every step records its state, timestamps and the
``agent_commands.id`` it is waiting on, so a jobs-process restart resumes the
same row: a step left ``running`` with a ``command_id`` just re-waits for the
result the hub stores (the hub replays unanswered commands on reconnect).

Safety:

- one swap per device at a time (``LockPort`` + device ids in ``plan``);
- a fencing callback is consulted before every state change; when the jobs
  leader lost its lease the executor stops touching the row and the new
  leader resumes it;
- failure at or after ``unload`` triggers rollback (route the old instance
  back on, or reload it from ``rollback.previous_spec``); rollback is
  attempted once and its outcome is stored in ``rollback``;
- the whole swap is bounded by ``SWAP_MAX_DURATION_SECONDS``.

Live UI: when a ``live`` publisher is wired (``build_executor`` passes
``app.services.alerts.live.publish_live``), the start of every step and every
state change (started, step changed, rolling back, completed, rolled back,
failed, cancelled) is published as a ``swap`` live event after the row is saved.
Dry-run swaps are not announced. Publishing is best effort and never raises.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.core.logger import get_logger
from app.services.ha.automation_metrics import observe_swap
from app.services.swap.ports import (
    AlertsPort,
    CommandPort,
    CommandResult,
    DeploymentPort,
    FenceLost,
    HealthProbePort,
    InstanceRecord,
    LoadArgsPort,
    LockPort,
    RoutingPort,
    StepFailure,
    Swap,
    SwapStore,
)

logger = get_logger(__name__)

STATE_FOR_STEP = {
    "plan": "pending",
    "route_off": "draining",
    "drain": "draining",
    "unload": "unloading",
    "load": "loading",
    "restart": "loading",
    "health": "health_check",
    "route_on": "health_check",
    "reweight": "health_check",
}

FenceFn = Callable[[], Awaitable[bool]]
LiveFn = Callable[[str, Dict[str, Any]], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class SwapExecutor:
    """Drives one swap operation through its steps, resumably."""

    def __init__(
        self,
        store: SwapStore,
        commands: CommandPort,
        routing: RoutingPort,
        load_args: LoadArgsPort,
        health: HealthProbePort,
        locks: LockPort,
        alerts: Optional[AlertsPort] = None,
        fence: Optional[FenceFn] = None,
        max_duration_seconds: int = 1800,
        lock_ttl_seconds: int = 2400,
        owner: str = "swap-executor",
        command_ack_slack_seconds: int = 10,
        deployments: Optional[DeploymentPort] = None,
        load_wait_seconds: int = 3600,
        live: Optional[LiveFn] = None,
    ):
        self.store = store
        self.commands = commands
        self.routing = routing
        self.load_args = load_args
        self.health = health
        self.locks = locks
        self.alerts = alerts
        self.fence = fence
        self.max_duration = max_duration_seconds
        self.lock_ttl = lock_ttl_seconds
        self.owner = owner
        self.ack_slack = command_ack_slack_seconds
        self.deployments = deployments
        self.load_wait = load_wait_seconds
        self.live = live
        self._announced: Dict[str, tuple] = {}

    # ------------------------------------------------------------------ public

    async def drive(self, swap_id: uuid.UUID) -> Optional[Swap]:
        """Run the swap until it finishes, blocks on a dependency/lock, or loses the fence."""
        swap = await self.store.get_swap(swap_id)
        if swap is None or swap.is_terminal:
            return swap
        if swap.dry_run:
            return await self._finish_dry_run(swap)
        if not await self._dependencies_ready(swap):
            return swap
        lock_keys = [f"swap:lock:device:{d}" for d in swap.plan.get("lock_device_ids", [])]
        # The swap owns its device locks (not the process): after a jobs restart / leader
        # change the new leader resumes the swap and re-acquires them. Fencing keeps it to one
        # driver; other swaps on the same devices still wait.
        owner = f"swap:{swap.id}"
        if lock_keys and not await self.locks.acquire(lock_keys, owner, self.lock_ttl):
            logger.info(f"swap {swap.id}: device lock busy, retrying later")
            return swap
        fence_lost = False
        try:
            swap = await self._run(swap)
            if swap.is_terminal:
                await self._after_finished(swap)
            return swap
        except FenceLost:
            fence_lost = True
            logger.warning(f"swap {swap.id}: fencing token lost, another leader resumes it")
            return swap
        finally:
            # The lock owner is the swap, not the process: after losing the fence the new
            # leader may already hold these keys under the same owner. Releasing them here
            # would let another swap grab the devices mid-step; leave them to the new
            # leader (or their TTL).
            if lock_keys and not fence_lost:
                await self.locks.release(lock_keys, owner)

    async def _after_finished(self, swap: Swap) -> None:
        hook = getattr(self.store, "after_finished", None)
        if hook is None:
            return
        try:
            await hook(swap)
        except FenceLost:
            raise
        except Exception as exc:  # noqa: BLE001 - bookkeeping must not undo a finished swap
            logger.error(f"swap {swap.id}: after_finished hook failed: {exc}")

    # ------------------------------------------------------------------ main loop

    async def _run(self, swap: Swap) -> Swap:
        if swap.started_at is None:
            swap.started_at = _now()
            await self._save(swap)
        while swap.current_step < len(swap.steps):
            if not await self._fenced():
                logger.warning(f"swap {swap.id}: fence lost, leaving step {swap.current_step}")
                raise FenceLost(f"swap {swap.id}: fence lost before step {swap.current_step}")
            if await self._cancel_requested(swap):
                return await self._cancel(swap)
            if swap.started_at and (_now() - swap.started_at).total_seconds() > self.max_duration:
                return await self._fail(
                    swap,
                    StepFailure("swap exceeded SWAP_MAX_DURATION_SECONDS", "timeout"),
                )
            step = swap.steps[swap.current_step]
            swap.state = STATE_FOR_STEP.get(step["name"], swap.state)
            if step["state"] != "running":
                step["state"] = "running"
                step["started_at"] = _iso(_now())
            await self._save(swap)
            try:
                handler = getattr(self, f"_step_{step['name']}")
                await handler(swap, step)
            except FenceLost:
                raise
            except StepFailure as exc:
                step["state"] = "failed"
                step["error"] = str(exc)
                step["finished_at"] = _iso(_now())
                step["details"] = {
                    **(step.get("details") or {}),
                    **exc.details,
                    "code": exc.code,
                }
                await self._save(swap)
                return await self._fail(swap, exc)
            except Exception as exc:  # noqa: BLE001 - any bug must still roll back
                logger.exception(f"swap {swap.id}: step {step['name']} crashed: {exc}")
                step["state"] = "failed"
                step["error"] = f"{type(exc).__name__}: {exc}"
                step["finished_at"] = _iso(_now())
                await self._save(swap)
                return await self._fail(swap, StepFailure(str(exc), "internal"))
            if step["state"] == "running":
                step["state"] = "completed"
            step["finished_at"] = _iso(_now())
            swap.current_step += 1
            await self._save(swap)
        swap.state = "completed"
        swap.finished_at = _now()
        await self._save(swap)
        if swap.kind in ("move", "swap") and swap.from_instance_id and swap.to_instance_id:
            await self.store.write_audit(
                "instance.moved",
                "instance",
                swap.to_instance_id,
                None,
                {"instance_id": str(swap.from_instance_id)},
                {
                    "instance_id": str(swap.to_instance_id),
                    "load": swap.plan.get("load"),
                },
                f"instance moved by swap {swap.kind}",
                {"swap_operation_id": str(swap.id)},
            )
        await self.store.write_audit(
            "swap.completed",
            "swap_operation",
            swap.id,
            swap.kind,
            None,
            {"plan": swap.plan, "steps": [s["name"] for s in swap.steps]},
            f"swap {swap.kind} completed",
            {"trigger": swap.trigger},
        )
        return swap

    async def _cancel_requested(self, swap: Swap) -> bool:
        if swap.rollback.get("cancel_requested"):
            return True
        probe = getattr(self.store, "cancel_requested", None)
        if probe is not None and await probe(swap.id):
            swap.rollback["cancel_requested"] = True
            return True
        return False

    async def _fenced(self) -> bool:
        if self.fence is None:
            return True
        try:
            return bool(await self.fence())
        except Exception as exc:  # noqa: BLE001
            logger.error(f"fence check failed: {exc}")
            return False

    async def _save(self, swap: Swap) -> None:
        await self.store.save_swap(swap)
        observe_swap(swap)
        await self._publish(swap)

    async def _publish(self, swap: Swap) -> None:
        """``swap`` live event when the state or current step changed. Never raises."""
        if self.live is None or swap.dry_run:
            return
        try:
            step = swap.steps[swap.current_step] if swap.current_step < len(swap.steps) else None
            if (
                (step is None or step["state"] == "pending")
                and not swap.is_terminal
                and swap.state != "rolling_back"
            ):
                return  # between steps; the next step's start is announced
            mark = (swap.state, swap.current_step)
            if self._announced.get(str(swap.id)) == mark:
                return
            self._announced[str(swap.id)] = mark
            if swap.is_terminal:
                self._announced.pop(str(swap.id), None)
            labels_fn = getattr(self.store, "swap_labels", None)
            labels = (labels_fn(swap.id) if labels_fn is not None else None) or {}
            await self.live(
                "swap",
                {
                    "swap_id": str(swap.id),
                    "state": swap.state,
                    "kind": swap.kind,
                    "deployment_id": swap.deployment_id,
                    "deployment_name": labels.get("deployment_name"),
                    "node_id": swap.node_id,
                    "rule_name": labels.get("rule_name"),
                    "rule_run_id": swap.rule_run_id,
                    "trigger": swap.trigger,
                    "error_message": swap.error_message,
                    "step": step["name"] if step else None,
                    "current_step": swap.current_step,
                    "total_steps": len(swap.steps),
                },
            )
        except Exception as exc:  # noqa: BLE001 - live events must never break a swap
            logger.debug(f"swap {swap.id}: live publish failed: {exc}")

    async def _dependencies_ready(self, swap: Swap) -> bool:
        deps = [_uuid(d) for d in swap.plan.get("depends_on", [])]
        if not deps:
            return True
        states = await self.store.swap_states(deps)
        bad = [
            d for d in deps if states.get(str(d)) in ("failed", "rolled_back", "cancelled", None)
        ]
        if bad:
            swap.state = "cancelled"
            swap.finished_at = _now()
            swap.error_message = f"dependency swap(s) did not complete: {', '.join(map(str, bad))}"
            for step in swap.steps:
                if step["state"] == "pending":
                    step["state"] = "skipped"
            await self._save(swap)
            return False
        return all(states.get(str(d)) == "completed" for d in deps)

    async def _finish_dry_run(self, swap: Swap) -> Swap:
        for step in swap.steps:
            step["state"] = "skipped"
        swap.state = "completed"
        swap.started_at = swap.started_at or _now()
        swap.finished_at = _now()
        await self._save(swap)
        return swap

    # ------------------------------------------------------------------ commands

    async def _send_or_resume(
        self,
        swap: Swap,
        step: Dict[str, Any],
        node_id: uuid.UUID,
        command: str,
        args: Dict[str, Any],
        timeout_seconds: int,
        instance_id: Optional[uuid.UUID],
    ) -> CommandResult:
        if step.get("command_id"):
            command_id = _uuid(step["command_id"])
        else:
            command_id = await self.commands.send(
                node_id,
                command,
                args,
                timeout_seconds,
                swap_operation_id=swap.id,
                instance_id=instance_id,
            )
            step["command_id"] = str(command_id)
            await self._save(swap)
        return await self.commands.wait(command_id, timeout_seconds + self.ack_slack)

    async def _via_deployments(
        self, swap: Swap, step: Dict[str, Any], start, wait_seconds: int
    ) -> CommandResult:
        """Run a DeploymentService call that sends a command, then wait (resumable)."""
        if step.get("command_id"):
            command_id = _uuid(step["command_id"])
        else:
            command_id = await start()
            if command_id is None:
                return CommandResult("succeeded", {"noop": True})
            step["command_id"] = str(command_id)
            await self._save(swap)
        return await self.commands.wait(command_id, wait_seconds)

    # ------------------------------------------------------------------ steps

    async def _step_plan(self, swap: Swap, step: Dict[str, Any]) -> None:
        details: Dict[str, Any] = {}
        if swap.from_instance_id:
            old = await self.store.get_instance(swap.from_instance_id)
            if old is None:
                raise StepFailure("from_instance not found", "instance_not_found")
            swap.rollback.setdefault("previous_instance_spec", old.spec())
            swap.rollback.setdefault(
                "previous_routing",
                {"traffic_weight": old.traffic_weight, "accepting": True},
            )
            swap.rollback.setdefault("attempted", False)
            details["from_instance"] = str(old.id)
        load = swap.plan.get("load")
        if load:
            if not load.get("instance_id"):
                slice_spec = load.get("slice")
                if self.deployments is not None:
                    inst = await self.deployments.place_instance(
                        _uuid(load["deployment_id"]),
                        _uuid(load.get("node_id")),
                        [_uuid(d) for d in load.get("device_ids", [])],
                        slice_spec,
                        int(load.get("traffic_weight", 100)),
                    )
                else:
                    inst = await self.store.create_instance(
                        deployment_id=_uuid(load["deployment_id"]),
                        node_id=_uuid(load["node_id"]),
                        device_ids=[_uuid(d) for d in load.get("device_ids", [])],
                        traffic_weight=int(load.get("traffic_weight", 100)),
                        served_model_name=load.get("served_model_name"),
                    )
                load["instance_id"] = str(inst.id)
                swap.to_instance_id = inst.id
                await self._save(swap)
                if self.deployments is None and slice_spec and slice_spec.get("vram_bytes"):
                    await self.store.reserve_slices(
                        inst.id,
                        inst.deployment_id,
                        inst.device_ids,
                        int(slice_spec["vram_bytes"]),
                        int(slice_spec.get("compute_pct", 100)),
                    )
                details["created_instance"] = str(inst.id)
            else:
                swap.to_instance_id = _uuid(load["instance_id"])
        step["details"] = details

    async def _step_route_off(self, swap: Swap, step: Dict[str, Any]) -> None:
        if not swap.from_instance_id:
            step["state"] = "skipped"
            return
        # DB first: the proxy config push and the routing-table builder read instances.state.
        await self.store.update_instance(
            swap.from_instance_id, state="draining", state_changed_at=_now()
        )
        await self.routing.set_accepting(swap.from_instance_id, False)
        swap.rollback["routed_off"] = True
        step["details"] = {"instance_id": str(swap.from_instance_id)}

    async def _step_drain(self, swap: Swap, step: Dict[str, Any]) -> None:
        if not swap.from_instance_id:
            step["state"] = "skipped"
            return
        old = await self.store.get_instance(swap.from_instance_id)
        if old is None:
            raise StepFailure("from_instance vanished", "instance_not_found")
        timeout = int(swap.drain_timeout_seconds)
        result = await self._send_or_resume(
            swap,
            step,
            old.node_id,
            "drain_instance",
            {
                "instance_id": str(old.id),
                "timeout_seconds": timeout,
                "wait_for_zero": True,
            },
            timeout,
            old.id,
        )
        details = dict(result.result or {})
        if result.ok:
            step["details"] = {**details, "timed_out": False}
            return
        timed_out = (
            result.error_code in ("drain_timeout", "timeout") or result.status == "timed_out"
        )
        if timed_out and swap.plan.get("force_after_timeout"):
            step["details"] = {**details, "timed_out": True, "forced": True}
            swap.plan["force_unload"] = True
            logger.warning(f"swap {swap.id}: drain timed out, forcing unload per plan")
            return
        raise StepFailure(
            f"drain failed: {result.error_message or result.status}",
            result.error_code or result.status,
            details,
        )

    async def _step_unload(self, swap: Swap, step: Dict[str, Any]) -> None:
        if not swap.from_instance_id:
            step["state"] = "skipped"
            return
        old = await self.store.get_instance(swap.from_instance_id)
        if old is None:
            raise StepFailure("from_instance vanished", "instance_not_found")
        force = bool(swap.plan.get("force") or swap.plan.get("force_unload"))
        await self.store.update_instance(old.id, state="unloading", state_changed_at=_now())
        timeout = int(swap.drain_timeout_seconds) if not force else 60
        if self.deployments is not None:
            result = await self._via_deployments(
                swap,
                step,
                lambda: self.deployments.stop_instance(
                    old.id, force, 0 if force else timeout, swap.id
                ),
                timeout + 180,
            )
        else:
            result = await self._send_or_resume(
                swap,
                step,
                old.node_id,
                "unload_model",
                {
                    "instance_id": str(old.id),
                    "force": force,
                    "drain_timeout_seconds": 0 if force else timeout,
                    "stop_engine_if_empty": bool(swap.plan.get("stop_engine_if_empty", False)),
                },
                timeout,
                old.id,
            )
        if not result.ok:
            await self.store.update_instance(
                old.id, state="error", state_message=result.error_message
            )
            raise StepFailure(
                f"unload failed: {result.error_message or result.status}",
                result.error_code or result.status,
                dict(result.result or {}),
            )
        now = _now()
        await self.store.update_instance(
            old.id,
            state="stopped",
            health="unknown",
            stopped_at=now,
            state_changed_at=now,
            traffic_weight=0,
        )
        await self.store.set_slices_status(old.id, "released")
        swap.rollback["old_instance_gone"] = True
        step["details"] = dict(result.result or {})
        await self.store.write_audit(
            "instance.unloaded",
            "instance",
            old.id,
            old.served_model_name,
            old.spec(),
            None,
            f"instance unloaded by swap {swap.kind}",
            {"swap_operation_id": str(swap.id)},
        )

    async def _step_load(self, swap: Swap, step: Dict[str, Any]) -> None:
        target_id = swap.to_instance_id or _uuid((swap.plan.get("load") or {}).get("instance_id"))
        if target_id is None:
            step["state"] = "skipped"
            return
        inst = await self.store.get_instance(target_id)
        if inst is None:
            raise StepFailure("to_instance not found", "instance_not_found")
        slice_spec = (swap.plan.get("load") or {}).get("slice")
        if self.deployments is not None:
            await self.store.update_instance(inst.id, started_at=_now())
            result = await self._via_deployments(
                swap,
                step,
                lambda: self.deployments.start_instance(inst.id, swap.id),
                self.load_wait,
            )
        else:
            args = await self.load_args.build_load_args(inst, slice_spec)
            health_timeout = int(args.get("health_timeout_seconds", 600))
            await self.store.update_instance(
                inst.id, state="loading", started_at=_now(), state_changed_at=_now()
            )
            result = await self._send_or_resume(
                swap,
                step,
                inst.node_id,
                "load_model",
                args,
                health_timeout + 60,
                inst.id,
            )
        if not result.ok:
            await self.store.update_instance(
                inst.id,
                state="error",
                health="unknown",
                state_message=result.error_message,
                state_changed_at=_now(),
            )
            await self.store.set_slices_status(inst.id, "released")
            raise StepFailure(
                f"load failed: {result.error_message or result.status}",
                result.error_code or result.status,
                dict(result.result or {}),
            )
        res = result.result or {}
        now = _now()
        fields: Dict[str, Any] = {
            "state": "ready",
            "health": "healthy",
            "ready_at": now,
            "last_health_at": now,
            "state_changed_at": now,
        }
        if res.get("port") is not None:
            fields["port"] = int(res["port"])
        if res.get("pid") is not None:
            fields["pid"] = int(res["pid"])
        if res.get("served_model_name"):
            fields["served_model_name"] = res["served_model_name"]
        if res.get("engine_process_id"):
            fields["engine_process_id"] = _uuid(res["engine_process_id"])
        await self.store.update_instance(inst.id, **fields)
        await self.store.set_slices_status(inst.id, "active")
        step["details"] = {k: v for k, v in res.items() if k != "files"}
        await self.store.write_audit(
            "instance.loaded",
            "instance",
            inst.id,
            fields.get("served_model_name") or inst.served_model_name,
            None,
            {
                **inst.spec(),
                **{
                    k: (str(v) if isinstance(v, (uuid.UUID, datetime)) else v)
                    for k, v in fields.items()
                },
            },
            f"instance loaded by swap {swap.kind}",
            {"swap_operation_id": str(swap.id)},
        )

    async def _step_restart(self, swap: Swap, step: Dict[str, Any]) -> None:
        old = await self.store.get_instance(swap.from_instance_id)
        if old is None:
            raise StepFailure("instance not found", "instance_not_found")
        timeout = int(swap.drain_timeout_seconds)
        await self.store.update_instance(old.id, state="loading", state_changed_at=_now())
        result = await self._send_or_resume(
            swap,
            step,
            old.node_id,
            "restart_instance",
            {
                "instance_id": str(old.id),
                "drain_timeout_seconds": timeout,
                "force": bool(swap.plan.get("force", True)),
            },
            timeout + 660,
            old.id,
        )
        if not result.ok:
            await self.store.update_instance(
                old.id,
                state="error",
                health="unhealthy",
                state_message=result.error_message,
            )
            swap.rollback["old_instance_gone"] = True
            raise StepFailure(
                f"restart failed: {result.error_message or result.status}",
                result.error_code or result.status,
                dict(result.result or {}),
            )
        res = result.result or {}
        now = _now()
        fields: Dict[str, Any] = {
            "state": "ready",
            "health": "healthy",
            "ready_at": now,
            "last_health_at": now,
            "state_changed_at": now,
            "restart_count": int(old.restart_count or 0) + 1,
        }
        if res.get("port") is not None:
            fields["port"] = int(res["port"])
        if res.get("pid") is not None:
            fields["pid"] = int(res["pid"])
        await self.store.update_instance(old.id, **fields)
        swap.to_instance_id = old.id
        step["details"] = res
        await self.store.write_audit(
            "instance.restarted",
            "instance",
            old.id,
            old.served_model_name,
            None,
            res,
            f"instance restarted by swap {swap.kind}",
            {"swap_operation_id": str(swap.id)},
        )

    async def _step_health(self, swap: Swap, step: Dict[str, Any]) -> None:
        target_id = swap.to_instance_id
        if target_id is None:
            step["state"] = "skipped"
            return
        inst = await self.store.get_instance(target_id)
        if inst is None:
            raise StepFailure("to_instance not found", "instance_not_found")
        result = await self.health.probe(inst)
        if result.status == "skipped":
            step["state"] = "skipped"
            step["details"] = {"reason": result.error_message or "probe unavailable"}
            return
        if not result.ok:
            await self.store.update_instance(
                inst.id, health="unhealthy", health_message=result.error_message
            )
            raise StepFailure(
                f"health probe failed: {result.error_message}",
                "health_timeout",
                result.result or {},
            )
        step["details"] = result.result or {}

    async def _step_route_on(self, swap: Swap, step: Dict[str, Any]) -> None:
        target_id = swap.to_instance_id
        if target_id is None:
            step["state"] = "skipped"
            return
        weight = int(swap.plan.get("target_weight", 100))
        await self.store.update_instance(target_id, traffic_weight=weight)
        await self.routing.set_weight(target_id, weight)
        await self.routing.set_accepting(target_id, True)
        if swap.deployment_id:
            await self.store.set_deployment_status(swap.deployment_id, "running", None)
        step["details"] = {"instance_id": str(target_id), "traffic_weight": weight}

    async def _step_reweight(self, swap: Swap, step: Dict[str, Any]) -> None:
        for item in swap.plan.get("reweight", []):
            iid = _uuid(item["instance_id"])
            await self.store.update_instance(iid, traffic_weight=int(item["traffic_weight"]))
            await self.routing.set_weight(iid, int(item["traffic_weight"]))
        step["details"] = {"count": len(swap.plan.get("reweight", []))}

    # ------------------------------------------------------------------ failure / rollback / cancel

    async def _fail(self, swap: Swap, exc: StepFailure) -> Swap:
        swap.error_message = str(exc)[:2048]
        if (
            swap.from_instance_id
            and swap.rollback.get("routed_off")
            and not swap.rollback.get("attempted")
        ):
            return await self._rollback(swap, exc)
        await self._cleanup_new_instance(swap)
        return await self._finish_failed(swap, exc)

    async def _rollback(self, swap: Swap, exc: StepFailure) -> Swap:
        swap.state = "rolling_back"
        swap.rollback["attempted"] = True
        swap.rollback["reason"] = str(exc)
        await self._save(swap)
        await self._cleanup_new_instance(swap)
        old_id = swap.from_instance_id
        old = await self.store.get_instance(old_id) if old_id else None
        ok = False
        detail = ""
        try:
            if old is not None and not swap.rollback.get("old_instance_gone"):
                await self._resume_old(old)
                ok, detail = True, "route_on"
            elif old is not None:
                ok, detail = await self._reload_old(swap, old)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"rollback error: {e}"
        swap.rollback["result"] = detail
        now = _now()
        if ok:
            swap.state = "rolled_back"
            swap.finished_at = now
            await self._save(swap)
            await self._alert(
                "swap.rolled_back",
                "warning",
                f"Swap {swap.kind} rolled back",
                f"{exc} — previous instance restored ({detail})",
                swap,
            )
        else:
            swap.state = "failed"
            swap.finished_at = now
            await self._save(swap)
            if swap.deployment_id:
                await self.store.set_deployment_status(
                    swap.deployment_id, "degraded", swap.error_message
                )
            await self._alert(
                "swap.failed",
                "critical",
                f"Swap {swap.kind} failed",
                f"{exc} — rollback did not restore the previous instance ({detail})",
                swap,
            )
        await self.store.write_audit(
            "swap.failed" if not ok else "swap.rolled_back",
            "swap_operation",
            swap.id,
            swap.kind,
            None,
            {"error": str(exc), "rollback": detail},
            str(exc),
            {"trigger": swap.trigger},
            outcome="failure",
        )
        return swap

    async def _resume_old(self, old: InstanceRecord) -> None:
        try:
            cid = await self.commands.send(
                old.node_id,
                "drain_instance",
                {
                    "instance_id": str(old.id),
                    "timeout_seconds": 0,
                    "wait_for_zero": False,
                    "resume": True,
                },
                30,
                instance_id=old.id,
            )
            await self.commands.wait(cid, 30)
        except Exception as e:  # noqa: BLE001 - routing table is what matters
            logger.warning(f"resume drain for {old.id} failed: {e}")
        await self.store.update_instance(old.id, state="ready", state_changed_at=_now())
        await self.routing.set_accepting(old.id, True)

    async def _reload_old(self, swap: Swap, old: InstanceRecord) -> tuple:
        spec = swap.rollback.get("previous_instance_spec") or old.spec()
        if self.deployments is not None:
            return await self._replace_old(swap, old, spec)
        slice_spec = swap.plan.get("previous_slice") or None
        args = await self.load_args.build_load_args(old, slice_spec)
        await self.store.update_instance(old.id, state="loading", state_changed_at=_now())
        cid = await self.commands.send(
            old.node_id,
            "load_model",
            args,
            int(args.get("health_timeout_seconds", 600)) + 60,
            swap_operation_id=swap.id,
            instance_id=old.id,
        )
        result = await self.commands.wait(cid, int(args.get("health_timeout_seconds", 600)) + 70)
        if not result.ok:
            await self.store.update_instance(
                old.id, state="error", state_message=result.error_message
            )
            return False, f"reload failed: {result.error_message or result.status}"
        res = result.result or {}
        now = _now()
        fields = {
            "state": "ready",
            "health": "healthy",
            "ready_at": now,
            "state_changed_at": now,
            "traffic_weight": int(spec.get("traffic_weight", 100)),
        }
        if res.get("port") is not None:
            fields["port"] = int(res["port"])
        await self.store.update_instance(old.id, **fields)
        await self.store.set_slices_status(old.id, "active")
        await self.routing.set_accepting(old.id, True)
        return True, "reloaded_previous_spec"

    async def _replace_old(self, swap: Swap, old: InstanceRecord, spec: Dict[str, Any]) -> tuple:
        """Rollback: a fresh instance on the old node/devices with the old weight."""
        weight = int(spec.get("traffic_weight", 100))
        inst = await self.deployments.place_instance(
            old.deployment_id,
            old.node_id,
            list(old.device_ids),
            swap.plan.get("previous_slice"),
            weight,
        )
        swap.rollback["replacement_instance_id"] = str(inst.id)
        await self._save(swap)
        cid = await self.deployments.start_instance(inst.id, swap.id)
        result = await self.commands.wait(cid, self.load_wait)
        if not result.ok:
            await self.store.update_instance(
                inst.id, state="error", state_message=result.error_message
            )
            return False, f"reload failed: {result.error_message or result.status}"
        res = result.result or {}
        now = _now()
        fields: Dict[str, Any] = {
            "state": "ready",
            "health": "healthy",
            "ready_at": now,
            "state_changed_at": now,
            "traffic_weight": weight,
        }
        if res.get("port") is not None:
            fields["port"] = int(res["port"])
        await self.store.update_instance(inst.id, **fields)
        await self.store.set_slices_status(inst.id, "active")
        await self.routing.set_accepting(inst.id, True)
        return True, "reloaded_previous_spec"

    async def _cleanup_new_instance(self, swap: Swap) -> None:
        new_id = swap.to_instance_id
        if new_id is None or new_id == swap.from_instance_id:
            return
        inst = await self.store.get_instance(new_id)
        if inst is None:
            return
        if inst.state in ("ready", "loading") and self.deployments is not None:
            try:
                cid = await self.deployments.stop_instance(inst.id, True, 0, swap.id)
                if cid is not None:
                    await self.commands.wait(cid, 120)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"cleanup unload of {inst.id} failed: {e}")
        elif inst.state == "ready":
            try:
                cid = await self.commands.send(
                    inst.node_id,
                    "unload_model",
                    {
                        "instance_id": str(inst.id),
                        "force": True,
                        "drain_timeout_seconds": 0,
                    },
                    60,
                    swap_operation_id=swap.id,
                    instance_id=inst.id,
                )
                await self.commands.wait(cid, 70)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"cleanup unload of {inst.id} failed: {e}")
        await self.store.update_instance(
            inst.id, state="stopped", stopped_at=_now(), traffic_weight=0
        )
        await self.store.set_slices_status(inst.id, "released")
        swap.rollback["new_instance_cleaned"] = str(inst.id)

    async def _finish_failed(self, swap: Swap, exc: StepFailure) -> Swap:
        swap.state = "failed"
        swap.finished_at = _now()
        for step in swap.steps:
            if step["state"] == "pending":
                step["state"] = "skipped"
        await self._save(swap)
        await self._alert("swap.failed", "critical", f"Swap {swap.kind} failed", str(exc), swap)
        await self.store.write_audit(
            "swap.failed",
            "swap_operation",
            swap.id,
            swap.kind,
            None,
            {"error": str(exc)},
            str(exc),
            {"trigger": swap.trigger},
            outcome="failure",
        )
        return swap

    async def _cancel(self, swap: Swap) -> Swap:
        names = [s["name"] for s in swap.steps]
        unload_index = names.index("unload") if "unload" in names else len(names)
        past_unload = swap.current_step > unload_index or swap.rollback.get("old_instance_gone")
        if past_unload:
            return await self._rollback(swap, StepFailure("cancelled by operator", "cancelled"))
        for step in swap.steps[swap.current_step :]:
            if step["state"] in ("pending", "running"):
                if step.get("command_id"):
                    try:
                        await self.commands.cancel(_uuid(step["command_id"]))
                    except Exception:  # noqa: BLE001
                        pass
                step["state"] = "skipped"
        await self._cleanup_new_instance(swap)
        if swap.from_instance_id and swap.rollback.get("routed_off"):
            old = await self.store.get_instance(swap.from_instance_id)
            if old is not None:
                await self._resume_old(old)
        swap.state = "cancelled"
        swap.finished_at = _now()
        swap.error_message = "cancelled by operator"
        await self._save(swap)
        return swap

    async def _alert(
        self, event_type: str, severity: str, title: str, message: str, swap: Swap
    ) -> None:
        if self.alerts is None:
            return
        try:
            await self.alerts.emit(
                event_type,
                severity,
                title,
                message,
                resource_type="swap_operation",
                resource_id=swap.id,
                resource_name=swap.kind,
                payload={
                    "deployment_id": (str(swap.deployment_id) if swap.deployment_id else None),
                    "trigger": swap.trigger,
                    "kind": swap.kind,
                },
                dedupe_key=f"{event_type}:{swap.id}",
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"alert emit failed for swap {swap.id}: {e}")


def build_steps(
    kind: str, order: str = "unload_first", has_from: bool = True, has_load: bool = True
) -> List[Dict[str, Any]]:
    """Ordered step list for a swap kind (plan.order chooses load-first for move/swap)."""
    from app.services.swap.ports import make_step

    if kind in ("unload", "scale_down"):
        names = ["plan", "route_off", "drain", "unload"]
    elif kind in ("load", "scale_up"):
        names = ["plan", "load", "health", "route_on"]
    elif kind == "restart":
        names = ["plan", "route_off", "restart", "health", "route_on"]
    elif kind in ("move", "swap"):
        if order == "load_first":
            names = [
                "plan",
                "load",
                "health",
                "route_off",
                "drain",
                "unload",
                "route_on",
            ]
        else:
            names = [
                "plan",
                "route_off",
                "drain",
                "unload",
                "load",
                "health",
                "route_on",
            ]
    elif kind == "apply_layout":
        names = ["plan", "reweight"]
    else:
        raise ValueError(f"unknown swap kind {kind!r}")
    return [make_step(n) for n in names]
