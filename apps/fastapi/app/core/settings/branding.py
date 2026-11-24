"""
Branding settings — theme, logos and product name come from .env only (no settings UI).

Served by ``GET /api/v1/branding`` (public). Field names keep the original
BRAND_* names; the libs/contracts/env.md names (APP_BYLINE,
BRAND_COLOR, LOGO_URL, FAVICON_URL, DOCS_URL, SUPPORT_URL) are accepted as
aliases so either spelling in .env works.
"""

from __future__ import annotations

from typing import Optional

from app.core.settings.base import DomainSettings, strip_quotes
from pydantic import AliasChoices, Field, field_validator


class BrandingSettings(DomainSettings):
    """White-label values served by GET /api/v1/branding and used in alerts/emails."""

    BRAND_NAME: Optional[str] = Field(
        None, description="Product name shown in the UI (defaults to APP_NAME)"
    )
    BRAND_TAGLINE: str = Field(
        default="GPU control plane",
        validation_alias=AliasChoices("BRAND_TAGLINE", "APP_BYLINE"),
        description="Descriptor line under the logo (env.md: APP_BYLINE)",
    )
    BRAND_COMPANY: str = Field(default="", description="Company name for footers (optional)")
    BRAND_LOGO_URL: str = Field(
        default="/branding/logo.svg",
        validation_alias=AliasChoices("BRAND_LOGO_URL", "LOGO_URL"),
        description="Logo URL, absolute or app-relative (env.md: LOGO_URL)",
    )
    BRAND_LOGO_DARK_URL: str = Field(
        default="",
        description="Logo URL for the dark theme (empty = same as BRAND_LOGO_URL)",
    )
    BRAND_FAVICON_URL: str = Field(
        default="/branding/favicon.svg",
        validation_alias=AliasChoices("BRAND_FAVICON_URL", "FAVICON_URL"),
        description="Favicon URL (env.md: FAVICON_URL)",
    )
    BRAND_PRIMARY_COLOR: str = Field(
        default="#B3122E",
        validation_alias=AliasChoices("BRAND_PRIMARY_COLOR", "BRAND_COLOR"),
        description=(
            "Primary accent color (env.md: BRAND_COLOR). The default is the GPU Deck crimson;"
            " the web app uses its dark step #D3263F in dark mode"
        ),
    )
    BRAND_SUPPORT_URL: str = Field(
        default="",
        validation_alias=AliasChoices("BRAND_SUPPORT_URL", "SUPPORT_URL"),
        description="Support link (optional)",
    )
    BRAND_DOCS_URL: str = Field(
        default="",
        validation_alias=AliasChoices("BRAND_DOCS_URL", "DOCS_URL"),
        description="Docs link (optional)",
    )

    @field_validator("BRAND_NAME", "BRAND_TAGLINE", "BRAND_COMPANY")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        return strip_quotes(v) if v else v

    @property
    def brand_name(self) -> str:
        return self.BRAND_NAME or getattr(self, "APP_NAME", "GPU Deck")
