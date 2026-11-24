"""
Health check API routes (admin.yaml: Health tag).

GET /api/v1/health        bounded probes of Postgres and Redis (critical) plus
                          celery, agent_hub, jobs_leader, api_layer (informational);
                          200 healthy/degraded, 503 unhealthy
GET /api/v1/health/ready  migrations applied + seed done (503 otherwise); hub listening reported
GET /api/v1/health/live   process up (plain {"status": "ok"}, used by probes/bootstrap)
GET /api/v1/system/info   version, URLs, limits and retention in effect
"""

import asyncio
import socket
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import psutil
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_async_engine
from app.db.redis.client import get_redis, redis_key
from app.middlewares.session_verification import get_current_admin
from app.schemas.common_schema import ServiceHealth
from app.schemas.health_schema import ServingPrefixes, SystemInfo
from app.services.serving.formats import explicit_prefix
from app.utils.api_wrapper import handle_exceptions, success_response
from app.utils.enums import HealthStatus
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Health"]

# Two attempts of at most 1.5 s each: the probe stays inside the web app's 5 s
# /health budget even when the pool is saturated.
DB_HEALTH_ATTEMPTS = 2
DB_HEALTH_TIMEOUT_S = 1.5
DB_HEALTH_RETRY_DELAY_S = 0.2
REDIS_HEALTH_TIMEOUT_S = 1.5
SOCKET_PROBE_TIMEOUT_S = 0.5
HTTP_PROBE_TIMEOUT_S = 1.5
WORKER_HEARTBEAT_STALE_S = 15 * 60  # system heartbeat beat entry runs every 5 min

_DB_SATURATION_MARKERS = (
    "too many clients",
    "too many connections",
    "remaining connection slots",
    "queuepool limit",
)

# Prime psutil's CPU counter so cpu_percent(interval=None) returns a real reading.
psutil.cpu_percent(interval=None)


async def _probe_database() -> None:
    async with get_async_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


def _is_database_saturated(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, PoolTimeoutError)):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _DB_SATURATION_MARKERS)


async def check_database_health() -> ServiceHealth:
    """UNHEALTHY only when unreachable; out of free connections is DEGRADED."""
    start_time = time.time()
    last_error: Optional[BaseException] = None
    for attempt in range(DB_HEALTH_ATTEMPTS):
        try:
            await asyncio.wait_for(_probe_database(), timeout=DB_HEALTH_TIMEOUT_S)
            return ServiceHealth(
                status=HealthStatus.HEALTHY,
                response_time_ms=(time.time() - start_time) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt + 1 < DB_HEALTH_ATTEMPTS:
                await asyncio.sleep(DB_HEALTH_RETRY_DELAY_S)

    if last_error and _is_database_saturated(last_error):
        logger.warning(f"Database health: no free connection ({type(last_error).__name__})")
        return ServiceHealth(
            status=HealthStatus.DEGRADED, error="Database busy: no free connection"
        )
    logger.error(f"Database health check error: {type(last_error).__name__}: {last_error}")
    return ServiceHealth(status=HealthStatus.UNHEALTHY, error="Database unreachable")


async def check_redis_health() -> ServiceHealth:
    start_time = time.time()
    try:
        client = await get_redis()
        await asyncio.wait_for(client.ping(), timeout=REDIS_HEALTH_TIMEOUT_S)
        return ServiceHealth(
            status=HealthStatus.HEALTHY,
            response_time_ms=(time.time() - start_time) * 1000,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Redis health check error: {type(exc).__name__}: {exc}")
        return ServiceHealth(status=HealthStatus.UNHEALTHY, error="Redis unreachable")


async def check_celery_health() -> ServiceHealth:
    """Worker liveness from the system heartbeat key (no blocking broker inspect)."""
    try:
        client = await get_redis()
        stamp = await asyncio.wait_for(
            client.get(redis_key("workers", "heartbeat")),
            timeout=REDIS_HEALTH_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        return ServiceHealth(status=HealthStatus.UNHEALTHY, error=f"heartbeat unreadable: {exc}")
    if not stamp:
        return ServiceHealth(status=HealthStatus.DEGRADED, error="No worker heartbeat yet")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
    except ValueError:
        return ServiceHealth(status=HealthStatus.DEGRADED, error="Malformed heartbeat")
    if age > WORKER_HEARTBEAT_STALE_S:
        return ServiceHealth(
            status=HealthStatus.DEGRADED, error=f"Worker heartbeat stale ({int(age)}s)"
        )
    return ServiceHealth(status=HealthStatus.HEALTHY, details={"heartbeat_age_s": int(age)})


def _hub_target() -> tuple[str, int]:
    url = getattr(settings, "AGENT_HUB_PUBLIC_URL", None) or ""
    parsed = urlparse(url) if url else None
    host = (parsed.hostname if parsed else None) or "127.0.0.1"
    port = (parsed.port if parsed else None) or int(settings.AGENT_HUB_PORT)
    if host not in ("localhost", "127.0.0.1", "::1"):
        host = "127.0.0.1"  # probe the local listener, not a public DNS name
    return host, port


async def check_agent_hub_health() -> ServiceHealth:
    """Is something listening on the agent hub port (the mTLS agent hub listener)?"""
    host, port = _hub_target()
    start = time.time()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=SOCKET_PROBE_TIMEOUT_S
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return ServiceHealth(
            status=HealthStatus.HEALTHY,
            response_time_ms=(time.time() - start) * 1000,
            details={"port": port},
        )
    except (OSError, asyncio.TimeoutError, socket.gaierror) as exc:
        return ServiceHealth(
            status=HealthStatus.DEGRADED,
            error=f"agent hub not listening on {host}:{port} ({type(exc).__name__})",
        )


async def check_jobs_leader_health() -> ServiceHealth:
    """Is a jobs-leader lease held (ha_leases, the leader election table)?"""
    try:
        async with get_async_engine().connect() as conn:
            row = (
                await asyncio.wait_for(
                    conn.execute(
                        text(
                            "SELECT holder_id, expires_at FROM ha_leases "
                            "WHERE name = 'jobs-leader' AND expires_at > now() LIMIT 1"
                        )
                    ),
                    timeout=DB_HEALTH_TIMEOUT_S,
                )
            ).first()
    except Exception as exc:  # noqa: BLE001
        return ServiceHealth(status=HealthStatus.DEGRADED, error=f"lease unreadable: {exc}")
    if row is None:
        return ServiceHealth(status=HealthStatus.DEGRADED, error="No jobs leader lease held")
    return ServiceHealth(status=HealthStatus.HEALTHY, details={"holder_id": row[0]})


async def check_api_layer_health() -> ServiceHealth:
    """Liveness of the serving process (GET /health/live on API_LAYER_PORT, loopback)."""
    import httpx

    url = f"http://127.0.0.1:{settings.API_LAYER_PORT}/health/live"
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=HTTP_PROBE_TIMEOUT_S) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            return ServiceHealth(
                status=HealthStatus.HEALTHY,
                response_time_ms=(time.time() - start) * 1000,
            )
        return ServiceHealth(status=HealthStatus.DEGRADED, error=f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return ServiceHealth(
            status=HealthStatus.DEGRADED,
            error=f"api layer unreachable ({type(exc).__name__})",
        )


def check_system_health() -> dict:
    try:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": memory.percent,
            "disk_percent": disk.percent,
            "memory_available_mb": round(memory.available / (1024 * 1024), 1),
            "disk_available_gb": round(disk.free / (1024 * 1024 * 1024), 2),
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"System health check error: {e}")
        return {"error": str(e)}


def _overall(critical: list) -> HealthStatus:
    if any(s.status == HealthStatus.UNHEALTHY for s in critical):
        return HealthStatus.UNHEALTHY
    if any(s.status == HealthStatus.DEGRADED for s in critical):
        return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY


async def build_health_payload(process: str, extended: bool = False) -> dict:
    """Shared by the admin and serving health endpoints."""
    db_health, redis_health = await asyncio.gather(check_database_health(), check_redis_health())
    checks: Dict[str, Any] = {
        "database": db_health.model_dump(),
        "redis": redis_health.model_dump(),
    }
    if extended:
        celery, hub, leader, api_layer = await asyncio.gather(
            check_celery_health(),
            check_agent_hub_health(),
            check_jobs_leader_health(),
            check_api_layer_health(),
        )
        checks.update(
            celery=celery.model_dump(),
            agent_hub=hub.model_dump(),
            jobs_leader=leader.model_dump(),
            api_layer=api_layer.model_dump(),
        )
    checks["system"] = check_system_health()
    return {
        "status": _overall([db_health, redis_health]).value,
        "version": settings.APP_VERSION,
        "process": process,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


def _status_response(payload: dict):
    body = success_response(data=payload)
    if payload["status"] == HealthStatus.UNHEALTHY.value:
        return JSONResponse(status_code=503, content=body)
    return body


@router.get("/health", summary="Health check")
@handle_exceptions
async def comprehensive_health_check():
    """Health of PostgreSQL, Redis (critical), workers, agent hub, jobs leader, API layer."""
    return _status_response(await build_health_payload("admin", extended=True))


# ---------- readiness ----------


def _expected_head() -> Optional[str]:
    try:
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        ini = Path(__file__).resolve().parents[4] / "alembic.ini"
        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(ini.parent / "app/db/postgres/alembic"))
        locations = cfg.get_main_option("version_locations") or ""
        cfg.set_main_option(
            "version_locations",
            " ".join(str(ini.parent / loc) for loc in locations.split()),
        )
        heads = ScriptDirectory.from_config(cfg).get_heads()
        return heads[0] if len(heads) == 1 else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"readiness: cannot read migration head: {exc}")
        return None


async def check_migrations() -> ServiceHealth:
    try:
        async with get_async_engine().connect() as conn:
            current = (
                await asyncio.wait_for(
                    conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")),
                    timeout=DB_HEALTH_TIMEOUT_S,
                )
            ).scalar()
    except Exception as exc:  # noqa: BLE001
        return ServiceHealth(status=HealthStatus.UNHEALTHY, error=f"migrations unknown: {exc}")
    head = _expected_head()
    if not current:
        return ServiceHealth(status=HealthStatus.UNHEALTHY, error="No migration applied")
    if head and current != head:
        return ServiceHealth(
            status=HealthStatus.UNHEALTHY,
            error=f"Database at {current}, code expects {head}",
            details={"current": current, "head": head},
        )
    return ServiceHealth(status=HealthStatus.HEALTHY, details={"current": current})


async def check_seed() -> ServiceHealth:
    try:
        async with get_async_engine().connect() as conn:
            count = (
                await asyncio.wait_for(
                    conn.execute(text("SELECT count(*) FROM admin_users")),
                    timeout=DB_HEALTH_TIMEOUT_S,
                )
            ).scalar()
    except Exception as exc:  # noqa: BLE001
        return ServiceHealth(status=HealthStatus.UNHEALTHY, error=f"seed unknown: {exc}")
    if not count:
        return ServiceHealth(status=HealthStatus.UNHEALTHY, error="Admin not seeded yet")
    return ServiceHealth(status=HealthStatus.HEALTHY)


@router.get("/health/ready", summary="Readiness (migrations applied, seed done, hub listening)")
@handle_exceptions
async def readiness():
    migrations, seed, hub = await asyncio.gather(
        check_migrations(), check_seed(), check_agent_hub_health()
    )
    # The hub listener runs in its own process; it is reported but does not gate readiness.
    payload = {
        "status": _overall([migrations, seed]).value,
        "version": settings.APP_VERSION,
        "process": "admin",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "migrations": migrations.model_dump(),
            "seed": seed.model_dump(),
            "agent_hub": hub.model_dump(),
        },
    }
    return _status_response(payload)


@router.get("/health/live", summary="Liveness (process up)")
async def liveness():
    """Process is up (no dependency probes) — for container/k8s liveness."""
    return {"status": "ok"}


# ---------- system info ----------

_RETENTION_FIELDS = (
    "METRICS_RAW_RETENTION_HOURS",
    "METRICS_ROLLUP_RETENTION_DAYS",
    "LOGS_RETENTION_DAYS",
    "TRACES_RETENTION_DAYS",
    "USAGE_EVENTS_RETENTION_DAYS",
    "AUDIT_RETENTION_DAYS",
    "ALERT_EVENTS_RETENTION_DAYS",
    "RULE_RUNS_RETENTION_DAYS",
)


def build_system_info() -> SystemInfo:
    retention = {
        name.lower(): int(getattr(settings, name))
        for name in _RETENTION_FIELDS
        if getattr(settings, name, None) is not None
    }
    hub_url = getattr(settings, "AGENT_HUB_PUBLIC_URL", None) or (
        f"https://localhost:{settings.AGENT_HUB_PORT}"
    )
    return SystemInfo(
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.APP_ENV,
        build_sha=settings.BUILD_SHA,
        protocol_version=int(settings.AGENT_PROTOCOL_VERSION),
        api_layer_url=settings.PUBLIC_SERVING_URL,
        agent_hub_url=hub_url,
        # Full mount prefixes (/openai/v1, /anthropic/v1, /ollama/api), not the roots.
        serving=ServingPrefixes(
            openai_prefix=explicit_prefix("openai"),
            anthropic_prefix=explicit_prefix("anthropic"),
            ollama_prefix=explicit_prefix("ollama"),
        ),
        retention=retention,
        limits={
            "max_request_body_bytes": settings.MAX_REQUEST_BODY_BYTES,
            "max_upload_body_bytes": settings.MAX_UPLOAD_BODY_BYTES,
            "admin_max_request_body_bytes": settings.ADMIN_MAX_REQUEST_BODY_BYTES,
            "request_timeout_seconds": settings.REQUEST_TIMEOUT_SECONDS,
            "stream_idle_timeout_seconds": settings.STREAM_IDLE_TIMEOUT_SECONDS,
            "session_expire_hours": settings.SESSION_EXPIRE_HOURS,
            "session_idle_timeout_minutes": settings.SESSION_IDLE_TIMEOUT_MINUTES,
            "auth_lockout_attempts": settings.AUTH_LOCKOUT_ATTEMPTS,
            "auth_lockout_minutes": settings.AUTH_LOCKOUT_MINUTES,
            "auth_password_min_length": settings.AUTH_PASSWORD_MIN_LENGTH,
            "rate_limit_per_minute": settings.RATE_LIMIT_PER_MINUTE,
            "audit_export_max_rows": settings.AUDIT_EXPORT_MAX_ROWS,
        },
    )


@router.get("/system/info", summary="Version, URLs, limits and retention in effect")
@handle_exceptions
async def get_system_info(request: Request, admin=Depends(get_current_admin)):
    return success_response(data=build_system_info())
