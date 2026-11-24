"""OpenAI audio: transcriptions / translations (multipart, raw pass-through) and speech."""

from app.core.logger import get_logger
from app.services.serving.endpoint import serve
from app.services.serving.handlers_media import openai_audio_speech, openai_audio_upload
from fastapi import APIRouter, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Serving: OpenAI"]


@router.post("/audio/transcriptions", summary="Speech to text")
async def transcriptions(request: Request):
    async def handler(ctx, raw: bytes):
        return await openai_audio_upload(ctx, raw, request.headers.get("content-type", ""))

    return await serve(request, "openai", "audio/transcriptions", handler, body="raw")


@router.post("/audio/translations", summary="Speech to English text")
async def translations(request: Request):
    async def handler(ctx, raw: bytes):
        content_type = request.headers.get("content-type", "")
        return await openai_audio_upload(ctx, raw, content_type, translation=True)

    return await serve(request, "openai", "audio/translations", handler, body="raw")


@router.post("/audio/speech", summary="Text to speech")
async def speech(request: Request):
    return await serve(request, "openai", "audio/speech", openai_audio_speech)
