//! Signed node security policy (`protocol.md` §13, `security_policy.json`).
//!
//! The hub signs one policy document per node with the internal CA key. It carries everything
//! that decides who the node talks to: the pinned key fingerprints (SubjectPublicKeyInfo
//! sha256) of the agent hub and of the API layer, the outbound tunnel endpoints, the
//! direct-mode source allow-list and handshake limits and the SSH-instance default CIDRs.
//!
//! Trust rules:
//! - the signature is checked with the CA certificate pinned at join (`<state_dir>/ca.pem`),
//!   never with a key that arrives next to the policy; the algorithm follows the CA key type;
//! - the policy must name this node (`node_id`), and its `serial` must not be lower than the
//!   one already held (no rollback to older pins or wider CIDRs);
//! - the signed form is persisted (`<state_dir>/security_policy.json`) and re-verified on
//!   every start, so a tampered file is ignored.

use std::net::IpAddr;
use std::path::Path;

use anyhow::{anyhow, bail, Context, Result};
use base64::Engine as _;
use gpudeck_protocol::{SecurityPolicy, SignedPolicy};
use ring::signature;
use uuid::Uuid;

/// Default API-layer subject when the policy names pins but no subject rule.
pub const API_LAYER_SUBJECT: &str = "CN=api-layer,O=gpudeck-services";
/// Default tunnel connections per endpoint.
pub const DEFAULT_TUNNEL_CONNECTIONS: u32 = 2;
/// Default tunnel idle timeout (no request, not even a keep-alive probe).
pub const DEFAULT_TUNNEL_IDLE_SECONDS: u64 = 60;
/// Default direct-mode limits.
pub const DEFAULT_HANDSHAKES_PER_MINUTE: u32 = 120;
pub const DEFAULT_HANDSHAKE_BURST: u32 = 30;
pub const DEFAULT_MAX_PENDING_HANDSHAKES: u32 = 64;
/// Rejected inbound attempts are reported every this many seconds…
pub const DEFAULT_REPORT_INTERVAL_SECONDS: u64 = 60;
/// …as `critical` when at least this many happened in one interval.
pub const DEFAULT_BURST_THRESHOLD: u64 = 20;

/// Private / loopback ranges a direct-mode proxy accepts when the policy lists no CIDRs.
pub const PRIVATE_RANGES: &[&str] = &[
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "fc00::/7",
    "::1/128",
];

/// `ecdsa-p256-sha256` etc. for the public key of a CA certificate (DER).
fn algorithm_for(
    ca_der: &[u8],
) -> Result<(
    &'static str,
    &'static dyn signature::VerificationAlgorithm,
    Vec<u8>,
)> {
    let (_, cert) =
        x509_parser::parse_x509_certificate(ca_der).map_err(|e| anyhow!("parse CA: {e}"))?;
    let spki = &cert.tbs_certificate.subject_pki;
    let key = spki.subject_public_key.data.to_vec();
    let oid = spki.algorithm.algorithm.to_id_string();
    match oid.as_str() {
        // id-ecPublicKey: the curve is the algorithm parameter.
        "1.2.840.10045.2.1" => {
            let curve = spki
                .algorithm
                .parameters
                .as_ref()
                .and_then(|p| p.as_oid().ok())
                .map(|o| o.to_id_string())
                .unwrap_or_default();
            match curve.as_str() {
                "1.2.840.10045.3.1.7" => {
                    Ok(("ecdsa-p256-sha256", &signature::ECDSA_P256_SHA256_ASN1, key))
                }
                "1.3.132.0.34" => {
                    Ok(("ecdsa-p384-sha384", &signature::ECDSA_P384_SHA384_ASN1, key))
                }
                other => bail!("unsupported CA curve {other}"),
            }
        }
        "1.3.101.112" => Ok(("ed25519", &signature::ED25519, key)),
        "1.2.840.113549.1.1.1" => Ok((
            "rsa-pkcs1-sha256",
            &signature::RSA_PKCS1_2048_8192_SHA256,
            key,
        )),
        other => bail!("unsupported CA key algorithm {other}"),
    }
}

/// Verify `signed` against every CA certificate in `ca_pem` and return the policy document.
/// Checks: signature (algorithm chosen by the CA key), `v == 1`, `node_id == node_id`.
pub fn verify(signed: &SignedPolicy, ca_pem: &str, node_id: Uuid) -> Result<SecurityPolicy> {
    let b64 = base64::engine::general_purpose::STANDARD;
    let doc = b64
        .decode(signed.policy.trim())
        .context("policy is not base64")?;
    let sig = b64
        .decode(signed.signature.trim())
        .context("signature is not base64")?;
    let mut verified = false;
    let mut last_err = anyhow!("no CA certificate to verify the policy");
    for ca in crate::pki::parse_cert_bundle(ca_pem)? {
        match algorithm_for(&ca) {
            Ok((name, alg, key)) => {
                if name != signed.algorithm {
                    last_err = anyhow!(
                        "policy algorithm {:?} does not match the CA key ({name})",
                        signed.algorithm
                    );
                    continue;
                }
                if signature::UnparsedPublicKey::new(alg, &key)
                    .verify(&doc, &sig)
                    .is_ok()
                {
                    verified = true;
                    break;
                }
                last_err = anyhow!("policy signature does not verify with the pinned CA");
            }
            Err(e) => last_err = e,
        }
    }
    if !verified {
        return Err(last_err);
    }
    let policy: SecurityPolicy =
        serde_json::from_slice(&doc).context("policy is not a security_policy document")?;
    if policy.v != 1 {
        bail!("unsupported policy version {}", policy.v);
    }
    if policy.node_id != node_id {
        bail!(
            "policy is for node {}, not this node ({node_id})",
            policy.node_id
        );
    }
    Ok(policy)
}

/// Load and re-verify the persisted policy. A missing file is `Ok(None)`; a file that does not
/// verify (tampered, other CA) is an error the caller logs and ignores.
pub fn load(path: &Path, ca_pem: &str, node_id: Uuid) -> Result<Option<SecurityPolicy>> {
    let Some(signed) = crate::state::read_json::<SignedPolicy>(path)? else {
        return Ok(None);
    };
    verify(&signed, ca_pem, node_id).map(Some)
}

/// Accept `next` over `current`: same node, serial not lower (rollback).
pub fn accept_newer(current: Option<&SecurityPolicy>, next: &SecurityPolicy) -> Result<()> {
    if let Some(c) = current {
        if next.serial < c.serial {
            bail!(
                "policy serial {} is older than the held {} (rollback refused)",
                next.serial,
                c.serial
            );
        }
    }
    Ok(())
}

/// Parse CIDRs / bare addresses, skipping (and returning) the invalid ones.
pub fn parse_nets(v: &[String]) -> (Vec<ipnet::IpNet>, Vec<String>) {
    let mut ok = vec![];
    let mut bad = vec![];
    for s in v {
        let s = s.trim();
        if s.is_empty() {
            continue;
        }
        match s
            .parse::<ipnet::IpNet>()
            .or_else(|_| s.parse::<IpAddr>().map(ipnet::IpNet::from))
        {
            Ok(n) => ok.push(n.trunc()),
            Err(_) => bad.push(s.to_string()),
        }
    }
    (ok, bad)
}

/// `ip` in one of `nets`, with IPv4-mapped IPv6 addresses compared as IPv4.
pub fn ip_in(nets: &[ipnet::IpNet], ip: IpAddr) -> bool {
    let ip = match ip {
        IpAddr::V6(v6) => v6.to_ipv4_mapped().map(IpAddr::V4).unwrap_or(ip),
        v4 => v4,
    };
    nets.iter().any(|n| n.contains(&ip))
}

/// Effective direct-mode source allow-list: the policy's CIDRs, else private + loopback ranges.
pub fn direct_sources(policy: Option<&SecurityPolicy>) -> Vec<ipnet::IpNet> {
    let listed = policy
        .and_then(|p| p.direct.as_ref())
        .map(|d| d.allowed_source_cidrs.clone())
        .unwrap_or_default();
    let (nets, bad) = parse_nets(&listed);
    if !bad.is_empty() {
        tracing::warn!(?bad, "direct.allowed_source_cidrs: invalid entries ignored");
    }
    if nets.is_empty() {
        let defaults: Vec<String> = PRIVATE_RANGES.iter().map(|s| s.to_string()).collect();
        return parse_nets(&defaults).0;
    }
    nets
}

/// Lowercase hex pins, invalid entries dropped.
pub fn normalize_pins(v: &[String]) -> Vec<String> {
    v.iter()
        .map(|p| p.trim().to_ascii_lowercase())
        .filter(|p| p.len() == 64 && p.chars().all(|c| c.is_ascii_hexdigit()))
        .collect()
}

/// API-layer pins and subject rules of a policy (`(pins, subjects)`).
pub fn api_layer_identity(policy: Option<&SecurityPolicy>) -> (Vec<String>, Vec<String>) {
    let Some(p) = policy.and_then(|p| p.api_layer.as_ref()) else {
        return (vec![], vec![API_LAYER_SUBJECT.to_string()]);
    };
    let subjects = if p.subjects.is_empty() {
        vec![API_LAYER_SUBJECT.to_string()]
    } else {
        p.subjects.clone()
    };
    (normalize_pins(&p.spki_sha256), subjects)
}

/// Hub key pins of a policy.
pub fn hub_pins(policy: Option<&SecurityPolicy>) -> Vec<String> {
    policy
        .and_then(|p| p.hub.as_ref())
        .map(|h| normalize_pins(&h.spki_sha256))
        .unwrap_or_default()
}

/// Validated tunnel endpoints (`host:port`, `[v6]:port`) of a policy.
pub fn tunnel_endpoints(policy: Option<&SecurityPolicy>) -> Vec<String> {
    policy
        .and_then(|p| p.tunnel.as_ref())
        .map(|t| {
            t.endpoints
                .iter()
                .map(|e| e.trim().to_string())
                .filter(|e| split_endpoint(e).is_some())
                .collect()
        })
        .unwrap_or_default()
}

/// `host:port` → (`host`, port); `[v6]:port` → (`v6`, port). Host names: letters, digits, `-`,
/// `.` only (no userinfo, path or scheme can ride along).
pub fn split_endpoint(e: &str) -> Option<(String, u16)> {
    let (host, port) = if let Some(rest) = e.strip_prefix('[') {
        let (h, p) = rest.split_once("]:")?;
        h.parse::<std::net::Ipv6Addr>().ok()?;
        (h.to_string(), p)
    } else {
        let (h, p) = e.rsplit_once(':')?;
        if h.is_empty()
            || h.len() > 253
            || !h
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '.')
        {
            return None;
        }
        (h.to_string(), p)
    };
    let port: u16 = port.parse().ok().filter(|p| *p > 0)?;
    Some((host, port))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testpki::TestCa;
    use gpudeck_protocol::{DirectPolicy, IdentityPins, TunnelPolicy};

    fn policy(node: Uuid, serial: u64) -> SecurityPolicy {
        SecurityPolicy {
            v: 1,
            node_id: node,
            serial,
            issued_at: gpudeck_protocol::now(),
            hub: Some(IdentityPins {
                spki_sha256: vec!["AB".repeat(32), "bad".into()],
                subjects: vec![],
            }),
            tunnel: Some(TunnelPolicy {
                endpoints: vec![
                    "cp.example.com:8444".into(),
                    "[2001:db8::1]:8444".into(),
                    "https://evil/x:1".into(),
                ],
                connections: Some(2),
                idle_timeout_seconds: None,
            }),
            direct: Some(DirectPolicy {
                allowed_source_cidrs: vec!["203.0.113.7".into(), "nope".into()],
                ..Default::default()
            }),
            ..Default::default()
        }
    }

    #[test]
    fn signed_policy_round_trip_and_tamper_checks() {
        let ca = TestCa::new("ca");
        let node = Uuid::new_v4();
        let signed = ca.sign_policy(&policy(node, 5));
        let p = verify(&signed, &ca.ca_pem(), node).unwrap();
        assert_eq!(p.serial, 5);
        assert_eq!(hub_pins(Some(&p)), vec!["ab".repeat(32)]);
        assert_eq!(
            tunnel_endpoints(Some(&p)),
            vec!["cp.example.com:8444", "[2001:db8::1]:8444"]
        );
        // Another node's policy is refused (no cross-node replay).
        assert!(verify(&signed, &ca.ca_pem(), Uuid::new_v4()).is_err());
        // Another CA (e.g. an attacker's) cannot sign for us.
        let other = TestCa::new("evil");
        let forged = other.sign_policy(&policy(node, 99));
        assert!(verify(&forged, &ca.ca_pem(), node).is_err());
        // Edited document: the signature no longer matches.
        let mut edited = signed.clone();
        let b64 = base64::engine::general_purpose::STANDARD;
        let mut doc: serde_json::Value =
            serde_json::from_slice(&b64.decode(&signed.policy).unwrap()).unwrap();
        doc["direct"]["allowed_source_cidrs"] = serde_json::json!(["0.0.0.0/0"]);
        edited.policy = b64.encode(serde_json::to_vec(&doc).unwrap());
        assert!(verify(&edited, &ca.ca_pem(), node).is_err());
        // Algorithm confusion: the CA is P-256, a claimed ed25519 signature is refused.
        let mut wrong_alg = signed.clone();
        wrong_alg.algorithm = "ed25519".into();
        assert!(verify(&wrong_alg, &ca.ca_pem(), node).is_err());
        // Rollback.
        assert!(accept_newer(Some(&policy(node, 6)), &p).is_err());
        assert!(accept_newer(Some(&policy(node, 5)), &p).is_ok());
        assert!(accept_newer(None, &p).is_ok());
        // Persisted form is re-verified on load.
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("security_policy.json");
        crate::state::write_json(&f, &signed, 0o600).unwrap();
        assert_eq!(load(&f, &ca.ca_pem(), node).unwrap().unwrap().serial, 5);
        crate::state::write_json(&f, &edited, 0o600).unwrap();
        assert!(load(&f, &ca.ca_pem(), node).is_err());
        assert!(load(&dir.path().join("missing"), &ca.ca_pem(), node)
            .unwrap()
            .is_none());
    }

    #[test]
    fn sources_and_endpoints() {
        let node = Uuid::new_v4();
        let p = policy(node, 1);
        let nets = direct_sources(Some(&p));
        assert!(ip_in(&nets, "203.0.113.7".parse().unwrap()));
        assert!(!ip_in(&nets, "10.0.0.1".parse().unwrap()));
        // IPv4-mapped IPv6 peers are matched as IPv4.
        assert!(ip_in(&nets, "::ffff:203.0.113.7".parse().unwrap()));
        let defaults = direct_sources(None);
        assert!(ip_in(&defaults, "10.1.2.3".parse().unwrap()));
        assert!(ip_in(&defaults, "127.0.0.1".parse().unwrap()));
        assert!(!ip_in(&defaults, "8.8.8.8".parse().unwrap()));
        assert_eq!(
            split_endpoint("hub.example:8444"),
            Some(("hub.example".into(), 8444))
        );
        assert_eq!(split_endpoint("user@hub:8444"), None);
        assert_eq!(split_endpoint("hub:0"), None);
        assert_eq!(split_endpoint("hub"), None);
        let (pins, subjects) = api_layer_identity(None);
        assert!(pins.is_empty());
        assert_eq!(subjects, vec![API_LAYER_SUBJECT]);
    }
}
