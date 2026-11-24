"""
Custom exceptions and exception handlers.

Admin API responses use the envelope ``{success, data, error, meta}``. The
serving API returns the error shape of the format being spoken (OpenAI,
Anthropic, Ollama), chosen by the request path prefix.
"""

from typing import Any, Dict, Optional

from app.core.settings import settings
from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class BaseAPIException(HTTPException):
    """Base exception for all custom API exceptions."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        headers: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code or self.__class__.__name__


class AuthenticationError(BaseAPIException):
    """Authentication failed."""

    def __init__(self, detail: str = "Authentication failed"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
            error_code="AUTHENTICATION_ERROR",
        )


class ApiKeyError(BaseAPIException):
    """Missing or invalid API key on the serving API."""

    def __init__(self, detail: str = "Invalid API key"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "ApiKey"},
            error_code="INVALID_API_KEY",
        )


class AuthorizationError(BaseAPIException):
    """Authorization failed - insufficient permissions."""

    def __init__(self, detail: str = "Insufficient permissions"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            error_code="AUTHORIZATION_ERROR",
        )


class CSRFError(BaseAPIException):
    """CSRF token missing or invalid."""

    def __init__(self, detail: str = "CSRF token missing or invalid"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            error_code="CSRF_ERROR",
        )


class InvalidTokenError(BaseAPIException):
    """Invalid or expired token."""

    def __init__(self, detail: str = "Invalid or expired token"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
            error_code="INVALID_TOKEN",
        )


class TokenExpiredError(BaseAPIException):
    """Token has expired."""

    def __init__(self, detail: str = "Token has expired"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
            error_code="TOKEN_EXPIRED",
        )


class InvalidCredentialsError(BaseAPIException):
    """Invalid username or password."""

    def __init__(self, detail: str = "Invalid credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            error_code="INVALID_CREDENTIALS",
        )


class AccountLockedError(BaseAPIException):
    """Too many failed logins."""

    def __init__(self, detail: str = "Account is locked. Try again later."):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            error_code="ACCOUNT_LOCKED",
        )


class ResourceNotFoundError(BaseAPIException):
    """Resource not found."""

    def __init__(self, detail: str = "Resource not found", resource_type: Optional[str] = None):
        if resource_type:
            detail = f"{resource_type} not found"
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
            error_code="RESOURCE_NOT_FOUND",
        )


class DuplicateResourceError(BaseAPIException):
    """Resource already exists (unique constraint violation)."""

    def __init__(
        self,
        detail: str = "Resource already exists",
        resource_type: Optional[str] = None,
        field: Optional[str] = None,
    ):
        if resource_type and field:
            detail = f"{resource_type} with this {field} already exists"
        elif resource_type:
            detail = f"{resource_type} already exists"
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
            error_code="DUPLICATE_RESOURCE",
        )


class ConflictError(BaseAPIException):
    """State conflict (e.g. unload while a swap is in progress)."""

    def __init__(self, detail: str = "Conflict"):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail, error_code="CONFLICT")


class ExternalDeploymentError(BaseAPIException):
    """The target is an external (observed) deployment / instance: GPU Deck never changes it."""

    def __init__(
        self,
        detail: str = (
            "This is an external deployment that GPU Deck only observes; it cannot be changed, "
            "started, stopped, scaled, swapped or benchmarked from GPU Deck"
        ),
    ):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
            error_code="EXTERNAL_DEPLOYMENT",
        )


class ValidationError(BaseAPIException):
    """Validation error."""

    def __init__(self, detail: str = "Validation error"):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
            error_code="VALIDATION_ERROR",
        )


class DatabaseError(BaseAPIException):
    """Database operation error."""

    def __init__(self, detail: str = "Database error"):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
            error_code="DATABASE_ERROR",
        )


class ExternalServiceError(BaseAPIException):
    """External service error."""

    def __init__(self, detail: str = "External service error", service: Optional[str] = None):
        if service:
            detail = f"{service} service error"
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
            error_code="EXTERNAL_SERVICE_ERROR",
        )


class UpstreamEngineError(BaseAPIException):
    """The node agent / inference engine returned an error."""

    def __init__(self, detail: str = "Upstream engine error"):
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
            error_code="UPSTREAM_ERROR",
        )


class ModelNotAvailableError(BaseAPIException):
    """No healthy instance serves the requested model."""

    def __init__(self, detail: str = "Model not available"):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
            error_code="MODEL_NOT_AVAILABLE",
        )


class RateLimitExceededError(BaseAPIException):
    """Rate limit exceeded."""

    def __init__(self, detail: str = "Rate limit exceeded"):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            error_code="RATE_LIMIT_EXCEEDED",
        )


class RequestTooLargeError(BaseAPIException):
    """Request body exceeds the configured cap."""

    def __init__(self, detail: str = "Request body too large"):
        super().__init__(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=detail,
            error_code="REQUEST_TOO_LARGE",
        )


class FeatureDisabledError(BaseAPIException):
    """Feature is disabled."""

    def __init__(self, detail: str = "This feature is currently disabled"):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
            error_code="FEATURE_DISABLED",
        )


class NotImplementedYetError(BaseAPIException):
    """Placeholder endpoint not implemented yet."""

    def __init__(self, detail: str = "Not implemented yet"):
        super().__init__(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=detail,
            error_code="NOT_IMPLEMENTED",
        )


# ==========================================
# Envelope helpers
# ==========================================


def _error_envelope(code: str, message: str, details=None, field=None) -> dict:
    return {
        "success": False,
        "data": None,
        "error": {"code": code, "message": message, "details": details, "field": field},
        "meta": None,
    }


def _http_status_to_openai_code(status_code: int) -> str:
    return {
        400: "invalid_request_error",
        401: "authentication_error",
        402: "billing_hard_limit_reached",
        403: "permission_denied",
        404: "not_found",
        413: "invalid_request_error",
        422: "invalid_request_error",
        429: "rate_limit_exceeded",
        500: "internal_server_error",
        502: "upstream_error",
        503: "model_not_available",
    }.get(status_code, "api_error")


def _http_status_to_anthropic_type(status_code: int) -> str:
    return {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        413: "request_too_large",
        422: "invalid_request_error",
        429: "rate_limit_error",
        500: "api_error",
        502: "api_error",
        503: "overloaded_error",
        529: "overloaded_error",
    }.get(status_code, "api_error")


def serving_format_for_path(path: str, headers=None) -> Optional[str]:
    """Which serving format a request belongs to: 'openai' | 'anthropic' | 'ollama' | None.

    Delegates to ``app.services.serving.formats.format_for_request`` (serving.md
    §1-§2): explicit ``/openai/v1``, ``/anthropic/v1``, ``/ollama/api`` prefixes,
    bare ``/v1/messages*`` -> Anthropic, bare ``/v1/models*`` by headers
    (``anthropic-version`` / ``x-api-key`` without ``Authorization``), other bare
    ``/v1/*`` -> OpenAI, bare ``/api/*`` -> Ollama. ``headers`` is any mapping
    with lower-case keys (Starlette ``Headers`` works).
    """
    from app.services.serving.formats import format_for_request

    return format_for_request(path, headers)


def serving_format_for_scope(scope) -> Optional[str]:
    """``serving_format_for_path`` for a raw ASGI scope (pure-ASGI middlewares)."""
    from starlette.datastructures import Headers

    return serving_format_for_path(scope.get("path", ""), Headers(scope=scope))


def serving_error_body(
    fmt: Optional[str], status_code: int, message: str, code: Optional[str] = None
):
    """Error payload in the wire format of the serving prefix being used."""
    if fmt == "anthropic":
        return {
            "type": "error",
            "error": {
                "type": code or _http_status_to_anthropic_type(status_code),
                "message": message,
            },
        }
    if fmt == "ollama":
        return {"error": message}
    # OpenAI shape is also the default for unknown serving paths.
    return {
        "error": {
            "message": message,
            "type": "api_error",
            "code": code or _http_status_to_openai_code(status_code),
        }
    }


# ==========================================
# Handlers — admin API (envelope)
# ==========================================


async def base_exception_handler(request: Request, exc: BaseAPIException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_envelope(exc.error_code, exc.detail),
        headers=exc.headers,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    message = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_envelope("HTTP_ERROR", message),
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = [
        {
            "field": ".".join(str(loc) for loc in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_envelope("VALIDATION_ERROR", "Request validation failed", details=errors),
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    from app.core.logger import get_logger

    get_logger(__name__).opt(exception=exc).error(
        f"Unhandled exception on {request.method} {request.url.path} — "
        f"type={type(exc).__name__} msg={exc}"
    )
    message = str(exc) if settings.DEBUG else "An unexpected error occurred"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_envelope("INTERNAL_ERROR", message or "An unexpected error occurred"),
    )


# ==========================================
# Handlers — serving API (OpenAI / Anthropic / Ollama shapes)
# ==========================================


async def serving_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    fmt = serving_format_for_path(request.url.path, request.headers)
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get("message", str(detail))
        code = detail.get("code")
    else:
        message = str(detail)
        code = getattr(exc, "error_code", None)
        if code and fmt in ("openai", "anthropic"):
            # Our UPPER_SNAKE codes are not wire codes; map by status instead.
            code = None
    return JSONResponse(
        status_code=exc.status_code,
        content=serving_error_body(fmt, exc.status_code, message, code),
        headers=getattr(exc, "headers", None),
    )


async def serving_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    fmt = serving_format_for_path(request.url.path, request.headers)
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(part) for part in first.get("loc", []) if part != "body")
    message = f"{loc}: {first.get('msg', 'invalid request')}" if loc else "invalid request"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=serving_error_body(fmt, 422, message, "invalid_request_error"),
    )


async def serving_generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    from app.core.logger import get_logger

    get_logger(__name__).opt(exception=exc).error(
        f"Unhandled serving exception on {request.method} {request.url.path} — "
        f"type={type(exc).__name__} msg={exc}"
    )
    fmt = serving_format_for_path(request.url.path, request.headers)
    message = str(exc) if settings.DEBUG else "internal server error"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=serving_error_body(fmt, 500, message),
    )
