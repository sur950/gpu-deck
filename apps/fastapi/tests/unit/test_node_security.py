"""
Node security policy and serving modes (agent protocol §13), units only: detached CA
signatures for every CA key type, key pins, cluster ``config.node_security`` validation,
tunnel endpoint derivation, ``tunnel://`` routing URLs and the tunnel peer check.
"""

from __future__ import annotations

import base64
import hashlib
import json
import ssl
import uuid
from types import SimpleNamespace

import pytest
from app.core.settings import settings
from app.services.pki import ca as cax
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding


@pytest.mark.parametrize(
    "key_type,algorithm",
    [
        ("ecdsa_p384", "ecdsa-p384-sha384"),
        ("ecdsa_p256", "ecdsa-p256-sha256"),
        ("ed25519", "ed25519"),
        ("rsa_3072", "rsa-pkcs1-sha256"),
    ],
)
def test_detached_signature_matches_the_agent_algorithm(key_type, algorithm):
    key, cert = cax.generate_ca(key_type)
    data = b'{"v":1}'
    sig, alg = cax.sign_detached(key, data)
    assert alg == algorithm
    pub = cert.public_key()
    if key_type.startswith("ecdsa"):
        h = hashes.SHA384() if key_type == "ecdsa_p384" else hashes.SHA256()
        pub.verify(sig, data, ec.ECDSA(h))
    elif key_type == "ed25519":
        pub.verify(sig, data)
    else:
        pub.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())


def test_spki_pin_survives_reissuing_for_the_same_key():
    ca_key, ca_cert = cax.generate_ca("ecdsa_p256")
    key = cax.generate_private_key("ecdsa_p256")
    csr = cax.build_csr(key, "api-layer", cax.SERVICE_ORG)
    a = cax.sign_service_csr(ca_key, ca_cert, csr, "api_layer", None, [], 30)
    b = cax.sign_service_csr(ca_key, ca_cert, csr, "api_layer", None, ["x.local"], 60)
    assert cax.fingerprint_sha256(a) != cax.fingerprint_sha256(b)
    assert cax.spki_sha256(a) == cax.spki_sha256(b) == cax.spki_sha256_of_key(key.public_key())
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert cax.spki_sha256(a) == hashlib.sha256(der).hexdigest()
    # The API layer certificate is also the tunnel server identity.
    assert cax.has_server_auth(a)
    cp = cax.sign_service_csr(ca_key, ca_cert, csr, "control_plane", None, [], 30)
    assert not cax.has_server_auth(cp)


def test_cluster_node_security_config_is_validated():
    from app.schemas.fleet_schema import ClusterCreate, ClusterUpdate

    c = ClusterCreate(
        name="c",
        type="bare_metal",
        config={
            "node_security": {
                "proxy_allowed_source_cidrs": ["10.0.0.5", "192.168.1.7/24"],
                "ssh_default_allowed_cidrs": [],
            },
            "other": 1,
        },
    )
    assert c.config["node_security"]["proxy_allowed_source_cidrs"] == [
        "10.0.0.5/32",
        "192.168.1.0/24",
    ]
    assert c.config["other"] == 1
    for bad in (
        {"node_security": {"proxy_allowed_source_cidrs": ["nope"]}},
        {"node_security": {"allow_everyone": True}},
        {"node_security": ["10.0.0.0/8"]},
    ):
        with pytest.raises(ValueError):
            ClusterUpdate(config=bad)
    assert ClusterUpdate(config=None).config is None


def test_tunnel_endpoints_default_to_the_hub_host(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_TUNNEL_PUBLIC_URLS", "")
    monkeypatch.setattr(settings, "AGENT_HUB_PUBLIC_URL", "https://cp.example.com:8443")
    monkeypatch.setattr(settings, "AGENT_TUNNEL_PORT", 8444)
    monkeypatch.setattr(settings, "AGENT_TUNNEL_LISTENERS", 2)
    assert settings.get_tunnel_endpoints() == ["cp.example.com:8444", "cp.example.com:8445"]
    monkeypatch.setattr(
        settings, "AGENT_TUNNEL_PUBLIC_URLS", "api-1.internal:9000, tls://[2001:db8::7]:8444,a"
    )
    assert settings.get_tunnel_endpoints() == [
        "api-1.internal:9000",
        "[2001:db8::7]:8444",
        "a:8444",
    ]


def test_routing_uses_tunnel_urls_for_tunnel_nodes():
    from app.services.deployments.routing_publisher import proxy_url
    from app.services.serving.tunnel import tunnel_node
    from app.services.serving.upstream import origin_of

    nid = uuid.uuid4()
    tunnel = SimpleNamespace(
        id=nid, metadata_={"serving_mode": "tunnel"}, proxy_address="10.0.0.5", proxy_port=9443
    )
    direct = SimpleNamespace(
        id=nid, metadata_={"serving_mode": "direct"}, proxy_address="10.0.0.5", proxy_port=9443
    )
    legacy = SimpleNamespace(id=nid, metadata_={}, proxy_address="10.0.0.5", proxy_port=9443)
    assert proxy_url(tunnel) == f"tunnel://{nid}"
    assert proxy_url(direct) == proxy_url(legacy) == "https://10.0.0.5:9443"
    assert tunnel_node(f"tunnel://{nid}/instances/x/v1/chat") == str(nid)
    assert tunnel_node("https://10.0.0.5:9443") is None
    assert origin_of(f"tunnel://{nid}/instances/x") == f"tunnel://{nid}"


def _pki():
    ca_key, ca_cert = cax.generate_ca("ecdsa_p256")
    node_id = uuid.uuid4()
    nkey = cax.generate_private_key("ecdsa_p256")
    node = cax.sign_node_csr(
        ca_key, ca_cert, cax.build_csr(nkey, "pending", cax.NODE_ORG), node_id, 30
    )
    skey = cax.generate_private_key("ecdsa_p256")
    svc = cax.sign_service_csr(
        ca_key,
        ca_cert,
        cax.build_csr(skey, "api-layer", cax.SERVICE_ORG),
        "api_layer",
        None,
        [],
        30,
    )
    return node_id, node, svc


def test_tunnel_peer_check():
    from app.services.serving.tunnel import check_peer

    node_id, node, svc = _pki()
    der = node.public_bytes(serialization.Encoding.DER)
    assert check_peer(der, set()) == str(node_id)
    with pytest.raises(ssl.SSLCertVerificationError):
        check_peer(der, {hashlib.sha256(der).hexdigest()})  # revoked
    with pytest.raises(ssl.SSLCertVerificationError):
        check_peer(svc.public_bytes(serialization.Encoding.DER), set())  # not a node cert
    with pytest.raises(ssl.SSLCertVerificationError):
        check_peer(None, set())


def test_policy_document_shape_validates_against_the_contract():
    from app.services.agent_hub.protocol import validate_payload

    doc = {
        "v": 1,
        "node_id": str(uuid.uuid4()),
        "serial": 1764000000000,
        "issued_at": "2026-11-24T10:00:00.000Z",
        "hub": {"spki_sha256": ["a" * 64]},
        "api_layer": {"spki_sha256": ["b" * 64], "subjects": ["CN=api-layer,O=gpudeck-services"]},
        "tunnel": {"endpoints": ["cp.example.com:8444"], "connections": 2},
        "direct": {"allowed_source_cidrs": ["10.0.0.0/8"]},
        "ssh": {"default_allowed_cidrs": []},
        "ingress_report": {"interval_seconds": 60, "burst_threshold": 20},
    }
    validate_payload("security_policy", doc)
    key, cert = cax.generate_ca("ecdsa_p384")
    body = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode()
    sig, alg = cax.sign_detached(key, body)
    signed = {
        "policy": base64.b64encode(body).decode(),
        "signature": base64.b64encode(sig).decode(),
        "algorithm": alg,
    }
    validate_payload("config_push", {"config_version": 1, "full": False, "security": signed})
    assert isinstance(cert, x509.Certificate)


def test_installer_applies_the_firewall_and_pins_the_hub_ca(monkeypatch):
    import subprocess

    from app.services.nodes import agent_downloads as dl

    monkeypatch.setattr(settings, "AGENT_HUB_PUBLIC_URL", "https://hub.example:8443")
    monkeypatch.setattr(settings, "AGENT_HUB_TLS_CERT_FILE", None)
    monkeypatch.setattr(dl.download_settings, "AGENT_DOWNLOAD_BASE_URL", "")
    ca = "-----BEGIN CERTIFICATE-----\nMII\n-----END CERTIFICATE-----"
    script = dl.install_script(
        "gdj_x", hub_url="https://hub.example:8443", ca_fingerprint="ab" * 32, ca_pem=ca
    )
    assert 'SERVING_MODE="${GPUDECK_SERVING_MODE:-tunnel}"' in script
    assert 'FIREWALL="${GPUDECK_FIREWALL:-1}"' in script
    assert "STRICT_CA=1" in script, "hub downloads never fall back to the system trust store"
    assert "firewall apply $PRESERVE" in script and "firewall status" in script
    assert "AmbientCapabilities=CAP_NET_ADMIN" in script
    assert "--ca-fingerprint $CA_FINGERPRINT" in script
    subprocess.run(["sh", "-n"], input=script.encode(), check=True)
    direct = dl.install_script(
        "gdj_x",
        hub_url="https://hub.example:8443",
        ca_fingerprint="ab" * 32,
        ca_pem=ca,
        serving_mode="direct",
        firewall=False,
    )
    assert 'SERVING_MODE="${GPUDECK_SERVING_MODE:-direct}"' in direct
    assert 'FIREWALL="${GPUDECK_FIREWALL:-0}"' in direct
    subprocess.run(["sh", "-n"], input=direct.encode(), check=True)
    cmd = dl.install_command("gdj_x", ca, serving_mode="direct", firewall=False)
    assert "/agent/v1/install.sh?mode=direct&firewall=0" in cmd
    assert "install.sh?" not in dl.install_command("gdj_x", ca)
