"""Anthropic ``GET /v1/models`` and ``GET /v1/models/{model}``."""

from app.core.logger import get_logger
from app.services.serving import models_listing
from app.services.serving.endpoint import serve_public
from fastapi import APIRouter, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: Anthropic"]
BARE = False  # bare /v1/models is shared with OpenAI -> app/api/serving/_compat.py


@router.get("/models", summary="List models")
async def list_models(request: Request):
    async def payload():
        return models_listing.anthropic_models()

    return await serve_public(request, "anthropic", payload)


@router.get("/models/{model:path}", summary="Get a model")
async def get_model(request: Request, model: str):
    async def payload():
        return models_listing.anthropic_model_by_name(model)

    return await serve_public(request, "anthropic", payload)
