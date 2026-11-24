"""
Cloud catalog: provider descriptions plus cached region / GPU
instance-type / quota listings.

Listings are cached in Redis (``gpu-deck:cloud:catalog:...``, non-secret
data only) for ``CLOUD_CATALOG_CACHE_SECONDS``; ``refresh=True`` bypasses the
cache. Provider SDK calls run in a worker thread.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, Dict, List, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.redis.client import get_redis, redis_key
from app.services.cloud.base import (
    CloudInstanceType,
    CloudInvalidRequestError,
    CloudProvider,
    CloudQuota,
    CloudRegion,
)
from app.services.cloud.providers import PROVIDERS

logger = get_logger(__name__)

PROVIDER_INFO: Dict[str, Dict[str, Any]] = {
    "aws": {
        "credential_fields": [
            "access_key_id",
            "secret_access_key",
            "session_token?",
            "role_arn?",
        ],
        "sdk": "boto3",
        "pricing": "AWS Pricing API, on-demand Linux list price",
        "launch_options": [
            "subnet_id",
            "security_group_ids",
            "key_name",
            "iam_instance_profile",
            "associate_public_ip",
        ],
        "notes": "Default image: AWS Deep Learning Base OSS NVIDIA driver AMI (Ubuntu 22.04). "
        "IMDSv2 required; root volume gp3, encrypted.",
    },
    "azure": {
        "credential_fields": [
            "tenant_id",
            "client_id",
            "client_secret",
            "subscription_id",
        ],
        "sdk": "azure-mgmt-compute + azure-mgmt-network",
        "pricing": "Azure Retail Prices API, pay-as-you-go Linux",
        "launch_options": [
            "resource_group",
            "subnet_id",
            "public_ip",
            "network_security_group_id",
            "admin_username",
            "ssh_public_key",
            "os_disk_type",
        ],
        "notes": "resource_group and subnet_id are required. Default image "
        "microsoft-dsvm:ubuntu-hpc:2204:latest (NVIDIA drivers).",
    },
    "gcp": {
        "credential_fields": ["service account JSON key (type, project_id, private_key, ...)"],
        "sdk": "google-cloud-compute",
        "pricing": "not exposed by the Compute API (price is null; set a plan by hand)",
        "launch_options": [
            "project",
            "network",
            "subnetwork",
            "public_ip",
            "disk_type",
            "service_account_email",
        ],
        "notes": "N1 GPUs are offered as n1-standard-N+<accelerator>xK. Labels use gpu-deck-* "
        "(no ':' allowed); instances carry the network tag gpu-deck-node.",
    },
    "digitalocean": {
        "credential_fields": ["token"],
        "sdk": "httpx (REST v2)",
        "pricing": "DigitalOcean sizes price_hourly",
        "launch_options": ["vpc_uuid", "ssh_keys", "monitoring"],
        "notes": "Powered-off GPU Droplets are still billed; prefer terminate.",
    },
}


def provider_infos() -> List[Dict[str, Any]]:
    enabled = set(settings.get_cloud_providers_enabled())
    out = []
    for name, (_, _, cluster_type, display, cred_type) in PROVIDERS.items():
        info = PROVIDER_INFO[name]
        out.append(
            {
                "id": name,
                "name": display,
                "cluster_type": cluster_type,
                "credential_type": cred_type,
                "credential_fields": info["credential_fields"],
                "sdk": info["sdk"],
                "enabled": name in enabled,
                "pricing": info["pricing"],
                "launch_options": info["launch_options"],
                # No cloud account was available while building this: every provider is
                # verified against recorded/mocked responses only.
                "verified_live": False,
                "notes": info["notes"],
            }
        )
    return out


def ensure_enabled(provider: str) -> None:
    if provider not in PROVIDERS:
        raise CloudInvalidRequestError(f"unknown cloud provider {provider!r}")
    if provider not in settings.get_cloud_providers_enabled():
        raise CloudInvalidRequestError(
            f"cloud provider {provider!r} is disabled (CLOUD_PROVIDERS_ENABLED)",
            provider=provider,
        )


async def run_provider(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Blocking SDK call in a worker thread."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def _key(provider: str, credential_id: uuid.UUID, kind: str, region: str = "-") -> str:
    return redis_key("cloud", "catalog", provider, str(credential_id), kind, region or "-")


async def _cached(key: str, refresh: bool, loader) -> List[Dict[str, Any]]:
    ttl = int(settings.CLOUD_CATALOG_CACHE_SECONDS)
    if ttl > 0 and not refresh:
        try:
            raw = await (await get_redis()).get(key)
            if raw:
                return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"cloud catalog cache read failed: {type(exc).__name__}")
    data = await loader()
    if ttl > 0:
        try:
            await (await get_redis()).set(key, json.dumps(data, default=str), ex=ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"cloud catalog cache write failed: {type(exc).__name__}")
    return data


async def regions(
    client: CloudProvider, credential_id: uuid.UUID, *, refresh: bool = False
) -> List[Dict[str, Any]]:
    async def load():
        items: List[CloudRegion] = await run_provider(client.list_regions)
        return [r.to_dict() for r in items]

    return await _cached(_key(client.name, credential_id, "regions"), refresh, load)


async def instance_types(
    client: CloudProvider,
    credential_id: uuid.UUID,
    region: str,
    *,
    refresh: bool = False,
) -> List[Dict[str, Any]]:
    async def load():
        items: List[CloudInstanceType] = await run_provider(client.list_instance_types, region)
        return [i.to_dict() for i in items]

    return await _cached(_key(client.name, credential_id, "types", region), refresh, load)


async def quotas(client: CloudProvider, region: str) -> List[Dict[str, Any]]:
    items: List[CloudQuota] = await run_provider(client.list_quotas, region)
    return [q.to_dict() for q in items]


async def find_instance_type(
    client: CloudProvider, credential_id: uuid.UUID, region: str, type_id: str
) -> Dict[str, Any]:
    items = await instance_types(client, credential_id, region)
    match = next((i for i in items if i["id"] == type_id), None)
    if match is None:
        items = await instance_types(client, credential_id, region, refresh=True)
        match = next((i for i in items if i["id"] == type_id), None)
    if match is None:
        raise CloudInvalidRequestError(
            f"{client.name} GPU instance type {type_id!r} is not offered in {region}",
            provider=client.name,
        )
    return match


def filter_instance_types(
    items: List[Dict[str, Any]],
    *,
    gpu_vendor: Optional[str] = None,
    min_vram_gb: Optional[float] = None,
    gpu_count: Optional[int] = None,
    search: Optional[str] = None,
    max_price: Optional[float] = None,
) -> List[Dict[str, Any]]:
    out = []
    for item in items:
        if gpu_vendor and (item.get("gpu_vendor") or "").lower() != gpu_vendor.lower():
            continue
        if min_vram_gb is not None:
            vram = item.get("total_vram_bytes") or 0
            if vram < min_vram_gb * 1024**3:
                continue
        if gpu_count is not None and int(item.get("gpu_count") or 0) != gpu_count:
            continue
        if search:
            hay = f"{item.get('id')} {item.get('gpu_model') or ''} {item.get('family') or ''}"
            if search.lower() not in hay.lower():
                continue
        if max_price is not None:
            price = item.get("price_per_hour_usd")
            if price is None or price > max_price:
                continue
        out.append(item)
    return out
