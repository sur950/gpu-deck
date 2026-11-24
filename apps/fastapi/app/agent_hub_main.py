"""
Agent hub entry point (process "hub", port 8443 locally, mTLS).

    python -m app.agent_hub_main                       # TLS material from the vault / env
    uvicorn app.agent_hub_main:app --port 8443 \\
        --ssl-certfile certs/hub.crt --ssl-keyfile certs/hub.key \\
        --ssl-ca-certs certs/ca.pem --ssl-cert-reqs 1 \\
        --ws app.services.agent_hub.ws_protocol:MTLSWebSocketProtocol

Decision: the hub runs as its **own uvicorn process**, not a second
listener inside the admin API. Reasons: the TLS mode differs (client
certificates required, TLS 1.3 only, CERT_OPTIONAL so the join POST works
without a cert), the admin middlewares (sessions, CSRF, CORS) must not apply
to agent traffic, and only this process holds sockets — the admin API and the
jobs process reach agents through the Redis bus (``services/agent_hub/bus.py``),
so the hub can be restarted or scaled independently.

Endpoints: ``POST /agent/v1/join``, ``WS /agent/v1/ws``, ``GET /agent/v1/ca``,
``GET /health``, ``GET /metrics`` (Prometheus, includes ``gpudeck_hub_*``).
"""

from __future__ import annotations

import asyncio
import ssl
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import uvicorn
from app.api.v1.routes.health import build_health_payload
from app.core.exceptions import (
    BaseAPIException,
    base_exception_handler,
    generic_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.core.logger import get_logger
from app.core.observability import setup_observability
from app.core.rate_limiter import limiter, rate_limit_exceeded_handler
from app.core.settings import settings
from app.db.postgres.connection import close_db, init_db
from app.db.redis.cache_service import CacheService
from app.db.redis.client import close_redis
from app.middlewares import core_middlewares, request_limits
from app.services.agent_hub import routes as hub_routes
from app.services.agent_hub.service import HubRuntime
from app.services.agent_hub.ws_protocol import MTLSWebSocketProtocol
from app.services.pki.service import PkiService, ensure_ca_standalone
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = get_logger(__name__)

PROCESS = "hub"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} agent hub v{settings.APP_VERSION}")
    await init_db()
    await ensure_ca_standalone()
    runtime = HubRuntime()
    app.state.hub_runtime = runtime
    await runtime.start()
    logger.info(f"Agent hub ready on {settings.AGENT_HUB_HOST}:{settings.AGENT_HUB_PORT}")
    yield
    logger.info("Shutting down agent hub...")
    await runtime.stop()
    await CacheService.reset()
    await close_redis()
    await close_db()
    logger.info("Agent hub shut down")


app = FastAPI(
    title=f"{settings.APP_NAME} Agent Hub",
    description="mTLS WebSocket hub for node agents plus the join endpoint.",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# No router-level Request dependency here: it cannot resolve on the WebSocket
# route. core_middlewares sets the request context for HTTP requests.
app.state.limiter = limiter
# Only the core middlewares (request id, security headers, logging) and the body cap (the
# join POST is unauthenticated until the token is checked); no sessions/CSRF/CORS.
core_middlewares.install(app, PROCESS)
request_limits.install(app, PROCESS)

app.add_exception_handler(BaseAPIException, base_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_exception_handler(Exception, generic_exception_handler)

setup_observability(app, process=PROCESS)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": settings.APP_NAME,
        "process": PROCESS,
        "version": settings.APP_VERSION,
    }


@app.get("/health", include_in_schema=False)
async def health():
    payload = await build_health_payload(PROCESS)
    runtime: Optional[HubRuntime] = getattr(app.state, "hub_runtime", None)
    payload["checks"]["hub"] = {
        "connected_nodes": len(runtime.registry) if runtime else 0,
        "draining": runtime.draining if runtime else True,
    }
    return payload


app.include_router(hub_routes.router, tags=hub_routes.TAGS)


# ==========================================
# Server construction
# ==========================================


def build_config(
    host: str,
    port: int,
    tls: Dict[str, str],
    application: Any = None,
    log_level: str = "info",
) -> uvicorn.Config:
    """uvicorn config with mTLS (CERT_OPTIONAL: join has no client cert, ws checks for one)."""
    return uvicorn.Config(
        application or app,
        host=host,
        port=port,
        ssl_certfile=tls["certfile"],
        ssl_keyfile=tls["keyfile"],
        ssl_ca_certs=tls["ca_certs"],
        ssl_cert_reqs=ssl.CERT_OPTIONAL,
        ws=MTLSWebSocketProtocol,
        ws_max_size=settings.HUB_MAX_FRAME_BYTES,
        ws_ping_interval=float(settings.HUB_PING_INTERVAL_SECONDS),
        ws_ping_timeout=float(settings.HUB_PING_INTERVAL_SECONDS),
        log_level=log_level,
        lifespan="on",
        proxy_headers=False,
    )


def harden_ssl(server: uvicorn.Server) -> None:
    """TLS 1.3 only (protocol.md §1); must run after ``config.load()``."""
    ctx = server.config.ssl
    if ctx is not None:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3


async def serve(host: Optional[str] = None, port: Optional[int] = None) -> None:
    async def _tls() -> Dict[str, str]:
        from app.db.postgres.connection import get_session_maker

        async with get_session_maker()() as session:
            paths = await PkiService.from_session(session).materialize_hub_tls()
            await session.commit()
            return paths

    tls = await _tls()
    config = build_config(host or settings.AGENT_HUB_HOST, port or settings.AGENT_HUB_PORT, tls)
    server = uvicorn.Server(config)
    config.load()
    harden_ssl(server)
    await server.serve()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
