"""
One agent WebSocket connection — protocol.md §3 lifecycle:

    mTLS + upgrade -> hello -> hello_ack -> config_push (if versions differ)
    -> replay of pending commands -> heartbeat / inventory / metrics / events
    / command_result frames until close.

Identity comes from the client certificate CN (``node_id``); every frame's
``envelope.node_id`` must match (close 4001). Frames are validated against the
contract schemas (close 4002). Rejections at hello close with 4003 after a
``hello_ack(accepted=false)``; a newer connection for the same node closes
the old one with 4004.

Metrics and log ingest run on a per-connection queue so a slow insert never
blocks command results or heartbeats.
"""

from __future__ import annotations

import asyncio
import ipaddress
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_session_maker
from app.db.postgres.models.generated_models import AgentCommands
from app.db.postgres.repositories.agent_command_repository import AgentCommandRepository
from app.db.postgres.repositories.node_certificate_repository import (
    NodeCertificateRepository,
)
from app.db.postgres.repositories.node_repository import NodeRepository
from app.services.agent_hub import bus
from app.services.agent_hub.config_builder import ConfigBuilder
from app.services.agent_hub.protocol import (
    CLOSE_IDENTITY_MISMATCH,
    CLOSE_PROTOCOL_ERROR,
    CLOSE_REJECTED,
    CLOSE_SUPERSEDED,
    HUB_FEATURES,
    PROTOCOL_VERSION,
    ProtocolError,
    build_envelope,
    dumps,
    loads,
    validate_envelope,
)
from app.services.pki import ca as cax
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

if TYPE_CHECKING:  # pragma: no cover
    from app.services.agent_hub.service import HubRuntime

logger = get_logger(__name__)

HANDSHAKE_ACK_WAIT_SECONDS = 10.0

HELLO_TIMEOUT_SECONDS = 30
INGEST_QUEUE_SIZE = 200


def _advertised_proxy_address(
    declared: Optional[str], observed: Optional[str], ips: List[str]
) -> Optional[str]:
    """
    Address other processes use to reach the node proxy: the agent's ``proxy_address`` when
    set, else the address the node joined from. A loopback source (the agent reached the hub
    through a port forward, e.g. Docker Desktop / OrbStack ``host.docker.internal``) is useless
    to anyone else, so the node's first reported interface address replaces it.
    """
    if declared:
        return declared
    try:
        loopback = observed is not None and ipaddress.ip_address(observed).is_loopback
    except ValueError:
        loopback = False
    if loopback and ips:
        return ips[0]
    return observed


def _config_digest(payload: Dict[str, Any]) -> str:
    """Content hash of a full config_push (version/full flags excluded)."""
    import hashlib
    import json

    body = {k: v for k, v in payload.items() if k not in ("config_version", "full")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class AgentConnection:
    def __init__(self, websocket: WebSocket, cert: cax.CertInfo, hub: "HubRuntime"):
        self.websocket = websocket
        self.cert = cert
        self.hub = hub
        self.node_id: uuid.UUID = uuid.UUID(str(cert.common_name))
        self.fingerprint = cert.fingerprint_sha256
        self.node_name: Optional[str] = None
        self.cluster_id: Optional[uuid.UUID] = None
        self.agent_version: Optional[str] = None
        self.connected_at = datetime.now(timezone.utc)
        self.last_heartbeat_at: Optional[datetime] = None
        self.closed = False
        self._send_lock = asyncio.Lock()
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=INGEST_QUEUE_SIZE)
        self._worker: Optional[asyncio.Task] = None
        self.frames_in = 0
        self.frames_out = 0
        # protocol.md: nothing but hello_ack may precede the handshake's end. The
        # connection is registered (visible to dispatch / sweeper) a moment before
        # hello_ack goes out, so config pushes and commands wait for this.
        self.acked = asyncio.Event()

    # ---------- sending ----------

    async def send(self, frame: Dict[str, Any]) -> bool:
        if self.closed or self.websocket.client_state != WebSocketState.CONNECTED:
            return False
        async with self._send_lock:
            try:
                await self.websocket.send_text(dumps(frame))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"hub: send failed node={self.node_id}: {exc}")
                return False
        self.frames_out += 1
        self.hub.metrics.messages_total.labels(type=frame["type"], direction="out").inc()
        return True

    async def send_command(self, row: AgentCommands) -> bool:
        payload = dict(row.payload or {})
        payload.setdefault("command", row.command_type)
        payload.setdefault("args", {})
        if not await self._wait_acked():
            return False
        frame = build_envelope("command", payload, self.node_id, frame_id=row.id)
        return await self.send(frame)

    async def _wait_acked(self) -> bool:
        if self.acked.is_set():
            return True
        try:
            await asyncio.wait_for(self.acked.wait(), timeout=HANDSHAKE_ACK_WAIT_SECONDS)
            return True
        except asyncio.TimeoutError:
            return False

    async def send_config(self, payload: Dict[str, Any]) -> bool:
        if not await self._wait_acked():
            return False
        return await self.send(build_envelope("config_push", payload, self.node_id))

    async def close(self, code: int, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self.websocket.client_state == WebSocketState.CONNECTED:
                await self.websocket.close(code=code, reason=reason[:120])
        except Exception:  # noqa: BLE001
            pass

    # ---------- lifecycle ----------

    async def run(self) -> None:
        await self.websocket.accept()
        try:
            accepted = await self._handshake()
            if not accepted:
                return
            self._worker = asyncio.create_task(self._ingest_worker(), name=f"ingest:{self.node_id}")
            await self._loop()
        except WebSocketDisconnect as exc:
            logger.info(f"hub: node={self.node_id} disconnected code={exc.code}")
        except ProtocolError as exc:
            logger.warning(f"hub: protocol error node={self.node_id}: {exc}")
            await self.close(CLOSE_PROTOCOL_ERROR, f"{exc.frame_id or ''} {exc}".strip())
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"hub: connection crashed node={self.node_id}: {exc}")
            await self.close(1011, "internal error")
        finally:
            await self._teardown()

    async def _recv(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        text = await asyncio.wait_for(self.websocket.receive_text(), timeout=timeout)
        if len(text) > settings.HUB_MAX_FRAME_BYTES:
            raise ProtocolError("frame exceeds HUB_MAX_FRAME_BYTES")
        frame = loads(text)
        if settings.HUB_VALIDATE_SCHEMAS:
            validate_envelope(frame)
        self.frames_in += 1
        self.hub.metrics.messages_total.labels(type=str(frame.get("type")), direction="in").inc()
        if frame.get("node_id") is not None and str(frame["node_id"]) != str(self.node_id):
            await self.close(CLOSE_IDENTITY_MISMATCH, "envelope.node_id does not match certificate")
            raise WebSocketDisconnect(CLOSE_IDENTITY_MISMATCH)
        return frame

    async def _handshake(self) -> bool:
        try:
            frame = await self._recv(timeout=HELLO_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await self.close(CLOSE_PROTOCOL_ERROR, "no hello")
            return False
        if frame["type"] != "hello":
            raise ProtocolError("first frame must be hello", frame.get("id"))
        hello = frame["payload"]
        reason = await self._validate_hello(hello)
        ack_base = self._ack_base()
        if reason is not None:
            await self.send(
                build_envelope(
                    "hello_ack",
                    {
                        **ack_base,
                        "accepted": False,
                        "reason": reason,
                        "config_version": 0,
                    },
                    self.node_id,
                    correlation_id=frame["id"],
                )
            )
            logger.warning(f"hub: rejected node={self.node_id} reason={reason}")
            await self.close(CLOSE_REJECTED, reason)
            return False

        config_version, drain, config_payload = await self._apply_hello(hello)
        previous = self.hub.registry.register(self)
        if previous is not None:
            await previous.close(CLOSE_SUPERSEDED, "superseded by a new connection")
        await bus.set_connected(
            str(self.node_id),
            {
                "hub": self.hub.hub_id,
                "connected_at": self.connected_at,
                "fingerprint": self.fingerprint,
                "agent_version": self.agent_version,
            },
        )
        await self.send(
            build_envelope(
                "hello_ack",
                {
                    **ack_base,
                    "accepted": True,
                    "reason": None,
                    "config_version": config_version,
                    "min_agent_version": None,
                    "drain": drain,
                },
                self.node_id,
                correlation_id=frame["id"],
            )
        )
        self.acked.set()
        if config_payload is not None:
            await self.send_config(config_payload)
        await self._replay_commands()
        self.hub.metrics.connected_nodes.set(len(self.hub.registry))
        await self.hub.on_connected(self)
        logger.info(
            f"hub: node={self.node_id} name={self.node_name} connected "
            f"agent={self.agent_version} fp={self.fingerprint[:12]}"
        )
        return True

    async def _validate_hello(self, hello: Dict[str, Any]) -> Optional[str]:
        """None when accepted, else the hello_ack.reason."""
        if int(hello.get("protocol_version", 0)) != PROTOCOL_VERSION:
            return "protocol_unsupported"
        if self.hub.draining:
            return "hub_draining"
        async with get_session_maker()() as session:
            node = await NodeRepository(session).get_by_id(self.node_id)
            if node is None:
                return "node_unknown"
            if node.status == "removed" or node.deleted_at is not None:
                return "node_removed"
            cert = await NodeCertificateRepository(session).get_by_fingerprint(self.fingerprint)
            if cert is None or cert.node_id != self.node_id or cert.revoked_at is not None:
                return "cert_revoked"
            self.node_name = node.name
            self.cluster_id = node.cluster_id
        return None

    def _ack_base(self) -> Dict[str, Any]:
        return {
            "server_time": datetime.now(timezone.utc),
            "node_id": str(self.node_id),
            "node_name": self.node_name,
            "cluster_id": str(self.cluster_id) if self.cluster_id else None,
            "intervals": {
                "heartbeat_seconds": settings.HEARTBEAT_INTERVAL_SECONDS,
                "metrics_seconds": settings.METRICS_INTERVAL_SECONDS,
                "inventory_seconds": settings.INVENTORY_INTERVAL_SECONDS,
                "command_ack_timeout_seconds": settings.COMMAND_ACK_TIMEOUT_SECONDS,
            },
            "features": list(HUB_FEATURES),
        }

    async def send_drain_state(self) -> bool:
        """
        Re-send ``hello_ack`` with the node's current ``drain`` flag. The flag otherwise only
        reaches the agent at connect time, so a drain / undrain of a connected node never got
        through (an agent that connected while drained kept refusing every load_model after
        an undrain). Agents apply ``intervals`` and ``drain`` from every hello_ack they get.
        """
        if not self.acked.is_set():
            return False
        async with get_session_maker()() as session:
            node = await NodeRepository(session).get_by_id(self.node_id)
        if node is None:
            return False
        drain = node.status in ("draining", "drained")
        sent = await self.send(
            build_envelope(
                "hello_ack",
                {
                    **self._ack_base(),
                    "accepted": True,
                    "reason": None,
                    "config_version": int((node.metadata_ or {}).get("config_version") or 0),
                    "min_agent_version": None,
                    "drain": drain,
                },
                self.node_id,
            )
        )
        logger.info(f"hub: node={self.node_id} drain={drain} pushed")
        return sent

    async def _apply_hello(self, hello: Dict[str, Any]):
        self.agent_version = hello.get("agent_version")
        client_host = self.websocket.client.host if self.websocket.client else None
        proxy = hello.get("proxy") or {}
        cache = hello.get("cache") or {}
        ips = hello.get("ip_addresses") or []
        declared_fp = proxy.get("cert_fingerprint_sha256")
        if declared_fp and declared_fp != self.fingerprint:
            logger.warning(
                f"hub: node={self.node_id} declares cert fingerprint {declared_fp[:12]} "
                f"but presented {self.fingerprint[:12]}"
            )
        async with get_session_maker()() as session:
            nodes = NodeRepository(session)
            node = await nodes.get_by_id(self.node_id)
            assert node is not None
            labels = dict(hello.get("labels") or {})
            labels.update(node.labels or {})  # UI labels win
            metadata_patch: Dict[str, Any] = {
                "boot_id": hello.get("boot_id"),
                "agent_started_at": hello.get("started_at"),
                "cache_used_bytes": cache.get("used_bytes"),
            }
            if node.cert_fingerprint and node.cert_fingerprint != self.fingerprint:
                # Rotation completed: revoke the previous cert after a grace period.
                metadata_patch["pending_cert_revocation"] = {
                    "fingerprint": node.cert_fingerprint,
                    "after": (
                        datetime.now(timezone.utc).timestamp()
                        + settings.NODE_CERT_OLD_REVOKE_AFTER_SECONDS
                    ),
                }
            await nodes.mark_connected(
                self.node_id,
                hostname=hello.get("hostname"),
                ip_address=(ips[0] if ips else client_host) or None,
                proxy_address=_advertised_proxy_address(
                    proxy.get("address"), node.proxy_address or client_host, ips
                ),
                proxy_port=int(proxy.get("port") or node.proxy_port or 9443),
                os=hello.get("os"),
                os_version=hello.get("os_version"),
                arch=hello.get("arch"),
                kernel_version=hello.get("kernel_version"),
                agent_version=self.agent_version,
                protocol_version=int(hello.get("protocol_version", 1)),
                capabilities=hello.get("capabilities") or {},
                cpu_cores=hello.get("cpu_cores"),
                memory_total_bytes=hello.get("memory_total_bytes"),
                cache_dir=cache.get("dir"),
                cache_limit_bytes=cache.get("limit_bytes"),
                cert_fingerprint=self.fingerprint,
                labels=labels,
            )
            await nodes.merge_metadata(self.node_id, metadata_patch)
            await session.flush()
            node = await nodes.get_by_id(self.node_id)
            await session.refresh(node)
            current_version = int((node.metadata_ or {}).get("config_version") or 0)
            drain = node.status in ("draining", "drained")
            builder = ConfigBuilder(session)
            config_payload = None
            # The config also derives from env settings (timeouts, intervals, egress) and
            # from state the agent may have missed while disconnected: compare content, not
            # only the counter, so a hub restart with new settings reaches every agent.
            candidate = await builder.build(node, full=True, bump_version=False)
            digest = _config_digest(candidate)
            stored = (node.metadata_ or {}).get("config_digest")
            if current_version == 0 or stored != digest:
                config_payload = await builder.build(node, full=True, bump_version=True)
                current_version = config_payload["config_version"]
                await nodes.merge_metadata(self.node_id, {"config_digest": digest})
            elif int(hello.get("config_version") or 0) != current_version:
                config_payload = candidate
            await session.commit()
        return current_version, drain, config_payload

    async def _replay_commands(self) -> None:
        async with get_session_maker()() as session:
            repo = AgentCommandRepository(session)
            rows = await repo.replayable_for_node(self.node_id)
            for row in rows:
                if await self.send_command(row):
                    await repo.mark_sent(row.id)
            await session.commit()
        if rows:
            logger.info(f"hub: replayed {len(rows)} command(s) to node={self.node_id}")

    async def _loop(self) -> None:
        while not self.closed:
            frame = await self._recv()
            try:
                await self._dispatch_frame(frame)
            except (WebSocketDisconnect, ProtocolError):
                raise
            except Exception as exc:  # noqa: BLE001 - one bad frame must not drop the agent
                logger.exception(
                    f"hub: handling {frame.get('type')} from node={self.node_id} failed: {exc}"
                )

    async def _dispatch_frame(self, frame: Dict[str, Any]) -> None:
        kind = frame["type"]
        payload = frame.get("payload") or {}
        if kind == "heartbeat":
            self.last_heartbeat_at = datetime.now(timezone.utc)
            await self.hub.ingest.heartbeat(self.node_id, payload)
        elif kind == "inventory":
            await self.hub.ingest.inventory(self.node_id, payload)
        elif kind == "metrics_batch":
            self._enqueue(("metrics", frame))
        elif kind == "event":
            if payload.get("kind") == "log":
                self._enqueue(("event", frame))
            else:
                await self.hub.ingest.event(self.node_id, frame["id"], payload)
        elif kind == "command_result":
            await self.hub.on_command_result(self, frame)
        elif kind == "hello":
            raise ProtocolError("duplicate hello", frame.get("id"))
        else:
            raise ProtocolError(f"unexpected frame type {kind} from agent", frame.get("id"))

    def _enqueue(self, item) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            # metrics_batch / log events are fire-and-forget by contract: drop under pressure
            self.hub.metrics.dropped_total.labels(type=item[0]).inc()

    async def _ingest_worker(self) -> None:
        while True:
            kind, frame = await self._queue.get()
            try:
                if kind == "metrics":
                    counts = await self.hub.ingest.metrics_batch(self.node_id, frame["payload"])
                    for table, n in counts.items():
                        self.hub.metrics.ingest_rows_total.labels(table=table).inc(n)
                    lag = (datetime.now(timezone.utc) - _frame_ts(frame)).total_seconds()
                    self.hub.metrics.ingest_lag_seconds.set(max(0.0, lag))
                else:
                    await self.hub.ingest.event(self.node_id, frame["id"], frame["payload"])
            except Exception as exc:  # noqa: BLE001
                logger.error(f"hub: ingest {kind} failed node={self.node_id}: {exc}")
            finally:
                self._queue.task_done()

    async def _teardown(self) -> None:
        self.closed = True
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self.hub.registry.unregister(self):
            await bus.set_disconnected(str(self.node_id))
            self.hub.metrics.connected_nodes.set(len(self.hub.registry))
            try:
                async with get_session_maker()() as session:
                    await NodeRepository(session).mark_disconnected(self.node_id, "socket closed")
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"hub: could not record disconnect node={self.node_id}: {exc}")
            await self.hub.on_disconnected(self)


def _frame_ts(frame: Dict[str, Any]) -> datetime:
    try:
        dt = datetime.fromisoformat(str(frame.get("ts")).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)
