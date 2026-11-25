"""
Internal CA primitives: pure ``cryptography`` helpers with no I/O.

- generate a CA (ECDSA P-384 default) and self-signed certificate
- sign node CSRs (``CN=<node_id>``, ``O=gpudeck-nodes``, SAN
  ``URI:gpudeck://node/<id>``) and service CSRs (``O=gpudeck-services``)
- generate key pairs / CSRs for service certs issued server-side
- parse certificates, fingerprints, key pins (SubjectPublicKeyInfo sha256), serials, and
  build an X.509 CRL
- sign detached documents with the CA key (the node security policy, protocol.md §13)

``PkiService`` wraps these with the vault and the ``node_certificates`` table.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import uuid
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from app.core.logger import get_logger
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

logger = get_logger(__name__)

NODE_ORG = "gpudeck-nodes"
SERVICE_ORG = "gpudeck-services"
CA_COMMON_NAME = "GPU Deck Internal CA"
SERVICE_DEFAULT_CN = {
    "api_layer": "api-layer",
    "control_plane": "control-plane",
    "agent_hub": "agent-hub",
}

PrivateKey = Union[ec.EllipticCurvePrivateKey, ed25519.Ed25519PrivateKey, rsa.RSAPrivateKey]


@dataclass
class CertInfo:
    serial: str
    fingerprint_sha256: str
    subject: str
    not_before: dt.datetime
    not_after: dt.datetime
    certificate_pem: str
    common_name: Optional[str]
    organization: Optional[str]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def generate_private_key(key_type: str = "ecdsa_p384") -> PrivateKey:
    if key_type == "ecdsa_p384":
        return ec.generate_private_key(ec.SECP384R1())
    if key_type == "ecdsa_p256":
        return ec.generate_private_key(ec.SECP256R1())
    if key_type == "ed25519":
        return ed25519.Ed25519PrivateKey.generate()
    if key_type == "rsa_3072":
        return rsa.generate_private_key(public_exponent=65537, key_size=3072)
    raise ValueError(f"unsupported key type {key_type}")


def key_to_pem(key: PrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def key_from_pem(pem: str) -> PrivateKey:
    return serialization.load_pem_private_key(pem.encode(), password=None)  # type: ignore


def cert_to_pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def cert_from_pem(pem: str) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pem.encode())


def csr_from_pem(pem: str) -> x509.CertificateSigningRequest:
    return x509.load_pem_x509_csr(pem.encode())


def fingerprint_sha256(cert: x509.Certificate) -> str:
    return cert.fingerprint(hashes.SHA256()).hex()


def spki_sha256_of_key(public_key) -> str:
    """sha256 hex of a public key's DER SubjectPublicKeyInfo (what agents pin)."""
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


def spki_sha256(cert: x509.Certificate) -> str:
    """Key pin of a certificate: survives re-issuing a certificate for the same key."""
    return spki_sha256_of_key(cert.public_key())


def sign_detached(key: PrivateKey, data: bytes) -> Tuple[bytes, str]:
    """
    Sign ``data`` with a CA key. Returns ``(signature, algorithm)`` where the algorithm name is
    what the agent derives from the CA key type (``common.json#/$defs/SignedPolicy``):
    P-256 → ``ecdsa-p256-sha256``, P-384 → ``ecdsa-p384-sha384`` (DER signatures), Ed25519,
    RSA → PKCS#1 v1.5 with SHA-256.
    """
    if isinstance(key, ec.EllipticCurvePrivateKey):
        if isinstance(key.curve, ec.SECP384R1):
            return key.sign(data, ec.ECDSA(hashes.SHA384())), "ecdsa-p384-sha384"
        if isinstance(key.curve, ec.SECP256R1):
            return key.sign(data, ec.ECDSA(hashes.SHA256())), "ecdsa-p256-sha256"
        raise ValueError(f"unsupported CA curve {key.curve.name}")
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return key.sign(data), "ed25519"
    if isinstance(key, rsa.RSAPrivateKey):
        return key.sign(data, padding.PKCS1v15(), hashes.SHA256()), "rsa-pkcs1-sha256"
    raise ValueError("unsupported CA key type")


def has_server_auth(cert: x509.Certificate) -> bool:
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        return False
    return ExtendedKeyUsageOID.SERVER_AUTH in eku


def serial_hex(cert: x509.Certificate) -> str:
    return format(cert.serial_number, "x")


def _sig_hash(key: PrivateKey):
    return None if isinstance(key, ed25519.Ed25519PrivateKey) else hashes.SHA256()


def generate_ca(
    key_type: str = "ecdsa_p384",
    common_name: str = CA_COMMON_NAME,
    validity_days: int = 3650,
) -> Tuple[PrivateKey, x509.Certificate]:
    key = generate_private_key(key_type)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "GPU Deck"),
        ]
    )
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=validity_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, _sig_hash(key))
    )
    return key, cert


def _san_entries(sans: Iterable[str]) -> List[x509.GeneralName]:
    out: List[x509.GeneralName] = []
    for san in sans:
        san = san.strip()
        if not san:
            continue
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(san)))
        except ValueError:
            if san.startswith("gpudeck://") or "://" in san:
                out.append(x509.UniformResourceIdentifier(san))
            else:
                out.append(x509.DNSName(san))
    return out


def sign_csr(
    ca_key: PrivateKey,
    ca_cert: x509.Certificate,
    csr: x509.CertificateSigningRequest,
    subject: x509.Name,
    sans: Sequence[x509.GeneralName],
    validity_days: int,
    server_auth: bool = False,
    client_auth: bool = True,
) -> x509.Certificate:
    """Sign a CSR with the hub-chosen subject (the CSR's own subject is ignored)."""
    if not csr.is_signature_valid:
        raise ValueError("CSR signature is invalid")
    now = _now()
    ekus = []
    if client_auth:
        ekus.append(ExtendedKeyUsageOID.CLIENT_AUTH)
    if server_auth:
        ekus.append(ExtendedKeyUsageOID.SERVER_AUTH)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=validity_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage(ekus), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False)
    )
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(list(sans)), critical=False)
    return builder.sign(ca_key, _sig_hash(ca_key))


def node_subject(node_id: Union[str, uuid.UUID]) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, str(node_id)),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, NODE_ORG),
        ]
    )


def service_subject(common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, SERVICE_ORG),
        ]
    )


def sign_node_csr(
    ca_key: PrivateKey,
    ca_cert: x509.Certificate,
    csr_pem: str,
    node_id: Union[str, uuid.UUID],
    validity_days: int,
) -> x509.Certificate:
    """
    Node agent certificate: ``CN=<node_id>, O=gpudeck-node`` + ``URI:gpudeck://node/<id>``
    plus the DNS / IP SANs the agent asked for in its CSR (hostname, IPs,
    proxy address), so the same cert can serve the node proxy over TLS. Both
    clientAuth (hub mTLS) and serverAuth (proxy) EKUs are set. The API layer
    still verifies the proxy by CA chain + ``CN == node id`` (SANs are a
    convenience for generic TLS clients, not the trust anchor).
    """
    csr = csr_from_pem(csr_pem)
    sans: List[x509.GeneralName] = [x509.UniformResourceIdentifier(f"gpudeck://node/{node_id}")]
    sans.extend(csr_dns_ip_sans(csr))
    return sign_csr(
        ca_key,
        ca_cert,
        csr,
        node_subject(node_id),
        sans,
        validity_days,
        server_auth=True,
        client_auth=True,
    )


MAX_CSR_SANS = 32


def csr_dns_ip_sans(csr: x509.CertificateSigningRequest) -> List[x509.GeneralName]:
    """DNS names and IP addresses requested in the CSR (wildcards and URIs are dropped)."""
    try:
        ext = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    out: List[x509.GeneralName] = []
    seen = set()
    for name in ext.get_values_for_type(x509.DNSName):
        name = str(name).strip().lower().rstrip(".")
        if not name or "*" in name or len(name) > 253 or ("dns", name) in seen:
            continue
        seen.add(("dns", name))
        out.append(x509.DNSName(name))
    for ip in ext.get_values_for_type(x509.IPAddress):
        if ("ip", str(ip)) in seen:
            continue
        seen.add(("ip", str(ip)))
        out.append(x509.IPAddress(ip))
    return out[:MAX_CSR_SANS]


def sign_service_csr(
    ca_key: PrivateKey,
    ca_cert: x509.Certificate,
    csr_pem: str,
    kind: str,
    common_name: Optional[str],
    sans: Iterable[str],
    validity_days: int,
) -> x509.Certificate:
    csr = csr_from_pem(csr_pem)
    cn = common_name or SERVICE_DEFAULT_CN[kind]
    # The API layer's key is also the server identity of the outbound tunnel listener the
    # agents dial (protocol.md §13), so its certificate carries serverAuth as well.
    server_auth = kind in ("agent_hub", "api_layer")
    return sign_csr(
        ca_key,
        ca_cert,
        csr,
        service_subject(cn),
        _san_entries(sans),
        validity_days,
        server_auth=server_auth,
        client_auth=True,
    )


def build_csr(key: PrivateKey, common_name: str = "pending", organization: str = NODE_ORG) -> str:
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
                ]
            )
        )
        .sign(key, _sig_hash(key))
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def cert_info(cert: x509.Certificate) -> CertInfo:
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    org = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
    return CertInfo(
        serial=serial_hex(cert),
        fingerprint_sha256=fingerprint_sha256(cert),
        subject=cert.subject.rfc4514_string(),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        certificate_pem=cert_to_pem(cert),
        common_name=str(cn[0].value) if cn else None,
        organization=str(org[0].value) if org else None,
    )


def cert_info_from_der(der: bytes) -> CertInfo:
    return cert_info(x509.load_der_x509_certificate(der))


def build_crl(
    ca_key: PrivateKey,
    ca_cert: x509.Certificate,
    revoked: Iterable[Tuple[str, dt.datetime]],
    next_update_days: int = 7,
) -> str:
    """X.509 CRL PEM from (serial_hex, revoked_at) pairs."""
    now = _now()
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(now)
        .next_update(now + dt.timedelta(days=next_update_days))
    )
    for serial, revoked_at in revoked:
        entry = (
            x509.RevokedCertificateBuilder()
            .serial_number(int(serial, 16))
            .revocation_date(revoked_at)
            .build()
        )
        builder = builder.add_revoked_certificate(entry)
    return builder.sign(ca_key, _sig_hash(ca_key)).public_bytes(serialization.Encoding.PEM).decode()


def cert_sans(cert: x509.Certificate) -> List[str]:
    """DNS names, IPs and URIs from the SubjectAlternativeName extension."""
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    out: List[str] = []
    out.extend(ext.get_values_for_type(x509.DNSName))
    out.extend(str(ip) for ip in ext.get_values_for_type(x509.IPAddress))
    out.extend(ext.get_values_for_type(x509.UniformResourceIdentifier))
    return out


def pem_fingerprint(pem: str) -> str:
    return fingerprint_sha256(cert_from_pem(pem))


def der_fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()
