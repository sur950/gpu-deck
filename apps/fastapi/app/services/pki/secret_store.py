"""
Secret store adapter: how the PKI reaches the vault.

The PKI only needs "get a secret by credential name" and "create or replace
one". Both go through ``VaultService`` (MultiFernet over ``VAULT_KEYS``,
Redis ciphertext cache, usage history). Secrets kept here:

| Credential name      | type     | provider        | Content (JSON)                    |
| -------------------- | -------- | --------------- | --------------------------------- |
| ``internal-ca``      | ca_key   | internal_ca     | ``{"key_pem", "cert_pem"}``       |
| ``agent-hub-tls``    | tls_key  | agent_hub_tls   | ``{"key_pem", "cert_pem"}``       |
| ``control-plane-tls``| tls_key  | control_plane   | ``{"key_pem", "cert_pem"}``       |

Tests can pass any object with the same two coroutines (``SecretStore``).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Protocol

from app.core.logger import get_logger
from app.services.vault.service import VaultService
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class SecretStore(Protocol):
    async def get_secret(self, name: str) -> Optional[str]: ...

    async def put_secret(
        self, name: str, value: str, type_: str, provider: str, description: str
    ) -> None: ...


class VaultSecretStore:
    """``SecretStore`` over VaultService (same session as the caller)."""

    def __init__(self, session: AsyncSession):
        self.vault = VaultService(session)

    async def get_secret(self, name: str) -> Optional[str]:
        return await self.vault.get_secret(name, purpose="pki", required=False)

    async def put_secret(
        self, name: str, value: str, type_: str, provider: str, description: str
    ) -> None:
        await self.vault.upsert_secret(
            name,
            value,
            type=type_,
            provider=provider,
            description=description,
            metadata={"managed_by": "pki"},
        )


class MemorySecretStore:
    """In-memory store for unit tests and offline tooling."""

    def __init__(self) -> None:
        self.values: Dict[str, str] = {}

    async def get_secret(self, name: str) -> Optional[str]:
        return self.values.get(name)

    async def put_secret(
        self, name: str, value: str, type_: str, provider: str, description: str
    ) -> None:
        self.values[name] = value


def build_secret_store(session: AsyncSession) -> SecretStore:
    return VaultSecretStore(session)


def dumps_secret(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def loads_secret(value: str) -> Dict[str, Any]:
    return json.loads(value)
