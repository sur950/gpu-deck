"""
Serving process lifecycle: warm the routing table (Redis / dev engine),
start the pub/sub refresher, the upstream pool warmer and the in-flight
flusher, close the upstream pools on shutdown.

The table itself is written by the control plane (
``services/agent_hub/routing_table.py``) and patched by the swap manager.
"""

from __future__ import annotations

from typing import Optional

from app.core.lifecycle import on_shutdown, on_startup
from app.core.logger import get_logger
from app.services.serving import metrics as prom
from app.services.serving.inflight import get_inflight_tracker
from app.services.serving.routing import RoutingRefresher, get_routing_table
from app.services.serving.upstream import PoolWarmer, close_upstream_client

logger = get_logger(__name__)

_refresher: Optional[RoutingRefresher] = None
_warmer: Optional[PoolWarmer] = None


@on_startup("serving")
async def warm_routing_table() -> None:
    global _refresher, _warmer
    table = get_routing_table()
    table.on_update(lambda t: prom.routing_version.set(t.version))
    _refresher = RoutingRefresher(table)
    await _refresher.start()
    _warmer = PoolWarmer(table)
    await _warmer.start()
    await get_inflight_tracker().start()
    logger.info(
        f"routing table v{table.version}: {len(table.models)} model(s), "
        f"{table.online_node_count()} node(s)"
    )


@on_shutdown("serving")
async def stop_serving() -> None:
    if _refresher is not None:
        await _refresher.stop()
    if _warmer is not None:
        await _warmer.stop()
    await get_inflight_tracker().stop()
    await close_upstream_client()


def refresher() -> Optional[RoutingRefresher]:
    return _refresher
