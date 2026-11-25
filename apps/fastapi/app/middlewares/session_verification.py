"""
Request authentication dependencies.

Every other domain's admin route uses::

    from app.middlewares.session_verification import get_current_admin

    async def my_route(request: Request, admin=Depends(get_current_admin), ...):

``admin`` is the ``AdminUsers`` row (``admin.id``, ``admin.username``). The
validated session dict is on ``request.state.session`` and
``request.state.user_id`` / ``request.state.admin_id`` hold the admin id (the
rate limiter keys on ``user_id``).

The token comes from ``Authorization: Bearer`` (CLI/Terraform) or the session
cookie (browsers; the cookie_auth middleware rewrites it into a Bearer header).
Infrastructure failures are reported as 503 with Retry-After, never as 401.

This module exposes no ``install()`` so the middleware registry ignores it.
"""

from typing import Any, Dict, Optional

from app.core.exceptions import AuthenticationError, BaseAPIException
from app.core.logger import get_logger
from app.core.request_context import set_request_actor
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import AdminUsers
from app.db.postgres.repositories.admin_user_repository import AdminUserRepository
from app.services.auth.cookie import CookieService
from app.services.auth.session_manager import SessionManager
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

security = HTTPBearer(auto_error=False)

_INFRA_ERRORS: tuple = (OSError, TimeoutError, ConnectionError)


def _infra_error_types() -> tuple:
    """Exception classes that mean "dependency down", imported lazily."""
    types: list = list(_INFRA_ERRORS)
    try:
        import asyncpg

        types.append(asyncpg.PostgresError)
    except Exception:  # noqa: BLE001
        pass
    try:
        from sqlalchemy.exc import DBAPIError, OperationalError

        types.extend([DBAPIError, OperationalError])
    except Exception:  # noqa: BLE001
        pass
    try:
        from redis.exceptions import RedisError

        types.append(RedisError)
    except Exception:  # noqa: BLE001
        pass
    return tuple(types)


async def _resolve_admin(
    request: Request, response: Response, token: Optional[str], db: AsyncSession
) -> AdminUsers:
    if not token:
        raise AuthenticationError("No authentication token provided")
    manager = SessionManager(db)
    session_data = await manager.validate_session(token)
    if session_data is None:
        CookieService.clear_auth_cookies(response)
        raise AuthenticationError("Session has expired or been revoked. Please log in again.")

    await manager.touch_session(session_data)

    admin_repo = AdminUserRepository(db)
    admin = await admin_repo.get_by_id(session_data["admin_id"])
    if admin is None or not admin.is_active:
        await manager.revoke_by_hash(session_data["token_hash"], session_data["admin_id"])
        CookieService.clear_auth_cookies(response)
        raise AuthenticationError("Admin account is not active")

    request.state.session = session_data
    request.state.admin = admin
    request.state.admin_id = str(admin.id)
    request.state.user_id = str(admin.id)
    set_request_actor(f"admin:{admin.id}")
    # Commit so a slow handler is not idle-in-transaction on the touch write.
    await admin_repo.release_connection()
    return admin


async def get_current_admin(
    request: Request,
    response: Response,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> AdminUsers:
    """Authenticated admin or 401 (503 with Retry-After when Postgres/Redis are down)."""
    token = credentials.credentials if credentials else CookieService.get_session_token(request)
    try:
        return await _resolve_admin(request, response, token, db)
    except BaseAPIException:
        raise
    except _infra_error_types() as exc:
        logger.error(f"Auth dependency unavailable: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Authentication service temporarily unavailable",
            headers={"Retry-After": "5"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Authentication error: {exc}")
        raise HTTPException(status_code=500, detail="Authentication error")


async def get_optional_admin(
    request: Request,
    response: Response,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Optional[AdminUsers]:
    """Like get_current_admin but returns None instead of raising 401."""
    token = credentials.credentials if credentials else CookieService.get_session_token(request)
    if not token:
        return None
    try:
        return await _resolve_admin(request, response, token, db)
    except BaseAPIException:
        return None


def current_session(request: Request) -> Dict[str, Any]:
    """The validated session dict set by get_current_admin (for routes needing session_id)."""
    data = getattr(request.state, "session", None)
    if not data:
        raise AuthenticationError("No active session")
    return data


def authenticated_via_cookie(request: Request) -> bool:
    """True when the credentials came from the session cookie (CSRF applies)."""
    state = request.scope.get("state") or {}
    if state.get("auth_via_cookie"):
        return True
    return bool(request.cookies.get(settings.SESSION_COOKIE_NAME)) and not request.headers.get(
        "authorization"
    )


__all__ = [
    "get_current_admin",
    "get_optional_admin",
    "current_session",
    "authenticated_via_cookie",
    "security",
]
