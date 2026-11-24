"""
Ollama API: chat, generate, embed (+ legacy embeddings), tags, show, ps,
version. Model management endpoints (pull/push/create/delete/copy/blobs) are
answered with 404 — models are managed in the control plane.
"""

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.serving import models_listing
from app.services.serving.endpoint import render_error, serve, serve_public
from app.services.serving.errors import ServingError
from app.services.serving.handlers_text import (
    ollama_chat,
    ollama_embed,
    ollama_generate,
)
from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: Ollama"]

OLLAMA_COMPAT_VERSION = "0.12.0"


@router.post("/chat", summary="Chat")
async def chat(request: Request):
    return await serve(request, "ollama", "api/chat", ollama_chat)


@router.post("/generate", summary="Generate")
async def generate(request: Request):
    return await serve(request, "ollama", "api/generate", ollama_generate)


@router.post("/embed", summary="Embeddings")
async def embed(request: Request):
    return await serve(request, "ollama", "api/embed", ollama_embed)


@router.post("/embeddings", summary="Embeddings (legacy)")
async def embeddings(request: Request):
    async def handler(ctx, body):
        return await ollama_embed(ctx, body, legacy=True)

    return await serve(request, "ollama", "api/embed", handler)


@router.get("/tags", summary="List models")
async def tags(request: Request):
    async def payload():
        return models_listing.ollama_tags_payload()

    return await serve_public(request, "ollama", payload)


@router.get("/ps", summary="Loaded models")
async def ps(request: Request):
    async def payload():
        return models_listing.ollama_ps_payload()

    return await serve_public(request, "ollama", payload)


@router.post("/show", summary="Model details")
async def show(request: Request):
    async def handler(ctx, body):
        ctx.metered = True
        name = body.get("model") or body.get("name")
        if not name:
            raise ServingError.invalid_request("model is required", "model")
        return JSONResponse(
            models_listing.ollama_show_payload(str(name)),
            headers=ctx.response_headers(),
        )

    return await serve(request, "ollama", "api/show", handler)


@router.get("/version", summary="Server version")
async def version(request: Request):
    return JSONResponse({"version": OLLAMA_COMPAT_VERSION, "gpudeck": settings.APP_VERSION})


MODEL_MANAGEMENT = ("pull", "push", "create", "delete", "copy", "blobs/{digest}")


async def unsupported(request: Request):
    err = ServingError(
        404,
        "unsupported_endpoint",
        "not supported by GPU Deck; manage models in the control plane",
    )
    return render_error("ollama", err)


for _path in MODEL_MANAGEMENT:
    router.add_api_route(
        f"/{_path}",
        unsupported,
        methods=["GET", "POST", "PUT", "DELETE", "HEAD"],
        include_in_schema=False,
    )
