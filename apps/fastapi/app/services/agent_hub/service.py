"""
Agent hub service.

Two halves:

``HubRuntime`` — lives only in the hub process (``app/agent_hub_main.py``):
owns the connection registry, the ingest, the Prometheus gauges, the
``hub:dispatch`` subscription that turns bus messages from other processes
into frames on the right socket, and a sweeper (re-dispatch of commands whose
bus message was missed, offline detection). Fleet changes that affect serving
(node up/down, inventory, instance events, revocations) trigger the deployments routing
table publisher (``app.services.deployments.routing_publisher``), debounced to
once per second. It applies ``command_result`` frames and publishes them on
``hub:results``.

``AgentHub`` — the API every process uses (admin routes, jobs, deployments, swaps, serving)::

    outcome = await AgentHub.send_command(node_id, "drain_instance", {...}, timeout=120)
    outcome = await AgentHub.send_command(node_id, "load_model", args, wait=False)
    outcome = await AgentHub.wait_for(outcome.command_id, timeout=600)
    await AgentHub.cancel_command(command_id)
    await AgentHub.push_config(node_id, sections=["routing"])   # node_id=None: every node
    await AgentHub.broadcast_crl([fingerprint, ...])             # after a revocation
    await AgentHub.disconnect_node(node_id, 4003, "node removed")
    await AgentHub.request_routing_rebuild()

``send_command`` persists an ``agent_commands`` row (its id is the envelope
id), notifies the hub over Redis and, with ``wait=True``, waits for the
terminal result (Redis ``hub:results`` plus a DB poll as a safety net). An
offline node keeps the row ``pending``; the hub replays it on reconnect with
the same id (protocol.md §3).

``get_agent_hub(session)`` returns ``SessionAgentHub``: the deployments port shape
(``send_command(...) -> command_id``, ``is_connected``) writing the row in the
caller's transaction and notifying the hub after that transaction commits.
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import uuid
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_session_maker
from app.db.postgres.models.generated_models import AgentCommands
from app.db.postgres.repositories.agent_command_repository import (
    REPLAYABLE_STATES,
    TERMINAL_STATES,
    AgentCommandRepository,
)
from app.db.postgres.repositories.node_repository import NodeRepository
from app.services.agent_hub import bus
from app.services.agent_hub.config_builder import ConfigBuilder
from app.services.agent_hub.ingest import HubIngest
from app.services.agent_hub.protocol import (
    CLOSE_HUB_RESTARTING,
    CLOSE_REJECTED,
    ProtocolError,
    validate_payload,
)
from app.services.agent_hub.registry import ConnectionRegistry, registry
from app.services.metrics.live import publish_live_event
from prometheus_client import Counter, Gauge
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

COMMANDS = (
    "load_model",
    "unload_model",
    "drain_instance",
    "start_engine",
    "stop_engine",
    "restart_instance",
    "prefetch_model",
    "evict_cache",
    "run_benchmark",
    "update_config",
    "rotate_cert",
    "install_cert",
    "cancel",
    "scan_path",
    "hash_files",
    "start_ssh_instance",
    "stop_ssh_instance",
    "stage_update",
    "apply_update",
)
SWEEP_TICK_SECONDS = 1.0
MAINTENANCE_EVERY_SECONDS = 5.0
# Key pins / tunnel endpoints are re-checked this often; a change re-pushes the signed policy.
SECURITY_CHECK_EVERY_SECONDS = 60.0
REDISPATCH_AFTER_SECONDS = 2.0


# ==========================================
# Prometheus (hub process)
# ==========================================


class HubMetrics:
    def __init__(self) -> None:
        self.connected_nodes = Gauge("gpudeck_hub_connected_nodes", "Agents connected to this hub")
        self.messages_total = Counter(
            "gpudeck_hub_messages_total", "Protocol frames", ["type", "direction"]
        )
        self.ingest_rows_total = Counter(
            "gpudeck_hub_ingest_rows_total", "Rows written by the hub ingest", ["table"]
        )
        self.ingest_lag_seconds = Gauge(
            "gpudeck_hub_ingest_lag_seconds", "Age of the last ingested metrics batch"
        )
        self.dropped_total = Counter(
            "gpudeck_hub_dropped_frames_total",
            "Frames dropped under ingest pressure",
            ["type"],
        )


_hub_metrics: Optional[HubMetrics] = None


def hub_metrics() -> HubMetrics:
    global _hub_metrics
    if _hub_metrics is None:
        _hub_metrics = HubMetrics()
    return _hub_metrics


# ==========================================
# Hub runtime (hub process only)
# ==========================================


class HubRuntime:
    def __init__(self, registry_: Optional[ConnectionRegistry] = None):
        self.registry = registry_ or registry
        self.ingest = HubIngest(self)
        self.metrics = hub_metrics()
        self.hub_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.draining = False
        self.routing_dirty = True
        # Last routing set pushed to each connected node's proxy (config_push.routing).
        self._node_routing_sigs: Dict[uuid.UUID, str] = {}
        self._dispatch: Optional[bus.Subscription] = None
        self._sweeper: Optional[asyncio.Task] = None
        self._follow_ups: set = set()
        self._security_sig: Optional[str] = None
        self._next_security_check = 0.0

    async def start(self) -> None:
        await bus.clear_presence_for_hub(self.hub_id)
        self._dispatch = bus.Subscription(bus.CH_DISPATCH, self._on_dispatch)
        await self._dispatch.start()
        self._sweeper = asyncio.create_task(self._sweep_loop(), name="hub-sweeper")
        logger.info(f"hub runtime started id={self.hub_id}")

    async def stop(self) -> None:
        self.draining = True
        if self._sweeper is not None:
            self._sweeper.cancel()
            try:
                await self._sweeper
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for conn in self.registry.all():
            await conn.close(CLOSE_HUB_RESTARTING, "hub restarting")
        if self._dispatch is not None:
            await self._dispatch.stop()
        await bus.clear_presence_for_hub(self.hub_id)
        logger.info("hub runtime stopped")

    def mark_routing_dirty(self) -> None:
        self.routing_dirty = True

    # ---------- sweeper ----------

    async def _sweep_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_maintenance = loop.time() + MAINTENANCE_EVERY_SECONDS
        while True:
            try:
                await asyncio.sleep(SWEEP_TICK_SECONDS)
                now = loop.time()
                if self.routing_dirty:
                    await self._refresh_routing()
                if now >= next_maintenance:
                    await self._redispatch_pending()
                    await self._maintenance()
                    next_maintenance = now + MAINTENANCE_EVERY_SECONDS
                if now >= self._next_security_check:
                    self._next_security_check = now + SECURITY_CHECK_EVERY_SECONDS
                    await self._refresh_security()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the sweeper must keep running
                logger.error(f"hub sweeper iteration failed: {exc}")

    async def _refresh_routing(self) -> None:
        """Push changed proxy routing to connected nodes, then republish the API layer table."""
        self.routing_dirty = False
        await self._push_node_routing()
        try:
            from app.services.deployments import routing_publisher
        except (
            Exception
        ):  # noqa: BLE001 - routing publisher not present: its own job refreshes the table
            return
        publish = getattr(routing_publisher, "publish_routing_table", None)
        if publish is None:
            return
        try:
            await publish()
        except Exception as exc:  # noqa: BLE001 - next change / the routing refresh job retries
            logger.warning(f"routing table publish failed: {exc}")

    async def _push_node_routing(self) -> None:
        """
        Instances that became ready / stopped change what each node proxy may
        expose; without a ``config_push.routing`` the proxy answers
        ``instance_not_found``. Only nodes whose routing set changed get a push.
        """
        import hashlib
        import json

        from app.db.postgres.repositories.inventory_repository import (
            InventoryRepository,
        )

        conns = self.registry.all()
        live = {c.node_id for c in conns}
        for node_id in list(self._node_routing_sigs):
            if node_id not in live:
                self._node_routing_sigs.pop(node_id, None)
        for conn in conns:
            try:
                async with get_session_maker()() as session:
                    rows = await InventoryRepository(session).routing_entries_for_node(conn.node_id)
                sig = hashlib.sha256(
                    json.dumps(rows, sort_keys=True, default=str).encode()
                ).hexdigest()
                if self._node_routing_sigs.get(conn.node_id) == sig:
                    continue
                await self._push_config(conn, False, ["routing"])
                self._node_routing_sigs[conn.node_id] = sig
            except Exception as exc:  # noqa: BLE001 - retried on the next change
                logger.warning(f"routing push to node={conn.node_id} failed: {exc}")

    async def _redispatch_pending(self) -> None:
        """Commands whose bus message was missed (publisher crashed, Redis blip)."""
        node_ids = [c.node_id for c in self.registry.all()]
        if not node_ids:
            return
        async with get_session_maker()() as session:
            repo = AgentCommandRepository(session)
            rows = await repo.undispatched_for_nodes(node_ids, REDISPATCH_AFTER_SECONDS)
            for row in rows:
                conn = self.registry.get(row.node_id)
                if conn is not None and await conn.send_command(row):
                    await repo.mark_sent(row.id)
            await session.commit()
        if rows:
            logger.info(f"hub sweeper re-dispatched {len(rows)} pending command(s)")

    async def _refresh_security(self) -> None:
        """Re-push the signed policy to every connected node when a pin or endpoint changed
        (service key re-issued / rotated, settings changed)."""
        from app.services.agent_hub.security_policy import pins_signature

        async with get_session_maker()() as session:
            sig = await pins_signature(session)
            await session.commit()
        if self._security_sig is None or sig == self._security_sig:
            self._security_sig = sig
            return
        self._security_sig = sig
        logger.info("hub: key pins or tunnel endpoints changed; re-pushing security policies")
        for conn in self.registry.all():
            try:
                await self._push_config(conn, False, ["security"])
            except Exception as exc:  # noqa: BLE001 - next check retries
                logger.warning(f"security push to node={conn.node_id} failed: {exc}")

    async def _maintenance(self) -> None:
        from app.services.nodes.maintenance import run_hub_maintenance

        changed = await run_hub_maintenance(connected={str(c.node_id) for c in self.registry.all()})
        if changed:
            self.mark_routing_dirty()

    # ---------- bus -> sockets ----------

    async def _on_dispatch(self, message: Dict[str, Any]) -> None:
        kind = message.get("type")
        node_id = _uuid(message.get("node_id"))
        if kind == "command" and node_id:
            await self._dispatch_command(node_id, _uuid(message.get("command_id")))
        elif kind == "config_push":
            targets = [c for c in self.registry.all() if node_id is None or c.node_id == node_id]
            for conn in targets:
                await self._push_config(conn, bool(message.get("full")), message.get("sections"))
            if message.get("sections") is None or "routing" in (message.get("sections") or []):
                self.mark_routing_dirty()
        elif kind == "disconnect" and node_id:
            conn = self.registry.get(node_id)
            if conn is not None:
                await conn.close(
                    int(message.get("code") or CLOSE_REJECTED),
                    message.get("reason") or "",
                )
            self.mark_routing_dirty()
        elif kind == "crl_updated":
            revoked = set(message.get("fingerprints") or [])
            for conn in self.registry.all():
                if conn.fingerprint in revoked:
                    logger.warning(f"hub: closing node={conn.node_id}: certificate revoked")
                    await conn.close(CLOSE_REJECTED, "cert_revoked")
                else:
                    await self._push_config(conn, False, ["crl"])
            self.mark_routing_dirty()
        elif kind == "drain_state" and node_id:
            conn = self.registry.get(node_id)
            if conn is not None:
                await conn.send_drain_state()
        elif kind == "routing_rebuild":
            self.mark_routing_dirty()

    async def _dispatch_command(self, node_id: uuid.UUID, command_id: Optional[uuid.UUID]) -> None:
        conn = self.registry.get(node_id)
        if conn is None or command_id is None:
            return  # offline: stays pending, replayed on reconnect
        async with get_session_maker()() as session:
            repo = AgentCommandRepository(session)
            row = await repo.get_by_id(command_id)
            if row is None or row.state not in REPLAYABLE_STATES:
                return
            if row.expires_at and row.expires_at < datetime.now(timezone.utc):
                await repo.mark_timed_out(command_id)
                await session.commit()
                row = await repo.get_by_id(command_id)
                await _publish_result(row, "timed_out", error_code="timeout")
                return
            if await conn.send_command(row):
                await repo.mark_sent(command_id)
            await session.commit()

    async def _push_config(self, conn, full: bool, sections: Optional[List[str]]) -> None:
        async with get_session_maker()() as session:
            node = await NodeRepository(session).get_by_id(conn.node_id)
            if node is None:
                return
            payload = await ConfigBuilder(session).build(node, full=full, sections=sections)
            await session.commit()
        await conn.send_config(payload)

    # ---------- socket callbacks ----------

    async def on_connected(self, conn) -> None:
        self.mark_routing_dirty()
        await _agent_update_hook("on_connected", conn.node_id, conn.agent_version)
        # A fresh signed policy on every connect (pins may have changed while it was away; the
        # full push only follows when config versions differ).
        try:
            await self._push_config(conn, False, ["security"])
        except Exception as exc:  # noqa: BLE001 - the periodic check retries
            logger.warning(f"security push to node={conn.node_id} failed: {exc}")
        await publish_live_event(
            "node",
            {
                "node_id": str(conn.node_id),
                "cluster_id": str(conn.cluster_id) if conn.cluster_id else None,
                "status": "online",
                "name": conn.node_name,
                "agent_version": conn.agent_version,
            },
        )

    async def on_disconnected(self, conn) -> None:
        self._node_routing_sigs.pop(conn.node_id, None)
        self.mark_routing_dirty()
        await _agent_update_hook("on_disconnected", conn.node_id)
        await publish_live_event(
            "node",
            {
                "node_id": str(conn.node_id),
                "cluster_id": str(conn.cluster_id) if conn.cluster_id else None,
                "status": "disconnected",
                "name": conn.node_name,
            },
        )

    async def on_command_result(self, conn, frame: Dict[str, Any]) -> None:
        payload = frame.get("payload") or {}
        command_id = _uuid(frame.get("correlation_id"))
        if command_id is None:
            return
        status = payload.get("status")
        error = payload.get("error") or {}
        follow_up = None
        async with get_session_maker()() as session:
            repo = AgentCommandRepository(session)
            existing = await repo.get_by_id(command_id)
            if existing is None or existing.node_id != conn.node_id:
                logger.warning(
                    f"hub: result for unknown command {command_id} from node={conn.node_id}"
                )
                return
            was_terminal = existing.state in TERMINAL_STATES
            row = await repo.apply_result(
                command_id,
                status,
                result=payload.get("result"),
                error_code=error.get("code"),
                error_message=error.get("message"),
                progress_pct=payload.get("progress_pct"),
                finished_at=_dt(payload.get("finished_at")),
            )
            if row.state in TERMINAL_STATES and not was_terminal:
                follow_up = await self._after_terminal(session, conn, row)
            await session.commit()
        await _publish_result(row, row.state)
        if follow_up is not None:
            task = asyncio.create_task(follow_up)
            self._follow_ups.add(task)
            task.add_done_callback(self._follow_ups.discard)

    async def _after_terminal(self, session: AsyncSession, conn, row: AgentCommands):
        """Side effects of a finished command; returns a coroutine to run after commit."""
        if row.command_type in ("start_ssh_instance", "stop_ssh_instance"):
            from app.services.gpu_instances.lifecycle import on_command_terminal

            return on_command_terminal(row.id)
        if row.command_type in ("stage_update", "apply_update"):
            from app.services.agent_updates import orchestrator

            return orchestrator.on_command_terminal(row.id)
        if row.command_type in (
            "load_model",
            "unload_model",
            "drain_instance",
            "restart_instance",
        ):
            self.mark_routing_dirty()
        if row.command_type == "rotate_cert" and row.state == "succeeded":
            csr_pem = (row.result or {}).get("csr_pem")
            if not csr_pem:
                logger.warning(f"hub: rotate_cert on node={conn.node_id} returned no csr_pem")
                return None
            from app.services.pki.service import PkiService

            pki = PkiService.from_session(session)
            cert = await pki.sign_node_csr(conn.node_id, csr_pem)
            ca = await pki.ensure_ca()
            args = {
                "certificate_pem": cert.certificate_pem,
                "ca_bundle_pem": ca.bundle_pem,
                "serial": cert.serial,
                "not_after": cert.not_after,
            }
            return AgentHub.send_command(
                conn.node_id, "install_cert", args, timeout=120, wait=False
            )
        if row.command_type in ("unload_model", "drain_instance"):
            from app.services.nodes.service import check_drain_complete

            if await check_drain_complete(session, conn.node_id):
                self.mark_routing_dirty()
        return None


# ==========================================
# Public API (any process)
# ==========================================


@dataclass
class CommandOutcome:
    command_id: uuid.UUID
    node_id: uuid.UUID
    state: str
    result: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    progress_pct: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.state == "succeeded"

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


class _LoopWaiter:
    """hub:results subscription + pending futures of one event loop."""

    def __init__(self) -> None:
        self.futures: Dict[str, List[asyncio.Future]] = {}
        self.sub: Optional[bus.Subscription] = None
        self.lock = asyncio.Lock()

    async def on_message(self, payload: Dict[str, Any]) -> None:
        if payload.get("state") not in TERMINAL_STATES:
            return
        for fut in self.futures.get(str(payload.get("command_id")), []):
            if not fut.done():
                fut.set_result(payload)


class _ResultWaiter:
    """
    Per-process subscription to hub:results resolving futures by command id — one per event
    loop. The jobs process (Celery threads pool, APScheduler) runs every task on its own
    thread and loop; a subscription, its Redis connection and its futures belong to the loop
    that created them and are never touched from another one (that used to stop a running
    task's subscription from a foreign thread and strand its waiters).
    """

    def __init__(self) -> None:
        self._loops: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopWaiter]" = (
            weakref.WeakKeyDictionary()
        )
        self._guard = threading.Lock()

    def _state(self) -> _LoopWaiter:
        loop = asyncio.get_running_loop()
        with self._guard:
            for old in [lp for lp in list(self._loops.keys()) if lp.is_closed()]:
                self._loops.pop(old, None)  # finished loop: its subscription died with it
            state = self._loops.get(loop)
            if state is None:
                state = self._loops[loop] = _LoopWaiter()
            return state

    async def ensure(self) -> None:
        state = self._state()
        if state.sub is not None:
            return
        async with state.lock:
            if state.sub is None:
                sub = bus.Subscription(bus.CH_RESULTS, state.on_message)
                await sub.start()
                state.sub = sub

    def register(self, command_id: uuid.UUID) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._state().futures.setdefault(str(command_id), []).append(fut)
        return fut

    def forget(self, command_id: uuid.UUID, fut: asyncio.Future) -> None:
        futures = self._state().futures
        pending = futures.get(str(command_id), [])
        if fut in pending:
            pending.remove(fut)
        if not pending:
            futures.pop(str(command_id), None)


_waiter = _ResultWaiter()


def build_command_payload(
    command: str,
    args: Dict[str, Any],
    timeout_seconds: int,
    swap_operation_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """``command`` payload checked against ``command.json`` before it is persisted."""
    if command not in COMMANDS:
        raise ValidationError(f"unknown agent command {command}")
    from app.services.agent_hub.protocol import dumps, loads

    payload = loads(
        dumps(
            {
                "command": command,
                "args": args or {},
                "timeout_seconds": int(timeout_seconds),
                "swap_operation_id": (str(swap_operation_id) if swap_operation_id else None),
            }
        )
    )
    try:
        validate_payload("command", payload)
    except ProtocolError as exc:
        raise ValidationError(f"invalid {command} args: {exc}") from exc
    return payload


async def _ensure_targets_managed(
    session: AsyncSession, instance_id: Optional[uuid.UUID], args: Dict[str, Any]
) -> None:
    """Never dispatch a command for an external (observed) instance or deployment."""
    from app.services.deployments.external import ensure_command_targets_managed

    await ensure_command_targets_managed(session, instance_id, args)


class AgentHub:
    """Static facade; no sockets here — everything goes through the Redis bus."""

    @staticmethod
    async def send_command(
        node_id: uuid.UUID,
        command: str,
        args: Dict[str, Any],
        timeout: Optional[int] = None,
        swap_operation_id: Optional[uuid.UUID] = None,
        instance_id: Optional[uuid.UUID] = None,
        wait: bool = True,
        grace_seconds: int = 5,
    ) -> CommandOutcome:
        timeout = int(timeout or settings.COMMAND_DEFAULT_TIMEOUT_SECONDS)
        payload = build_command_payload(command, args, timeout, swap_operation_id)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=timeout)
        command_id = uuid.uuid4()
        async with get_session_maker()() as session:
            await _ensure_targets_managed(session, instance_id, args)
            node = await NodeRepository(session).get_active(node_id)
            if node is None or node.status == "removed":
                raise ResourceNotFoundError(resource_type="Node")
            unsupported = _unsupported_reason(node.agent_version, command)
            await AgentCommandRepository(session).create(
                id=command_id,
                node_id=node_id,
                command_type=command,
                payload=payload,
                state="pending" if unsupported is None else "rejected",
                swap_operation_id=swap_operation_id,
                instance_id=instance_id,
                expires_at=expires_at,
                error_code=None if unsupported is None else "unsupported_by_agent",
                error_message=unsupported,
                completed_at=(None if unsupported is None else datetime.now(timezone.utc)),
            )
            await session.commit()
        if unsupported is not None:
            return CommandOutcome(
                command_id,
                node_id,
                "rejected",
                error_code="unsupported_by_agent",
                error_message=unsupported,
            )
        fut = None
        if wait:
            await _waiter.ensure()
            fut = _waiter.register(command_id)
        await _notify_dispatch(node_id, command_id)
        if fut is None:
            return CommandOutcome(command_id, node_id, "pending")
        try:
            return await _wait_for_result(command_id, node_id, fut, expires_at, grace_seconds)
        finally:
            _waiter.forget(command_id, fut)

    @staticmethod
    async def wait_for(command_id: uuid.UUID, timeout: float) -> CommandOutcome:
        """Wait up to ``timeout`` s for a terminal state (else return the current one)."""
        await _waiter.ensure()
        fut = _waiter.register(command_id)
        try:
            current = await AgentHub.get_command(command_id)
            if current is None:
                raise ResourceNotFoundError(resource_type="Agent command")
            if current.terminal:
                return current
            deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout))
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return await AgentHub.get_command(command_id) or current
                try:
                    payload = await asyncio.wait_for(
                        asyncio.shield(fut), timeout=min(2.0, remaining)
                    )
                    return _outcome_from_payload(payload)
                except asyncio.TimeoutError:
                    outcome = await AgentHub.get_command(command_id)
                    if outcome is not None and outcome.terminal:
                        return outcome
        finally:
            _waiter.forget(command_id, fut)

    @staticmethod
    async def cancel_command(command_id: uuid.UUID, reason: str = "cancelled") -> bool:
        """
        Not yet accepted -> row cancelled locally (the agent dedups if it already got it).
        Accepted/running -> a ``cancel`` command targets it; the agent ends it with
        ``failed(code=cancelled)``. Returns False when the command was already terminal.
        """
        async with get_session_maker()() as session:
            repo = AgentCommandRepository(session)
            row = await repo.get_by_id(command_id)
            if row is None:
                raise ResourceNotFoundError(resource_type="Agent command")
            if row.state in TERMINAL_STATES:
                return False
            if row.state == "pending" and await repo.mark_cancelled(command_id, reason):
                await session.commit()
                row = await repo.get_by_id(command_id)
                await _publish_result(row, "cancelled")
                return True
            node_id = row.node_id
        await AgentHub.send_command(
            node_id,
            "cancel",
            {"target_command_id": str(command_id)},
            timeout=60,
            wait=False,
        )
        return True

    @staticmethod
    async def get_command(command_id: uuid.UUID) -> Optional[CommandOutcome]:
        async with get_session_maker()() as session:
            row = await AgentCommandRepository(session).get_by_id(command_id)
            return _outcome_from_row(row) if row else None

    @staticmethod
    async def push_config(
        node_id: Optional[uuid.UUID] = None,
        full: bool = False,
        sections: Optional[List[str]] = None,
    ) -> None:
        await bus.publish(
            bus.CH_DISPATCH,
            {
                "type": "config_push",
                "node_id": node_id,
                "full": full,
                "sections": sections,
            },
        )

    @staticmethod
    async def broadcast_crl(revoked_fingerprints: List[str]) -> None:
        await bus.publish(
            bus.CH_DISPATCH,
            {"type": "crl_updated", "fingerprints": revoked_fingerprints},
        )

    @staticmethod
    async def disconnect_node(
        node_id: uuid.UUID, code: int = CLOSE_REJECTED, reason: str = ""
    ) -> None:
        await bus.publish(
            bus.CH_DISPATCH,
            {"type": "disconnect", "node_id": node_id, "code": code, "reason": reason},
        )

    @staticmethod
    async def push_drain_state(node_id: uuid.UUID) -> None:
        """Tell a connected agent that its node was drained / undrained (hello_ack.drain)."""
        await bus.publish(bus.CH_DISPATCH, {"type": "drain_state", "node_id": node_id})

    @staticmethod
    async def request_routing_rebuild() -> None:
        await bus.publish(bus.CH_DISPATCH, {"type": "routing_rebuild"})

    @staticmethod
    async def connected_nodes() -> Dict[str, Dict[str, Any]]:
        return await bus.connected_nodes()

    @staticmethod
    async def is_connected(node_id: uuid.UUID) -> bool:
        try:
            return str(node_id) in await bus.connected_nodes()
        except Exception:  # noqa: BLE001 - Redis down: fall back to the DB view
            async with get_session_maker()() as session:
                node = await NodeRepository(session).get_by_id(node_id)
            return node is not None and node.status in ("online", "draining", "drained")


class SessionAgentHub:
    """
    the deployments ``AgentHubPort`` (``app/services/deployments/agent_hub_port.py``):
    the row is written in the caller's session (same transaction as the
    instance it refers to) and the hub is notified after that transaction
    commits. The hub sweeper re-dispatches anything whose notification was lost.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        if not session.info.get("_hub_hooks"):
            session.info["_hub_hooks"] = True
            session.info["_hub_pending"] = []
            sa_event.listen(session.sync_session, "after_commit", _after_commit)
            sa_event.listen(session.sync_session, "after_rollback", _after_rollback)

    async def send_command(
        self,
        node_id: uuid.UUID,
        command: str,
        args: Dict[str, Any],
        *,
        instance_id: Optional[uuid.UUID] = None,
        swap_operation_id: Optional[uuid.UUID] = None,
        timeout_seconds: Optional[int] = None,
    ) -> uuid.UUID:
        timeout = int(timeout_seconds or settings.COMMAND_DEFAULT_TIMEOUT_SECONDS)
        payload = build_command_payload(command, args, timeout, swap_operation_id)
        await _ensure_targets_managed(self.session, instance_id, args)
        node = await NodeRepository(self.session).get_by_id(node_id)
        if node is None or node.status == "removed":
            raise ResourceNotFoundError(resource_type="Node")
        unsupported = _unsupported_reason(node.agent_version, command)
        row = AgentCommands(
            id=uuid.uuid4(),
            node_id=node_id,
            command_type=command,
            payload=payload,
            state="pending" if unsupported is None else "rejected",
            attempts=0,
            swap_operation_id=swap_operation_id,
            instance_id=instance_id,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=timeout),
            error_code=None if unsupported is None else "unsupported_by_agent",
            error_message=unsupported,
            completed_at=None if unsupported is None else datetime.now(timezone.utc),
        )
        self.session.add(row)
        await self.session.flush()
        if unsupported is None:
            self.session.info["_hub_pending"].append((node_id, row.id))
        return row.id

    async def is_connected(self, node_id: uuid.UUID) -> bool:
        return await AgentHub.is_connected(node_id)

    async def wait(self, command_id: uuid.UUID, timeout_seconds: float) -> CommandOutcome:
        return await AgentHub.wait_for(command_id, timeout_seconds)

    async def cancel(self, command_id: uuid.UUID) -> bool:
        return await AgentHub.cancel_command(command_id)


def get_agent_hub(session: AsyncSession) -> SessionAgentHub:
    """Factory the deployments services resolve (``agent_hub_port.get_agent_hub``)."""
    return SessionAgentHub(session)


def _after_commit(sync_session) -> None:
    pending = sync_session.info.get("_hub_pending") or []
    if not pending:
        return
    sync_session.info["_hub_pending"] = []
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (sync use): the hub sweeper re-dispatches within seconds
    for node_id, command_id in pending:
        loop.create_task(_notify_dispatch(node_id, command_id))


def _after_rollback(sync_session) -> None:
    sync_session.info["_hub_pending"] = []


async def _notify_dispatch(node_id: uuid.UUID, command_id: uuid.UUID) -> None:
    try:
        await bus.publish(
            bus.CH_DISPATCH,
            {"type": "command", "node_id": node_id, "command_id": command_id},
        )
    except Exception as exc:  # noqa: BLE001 - the sweeper re-dispatches pending rows
        logger.warning(f"hub dispatch publish failed command={command_id}: {exc}")


async def _wait_for_result(
    command_id: uuid.UUID,
    node_id: uuid.UUID,
    fut: asyncio.Future,
    expires_at: datetime,
    grace: int,
) -> CommandOutcome:
    deadline = expires_at + timedelta(seconds=grace)
    while True:
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            break
        try:
            payload = await asyncio.wait_for(asyncio.shield(fut), timeout=min(2.0, remaining))
            return _outcome_from_payload(payload)
        except asyncio.TimeoutError:
            outcome = await AgentHub.get_command(command_id)
            if outcome is not None and outcome.terminal:
                return outcome
    async with get_session_maker()() as session:
        repo = AgentCommandRepository(session)
        await repo.mark_timed_out(command_id)
        await session.commit()
        row = await repo.get_by_id(command_id)
    if row is None:
        return CommandOutcome(command_id, node_id, "timed_out")
    await _publish_result(row, row.state)
    return _outcome_from_row(row)


def _unsupported_reason(agent_version: Optional[str], command: str) -> Optional[str]:
    minimum = settings.get_command_min_agent_versions().get(command)
    if not minimum:
        return None
    version = agent_version or "0.0.0"
    if _semver(version) < _semver(minimum):
        return f"{command} requires agent >= {minimum}, node runs {version}"
    return None


def _semver(value: str):
    parts = []
    for piece in str(value).split("-")[0].split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


async def _publish_result(
    row: Optional[AgentCommands], state: str, error_code: Optional[str] = None
) -> None:
    if row is None:
        return
    try:
        await bus.publish(
            bus.CH_RESULTS,
            {
                "command_id": row.id,
                "node_id": row.node_id,
                "command": row.command_type,
                "state": state,
                "result": row.result,
                "error_code": error_code or row.error_code,
                "error_message": row.error_message,
                "progress_pct": row.progress_pct,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"hub: result publish failed command={row.id}: {exc}")


def _outcome_from_row(row: AgentCommands) -> CommandOutcome:
    return CommandOutcome(
        row.id,
        row.node_id,
        row.state,
        row.result,
        row.error_code,
        row.error_message,
        row.progress_pct,
    )


def _outcome_from_payload(payload: Dict[str, Any]) -> CommandOutcome:
    return CommandOutcome(
        uuid.UUID(str(payload["command_id"])),
        uuid.UUID(str(payload["node_id"])),
        payload["state"],
        payload.get("result"),
        payload.get("error_code"),
        payload.get("error_message"),
        payload.get("progress_pct"),
        raw=payload,
    )


async def _agent_update_hook(name: str, *args: Any) -> None:
    """Agent updates follow connects / disconnects (restart, re-adoption); never raises."""
    try:
        from app.services.agent_updates import orchestrator

        await getattr(orchestrator, name)(*args)
    except Exception as exc:  # noqa: BLE001 - the socket lifecycle must go on
        logger.warning(f"hub: agent update {name} failed: {exc}")


def _uuid(value: Any) -> Optional[uuid.UUID]:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
