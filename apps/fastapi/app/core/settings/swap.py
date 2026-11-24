"""
Swap manager settings: drain/force defaults, whole-swap
timeout, command polling and per-device lock TTL.
"""

from __future__ import annotations

from app.core.settings.base import DomainSettings
from pydantic import Field


class SwapSettings(DomainSettings):
    """Knobs for the swap state machine (drain -> unload -> load -> health -> route)."""

    SWAP_DEFAULT_DRAIN_TIMEOUT_SECONDS: int = Field(
        default=120,
        description="Drain timeout used when a swap request does not set one",
    )
    SWAP_MAX_DURATION_SECONDS: int = Field(
        default=1800,
        description="A swap older than this (since started_at) fails and rolls back",
    )
    SWAP_FORCE_AFTER_DRAIN_TIMEOUT: bool = Field(
        default=False,
        description="Default force_after_timeout for rule-triggered swaps: unload even if "
        "sessions remain after the drain timeout (false = fail and roll back)",
    )
    SWAP_COMMAND_POLL_SECONDS: float = Field(
        default=1.0,
        description="How often the swap manager polls agent_commands for a result",
    )
    SWAP_DEVICE_LOCK_TTL_SECONDS: int = Field(
        default=2400,
        description="TTL of the per-device Redis lock held while a swap runs",
    )
    SWAP_DRIVE_INTERVAL_SECONDS: int = Field(
        default=5,
        description="How often the jobs leader looks for pending/resumable swaps",
    )
    SWAP_HEALTH_PROBE_TIMEOUT_SECONDS: float = Field(
        default=5.0,
        description="Timeout of the control-plane /_health probe through the proxy",
    )
