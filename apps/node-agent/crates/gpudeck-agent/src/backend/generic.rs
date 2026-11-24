//! Engine driver that needs nothing engine-specific: HTTP health probe per `HealthConfig`,
//! llama.cpp-style router admin calls (`/models`, `/models/load|unload`), and metrics derived
//! from the agent proxy (`metrics.md` §4). Used for every engine when built without the plugins and as
//! the proxy-merge implementation shared with the plugin bridge.

use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use async_trait::async_trait;
use gpudeck_protocol::{
    EngineName, HealthConfig, HttpMethod, MetricsConfig, SessionSample, SessionState,
};
use uuid::Uuid;

use super::{EngineDriver, EngineLaunch, Health, ProcSpec, ProxySnapshot, RouterModel, Scrape};
use crate::proxy::stats::window_stats;

pub struct GenericDriver {
    name: EngineName,
    http: reqwest::Client,
}

impl GenericDriver {
    pub fn new(name: EngineName) -> Self {
        Self {
            name,
            http: local_http_client(),
        }
    }
}

/// Client for engine admin calls on localhost (no proxy, short timeouts).
pub fn local_http_client() -> reqwest::Client {
    reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .expect("http client")
}

pub fn with_model(url: &str, model: Option<&str>) -> String {
    match model {
        Some(m) => format!(
            "{url}{}model={}",
            if url.contains('?') { '&' } else { '?' },
            urlencoding::encode(m)
        ),
        None => url.to_string(),
    }
}

/// HTTP health probe shared by every driver.
pub async fn probe(
    http: &reqwest::Client,
    base_url: &str,
    health: &HealthConfig,
    model: Option<&str>,
) -> Health {
    let url = format!("{}{}", base_url.trim_end_matches('/'), health.path);
    let url = if model.is_some() && health.path == "/health" {
        url
    } else {
        with_model(&url, model)
    };
    let req = match health.method {
        HttpMethod::Get => http.get(&url),
        HttpMethod::Post => http.post(&url),
    };
    match req
        .timeout(Duration::from_secs_f64(health.timeout_seconds.max(0.5)))
        .send()
        .await
    {
        Ok(resp) => {
            let status = resp.status().as_u16();
            if health.healthy_statuses.contains(&status) {
                Health::Healthy
            } else if status == 503 {
                Health::Loading
            } else {
                Health::Unhealthy(format!("health {url} returned {status}"))
            }
        }
        Err(e) if e.is_connect() => Health::Unhealthy(format!("connect {url}: refused")),
        Err(e) => Health::Unhealthy(format!("health {url}: {e}")),
    }
}

/// llama.cpp router `GET /models`.
pub async fn router_models(http: &reqwest::Client, base_url: &str) -> Result<Vec<RouterModel>> {
    let v: serde_json::Value = http
        .get(format!("{base_url}/models"))
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    let data = v
        .get("data")
        .and_then(|d| d.as_array())
        .ok_or_else(|| anyhow!("router /models: no data array"))?;
    Ok(data
        .iter()
        .filter_map(|m| {
            Some(RouterModel {
                id: m.get("id")?.as_str()?.to_string(),
                status: m
                    .pointer("/status/value")
                    .and_then(|s| s.as_str())
                    .unwrap_or("unknown")
                    .to_string(),
                failed: m
                    .pointer("/status/failed")
                    .and_then(|f| f.as_bool())
                    .unwrap_or(false),
            })
        })
        .collect())
}

pub async fn router_call(
    http: &reqwest::Client,
    base_url: &str,
    op: &str,
    name: &str,
) -> Result<()> {
    let resp = http
        .post(format!("{base_url}/models/{op}"))
        .json(&serde_json::json!({ "model": name }))
        .send()
        .await
        .with_context(|| format!("router {op}"))?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if status.is_success() {
        return Ok(());
    }
    // Idempotent outcomes.
    if op == "load" && text.contains("already running") {
        return Ok(());
    }
    if op == "unload" && text.contains("not running") {
        return Ok(());
    }
    bail!(
        "router {op} {name}: HTTP {status}: {}",
        text.chars().take(300).collect::<String>()
    )
}

#[async_trait]
impl EngineDriver for GenericDriver {
    fn name(&self) -> EngineName {
        self.name
    }

    fn adapter_command(&self, launch: &EngineLaunch) -> Result<ProcSpec> {
        bail!("engine {} has no adapter command builder in this build; give a launch_template argv (model {:?})", self.name, launch.served_model_name)
    }

    async fn health(&self, base_url: &str, health: &HealthConfig, model: Option<&str>) -> Health {
        if let Some(m) = model {
            // Router: per-model readiness comes from /models.
            return match router_models(&self.http, base_url).await {
                Ok(models) => match models.iter().find(|r| r.id == m) {
                    Some(r) if r.status == "loaded" || r.status == "sleeping" => Health::Healthy,
                    Some(r) if r.failed => Health::Unhealthy(format!("router model {m} failed")),
                    Some(r) if r.status == "loading" => Health::Loading,
                    Some(_) => Health::Unhealthy(format!("router model {m} not loaded")),
                    None => Health::Unhealthy(format!("router does not know model {m}")),
                },
                Err(e) => Health::Unhealthy(e.to_string()),
            };
        }
        probe(&self.http, base_url, health, None).await
    }

    async fn scrape(
        &self,
        _base_url: &str,
        _metrics: &MetricsConfig,
        _model: Option<&str>,
    ) -> Result<Scrape> {
        Ok(Scrape::default())
    }

    fn supports_hot_swap(&self) -> bool {
        self.name == EngineName::LlamaCpp
    }

    async fn load_model(&self, base_url: &str, name: &str, _path: Option<&str>) -> Result<()> {
        router_call(&self.http, base_url, "load", name).await
    }

    async fn unload_model(&self, base_url: &str, name: &str) -> Result<()> {
        router_call(&self.http, base_url, "unload", name).await
    }

    async fn router_models(&self, base_url: &str) -> Result<Vec<RouterModel>> {
        router_models(&self.http, base_url).await
    }

    fn merge_proxy(&self, scrape: &mut Scrape, snap: &ProxySnapshot, derive_from_proxy: bool) {
        merge_proxy(scrape, snap, derive_from_proxy);
    }

    fn admin_paths(&self) -> Vec<String> {
        super::ALWAYS_BLOCKED_PATHS
            .iter()
            .map(|s| s.to_string())
            .collect()
    }
}

/// Fill `scrape` from the proxy snapshot. Engine-provided values win unless `derive_all`.
pub fn merge_proxy(scrape: &mut Scrape, snap: &ProxySnapshot, derive_all: bool) {
    let e = &mut scrape.engine;
    macro_rules! fill {
        ($field:ident, $val:expr) => {
            if derive_all || e.$field.is_none() {
                e.$field = $val;
            }
        };
    }
    // Request/error counters always come from the proxy (engines rarely count them).
    fill!(requests_total, Some(snap.requests_total));
    fill!(requests_success_total, Some(snap.requests_success_total));
    fill!(errors_total, Some(snap.errors_total));
    fill!(active_sessions, Some(snap.active));
    fill!(waiting_sessions, Some(snap.waiting));
    if derive_all {
        e.prompt_tokens_total = Some(snap.prompt_tokens_total);
        e.generated_tokens_total = Some(snap.generated_tokens_total);
        e.cached_tokens_total = snap.cached_tokens_total;
        e.slots_total = snap.max_concurrent;
        e.slots_idle = snap.max_concurrent.map(|t| t.saturating_sub(snap.active));
    } else if e.cached_tokens_total.is_none() {
        e.cached_tokens_total = snap.cached_tokens_total;
    }
    let w = window_stats(&snap.window, snap.window_seconds);
    fill!(ttft_ms_avg, w.ttft_ms_avg);
    fill!(ttft_ms_p50, w.ttft_ms_p50);
    fill!(ttft_ms_p95, w.ttft_ms_p95);
    fill!(e2e_ms_avg, w.e2e_ms_avg);
    fill!(e2e_ms_p50, w.e2e_ms_p50);
    fill!(e2e_ms_p95, w.e2e_ms_p95);
    fill!(tpot_ms_avg, w.tpot_ms_avg);
    fill!(queue_ms_avg, w.queue_ms_avg);
    fill!(prompt_tps, w.prompt_tps);
    fill!(generation_tps, w.generation_tps);
    if e.spec_accept_rate.is_none() && snap.draft_tokens_total > 0 {
        e.spec_accept_rate =
            Some(snap.draft_accepted_total as f64 / snap.draft_tokens_total as f64);
    }
    if derive_all {
        // KV estimate from in-flight (prompt + generated) tokens.
        let used: u64 = snap
            .inflight
            .iter()
            .filter(|i| !i.queued)
            .map(|i| i.prompt_tokens.unwrap_or(0) + i.chunks_seen)
            .sum();
        if used > 0 || !snap.inflight.is_empty() {
            e.kv_cache_used_tokens = Some(used);
        }
        if let (Some(ctx), Some(conc)) = (snap.context_size, snap.max_concurrent) {
            e.kv_cache_total_tokens = Some(ctx * conc);
            if ctx * conc > 0 {
                e.kv_cache_usage_pct = Some(used as f64 * 100.0 / (ctx * conc) as f64);
            }
        }
        if let Some(bpt) = snap.kv_bytes_per_token {
            e.kv_cache_bytes = Some(used * bpt);
        }
    }
    // Proxy-derived sessions when the engine reported none.
    if scrape.sessions.is_empty() {
        for i in &snap.inflight {
            let state = if i.queued {
                SessionState::Waiting
            } else if i.first_byte_seen {
                SessionState::Generating
            } else {
                SessionState::Processing
            };
            let mut s = SessionSample::new(Uuid::nil(), i.connection_id.clone(), state);
            s.request_id = i.request_id.as_deref().and_then(|r| r.parse().ok());
            s.prompt_tokens = i.prompt_tokens;
            s.generated_tokens = Some(i.chunks_seen);
            s.kv_cache_tokens = Some(i.prompt_tokens.unwrap_or(0) + i.chunks_seen);
            s.kv_cache_bytes = snap
                .kv_bytes_per_token
                .map(|b| b * (i.prompt_tokens.unwrap_or(0) + i.chunks_seen));
            s.context_size = snap.context_size;
            s.ttft_ms = i.ttft_ms;
            s.elapsed_ms = Some(i.elapsed_ms);
            if i.elapsed_ms > 0.0 && i.chunks_seen > 0 {
                s.tokens_per_second = Some(i.chunks_seen as f64 * 1000.0 / i.elapsed_ms);
            }
            scrape.sessions.push(s);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proxy::stats::{Inflight, RequestRecord};

    #[test]
    fn merge_fills_only_missing_unless_derive() {
        let snap = ProxySnapshot {
            requests_total: 3,
            requests_success_total: 2,
            errors_total: 1,
            prompt_tokens_total: 30,
            generated_tokens_total: 60,
            active: 1,
            max_concurrent: Some(2),
            window: vec![RequestRecord {
                status: 200,
                e2e_ms: 50.0,
                ttft_ms: Some(10.0),
                completion_tokens: Some(5),
                prompt_tokens: Some(4),
                ..Default::default()
            }],
            window_seconds: 2.0,
            inflight: vec![Inflight {
                connection_id: "proxy-1".into(),
                request_id: Some(Uuid::new_v4().to_string()),
                elapsed_ms: 100.0,
                queued: false,
                first_byte_seen: true,
                ttft_ms: Some(5.0),
                prompt_tokens: Some(7),
                chunks_seen: 3,
            }],
            context_size: Some(100),
            kv_bytes_per_token: Some(2),
            ..Default::default()
        };
        let mut s = Scrape::default();
        s.engine.prompt_tokens_total = Some(999);
        merge_proxy(&mut s, &snap, false);
        assert_eq!(s.engine.prompt_tokens_total, Some(999));
        assert_eq!(s.engine.requests_total, Some(3));
        assert_eq!(s.engine.ttft_ms_avg, Some(10.0));
        assert_eq!(s.engine.kv_cache_used_tokens, None);
        assert_eq!(s.sessions.len(), 1);
        assert_eq!(s.sessions[0].state, SessionState::Generating);
        let mut d = Scrape::default();
        merge_proxy(&mut d, &snap, true);
        assert_eq!(d.engine.prompt_tokens_total, Some(30));
        assert_eq!(d.engine.slots_idle, Some(1));
        assert_eq!(d.engine.kv_cache_used_tokens, Some(10));
        assert_eq!(d.engine.kv_cache_total_tokens, Some(200));
        assert_eq!(d.engine.kv_cache_bytes, Some(20));
    }

    #[test]
    fn model_query_appended() {
        assert_eq!(
            with_model("http://x/metrics", Some("a b")),
            "http://x/metrics?model=a%20b"
        );
        assert_eq!(
            with_model("http://x/slots?y=1", Some("m")),
            "http://x/slots?y=1&model=m"
        );
    }
}
