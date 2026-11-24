"""
OpenAI chat/completions and completions (pass-through; stream_options usage
injection: the usage chunk is forwarded unchanged when the client asked for it and stripped when
only the proxy requested it).
"""

from app.core.logger import get_logger
from app.services.serving.endpoint import serve
from app.services.serving.errors import ServingError
from app.services.serving.handlers_text import openai_passthrough
from fastapi import APIRouter, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: OpenAI"]


@router.post("/chat/completions", summary="Chat completions")
async def chat_completions(request: Request):
    async def handler(ctx, body):
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            raise ServingError.invalid_request("messages: field required", "messages")
        return await openai_passthrough(ctx, body, "v1/chat/completions")

    return await serve(request, "openai", "chat/completions", handler)


@router.post("/completions", summary="Text completions")
async def completions(request: Request):
    async def handler(ctx, body):
        if body.get("prompt") is None:
            raise ServingError.invalid_request("prompt: field required", "prompt")
        return await openai_passthrough(ctx, body, "v1/completions")

    return await serve(request, "openai", "completions", handler)
