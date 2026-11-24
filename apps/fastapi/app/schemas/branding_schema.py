"""
Branding schema (admin.yaml: Branding tag). Values come from .env only.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class Branding(BaseModel):
    app_name: str
    app_byline: str
    brand_color: str
    logo_url: str
    favicon_url: str
    docs_url: Optional[str] = None
    support_url: Optional[str] = None
    theme: Dict[str, Any] = Field(default_factory=dict)
