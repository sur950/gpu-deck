"""
Client IP resolution for IP rules (spoof-resistant).

``X-Forwarded-For`` / ``X-Real-IP`` are only believed when the TCP peer is a
trusted proxy (TRUSTED_PROXIES, loopback by default because Caddy fronts the
apps locally). The forwarded chain is walked from the right and the first
address that is not itself a trusted proxy is the client. A direct caller
therefore cannot dodge a block rule or slip into an allow-list by sending a
forged header.
"""

from __future__ import annotations

import ipaddress
from typing import List, Optional, Tuple

from app.core.logger import get_logger
from app.core.settings import settings
from starlette.types import Scope

logger = get_logger(__name__)

_cache: Tuple[str, List] = ("", [])


def _trusted_networks() -> List:
    global _cache
    raw = settings.TRUSTED_PROXIES
    if raw != _cache[0]:
        nets = []
        for value in settings.get_trusted_proxies():
            try:
                nets.append(ipaddress.ip_network(value, strict=False))
            except ValueError:
                logger.warning(f"TRUSTED_PROXIES: ignoring invalid entry {value!r}")
        _cache = (raw, nets)
    return _cache[1]


def is_trusted_proxy(ip: Optional[str]) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return any(addr.version == net.version and addr in net for net in _trusted_networks())


def _header(scope: Scope, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


_SCOPE_KEY = "gpudeck.client_ip"


def resolve_client_ip(scope: Scope) -> str:
    """Client address for an ASGI scope ('' when unknown); memoised on the scope."""
    cached = scope.get(_SCOPE_KEY)
    if cached is not None:
        return cached
    ip = _resolve(scope)
    try:
        scope[_SCOPE_KEY] = ip
    except TypeError:  # pragma: no cover - read-only mapping
        pass
    return ip


def _resolve(scope: Scope) -> str:
    client = scope.get("client")
    peer = client[0] if client else ""
    if not is_trusted_proxy(peer):
        return peer or ""
    forwarded = _header(scope, b"x-forwarded-for")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        for hop in reversed(hops):
            if not is_trusted_proxy(hop):
                return hop
        if hops:
            return hops[0]
    real_ip = _header(scope, b"x-real-ip")
    if real_ip:
        return real_ip.strip()
    return peer or ""


def resolve_request_ip(request) -> str:
    """Same as ``resolve_client_ip`` for a Starlette ``Request``."""
    return resolve_client_ip(request.scope)
