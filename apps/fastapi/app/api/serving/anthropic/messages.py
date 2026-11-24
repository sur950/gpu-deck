"""
Anthropic ``/v1/messages`` (streaming, tools, thinking, images, documents,
compaction / context management) and ``/v1/messages/count_tokens``.

Translated to OpenAI chat completions unless the engine speaks Anthropic
natively (routing table ``native_formats``).
"""

from app.core.logger import get_logger
from app.services.serving.endpoint import serve
from app.services.serving.handlers_text import (
    anthropic_count_tokens,
    anthropic_messages,
)
from fastapi import APIRouter, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: Anthropic"]


@router.post("/messages", summary="Create a message")
async def create_message(request: Request):
    return await serve(request, "anthropic", "messages", anthropic_messages)


@router.post("/messages/count_tokens", summary="Count input tokens (free)")
async def count_tokens(request: Request):
    return await serve(request, "anthropic", "messages/count_tokens", anthropic_count_tokens)
