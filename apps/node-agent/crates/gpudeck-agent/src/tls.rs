//! rustls configuration: hub client (mTLS, TLS 1.3 only), join client (CA pinning / TOFU),
//! proxy server (client certificate required), certificate identity checks and the CRL.

use std::collections::HashSet;
use std::path::Path;
use std::sync::{Arc, OnceLock};

use anyhow::{anyhow, Context, Result};
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::CryptoProvider;
use rustls::pki_types::{CertificateDer, PrivateKeyDer, ServerName, UnixTime};
use rustls::server::WebPkiClientVerifier;
use rustls::{ClientConfig, DigitallySignedStruct, RootCertStore, ServerConfig, SignatureScheme};

use crate::pki::{inspect_cert, normalize_serial, CertInfo};
use crate::state::StatePaths;

/// Install the ring provider once per process (idempotent).
pub fn init_crypto() {
    if CryptoProvider::get_default().is_none() {
        let _ = rustls::crypto::ring::default_provider().install_default();
    }
}

/// Builder for the agent's plain HTTP(S) clients: model and engine downloads, external instance
/// probes and the loopback engines. Server certificates are checked against Mozilla's root store
/// compiled into the binary (webpki-root-certs), as with reqwest 0.12's `rustls-tls`, so a host
/// or container without a CA bundle still verifies TLS (reqwest 0.13 reads the OS store by
/// default). Also installs the ring provider that reqwest's `rustls-no-provider` build needs.
pub fn http_client_builder() -> reqwest::ClientBuilder {
    init_crypto();
    reqwest::Client::builder().tls_certs_only(public_roots().iter().cloned())
}

fn public_roots() -> &'static [reqwest::Certificate] {
    static ROOTS: OnceLock<Vec<reqwest::Certificate>> = OnceLock::new();
    ROOTS.get_or_init(|| {
        webpki_root_certs::TLS_SERVER_ROOT_CERTS
            .iter()
            .filter_map(|der| reqwest::Certificate::from_der(der).ok())
            .collect()
    })
}

fn provider() -> Arc<CryptoProvider> {
    init_crypto();
    CryptoProvider::get_default()
        .cloned()
        .expect("crypto provider installed")
}

/// The node's identity material loaded from `<state_dir>`.
#[derive(Clone)]
pub struct Identity {
    pub cert_chain: Vec<CertificateDer<'static>>,
    pub key: Arc<PrivateKeyDer<'static>>,
    pub ca_bundle: Vec<CertificateDer<'static>>,
    pub info: CertInfo,
}

impl std::fmt::Debug for Identity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Identity")
            .field("info", &self.info)
            .field("ca_certs", &self.ca_bundle.len())
            .finish()
    }
}

impl Identity {
    pub fn load(paths: &StatePaths) -> Result<Self> {
        crate::state::check_private_mode(&paths.key())?;
        let key_pem = std::fs::read_to_string(paths.key())
            .with_context(|| format!("read {}", paths.key().display()))?;
        let cert_pem = std::fs::read_to_string(paths.cert())
            .with_context(|| format!("read {}", paths.cert().display()))?;
        let ca_pem = std::fs::read_to_string(paths.ca())
            .with_context(|| format!("read {}", paths.ca().display()))?;
        Self::from_pems(&key_pem, &cert_pem, &ca_pem)
    }

    pub fn from_pems(key_pem: &str, cert_pem: &str, ca_pem: &str) -> Result<Self> {
        let key = parse_private_key(key_pem)?;
        let cert_chain = crate::pki::parse_cert_bundle(cert_pem)?;
        let ca_bundle = crate::pki::parse_cert_bundle(ca_pem)?;
        let info = inspect_cert(&cert_chain[0])?;
        Ok(Self {
            cert_chain,
            key: Arc::new(key),
            ca_bundle,
            info,
        })
    }

    pub fn fingerprint(&self) -> String {
        self.info.fingerprint_sha256.clone()
    }

    pub fn root_store(&self) -> Result<RootCertStore> {
        let mut roots = RootCertStore::empty();
        for c in &self.ca_bundle {
            roots.add(c.clone()).context("add CA to root store")?;
        }
        Ok(roots)
    }

    /// mTLS client config for the hub WebSocket (TLS 1.3 only).
    pub fn hub_client_config(&self) -> Result<ClientConfig> {
        let roots = self.root_store()?;
        let cfg = ClientConfig::builder_with_provider(provider())
            .with_protocol_versions(&[&rustls::version::TLS13])
            .context("tls13")?
            .with_root_certificates(roots)
            .with_client_auth_cert(self.cert_chain.clone(), self.key.clone_key())
            .context("client auth cert")?;
        Ok(cfg)
    }

    /// Server config for the :9443 proxy: TLS 1.3, client certificate chained to the internal CA required.
    pub fn proxy_server_config(&self) -> Result<ServerConfig> {
        let roots = Arc::new(self.root_store()?);
        let verifier = WebPkiClientVerifier::builder_with_provider(roots, provider())
            .build()
            .context("client verifier")?;
        let mut cfg = ServerConfig::builder_with_provider(provider())
            .with_protocol_versions(&[&rustls::version::TLS13])
            .context("tls13")?
            .with_client_cert_verifier(verifier)
            .with_single_cert(self.cert_chain.clone(), self.key.clone_key())
            .context("server cert")?;
        // HTTP/2 lets the API layer multiplex many requests over one connection (one TLS
        // handshake per node instead of one per concurrent request on a high-latency link).
        cfg.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
        Ok(cfg)
    }
}

pub fn parse_private_key(pem: &str) -> Result<PrivateKeyDer<'static>> {
    let mut reader = std::io::Cursor::new(pem.as_bytes());
    rustls_pemfile::private_key(&mut reader)
        .context("parse private key")?
        .ok_or_else(|| anyhow!("no private key in PEM"))
}

/// Client config for the join call: no client cert; either pinned to a CA fingerprint
/// (any chain containing a cert with that sha256) or trust-on-first-use (`pin = None`).
pub fn join_client_config(pin: Option<&str>) -> Result<ClientConfig> {
    let verifier = Arc::new(PinnedVerifier {
        pin: pin.map(str::to_owned),
        provider: provider(),
    });
    let cfg = ClientConfig::builder_with_provider(provider())
        .with_protocol_versions(&[&rustls::version::TLS13])
        .context("tls13")?
        .dangerous()
        .with_custom_certificate_verifier(verifier)
        .with_no_client_auth();
    Ok(cfg)
}

/// Client config trusting a specific CA bundle (used for the API-layer side in tests/tools).
pub fn client_config_with_ca(ca_pem: &str, client: Option<(&str, &str)>) -> Result<ClientConfig> {
    let mut roots = RootCertStore::empty();
    for c in crate::pki::parse_cert_bundle(ca_pem)? {
        roots.add(c)?;
    }
    let b = ClientConfig::builder_with_provider(provider())
        .with_protocol_versions(&[&rustls::version::TLS13])
        .context("tls13")?
        .with_root_certificates(roots);
    Ok(match client {
        Some((cert_pem, key_pem)) => b.with_client_auth_cert(
            crate::pki::parse_cert_bundle(cert_pem)?,
            parse_private_key(key_pem)?,
        )?,
        None => b.with_no_client_auth(),
    })
}

/// Client config for calling an agent proxy (what the API layer does): chain must verify
/// against the internal CA and the server certificate CN must be `node_id`. The host name is
/// not checked because node certificates carry `URI:gpudeck://node/<id>` (plus whatever SANs the
/// hub copies from the CSR) and `proxy_address` may be NATed.
pub fn node_proxy_client_config(
    ca_pem: &str,
    cert_pem: &str,
    key_pem: &str,
    node_id: &str,
) -> Result<ClientConfig> {
    let mut roots = RootCertStore::empty();
    for c in crate::pki::parse_cert_bundle(ca_pem)? {
        roots.add(c)?;
    }
    let inner =
        rustls::client::WebPkiServerVerifier::builder_with_provider(Arc::new(roots), provider())
            .build()
            .context("server verifier")?;
    let verifier = Arc::new(NodeIdVerifier {
        inner,
        node_id: node_id.to_string(),
    });
    let cfg = ClientConfig::builder_with_provider(provider())
        .with_protocol_versions(&[&rustls::version::TLS13])
        .context("tls13")?
        .dangerous()
        .with_custom_certificate_verifier(verifier)
        .with_client_auth_cert(
            crate::pki::parse_cert_bundle(cert_pem)?,
            parse_private_key(key_pem)?,
        )?;
    Ok(cfg)
}

#[derive(Debug)]
struct NodeIdVerifier {
    inner: Arc<rustls::client::WebPkiServerVerifier>,
    node_id: String,
}

impl ServerCertVerifier for NodeIdVerifier {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        intermediates: &[CertificateDer<'_>],
        server_name: &ServerName<'_>,
        ocsp: &[u8],
        now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        match self
            .inner
            .verify_server_cert(end_entity, intermediates, server_name, ocsp, now)
        {
            Ok(v) => Ok(v),
            Err(rustls::Error::InvalidCertificate(
                rustls::CertificateError::NotValidForName
                | rustls::CertificateError::NotValidForNameContext { .. },
            )) => Ok(ServerCertVerified::assertion()),
            Err(e) => Err(e),
        }
        .and_then(|v| {
            let info = inspect_cert(end_entity.as_ref())
                .map_err(|e| rustls::Error::General(e.to_string()))?;
            if info.common_name.as_deref() == Some(self.node_id.as_str()) {
                Ok(v)
            } else {
                Err(rustls::Error::General(format!(
                    "proxy certificate CN {:?} is not node {}",
                    info.common_name, self.node_id
                )))
            }
        })
    }
    fn verify_tls12_signature(
        &self,
        m: &[u8],
        c: &CertificateDer<'_>,
        d: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        self.inner.verify_tls12_signature(m, c, d)
    }
    fn verify_tls13_signature(
        &self,
        m: &[u8],
        c: &CertificateDer<'_>,
        d: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        self.inner.verify_tls13_signature(m, c, d)
    }
    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.inner.supported_verify_schemes()
    }
}

#[derive(Debug)]
struct PinnedVerifier {
    pin: Option<String>,
    provider: Arc<CryptoProvider>,
}

impl ServerCertVerifier for PinnedVerifier {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        if let Some(pin) = &self.pin {
            let found = std::iter::once(end_entity)
                .chain(intermediates.iter())
                .any(|c| crate::pki::fingerprint_eq(&crate::pki::fingerprint_sha256(c), pin));
            if !found {
                return Err(rustls::Error::General(format!(
                    "hub CA fingerprint does not match pin {pin}"
                )));
            }
        }
        Ok(ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(
            message,
            cert,
            dss,
            &self.provider.signature_verification_algorithms,
        )
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(
            message,
            cert,
            dss,
            &self.provider.signature_verification_algorithms,
        )
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.provider
            .signature_verification_algorithms
            .supported_schemes()
    }
}

/// Post-handshake identity policy for proxy clients: subject allow-list + CRL.
#[derive(Debug, Clone, Default)]
pub struct ClientPolicy {
    /// e.g. `["O=gpudeck-services"]`; every rule is `K=V[,K=V]`, any rule matching admits.
    pub allowed_subjects: Vec<String>,
    pub revoked_serials: HashSet<String>,
    pub revoked_fingerprints: HashSet<String>,
}

impl ClientPolicy {
    pub fn new(allowed_subjects: Vec<String>) -> Self {
        Self {
            allowed_subjects,
            ..Default::default()
        }
    }

    pub fn set_crl(&mut self, serials: &[String], fingerprints: &[String]) {
        self.revoked_serials = serials.iter().map(|s| normalize_serial(s)).collect();
        self.revoked_fingerprints = fingerprints
            .iter()
            .map(|f| f.to_ascii_lowercase())
            .collect();
    }

    /// Returns `Err(reason)` when the presented certificate must be refused.
    pub fn check(&self, der: &[u8]) -> Result<CertInfo, String> {
        let info = inspect_cert(der).map_err(|e| format!("unparseable client certificate: {e}"))?;
        if self.revoked_serials.contains(&info.serial_hex)
            || self.revoked_fingerprints.contains(&info.fingerprint_sha256)
        {
            return Err(format!(
                "client certificate revoked (serial {})",
                info.serial_hex
            ));
        }
        let rules: Vec<&str> = if self.allowed_subjects.is_empty() {
            vec!["O=gpudeck-services"]
        } else {
            self.allowed_subjects.iter().map(String::as_str).collect()
        };
        if !rules.iter().any(|r| info.matches_subject_rule(r)) {
            return Err(format!(
                "client subject not allowed (O={:?}, CN={:?})",
                info.organization, info.common_name
            ));
        }
        Ok(info)
    }
}

/// Write cert/key/CA into the state dir with 0600 permissions.
pub fn store_identity(
    paths: &StatePaths,
    key_pem: &str,
    cert_pem: &str,
    ca_pem: &str,
) -> Result<()> {
    crate::state::write_private(&paths.key(), key_pem.as_bytes(), 0o600)?;
    crate::state::write_private(&paths.cert(), cert_pem.as_bytes(), 0o600)?;
    crate::state::write_private(&paths.ca(), ca_pem.as_bytes(), 0o600)?;
    Ok(())
}

pub fn read_pem(path: &Path) -> Result<String> {
    std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testpki::TestCa;

    #[test]
    fn client_policy_subject_and_crl() {
        let ca = TestCa::new("ca");
        let api = ca.issue_client("api-layer", "gpudeck-services");
        let node = ca.issue_client("node-1", "gpudeck-nodes");
        let api_der = crate::pki::parse_cert_bundle(&api.cert_pem)
            .unwrap()
            .remove(0);
        let node_der = crate::pki::parse_cert_bundle(&node.cert_pem)
            .unwrap()
            .remove(0);
        let mut policy = ClientPolicy::default();
        assert!(policy.check(&api_der).is_ok());
        assert!(policy.check(&node_der).is_err());
        policy.set_crl(&[api.serial_hex.to_uppercase()], &[]);
        assert!(policy.check(&api_der).unwrap_err().contains("revoked"));
        policy.set_crl(&[], &[crate::pki::fingerprint_sha256(&api_der)]);
        assert!(policy.check(&api_der).is_err());
        let policy = ClientPolicy::new(vec!["CN=node-1".into()]);
        assert!(policy.check(&node_der).is_ok());
    }

    #[test]
    fn identity_builds_configs() {
        let ca = TestCa::new("ca");
        let key = crate::pki::generate_key(gpudeck_protocol::KeyType::EcdsaP256).unwrap();
        let csr = crate::pki::build_csr(&key, "pending").unwrap();
        let issued = ca.sign_csr(&csr, "node", crate::pki::NODE_ORG, 10).unwrap();
        let id = Identity::from_pems(&key.serialize_pem(), &issued.cert_pem, &ca.ca_pem()).unwrap();
        id.hub_client_config().unwrap();
        id.proxy_server_config().unwrap();
        join_client_config(Some(&ca.fingerprint())).unwrap();
        let dir = tempfile::tempdir().unwrap();
        let paths = StatePaths::new(dir.path());
        store_identity(&paths, &key.serialize_pem(), &issued.cert_pem, &ca.ca_pem()).unwrap();
        let loaded = Identity::load(&paths).unwrap();
        assert_eq!(loaded.fingerprint(), id.fingerprint());
    }
}
