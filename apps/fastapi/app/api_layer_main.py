"""
Serving API entry point (process "serving", port 8181 locally).

    uvicorn app.api_layer_main:app --port 8181

The data plane: OpenAI, Anthropic and Ollama formats (serving.md), API-key
auth, pass-through or translation to node-agent engine proxies, usage
metering after the response. No browser sessions, no CORS.

Routes are auto-discovered from app/api/serving/** (the serving API) and mounted
twice: at the explicit prefixes (``/openai/v1``, ``/anthropic/v1``,
``/ollama/api``) and — with ``SERVING_ENABLE_BARE_PATHS`` — at the bare
compat prefixes (``/v1`` for OpenAI and Anthropic, ``/api`` for Ollama).
The shared bare ``/v1/models`` is dispatched by headers
(``app/api/serving/_compat.py``). Errors are rendered in the request's wire
format (``app.services.serving.formats.format_for_request``).
"""

from contextlib import asynccontextmanager

from app.api.router_registry import discover_routers
from app.api.v1.routes.health import build_health_payload
from app.core.lifecycle import run_shutdown_hooks, run_startup_hooks
from app.core.logger import get_logger
from app.core.observability import setup_observability
from app.core.settings import settings
from app.db.postgres.connection import close_db, init_db
from app.db.redis.cache_service import CacheService
from app.db.redis.client import close_redis
from app.middlewares import install_middlewares
from app.services.serving.errors import error_body
from app.services.serving.formats import bare_prefix, format_for_request
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

logger = get_logger(__name__)

PROCESS = "serving"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown for the serving API process."""
    logger.info(f"Starting {settings.APP_NAME} serving API v{settings.APP_VERSION}")
    await init_db()
    # services/serving/hooks.py: routing table (Redis + pub/sub), in-flight flusher.
    await run_startup_hooks(PROCESS)
    logger.info(f"{settings.APP_NAME} serving API started on port {settings.API_LAYER_PORT}")
    yield
    logger.info("Shutting down serving API...")
    await run_shutdown_hooks(PROCESS)
    await CacheService.reset()
    await close_redis()
    await close_db()
    logger.info("Serving API shut down")


app = FastAPI(
    title=f"{settings.APP_NAME} Serving API",
    description="OpenAI, Anthropic and Ollama compatible inference API (API-key auth).",
    version=settings.APP_VERSION,
    contact={"name": "GPU Deck", "url": "https://github.com/sur950/gpu-deck"},
    license_info={"name": "Apache-2.0", "identifier": "Apache-2.0"},
    lifespan=lifespan,
    # The serving API is documented by libs/contracts/openapi/serving.md — no live docs.
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)

# ==========================================
# MIDDLEWARE
# ==========================================

# Pure-ASGI stack only (no BaseHTTPMiddleware) — this is the hot path. The request
# context (id, IP, UA) is set by core_middlewares, so the serving app does not
# add the admin app's global set_request_context_dep dependency.
install_middlewares(app, PROCESS)

# ==========================================
# EXCEPTION HANDLERS — wire-format error shapes (serving.md §4)
# ==========================================

_STATUS_CODES = {
    404: "unsupported_endpoint",
    405: "unsupported_endpoint",
    413: "request_too_large",
    422: "invalid_request_error",
}


def _format(request: Request) -> str:
    return format_for_request(request.url.path, request.headers) or "openai"


def _error(
    request: Request, status_code: int, code: str, message: str, headers=None
) -> JSONResponse:
    fmt = _format(request)
    out_headers = {"x-gpudeck-error-code": code, "Cache-Control": "no-store"}
    out_headers.update(headers or {})
    return JSONResponse(
        status_code=status_code,
        content=error_body(fmt, status_code, code, message),
        headers=out_headers,
    )


async def serving_http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    code = None
    if isinstance(detail, dict):
        code = detail.get("code")
        detail = detail.get("message", str(detail))
    if code is None and exc.status_code in (404, 405):
        # Unknown route / method: the shared skeleton shape (serving_error_body).
        from app.core.exceptions import serving_error_body

        return JSONResponse(
            status_code=exc.status_code,
            content=serving_error_body(_format(request), exc.status_code, str(detail)),
            headers={
                "x-gpudeck-error-code": "unsupported_endpoint",
                **(exc.headers or {}),
            },
        )
    code = str(
        code
        or getattr(exc, "wire_code", None)
        or _STATUS_CODES.get(exc.status_code, "invalid_request_error")
    )
    message = str(detail) if detail else "request failed"
    return _error(request, exc.status_code, code.lower(), message, getattr(exc, "headers", None))


async def serving_validation_exception_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(part) for part in first.get("loc", []) if part not in ("body", "query"))
    message = f"{loc}: {first.get('msg', 'invalid request')}" if loc else "invalid request"
    return _error(request, 400, "invalid_request_error", message)


async def serving_generic_exception_handler(request: Request, exc: Exception):
    logger.opt(exception=exc).error(
        f"Unhandled serving exception on {request.method} {request.url.path}: "
        f"{type(exc).__name__}: {exc}"
    )
    message = str(exc) if settings.DEBUG else "internal server error"
    return _error(request, 500, "internal_error", message)


app.add_exception_handler(StarletteHTTPException, serving_http_exception_handler)
app.add_exception_handler(RequestValidationError, serving_validation_exception_handler)
app.add_exception_handler(Exception, serving_generic_exception_handler)

# ==========================================
# OBSERVABILITY
# ==========================================

setup_observability(app, process=PROCESS)


# ==========================================
# ROUTERS
# ==========================================


@app.get("/", include_in_schema=False)
async def root():
    from app.services.serving.formats import explicit_prefix

    return {
        "name": settings.APP_NAME,
        "process": PROCESS,
        "version": settings.APP_VERSION,
        "formats": {fmt: explicit_prefix(fmt) for fmt in ("openai", "anthropic", "ollama")},
        "bare_paths": bool(settings.SERVING_ENABLE_BARE_PATHS),
        "status": "running",
    }


@app.get("/health", include_in_schema=False)
async def health():
    """Dependencies + routing table. 503 when the table is not loaded (or no node, if required)."""
    from app.services.serving.routing import get_routing_table

    payload = await build_health_payload(PROCESS)
    table = get_routing_table()
    nodes = table.online_node_count()
    payload["checks"]["routing"] = {
        "status": "healthy" if table.loaded else "unhealthy",
        "details": {
            "version": table.version,
            "models": len(table.models),
            "online_nodes": nodes,
            "generated_at": table.generated_at,
        },
    }
    from app.services.serving.tunnel import get_tunnel_registry, get_tunnel_server

    tunnels = get_tunnel_registry()
    tserver = get_tunnel_server()
    payload["checks"]["agent_tunnels"] = {
        "status": "healthy",
        "details": {
            "listening_port": tserver.port if tserver else None,
            "nodes": len(tunnels.counts()),
            "connections": sum(tunnels.counts().values()),
            "rejected": dict(tunnels.rejected_total),
        },
    }
    from app.services.serving.hooks import refresher

    # Before the lifecycle ran (tests, import-time probes) there is no table to wait for.
    started = refresher() is not None
    ok = (table.loaded or not started) and payload.get("status") != "unhealthy"
    if settings.SERVING_HEALTH_REQUIRE_NODES and nodes < 1:
        ok = False
    if not ok:
        payload["status"] = "unhealthy"
    return JSONResponse(payload, status_code=200 if ok else 503)


@app.get("/health/live", include_in_schema=False)
async def liveness():
    return {"status": "ok"}


def mount_serving_routers(application: FastAPI) -> None:
    """Explicit prefixes always; bare compat prefixes when SERVING_ENABLE_BARE_PATHS."""
    discovered = discover_routers("app.api.serving")
    for item in discovered:
        application.include_router(
            item.router,
            prefix=item.prefix,
            tags=item.tags,
            include_in_schema=item.include_in_schema,
            dependencies=item.dependencies or None,
        )
        logger.info(f"[serving] mounted {item.module_name} at '{item.prefix}'")
    if not settings.SERVING_ENABLE_BARE_PATHS:
        return
    import importlib

    from app.api.serving import _compat

    # Shared /v1/models first so neither format's bare mount shadows it.
    application.include_router(_compat.router, tags=_compat.TAGS, include_in_schema=False)
    for item in discovered:
        module = importlib.import_module(item.module_name)
        if getattr(module, "BARE", True) is False:
            continue
        fmt = getattr(importlib.import_module(item.module_name.rsplit(".", 1)[0]), "FORMAT", None)
        if fmt is None:
            continue
        application.include_router(
            item.router,
            prefix=bare_prefix(fmt),
            tags=item.tags,
            include_in_schema=False,
            dependencies=item.dependencies or None,
        )
    logger.info("[serving] bare compat paths mounted at /v1 and /api")


mount_serving_routers(app)

logger.info("Serving API configured")
