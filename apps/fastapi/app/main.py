"""
Admin API entry point (process "admin", port 8180 locally).

    uvicorn app.main:app --port 8180

Serves the web UI's REST API under /api/v1 plus the agent hub WebSocket
placeholder. Everything domain-specific plugs in through registries, so this
file does not change when a domain adds a feature:

- routes:      app/api/v1/routes/<name>.py exposing `router`   (router_registry)
- middlewares: app/middlewares/<name>.py exposing `install()`  (middlewares/__init__)
- settings:    app/core/settings/<domain>.py                   (settings/__init__)
- lifecycle:   app/services/<domain>/hooks.py                  (core/lifecycle)
"""

from contextlib import asynccontextmanager

from app.api.operation_ids import contract_unique_id
from app.api.router_registry import include_discovered_routers
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BaseAPIException,
    base_exception_handler,
    generic_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.core.lifecycle import run_shutdown_hooks, run_startup_hooks
from app.core.logger import get_logger
from app.core.observability import setup_observability
from app.core.rate_limiter import limiter, rate_limit_exceeded_handler
from app.core.request_context import set_request_context_dep
from app.core.settings import settings
from app.db.postgres.connection import close_db, get_session_maker, init_db
from app.db.redis.cache_service import CacheService
from app.db.redis.client import close_redis
from app.middlewares import install_middlewares
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = get_logger(__name__)

PROCESS = "admin"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown for the admin API process."""
    logger.info(f"Starting {settings.APP_NAME} admin API v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.APP_ENV}")

    await init_db()

    # Seed/rotate the admin from .env (see app/db/postgres/seed.py)
    from app.db.postgres.seed import seed_if_empty

    async with get_session_maker()() as seed_session:
        await seed_if_empty(seed_session)

    # Domain startup hooks (routing table warmers, caches, ...)
    await run_startup_hooks(PROCESS)

    # Optional in-process scheduler (single-container deployments only)
    scheduler = None
    if settings.SCHEDULER_EMBEDDED:
        from app.workers.scheduler import register_scheduled_jobs
        from app.workers.scheduler import scheduler as _scheduler

        scheduler = _scheduler
        scheduler.initialize()
        register_scheduled_jobs()
        scheduler.start()

    logger.info(f"{settings.APP_NAME} admin API started on port {settings.PORT}")
    yield

    logger.info(f"Shutting down {settings.APP_NAME} admin API...")
    if scheduler is not None:
        scheduler.shutdown()
    await run_shutdown_hooks(PROCESS)
    await CacheService.reset()
    await close_redis()
    await close_db()
    logger.info("Admin API shut down")


app = FastAPI(
    title=f"{settings.APP_NAME} Admin API",
    description=f"""
{settings.APP_NAME} — GPU control plane admin API.

* **Login** — one seeded admin (`POST /api/v1/auth/login`), opaque session cookie, CSRF.
* **API keys** — created here, used by clients of the serving API (port {settings.API_LAYER_PORT}).
* **Fleet** — clusters, nodes, join tokens, device inventory, live metrics, topology.
* **Models** — catalog, engines, deployments, placement, model cache, benchmarks.
* **Automation** — rules, layouts, swap manager, alerts.
* **Metering & billing** — usage per key/model, price plans, reports.

All responses use the envelope `{{"success", "data", "error", "meta"}}`.
""",
    version=settings.APP_VERSION,
    contact={"name": "GPU Deck", "url": "https://github.com/sur950/gpu-deck"},
    license_info={"name": "Apache-2.0", "identifier": "Apache-2.0"},
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    swagger_ui_parameters={"withCredentials": True},
    # operationIds from libs/contracts/openapi/admin.yaml (app/api/operation_ids.py)
    generate_unique_id_function=contract_unique_id,
)

# ==========================================
# MIDDLEWARE
# ==========================================

# Global dependency: captures IP + UA + request id into ContextVars on every route
app.router.dependencies.append(Depends(set_request_context_dep))

# Rate limiting (slowapi) — routes opt in with @rate_limit("n/minute")
app.state.limiter = limiter

# Core + discovered middlewares (app/middlewares/*.py)
install_middlewares(app, PROCESS)

# ==========================================
# EXCEPTION HANDLERS — most-specific first (Starlette walks the MRO)
# ==========================================

app.add_exception_handler(AuthenticationError, base_exception_handler)  # 401
app.add_exception_handler(AuthorizationError, base_exception_handler)  # 403
app.add_exception_handler(BaseAPIException, base_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# ==========================================
# OBSERVABILITY (/metrics + OTel)
# ==========================================

setup_observability(app, process=PROCESS)


# ==========================================
# ROUTERS
# ==========================================


@app.get("/", tags=["Root"], include_in_schema=False)
async def root():
    return {
        "name": settings.APP_NAME,
        "process": PROCESS,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "status": "running",
    }


# Every app/api/v1/routes/<name>.py exposing `router` is mounted under API_V1_PREFIX
include_discovered_routers(
    app, "app.api.v1.routes", default_prefix=settings.API_V1_PREFIX, label=PROCESS
)

logger.info("Admin API configured")
