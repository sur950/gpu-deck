//! Key generation, CSRs and certificate inspection (rcgen + x509-parser).

use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, TimeZone, Utc};
use gpudeck_protocol::KeyType;
use rcgen::{CertificateParams, DistinguishedName, DnType, KeyPair, PublicKeyData};
use rustls_pki_types::CertificateDer;
use sha2::{Digest, Sha256};

pub const NODE_ORG: &str = "gpudeck-nodes";
pub const SERVICES_ORG: &str = "gpudeck-services";

/// Generate a key pair of the requested type and return it PEM encoded (PKCS#8).
pub fn generate_key(key_type: KeyType) -> Result<KeyPair> {
    let alg = match key_type {
        KeyType::EcdsaP256 => &rcgen::PKCS_ECDSA_P256_SHA256,
        KeyType::Ed25519 => &rcgen::PKCS_ED25519,
        KeyType::Rsa3072 => {
            return Err(anyhow!("rsa_3072 key generation is not supported by this agent build (use ecdsa_p256 or ed25519)"))
        }
    };
    KeyPair::generate_for(alg).context("generate key pair")
}

/// Build the join/rotation CSR: `CN=pending, O=gpudeck-nodes` (the hub overwrites the subject).
pub fn build_csr(key: &KeyPair, cn: &str) -> Result<String> {
    build_csr_with_sans(key, cn, &[])
}

/// CSR with DNS / IP subject alternative names (host name and addresses of the proxy listener),
/// so a hub that copies requested SANs issues a certificate valid for `proxy_address`.
pub fn build_csr_with_sans(key: &KeyPair, cn: &str, sans: &[String]) -> Result<String> {
    let mut params = CertificateParams::new(Vec::<String>::new()).context("csr params")?;
    for s in sans {
        if let Ok(ip) = s.parse::<std::net::IpAddr>() {
            params.subject_alt_names.push(rcgen::SanType::IpAddress(ip));
        } else if let Ok(dns) = s.clone().try_into() {
            params.subject_alt_names.push(rcgen::SanType::DnsName(dns));
        }
    }
    let mut dn = DistinguishedName::new();
    dn.push(DnType::CommonName, cn);
    dn.push(DnType::OrganizationName, NODE_ORG);
    params.distinguished_name = dn;
    let csr = params.serialize_request(key).context("serialize csr")?;
    csr.pem().context("csr pem")
}

pub fn parse_key_pem(pem: &str) -> Result<KeyPair> {
    KeyPair::from_pem(pem).context("parse private key")
}

/// sha256 hex of the DER certificate.
pub fn fingerprint_sha256(der: &[u8]) -> String {
    hex::encode(Sha256::digest(der))
}

/// Parse every certificate in a PEM bundle.
pub fn parse_cert_bundle(pem: &str) -> Result<Vec<CertificateDer<'static>>> {
    let mut reader = std::io::Cursor::new(pem.as_bytes());
    let certs: Vec<_> = rustls_pemfile::certs(&mut reader)
        .collect::<Result<_, _>>()
        .context("parse certificate pem")?;
    if certs.is_empty() {
        return Err(anyhow!("no certificates found in PEM"));
    }
    Ok(certs)
}

/// Parsed facts about an X.509 certificate.
#[derive(Debug, Clone, PartialEq)]
pub struct CertInfo {
    pub common_name: Option<String>,
    pub organization: Option<String>,
    /// Lowercase hex, no separators, no leading zeros.
    pub serial_hex: String,
    pub fingerprint_sha256: String,
    pub not_before: DateTime<Utc>,
    pub not_after: DateTime<Utc>,
    pub is_ca: bool,
    /// sha256 hex of the DER SubjectPublicKeyInfo (what key pins name; survives re-issuing a
    /// certificate for the same key).
    pub spki_sha256: String,
}

impl CertInfo {
    /// `O=<org>,CN=<cn>` style subject string used for allow-list matching.
    pub fn matches_subject_rule(&self, rule: &str) -> bool {
        // Rule forms: "O=gpudeck-services", "CN=api-layer", "O=x,CN=y" (all parts must match).
        let parts: Vec<&str> = rule
            .split(',')
            .map(str::trim)
            .filter(|p| !p.is_empty())
            .collect();
        // An empty rule matches nothing (never "anything").
        !parts.is_empty()
            && parts.into_iter().all(|part| match part.split_once('=') {
                Some((k, v)) => match k.trim().to_ascii_uppercase().as_str() {
                    "O" => self.organization.as_deref() == Some(v.trim()),
                    "CN" => self.common_name.as_deref() == Some(v.trim()),
                    _ => false,
                },
                None => false,
            })
    }
}

pub fn inspect_cert(der: &[u8]) -> Result<CertInfo> {
    let (_, cert) =
        x509_parser::parse_x509_certificate(der).map_err(|e| anyhow!("parse x509: {e}"))?;
    let common_name = cert
        .subject()
        .iter_common_name()
        .next()
        .and_then(|a| a.as_str().ok())
        .map(str::to_owned);
    let organization = cert
        .subject()
        .iter_organization()
        .next()
        .and_then(|a| a.as_str().ok())
        .map(str::to_owned);
    let not_before = Utc
        .timestamp_opt(cert.validity().not_before.timestamp(), 0)
        .single()
        .unwrap_or_default();
    let not_after = Utc
        .timestamp_opt(cert.validity().not_after.timestamp(), 0)
        .single()
        .unwrap_or_default();
    let is_ca = cert
        .basic_constraints()
        .ok()
        .flatten()
        .map(|bc| bc.value.ca)
        .unwrap_or(false);
    Ok(CertInfo {
        common_name,
        organization,
        serial_hex: normalize_serial(&cert.raw_serial_as_string()),
        fingerprint_sha256: fingerprint_sha256(der),
        not_before,
        not_after,
        is_ca,
        spki_sha256: hex::encode(Sha256::digest(cert.tbs_certificate.subject_pki.raw)),
    })
}

/// sha256 hex of the SubjectPublicKeyInfo of a DER certificate.
pub fn spki_sha256(der: &[u8]) -> Result<String> {
    Ok(inspect_cert(der)?.spki_sha256)
}

/// Normalise a serial for CRL comparison: lowercase hex, strip `:`/`0x`/spaces and leading zeros.
pub fn normalize_serial(s: &str) -> String {
    let cleaned: String = s
        .trim()
        .trim_start_matches("0x")
        .chars()
        .filter(|c| c.is_ascii_hexdigit())
        .collect::<String>()
        .to_ascii_lowercase();
    let trimmed = cleaned.trim_start_matches('0');
    if trimmed.is_empty() {
        "0".into()
    } else {
        trimmed.to_string()
    }
}

/// Fingerprint of the first CA certificate in a bundle.
pub fn ca_fingerprint(bundle_pem: &str) -> Result<String> {
    let certs = parse_cert_bundle(bundle_pem)?;
    Ok(fingerprint_sha256(&certs[0]))
}

/// Compare two fingerprints ignoring case and `:` separators.
pub fn fingerprint_eq(a: &str, b: &str) -> bool {
    let norm = |s: &str| {
        s.chars()
            .filter(|c| c.is_ascii_hexdigit())
            .collect::<String>()
            .to_ascii_lowercase()
    };
    norm(a) == norm(b)
}

/// Check that `cert_pem` was signed with `key` (public key match).
pub fn cert_matches_key(cert_pem: &str, key: &KeyPair) -> Result<bool> {
    let certs = parse_cert_bundle(cert_pem)?;
    let (_, cert) =
        x509_parser::parse_x509_certificate(&certs[0]).map_err(|e| anyhow!("parse x509: {e}"))?;
    let spki = cert.public_key().raw;
    Ok(spki == key.subject_public_key_info().as_slice())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testpki::TestCa;

    #[test]
    fn csr_and_signing_round_trip() {
        let key = generate_key(KeyType::EcdsaP256).unwrap();
        let csr = build_csr(&key, "pending").unwrap();
        assert!(csr.contains("BEGIN CERTIFICATE REQUEST"));
        let ca = TestCa::new("GPU Deck Test CA");
        let node_id = uuid::Uuid::new_v4();
        let signed = ca
            .sign_csr(&csr, &node_id.to_string(), NODE_ORG, 30)
            .unwrap();
        let info = inspect_cert(&parse_cert_bundle(&signed.cert_pem).unwrap()[0]).unwrap();
        assert_eq!(
            info.common_name.as_deref(),
            Some(node_id.to_string().as_str())
        );
        assert_eq!(info.organization.as_deref(), Some(NODE_ORG));
        assert!(info.matches_subject_rule("O=gpudeck-nodes"));
        assert!(!info.matches_subject_rule("O=gpudeck-services"));
        assert!(info.matches_subject_rule(&format!("O=gpudeck-nodes, CN={node_id}")));
        assert!(cert_matches_key(&signed.cert_pem, &key).unwrap());
        let other = generate_key(KeyType::Ed25519).unwrap();
        assert!(!cert_matches_key(&signed.cert_pem, &other).unwrap());
        assert!(fingerprint_eq(
            &ca.fingerprint(),
            &ca.fingerprint().to_uppercase()
        ));
    }

    #[test]
    fn serial_normalisation() {
        assert_eq!(normalize_serial("00:AB:cd"), "abcd");
        assert_eq!(normalize_serial("0x0001"), "1");
        assert_eq!(normalize_serial("00"), "0");
    }

    #[test]
    fn rsa_is_rejected() {
        assert!(generate_key(KeyType::Rsa3072).is_err());
    }
}
