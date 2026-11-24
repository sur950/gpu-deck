"""
Cloud GPU VM provisioning settings: providers, agent bootstrap,
lifecycle timings and per-cluster limits. Provider credentials never live
here; they come from the vault through ``clusters.credential_id``.
"""

from __future__ import annotations

from typing import List

from app.core.settings.base import DomainSettings
from pydantic import Field


class CloudSettings(DomainSettings):
    """AWS / Azure / GCP / DigitalOcean GPU VMs that join as nodes."""

    CLOUD_PROVIDERS_ENABLED: str = Field(
        default="aws,azure,gcp,digitalocean",
        description="Comma list of cloud providers offered in the UI/API",
    )
    CLOUD_AGENT_VERSION: str = Field(
        default="latest",
        description="Node agent version cloud-init downloads from the hub "
        "(/agent/v1/downloads/{version}/{target}; latest = newest build in dist/agent)",
    )
    CLOUD_AGENT_DOWNLOAD_BASE_URL: str = Field(
        default="",
        description="Base URL of the agent download endpoint VMs use; empty = AGENT_HUB_PUBLIC_URL",
    )
    CLOUD_JOIN_TOKEN_TTL_MINUTES: int = Field(
        default=60,
        ge=5,
        le=1440,
        description="TTL of the one-time join token baked into a VM's cloud-init",
    )
    CLOUD_BOOT_TIMEOUT_MINUTES: int = Field(
        default=30,
        ge=5,
        description="A VM whose agent has not joined after this long goes to status error",
    )
    CLOUD_DRAIN_TIMEOUT_SECONDS: int = Field(
        default=600,
        ge=0,
        le=3600,
        description="Node drain time limit before stop/terminate proceeds anyway",
    )
    CLOUD_MAX_NODES_PER_CLUSTER: int = Field(
        default=20,
        ge=1,
        description="Default cap of nodes per cloud cluster (clusters.config.cloud.max_nodes wins)",
    )
    CLOUD_DEFAULT_DISK_GB: int = Field(default=200, ge=20, description="Default VM disk size")
    CLOUD_RECONCILE_SECONDS: int = Field(
        default=30, ge=5, description="Cadence of the cloud.reconcile lifecycle job"
    )
    CLOUD_STATUS_POLL_SECONDS: int = Field(
        default=300,
        ge=30,
        description="How often a running/stopped VM is re-read from the provider",
    )
    CLOUD_CATALOG_CACHE_SECONDS: int = Field(
        default=3600,
        ge=0,
        description="Redis cache of region / instance-type / price listings (0 = off)",
    )
    CLOUD_PROVIDER_TIMEOUT_SECONDS: int = Field(
        default=60, ge=5, description="Per-call timeout for provider APIs"
    )
    CLOUD_DO_API_URL: str = Field(
        default="https://api.digitalocean.com",
        description="DigitalOcean REST API base URL",
    )
    CLOUD_AZURE_PRICES_URL: str = Field(
        default="https://prices.azure.com/api/retail/prices",
        description="Azure Retail Prices API (public, unauthenticated)",
    )

    def get_cloud_providers_enabled(self) -> List[str]:
        return [p.strip() for p in self.CLOUD_PROVIDERS_ENABLED.split(",") if p.strip()]
