"""
Billing settings (price plans, reports).
"""

from __future__ import annotations

from app.core.settings.base import DomainSettings
from pydantic import Field


class BillingSettings(DomainSettings):
    """Currency and monthly report automation."""

    BILLING_CURRENCY: str = Field(default="USD", description="Display currency (prices are USD)")
    BILLING_MONTHLY_REPORT_DAY: int = Field(
        default=1,
        description="Day of month to auto-generate last month's report (0 = off)",
    )
