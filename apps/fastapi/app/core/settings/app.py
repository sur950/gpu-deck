"""
Application, server, process and CORS settings.
"""

from __future__ import annotations

from typing import List, Optional

from app.core.settings.base import DomainSettings, strip_quotes
from pydantic import AliasChoices, Field, field_validator


class AppSettings(DomainSettings):
    """Identity, environment and listener configuration for the three processes."""

    # Application
    APP_NAME: str = Field(default="GPU Deck", description="Application name")
    APP_VERSION: str = Field(default="1.0.0", description="Application version")
    APP_ENV: str = Field(
        default="development",
        description="Environment: development, staging, production",
    )
    DEBUG: bool = Field(default=False, description="Debug mode (enables /docs and error details)")
    API_V1_PREFIX: str = Field(default="/api/v1", description="Admin API v1 prefix")
    TZ: str = Field(default="UTC", description="Timezone for schedules and workers")

    # Listeners (local development defaults)
    # env.md names (ADMIN_HOST/ADMIN_PORT, ADMIN_PUBLIC_URL, API_LAYER_PUBLIC_URL) are
    # accepted as aliases; compose and Helm use them.
    HOST: str = Field(
        default="0.0.0.0",
        description="Bind host for every HTTP listener",
        validation_alias=AliasChoices("HOST", "ADMIN_HOST"),
    )
    PORT: int = Field(
        default=8180,
        description="Admin API port (app.main)",
        validation_alias=AliasChoices("PORT", "ADMIN_PORT"),
    )
    API_LAYER_PORT: int = Field(default=8181, description="Serving API port (app.api_layer_main)")
    AGENT_HUB_PORT: int = Field(
        default=8443,
        description="Agent hub WSS port (mTLS); node agents dial out to it",
    )
    AGENT_PROXY_PORT: int = Field(
        default=9443,
        description="Node-agent engine proxy port (mTLS) the API layer calls",
    )
    WORKERS: int = Field(default=4, description="Gunicorn workers per API process in production")
    RELOAD: bool = Field(default=False, description="Auto-reload on code changes (dev only)")

    # Serving API format roots (serving.md §1, env.md §11). Mounted as
    # {root}/v1 (OpenAI, Anthropic) and {root}/api (Ollama); the bare /v1 and /api
    # compat paths are added by SERVING_ENABLE_BARE_PATHS. Legacy values with the
    # version segment (/v1, /anthropic/v1, /api) are still accepted
    # (app.services.serving.formats.explicit_root strips it).
    SERVING_OPENAI_PREFIX: str = Field(
        default="/openai", description="OpenAI-format root (mounted at <root>/v1)"
    )
    SERVING_ANTHROPIC_PREFIX: str = Field(
        default="/anthropic", description="Anthropic-format root (mounted at <root>/v1)"
    )
    SERVING_OLLAMA_PREFIX: str = Field(
        default="/ollama", description="Ollama-format root (mounted at <root>/api)"
    )

    # Jobs process
    SCHEDULER_EMBEDDED: bool = Field(
        default=False,
        description="Run the APScheduler loop inside the admin API process instead of the "
        "dedicated jobs process (python -m app.workers.scheduler). Single-container only.",
    )

    # URLs
    FRONTEND_URL: str = Field(
        default="http://localhost:3180", description="Web UI base URL (CORS, links)"
    )
    PUBLIC_API_URL: str = Field(
        default="http://localhost:8180",
        description="Public base URL of the admin API",
        validation_alias=AliasChoices("PUBLIC_API_URL", "ADMIN_PUBLIC_URL"),
    )
    PUBLIC_SERVING_URL: str = Field(
        default="http://localhost:8181",
        description="Public base URL of the serving API",
        validation_alias=AliasChoices("PUBLIC_SERVING_URL", "API_LAYER_PUBLIC_URL"),
    )

    # CORS
    CORS_ORIGINS: str = Field(
        default="http://localhost:3180",
        description="Comma-separated allowed browser origins (scheme + host, no trailing slash)",
    )
    CORS_CREDENTIALS: bool = Field(default=True, description="Allow credentials")
    TRUSTED_HOSTS: str = Field(
        default="localhost,127.0.0.1",
        description="Extra Host header values accepted in production (comma list; hostnames "
        "from CORS_ORIGINS / PUBLIC_API_URL / PUBLIC_SERVING_URL are always accepted)",
    )
    CORS_METHODS: str = Field(default="*", description="Allowed methods (* or comma list)")
    CORS_HEADERS: str = Field(default="*", description="Allowed headers (* or comma list)")

    @field_validator("APP_ENV")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = ["development", "staging", "production", "test"]
        if v not in allowed:
            raise ValueError(f"APP_ENV must be one of {allowed}")
        return v

    @field_validator("APP_NAME")
    @classmethod
    def _strip_app_name(cls, v: str) -> str:
        return strip_quotes(v)

    def get_cors_origins(self) -> List[str]:
        """CORS origins as a list; trailing slashes stripped (browsers never send one)."""
        return [o.strip().rstrip("/") for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def get_trusted_hosts(self) -> List[str]:
        """Bare hostnames for TrustedHostMiddleware (Host header has no scheme)."""
        from urllib.parse import urlparse

        hosts = []
        for origin in self.get_cors_origins():
            hostname = urlparse(origin).hostname
            if hostname:
                hosts.append(hostname)
        for url in (self.PUBLIC_API_URL, self.PUBLIC_SERVING_URL):
            hostname = urlparse(url).hostname
            if hostname and hostname not in hosts:
                hosts.append(hostname)
        for extra in self.TRUSTED_HOSTS.split(","):
            extra = extra.strip()
            if extra and extra not in hosts:
                hosts.append(extra)
        return hosts or ["*"]

    def cors_methods_list(self) -> List[str]:
        return ["*"] if self.CORS_METHODS == "*" else self.CORS_METHODS.split(",")

    def cors_headers_list(self) -> List[str]:
        return ["*"] if self.CORS_HEADERS == "*" else self.CORS_HEADERS.split(",")

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def public_api_base_url(self) -> str:
        return self.PUBLIC_API_URL.rstrip("/")

    def resolve_optional(self, value: Optional[str]) -> Optional[str]:
        """Treat empty strings from .env as unset."""
        return value or None
