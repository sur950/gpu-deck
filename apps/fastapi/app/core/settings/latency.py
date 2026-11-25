"""
Network latency settings: API-layer identity and region, round-trip probing, latency-aware
routing, the ``Server-Timing`` header, RTT history and traffic-aware placement.

See ``docs/DEPLOYMENT.md`` (Network latency) and ``libs/contracts/env.md``.
"""

from __future__ import annotations

from app.core.settings.base import DomainSettings
from pydantic import Field


class LatencySettings(DomainSettings):
    """How the API layer measures and uses the network distance to each node."""

    # --- API-layer identity -------------------------------------------------
    API_LAYER_ID: str = Field(
        default="",
        description="Stable id of this API layer in RTT reports and request logs "
        "(empty = the host name)",
    )
    API_LAYER_REGION: str = Field(
        default="default",
        description="Region label of this API layer (e.g. eu-west); RTT, traffic counts and "
        "placement are grouped by it",
    )

    # --- probing --------------------------------------------------------------
    SERVING_RTT_PROBE_SECONDS: float = Field(
        default=10.0,
        description="How often the API layer measures its round trip to every connected node "
        "(one GET /_health on the warm connection; 0 = off)",
    )
    SERVING_RTT_EWMA_ALPHA: float = Field(
        default=0.3, description="Weight of the newest RTT sample in the smoothed value (0-1)"
    )

    # --- routing ----------------------------------------------------------------
    SERVING_LATENCY_ROUTING: bool = Field(
        default=True,
        description="Prefer the replica with the lowest expected latency (measured RTT plus a "
        "load penalty) for the least_sessions strategy",
    )
    SERVING_LATENCY_TOLERANCE_MS: float = Field(
        default=5.0,
        description="Replicas whose expected latency is within this many ms of the best share "
        "the traffic",
    )
    SERVING_LATENCY_LOAD_PENALTY_MS: float = Field(
        default=200.0,
        description="Expected extra latency (ms) of a replica running at its full concurrency; "
        "scaled by its in-flight share and weight",
    )
    SERVING_LATENCY_DEFAULT_CAPACITY: int = Field(
        default=8,
        description="Concurrency assumed per replica for the load penalty when the deployment "
        "sets no max_concurrent",
    )

    # --- Server-Timing ------------------------------------------------------------
    SERVING_SERVER_TIMING: str = Field(
        default="generic",
        description="Server-Timing header on serving responses: off | generic (phase "
        "durations only) | detailed (adds the API-layer region and node name)",
    )

    # --- history ----------------------------------------------------------------
    LATENCY_RETENTION_DAYS: int = Field(
        default=14, description="Days of per-minute RTT samples kept in node_latency_samples"
    )

    # --- placement ----------------------------------------------------------------
    SCHEDULER_LATENCY_AWARE: bool = Field(
        default=True,
        description="Placement prefers nodes close to the API layers that carry a "
        "deployment's traffic (no effect without RTT data)",
    )
    SCHEDULER_LATENCY_WEIGHT: float = Field(
        default=30.0,
        description="Placement score points a node loses at SCHEDULER_LATENCY_SCALE_MS over "
        "the best candidate's traffic-weighted RTT",
    )
    SCHEDULER_LATENCY_TOLERANCE_MS: float = Field(
        default=5.0, description="RTT difference (ms) placement treats as equal"
    )
    SCHEDULER_LATENCY_SCALE_MS: float = Field(
        default=50.0,
        description="RTT excess (ms) at which the full SCHEDULER_LATENCY_WEIGHT applies",
    )
    SCHEDULER_LINK_COST_WEIGHT: float = Field(
        default=10.0,
        description="Score points per unit of inter-node link cost for pipeline stages on "
        "different nodes (other cluster 3, other zone 2, other rack 1, plus measured RTT)",
    )
    SCHEDULER_TRAFFIC_WINDOW_HOURS: int = Field(
        default=24,
        description="Hours of per-region request counts placement weighs RTT by",
    )
