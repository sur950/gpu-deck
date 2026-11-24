"""OpenAI ``GET /models`` and ``GET /models/{model}`` from the in-memory routing table."""

from app.core.logger import get_logger
from app.services.serving import models_listing
from app.services.serving.endpoint import serve_public
from fastapi import APIRouter, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: OpenAI"]
BARE = False  # bare /v1/models is shared with Anthropic -> app/api/serving/_compat.py


@router.get("/models", summary="List models")
async def list_models(request: Request):
    async def payload():
        return models_listing.openai_models()

    return await serve_public(request, "openai", payload)


@router.get("/models/{model:path}", summary="Get a model")
async def get_model(request: Request, model: str):
    async def payload():
        return models_listing.openai_model_by_name(model)

    return await serve_public(request, "openai", payload)
