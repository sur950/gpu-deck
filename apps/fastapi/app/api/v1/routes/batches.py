"""
Admin view of Anthropic message batches (admin.yaml: Batches tag).

GET  /batches                     list (api_key_id, status filters)
GET  /batches/{batch_id}          get (uuid or msgbatch_ id)
POST /batches/{batch_id}/cancel   cancel pending items
"""

import uuid
from typing import Optional

from app.core.exceptions import ResourceNotFoundError
from app.core.logger import get_logger
from app.db.postgres.connection import get_db
from app.db.postgres.repositories.batch_repository import BatchRepository
from app.middlewares.session_verification import get_current_admin
from app.schemas.batches_schema import Batch, BatchStatus
from app.services.audit.service import audit
from app.services.serving.batches import BatchService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Batches"]


def _dump(batch) -> dict:
    return Batch.model_validate(batch).model_dump(mode="json")


async def _get(db: AsyncSession, batch_id: str):
    batch = await BatchRepository(db).get_by_any_id(batch_id)
    if batch is None:
        raise ResourceNotFoundError(resource_type="Batch")
    return batch


@router.get("/batches", summary="Anthropic message batches (admin view)")
@handle_exceptions
async def list_batches(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    api_key_id: Optional[uuid.UUID] = Query(None),
    status: Optional[BatchStatus] = Query(None),
    admin=Depends(get_current_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    rows, total = await BatchRepository(db).list_admin(
        page, page_size, api_key_id, status.value if status else None
    )
    return success_response(
        data=[_dump(b) for b in rows], meta=paginated_meta(total, page, page_size)
    )


@router.get("/batches/{batch_id}", summary="Get a batch")
@handle_exceptions
async def get_batch(
    request: Request,
    batch_id: str,
    admin=Depends(get_current_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    return success_response(data=_dump(await _get(db, batch_id)))


@router.post("/batches/{batch_id}/cancel", summary="Cancel a batch")
@handle_exceptions
async def cancel_batch(
    request: Request,
    batch_id: str,
    admin=Depends(get_current_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    batch = await BatchService(db).cancel(await _get(db, batch_id))
    audit.record(
        "batch.cancel",
        resource_type="batch",
        resource_id=batch.id,
        resource_name=batch.external_id,
    )
    return success_response(data=_dump(batch))
