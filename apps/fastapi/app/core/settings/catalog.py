"""
Model catalog settings: Hugging Face / ModelScope endpoints and tokens,
search caching and local scan roots (libs/contracts/env.md §8).
"""

from __future__ import annotations

from typing import List

from app.core.settings.base import DomainSettings
from pydantic import Field


class CatalogSettings(DomainSettings):
    """Catalog sources: HF, ModelScope and local paths."""

    HF_ENDPOINT: str = Field(
        default="https://huggingface.co",
        description="Hugging Face hub base URL (mirror for offline sites)",
    )
    MODELSCOPE_ENDPOINT: str = Field(
        default="https://www.modelscope.cn",
        description="ModelScope base URL (mirror for offline sites)",
    )
    HF_TOKEN_CREDENTIAL: str = Field(
        default="huggingface-token",
        description="Vault credential name holding an optional Hugging Face token",
    )
    MODELSCOPE_TOKEN_CREDENTIAL: str = Field(
        default="modelscope-token",
        description="Vault credential name holding an optional ModelScope token",
    )
    CATALOG_SEARCH_CACHE_SECONDS: int = Field(
        default=300,
        description="Redis TTL for remote catalog search / file-listing results",
    )
    CATALOG_HTTP_TIMEOUT_SECONDS: float = Field(
        default=20.0, description="HTTP timeout for HF / ModelScope API calls"
    )
    LOCAL_MODEL_SCAN_ROOTS: str = Field(
        default="~/.ollama/models,~/.lmstudio/models,~/.cache/huggingface/hub,/models",
        description="Comma-separated default scan roots offered in the UI",
    )
    LOCAL_SCAN_MAX_FILES: int = Field(
        default=20000, description="Safety cap on files visited by one local scan"
    )

    def get_local_scan_roots(self) -> List[str]:
        return [r.strip() for r in self.LOCAL_MODEL_SCAN_ROOTS.split(",") if r.strip()]
