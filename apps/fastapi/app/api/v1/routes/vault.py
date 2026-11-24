"""
Vault routes (admin.yaml: Vault tag). Responses carry masked values and public
(non-secret) fields only.

GET    /vault                         list (type, provider[,provider], status, search, sort,
                                      page, include_system)
POST   /vault                         store a typed secret (provider + fields)          201
GET    /vault/metadata                type catalog (kinds + JSON Schemas), pickers, defaults
GET    /vault/stats                   counts (system credentials excluded)
GET    /vault/{credential_id}         get (masked, with usage counts)
PATCH  /vault/{credential_id}         update; secret fields left out keep their value
DELETE /vault/{credential_id}         soft-delete (409 CREDENTIAL_IN_USE)                204
POST   /vault/{credential_id}/reveal  plaintext once, audited + usage history row
POST   /vault/{credential_id}/test    offline check (decrypt, status, schema), audited
GET    /vault/{credential_id}/usage   what references it + recent use history

System credentials (internal CA, TLS keys) are hidden from the list unless
``include_system=true`` and refuse update / delete / reveal / test (403
CREDENTIAL_SYSTEM).
"""

import uuid
from typing import Optional

from app.core.exceptions import ValidationError
from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.vault_schema import (
    Credential,
    CredentialCreate,
    CredentialRevealed,
    CredentialStats,
    CredentialStatus,
    CredentialTestResult,
    CredentialType,
    CredentialUpdate,
    CredentialUsage,
    VaultMetadata,
)
from app.services.vault import VaultService
from app.services.vault import types as kinds
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Vault"]


def _check_kind(provider: Optional[str], type_: Optional[str]) -> None:
    """Admin writes may only create user kinds (never system CA / TLS material)."""
    if provider is not None and kinds.is_system(provider, None, None):
        raise ValidationError(f"'{provider}' credentials are managed by GPU Deck")
    if type_ in kinds.SYSTEM_TYPES:
        raise ValidationError("ca_key / tls_key credentials are managed by GPU Deck")
    if provider is not None and kinds.get_kind(provider) is None:
        raise ValidationError(
            f"Unknown credential type '{provider}'; one of {', '.join(sorted(kinds.KINDS))}"
        )


@router.get("/vault", summary="List credentials (masked)")
@handle_exceptions
async def list_credentials(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    type_: Optional[CredentialType] = Query(None, alias="type"),
    provider: Optional[str] = Query(None),
    status_filter: Optional[CredentialStatus] = Query(None, alias="status"),
    include_system: bool = Query(False),
    admin=Depends(get_current_admin),
    service: VaultService = Depends(VaultService.as_dependency),
):
    items, total = await service.list_credentials(
        page=page,
        page_size=page_size,
        type_=type_.value if type_ else None,
        provider=provider,
        status=status_filter.value if status_filter else None,
        search=search,
        sort=sort,
        include_system=include_system,
    )
    return success_response(
        data=[Credential.model_validate(i) for i in items],
        meta=paginated_meta(total, page, page_size),
    )


@router.post("/vault", summary="Store a secret", status_code=201)
@handle_exceptions
async def create_credential(
    request: Request,
    payload: CredentialCreate,
    admin=Depends(get_current_admin),
    service: VaultService = Depends(VaultService.as_dependency),
):
    data = payload.model_dump()
    data["type"] = payload.type.value if payload.type else None
    data["provider"] = payload.provider or "generic"
    _check_kind(data["provider"], data["type"])
    created = await service.create_credential(data, created_by=admin.id)
    return success_response(data=Credential.model_validate(await service.with_usage(created)))


@router.get("/vault/metadata", summary="Credential types (fields, JSON Schemas) for forms")
@handle_exceptions
async def get_vault_metadata(
    request: Request,
    admin=Depends(get_current_admin),
    service: VaultService = Depends(VaultService.as_dependency),
):
    return success_response(data=VaultMetadata.model_validate(await service.get_metadata()))


@router.get("/vault/stats", summary="Counts")
@handle_exceptions
async def get_vault_stats(
    request: Request,
    admin=Depends(get_current_admin),
    service: VaultService = Depends(VaultService.as_dependency),
):
    return success_response(data=CredentialStats.model_validate(await service.get_stats()))


@router.get("/vault/{credential_id}", summary="Get a credential (masked)")
@handle_exceptions
async def get_credential(
    request: Request,
    credential_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: VaultService = Depends(VaultService.as_dependency),
):
    return success_response(
        data=Credential.model_validate(await service.get_credential(credential_id))
    )


@router.patch("/vault/{credential_id}", summary="Update (secret fields left out are kept)")
@handle_exceptions
async def update_credential(
    request: Request,
    credential_id: uuid.UUID,
    payload: CredentialUpdate,
    admin=Depends(get_current_admin),
    service: VaultService = Depends(VaultService.as_dependency),
):
    data = payload.model_dump(exclude_unset=True)
    if payload.type is not None:
        data["type"] = payload.type.value
    _check_kind(data.get("provider"), data.get("type"))
    updated = await service.update_credential(credential_id, data, internal=False)
    return success_response(data=Credential.model_validate(updated))


@router.delete(
    "/vault/{credential_id}",
    summary="Soft-delete / revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def delete_credential(
    request: Request,
    credential_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: VaultService = Depends(VaultService.as_dependency),
):
    await service.delete_credential(credential_id, internal=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/vault/{credential_id}/reveal", summary="Reveal plaintext once (audited)")
@handle_exceptions
async def reveal_credential(
    request: Request,
    credential_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: VaultService = Depends(VaultService.as_dependency),
):
    revealed = await service.reveal_credential(credential_id, requested_by=admin.id, internal=False)
    return success_response(data=CredentialRevealed.model_validate(revealed))


@router.post("/vault/{credential_id}/test", summary="Check a credential offline (audited)")
@handle_exceptions
async def test_credential(
    request: Request,
    credential_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: VaultService = Depends(VaultService.as_dependency),
):
    result = await service.test_credential(credential_id, requested_by=admin.id)
    return success_response(data=CredentialTestResult.model_validate(result))


@router.get("/vault/{credential_id}/usage", summary="Where a credential is used")
@handle_exceptions
async def get_credential_usage(
    request: Request,
    credential_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: VaultService = Depends(VaultService.as_dependency),
):
    return success_response(data=CredentialUsage.model_validate(await service.usage(credential_id)))
