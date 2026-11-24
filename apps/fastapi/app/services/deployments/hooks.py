"""
Deployment lifecycle hooks: the jobs process subscribes to on-demand
load requests from the API layer (``gpu-deck:deployments:start-requested``).
"""

from app.core.lifecycle import on_shutdown, on_startup
from app.core.logger import get_logger

logger = get_logger(__name__)


@on_startup("scheduler")
def start_on_demand_subscriber() -> None:
    from app.services.deployments.on_demand import get_subscriber

    get_subscriber().start()


@on_shutdown("scheduler")
def stop_on_demand_subscriber() -> None:
    from app.services.deployments.on_demand import get_subscriber

    get_subscriber().stop()
