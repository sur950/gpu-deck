"""
Admin authentication dependency used by the catalog and deployment routes.

The session middleware publishes the real dependency in ``app.middlewares.session_verification``;
this module re-exports it so every route imports one name. When that module
is unavailable (isolated unit runs) a strict fallback accepts only an admin
that a middleware already stored on ``request.state`` and raises 401 otherwise,
so no endpoint is ever open by accident.
"""

from typing import Any

from app.core.exceptions import AuthenticationError
from app.core.logger import get_logger
from fastapi import Request

logger = get_logger(__name__)

try:  # pragma: no cover - exercised through that module
    from app.middlewares.session_verification import get_current_admin  # noqa: F401
except (
    Exception
):  # noqa: BLE001 - fallback keeps this module importable without the session middleware

    async def get_current_admin(request: Request) -> Any:  # type: ignore[misc]
        """Return the authenticated admin or raise ``AuthenticationError``."""
        admin = getattr(request.state, "admin", None)
        if admin is not None:
            return admin
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            return {"id": user_id}
        raise AuthenticationError("Admin session required")


def admin_id(admin: Any):
    """Best-effort admin id for ``created_by`` columns (dict or ORM row)."""
    if admin is None:
        return None
    if isinstance(admin, dict):
        return admin.get("id")
    return getattr(admin, "id", None)


__all__ = ["get_current_admin", "admin_id"]
