"""
OpenAI Responses API, translated to chat completions (stateless; ``store`` and
``background`` responses are kept in Redis for GET / DELETE / cancel).
"""

from app.core.logger import get_logger
from app.services.serving.endpoint import serve
from app.services.serving.errors import ServingError
from app.services.serving.handlers_text import (
    cancel_background_response,
    delete_response,
    load_response,
    openai_responses,
)
from app.services.serving.pipeline import json_response
from app.services.serving.usage import Usage
from fastapi import APIRouter, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: OpenAI"]


def _not_found(response_id: str) -> ServingError:
    return ServingError(404, "invalid_request_error", f"response '{response_id}' not found")


@router.post("/responses", summary="Create a response")
async def create_response(request: Request):
    return await serve(request, "openai", "responses", openai_responses)


@router.get("/responses/{response_id}", summary="Retrieve a stored / background response")
async def get_response(request: Request, response_id: str):
    async def handler(ctx, _):
        ctx.metered = True
        stored = await load_response(response_id, ctx.api_key.id)
        if stored is None:
            raise _not_found(response_id)
        return json_response(ctx, stored, Usage())

    return await serve(request, "openai", "responses", handler, body="none")


@router.delete("/responses/{response_id}", summary="Delete a stored response")
async def remove_response(request: Request, response_id: str):
    async def handler(ctx, _):
        ctx.metered = True
        if not await delete_response(response_id, ctx.api_key.id):
            raise _not_found(response_id)
        payload = {"id": response_id, "object": "response.deleted", "deleted": True}
        return json_response(ctx, payload, Usage())

    return await serve(request, "openai", "responses", handler, body="none")


@router.post("/responses/{response_id}/cancel", summary="Cancel a background response")
async def cancel_response(request: Request, response_id: str):
    async def handler(ctx, _):
        ctx.metered = True
        stored = await cancel_background_response(response_id, ctx.api_key.id)
        if stored is None:
            raise _not_found(response_id)
        return json_response(ctx, stored, Usage())

    return await serve(request, "openai", "responses", handler, body="none")
