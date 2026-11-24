"""
Deployment, placement scheduler and model cache settings.
"""

from __future__ import annotations

from typing import Tuple

from app.core.settings.base import DomainSettings
from pydantic import AliasChoices, Field


class DeploymentSettings(DomainSettings):
    """Scheduler knobs, engine ports and reconciliation cadence."""

    ENGINE_PORT_RANGE: str = Field(
        default="10240-10340",
        description="Default localhost port range for engines on a node",
    )
    SCHEDULER_VRAM_HEADROOM_PCT: int = Field(
        default=10,
        description="Free VRAM kept per device when placing (percent of total)",
    )
    SCHEDULER_KV_BYTES_PER_TOKEN_FALLBACK: int = Field(
        default=131072,
        description="KV-cache bytes per token when the model config lacks shape",
    )
    SCHEDULER_MAX_DEVICE_COMBOS: int = Field(
        default=64, description="Max device combinations evaluated per node per replica"
    )
    DEPLOYMENT_RECONCILE_INTERVAL_SECONDS: int = Field(
        default=15,
        description="Jobs-process tick that turns desired replicas into instances",
    )
    DEPLOYMENT_LOAD_TIMEOUT_SECONDS: int = Field(
        default=900,
        description="pending/loading instances older than this are marked error",
    )
    DEPLOYMENT_UNLOAD_DRAIN_TIMEOUT_SECONDS: int = Field(
        default=120,
        description="Default drain timeout for unloads issued by deployments",
    )
    DEPLOYMENT_ERROR_GRACE_SECONDS: int = Field(
        default=60,
        description="Errored instances still count toward replicas this long (auto-heal window)",
    )
    DEPLOYMENT_CRASHLOOP_ERRORS: int = Field(
        default=3,
        description="Instance failures inside the window that mark a deployment failed",
    )
    DEPLOYMENT_CRASHLOOP_WINDOW_SECONDS: int = Field(
        default=600, description="Crash-loop detection window"
    )
    DEPLOYMENT_STOPPED_INSTANCE_RETENTION_DAYS: int = Field(
        default=7, description="Stopped instance rows older than this are pruned"
    )
    ROUTING_PUBLISH_INTERVAL_SECONDS: int = Field(
        default=2,
        description="Routing table refresh (live session counts) for the API layer",
        validation_alias=AliasChoices(
            "ROUTING_PUBLISH_INTERVAL_SECONDS", "ROUTING_TABLE_REFRESH_SECONDS"
        ),
    )
    MODEL_CACHE_LRU_SWEEP_INTERVAL_MINUTES: int = Field(
        default=60, description="How often the LRU cache sweep runs per node"
    )
    MODEL_CACHE_HIGH_WATER_PCT: int = Field(
        default=90,
        description="Sweep a node cache when used bytes exceed this % of its limit",
    )
    MODEL_CACHE_LOW_WATER_PCT: int = Field(
        default=75, description="Sweep until used bytes drop under this % of the limit"
    )

    def get_engine_port_range(self) -> Tuple[int, int]:
        """``[start, end)`` parsed from ``ENGINE_PORT_RANGE``."""
        start, _, end = self.ENGINE_PORT_RANGE.partition("-")
        lo = int(start)
        hi = int(end) if end else lo + 100
        if hi <= lo:
            raise ValueError("ENGINE_PORT_RANGE end must be greater than start")
        return lo, hi
