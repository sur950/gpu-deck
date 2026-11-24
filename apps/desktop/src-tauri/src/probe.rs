//! Server validation: calls the public, unauthenticated admin endpoints
//! `GET /api/v1/branding` and `GET /api/v1/health` of a server before it is
//! saved or connected. Runs in Rust (not the connect page) so the admin API's
//! CORS policy does not have to allow the desktop app's local origin.
//!
//! No cookies, no credentials, redirects not followed, bodies capped.

use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::Value;

use crate::servers::normalize_server_url;
use crate::status::clean;

const TIMEOUT: Duration = Duration::from_secs(6);
const MAX_BODY: usize = 256 * 1024;

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Branding {
    pub app_name: String,
    pub app_byline: String,
    pub brand_color: String,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Check {
    pub name: String,
    pub status: String,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct HealthInfo {
    pub status: String,
    pub version: Option<String>,
    pub checks: Vec<Check>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ProbeResult {
    pub origin: String,
    pub api_base: String,
    pub insecure: bool,
    /// True when the branding endpoint answered with a GPU Deck envelope.
    pub is_gpu_deck: bool,
    pub branding: Option<Branding>,
    pub health: Option<HealthInfo>,
    pub error: Option<String>,
    pub latency_ms: u64,
}

fn envelope_data(body: &Value) -> Option<&Value> {
    body.get("success")?;
    body.get("data").filter(|d| d.is_object())
}

pub fn parse_branding(body: &Value) -> Option<Branding> {
    let data = envelope_data(body)?;
    let s = |k: &str| data.get(k).and_then(Value::as_str).map(|v| clean(v, 80));
    Some(Branding {
        app_name: s("app_name").filter(|v| !v.is_empty())?,
        app_byline: s("app_byline").unwrap_or_default(),
        brand_color: s("brand_color")
            .filter(|c| {
                c.len() == 7 && c.starts_with('#') && c[1..].chars().all(|h| h.is_ascii_hexdigit())
            })
            .unwrap_or_else(|| "#B3122E".into()),
    })
}

/// Health from a 200 envelope, or "unhealthy" from a 503 error envelope.
pub fn parse_health(status: u16, body: &Value) -> Option<HealthInfo> {
    if let Some(data) = envelope_data(body) {
        let state = data
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let mut checks: Vec<Check> = data
            .get("checks")
            .and_then(Value::as_object)
            .map(|m| {
                m.iter()
                    .map(|(k, v)| Check {
                        name: clean(k, 40),
                        status: clean(
                            v.get("status")
                                .and_then(Value::as_str)
                                .or_else(|| v.as_str())
                                .unwrap_or("unknown"),
                            20,
                        ),
                    })
                    .take(20)
                    .collect()
            })
            .unwrap_or_default();
        checks.sort_by(|a, b| a.name.cmp(&b.name));
        return Some(HealthInfo {
            status: clean(state, 20),
            version: data
                .get("version")
                .and_then(Value::as_str)
                .map(|v| clean(v, 40)),
            checks,
        });
    }
    (status == 503 && body.get("success").is_some()).then(|| HealthInfo {
        status: "unhealthy".into(),
        version: None,
        checks: Vec::new(),
    })
}

async fn get_json(client: &reqwest::Client, url: &str) -> Result<(u16, Value), String> {
    let mut resp = client.get(url).send().await.map_err(describe)?;
    let status = resp.status().as_u16();
    let mut buf = Vec::new();
    while let Some(chunk) = resp.chunk().await.map_err(describe)? {
        buf.extend_from_slice(&chunk);
        if buf.len() > MAX_BODY {
            return Err("response too large".into());
        }
    }
    let value = serde_json::from_slice(&buf)
        .map_err(|_| format!("HTTP {status}, not a GPU Deck JSON response"))?;
    Ok((status, value))
}

fn describe(e: reqwest::Error) -> String {
    if e.is_timeout() {
        "timed out".into()
    } else if e.is_connect() {
        "connection refused or host unreachable".into()
    } else if e.is_redirect() {
        "redirected".into()
    } else {
        // reqwest errors carry the URL, never credentials (none are sent).
        let mut msg = e.to_string();
        let mut src = std::error::Error::source(&e);
        while let Some(s) = src {
            msg = format!("{msg}: {s}");
            src = s.source();
        }
        clean(&msg, 200)
    }
}

pub async fn probe(url: &str, api_url: Option<&str>) -> Result<ProbeResult, String> {
    let ui = normalize_server_url(url).map_err(|e| e.to_string())?;
    let api = match api_url.map(str::trim).filter(|a| !a.is_empty()) {
        Some(a) => {
            normalize_server_url(a)
                .map_err(|e| format!("Admin API URL: {e}"))?
                .origin
        }
        None => ui.origin.clone(),
    };
    let client = reqwest::Client::builder()
        .timeout(TIMEOUT)
        .connect_timeout(Duration::from_secs(4))
        .redirect(reqwest::redirect::Policy::none())
        .user_agent(concat!("GPUDeck-Desktop/", env!("CARGO_PKG_VERSION")))
        .build()
        .map_err(|e| e.to_string())?;
    let started = Instant::now();
    let (branding_url, health_url) = (
        format!("{api}/api/v1/branding"),
        format!("{api}/api/v1/health"),
    );
    let (branding, health) = tokio::join!(
        get_json(&client, &branding_url),
        get_json(&client, &health_url)
    );
    let latency_ms = started.elapsed().as_millis() as u64;
    let mut error = None;
    let branding = match branding {
        Ok((200, body)) => parse_branding(&body),
        Ok((code, _)) => {
            error = Some(format!("{api}/api/v1/branding answered HTTP {code}"));
            None
        }
        Err(e) => {
            error = Some(format!("{api}: {e}"));
            None
        }
    };
    let health = match health {
        Ok((code, body)) => parse_health(code, &body),
        Err(e) => {
            error.get_or_insert(format!("{api}: {e}"));
            None
        }
    };
    let is_gpu_deck = branding.is_some();
    if !is_gpu_deck && error.is_none() {
        error = Some(format!("{api} does not look like a GPU Deck admin API"));
    }
    Ok(ProbeResult {
        origin: ui.origin,
        api_base: api,
        insecure: ui.insecure,
        is_gpu_deck,
        branding,
        health,
        error,
        latency_ms,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_branding_envelope() {
        let body = json!({"success": true, "data": {"app_name": "GPU Deck", "app_byline": "GPU control plane",
            "brand_color": "#B3122E", "logo_url": "", "favicon_url": "/x.png"}, "error": null, "meta": null});
        assert_eq!(
            parse_branding(&body),
            Some(Branding {
                app_name: "GPU Deck".into(),
                app_byline: "GPU control plane".into(),
                brand_color: "#B3122E".into()
            })
        );
        let odd = json!({"success": true, "data": {"app_name": "X\nY", "brand_color": "red; background:url(x)"}});
        let b = parse_branding(&odd).unwrap();
        assert_eq!(b.app_name, "X Y");
        assert_eq!(b.brand_color, "#B3122E");
        assert_eq!(parse_branding(&json!({"name": "not gpu deck"})), None);
        assert_eq!(
            parse_branding(&json!({"success": true, "data": {"app_name": ""}})),
            None
        );
    }

    #[test]
    fn parses_health_ok_and_503() {
        let body = json!({"success": true, "data": {"status": "degraded", "version": "1.2.0",
            "timestamp": "2026-11-18T00:00:00Z",
            "checks": {"redis": {"status": "healthy"}, "database": {"status": "healthy"}, "celery": {"status": "unhealthy"}}}});
        let h = parse_health(200, &body).unwrap();
        assert_eq!(h.status, "degraded");
        assert_eq!(h.version.as_deref(), Some("1.2.0"));
        assert_eq!(
            h.checks.iter().map(|c| c.name.as_str()).collect::<Vec<_>>(),
            ["celery", "database", "redis"]
        );
        let down = json!({"success": false, "data": null, "error": {"code": "SERVICE_UNAVAILABLE", "message": "x"}});
        assert_eq!(parse_health(503, &down).unwrap().status, "unhealthy");
        assert_eq!(parse_health(404, &json!({"detail": "nope"})), None);
    }

    #[tokio::test]
    async fn probe_rejects_invalid_urls_without_network() {
        assert!(probe("http://evil.example.com", None).await.is_err());
        assert!(probe("https://ok.example.com", Some("ftp://x"))
            .await
            .unwrap_err()
            .starts_with("Admin API URL"));
    }

    #[tokio::test]
    async fn probe_reports_unreachable_servers() {
        crate::install_crypto_provider();
        // Port 9 (discard) on loopback is closed on CI and dev machines.
        let r = probe("http://127.0.0.1:9", None).await.unwrap();
        assert!(!r.is_gpu_deck);
        assert!(r.insecure);
        assert!(r.error.is_some());
    }
}
