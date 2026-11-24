"""
API route wrappers and decorators for error handling and response normalization.

Every admin route is decorated with ``@handle_exceptions`` and returns
``success_response(data, meta)`` — the ``{success, data, error, meta}``
envelope. List endpoints pass ``meta={"total", "page", "page_size", "has_more"}``.
"""

from functools import wraps
from typing import Any, Callable, Dict, Generic, Optional, TypeVar

from app.core.exceptions import BaseAPIException
from app.core.logger import get_logger
from app.core.settings import settings
from fastapi import (  # noqa: F401 — kept in __globals__ so FastAPI resolves "Request" ForwardRefs
    Request,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

logger = get_logger(__name__)

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """Normalized API response format."""

    success: bool
    data: Optional[T] = None
    error: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None


class ErrorDetail(BaseModel):
    """Error details for failed responses."""

    code: str
    message: str
    details: Optional[Any] = None
    field: Optional[str] = None


def success_response(data: Any = None, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create a successful response, auto-serializing Pydantic models via model_dump()."""
    if isinstance(data, BaseModel):
        data = data.model_dump()
    elif isinstance(data, list):
        data = [item.model_dump() if isinstance(item, BaseModel) else item for item in data]
    return {"success": True, "data": data, "error": None, "meta": meta}


def paginated_meta(total: int, page: int, page_size: int) -> Dict[str, Any]:
    """Standard list meta block."""
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
    }


def error_response(
    code: str,
    message: str,
    details: Optional[Any] = None,
    field: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create an error response (details hidden outside DEBUG)."""
    return {
        "success": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "details": details if settings.DEBUG else None,
            "field": field,
        },
        "meta": meta,
    }


def handle_exceptions(func: Callable) -> Callable:
    """
    Decorator to handle exceptions in async API routes.

    - Starlette Response objects (StreamingResponse etc.) pass through unchanged
    - a dict with a "success" key is returned as-is; anything else is wrapped
    - BaseAPIException -> its status + error_code; StarletteHTTPException -> HTTP_ERROR
    - anything else -> 500 INTERNAL_ERROR (message hidden unless DEBUG), logged with stack
    """

    @wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        try:
            result = await func(*args, **kwargs)
            if isinstance(result, Response):
                return result
            if isinstance(result, dict) and "success" in result:
                return result
            return success_response(data=result)

        except BaseAPIException as e:
            logger.warning(f"API Exception in {func.__name__}: {e.error_code} - {e.detail}")
            return JSONResponse(
                status_code=e.status_code,
                content=error_response(code=e.error_code, message=e.detail),
                headers=e.headers,
            )

        except StarletteHTTPException as e:
            return JSONResponse(
                status_code=e.status_code,
                content=error_response(code="HTTP_ERROR", message=str(e.detail)),
                headers=getattr(e, "headers", None),
            )

        except Exception as e:
            logger.exception(f"Unhandled exception in {func.__name__}: {e}")
            return JSONResponse(
                status_code=500,
                content=error_response(
                    code="INTERNAL_ERROR",
                    message=(
                        str(e)
                        if settings.DEBUG
                        else "An unexpected error occurred. Please try again later."
                    ),
                    details=({"exception_type": type(e).__name__} if settings.DEBUG else None),
                ),
            )

    return wrapper


__all__ = [
    "APIResponse",
    "ErrorDetail",
    "success_response",
    "paginated_meta",
    "error_response",
    "handle_exceptions",
]
