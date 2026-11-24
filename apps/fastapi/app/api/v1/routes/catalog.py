"""
Catalog source routes (admin.yaml tag "Catalog", remote part).

GET  /catalog/search        — search Hugging Face / ModelScope
GET  /catalog/remote-files  — files of a remote repo with sizes, hashes, format, suggestions
POST /catalog/local/scan    — scan a local path (plain, Ollama blobs, LM Studio, HF cache, MLX)
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.schemas.catalog_schema import (
    CatalogSort,
    LocalScanRequest,
    ModelFileFormat,
    ModelType,
    RemoteSource,
)
from app.services.catalog.auth import get_current_admin
from app.services.catalog.service import CatalogService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Catalog"]


@router.get("/catalog/search", summary="Search Hugging Face / ModelScope")
@handle_exceptions
async def search_catalog(
    request: Request,
    source: RemoteSource = Query(...),
    q: str = Query(..., min_length=1),
    type: Optional[ModelType] = Query(None),
    format: Optional[ModelFileFormat] = Query(None),
    author: Optional[str] = Query(None),
    sort: CatalogSort = Query(CatalogSort.DOWNLOADS),
    limit: int = Query(25, ge=1, le=100),
    credential_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    """Search a remote source; results flag repos already in the catalog. ``credential_id``
    picks the vault token (else the default token)."""
    rows = await service.search(
        source.value,
        q,
        model_type=type.value if type else None,
        fmt=format.value if format else None,
        author=author,
        sort=sort.value,
        limit=limit,
        credential_id=credential_id,
    )
    return success_response(data=rows, meta=paginated_meta(len(rows), 1, limit))


@router.get("/catalog/remote-files", summary="List files of a remote repo")
@handle_exceptions
async def list_remote_files(
    request: Request,
    source: RemoteSource = Query(...),
    repo_id: str = Query(..., min_length=1),
    revision: Optional[str] = Query(None),
    credential_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    """Files with sizes, sha256, format and suggested settings for a repo (gated repos need
    a token: ``credential_id`` or the default)."""
    data = await service.remote_files(source.value, repo_id, revision, credential_id)
    return success_response(data=data)


@router.post("/catalog/local/scan", summary="Scan a local path for model files")
@handle_exceptions
async def scan_local_path(
    request: Request,
    body: LocalScanRequest,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    """Find model candidates under a path on the control-plane host."""
    rows = await service.scan_local(body)
    return success_response(data=rows, meta=paginated_meta(len(rows), 1, max(len(rows), 1)))
