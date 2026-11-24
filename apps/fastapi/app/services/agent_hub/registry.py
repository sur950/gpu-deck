"""
In-process registry of connected agent sockets. One entry per node;
a new accepted connection for the same node supersedes the previous one
(close 4004, protocol.md §3).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Dict, List, Optional

from app.core.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from app.services.agent_hub.connection import AgentConnection

logger = get_logger(__name__)


class ConnectionRegistry:
    def __init__(self) -> None:
        self._by_node: Dict[uuid.UUID, "AgentConnection"] = {}

    def get(self, node_id: uuid.UUID) -> Optional["AgentConnection"]:
        return self._by_node.get(node_id)

    def all(self) -> List["AgentConnection"]:
        return list(self._by_node.values())

    def register(self, conn: "AgentConnection") -> Optional["AgentConnection"]:
        """Register and return the connection that was superseded, if any."""
        previous = self._by_node.get(conn.node_id)
        self._by_node[conn.node_id] = conn
        return previous if previous is not conn else None

    def unregister(self, conn: "AgentConnection") -> bool:
        if self._by_node.get(conn.node_id) is conn:
            del self._by_node[conn.node_id]
            return True
        return False

    def __len__(self) -> int:
        return len(self._by_node)


registry = ConnectionRegistry()
