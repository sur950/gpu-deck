"""
Media / vector endpoints: embeddings, rerank, audio transcriptions /
translations / speech, image generations / edits, videos.

Engines speak OpenAI for all of these except whisper.cpp (``/inference``) and
stable-diffusion.cpp (``/txt2img`` / ``/img2img``), whose bodies the API layer
translates. Multipart uploads are forwarded as raw bytes (the ``model`` field
is read from the multipart body without re-encoding it). Video jobs are
async: the job id -> instance mapping lives in Redis so follow-up calls reach
the same engine.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Dict, Optional, Tuple

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.serving.context import ServingContext
from app.services.serving.errors import ServingError
from app.services.serving.pipeline import (
    bytes_response,
    get_engine,
    json_response,
    passthrough_error_response,
)
from app.services.serving.routing import RouteTarget
from app.services.serving.usage import (
    Usage,
    audio_seconds_from_bytes,
    estimate_tokens,
    extract_openai_usage,
    wav_duration_seconds,
)
from starlette.responses import Response

logger = get_logger(__name__)


async def _error_or_none(ctx: ServingContext, result) -> Optional[Response]:
    if result.status_code < 400:
        return None
    data = await result.read_bytes()
    return passthrough_error_response(
        ctx,
        result.status_code,
        data,
        result.headers.get("content-type", "application/json"),
    )


# ---------------------------------------------------------------------------
# multipart helpers (raw pass-through, no re-encoding)
# ---------------------------------------------------------------------------


def multipart_boundary(content_type: str) -> Optional[bytes]:
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    return match.group(1).encode() if match else None


def multipart_fields(body: bytes, boundary: bytes) -> Dict[str, Tuple[Dict[str, str], bytes]]:
    """{name: (headers, data)} for every part (data of file parts included)."""
    parts: Dict[str, Tuple[Dict[str, str], bytes]] = {}
    delimiter = b"--" + boundary
    for raw in body.split(delimiter)[1:]:
        if raw.startswith(b"--"):
            break
        raw = raw.lstrip(b"\r\n")
        head, _, data = raw.partition(b"\r\n\r\n")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        headers: Dict[str, str] = {}
        for line in head.decode("utf-8", "replace").split("\r\n"):
            k, _, v = line.partition(":")
            if k:
                headers[k.strip().lower()] = v.strip()
        disp = headers.get("content-disposition", "")
        m = re.search(r'name="([^"]*)"', disp)
        if m:
            parts[m.group(1)] = (headers, data)
    return parts


def multipart_text(parts: Dict[str, Tuple[Dict[str, str], bytes]], name: str) -> Optional[str]:
    part = parts.get(name)
    if part is None:
        return None
    return part[1].decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# embeddings / rerank
# ---------------------------------------------------------------------------


def _check_items(value: Any, param: str) -> None:
    if isinstance(value, list) and len(value) > settings.SERVING_MAX_INPUT_ITEMS:
        raise ServingError(
            413,
            "request_too_large",
            f"{param} has more than {settings.SERVING_MAX_INPUT_ITEMS} items",
            param=param,
        )


async def openai_embeddings(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    if body.get("input") is None:
        raise ServingError.invalid_request("input: field required", "input")
    _check_items(body.get("input"), "input")
    result = await get_engine().open(ctx, "v1/embeddings", body, stream=False)
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    data = await result.read_json()
    return json_response(ctx, data, extract_openai_usage(data, "embeddings"))


async def openai_rerank(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    if body.get("query") is None or body.get("documents") is None:
        raise ServingError.invalid_request("query and documents are required", "documents")
    _check_items(body.get("documents"), "documents")
    result = await get_engine().open(ctx, "v1/rerank", body, stream=False)
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    data = await result.read_json()
    usage = extract_openai_usage(data, "rerank")
    if not usage.input_tokens:
        usage.input_tokens = estimate_tokens(str(body.get("query", ""))) + sum(
            estimate_tokens(d if isinstance(d, str) else json.dumps(d))
            for d in body.get("documents") or []
        )
        usage.estimated = True
    return json_response(ctx, data, usage)


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------


async def openai_audio_upload(
    ctx: ServingContext, raw: bytes, content_type: str, translation: bool = False
) -> Response:
    boundary = multipart_boundary(content_type)
    if boundary is None:
        raise ServingError(415, "invalid_request_error", "multipart/form-data body required")
    parts = multipart_fields(raw, boundary)
    model = multipart_text(parts, "model")
    if not model:
        raise ServingError.invalid_request("model: field required", "model")
    ctx.model = model
    engine = get_engine()
    target = await engine.resolve(ctx, model)
    path = "v1/audio/translations" if translation else "v1/audio/transcriptions"
    if target.instance.engine == "whisper_cpp":
        path = "inference"
    result = await engine.open(
        ctx,
        path,
        content=raw,
        headers={"Content-Type": content_type},
        stream=False,
        model=model,
    )
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    data = await result.read_bytes()
    usage = Usage()
    file_part = parts.get("file")
    seconds = wav_duration_seconds(file_part[1]) if file_part else None
    ctype = result.headers.get("content-type", "application/json")
    if "json" in ctype:
        try:
            parsed = json.loads(data)
            u = parsed.get("usage") or {}
            usage.input_tokens = int(u.get("input_tokens") or 0)
            usage.output_tokens = int(u.get("output_tokens") or 0)
            if u.get("seconds"):
                seconds = float(u["seconds"])
            elif parsed.get("duration"):
                seconds = float(parsed["duration"])
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
    usage.media_units = float(seconds or 0.0)
    return bytes_response(ctx, data, ctype, usage)


async def openai_audio_speech(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    if not body.get("input"):
        raise ServingError.invalid_request("input: field required", "input")
    result = await get_engine().open(ctx, "v1/audio/speech", body, stream=False)
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    data = await result.read_bytes()
    usage = Usage()
    tokens_header = result.headers.get("x-input-tokens")
    if tokens_header and tokens_header.isdigit():
        usage.input_tokens = int(tokens_header)
    else:
        usage.input_tokens = estimate_tokens(str(body.get("input", "")))
        usage.estimated = True
    usage.media_units = audio_seconds_from_bytes(data, body.get("response_format"))
    ctype = result.headers.get("content-type") or f"audio/{body.get('response_format', 'mpeg')}"
    return bytes_response(ctx, data, ctype, usage)


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------


def _size(body: Dict[str, Any]) -> Tuple[int, int]:
    size = str(body.get("size") or "1024x1024")
    try:
        w, h = size.lower().split("x")
        return int(w), int(h)
    except ValueError:
        return 1024, 1024


def sd_cpp_txt2img_body(body: Dict[str, Any]) -> Dict[str, Any]:
    w, h = _size(body)
    out = {
        "prompt": body.get("prompt", ""),
        "negative_prompt": body.get("negative_prompt", ""),
        "width": w,
        "height": h,
        "batch_count": int(body.get("n") or 1),
        "sample_steps": int(body.get("steps") or 20),
        "cfg_scale": float(body.get("guidance") or body.get("cfg_scale") or 7.0),
        "seed": int(body.get("seed", -1)),
    }
    return out


def sd_cpp_response_to_openai(data: Any, response_format: str) -> Dict[str, Any]:
    if isinstance(data, dict) and "data" in data:
        return data
    items = data if isinstance(data, list) else [data]
    out = []
    for item in items:
        b64 = (
            item.get("data") or item.get("b64_json") or item.get("image")
            if isinstance(item, dict)
            else item
        )
        entry: Dict[str, Any] = {"b64_json": b64}
        if response_format == "url":
            entry = {"url": f"data:image/png;base64,{b64}"}
        out.append(entry)
    return {"created": int(time.time()), "data": out}


async def openai_images_generations(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    if not body.get("prompt"):
        raise ServingError.invalid_request("prompt: field required", "prompt")
    engine = get_engine()
    target = await engine.resolve(ctx, body.get("model") or "")
    if target.instance.engine == "stable_diffusion_cpp":
        result = await engine.open(
            ctx,
            "txt2img",
            sd_cpp_txt2img_body(body),
            stream=False,
            model=body["model"],
            rewrite_model=False,
        )
    else:
        result = await engine.open(ctx, "v1/images/generations", body, stream=False)
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    data = await result.read_json()
    if target.instance.engine == "stable_diffusion_cpp":
        data = sd_cpp_response_to_openai(
            (
                data.get("data", data)
                if "data" in data
                and isinstance(data.get("data"), list)
                and data.get("data")
                and not isinstance(data["data"][0], dict)
                else data
            ),
            body.get("response_format", "b64_json"),
        )
    usage = extract_openai_usage(data)
    usage.media_units = float(len(data.get("data") or []) or int(body.get("n") or 1))
    return json_response(ctx, data, usage)


async def openai_images_edits(ctx: ServingContext, raw: bytes, content_type: str) -> Response:
    boundary = multipart_boundary(content_type)
    if boundary is None:
        raise ServingError(415, "invalid_request_error", "multipart/form-data body required")
    parts = multipart_fields(raw, boundary)
    model = multipart_text(parts, "model")
    if not model:
        raise ServingError.invalid_request("model: field required", "model")
    ctx.model = model
    engine = get_engine()
    target = await engine.resolve(ctx, model)
    if target.instance.engine == "stable_diffusion_cpp":
        image = parts.get("image")
        body = {
            "prompt": multipart_text(parts, "prompt") or "",
            "init_image": base64.b64encode(image[1]).decode() if image else "",
            "batch_count": int(multipart_text(parts, "n") or 1),
        }
        mask = parts.get("mask")
        if mask:
            body["mask_image"] = base64.b64encode(mask[1]).decode()
        result = await engine.open(
            ctx, "img2img", body, stream=False, model=model, rewrite_model=False
        )
    else:
        result = await engine.open(
            ctx,
            "v1/images/edits",
            content=raw,
            headers={"Content-Type": content_type},
            stream=False,
            model=model,
        )
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    data = await result.read_json()
    if target.instance.engine == "stable_diffusion_cpp":
        data = sd_cpp_response_to_openai(
            data, multipart_text(parts, "response_format") or "b64_json"
        )
    usage = extract_openai_usage(data)
    usage.media_units = float(len(data.get("data") or []) or 1)
    return json_response(ctx, data, usage)


# ---------------------------------------------------------------------------
# videos (async jobs)
# ---------------------------------------------------------------------------


def _video_key(video_id: str) -> str:
    from app.db.redis.client import redis_key

    return redis_key("serving", "videos", video_id)


async def _remember_video(video_id: str, target: RouteTarget, body: Dict[str, Any]) -> None:
    from app.db.redis.client import get_redis

    try:
        client = await get_redis()
        await client.set(
            _video_key(video_id),
            json.dumps(
                {
                    "model": target.requested_name,
                    "instance_id": target.instance.instance_id,
                    "seconds": body.get("seconds"),
                    "metered": False,
                }
            ),
            ex=settings.SERVING_VIDEO_JOB_TIMEOUT_SECONDS * 2,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"video job mapping not stored: {exc}")


async def _video_job(video_id: str) -> Optional[Dict[str, Any]]:
    from app.db.redis.client import get_redis

    try:
        client = await get_redis()
        raw = await client.get(_video_key(video_id))
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"video job lookup failed: {exc}")
        return None


async def _video_target(ctx: ServingContext, video_id: str) -> Tuple[Dict[str, Any], RouteTarget]:
    job = await _video_job(video_id)
    if job is None:
        raise ServingError(404, "invalid_request_error", f"video job '{video_id}' not found")
    ctx.model = job["model"]
    engine = get_engine()
    entry = engine.table.get(job["model"])
    if entry is None:
        raise ServingError.model_not_found(job["model"])
    inst = next((i for i in entry.instances if i.instance_id == job["instance_id"]), None)
    if inst is None:
        raise ServingError.no_instance_available(job["model"])
    return job, RouteTarget(model=entry, instance=inst, requested_name=job["model"])


async def _open_on(ctx: ServingContext, target: RouteTarget, method: str, path: str, body=None):
    engine = get_engine()
    engine.table.models  # routing already resolved; pin the target
    ctx.target = target
    from app.services.serving.upstream import (
        get_upstream_client,
        timeouts,
        upstream_headers,
    )

    client = get_upstream_client(engine.table.ca_bundle_pem, target.instance.proxy_url)
    headers = upstream_headers(ctx.request_id, ctx.api_key.id, target.model.deployment_id, ctx.fmt)
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = client.build_request(
        method,
        target.instance.engine_url(path),
        content=data,
        headers=headers,
        timeout=timeouts(False),
    )
    try:
        response = await client.send(request, stream=True)
    except Exception as exc:  # noqa: BLE001
        raise ServingError.upstream_error(
            f"cannot reach instance {target.instance.instance_id}: {exc}"
        )
    from app.services.serving.pipeline import UpstreamResult

    return UpstreamResult(ctx, response, target)


async def openai_videos_create(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    if not body.get("prompt"):
        raise ServingError.invalid_request("prompt: field required", "prompt")
    engine = get_engine()
    result = await engine.open(ctx, "v1/videos", body, stream=False)
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    data = await result.read_json()
    if data.get("id") and ctx.target is not None:
        await _remember_video(str(data["id"]), ctx.target, body)
    ctx.metadata["video_job"] = data.get("id")
    usage = Usage()
    if data.get("status") == "completed":
        usage.media_units = float(body.get("seconds") or data.get("seconds") or 0)
    return json_response(ctx, data, usage)


async def openai_videos_get(ctx: ServingContext, video_id: str) -> Response:
    job, target = await _video_target(ctx, video_id)
    result = await _open_on(ctx, target, "GET", f"v1/videos/{video_id}")
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    data = await result.read_json()
    usage = Usage()
    status = data.get("status")
    if status in ("completed", "failed", "error") and not job.get("metered"):
        usage.media_units = (
            float(job.get("seconds") or data.get("seconds") or 0) if status == "completed" else 0.0
        )
        job["metered"] = True
        from app.db.redis.client import get_redis

        try:
            client = await get_redis()
            await client.set(
                _video_key(video_id),
                json.dumps(job),
                ex=settings.SERVING_VIDEO_JOB_TIMEOUT_SECONDS * 2,
            )
        except Exception:  # noqa: BLE001
            pass
        ctx.metadata["video_job"] = video_id
        return json_response(ctx, data, usage, status_code=200)
    ctx.metered = True  # polling is free
    return json_response(ctx, data, usage)


async def openai_videos_content(ctx: ServingContext, video_id: str) -> Response:
    _, target = await _video_target(ctx, video_id)
    result = await _open_on(ctx, target, "GET", f"v1/videos/{video_id}/content")
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    data = await result.read_bytes()
    ctx.metered = True
    return bytes_response(ctx, data, result.headers.get("content-type", "video/mp4"), Usage())


async def openai_videos_delete(ctx: ServingContext, video_id: str) -> Response:
    _, target = await _video_target(ctx, video_id)
    result = await _open_on(ctx, target, "DELETE", f"v1/videos/{video_id}")
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    data = await result.read_json()
    ctx.metered = True
    return json_response(ctx, data, Usage())


async def openai_videos_list(ctx: ServingContext) -> Response:
    """List jobs known to this API layer (Redis mapping), each fetched from its engine."""
    from app.db.redis.client import get_redis, redis_key

    items = []
    try:
        client = await get_redis()
        async for key in client.scan_iter(match=redis_key("serving", "videos", "*")):
            raw = await client.get(key)
            if raw:
                job = json.loads(raw)
                items.append(
                    {
                        "id": key.rsplit(":", 1)[-1],
                        "object": "video",
                        "model": job.get("model"),
                        "seconds": job.get("seconds"),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"video list failed: {exc}")
    ctx.metered = True
    return json_response(ctx, {"object": "list", "data": items}, Usage())
