"""OpenAI embeddings and rerank (Jina/Cohere-style body, also ``/reranking``)."""

from app.core.logger import get_logger
from app.services.serving.endpoint import serve
from app.services.serving.handlers_media import openai_embeddings, openai_rerank
from fastapi import APIRouter, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: OpenAI"]


@router.post("/embeddings", summary="Embeddings")
async def embeddings(request: Request):
    return await serve(request, "openai", "embeddings", openai_embeddings)


@router.post("/rerank", summary="Rerank documents")
async def rerank(request: Request):
    return await serve(request, "openai", "rerank", openai_rerank)


@router.post("/reranking", summary="Rerank documents (alias)")
async def reranking(request: Request):
    return await serve(request, "openai", "rerank", openai_rerank)
