"""
Auth routes (admin.yaml: Auth tag).

POST /auth/login            login (rate limited, lockout, sets session + CSRF cookies)
POST /auth/logout           revoke current session, clear cookies            204
GET  /auth/me               current admin
GET  /auth/csrf             CSRF token for the current session (+ cookie refresh)
POST /auth/change-password  verify current, re-hash, revoke other sessions    204
GET  /auth/sessions         list active sessions
DELETE /auth/sessions/{id}  revoke a session                                  204
POST /auth/logout-all       revoke every session except the current one      204

Route layer handles HTTP concerns only; AuthService owns the logic.
"""

import time
import uuid

from app.core.logger import get_logger
from app.core.rate_limiter import rate_limit
from app.core.settings import settings
from app.middlewares.session_verification import current_session, get_current_admin
from app.schemas.auth_schema import (
    AdminUser,
    ChangePasswordRequest,
    CsrfToken,
    LoginRequest,
    LoginResult,
    Session,
)
from app.services.auth.cookie import CookieService
from app.services.auth.csrf import generate_csrf_token
from app.services.auth.service import AuthService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Auth"]


@router.post("/auth/login", summary="Log in")
@rate_limit(f"{settings.RATE_LIMIT_LOGIN_PER_MINUTE}/minute")
@handle_exceptions
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    service: AuthService = Depends(AuthService.as_dependency),
):
    """Single seeded admin. Sets the HttpOnly session cookie and the readable CSRF cookie."""
    result = await service.login(request, payload.username, payload.password)
    CookieService.set_session_cookie(response, result["raw_token"], result["expires_in"])
    CookieService.set_csrf_cookie(response, result["csrf_token"], result["expires_in"])
    data = LoginResult(
        session_id=result["session_id"],
        expires_at=result["expires_at"],
        expires_in=result["expires_in"],
        csrf_token=result["csrf_token"],
        user=AdminUser.model_validate(AuthService.admin_to_dict(result["admin"])),
        must_change_password=result["must_change_password"],
    )
    return success_response(data=data)


@router.post("/auth/logout", summary="Log out", status_code=status.HTTP_204_NO_CONTENT)
@handle_exceptions
async def logout(
    request: Request,
    admin=Depends(get_current_admin),
    service: AuthService = Depends(AuthService.as_dependency),
):
    await service.logout(admin, current_session(request))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    CookieService.clear_auth_cookies(response)
    return response


@router.get("/auth/me", summary="Current admin")
@handle_exceptions
async def get_me(request: Request, admin=Depends(get_current_admin)):
    return success_response(data=AdminUser.model_validate(AuthService.admin_to_dict(admin)))


@router.get("/auth/csrf", summary="Get a CSRF token for the current session")
@handle_exceptions
async def get_csrf_token(request: Request, response: Response, admin=Depends(get_current_admin)):
    """Token = HMAC(SECRET_KEY, session token hash); also refreshes the CSRF cookie."""
    session = current_session(request)
    token = generate_csrf_token(session["token_hash"])
    remaining = max(int(float(session["expires_at"]) - time.time()), 1)
    CookieService.set_csrf_cookie(response, token, remaining)
    return success_response(
        data=CsrfToken(
            csrf_token=token,
            header_name=settings.CSRF_HEADER_NAME,
            cookie_name=settings.CSRF_COOKIE_NAME,
        )
    )


@router.post(
    "/auth/change-password",
    summary="Change the admin password",
    status_code=status.HTTP_204_NO_CONTENT,
)
@rate_limit("10/minute")
@handle_exceptions
async def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    admin=Depends(get_current_admin),
    service: AuthService = Depends(AuthService.as_dependency),
):
    await service.change_password(
        admin, current_session(request), payload.current_password, payload.new_password
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/sessions", summary="List active sessions")
@handle_exceptions
async def list_sessions(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    admin=Depends(get_current_admin),
    service: AuthService = Depends(AuthService.as_dependency),
):
    items, total = await service.list_sessions(admin, current_session(request), page, page_size)
    return success_response(
        data=[Session.model_validate(i) for i in items],
        meta=paginated_meta(total, page, page_size),
    )


@router.delete(
    "/auth/sessions/{session_id}",
    summary="Revoke a session",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def revoke_session(
    request: Request,
    session_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AuthService = Depends(AuthService.as_dependency),
):
    await service.revoke_session(admin, session_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if str(session_id) == current_session(request).get("session_id"):
        CookieService.clear_auth_cookies(response)
    return response


@router.post(
    "/auth/logout-all",
    summary="Revoke every session except the current one",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def logout_all(
    request: Request,
    admin=Depends(get_current_admin),
    service: AuthService = Depends(AuthService.as_dependency),
):
    await service.logout_all(admin, current_session(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
