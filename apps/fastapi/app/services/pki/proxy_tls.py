"""
TLS verification of node-agent proxies (PKI side of protocol.md §6).

Node proxies serve their node certificate (``CN=<node_id>, O=gpudeck-nodes``,
``URI:gpudeck://node/<id>`` plus the DNS/IP SANs from the agent's CSR). A
generic hostname check is not the trust anchor here: proxies are dialled by
``proxy_address``, which may be an IP the CSR did not list or a NAT address.
The API layer therefore verifies

1. the chain against the internal CA (OpenSSL, ``CERT_REQUIRED``), and
2. ``CN == node id`` of the node the connection was opened for, looked up from
   the routing table (``nodes[node_id].proxy_url`` host:port). When the host is
   not in the table (jobs process before its first refresh, tests) the cert
   must still be a node certificate whose URI SAN names its own CN.

``node_proxy_ssl_context()`` returns an ``ssl.SSLContext`` subclass whose
SSLObject / SSLSocket run the check right after the handshake, so it works with
one pooled httpx client for every node (anyio uses ``wrap_bio``).
"""

from __future__ import annotations

import ssl
import uuid
from typing import Callable, Dict, Optional, Set
from urllib.parse import urlsplit

from app.core.logger import get_logger
from app.services.pki.ca import NODE_ORG
from cryptography import x509
from cryptography.x509.oid import NameOID

logger = get_logger(__name__)

NODE_URI_PREFIX = "gpudeck://node/"

# Override in tests: () -> {host_or_host:port -> {node ids}}.
_host_map_provider: Optional[Callable[[], Dict[str, Set[str]]]] = None


def set_host_map_provider(
    provider: Optional[Callable[[], Dict[str, Set[str]]]],
) -> None:
    global _host_map_provider
    _host_map_provider = provider


def _routing_host_map() -> Dict[str, Set[str]]:
    """``{host: {node_id}}`` from the API layer's in-process routing table."""
    if _host_map_provider is not None:
        return _host_map_provider()
    try:
        from app.services.serving.routing import get_routing_table

        nodes = get_routing_table().nodes or {}
    except Exception:  # noqa: BLE001 - routing table not importable/loaded
        return {}
    out: Dict[str, Set[str]] = {}
    for node_id, info in nodes.items():
        url = str((info or {}).get("proxy_url") or "")
        host = urlsplit(url if "://" in url else f"https://{url}").hostname
        if host:
            out.setdefault(host.lower(), set()).add(str(node_id))
    return out


def verify_node_peer(server_hostname: Optional[str], der: Optional[bytes]) -> str:
    """Return the node id of the peer certificate or raise ``SSLCertVerificationError``."""
    if not der:
        raise ssl.SSLCertVerificationError("node proxy presented no certificate")
    cert = x509.load_der_x509_certificate(der)
    cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    orgs = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
    cn = str(cns[0].value) if cns else ""
    org = str(orgs[0].value) if orgs else ""
    try:
        node_id = str(uuid.UUID(cn))
    except ValueError:
        raise ssl.SSLCertVerificationError(f"proxy certificate CN {cn!r} is not a node id")
    if org != NODE_ORG:
        raise ssl.SSLCertVerificationError(f"proxy certificate O={org!r} is not {NODE_ORG}")
    expected = _routing_host_map().get((server_hostname or "").lower().strip("[]"))
    if expected:
        if node_id not in expected:
            raise ssl.SSLCertVerificationError(
                f"proxy {server_hostname} presented node {node_id}, expected "
                f"{', '.join(sorted(expected))}"
            )
        return node_id
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        uris = ext.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound:
        uris = []
    if f"{NODE_URI_PREFIX}{node_id}" not in uris:
        raise ssl.SSLCertVerificationError("proxy certificate URI SAN does not match its CN")
    return node_id


class _NodeProxySSLObject(ssl.SSLObject):
    def do_handshake(self) -> None:
        super().do_handshake()
        verify_node_peer(self.server_hostname, self.getpeercert(binary_form=True))


class _NodeProxySSLSocket(ssl.SSLSocket):
    def do_handshake(self, block: bool = False) -> None:  # type: ignore[override]
        super().do_handshake(block)
        verify_node_peer(self.server_hostname, self.getpeercert(binary_form=True))


class NodeProxySSLContext(ssl.SSLContext):
    """Chain verification by OpenSSL, identity by ``CN == node id`` (no hostname check)."""

    sslobject_class = _NodeProxySSLObject
    sslsocket_class = _NodeProxySSLSocket


def node_proxy_ssl_context() -> ssl.SSLContext:
    """Client context for node proxies; the caller loads the CA bundle and client cert."""
    ctx = NodeProxySSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx
