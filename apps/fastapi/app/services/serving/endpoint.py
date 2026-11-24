"""
Request wrapper shared by every serving route.

``serve`` authenticates the API key (``verify_api_key`` through
``services.serving.auth``), reads and size-checks the body, builds the
``ServingContext``, runs the handler and renders any ``ServingError`` in the
wire format of the request (serving.md §4). Errors that happen after the
model is known are metered with ``status=error``.

The IP allow/block check for scope ``api_layer`` runs earlier, in the
``app/middlewares/ip_rules.py`` (installed on the serving process), so it is
not repeated here.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.serving import metrics as prom
from app.services.serving.auth import ApiKeyContext, verify_api_key
from app.services.serving.context import ServingContext
from app.services.serving.errors import ServingError, error_body, error_headers
from app.services.serving.usage import Usage
from fastapi import Request
from starlette.responses import JSONResponse, Response

logger = get_logger(__name__)

Handler = Callable[[ServingContext, Any], Awaitable[Response]]


def render_error(fmt: str, err: ServingError, ctx: Optional[ServingContext] = None) -> JSONResponse:
    headers = error_headers(err)
    if ctx is not None:
        base = ctx.response_headers()
        base.update(headers)
        headers = base
    return JSONResponse(
        status_code=err.status_code,
        content=error_body(fmt, err.status_code, err.code, err.message, err.param),
        headers=headers,
    )


async def read_json_body(request: Request, limit: Optional[int] = None) -> Dict[str, Any]:
    """Read and parse a JSON object body, enforcing SERVING_MAX_JSON_BODY_BYTES."""
    limit = limit or settings.SERVING_MAX_JSON_BODY_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ServingError.request_too_large(limit)
    raw = await request.body()
    if len(raw) > limit:
        raise ServingError.request_too_large(limit)
    if not raw.strip():
        raise ServingError.invalid_request("request body is empty")
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ServingError.invalid_request(f"request body is not valid JSON: {exc}")
    if not isinstance(body, dict):
        raise ServingError.invalid_request("request body must be a JSON object")
    request.state.body_size = len(raw)
    return body


async def read_raw_body(request: Request, limit: Optional[int] = None) -> bytes:
    """Multipart uploads: raw bytes, capped at SERVING_MAX_UPLOAD_BYTES."""
    limit = limit or settings.SERVING_MAX_UPLOAD_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ServingError.request_too_large(limit)
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ServingError.request_too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks)


async def authenticate(request: Request) -> ApiKeyContext:
    return await verify_api_key(request)


async def serve(
    request: Request,
    fmt: str,
    endpoint: str,
    handler: Handler,
    *,
    body: str = "json",
) -> Response:
    """Authenticate, read the body (``json`` | ``raw`` | ``none``), run, render errors."""
    ctx: Optional[ServingContext] = None
    try:
        api_key = await authenticate(request)
        ctx = ServingContext(request=request, fmt=fmt, endpoint=endpoint, api_key=api_key)
        if body == "json":
            payload: Any = await read_json_body(request)
            if isinstance(payload.get("model"), str):
                ctx.model = payload["model"]
            ctx.stream = bool(payload.get("stream", fmt == "ollama" and endpoint != "api/embed"))
        elif body == "raw":
            payload = await read_raw_body(request)
        else:
            payload = None
        return await handler(ctx, payload)
    except ServingError as err:
        return await _error_response(fmt, err, ctx)


async def _error_response(fmt: str, err: ServingError, ctx: Optional[ServingContext]) -> Response:
    if ctx is not None:
        prom.observe(ctx, err.status_code)
        if ctx.model and not ctx.metered:
            from app.services.metering.service import record_request

            status = "timeout" if err.code in ("upstream_timeout", "request_timeout") else "error"
            await record_request(ctx, Usage(), status, err.status_code, err.code)
    if err.status_code >= 500:
        logger.warning(f"serving {fmt} error {err.status_code} {err.code}: {err.message}")
    return render_error(fmt, err, ctx)


async def serve_public(
    request: Request, fmt: str, handler: Callable[[], Awaitable[Any]]
) -> Response:
    """Authenticated read-only endpoints (model listings): no body, no metering."""
    try:
        api_key = await authenticate(request)
        ctx = ServingContext(request=request, fmt=fmt, endpoint="models", api_key=api_key)
        ctx.metered = True
        payload = await handler()
        return JSONResponse(content=payload, headers=ctx.response_headers())
    except ServingError as err:
        return render_error(fmt, err)
