"""
uvicorn WebSocket protocol with mTLS peer identity.

uvicorn terminates TLS but never puts the client certificate into the ASGI
scope. This subclass of its ``websockets``-sans-I/O protocol copies the peer
certificate from the transport's ``ssl_object`` into
``scope["extensions"]["tls"]`` before the app runs::

    scope["extensions"]["tls"] = {
        "client_cert_der": b"...",           # None when no cert was presented
        "client_cert_pem": "-----BEGIN ...", # for logging / fingerprinting
        "tls_version": "TLSv1.3",
        "cipher": "TLS_AES_256_GCM_SHA384",
    }

Wire it with ``uvicorn.Config(..., ws="app.services.agent_hub.ws_protocol:MTLSWebSocketProtocol")``
(``app/agent_hub_main.py`` does this). The listener uses ``ssl_cert_reqs=CERT_OPTIONAL``
so ``POST /agent/v1/join`` works without a client cert while ``/agent/v1/ws``
refuses connections that present none (close 4003).
"""

from __future__ import annotations

import ssl
from typing import Any, Dict, Optional

from app.core.logger import get_logger
from uvicorn.protocols.websockets.websockets_sansio_impl import WebSocketsSansIOProtocol

logger = get_logger(__name__)


def tls_info_from_transport(transport: Any) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "client_cert_der": None,
        "client_cert_pem": None,
        "tls_version": None,
        "cipher": None,
    }
    if transport is None:
        return info
    ssl_object: Optional[ssl.SSLObject] = transport.get_extra_info("ssl_object")
    if ssl_object is None:
        return info
    try:
        der = ssl_object.getpeercert(binary_form=True)
    except ValueError:
        der = None
    if der:
        info["client_cert_der"] = der
        info["client_cert_pem"] = ssl.DER_cert_to_PEM_cert(der)
    info["tls_version"] = ssl_object.version()
    cipher = ssl_object.cipher()
    info["cipher"] = cipher[0] if cipher else None
    return info


class MTLSWebSocketProtocol(WebSocketsSansIOProtocol):
    """
    websockets-sansio protocol that exposes the client certificate to the ASGI app and
    reports the round trip of every keep-alive ping an agent answers
    (``app.services.agent_hub.latency``).
    """

    _gpudeck_node: Optional[str] = None

    async def run_asgi(self) -> None:  # type: ignore[override]
        extensions = self.scope.setdefault("extensions", {})
        tls = tls_info_from_transport(self.transport)
        extensions["tls"] = tls  # type: ignore[assignment]
        from app.services.agent_hub.latency import node_id_from_der

        self._gpudeck_node = node_id_from_der(tls.get("client_cert_der"))
        await super().run_asgi()

    def handle_pong(self, event: Any) -> None:  # type: ignore[override]
        pending = getattr(self, "pending_ping_payload", None)
        matched = pending is not None and bytes(event.data) == pending
        super().handle_pong(event)
        if matched and self._gpudeck_node:
            try:
                from app.services.agent_hub import latency

                latency.record(self._gpudeck_node, float(self.last_ping_rtt) * 1000.0)
            except Exception as exc:  # noqa: BLE001 - diagnostics only
                logger.debug(f"hub RTT not recorded: {exc}")

    def connection_lost(self, exc: Optional[Exception]) -> None:  # type: ignore[override]
        super().connection_lost(exc)
        if self._gpudeck_node:
            try:
                from app.services.agent_hub import latency

                latency.forget(self._gpudeck_node)
            except Exception:  # noqa: BLE001
                pass


__all__ = ["MTLSWebSocketProtocol", "tls_info_from_transport"]
