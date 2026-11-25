"""
Agent protocol wire helpers: JSON Schema validation of every inbound
frame against ``libs/contracts/agent-protocol/*.json`` (the contract shared
with the Rust agent), envelope construction for outbound frames and a JSON
encoder that understands UUIDs and datetimes.

Close codes (protocol.md §1): 4001 identity mismatch, 4002 protocol error,
4003 rejected, 4004 superseded, 1012 hub restarting.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Union

from app.core.logger import get_logger
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

logger = get_logger(__name__)

CLOSE_IDENTITY_MISMATCH = 4001
CLOSE_PROTOCOL_ERROR = 4002
CLOSE_REJECTED = 4003
CLOSE_SUPERSEDED = 4004
CLOSE_HUB_RESTARTING = 1012

PROTOCOL_VERSION = 1

# Optional protocol features this hub accepts (hello_ack.features): agents send the matching
# fields (observed engines, GPU processes, serving mode + ingress_rejected events, agent update
# status + agent_update events) only when listed.
HUB_FEATURES = ["observed_engines", "gpu_processes", "secure_transport", "agent_updates"]


def _contract_dir() -> Path:
    """``$GPU_DECK_CONTRACTS_DIR/agent-protocol``, else ``<repo>/libs/contracts/agent-protocol``."""
    override = os.environ.get("GPU_DECK_CONTRACTS_DIR")
    if override:
        return Path(override) / "agent-protocol"
    # apps/fastapi/app/services/agent_hub/protocol.py -> repo root is parents[5]
    parents = Path(__file__).resolve().parents
    root = parents[5] if len(parents) > 5 else parents[-1]
    return root / "libs" / "contracts" / "agent-protocol"


CONTRACT_DIR = _contract_dir()


class ProtocolError(Exception):
    """Frame failed envelope / payload validation."""

    def __init__(self, message: str, frame_id: Optional[str] = None):
        super().__init__(message)
        self.frame_id = frame_id


@lru_cache(maxsize=1)
def _validators() -> Dict[str, Draft202012Validator]:
    registry = Registry()
    schemas: Dict[str, Dict[str, Any]] = {}
    for path in sorted(CONTRACT_DIR.glob("*.json")):
        schema = json.loads(path.read_text())
        schemas[path.stem] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        # relative refs like "common.json#/$defs/uuid" resolve against the $id base
    validators = {
        name: Draft202012Validator(schema, registry=registry) for name, schema in schemas.items()
    }
    logger.info(f"agent protocol: loaded {len(validators)} schemas from {CONTRACT_DIR}")
    return validators


def validate_envelope(frame: Dict[str, Any]) -> None:
    """Raise ProtocolError when the frame violates envelope.json (payload dispatched by type)."""
    validator = _validators()["envelope"]
    error = next(iter(validator.iter_errors(frame)), None)
    if error is not None:
        path = "/".join(str(p) for p in error.absolute_path) or "$"
        raise ProtocolError(f"{path}: {error.message}"[:400], frame.get("id"))


def validate_payload(message_type: str, payload: Dict[str, Any]) -> None:
    """Validate an outbound payload (used in tests to keep the hub honest)."""
    validator = _validators()[message_type]
    error = next(iter(validator.iter_errors(payload)), None)
    if error is not None:
        path = "/".join(str(p) for p in error.absolute_path) or "$"
        raise ProtocolError(f"{message_type}/{path}: {error.message}"[:400])


def _json_default(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (
            value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
    if isinstance(value, (set, frozenset)):
        return list(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, separators=(",", ":"))


def loads(text: str) -> Dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ProtocolError("frame is not a JSON object")
    return data


def now_iso() -> str:
    return _json_default(datetime.now(timezone.utc))


def build_envelope(
    message_type: str,
    payload: Dict[str, Any],
    node_id: Union[str, uuid.UUID, None],
    correlation_id: Union[str, uuid.UUID, None] = None,
    frame_id: Union[str, uuid.UUID, None] = None,
) -> Dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "id": str(frame_id or uuid.uuid4()),
        "type": message_type,
        "ts": now_iso(),
        "node_id": str(node_id) if node_id else None,
        "correlation_id": str(correlation_id) if correlation_id else None,
        "payload": payload,
    }
