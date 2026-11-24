//! Join flow (`protocol.md` §2): key pair + CSR -> `POST /agent/v1/join` -> cert + CA chain
//! stored 0600 in `<state_dir>`, `node_id`/`hub_url` written to `agent.toml`.

use std::path::Path;

use anyhow::{anyhow, bail, Context, Result};
use gpudeck_protocol::{
    AdminEnvelope, JoinRequest, JoinResponse, KeyType, HUB_JOIN_PATH, PROTOCOL_VERSION,
};
use tracing::{info, warn};

use crate::config::AgentConfig;
use crate::state::StatePaths;

pub const AGENT_VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct JoinOptions {
    pub hub_url: String,
    pub token: String,
    pub name: Option<String>,
    pub ca_fingerprint: Option<String>,
    pub proxy_port: u16,
    pub labels: std::collections::BTreeMap<String, String>,
}

/// Run the join flow and persist the identity. Returns the hub response.
pub async fn join(
    cfg: &mut AgentConfig,
    config_path: &Path,
    opts: JoinOptions,
) -> Result<JoinResponse> {
    let paths = StatePaths::new(&cfg.state_dir);
    paths.ensure()?;
    let hub_url = opts.hub_url.trim_end_matches('/').to_string();
    if !hub_url.starts_with("https://") {
        bail!("hub url must be https:// (got {hub_url})");
    }
    if !opts.token.starts_with("gdj_") {
        warn!("join token does not start with gdj_; continuing");
    }

    let key = crate::pki::generate_key(KeyType::EcdsaP256)?;
    let host = crate::system::host_info();
    let csr_pem = crate::pki::build_csr_with_sans(
        &key,
        "pending",
        &proxy_sans(
            &host.hostname,
            &host.ip_addresses,
            cfg.proxy_address.as_deref(),
        ),
    )?;
    let name = opts
        .name
        .clone()
        .or_else(|| cfg.name.clone())
        .unwrap_or_else(|| host.hostname.clone());
    let body = JoinRequest {
        join_token: opts.token.clone(),
        csr_pem,
        hostname: host.hostname.clone(),
        name,
        os: host.os,
        arch: host.arch,
        agent_version: AGENT_VERSION.into(),
        protocol_version: PROTOCOL_VERSION,
        proxy_port: opts.proxy_port,
        labels: opts.labels.clone(),
    };

    let pin = opts
        .ca_fingerprint
        .clone()
        .or_else(|| cfg.ca_fingerprint.clone());
    if pin.is_none() {
        warn!("no --ca-fingerprint given: trusting the hub CA on first use");
    }
    let tls = match &pin {
        // Pinned: fetch the CA from `/agent/v1/ca`, check its fingerprint, then verify the hub's
        // server certificate (chain + host name) against it.
        Some(pin) => {
            let ca_pem = fetch_ca(&hub_url).await?;
            let fp = crate::pki::ca_fingerprint(&ca_pem)?;
            if !crate::pki::fingerprint_eq(pin, &fp) {
                bail!("hub CA fingerprint {fp} does not match --ca-fingerprint {pin}");
            }
            crate::tls::client_config_with_ca(&ca_pem, None)?
        }
        None => crate::tls::join_client_config(None)?,
    };
    let client = reqwest::Client::builder()
        .use_preconfigured_tls(tls)
        .timeout(std::time::Duration::from_secs(30))
        .build()?;
    let url = format!("{hub_url}{HUB_JOIN_PATH}");
    info!(%url, "joining hub");
    let resp = client
        .post(&url)
        .json(&body)
        .send()
        .await
        .with_context(|| format!("POST {url}"))?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        let code = serde_json::from_str::<AdminEnvelope<serde_json::Value>>(&text)
            .ok()
            .and_then(|e| e.error)
            .map(|e| format!("{} ({})", e.code, e.message))
            .unwrap_or_else(|| text.chars().take(300).collect());
        bail!("join rejected: HTTP {status}: {code}");
    }
    let joined: JoinResponse = match serde_json::from_str::<JoinResponse>(&text) {
        Ok(j) => j,
        Err(_) => serde_json::from_str::<AdminEnvelope<serde_json::Value>>(&text)
            .ok()
            .and_then(|e| e.data)
            .and_then(|d| serde_json::from_value::<JoinResponse>(d).ok())
            .ok_or_else(|| {
                anyhow!(
                    "unexpected join response: {}",
                    text.chars().take(300).collect::<String>()
                )
            })?,
    };

    // Validate what we got before trusting it.
    let ca_fp = crate::pki::ca_fingerprint(&joined.ca_bundle_pem)?;
    if let Some(pin) = &pin {
        if !crate::pki::fingerprint_eq(pin, &ca_fp) {
            bail!("CA bundle fingerprint {ca_fp} does not match pin {pin}");
        }
    }
    if !crate::pki::cert_matches_key(&joined.certificate_pem, &key)? {
        bail!("issued certificate does not match the generated key");
    }
    let info =
        crate::pki::inspect_cert(&crate::pki::parse_cert_bundle(&joined.certificate_pem)?[0])?;
    if info.common_name.as_deref() != Some(joined.node_id.to_string().as_str()) {
        bail!(
            "issued certificate CN {:?} != node_id {}",
            info.common_name,
            joined.node_id
        );
    }

    crate::tls::store_identity(
        &paths,
        &key.serialize_pem(),
        &joined.certificate_pem,
        &joined.ca_bundle_pem,
    )?;
    cfg.hub_url = Some(hub_url);
    cfg.node_id = Some(joined.node_id);
    cfg.ca_fingerprint = Some(ca_fp);
    cfg.proxy_port = opts.proxy_port;
    for (k, v) in &opts.labels {
        cfg.labels.insert(k.clone(), v.clone());
    }
    if cfg.name.is_none() {
        cfg.name = Some(joined.node_name.clone());
    }
    cfg.save(config_path)?;
    info!(node_id = %joined.node_id, node_name = %joined.node_name, not_after = %joined.not_after, "joined; identity stored in {}", paths.root.display());
    Ok(joined)
}

/// SANs requested for the node certificate: host name, proxy address, every non-loopback IP.
pub fn proxy_sans(hostname: &str, ips: &[String], proxy_address: Option<&str>) -> Vec<String> {
    let mut v: Vec<String> = vec![];
    if !hostname.is_empty() && hostname != "unknown" {
        v.push(hostname.to_string());
    }
    if let Some(a) = proxy_address {
        v.push(a.to_string());
    }
    v.extend(ips.iter().filter(|ip| !ip.contains('%')).cloned());
    v.dedup();
    v
}

/// `GET <hub>/agent/v1/ca` without trusting anything yet (the caller pins the result).
pub async fn fetch_ca(hub_url: &str) -> Result<String> {
    let tls = crate::tls::join_client_config(None)?;
    let client = reqwest::Client::builder()
        .use_preconfigured_tls(tls)
        .timeout(std::time::Duration::from_secs(15))
        .build()?;
    let url = format!("{hub_url}/agent/v1/ca");
    let v: serde_json::Value = client
        .get(&url)
        .send()
        .await
        .with_context(|| format!("GET {url}"))?
        .error_for_status()?
        .json()
        .await?;
    let data = v.get("data").unwrap_or(&v);
    data.get("certificate_pem")
        .and_then(|p| p.as_str())
        .map(str::to_owned)
        .ok_or_else(|| anyhow!("{url}: no certificate_pem in response"))
}
