"""
Anthropic Message Batches (serving.md §1, Anthropic Batches API shapes).

``POST /v1/messages/batches`` persists the batch and its items; the jobs
process (``batches_tasks.process_batch``) runs every item through the same
``/v1/messages`` pipeline and stores the Anthropic result object per item;
results stream back as JSONL. Admin listing lives in ``routes/batches.py``.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.repositories.batch_repository import (
    BatchItemRepository,
    BatchRepository,
)
from app.services.serving.errors import ServingError
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

PROCESSING_STATUS = {
    "in_progress": "in_progress",
    "canceling": "canceling",
    "ended": "ended",
}


def new_batch_id() -> str:
    return f"msgbatch_{secrets.token_hex(12)}"


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def batch_to_anthropic(batch, base_url: str) -> Dict[str, Any]:
    counts = dict(batch.request_counts or {})
    for key in ("processing", "succeeded", "errored", "canceled", "expired"):
        counts.setdefault(key, 0)
    ended = batch.status == "ended"
    return {
        "id": batch.external_id,
        "type": "message_batch",
        "processing_status": PROCESSING_STATUS.get(batch.status, batch.status),
        "request_counts": counts,
        "ended_at": _iso(batch.ended_at),
        "created_at": _iso(batch.created_at),
        "expires_at": _iso(batch.expires_at),
        "archived_at": _iso(batch.archived_at),
        "cancel_initiated_at": _iso(batch.cancel_initiated_at),
        "results_url": (
            f"{base_url}/v1/messages/batches/{batch.external_id}/results" if ended else None
        ),
    }


class BatchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.batches = BatchRepository(session)
        self.items = BatchItemRepository(session)

    async def create(self, api_key_id: Optional[str], body: Dict[str, Any], body_size: int) -> Any:
        requests = body.get("requests")
        if not isinstance(requests, list) or not requests:
            raise ServingError.invalid_request("requests: must be a non-empty list", "requests")
        if len(requests) > settings.SERVING_BATCH_MAX_ITEMS:
            raise ServingError(
                413,
                "request_too_large",
                f"a batch may hold at most {settings.SERVING_BATCH_MAX_ITEMS} requests",
            )
        if body_size > settings.SERVING_BATCH_MAX_BYTES:
            raise ServingError.request_too_large(settings.SERVING_BATCH_MAX_BYTES)
        seen = set()
        for idx, req in enumerate(requests):
            if (
                not isinstance(req, dict)
                or not req.get("custom_id")
                or not isinstance(req.get("params"), dict)
            ):
                raise ServingError.invalid_request(
                    f"requests[{idx}]: custom_id and params are required", "requests"
                )
            if req["custom_id"] in seen:
                raise ServingError.invalid_request(
                    f"duplicate custom_id '{req['custom_id']}'", "requests"
                )
            seen.add(req["custom_id"])
            params = req["params"]
            if not params.get("model"):
                raise ServingError.invalid_request(
                    f"requests[{idx}].params.model: field required", "requests"
                )
            if not params.get("messages"):
                raise ServingError.invalid_request(
                    f"requests[{idx}].params.messages: field required", "requests"
                )
            if params.get("max_tokens") is None:
                raise ServingError.invalid_request(
                    f"requests[{idx}].params.max_tokens: field required", "requests"
                )
        now = datetime.now(timezone.utc)
        batch = await self.batches.create(
            external_id=new_batch_id(),
            api_key_id=api_key_id,
            format="anthropic",
            status="in_progress",
            request_counts={
                "processing": len(requests),
                "succeeded": 0,
                "errored": 0,
                "canceled": 0,
                "expired": 0,
            },
            total_items=len(requests),
            expires_at=now + timedelta(hours=settings.SERVING_BATCH_EXPIRES_HOURS),
        )
        await self.items.bulk_create(
            [
                {
                    "batch_id": batch.id,
                    "custom_id": str(req["custom_id"]),
                    "model_name": str(req["params"].get("model")),
                    "request": req["params"],
                    "status": "pending",
                }
                for req in requests
            ]
        )
        return batch

    async def get_for_key(self, external_id: str, api_key_id: Optional[str]):
        batch = await self.batches.get_by_external_id(external_id)
        if batch is None or (
            api_key_id and batch.api_key_id and str(batch.api_key_id) != str(api_key_id)
        ):
            raise ServingError(404, "not_found_error", f"batch '{external_id}' not found")
        return batch

    async def list_for_key(
        self,
        api_key_id: Optional[str],
        limit: int,
        before_id: Optional[str],
        after_id: Optional[str],
    ):
        return await self.batches.list_for_key(api_key_id, limit, before_id, after_id)

    async def cancel(self, batch) -> Any:
        """Cancel pending items; the batch ends once in-flight items finish."""
        if batch.status == "ended":
            return batch
        now = datetime.now(timezone.utc)
        await self.batches.update(batch.id, status="canceling", cancel_initiated_at=now)
        await self.items.cancel_pending(batch.id)
        return await self.batches.refresh_counts(batch.id)

    async def delete(self, batch) -> None:
        if batch.status != "ended":
            raise ServingError.invalid_request("only ended batches can be deleted (cancel first)")
        await self.batches.delete(batch.id)

    async def results_lines(self, batch) -> AsyncIterator[bytes]:
        """JSONL result lines; validates now, streams later from its own DB session."""
        if batch.status != "ended":
            raise ServingError(404, "not_found_error", "results are not ready for this batch")
        return iter_result_lines(batch.id)


def item_result(item) -> Optional[Dict[str, Any]]:
    """The Anthropic ``result`` object of one finished item (None while pending)."""
    if item.status == "succeeded":
        return {"type": "succeeded", "message": item.result}
    if item.status == "errored":
        default = {
            "type": "error",
            "error": {"type": "api_error", "message": "unknown error"},
        }
        return {"type": "errored", "error": item.error or default}
    if item.status == "canceled":
        return {"type": "canceled"}
    if item.status == "expired":
        return {"type": "expired"}
    return None


async def iter_result_lines(batch_id, page_size: int = 500) -> AsyncIterator[bytes]:
    from app.db.postgres.connection import get_session_maker

    after = None
    while True:
        async with get_session_maker()() as session:
            page = await BatchItemRepository(session).page(batch_id, after, page_size)
            await session.commit()  # read-only; commit keeps the rows loaded
        if not page:
            return
        for item in page:
            result = item_result(item)
            if result is not None:
                line = {"custom_id": item.custom_id, "result": result}
                yield (json.dumps(line, ensure_ascii=False) + "\n").encode()
        if len(page) < page_size:
            return
        after = page[-1].id


TRANSIENT_STATUSES = {429, 502, 503, 504}


async def run_batch_item(
    params: Dict[str, Any], api_key_ctx, request_id: str, batch_external_id: str = ""
) -> Dict[str, Any]:
    """Execute one batch item through the messages pipeline (non-streaming), in the jobs process.

    Returns ``{"status": http_status, "body": anthropic_json}``; the item's usage row is
    enqueued like any other request (``endpoint=messages/batches``, ``metadata.batch_id``).
    """
    from app.services.serving.context import ServingContext
    from app.services.serving.errors import error_body
    from app.services.serving.handlers_text import anthropic_messages
    from app.services.serving.usage import Usage
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/anthropic/v1/messages",
        "headers": [(b"anthropic-version", settings.SERVING_ANTHROPIC_DEFAULT_VERSION.encode())],
        "query_string": b"",
        "client": ("127.0.0.1", 0),
        "server": ("batch", 0),
        "scheme": "http",
    }
    request = Request(scope)
    request.state.request_id = request_id
    request.state.client_ip = None
    ctx = ServingContext(
        request=request,
        fmt="anthropic",
        endpoint="messages/batches",
        api_key=api_key_ctx,
        request_id=request_id,
    )
    ctx.metadata["batch_id"] = batch_external_id
    ctx.model = str(params.get("model") or "")
    try:
        response = await anthropic_messages(ctx, {**params, "stream": False})
    except ServingError as err:
        from app.services.metering.service import record_request

        await record_request(ctx, Usage(), "error", err.status_code, err.code)
        return {
            "status": err.status_code,
            "body": error_body("anthropic", err.status_code, err.code, err.message),
        }
    body = json.loads(response.body) if getattr(response, "body", None) else {}
    if response.background is not None:
        await response.background()
    return {"status": response.status_code, "body": body}


def anthropic_error_result(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(body, dict) and body.get("type") == "error":
        return body
    message = None
    if isinstance(body, dict):
        inner = body.get("error")
        message = inner.get("message") if isinstance(inner, dict) else inner
    from app.services.serving.errors import _ANTHROPIC_TYPES

    return {
        "type": "error",
        "error": {
            "type": _ANTHROPIC_TYPES.get(status, "api_error"),
            "message": str(message or "error"),
        },
    }


async def process_batch(batch_id: str, max_rounds: int = 1000) -> Dict[str, int]:
    """Run pending items of one batch (jobs process). Safe to run concurrently."""
    import asyncio
    import uuid as _uuid

    from app.db.postgres.connection import get_session_maker
    from app.services.serving.auth import ApiKeyContext
    from app.services.serving.routing import RoutingRefresher, get_routing_table
    from app.services.serving.upstream import scoped_upstream_client

    table = get_routing_table()
    await RoutingRefresher(table).load_once()
    stats = {"succeeded": 0, "errored": 0, "requeued": 0}
    bid = _uuid.UUID(str(batch_id))
    maker = get_session_maker()
    async with scoped_upstream_client(table.ca_bundle_pem):
        for _ in range(max_rounds):
            async with maker() as session:
                batches = BatchRepository(session)
                items_repo = BatchItemRepository(session)
                batch = await batches.get_by_id(bid)
                if batch is None or batch.status == "ended":
                    await session.commit()
                    break
                now = datetime.now(timezone.utc)
                if batch.status == "canceling":
                    await items_repo.cancel_pending(bid)
                    await batches.refresh_counts(bid)
                    await session.commit()
                    break
                if batch.expires_at and batch.expires_at <= now:
                    await items_repo.expire_pending(bid)
                    await batches.refresh_counts(bid)
                    await session.commit()
                    break
                stale = now - timedelta(seconds=settings.SERVING_REQUEST_TIMEOUT_SECONDS * 2)
                await items_repo.requeue_stale(bid, stale)
                items = await items_repo.claim(bid, max(1, settings.SERVING_BATCH_CONCURRENCY))
                external_id = batch.external_id
                key_ctx = ApiKeyContext(
                    id=str(batch.api_key_id) if batch.api_key_id else "", name="batch"
                )
                await session.commit()
            if not items:
                async with maker() as session:
                    await BatchRepository(session).refresh_counts(bid)
                    await session.commit()
                break
            outcomes = await asyncio.gather(
                *(
                    run_batch_item(
                        dict(item.request or {}),
                        key_ctx,
                        str(_uuid.uuid4()),
                        external_id,
                    )
                    for item in items
                ),
                return_exceptions=True,
            )
            requeued = 0
            async with maker() as session:
                items_repo = BatchItemRepository(session)
                for item, outcome in zip(items, outcomes):
                    if isinstance(outcome, BaseException):
                        logger.opt(exception=outcome).error(f"batch item {item.id} crashed")
                        outcome = {
                            "status": 500,
                            "body": {
                                "type": "error",
                                "error": {
                                    "type": "api_error",
                                    "message": "internal error",
                                },
                            },
                        }
                    status = int(outcome["status"])
                    body = outcome["body"] or {}
                    if status < 400:
                        usage = body.get("usage") or {}
                        await items_repo.finish(
                            item.id,
                            "succeeded",
                            result=body,
                            input_tokens=int(usage.get("input_tokens") or 0),
                            output_tokens=int(usage.get("output_tokens") or 0),
                        )
                        stats["succeeded"] += 1
                    elif status in TRANSIENT_STATUSES:
                        await _requeue(items_repo, item.id)
                        requeued += 1
                    else:
                        await items_repo.finish(
                            item.id,
                            "errored",
                            error=anthropic_error_result(status, body),
                        )
                        stats["errored"] += 1
                await BatchRepository(session).refresh_counts(bid)
                await session.commit()
            stats["requeued"] += requeued
            if requeued == len(items):
                # Model not ready / busy: back off and let the dispatcher come back later.
                await asyncio.sleep(2)
                break
    return stats


async def _requeue(items_repo: BatchItemRepository, item_id) -> None:
    from app.db.postgres.models.generated_models import BatchItems
    from sqlalchemy import update

    await items_repo.session.execute(
        update(BatchItems).where(BatchItems.id == item_id).values(status="pending", started_at=None)
    )
    await items_repo.session.flush()


__all__ = [
    "BatchService",
    "anthropic_error_result",
    "batch_to_anthropic",
    "process_batch",
    "run_batch_item",
]
