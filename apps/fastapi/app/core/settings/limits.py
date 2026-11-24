"""
Request limit settings for the serving API.
The API layer caps request size and time so oversized or stuck requests cannot tie up GPUs.
"""

from __future__ import annotations

from app.core.settings.base import DomainSettings
from pydantic import Field


class RequestLimitSettings(DomainSettings):
    """Body size and time caps enforced by app/middlewares/request_limits.py."""

    MAX_REQUEST_BODY_BYTES: int = Field(
        default=32 * 1024 * 1024,
        description="Maximum request body (bytes) on the serving API",
    )
    MAX_UPLOAD_BODY_BYTES: int = Field(
        default=256 * 1024 * 1024,
        description="Maximum multipart body (bytes) for audio/image upload endpoints",
    )
    REQUEST_TIMEOUT_SECONDS: int = Field(
        default=600, description="Upper bound for one non-streaming serving request"
    )
    STREAM_IDLE_TIMEOUT_SECONDS: int = Field(
        default=120,
        description="Abort a stream when the engine sends nothing for this long",
    )
    ENGINE_CONNECT_TIMEOUT_SECONDS: float = Field(
        default=5.0, description="TCP/TLS connect timeout to the node-agent proxy"
    )
    ADMIN_MAX_REQUEST_BODY_BYTES: int = Field(
        default=4 * 1024 * 1024,
        description="Maximum request body (bytes) on the admin API",
    )
