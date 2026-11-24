"""
Alert settings: delivery defaults, dedupe window, retention
and the SMTP / Slack / webhook secret names used when a channel has no
credential of its own (libs/contracts/env.md sections 10 and 12).
"""

from __future__ import annotations

from typing import Optional

from app.core.settings.base import DomainSettings
from pydantic import Field


class AlertSettings(DomainSettings):
    """Alert channels and delivery knobs."""

    ALERT_DEFAULT_MIN_SEVERITY: str = Field(
        default="warning", description="min_severity for new channels when not given"
    )
    ALERT_DEDUPE_WINDOW_SECONDS: int = Field(
        default=600,
        description="Events with the same dedupe_key inside this window bump occurrences "
        "instead of creating a new alert_events row",
    )
    ALERT_EVENTS_RETENTION_DAYS: int = Field(
        default=90,
        description="alert_events older than this are deleted by the retention job",
    )
    ALERT_SEND_TIMEOUT_SECONDS: float = Field(
        default=10.0, description="HTTP / SMTP timeout for one delivery attempt"
    )
    ALERT_MAX_ATTEMPTS: int = Field(default=3, description="Delivery attempts per (event, channel)")
    ALERT_RETRY_BACKOFF_SECONDS: int = Field(
        default=30,
        description="Base backoff between delivery attempts (doubles per attempt)",
    )

    # Email defaults (channel config overrides); password lives in the vault.
    SMTP_HOST: Optional[str] = Field(None, description="Default SMTP host for email channels")
    SMTP_PORT: int = Field(default=587, description="Default SMTP port")
    SMTP_USERNAME: Optional[str] = Field(None, description="Default SMTP username")
    SMTP_PASSWORD_CREDENTIAL: str = Field(
        default="smtp-password",
        description="Vault credential name holding the SMTP password when a channel has none",
    )
    SMTP_FROM: str = Field(default="gpudeck@localhost", description="Default From address")
    SMTP_USE_TLS: bool = Field(default=True, description="STARTTLS (or implicit TLS on port 465)")

    SLACK_DEFAULT_WEBHOOK_CREDENTIAL: Optional[str] = Field(
        None,
        description="Vault credential name used when a Slack channel has no credential_id",
    )
    WEBHOOK_SIGNING_SECRET_CREDENTIAL: Optional[str] = Field(
        None,
        description="Vault credential name whose value signs webhook payloads "
        "(HMAC-SHA256 X-GPUDeck-Signature) when a channel has no credential_id",
    )
