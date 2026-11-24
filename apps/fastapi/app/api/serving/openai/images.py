"""OpenAI images: generations (JSON) and edits (multipart)."""

from app.core.logger import get_logger
from app.services.serving.endpoint import serve
from app.services.serving.handlers_media import (
    openai_images_edits,
    openai_images_generations,
)
from fastapi import APIRouter, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: OpenAI"]


@router.post("/images/generations", summary="Generate images")
async def generations(request: Request):
    return await serve(request, "openai", "images/generations", openai_images_generations)


@router.post("/images/edits", summary="Edit images")
async def edits(request: Request):
    async def handler(ctx, raw: bytes):
        return await openai_images_edits(ctx, raw, request.headers.get("content-type", ""))

    return await serve(request, "openai", "images/edits", handler, body="raw")
