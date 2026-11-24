"""
Provider construction from vault credentials.

The secret is read through ``VaultService`` (decrypted in memory only,
usage recorded with a ``cloud:<provider>:<purpose>`` purpose) and parsed by
the provider's ``parse_credentials``. Nothing here logs secret material.

Tests swap the factory with ``set_provider_factory(fn)`` where
``fn(provider_name, parsed_credentials) -> CloudProvider``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.cloud.base import CloudCredentialError, CloudProvider
from app.services.cloud.providers import PROVIDERS, provider_class
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

ProviderFactory = Callable[[str, Dict[str, Any]], CloudProvider]
_factory_override: Optional[ProviderFactory] = None


def set_provider_factory(factory: Optional[ProviderFactory]) -> None:
    """Replace provider construction (tests / fixtures). ``None`` restores the default."""
    global _factory_override
    _factory_override = factory


def parse_secret(provider: str, secret: str) -> Dict[str, Any]:
    """Vault plaintext -> provider credential dict (JSON, or a bare token for DigitalOcean)."""
    text = (secret or "").strip()
    if not text:
        raise CloudCredentialError("credential value is empty", provider=provider)
    try:
        raw = json.loads(text)
    except ValueError:
        if provider == "digitalocean":
            raw = {"token": text}
        else:
            raise CloudCredentialError(
                f"{provider} credential must be JSON (see the cloud README)",
                provider=provider,
            ) from None
    if not isinstance(raw, dict):
        raise CloudCredentialError("credential JSON must be an object", provider=provider)
    return provider_class(provider).parse_credentials(raw)  # type: ignore[attr-defined]


def build_provider(provider: str, credentials: Dict[str, Any]) -> CloudProvider:
    if _factory_override is not None:
        return _factory_override(provider, credentials)
    cls = provider_class(provider)
    timeout = float(settings.CLOUD_PROVIDER_TIMEOUT_SECONDS)
    if provider == "digitalocean":
        return cls(credentials, timeout=timeout, base_url=settings.CLOUD_DO_API_URL)
    if provider == "azure":
        return cls(credentials, timeout=timeout, prices_url=settings.CLOUD_AZURE_PRICES_URL)
    return cls(credentials, timeout=timeout)


async def load_provider(
    session: AsyncSession,
    provider: str,
    credential_id: Optional[uuid.UUID],
    *,
    purpose: str,
    project: Optional[str] = None,
) -> CloudProvider:
    """Provider client for ``credential_id`` (must be an active vault entry for ``provider``)."""
    from app.db.postgres.repositories.credential_repository import CredentialRepository
    from app.services.vault import VaultService

    if provider not in PROVIDERS:
        raise CloudCredentialError(f"unknown cloud provider {provider!r}", provider=provider)
    if credential_id is None:
        raise CloudCredentialError(
            f"no {provider} credential: set the cluster's credential_id (vault)",
            provider=provider,
        )
    row = await CredentialRepository(session).get_active(credential_id)
    if row is None or row.status != "active":
        raise CloudCredentialError(
            "credential_id does not reference an active vault credential",
            provider=provider,
        )
    if row.provider and row.provider != provider:
        raise CloudCredentialError(
            f"credential is for provider {row.provider!r}, not {provider!r}",
            provider=provider,
        )
    secret = await VaultService(session).get_secret(
        credential_id, purpose=f"cloud:{provider}:{purpose}"
    )
    parsed = parse_secret(provider, secret or "")
    instance = build_provider(provider, parsed)
    if project and provider == "gcp":
        instance.project = project  # type: ignore[attr-defined]
    return instance
