"""
Health / system info schemas (admin.yaml: Health tag). ``ServiceHealth`` and
``HealthCheckResponse`` live in common_schema.py (skeleton).
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ServingPrefixes(BaseModel):
    openai_prefix: str
    anthropic_prefix: str
    ollama_prefix: str


class SystemInfo(BaseModel):
    app_name: str
    version: str
    environment: str
    build_sha: Optional[str] = None
    protocol_version: int
    api_layer_url: str
    agent_hub_url: str
    serving: ServingPrefixes
    retention: Dict[str, int] = Field(default_factory=dict)
    limits: Dict[str, Any] = Field(default_factory=dict)
