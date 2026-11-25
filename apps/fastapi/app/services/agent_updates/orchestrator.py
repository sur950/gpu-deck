"""
Agent update orchestration (agent protocol §14). Process-agnostic: every function opens its own
sessions and commits; commands reach agents through ``AgentHub`` (Redis bus to the hub).

Run lifecycle (``agent_update_runs.state``)::

    pending -> staging -> staged -> swapping -> restarting -> readopting -> health_check
            -> succeeded | rolled_back | failed            (cancelled before the swap)

- ``pending`` / ``staging``: ``stage_update`` is sent unless the node already reported the
  version staged (pre-staging does that in the background, ``AGENT_UPDATE_PRESTAGE``);
- ``staged``: ``apply_update`` is sent; the run now has a deadline (twice the health timeout
  plus a margin). Nothing is downloaded from here on;
- ``swapping`` / ``restarting`` / ``readopting`` / ``health_check``: from the agents'
  ``event(agent_update)`` phases and the hub (a disconnect while swapping = restarting, a
  reconnect with the new version = readopting);
- the outcome comes from the agent that runs after the restart (event + ``apply_update``
  result). Past the deadline the run is marked failed (``timeout``); a later report from the
  node still records the real outcome.

While a run is between ``swapping`` and its outcome, ``nodes.metadata.agent_update_active``
holds ``{run_id, state, version, until}``: the UI shows the node as Updating and offline alerts
are held back (``NodeRepository.claim_offline_alerts``).

Rolling updates (``agent_update_rollouts``) update one node at a time in ``node_ids`` order and
stop at the first rolled back or failed node; nodes that cannot be updated (offline, managed by
Kubernetes, agent too old, already on the version) are skipped with a reason. ``auto`` rollouts
only start new nodes inside the cluster's maintenance window.

Entry points: ``tick`` (scheduler job ``agent_updates.tick``), ``advance_run`` /
``advance_rollout`` (right after an admin starts one), ``on_agent_event`` /
``on_connected`` / ``on_disconnected`` / ``on_command_terminal`` (hub).
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_session_maker
from app.db.postgres.models.generated_models import AgentUpdateRuns, Clusters
from app.db.postgres.repositories.agent_command_repository import (
    TERMINAL_STATES,
    AgentCommandRepository,
)
from app.db.postgres.repositories.agent_update_repository import (
    ACTIVE_RUN_STATES,
    TERMINAL_RUN_STATES,
    AgentRolloutRepository,
    AgentUpdateRunRepository,
)
from app.db.postgres.repositories.cluster_repository import ClusterRepository
from app.db.postgres.repositories.fleet_event_repository import FleetEventRepository
from app.db.postgres.repositories.node_repository import NodeRepository
from app.schemas.agent_update_schema import policy_from_config
from app.services.agent_updates import releases
from app.services.agent_updates import status as st
from app.services.agent_updates import tokens
from app.services.agent_updates.policy import auto_allowed

logger = get_logger(__name__)

STAGE_TIMEOUT_SECONDS = 1800
PRE_APPLY_DEADLINE_SECONDS = 3600
APPLY_MARGIN_SECONDS = 600
STAGE_RETRY_AFTER_SECONDS = 1800
PRESTAGE_EVERY_SECONDS = 60.0
APPLYING = ("swapping", "restarting", "readopting", "health_check")
_last_prestage = 0.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or utcnow()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def step(phase: str, message: Optional[str] = None, at: Optional[str] = None) -> Dict[str, Any]:
    return {"phase": phase, "at": at or _iso(), "message": message}


@asynccontextmanager
async def _lock(name: str, ttl: int = 30) -> AsyncIterator[bool]:
    """Best-effort cross-process lock (Redis SET NX); yields False when someone else holds it."""
    from app.db.redis.client import get_redis, redis_key

    key = redis_key("agent_updates", "lock", name)
    token = uuid.uuid4().hex
    got = True
    client = None
    try:
        client = await get_redis()
        got = bool(await client.set(key, token, nx=True, ex=ttl))
    except Exception as exc:  # noqa: BLE001 - Redis down: proceed (DB constraints still hold)
        logger.debug(f"agent update lock {name} unavailable: {exc}")
    try:
        yield got
    finally:
        if got and client is not None:
            try:
                if await client.get(key) == token:
                    await client.delete(key)
            except Exception:  # noqa: BLE001
                pass


async def _publish(node_id: uuid.UUID, run: Optional[AgentUpdateRuns]) -> None:
    from app.services.metrics.live import publish_live_event

    await publish_live_event(
        "node",
        {
            "node_id": str(node_id),
            "kind": "agent_update",
            "agent_update": (
                {
                    "run_id": str(run.id),
                    "state": run.state,
                    "to_version": run.to_version,
                    "steps": run.steps or [],
                }
                if run is not None
                else None
            ),
        },
    )


# ------------------------------------------------------------------ commands


async def send_stage(node_id: uuid.UUID, version: str, target: str) -> Optional[uuid.UUID]:
    """``stage_update`` with a fresh download token; the command id, or None."""
    from app.services.agent_hub.service import AgentHub

    manifest = await releases.get_manifest(version)
    if manifest is None or manifest.artifact(target) is None:
        logger.warning(f"agent update: no signed {version} build for {target} (node {node_id})")
        return None
    token = await tokens.issue(node_id, version, target)
    outcome = await AgentHub.send_command(
        node_id,
        "stage_update",
        {
            "version": version,
            "target": target,
            "manifest": manifest.wire(),
            "download_token": token,
        },
        timeout=STAGE_TIMEOUT_SECONDS,
        wait=False,
    )
    if outcome.state == "rejected":
        logger.warning(
            f"agent update: stage_update rejected for node {node_id}: {outcome.error_message}"
        )
        return None
    return outcome.command_id


async def _send_apply(run: AgentUpdateRuns) -> Any:
    from app.services.agent_hub.service import AgentHub

    health = int(run.health_timeout_seconds or settings.AGENT_UPDATE_HEALTH_TIMEOUT_SECONDS)
    return await AgentHub.send_command(
        run.node_id,
        "apply_update",
        {
            "version": run.to_version,
            "health_timeout_seconds": health,
            "run_id": str(run.id),
        },
        timeout=health * 2 + APPLY_MARGIN_SECONDS,
        wait=False,
    )


# ------------------------------------------------------------------ runs


async def create_run(
    session,
    node: Any,
    version: str,
    *,
    trigger: str,
    actor_id: Optional[uuid.UUID] = None,
    rollout_id: Optional[uuid.UUID] = None,
    health_timeout: Optional[int] = None,
) -> AgentUpdateRuns:
    runs = AgentUpdateRunRepository(session)
    return await runs.create(
        node_id=node.id,
        cluster_id=node.cluster_id,
        rollout_id=rollout_id,
        from_version=node.agent_version,
        to_version=version,
        state="pending",
        trigger=trigger,
        steps=[step("pending", f"update {node.agent_version or '?'} -> {version} requested")],
        health_timeout_seconds=int(health_timeout or settings.AGENT_UPDATE_HEALTH_TIMEOUT_SECONDS),
        deadline_at=utcnow() + timedelta(seconds=PRE_APPLY_DEADLINE_SECONDS),
        created_by=actor_id,
        started_at=utcnow(),
    )


async def _finish(
    session,
    run: AgentUpdateRuns,
    state: str,
    code: Optional[str] = None,
    message: Optional[str] = None,
    at: Optional[str] = None,
) -> bool:
    """Record the outcome once (a real outcome may replace a timeout). True when changed."""
    if run.state in TERMINAL_RUN_STATES and not (
        run.state == "failed" and run.error_code == "timeout" and state != "failed"
    ):
        return False
    runs = AgentUpdateRunRepository(session)
    nodes = NodeRepository(session)
    events = FleetEventRepository(session)
    await runs.set_state(
        run.id,
        state,
        step=step(state, message, at),
        error_code=code,
        error_message=(message or None) if state != "succeeded" else None,
    )
    await nodes.merge_metadata(run.node_id, {"agent_update_active": None})
    node = await nodes.get_by_id(run.node_id)
    name = node.name if node is not None else str(run.node_id)
    after = {"run_id": str(run.id), "from": run.from_version, "to": run.to_version}
    if state == "succeeded":
        await events.audit(
            "node.agent_updated",
            actor_type="system",
            resource_type="node",
            resource_id=run.node_id,
            resource_name=name,
            message=f"agent {run.from_version} -> {run.to_version}",
            after_state=after,
        )
    elif state in ("rolled_back", "failed"):
        critical = state == "failed"
        await events.alert(
            "critical" if critical else "warning",
            "node.agent_update_failed" if critical else "node.agent_update_rolled_back",
            f"Agent update {'failed' if critical else 'rolled back'} on {name}",
            message=message,
            resource_type="node",
            resource_id=run.node_id,
            resource_name=name,
            payload={**after, "error_code": code},
            dedupe_key=f"agent_update:{run.id}",
        )
        await events.audit(
            f"node.agent_update_{state}",
            actor_type="system",
            resource_type="node",
            resource_id=run.node_id,
            resource_name=name,
            message=message,
            after_state=after,
            outcome="failure",
        )
    return True


async def _mark_applying(session, run: AgentUpdateRuns, state: str, message: Optional[str]) -> None:
    runs = AgentUpdateRunRepository(session)
    health = int(run.health_timeout_seconds or 120)
    until = run.deadline_at or utcnow() + timedelta(seconds=health * 2 + APPLY_MARGIN_SECONDS)
    last = (run.steps or [])[-1:] or [{}]
    if run.state != state or last[0].get("phase") != state:
        await runs.set_state(run.id, state, step=step(state, message))
    await NodeRepository(session).merge_metadata(
        run.node_id,
        {
            "agent_update_active": {
                "run_id": str(run.id),
                "state": state,
                "version": run.to_version,
                "until": _iso(until),
            }
        },
    )


async def advance_run(run_id: uuid.UUID) -> Optional[str]:
    """Move one run forward (stage or apply when due, deadlines). Returns its state."""
    async with _lock(f"run:{run_id}") as got:
        if not got:
            return None
        return await _advance_run(run_id)


async def _advance_run(run_id: uuid.UUID) -> Optional[str]:
    async with get_session_maker()() as session:
        runs = AgentUpdateRunRepository(session)
        run = await runs.get_by_id(run_id)
        if run is None or run.state in TERMINAL_RUN_STATES:
            return run.state if run else None
        node = await NodeRepository(session).get_active(run.node_id)
        now = utcnow()
        if node is None or node.status == "removed":
            await _finish(session, run, "cancelled", "node_removed", "the node was removed")
            await session.commit()
            return "cancelled"
        if run.deadline_at and now > run.deadline_at:
            if run.state in ("pending", "staging", "staged"):
                await _finish(
                    session,
                    run,
                    "failed",
                    "stage_timeout",
                    "the node did not stage and start the update in time (is it online?)",
                )
            else:
                await _finish(
                    session,
                    run,
                    "failed",
                    "timeout",
                    "no outcome from the node in time; a node whose new agent does not come up "
                    "rolls back on its own and reports when it reconnects",
                )
            await session.commit()
            await _publish(run.node_id, run)
            return "failed"
        if run.state in APPLYING or node.status not in st.CONNECTED:
            await session.commit()
            return run.state
        rep = st.reported(node)
        staged = run.state == "staged" or rep.get("staged_version") == run.to_version
        stage_cmd = run.stage_command_id
        target = st.node_target(node)
        await session.commit()

    if staged:
        outcome = await _send_apply(run)
        async with get_session_maker()() as session:
            runs = AgentUpdateRunRepository(session)
            run = await runs.get_by_id(run_id)
            if run is None or run.state in TERMINAL_RUN_STATES:
                return None
            if outcome.state == "rejected":
                await _finish(session, run, "failed", outcome.error_code, outcome.error_message)
                await session.commit()
                return "failed"
            health = int(run.health_timeout_seconds or 120)
            deadline = utcnow() + timedelta(seconds=health * 2 + APPLY_MARGIN_SECONDS)
            await runs.set_state(
                run.id,
                "swapping",
                step=step("swapping", f"applying the staged {run.to_version}"),
                apply_command_id=outcome.command_id,
                deadline_at=deadline,
            )
            run = await runs.get_by_id(run_id)
            await _mark_applying(session, run, "swapping", None)
            await session.commit()
        await _publish(run.node_id, run)
        return "swapping"

    if stage_cmd is not None:
        return "staging"  # waiting for the stage_update result
    if not target:
        async with get_session_maker()() as session:
            run = await AgentUpdateRunRepository(session).get_by_id(run_id)
            await _finish(
                session,
                run,
                "failed",
                "update_unsupported",
                "the agent did not report its build target (it cannot update itself)",
            )
            await session.commit()
        return "failed"
    cmd_id = await send_stage(run.node_id, run.to_version, target)
    async with get_session_maker()() as session:
        runs = AgentUpdateRunRepository(session)
        run = await runs.get_by_id(run_id)
        if run is None or run.state in TERMINAL_RUN_STATES:
            return None
        if cmd_id is None:
            await _finish(
                session,
                run,
                "failed",
                "no_release",
                f"no signed {run.to_version} build for {target} is available to stage",
            )
            await session.commit()
            return "failed"
        await runs.set_state(
            run.id,
            "staging",
            step=step("staging", f"downloading {run.to_version} to the node"),
            stage_command_id=cmd_id,
        )
        await session.commit()
    await _publish(run.node_id, run)
    return "staging"


# ------------------------------------------------------------------ hub callbacks


async def on_agent_event(node_id: uuid.UUID, payload: Dict[str, Any]) -> None:
    """``event(agent_update)`` from a node (hub ingest)."""
    details = payload.get("details") or {}
    phase = str(details.get("phase") or "")
    version = details.get("version")
    message = payload.get("message")
    at = details.get("at") if isinstance(details.get("at"), str) else None
    finished_rollout: Optional[uuid.UUID] = None
    run = None
    advance: Optional[uuid.UUID] = None
    async with get_session_maker()() as session:
        nodes = NodeRepository(session)
        runs = AgentUpdateRunRepository(session)
        run = await runs.active_for_node(node_id)
        if run is None and details.get("run_id"):
            try:
                candidate = await runs.get_by_id(uuid.UUID(str(details["run_id"])))
            except ValueError:
                candidate = None
            if candidate is not None and candidate.node_id == node_id:
                run = candidate
        if run is not None and version and run.to_version != version:
            run = None
        node = await nodes.get_by_id(node_id)
        rep = dict(st.reported(node)) if node is not None else {}
        if phase == "staging":
            rep["state"] = "staging"
            await nodes.merge_metadata(node_id, {"agent_update": rep})
        elif phase == "staged":
            rep.update(state="staged", staged_version=version, staged_at=at or _iso())
            await nodes.merge_metadata(
                node_id, {"agent_update": rep, "agent_update_stage_failed": None}
            )
            if run is not None and run.state in ("pending", "staging"):
                await runs.set_state(run.id, "staged", step=step("staged", message, at))
                advance = run.id
        elif phase == "stage_failed":
            rep["state"] = "idle"
            await nodes.merge_metadata(
                node_id,
                {
                    "agent_update": rep,
                    "agent_update_stage_failed": {
                        "version": version,
                        "at": _iso(),
                        "error": details.get("error_code"),
                        "message": message,
                    },
                },
            )
            if run is not None and run.state in ("pending", "staging"):
                await _finish(
                    session,
                    run,
                    "failed",
                    details.get("error_code") or "stage_failed",
                    message,
                    at,
                )
                finished_rollout = run.rollout_id
        elif phase in APPLYING:
            if run is not None and run.state not in TERMINAL_RUN_STATES:
                await _mark_applying(session, run, phase, message)
        elif phase in ("succeeded", "rolled_back", "failed"):
            if run is not None:
                code = details.get("error_code") if phase != "succeeded" else None
                if await _finish(session, run, phase, code, message, at):
                    finished_rollout = run.rollout_id
        await session.commit()
        if run is not None:
            run = await runs.get_by_id(run.id)
    if run is not None:
        await _publish(node_id, run)
    if advance is not None:
        await advance_run(advance)
    if finished_rollout is not None:
        await advance_rollout(finished_rollout)


async def on_connected(node_id: uuid.UUID, agent_version: Optional[str]) -> None:
    """A node connected: the new version coming up means its engines are being re-adopted."""
    async with get_session_maker()() as session:
        runs = AgentUpdateRunRepository(session)
        run = await runs.active_for_node(node_id)
        if run is None:
            return
        if run.state in ("swapping", "restarting") and agent_version == run.to_version:
            await _mark_applying(session, run, "readopting", f"agent {agent_version} reconnected")
        elif run.state in APPLYING and agent_version == run.from_version:
            await runs.add_step(
                run.id,
                step(run.state, f"agent {agent_version} reconnected (rolling back?)"),
            )
        await session.commit()
        run = await runs.get_by_id(run.id)
    await _publish(node_id, run)
    if run is not None and run.state in ("pending", "staging", "staged"):
        await advance_run(run.id)


async def on_disconnected(node_id: uuid.UUID) -> None:
    """The agent went away: during a swap that is the restart (no offline alert while updating)."""
    async with get_session_maker()() as session:
        runs = AgentUpdateRunRepository(session)
        run = await runs.active_for_node(node_id)
        if run is None or run.state not in ("swapping", "restarting"):
            return
        await _mark_applying(session, run, "restarting", "agent stopped for the restart")
        await runs.note_node_updating(node_id, f"agent updating to {run.to_version}")
        await session.commit()
        run = await runs.get_by_id(run.id)
    await _publish(node_id, run)


async def on_command_terminal(command_id: uuid.UUID) -> None:
    """A ``stage_update`` / ``apply_update`` result (hub, after the row is terminal)."""
    rollout: Optional[uuid.UUID] = None
    advance: Optional[uuid.UUID] = None
    run = None
    async with get_session_maker()() as session:
        cmd = await AgentCommandRepository(session).get_by_id(command_id)
        if cmd is None or cmd.state not in TERMINAL_STATES:
            return
        nodes = NodeRepository(session)
        runs = AgentUpdateRunRepository(session)
        run = await runs.by_command(command_id)
        args = (cmd.payload or {}).get("args") or {}
        ok = cmd.state == "succeeded"
        if cmd.command_type == "stage_update":
            node = await nodes.get_by_id(cmd.node_id)
            rep = dict(st.reported(node)) if node is not None else {}
            if ok:
                result = cmd.result or {}
                rep.update(
                    state="staged",
                    staged_version=result.get("staged_version") or args.get("version"),
                    staged_sha256=result.get("sha256"),
                    staged_at=result.get("staged_at") or _iso(),
                )
                await nodes.merge_metadata(
                    cmd.node_id,
                    {"agent_update": rep, "agent_update_stage_failed": None},
                )
            else:
                rep["state"] = "idle"
                await nodes.merge_metadata(
                    cmd.node_id,
                    {
                        "agent_update": rep,
                        "agent_update_stage_failed": {
                            "version": args.get("version"),
                            "at": _iso(),
                            "error": cmd.error_code,
                            "message": cmd.error_message,
                        },
                    },
                )
            if run is not None and run.stage_command_id == command_id:
                if ok and run.state in ("pending", "staging"):
                    await runs.set_state(run.id, "staged", step=step("staged"))
                    advance = run.id
                elif not ok and run.state in ("pending", "staging"):
                    await _finish(
                        session,
                        run,
                        "failed",
                        cmd.error_code or "stage_failed",
                        cmd.error_message or f"stage_update {cmd.state}",
                    )
                    rollout = run.rollout_id
        elif cmd.command_type == "apply_update" and run is not None:
            if ok:
                if await _finish(session, run, "succeeded", None, "agent updated"):
                    rollout = run.rollout_id
            elif cmd.error_code == "rolled_back":
                if await _finish(session, run, "rolled_back", "rolled_back", cmd.error_message):
                    rollout = run.rollout_id
            elif cmd.state != "timed_out":
                # Refused before anything changed (not staged, helper missing, ...) or failed.
                # A timed-out command is left to the run deadline: the node may still report.
                if await _finish(
                    session,
                    run,
                    "failed",
                    cmd.error_code or "apply_failed",
                    cmd.error_message,
                ):
                    rollout = run.rollout_id
        await session.commit()
        if run is not None:
            run = await runs.get_by_id(run.id)
    if run is not None:
        await _publish(run.node_id, run)
    if advance is not None:
        await advance_run(advance)
    if rollout is not None:
        await advance_rollout(rollout)


# ------------------------------------------------------------------ rollouts


async def start_rollout(
    session,
    cluster_id: uuid.UUID,
    version: str,
    *,
    trigger: str,
    actor_id: Optional[uuid.UUID] = None,
):
    """A rollout over every node of the cluster (by name); the caller commits, then advances."""
    nodes = await NodeRepository(session).list_active(cluster_id)
    return await AgentRolloutRepository(session).create(
        cluster_id=cluster_id,
        target_version=version,
        trigger=trigger,
        state="running",
        node_ids=[n.id for n in nodes],
        completed_count=0,
        skipped=[],
        created_by=actor_id,
        started_at=utcnow(),
        message=f"rolling update to {version}",
    )


def skip_reason(node: Any, version: str) -> Optional[str]:
    """Why a rollout leaves ``node`` out (None = update it)."""
    if node is None or node.status == "removed":
        return "removed"
    mode = st.node_mode(node)
    if mode == "managed":
        return "managed by Helm"
    if not st.needs_update(node, version):
        return f"already on {node.agent_version}"
    if node.status not in st.CONNECTED:
        return f"node is {node.status}"
    if mode == "manual":
        return "agent predates self-update: update manually"
    if mode == "unsupported":
        return st.reported(node).get("reason") or "cannot update itself"
    return None


async def advance_rollout(rollout_id: uuid.UUID) -> Optional[str]:
    async with _lock(f"rollout:{rollout_id}") as got:
        if not got:
            return None
        return await _advance_rollout(rollout_id)


async def _advance_rollout(rollout_id: uuid.UUID) -> Optional[str]:
    next_run: Optional[uuid.UUID] = None
    async with get_session_maker()() as session:
        rollouts = AgentRolloutRepository(session)
        runs_repo = AgentUpdateRunRepository(session)
        nodes = NodeRepository(session)
        r = await rollouts.get_by_id(rollout_id)
        if r is None or r.state != "running":
            return r.state if r else None
        runs, _ = await runs_repo.list(1, 1000, rollout_id=rollout_id)
        if any(x.state in ACTIVE_RUN_STATES for x in runs):
            return "running"
        events = FleetEventRepository(session)
        bad = next((x for x in runs if x.state in ("rolled_back", "failed")), None)
        if bad is not None:
            node = await nodes.get_by_id(bad.node_id)
            name = node.name if node else str(bad.node_id)
            msg = (
                f"stopped: the update of {name} {bad.state.replace('_', ' ')}"
                f"{': ' + bad.error_message if bad.error_message else ''}"
            )
            await rollouts.patch(
                rollout_id,
                state="failed",
                failed_node_id=bad.node_id,
                message=msg[:1024],
            )
            await events.alert(
                "warning",
                "cluster.agent_rollout_stopped",
                "Rolling agent update stopped",
                message=msg,
                resource_type="cluster",
                resource_id=r.cluster_id,
                payload={"rollout_id": str(rollout_id), "node_id": str(bad.node_id)},
                dedupe_key=f"agent_rollout:{rollout_id}",
            )
            await session.commit()
            return "failed"
        completed = sum(1 for x in runs if x.state == "succeeded")
        skipped: List[Dict[str, Any]] = list(r.skipped or [])
        done = {x.node_id for x in runs} | {
            uuid.UUID(str(s["node_id"])) for s in skipped if s.get("node_id")
        }
        cluster = await session.get(Clusters, r.cluster_id)
        policy = policy_from_config(cluster.config if cluster is not None else None)
        if r.trigger == "auto" and not auto_allowed(policy):
            await rollouts.patch(
                rollout_id,
                completed_count=completed,
                message="waiting for the maintenance window",
            )
            await session.commit()
            return "running"
        for nid in r.node_ids or []:
            if nid in done:
                continue
            node = await nodes.get_active(nid)
            reason = skip_reason(node, r.target_version)
            if reason is not None:
                skipped.append(
                    {
                        "node_id": str(nid),
                        "node_name": node.name if node else None,
                        "reason": reason,
                    }
                )
                continue
            if await runs_repo.active_for_node(nid) is not None:
                skipped.append(
                    {
                        "node_id": str(nid),
                        "node_name": node.name,
                        "reason": "another update is running",
                    }
                )
                continue
            run = await create_run(
                session,
                node,
                r.target_version,
                trigger="rollout",
                actor_id=r.created_by,
                rollout_id=rollout_id,
            )
            await rollouts.patch(
                rollout_id,
                current_node_id=nid,
                completed_count=completed,
                skipped=skipped,
                message=(
                    f"updating {node.name} "
                    f"({completed + 1 + len(skipped)}/{len(r.node_ids or [])})"
                ),
            )
            next_run = run.id
            break
        else:
            await rollouts.patch(
                rollout_id,
                state="succeeded",
                current_node_id=None,
                completed_count=completed,
                skipped=skipped,
                message=(
                    f"{completed} node(s) updated to {r.target_version}, " f"{len(skipped)} skipped"
                ),
            )
            await events.audit(
                "cluster.agent_rollout_succeeded",
                actor_type="system",
                resource_type="cluster",
                resource_id=r.cluster_id,
                message=f"{completed} node(s) updated to {r.target_version}",
                after_state={"rollout_id": str(rollout_id), "skipped": skipped},
            )
        await session.commit()
    if next_run is not None:
        await advance_run(next_run)
        return "running"
    return "succeeded"


async def cancel_rollout(session, rollout_id: uuid.UUID, actor_id: Optional[uuid.UUID]) -> bool:
    """Stop starting nodes; a node already swapping finishes (it cannot be stopped halfway)."""
    rollouts = AgentRolloutRepository(session)
    r = await rollouts.get_by_id(rollout_id)
    if r is None or r.state != "running":
        return False
    await rollouts.patch(rollout_id, state="cancelled", message="cancelled by an admin")
    runs, _ = await AgentUpdateRunRepository(session).list(1, 1000, rollout_id=rollout_id)
    for run in runs:
        if run.state in ("pending", "staging", "staged"):
            await _finish(session, run, "cancelled", "cancelled", "rolling update cancelled")
    await FleetEventRepository(session).audit(
        "cluster.agent_rollout_cancelled",
        actor_type="admin",
        actor_id=actor_id,
        resource_type="cluster",
        resource_id=r.cluster_id,
        after_state={"rollout_id": str(rollout_id)},
    )
    return True


# ------------------------------------------------------------------ periodic work


async def _auto_rollouts(target: str) -> int:
    started: List[uuid.UUID] = []
    async with get_session_maker()() as session:
        clusters = await ClusterRepository(session).list_all_active()
        rollouts = AgentRolloutRepository(session)
        for cluster in clusters:
            policy = policy_from_config(cluster.config)
            if not auto_allowed(policy):
                continue
            if await rollouts.running(cluster.id):
                continue
            if await rollouts.failed_recently(cluster.id, target, utcnow() - timedelta(days=1)):
                continue
            nodes = await NodeRepository(session).list_active(cluster.id)
            ready = [
                n
                for n in nodes
                if skip_reason(n, target) is None
                and (
                    not settings.AGENT_UPDATE_PRESTAGE
                    or st.reported(n).get("staged_version") == target
                )
            ]
            if not ready:
                continue
            r = await start_rollout(session, cluster.id, target, trigger="auto")
            started.append(r.id)
            logger.info(f"agent updates: auto rollout to {target} in cluster {cluster.name}")
        await session.commit()
    for rid in started:
        await advance_rollout(rid)
    return len(started)


async def _prestage(target: str) -> int:
    """Stage ``target`` on every node that can update itself (background, bounded)."""
    global _last_prestage
    now = time.monotonic()
    if now - _last_prestage < PRESTAGE_EVERY_SECONDS:
        return 0
    _last_prestage = now
    todo: List[tuple] = []
    async with get_session_maker()() as session:
        commands = AgentCommandRepository(session)
        in_flight = 0
        candidates = []
        cutoff = utcnow() - timedelta(seconds=STAGE_RETRY_AFTER_SECONDS)
        for node in await NodeRepository(session).list_active():
            pending = await commands.pending_for_node(node.id, ("stage_update",))
            in_flight += pending
            if pending or node.status not in st.CONNECTED:
                continue
            if st.node_mode(node) not in ("helper", "self") or not st.needs_update(node, target):
                continue
            rep = st.reported(node)
            if rep.get("staged_version") == target or rep.get("state") in (
                "staging",
                "applying",
            ):
                continue
            failed = (node.metadata_ or {}).get("agent_update_stage_failed") or {}
            if failed.get("version") == target:
                try:
                    at = datetime.fromisoformat(str(failed.get("at")).replace("Z", "+00:00"))
                    if at > cutoff:
                        continue
                except ValueError:
                    pass
            if st.node_target(node):
                candidates.append((node.id, st.node_target(node)))
        await session.commit()
    room = max(0, int(settings.AGENT_UPDATE_STAGE_CONCURRENCY) - in_flight)
    todo = candidates[:room]
    for node_id, triple in todo:
        try:
            await send_stage(node_id, target, triple)
        except Exception as exc:  # noqa: BLE001 - next tick retries
            logger.warning(f"agent updates: pre-staging node {node_id} failed: {exc}")
    if todo:
        logger.info(f"agent updates: pre-staging {target} on {len(todo)} node(s)")
    return len(todo)


async def tick() -> Dict[str, int]:
    """Advance runs (deadlines, stage, apply), rollouts, maintenance windows and pre-staging."""
    out = {"runs": 0, "rollouts": 0, "auto": 0, "prestaged": 0}
    async with get_session_maker()() as session:
        active = [r.id for r in await AgentUpdateRunRepository(session).active()]
        rollouts = [r.id for r in await AgentRolloutRepository(session).running()]
        await session.commit()
    for run_id in active:
        try:
            await advance_run(run_id)
            out["runs"] += 1
        except Exception as exc:  # noqa: BLE001 - one run must not stop the others
            logger.error(f"agent updates: advancing run {run_id} failed: {exc}")
    for rid in rollouts:
        try:
            await advance_rollout(rid)
            out["rollouts"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.error(f"agent updates: advancing rollout {rid} failed: {exc}")
    target, _ = await releases.target_version()
    if target:
        try:
            out["auto"] = await _auto_rollouts(target)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"agent updates: maintenance windows failed: {exc}")
        if settings.AGENT_UPDATE_PRESTAGE:
            try:
                out["prestaged"] = await _prestage(target)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"agent updates: pre-staging failed: {exc}")
    return out


def reset_for_tests() -> None:
    global _last_prestage
    _last_prestage = 0.0
