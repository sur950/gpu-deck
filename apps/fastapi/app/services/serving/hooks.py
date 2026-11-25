"""
Serving process lifecycle: warm the routing table (Redis / dev engine),
start the pub/sub refresher, the upstream pool warmer, the in-flight
flusher, the agent tunnel listener (protocol.md §13) and the RTT prober
(``app.services.serving.latency``), close the upstream pools and the tunnels on
shutdown.

The table itself is written by the control plane (
``services/agent_hub/routing_table.py``) and patched by the swap manager.
"""

from __future__ import annotations

from typing import Optional

from app.core.lifecycle import on_shutdown, on_startup
from app.core.logger import get_logger
from app.services.serving import metrics as prom
from app.services.serving.inflight import get_inflight_tracker
from app.services.serving.latency import LatencyProber
from app.services.serving.routing import RoutingRefresher, get_routing_table
from app.services.serving.upstream import PoolWarmer, close_upstream_client

logger = get_logger(__name__)

_refresher: Optional[RoutingRefresher] = None
_warmer: Optional[PoolWarmer] = None
_prober: Optional[LatencyProber] = None


@on_startup("serving")
async def warm_routing_table() -> None:
    global _refresher, _warmer, _prober
    table = get_routing_table()
    table.on_update(lambda t: prom.routing_version.set(t.version))
    _refresher = RoutingRefresher(table)
    await _refresher.start()
    _warmer = PoolWarmer(table)
    await _warmer.start()
    await get_inflight_tracker().start()
    # After the PKI hook (which provisions the API layer certificate the agents pin).
    from app.services.serving.tunnel import start_tunnel_server

    await start_tunnel_server()
    from app.services.serving.batch_runner import start_batch_runner

    await start_batch_runner()
    # After the tunnel listener: tunnel nodes are probed over the agents' own connections.
    _prober = LatencyProber(table)
    await _prober.start()
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
    if _prober is not None:
        await _prober.stop()
    await get_inflight_tracker().stop()
    from app.services.serving.tunnel import stop_tunnel_server

    await stop_tunnel_server()
    from app.services.serving.batch_runner import stop_batch_runner

    await stop_batch_runner()
    await close_upstream_client()


def refresher() -> Optional[RoutingRefresher]:
    return _refresher
