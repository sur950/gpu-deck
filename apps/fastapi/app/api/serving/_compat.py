"""
Bare ``/v1/models`` and ``/v1/models/{model}`` shared by OpenAI and Anthropic
(serving.md §2 rule 2): ``anthropic-version`` header, or ``x-api-key`` without
``Authorization``, selects the Anthropic shape; otherwise OpenAI.

Not auto-discovered (leading underscore); mounted by ``app/api_layer_main.py``
when ``SERVING_ENABLE_BARE_PATHS``.
"""

from app.core.logger import get_logger
from app.services.serving import models_listing
from app.services.serving.endpoint import serve_public
from app.services.serving.formats import wants_anthropic_models
from fastapi import APIRouter, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: compat"]


@router.get("/v1/models", summary="List models (OpenAI or Anthropic shape by headers)")
async def list_models(request: Request):
    if wants_anthropic_models(request.headers):

        async def anthropic_payload():
            return models_listing.anthropic_models()

        return await serve_public(request, "anthropic", anthropic_payload)

    async def openai_payload():
        return models_listing.openai_models()

    return await serve_public(request, "openai", openai_payload)


@router.get("/v1/models/{model:path}", summary="Get a model (OpenAI or Anthropic shape)")
async def get_model(request: Request, model: str):
    if wants_anthropic_models(request.headers):

        async def anthropic_payload():
            return models_listing.anthropic_model_by_name(model)

        return await serve_public(request, "anthropic", anthropic_payload)

    async def openai_payload():
        return models_listing.openai_model_by_name(model)

    return await serve_public(request, "openai", openai_payload)
