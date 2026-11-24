"""
Request-scoped context using Python ContextVar.

Captures request id, client IP, User-Agent and the authenticated principal so
audit/usage writers can read them without threading the Request through every
service call.
"""

from contextvars import ContextVar
from typing import Optional

from starlette.requests import HTTPConnection

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_request_ip: ContextVar[Optional[str]] = ContextVar("request_ip", default=None)
_request_ua: ContextVar[Optional[str]] = ContextVar("request_ua", default=None)
_request_actor: ContextVar[Optional[str]] = ContextVar("request_actor", default=None)


def set_request_context(
    ip: Optional[str],
    user_agent: Optional[str],
    request_id: Optional[str] = None,
    actor: Optional[str] = None,
) -> None:
    _request_ip.set(ip)
    _request_ua.set(user_agent)
    _request_id.set(request_id)
    _request_actor.set(actor)


def set_request_actor(actor: Optional[str]) -> None:
    """Called by auth dependencies once the admin or API key is resolved."""
    _request_actor.set(actor)


def get_request_id() -> Optional[str]:
    return _request_id.get()


def get_request_ip() -> Optional[str]:
    return _request_ip.get()


def get_request_ua() -> Optional[str]:
    return _request_ua.get()


def get_request_actor() -> Optional[str]:
    return _request_actor.get()


def get_client_ip_from_request(request: HTTPConnection) -> str:
    """Client IP; forwarded headers are honoured only from TRUSTED_PROXIES."""
    from app.services.security.client_ip import resolve_client_ip

    return resolve_client_ip(request.scope)


async def set_request_context_dep(request: HTTPConnection) -> None:
    """FastAPI global dependency: captures IP + UA + request id into ContextVars."""
    ip = get_client_ip_from_request(request)
    ua = request.headers.get("user-agent") or None
    set_request_context(ip or None, ua, getattr(request.state, "request_id", None))
