//! Shared HTTP plumbing for adapters: one pooled client (no system proxy: engines are on
//! loopback), URL helpers for llama.cpp router-mode instance URLs, and the generic health probe.

use std::sync::OnceLock;
use std::time::Duration;

use anyhow::{anyhow, Context};

use crate::types::{HealthConfig, HealthStatus};

/// Per-request timeout for scrapes and admin calls.
pub const SCRAPE_TIMEOUT: Duration = Duration::from_secs(5);

pub fn client() -> &'static reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::Client::builder()
            .no_proxy()
            .pool_idle_timeout(Duration::from_secs(30))
            .connect_timeout(Duration::from_secs(2))
            .build()
            .expect("reqwest client")
    })
}

/// Split an instance URL into `(base, model)`: `http://127.0.0.1:10240?model=qwen3` →
/// (`http://127.0.0.1:10240`, `Some("qwen3")`). The `?model=` form addresses one model of a
/// llama.cpp router process (see `llama_cpp::instance_url`).
pub fn split_model(url: &str) -> (String, Option<String>) {
    let (base, query) = match url.split_once('?') {
        Some((b, q)) => (b, Some(q)),
        None => (url, None),
    };
    let base = base.trim_end_matches('/').to_string();
    let model = query.and_then(|q| {
        q.split('&').find_map(|kv| {
            let (k, v) = kv.split_once('=')?;
            if k == "model" {
                Some(percent_decode(v))
            } else {
                None
            }
        })
    });
    (base, model)
}

/// `base + path`, adding `?model=<m>` when routing to a router model.
pub fn url(base: &str, path: &str, model: Option<&str>) -> String {
    let mut u = format!("{}{}", base.trim_end_matches('/'), path);
    if let Some(m) = model {
        u.push(if u.contains('?') { '&' } else { '?' });
        u.push_str("model=");
        u.push_str(&percent_encode(m));
    }
    u
}

/// Minimal RFC 3986 percent-encoding of a query value.
pub fn percent_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

pub fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(v) = u8::from_str_radix(&s[i + 1..i + 3], 16) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(if bytes[i] == b'+' { b' ' } else { bytes[i] });
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

pub async fn get_text(url: &str) -> anyhow::Result<(u16, String)> {
    let resp = client()
        .get(url)
        .timeout(SCRAPE_TIMEOUT)
        .send()
        .await
        .with_context(|| format!("GET {url}"))?;
    let status = resp.status().as_u16();
    let body = resp
        .text()
        .await
        .with_context(|| format!("GET {url}: body"))?;
    Ok((status, body))
}

/// GET returning the body only on 2xx.
pub async fn get_ok(url: &str) -> anyhow::Result<String> {
    let (status, body) = get_text(url).await?;
    if !(200..300).contains(&status) {
        return Err(anyhow!("GET {url} -> {status}: {}", truncate(&body, 300)));
    }
    Ok(body)
}

pub async fn get_json(url: &str) -> anyhow::Result<serde_json::Value> {
    let body = get_ok(url).await?;
    serde_json::from_str(&body).with_context(|| format!("GET {url}: invalid JSON"))
}

pub async fn post_json(
    url: &str,
    body: &serde_json::Value,
    timeout: Duration,
) -> anyhow::Result<(u16, String)> {
    let resp = client()
        .post(url)
        .json(body)
        .timeout(timeout)
        .send()
        .await
        .with_context(|| format!("POST {url}"))?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    Ok((status, text))
}

pub fn truncate(s: &str, n: usize) -> &str {
    match s.char_indices().nth(n) {
        Some((i, _)) => &s[..i],
        None => s,
    }
}

/// Generic probe per `HealthConfig`: a status in `healthy_statuses` is healthy, 503 is loading,
/// anything else (or a transport error) is unhealthy.
pub async fn probe(base_url: &str, cfg: &HealthConfig) -> HealthStatus {
    let (base, _) = split_model(base_url);
    let u = url(&base, &cfg.path, None);
    let timeout = Duration::from_secs_f64(cfg.timeout_seconds.max(0.5));
    let req = if cfg.method.eq_ignore_ascii_case("POST") {
        client().post(&u)
    } else {
        client().get(&u)
    };
    match req.timeout(timeout).send().await {
        Ok(r) => {
            let code = r.status().as_u16();
            if cfg.healthy_statuses.contains(&code) {
                HealthStatus::Healthy
            } else if code == 503 {
                HealthStatus::Loading
            } else {
                HealthStatus::unhealthy(format!("{} {} -> {code}", cfg.method, cfg.path))
            }
        }
        Err(e) => HealthStatus::unhealthy(format!(
            "{} {}: {}",
            cfg.method,
            cfg.path,
            connect_reason(&e)
        )),
    }
}

fn connect_reason(e: &reqwest::Error) -> String {
    if e.is_connect() {
        "connection refused".into()
    } else if e.is_timeout() {
        "timeout".into()
    } else {
        e.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn model_urls() {
        let (b, m) = split_model("http://127.0.0.1:10240/?model=org%2Fqwen3%3AQ4");
        assert_eq!(b, "http://127.0.0.1:10240");
        assert_eq!(m.as_deref(), Some("org/qwen3:Q4"));
        assert_eq!(
            url(&b, "/slots", m.as_deref()),
            "http://127.0.0.1:10240/slots?model=org%2Fqwen3%3AQ4"
        );
        assert_eq!(split_model("http://h:1").1, None);
        assert_eq!(url("http://h:1/", "/metrics", None), "http://h:1/metrics");
        assert_eq!(percent_decode("a%2"), "a%2");
    }
}
