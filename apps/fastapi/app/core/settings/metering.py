"""
Metering settings (usage events, hourly rollups, GPU-minute allocation).

GPU-minute allocation needs no knobs of its own: uptime comes from the fleet domain's
per-minute engine rollups (or instance ready intervals) and is recomputed for
the current and previous hour on every rollup run.
"""

from __future__ import annotations

from app.core.settings.base import DomainSettings
from pydantic import Field


class MeteringSettings(DomainSettings):
    """Rollup cadence and retention of the usage pipeline."""

    METERING_ROLLUP_INTERVAL_MINUTES: int = Field(
        default=5, description="Re-run the current hour's rollup this often"
    )
    USAGE_EVENTS_RETENTION_DAYS: int = Field(
        default=400, description="Drop usage_events partitions older than this"
    )
