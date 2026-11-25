"""
Control plane (agent hub) -> agent round-trip time.

The hub already sends a WebSocket ping to every agent each ``HUB_PING_INTERVAL_SECONDS``
(uvicorn keep-alive) and every agent answers with a pong; that is part of RFC 6455, so it
works with every agent version and needs no protocol change. ``MTLSWebSocketProtocol``
reports each matched pong here: the RTT goes into an EWMA + jitter per node
(``app.services.latency.stats``) and is published to ``gpu-deck:latency:hub`` for the admin
API and the per-minute history. The node is identified by the client certificate's CN (the
same identity the hub checks on every frame).

The value includes the hub's own event-loop scheduling delay, so on a very busy hub it reads
a little high; the API layer's RTT (measured over the serving path) is the one routing uses.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Dict, Optional, Set

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.latency.stats import RttStats

logger = get_logger(__name__)

_stats: Dict[str, RttStats] = {}
_tasks: Set[asyncio.Task] = set()


def node_id_from_der(der: Optional[bytes]) -> Optional[str]:
    """The node id (certificate CN, a UUID) of an agent client certificate."""
    if not der:
        return None
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID

        cert = x509.load_der_x509_certificate(der)
        attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if not attrs:
            return None
        return str(uuid.UUID(str(attrs[0].value)))
    except Exception:  # noqa: BLE001 - not a node certificate (join, tests)
        return None


def stats(node_id: str) -> Optional[RttStats]:
    return _stats.get(node_id)


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def record(node_id: str, rtt_ms: float) -> None:
    """One ping / pong round trip of a connected agent (called from the protocol)."""
    entry = _stats.get(node_id)
    if entry is None:
        entry = _stats[node_id] = RttStats(settings.SERVING_RTT_EWMA_ALPHA)
    entry.add(rtt_ms)
    from app.services.latency.store import publish_hub

    _spawn(publish_hub(node_id, entry.snapshot()))


def forget(node_id: str) -> None:
    """The agent's connection closed: drop its RTT (a reconnect starts fresh)."""
    if _stats.pop(node_id, None) is None:
        return
    from app.services.latency.store import drop_hub

    _spawn(drop_hub(node_id))
