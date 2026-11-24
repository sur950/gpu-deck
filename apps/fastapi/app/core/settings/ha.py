"""
High-availability settings: Postgres lease leader election
for the jobs process.
"""

from __future__ import annotations

import os
import socket

from app.core.settings.base import DomainSettings
from pydantic import Field


def _default_instance_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class HaSettings(DomainSettings):
    """Lease TTL / renew cadence and the holder id written to ha_leases."""

    HA_LEASE_TTL_SECONDS: int = Field(
        default=30,
        description="Lease validity; a holder that stops renewing loses it after this",
    )
    HA_LEASE_RENEW_SECONDS: int = Field(
        default=10,
        description="How often the holder renews (must be well under the TTL)",
    )
    HA_INSTANCE_ID: str = Field(
        default_factory=_default_instance_id,
        description="Lease holder id of this process (default hostname:pid)",
    )
