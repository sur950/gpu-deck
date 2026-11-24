"""
Thin interface between the deployments services (deployments, model cache, benchmarks) and the agent
hub, coded against ``libs/contracts/agent-protocol/protocol.md`` §3–4.

``AgentHubPort.send_command(...)`` must:

1. insert an ``agent_commands`` row (``state=pending``) whose ``id`` is the
   envelope id / idempotency key of the command;
2. push the ``command`` envelope to the connected agent (``state=sent``);
3. return the command id. Results arrive as ``command_result`` and update the row.

Resolution order at runtime (``get_agent_hub``):

* ``set_agent_hub_override(hub)`` when a test or tool installed one;
* ``app.services.agent_hub.service.get_agent_hub(session)``
  (``SessionAgentHub``: the row is written in the caller's transaction and
  dispatched after commit), else its static ``AgentHub`` through
  ``B2AgentHubAdapter`` (``wait=False``); outcomes are read back from
  ``agent_commands`` by the reconciler;
* otherwise ``DbQueueAgentHub``: writes the ``agent_commands`` row only. The
  protocol makes this correct by construction — every ``pending``/``sent`` row
  is replayed to the agent when it (re)connects — the command is just not
  pushed immediately.

Tests inject ``FakeAgentHub`` (records calls, can auto-complete commands).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from app.core.logger import get_logger
from app.core.settings import settings
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
)


def default_timeout_seconds() -> int:
    return int(getattr(settings, "COMMAND_DEFAULT_TIMEOUT_SECONDS", 900))


@runtime_checkable
class AgentHubPort(Protocol):
    """What the deployments services need from the hub."""

    async def send_command(
        self,
        node_id: uuid.UUID,
        command: str,
        args: Dict[str, Any],
        *,
        instance_id: Optional[uuid.UUID] = None,
        swap_operation_id: Optional[uuid.UUID] = None,
        timeout_seconds: Optional[int] = None,
    ) -> uuid.UUID: ...

    async def is_connected(self, node_id: uuid.UUID) -> bool: ...


@dataclass
class SentCommand:
    id: uuid.UUID
    node_id: uuid.UUID
    command: str
    args: Dict[str, Any]
    instance_id: Optional[uuid.UUID] = None
    swap_operation_id: Optional[uuid.UUID] = None
    timeout_seconds: int = 900


@dataclass
class FakeAgentHub:
    """In-memory hub for tests: records every command; ``connected`` controls ``is_connected``."""

    sent: List[SentCommand] = field(default_factory=list)
    connected: bool = True
    fail_with: Optional[Exception] = None

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
        if self.fail_with is not None:
            raise self.fail_with
        if command not in COMMANDS:
            raise ValueError(f"unknown command {command}")
        cmd = SentCommand(
            id=uuid.uuid4(),
            node_id=node_id,
            command=command,
            args=args,
            instance_id=instance_id,
            swap_operation_id=swap_operation_id,
            timeout_seconds=timeout_seconds or default_timeout_seconds(),
        )
        self.sent.append(cmd)
        return cmd.id

    async def is_connected(self, node_id: uuid.UUID) -> bool:
        return self.connected

    def of(self, command: str) -> List[SentCommand]:
        return [c for c in self.sent if c.command == command]


class DbQueueAgentHub:
    """Fallback: persist the command row; the hub replays pending rows on (re)connect."""

    def __init__(self, session: AsyncSession):
        self.session = session

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
        from app.db.postgres.models.generated_models import AgentCommands

        if command not in COMMANDS:
            raise ValueError(f"unknown command {command}")
        timeout = timeout_seconds or default_timeout_seconds()
        row = AgentCommands(
            id=uuid.uuid4(),
            node_id=node_id,
            command_type=command,
            payload={"command": command, "args": args, "timeout_seconds": timeout},
            state="pending",
            attempts=0,
            swap_operation_id=swap_operation_id,
            instance_id=instance_id,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=timeout),
        )
        self.session.add(row)
        await self.session.flush()
        logger.info(f"agent command queued (db only) id={row.id} node={node_id} type={command}")
        return row.id

    async def is_connected(self, node_id: uuid.UUID) -> bool:
        from app.db.postgres.models.generated_models import Nodes
        from sqlalchemy import select

        result = await self.session.execute(select(Nodes.status).where(Nodes.id == node_id))
        status = result.scalar_one_or_none()
        return status == "online"


class B2AgentHubAdapter:
    """
    Adapts the static ``AgentHub.send_command(node_id, command, args, timeout=,
    swap_operation_id=, instance_id=, wait=)`` (returns a ``CommandOutcome``)
    to this port (returns the command id, never waits). The hub writes the
    ``agent_commands`` row in its own session, so callers commit their own
    rows first (``repo.release_connection()``) — the row references
    ``instances.id``.
    """

    def __init__(self, hub_cls: Any):
        self._hub = hub_cls

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
        if command not in COMMANDS:
            raise ValueError(f"unknown command {command}")
        outcome = await self._hub.send_command(
            node_id,
            command,
            args,
            timeout=timeout_seconds or default_timeout_seconds(),
            swap_operation_id=swap_operation_id,
            instance_id=instance_id,
            wait=False,
        )
        if getattr(outcome, "state", None) == "rejected":
            logger.warning(
                f"agent rejected {command} node={node_id}: {getattr(outcome, 'error_message', '')}"
            )
        return outcome.command_id

    async def is_connected(self, node_id: uuid.UUID) -> bool:
        return bool(await self._hub.is_connected(node_id))


def _resolve_fleet_hub(session: AsyncSession) -> Optional[AgentHubPort]:
    """The session-bound hub (``get_agent_hub(session)``), else its static facade."""
    try:
        from app.services.agent_hub import service as hub_service  # type: ignore
    except Exception:  # noqa: BLE001 - agent hub not importable
        return None
    factory = getattr(hub_service, "get_agent_hub", None)
    if callable(factory):
        try:
            hub = factory(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"get_agent_hub failed ({exc}); using the static facade")
        else:
            if hasattr(hub, "send_command"):
                return hub  # type: ignore[return-value]
    static = getattr(hub_service, "AgentHub", None)
    if static is not None and hasattr(static, "send_command"):
        return B2AgentHubAdapter(static)
    return None


_override: Optional[AgentHubPort] = None


def set_agent_hub_override(hub: Optional[AgentHubPort]) -> None:
    """Tests / tools: force every deployments service to use ``hub``."""
    global _override
    _override = hub


def get_agent_hub(session: AsyncSession) -> AgentHubPort:
    """The agent hub when available, else the DB-queue fallback."""
    if _override is not None:
        return _override
    hub = _resolve_fleet_hub(session)
    if hub is not None:
        return hub
    return DbQueueAgentHub(session)
