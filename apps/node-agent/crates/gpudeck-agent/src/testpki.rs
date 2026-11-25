//! A tiny in-memory CA used by the fake hub and the test suite (never by the production agent).

use anyhow::{anyhow, Context, Result};
use rcgen::{
    BasicConstraints, Certificate, CertificateParams, CertificateSigningRequestParams,
    DistinguishedName, DnType, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair, KeyUsagePurpose,
    SanType,
};

pub struct TestCa {
    pub cert: Certificate,
    issuer: Issuer<'static, KeyPair>,
    key_pem: String,
}

pub struct Issued {
    pub cert_pem: String,
    pub key_pem: Option<String>,
    pub serial_hex: String,
    pub not_after: chrono::DateTime<chrono::Utc>,
}

impl TestCa {
    pub fn new(cn: &str) -> Self {
        let key = KeyPair::generate_for(&rcgen::PKCS_ECDSA_P256_SHA256).expect("ca key");
        let mut params = CertificateParams::new(Vec::<String>::new()).expect("params");
        let mut dn = DistinguishedName::new();
        dn.push(DnType::CommonName, cn);
        dn.push(DnType::OrganizationName, "GPU Deck");
        params.distinguished_name = dn;
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        params.key_usages = vec![
            KeyUsagePurpose::KeyCertSign,
            KeyUsagePurpose::CrlSign,
            KeyUsagePurpose::DigitalSignature,
        ];
        let cert = params.self_signed(&key).expect("self sign");
        let key_pem = key.serialize_pem();
        let issuer = Issuer::new(params, key);
        Self {
            cert,
            issuer,
            key_pem,
        }
    }

    /// Sign a node security policy the way the hub does (CA key, ECDSA P-256 / SHA-256).
    pub fn sign_policy(
        &self,
        policy: &gpudeck_protocol::SecurityPolicy,
    ) -> gpudeck_protocol::SignedPolicy {
        use base64::Engine;
        use rcgen::SigningKey;
        let bytes = serde_json::to_vec(policy).expect("policy json");
        let key = KeyPair::from_pem(&self.key_pem).expect("ca key");
        let sig = key.sign(&bytes).expect("sign policy");
        let b64 = base64::engine::general_purpose::STANDARD;
        gpudeck_protocol::SignedPolicy {
            policy: b64.encode(&bytes),
            signature: b64.encode(sig),
            algorithm: "ecdsa-p256-sha256".into(),
        }
    }

    pub fn ca_pem(&self) -> String {
        self.cert.pem()
    }

    pub fn fingerprint(&self) -> String {
        crate::pki::fingerprint_sha256(self.cert.der())
    }

    fn base_params(cn: &str, org: &str, days: i64) -> CertificateParams {
        let mut params = CertificateParams::new(Vec::<String>::new()).expect("params");
        let mut dn = DistinguishedName::new();
        dn.push(DnType::CommonName, cn);
        dn.push(DnType::OrganizationName, org);
        params.distinguished_name = dn;
        params.not_before = rcgen::date_time_ymd(2026, 1, 1);
        let end = chrono::Utc::now() + chrono::Duration::days(days);
        params.not_after = rcgen::date_time_ymd(
            end.format("%Y").to_string().parse().unwrap(),
            end.format("%m").to_string().parse().unwrap(),
            end.format("%d").to_string().parse().unwrap(),
        );
        params.key_usages = vec![
            KeyUsagePurpose::DigitalSignature,
            KeyUsagePurpose::KeyEncipherment,
        ];
        params.extended_key_usages = vec![
            ExtendedKeyUsagePurpose::ServerAuth,
            ExtendedKeyUsagePurpose::ClientAuth,
        ];
        params.serial_number = Some(rcgen::SerialNumber::from(
            rand::random::<u64>().to_be_bytes().to_vec(),
        ));
        params
    }

    /// Issue a server certificate (hub) with DNS/IP SANs.
    pub fn issue_server(&self, cn: &str, sans: &[&str]) -> Issued {
        let mut params = Self::base_params(cn, "GPU Deck", 365);
        for san in sans {
            if let Ok(ip) = san.parse::<std::net::IpAddr>() {
                params.subject_alt_names.push(SanType::IpAddress(ip));
            } else {
                params.subject_alt_names.push(SanType::DnsName(
                    san.to_string().try_into().expect("dns san"),
                ));
            }
        }
        let key = KeyPair::generate_for(&rcgen::PKCS_ECDSA_P256_SHA256).expect("key");
        let cert = params.signed_by(&key, &self.issuer).expect("sign");
        let info = crate::pki::inspect_cert(cert.der()).expect("inspect");
        Issued {
            cert_pem: cert.pem(),
            key_pem: Some(key.serialize_pem()),
            serial_hex: info.serial_hex,
            not_after: info.not_after,
        }
    }

    /// Issue a client certificate (`O=gpudeck-services`, e.g. `CN=api-layer`).
    pub fn issue_client(&self, cn: &str, org: &str) -> Issued {
        let params = Self::base_params(cn, org, 365);
        let key = KeyPair::generate_for(&rcgen::PKCS_ECDSA_P256_SHA256).expect("key");
        let cert = params.signed_by(&key, &self.issuer).expect("sign");
        let info = crate::pki::inspect_cert(cert.der()).expect("inspect");
        Issued {
            cert_pem: cert.pem(),
            key_pem: Some(key.serialize_pem()),
            serial_hex: info.serial_hex,
            not_after: info.not_after,
        }
    }

    /// Sign an agent CSR the way the hub does: subject replaced by `CN=<node_id>, O=gpudeck-nodes`,
    /// SAN `URI:gpudeck://node/<node_id>`.
    pub fn sign_csr(&self, csr_pem: &str, cn: &str, org: &str, days: i64) -> Result<Issued> {
        let mut csr = CertificateSigningRequestParams::from_pem(csr_pem)
            .map_err(|e| anyhow!("csr parse: {e}"))?;
        let template = Self::base_params(cn, org, days);
        csr.params.distinguished_name = template.distinguished_name;
        csr.params.not_before = template.not_before;
        csr.params.not_after = template.not_after;
        csr.params.key_usages = template.key_usages;
        csr.params.extended_key_usages = template.extended_key_usages;
        csr.params.serial_number = template.serial_number;
        // Keep the requested DNS/IP SANs (proxy address) and add the node URI.
        csr.params.subject_alt_names.push(SanType::URI(
            format!("gpudeck://node/{cn}")
                .try_into()
                .context("uri san")?,
        ));
        let cert = csr
            .signed_by(&self.issuer)
            .map_err(|e| anyhow!("sign csr: {e}"))?;
        let info = crate::pki::inspect_cert(cert.der())?;
        Ok(Issued {
            cert_pem: cert.pem(),
            key_pem: None,
            serial_hex: info.serial_hex,
            not_after: info.not_after,
        })
    }
}
