"""
PKI unit tests: CA generation, node/service CSR signing, SANs, CRL, and
the PkiService CA bootstrap against an in-memory secret store (no DB).
"""

import uuid

import pytest
from app.services.pki import ca as cax
from app.services.pki.secret_store import MemorySecretStore, dumps_secret
from app.services.pki.service import PkiService, reset_ca_cache_for_tests
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def ca_pair():
    return cax.generate_ca("ecdsa_p256", validity_days=30)


def test_generated_ca_is_a_ca(ca_pair):
    _, cert = ca_pair
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is True
    assert cert.subject == cert.issuer
    assert len(cax.fingerprint_sha256(cert)) == 64


def test_node_csr_signed_with_hub_chosen_subject(ca_pair):
    key, ca_cert = ca_pair
    node_id = uuid.uuid4()
    agent_key = cax.generate_private_key("ecdsa_p256")
    csr = cax.build_csr(agent_key, "pending")
    cert = cax.sign_node_csr(key, ca_cert, csr, node_id, 365)
    info = cax.cert_info(cert)
    assert info.common_name == str(node_id)
    assert info.organization == cax.NODE_ORG
    assert f"gpudeck://node/{node_id}" in cax.cert_sans(cert)
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.CLIENT_AUTH in eku
    # The node cert also serves the agent proxy (protocol.md §6).
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    # chains to the CA
    ca_cert.public_key().verify(
        cert.signature,
        cert.tbs_certificate_bytes,
        cax.ec.ECDSA(cert.signature_hash_algorithm),
    )


def test_hub_service_cert_has_server_auth_and_sans(ca_pair):
    key, ca_cert = ca_pair
    svc_key = cax.generate_private_key("ecdsa_p256")
    csr = cax.build_csr(svc_key, "x", cax.SERVICE_ORG)
    cert = cax.sign_service_csr(
        key, ca_cert, csr, "agent_hub", None, ["localhost", "127.0.0.1"], 30
    )
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "agent-hub"
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    assert set(cax.cert_sans(cert)) == {"localhost", "127.0.0.1"}


def test_tampered_csr_is_rejected(ca_pair):
    key, ca_cert = ca_pair
    good = cax.build_csr(cax.generate_private_key("ecdsa_p256"))
    with pytest.raises(Exception):
        cax.sign_node_csr(key, ca_cert, good.replace("A", "B", 5), uuid.uuid4(), 30)


def test_crl_lists_revoked_serials(ca_pair):
    from datetime import datetime, timezone

    key, ca_cert = ca_pair
    pem = cax.build_crl(key, ca_cert, [("1f", datetime.now(timezone.utc))])
    crl = x509.load_pem_x509_crl(pem.encode())
    assert crl.get_revoked_certificate_by_serial_number(0x1F) is not None
    assert crl.is_signature_valid(ca_cert.public_key())


class _NoEvents:
    def __init__(self):
        self.events = []

    async def audit(self, event_type, **kwargs):
        self.events.append(event_type)


@pytest.mark.asyncio
async def test_pki_service_generates_once_then_loads(monkeypatch):
    reset_ca_cache_for_tests()
    store = MemorySecretStore()
    events = _NoEvents()
    service = PkiService(cert_repo=None, store=store, events=events)
    first = await service.ensure_ca()
    assert "internal-ca" in store.values
    assert events.events == ["pki.ca_created"]
    reset_ca_cache_for_tests()
    second = await PkiService(cert_repo=None, store=store, events=_NoEvents()).ensure_ca()
    assert second.fingerprint == first.fingerprint
    reset_ca_cache_for_tests()


@pytest.mark.asyncio
async def test_pki_service_imports_existing_material():
    reset_ca_cache_for_tests()
    key, cert = cax.generate_ca("ecdsa_p256", validity_days=5)
    store = MemorySecretStore()
    store.values["internal-ca"] = dumps_secret(
        {"key_pem": cax.key_to_pem(key), "cert_pem": cax.cert_to_pem(cert)}
    )
    material = await PkiService(cert_repo=None, store=store, events=_NoEvents()).ensure_ca()
    assert material.fingerprint == cax.fingerprint_sha256(cert)
    reset_ca_cache_for_tests()
