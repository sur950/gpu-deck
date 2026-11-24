"""
Cloud provider registry: provider name <-> implementation <-> cluster type.

Imports are lazy so a missing optional SDK only breaks its own provider.
"""

from __future__ import annotations

import importlib
from typing import Dict, Optional, Tuple, Type

from app.core.logger import get_logger
from app.services.cloud.base import CloudInvalidRequestError, CloudProvider

logger = get_logger(__name__)

# name -> (module, class, cluster type, display name, credential type)
PROVIDERS: Dict[str, Tuple[str, str, str, str, str]] = {
    "aws": ("aws", "AwsProvider", "cloud_aws", "Amazon Web Services", "cloud_account"),
    "azure": (
        "azure",
        "AzureProvider",
        "cloud_azure",
        "Microsoft Azure",
        "service_account",
    ),
    "gcp": ("gcp", "GcpProvider", "cloud_gcp", "Google Cloud", "service_account"),
    "digitalocean": (
        "digitalocean",
        "DigitalOceanProvider",
        "cloud_do",
        "DigitalOcean",
        "token",
    ),
}
CLUSTER_TYPE_TO_PROVIDER = {spec[2]: name for name, spec in PROVIDERS.items()}


def provider_class(name: str) -> Type[CloudProvider]:
    spec = PROVIDERS.get(name)
    if spec is None:
        raise CloudInvalidRequestError(f"unknown cloud provider {name!r}")
    module = importlib.import_module(f"app.services.cloud.providers.{spec[0]}")
    return getattr(module, spec[1])


def provider_for_cluster_type(cluster_type: str) -> Optional[str]:
    return CLUSTER_TYPE_TO_PROVIDER.get(cluster_type)
