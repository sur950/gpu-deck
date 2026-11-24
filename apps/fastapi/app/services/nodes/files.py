"""
Node-side file commands: ``scan_path`` (model candidates under a path
on the node, Ollama / LM Studio / HF cache layouts recognised) and
``hash_files`` (sha256 with streamed progress). Both are read-only on the node.

Used by ``POST /nodes/{id}/scan`` / ``POST /nodes/{id}/hash`` and by the
catalog (``LocalScanRequest.node_id``, node-local model versions).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from app.core.exceptions import ConflictError, ResourceNotFoundError
from app.core.logger import get_logger
from app.schemas.fleet_schema import NodeFileCommandResult

logger = get_logger(__name__)

TIMEOUT_SECONDS = {"scan_path": 600, "hash_files": 6 * 3600}


def supports(capabilities: Optional[Dict[str, Any]], command: str) -> bool:
    """``hello.capabilities.commands`` lists an agent's commands (absent = a pre-1.0 build)."""
    commands = (capabilities or {}).get("commands")
    return isinstance(commands, list) and command in commands


async def _ensure_node(node_id: uuid.UUID, command: Optional[str] = None) -> None:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.node_repository import NodeRepository

    async with get_session_maker()() as session:
        node = await NodeRepository(session).get_active(node_id)
    if node is None or node.status == "removed":
        raise ResourceNotFoundError(resource_type="Node")
    if node.status in ("pending", "offline"):
        raise ConflictError(f"node is {node.status}")
    if command and not supports(node.capabilities, command):
        raise ConflictError(
            f"the node agent ({node.agent_version or 'unknown version'}) does not support "
            f"{command}; upgrade it to >= 1.0.0"
        )


def outcome_dict(outcome: Any, command: str) -> Dict[str, Any]:
    return NodeFileCommandResult(
        command_id=outcome.command_id,
        command=command,
        node_id=outcome.node_id,
        state=outcome.state,
        progress_pct=outcome.progress_pct,
        result=outcome.result,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
    ).model_dump()


async def run_file_command(
    node_id: uuid.UUID, command: str, args: Dict[str, Any], wait_seconds: float
) -> Dict[str, Any]:
    """Send ``command`` and wait up to ``wait_seconds`` for its result."""
    from app.services.agent_hub.service import AgentHub

    await _ensure_node(node_id, command)
    sent = await AgentHub.send_command(
        node_id, command, args, timeout=TIMEOUT_SECONDS[command], wait=False
    )
    if sent.state == "rejected" or wait_seconds <= 0:
        return outcome_dict(sent, command)
    return outcome_dict(await AgentHub.wait_for(sent.command_id, wait_seconds), command)


def scan_args(
    path: str,
    recursive: bool = True,
    max_depth: int = 4,
    max_entries: int = 20000,
    read_metadata: bool = True,
) -> Dict[str, Any]:
    return {
        "path": path,
        "recursive": bool(recursive),
        "max_depth": int(max_depth),
        "max_entries": int(max_entries),
        "read_metadata": bool(read_metadata),
    }


def hash_args(paths: List[str], root: Optional[str] = None) -> Dict[str, Any]:
    args: Dict[str, Any] = {"paths": list(paths)}
    if root:
        args["root"] = root
    return args
