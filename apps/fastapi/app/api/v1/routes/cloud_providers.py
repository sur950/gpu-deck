"""
Cloud provider catalog routes (admin.yaml: Cloud tag).

GET  /cloud/providers                                   supported providers + credential formats
POST /cloud/providers/{provider}/check-credential       verify a vault credential against the API
GET  /cloud/providers/{provider}/regions                regions offering GPU VMs
GET  /cloud/providers/{provider}/instance-types         GPU instance types (model/count/VRAM/price)
GET  /cloud/providers/{provider}/quotas                 GPU / vCPU quotas of a region

Browsing takes ``credential_id`` (vault) or ``cluster_id`` (its credential).
Listings are cached (``refresh=true`` re-reads the provider).
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.core.rate_limiter import rate_limit
from app.middlewares.session_verification import get_current_admin
from app.schemas.cloud_schema import (
    CloudCredentialCheckRequest,
    CloudCredentialCheckResponse,
    CloudInstanceTypeResponse,
    CloudProviderInfo,
    CloudProviderName,
    CloudQuotaResponse,
    CloudRegionResponse,
)
from app.services.cloud import catalog
from app.services.cloud.service import CloudService
from app.utils.api_wrapper import handle_exceptions, success_response
from fastapi import APIRouter, Depends, Query, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Cloud"]


@router.get("/cloud/providers", summary="List cloud providers")
@handle_exceptions
async def list_cloud_providers(request: Request, admin=Depends(get_current_admin)):
    return success_response(
        data=[CloudProviderInfo.model_validate(p) for p in catalog.provider_infos()]
    )


@router.post("/cloud/providers/{provider}/check-credential", summary="Verify a cloud credential")
@rate_limit("20/minute")
@handle_exceptions
async def check_cloud_credential(
    request: Request,
    provider: CloudProviderName,
    payload: CloudCredentialCheckRequest,
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    result = await service.check_credential(provider, payload.credential_id)
    return success_response(data=CloudCredentialCheckResponse.model_validate(result))


@router.get("/cloud/providers/{provider}/regions", summary="Regions with GPU VMs")
@handle_exceptions
async def list_cloud_regions(
    request: Request,
    provider: CloudProviderName,
    credential_id: Optional[uuid.UUID] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    refresh: bool = Query(False),
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    items = await service.regions(provider, credential_id, cluster_id, refresh)
    return success_response(
        data=[CloudRegionResponse.model_validate(i) for i in items],
        meta={"total": len(items)},
    )


@router.get("/cloud/providers/{provider}/instance-types", summary="GPU instance types")
@handle_exceptions
async def list_cloud_instance_types(
    request: Request,
    provider: CloudProviderName,
    region: str = Query(..., min_length=1, max_length=100),
    credential_id: Optional[uuid.UUID] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    gpu_vendor: Optional[str] = Query(None, max_length=32),
    min_vram_gb: Optional[float] = Query(None, ge=0),
    gpu_count: Optional[int] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    search: Optional[str] = Query(None, max_length=100),
    refresh: bool = Query(False),
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    items = await service.instance_types(
        provider,
        region,
        credential_id,
        cluster_id,
        refresh,
        gpu_vendor=gpu_vendor,
        min_vram_gb=min_vram_gb,
        gpu_count=gpu_count,
        search=search,
        max_price=max_price,
    )
    return success_response(
        data=[CloudInstanceTypeResponse.model_validate(i) for i in items],
        meta={"total": len(items)},
    )


@router.get("/cloud/providers/{provider}/quotas", summary="GPU quotas of a region")
@handle_exceptions
async def list_cloud_quotas(
    request: Request,
    provider: CloudProviderName,
    region: str = Query(..., min_length=1, max_length=100),
    credential_id: Optional[uuid.UUID] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    items = await service.quotas(provider, region, credential_id, cluster_id)
    return success_response(data=[CloudQuotaResponse.model_validate(i) for i in items])
