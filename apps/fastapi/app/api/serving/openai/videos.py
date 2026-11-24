"""OpenAI videos (async jobs pinned to the instance that accepted them)."""

from app.core.logger import get_logger
from app.services.serving.endpoint import serve
from app.services.serving.handlers_media import (
    openai_videos_content,
    openai_videos_create,
    openai_videos_delete,
    openai_videos_get,
    openai_videos_list,
)
from fastapi import APIRouter, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: OpenAI"]


@router.post("/videos", summary="Create a video job")
async def create_video(request: Request):
    return await serve(request, "openai", "videos", openai_videos_create)


@router.get("/videos", summary="List video jobs")
async def list_videos(request: Request):
    async def handler(ctx, _):
        return await openai_videos_list(ctx)

    return await serve(request, "openai", "videos", handler, body="none")


@router.get("/videos/{video_id}", summary="Get a video job")
async def get_video(request: Request, video_id: str):
    async def handler(ctx, _):
        return await openai_videos_get(ctx, video_id)

    return await serve(request, "openai", "videos", handler, body="none")


@router.get("/videos/{video_id}/content", summary="Download video content")
async def video_content(request: Request, video_id: str):
    async def handler(ctx, _):
        return await openai_videos_content(ctx, video_id)

    return await serve(request, "openai", "videos", handler, body="none")


@router.delete("/videos/{video_id}", summary="Delete a video job")
async def delete_video(request: Request, video_id: str):
    async def handler(ctx, _):
        return await openai_videos_delete(ctx, video_id)

    return await serve(request, "openai", "videos", handler, body="none")
