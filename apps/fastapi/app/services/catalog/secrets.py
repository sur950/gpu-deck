"""
Vault lookups for catalog tokens (HF / ModelScope) and the benchmark API key.

Remote sources use ``resolve_source_token``: the token credential picked for
a model (``models.metadata.credential_id``) or a search, else the credential
named by ``HF_TOKEN_CREDENTIAL`` / ``MODELSCOPE_TOKEN_CREDENTIAL``, else the
newest active Hugging Face / ModelScope credential.

The vault owns the secrets. This helper resolves a credential *by name* through
whatever ``app.services.vault.service`` exposes, falling back to a direct
``credentials`` row + ``decrypt_value`` when only the crypto helpers exist,
and to ``None`` when the vault is not available. Values are never logged.
"""

from __future__ import annotations

import uuid
from typing import Optional

from app.core.exceptions import ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


async def resolve_credential_value(session: AsyncSession, name: Optional[str]) -> Optional[str]:
    """Plaintext of the active vault credential called ``name`` (or None)."""
    if not name:
        return None
    try:
        from app.db.postgres.models.generated_models import Credentials
    except ImportError:  # pragma: no cover
        return None
    try:
        result = await session.execute(
            select(Credentials).where(
                func.lower(Credentials.name) == name.lower(),
                Credentials.deleted_at.is_(None),
                Credentials.status == "active",
            )
        )
        row = result.scalars().first()
    except Exception as exc:  # noqa: BLE001 - vault table may be mid-migration
        logger.warning(f"credential lookup failed for {name!r}: {type(exc).__name__}")
        return None
    if row is None:
        return None
    try:
        from app.services.vault import service as vault_service  # type: ignore
    except Exception:  # noqa: BLE001
        logger.warning(f"vault service unavailable; credential {name!r} cannot be decrypted")
        return None
    for attr in ("decrypt_value", "decrypt"):
        fn = getattr(vault_service, attr, None)
        if callable(fn):
            try:
                value = fn(row.encrypted_value)
                return value if isinstance(value, str) else None
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"credential {name!r} could not be decrypted: {type(exc).__name__}")
                return None
    svc_cls = getattr(vault_service, "VaultService", None)
    getter = getattr(svc_cls, "decrypt_value", None) if svc_cls else None
    if callable(getter):
        try:
            value = getter(row.encrypted_value)
            return value if isinstance(value, str) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"credential {name!r} could not be decrypted: {type(exc).__name__}")
    return None


def model_credential_id(model) -> Optional[uuid.UUID]:
    """``models.metadata.credential_id`` (the token credential picked at registration)."""
    meta = getattr(model, "metadata_", None)
    if not isinstance(meta, dict):
        meta = getattr(model, "metadata", None)
    raw = meta.get("credential_id") if isinstance(meta, dict) else None
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


async def require_source_credential(
    session: AsyncSession, source: str, credential_id: uuid.UUID
) -> None:
    """422 unless ``credential_id`` is an active token of the source's kind."""
    from app.services.vault import types as credential_kinds
    from app.services.vault.service import require_compatible

    accepted = credential_kinds.MODEL_SOURCE_KINDS.get(source)
    if not accepted:
        raise ValidationError(f"{source} models take no access token credential")
    label = "Hugging Face" if source == "huggingface" else "ModelScope"
    await require_compatible(session, credential_id, accepted, f"{label} access")


async def resolve_source_token(
    session: AsyncSession,
    source: str,
    credential_id: Optional[uuid.UUID] = None,
    *,
    purpose: str = "catalog",
) -> Optional[str]:
    """Token for a remote model source: the picked credential, else the one named by
    ``HF_TOKEN_CREDENTIAL`` / ``MODELSCOPE_TOKEN_CREDENTIAL``, else the newest active
    credential of that kind (so a token added on the Credentials page just works)."""
    from app.services.vault.service import VaultService

    vault = VaultService(session)
    if credential_id is not None:
        await require_source_credential(session, source, credential_id)
        return await vault.get_secret(credential_id, purpose=f"{source}:{purpose}")
    name = {
        "huggingface": settings.HF_TOKEN_CREDENTIAL,
        "modelscope": settings.MODELSCOPE_TOKEN_CREDENTIAL,
    }.get(source)
    token = await resolve_credential_value(session, name)
    if token:
        return token
    try:
        return await vault.get_secret_by_provider(
            source, purpose=f"{source}:{purpose}", required=False
        )
    except Exception as exc:  # noqa: BLE001 - a missing default token is not fatal
        logger.warning(f"default {source} token unavailable: {type(exc).__name__}")
        return None
