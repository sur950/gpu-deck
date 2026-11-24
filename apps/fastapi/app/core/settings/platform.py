"""
Platform identity settings reported by GET /system/info.

Only fields no other domain is expected to declare live here (the settings
aggregator raises on duplicate field names). Hub URLs come from the fleet domain's settings
and are read with ``getattr`` fallbacks in the health routes.
"""

from __future__ import annotations

from typing import Optional

from app.core.settings.base import DomainSettings
from pydantic import Field


class PlatformSettings(DomainSettings):
    """Build metadata and protocol version exposed to the UI/CLI."""

    BUILD_SHA: Optional[str] = Field(None, description="Git SHA of the running build (CI sets it)")
    AGENT_PROTOCOL_VERSION: int = Field(
        default=1,
        description="Agent protocol major (libs/contracts/agent-protocol envelope.v)",
    )
