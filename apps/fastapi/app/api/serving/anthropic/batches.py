"""
Anthropic Message Batches (serving.md §1).

Batches are persisted here and executed by the jobs process
(``app/workers/tasks/batches_tasks.py``); results stream back as JSONL. The
batch endpoints themselves are free — every item is metered when it runs.
"""

import asyncio
from typing import Optional

from app.core.logger import get_logger
from app.db.postgres.connection import get_db
from app.services.serving.batches import BatchService, batch_to_anthropic
from app.services.serving.endpoint import serve
from app.services.serving.formats import explicit_root
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse, StreamingResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: Anthropic"]


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/") + explicit_root("anthropic")


@router.post("/messages/batches", summary="Create a message batch")
async def create_batch(request: Request, db: AsyncSession = Depends(get_db, scope="function")):
    async def handler(ctx, body):
        ctx.metered = True
        service = BatchService(db)
        size = int(getattr(request.state, "body_size", 0) or 0)
        batch = await service.create(ctx.api_key.id, body, size)
        await db.commit()
        from app.workers.tasks.batches_tasks import enqueue_batch

        await asyncio.to_thread(enqueue_batch, str(batch.id))
        return JSONResponse(
            batch_to_anthropic(batch, _base_url(request)),
            headers=ctx.response_headers(),
        )

    return await serve(request, "anthropic", "messages/batches", handler)


@router.get("/messages/batches", summary="List message batches")
async def list_batches(
    request: Request,
    limit: int = Query(20, ge=1, le=1000),
    before_id: Optional[str] = Query(None),
    after_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    async def handler(ctx, _):
        ctx.metered = True
        rows, has_more = await BatchService(db).list_for_key(
            ctx.api_key.id, limit, before_id, after_id
        )
        data = [batch_to_anthropic(b, _base_url(request)) for b in rows]
        payload = {
            "data": data,
            "has_more": has_more,
            "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None,
        }
        return JSONResponse(payload, headers=ctx.response_headers())

    return await serve(request, "anthropic", "messages/batches", handler, body="none")


@router.get("/messages/batches/{batch_id}", summary="Retrieve a message batch")
async def get_batch(
    request: Request, batch_id: str, db: AsyncSession = Depends(get_db, scope="function")
):
    async def handler(ctx, _):
        ctx.metered = True
        batch = await BatchService(db).get_for_key(batch_id, ctx.api_key.id)
        return JSONResponse(
            batch_to_anthropic(batch, _base_url(request)),
            headers=ctx.response_headers(),
        )

    return await serve(request, "anthropic", "messages/batches", handler, body="none")


@router.get("/messages/batches/{batch_id}/results", summary="Stream batch results (JSONL)")
async def batch_results(
    request: Request, batch_id: str, db: AsyncSession = Depends(get_db, scope="function")
):
    async def handler(ctx, _):
        ctx.metered = True
        service = BatchService(db)
        batch = await service.get_for_key(batch_id, ctx.api_key.id)
        lines = await service.results_lines(batch)
        return StreamingResponse(
            lines, media_type="application/x-jsonl", headers=ctx.response_headers()
        )

    return await serve(request, "anthropic", "messages/batches", handler, body="none")


@router.post("/messages/batches/{batch_id}/cancel", summary="Cancel a message batch")
async def cancel_batch(
    request: Request, batch_id: str, db: AsyncSession = Depends(get_db, scope="function")
):
    async def handler(ctx, _):
        ctx.metered = True
        service = BatchService(db)
        batch = await service.cancel(await service.get_for_key(batch_id, ctx.api_key.id))
        return JSONResponse(
            batch_to_anthropic(batch, _base_url(request)),
            headers=ctx.response_headers(),
        )

    return await serve(request, "anthropic", "messages/batches", handler, body="none")


@router.delete("/messages/batches/{batch_id}", summary="Delete an ended message batch")
async def delete_batch(
    request: Request, batch_id: str, db: AsyncSession = Depends(get_db, scope="function")
):
    async def handler(ctx, _):
        ctx.metered = True
        service = BatchService(db)
        batch = await service.get_for_key(batch_id, ctx.api_key.id)
        await service.delete(batch)
        payload = {"id": batch.external_id, "type": "message_batch_deleted"}
        return JSONResponse(payload, headers=ctx.response_headers())

    return await serve(request, "anthropic", "messages/batches", handler, body="none")
