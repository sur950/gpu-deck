//! `custom` engines: any server described by a [`CustomEngineConfig`] (container image or native
//! binary + how to start it, probe it and read its metrics).
//!
//! * **command** — `config.command` argv with `{{placeholders}}` (`launch::template_vars`);
//!   `{{flags}}` expands `launch_flags`. `runtime=container` wraps it in `docker run`
//!   (`LaunchSpec.container.image`).
//! * **health** — `config.health` (`HealthConfig`).
//! * **metrics** — `config.metrics.format`:
//!   * `prometheus`: `metric_map` maps engine metric names to canonical `EngineSample` fields; a
//!     target may carry a scale (`"kv_cache_usage_pct*100"`, `"ttft_ms_avg*1000"`); a target
//!     `ttft_ms_hist` / `e2e_ms_hist` / `tpot_ms_hist` / `queue_ms_hist` reads a *seconds*
//!     histogram and fills avg/p50/p95 over the scrape window. `*_tokens_total` counters also
//!     produce `prompt_tps` / `generation_tps` rates when those are not mapped.
//!   * `json`: `metric_map` keys are JSON pointers (`/stats/running`) or dotted paths
//!     (`stats.running`).
//!   * `none`: everything is proxy-derived.
//! * **hot swap** — `supports_hot_swap` + `load_path` / `unload_path`
//!   (`POST {"model": name, "path": p}`).
//!
//! The config is registered per base URL by `command()`, so `adapter_for(Custom)` works for
//! health/scrape/load; `CustomAdapter::with_config` additionally answers `supports_hot_swap`.

use std::collections::{BTreeMap, HashMap};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail};
use async_trait::async_trait;
use serde_json::{json, Map, Value};

use crate::flags::render_map;
use crate::http::{self, split_model, url};
use crate::launch::{base_env, expand, finish};
use crate::prometheus::{self, Snapshot};
use crate::state::{self, Hints, Prev};
use crate::types::*;
use crate::window::{hist_ms, PrevBuilder};

fn configs() -> &'static Mutex<HashMap<String, CustomEngineConfig>> {
    static M: OnceLock<Mutex<HashMap<String, CustomEngineConfig>>> = OnceLock::new();
    M.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Config registered for an engine base URL by `command()`.
pub fn config_for(base_url: &str) -> Option<CustomEngineConfig> {
    configs()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(&split_model(base_url).0)
        .cloned()
}

pub fn register_config(base_url: &str, cfg: CustomEngineConfig) {
    configs()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .insert(split_model(base_url).0, cfg);
}

#[derive(Debug, Default, Clone)]
pub struct CustomAdapter {
    config: Option<CustomEngineConfig>,
    /// `allowed_path_prefixes` / `admin_paths` as `'static` strs (leaked once per adapter).
    allowed: Vec<&'static str>,
    admin: Vec<&'static str>,
}

fn leak(s: &str) -> &'static str {
    Box::leak(s.to_string().into_boxed_str())
}

impl CustomAdapter {
    pub fn new() -> Self {
        CustomAdapter {
            config: None,
            allowed: vec!["/v1/"],
            admin: vec!["/metrics", "/health"],
        }
    }

    pub fn with_config(config: CustomEngineConfig) -> Self {
        let allowed = if config.allowed_path_prefixes.is_empty() {
            vec!["/v1/"]
        } else {
            config
                .allowed_path_prefixes
                .iter()
                .map(|p| leak(p))
                .collect()
        };
        let mut admin = vec!["/metrics", "/health"];
        for p in [
            &config.load_path,
            &config.unload_path,
            &config.metrics.path,
            &Some(config.health.path.clone()),
        ]
        .into_iter()
        .flatten()
        {
            if !admin.contains(&p.as_str()) {
                admin.push(leak(p));
            }
        }
        CustomAdapter {
            config: Some(config),
            allowed,
            admin,
        }
    }

    fn cfg(&self, base_url: &str) -> Option<CustomEngineConfig> {
        config_for(base_url).or_else(|| self.config.clone())
    }
}

/// `EngineSample` integer fields (JSON numbers must be written as integers for them).
const INT_FIELDS: &[&str] = &[
    "active_sessions",
    "waiting_sessions",
    "slots_total",
    "slots_idle",
    "requests_total",
    "requests_success_total",
    "errors_total",
    "prompt_tokens_total",
    "generated_tokens_total",
    "cached_tokens_total",
    "kv_cache_used_tokens",
    "kv_cache_total_tokens",
    "kv_cache_bytes",
    "process_vram_bytes",
    "process_rss_bytes",
];

const FLOAT_FIELDS: &[&str] = &[
    "prompt_tps",
    "generation_tps",
    "ttft_ms_avg",
    "ttft_ms_p50",
    "ttft_ms_p95",
    "e2e_ms_avg",
    "e2e_ms_p50",
    "e2e_ms_p95",
    "tpot_ms_avg",
    "queue_ms_avg",
    "kv_cache_usage_pct",
    "prefix_cache_hit_rate",
    "spec_accept_rate",
];

/// `"field*100"` → (`field`, 100.0).
fn parse_target(t: &str) -> (String, f64) {
    if let Some((f, s)) = t.split_once('*') {
        (f.trim().to_string(), s.trim().parse().unwrap_or(1.0))
    } else if let Some((f, s)) = t.split_once('/') {
        (
            f.trim().to_string(),
            1.0 / s.trim().parse::<f64>().unwrap_or(1.0),
        )
    } else {
        (t.trim().to_string(), 1.0)
    }
}

/// Write canonical `values` into an `EngineSample` (unknown targets go to `extra`).
pub fn sample_from_values(values: &BTreeMap<String, f64>) -> EngineSample {
    let mut s = EngineSample::now();
    let mut obj = match serde_json::to_value(&s) {
        Ok(Value::Object(m)) => m,
        _ => Map::new(),
    };
    let mut extra = Map::new();
    for (k, v) in values {
        if !v.is_finite() {
            continue;
        }
        if INT_FIELDS.contains(&k.as_str()) {
            obj.insert(k.clone(), json!(v.max(0.0).round() as u64));
        } else if FLOAT_FIELDS.contains(&k.as_str()) {
            obj.insert(k.clone(), json!(v));
        } else {
            extra.insert(k.clone(), json!(v));
        }
    }
    if let Ok(parsed) = serde_json::from_value::<EngineSample>(Value::Object(obj)) {
        s = parsed;
    }
    for (k, v) in extra {
        s.extra_insert(&k, v);
    }
    s
}

/// Prometheus scrape mapped through `metric_map`.
pub fn sample_from_prometheus(
    snap: &Snapshot,
    map: &BTreeMap<String, String>,
    prev: Option<&Prev>,
    now: Instant,
) -> (EngineSample, Prev) {
    let mut values = BTreeMap::new();
    let mut next = PrevBuilder::default();
    let mut hist_targets: Vec<(String, String)> = Vec::new();
    for (src, target) in map {
        let (field, scale) = parse_target(target);
        if field.ends_with("_ms_hist") {
            hist_targets.push((src.clone(), field));
            continue;
        }
        if let Some(v) = snap.sum(src) {
            values.insert(field.clone(), v * scale);
            if field.ends_with("_total") {
                next.counter(&field, Some(v * scale));
            }
        }
    }
    for (rate_field, counter) in [
        ("prompt_tps", "prompt_tokens_total"),
        ("generation_tps", "generated_tokens_total"),
    ] {
        if !values.contains_key(rate_field) {
            if let Some(r) = state::rate(prev, counter, values.get(counter).copied(), now) {
                values.insert(rate_field.into(), r);
            }
        }
    }
    let mut s = sample_from_values(&values);
    for (src, field) in hist_targets {
        let (avg, p50, p95) = hist_ms(snap, prev, &mut next, &[src.as_str()]);
        match field.as_str() {
            "ttft_ms_hist" => (s.ttft_ms_avg, s.ttft_ms_p50, s.ttft_ms_p95) = (avg, p50, p95),
            "e2e_ms_hist" => (s.e2e_ms_avg, s.e2e_ms_p50, s.e2e_ms_p95) = (avg, p50, p95),
            "tpot_ms_hist" => s.tpot_ms_avg = avg,
            "queue_ms_hist" => s.queue_ms_avg = avg,
            _ => {}
        }
    }
    (s, next.build(now))
}

/// JSON document mapped through `metric_map` (pointer or dotted keys).
pub fn sample_from_json(doc: &Value, map: &BTreeMap<String, String>) -> EngineSample {
    let mut values = BTreeMap::new();
    for (src, target) in map {
        let (field, scale) = parse_target(target);
        let ptr = if src.starts_with('/') {
            src.clone()
        } else {
            format!("/{}", src.replace('.', "/"))
        };
        if let Some(v) = doc.pointer(&ptr).and_then(|v| {
            v.as_f64()
                .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        }) {
            values.insert(field, v * scale);
        }
    }
    sample_from_values(&values)
}

#[async_trait]
impl EngineAdapter for CustomAdapter {
    fn kind(&self) -> EngineKind {
        EngineKind::Custom
    }

    fn command(&self, spec: &LaunchSpec) -> anyhow::Result<ProcessSpec> {
        let cfg = spec
            .custom
            .clone()
            .or_else(|| self.config.clone())
            .ok_or_else(|| anyhow!("custom engine needs LaunchSpec.custom (CustomEngineConfig)"))?;
        if cfg.command.is_empty()
            && spec
                .container
                .as_ref()
                .map(|c| c.command.is_empty())
                .unwrap_or(true)
        {
            bail!("custom engine config has an empty command");
        }
        let flags = render_map(&spec.launch_flags);
        let argv = expand(&cfg.command, spec, &flags)?;
        let mut env = base_env(spec);
        for (k, v) in &cfg.env {
            env.entry(k.clone()).or_insert_with(|| v.clone());
        }
        register_config(&spec.base_url(), cfg.clone());
        state::register(
            &spec.base_url(),
            Hints {
                served_model_name: Some(spec.served_model_name.clone()),
                context_size: spec.context_size.map(u64::from),
                max_concurrency: spec.max_concurrency.map(u64::from),
                kv_unified: true,
                kv_bytes_per_token: crate::kv::bytes_per_token(&spec.model_config, None, None),
                ..Hints::default()
            },
        );
        let (program, args) = match argv.split_first() {
            Some((p, rest)) => (p.clone(), rest.to_vec()),
            None => (String::new(), Vec::new()),
        };
        let mut spec2 = spec.clone();
        if spec2.working_dir.is_none() {
            spec2.working_dir = cfg.working_dir.clone();
        }
        finish(&spec2, program, args, env, None)
    }

    async fn health(&self, base_url: &str) -> HealthStatus {
        let cfg = self.cfg(base_url).map(|c| c.health).unwrap_or_default();
        http::probe(base_url, &cfg).await
    }

    async fn scrape(&self, base_url: &str) -> anyhow::Result<EngineSample> {
        let (base, _) = split_model(base_url);
        let cfg = self.cfg(&base).unwrap_or_default();
        let path = cfg.metrics.path.clone();
        let mut s = match (cfg.metrics.format.as_str(), path) {
            ("prometheus", Some(p)) => {
                let text = http::get_ok(&url(&base, &p, None)).await?;
                let snap = prometheus::parse(&text);
                let now = Instant::now();
                let prev = state::peek_prev(&base);
                let (s, next) = sample_from_prometheus(&snap, &cfg.metric_map, prev.as_ref(), now);
                state::swap_prev(&base, next);
                s
            }
            ("json", Some(p)) => sample_from_json(
                &http::get_json(&url(&base, &p, None)).await?,
                &cfg.metric_map,
            ),
            _ => return crate::mlx::proxy_only_sample(self, &base).await,
        };
        s.health = Some(InstanceHealth::Healthy);
        if let Some(h) = state::hints(&base) {
            if s.slots_total.is_none() {
                s.slots_total = h.max_concurrency;
            }
        }
        Ok(s)
    }

    fn supports_hot_swap(&self) -> bool {
        self.config
            .as_ref()
            .map(|c| c.supports_hot_swap)
            .unwrap_or(false)
    }

    async fn load_model(&self, base_url: &str, model: &ModelRef) -> anyhow::Result<()> {
        let cfg = self
            .cfg(base_url)
            .ok_or_else(|| anyhow!("no custom engine config registered for {base_url}"))?;
        let path = cfg
            .load_path
            .clone()
            .filter(|_| cfg.supports_hot_swap)
            .ok_or_else(|| anyhow!("custom engine does not support hot swap"))?;
        let (base, _) = split_model(base_url);
        let (code, body) = http::post_json(
            &url(&base, &path, None),
            &json!({"model": model.name, "path": model.path}),
            Duration::from_secs(600),
        )
        .await?;
        if !(200..300).contains(&code) {
            bail!(
                "custom load {}: {code} {}",
                model.name,
                http::truncate(&body, 300)
            );
        }
        Ok(())
    }

    async fn unload_model(&self, base_url: &str, model: &str) -> anyhow::Result<()> {
        let cfg = self
            .cfg(base_url)
            .ok_or_else(|| anyhow!("no custom engine config registered for {base_url}"))?;
        let path = cfg
            .unload_path
            .clone()
            .filter(|_| cfg.supports_hot_swap)
            .ok_or_else(|| anyhow!("custom engine does not support hot swap"))?;
        let (base, _) = split_model(base_url);
        let (code, body) = http::post_json(
            &url(&base, &path, None),
            &json!({"model": model}),
            Duration::from_secs(120),
        )
        .await?;
        if !(200..300).contains(&code) {
            bail!(
                "custom unload {model}: {code} {}",
                http::truncate(&body, 300)
            );
        }
        Ok(())
    }

    fn health_config(&self) -> HealthConfig {
        self.config
            .as_ref()
            .map(|c| c.health.clone())
            .unwrap_or_default()
    }

    fn metrics_config(&self) -> MetricsConfig {
        match &self.config {
            Some(c) => {
                let mut m = c.metrics.clone();
                if m.format == "none" || m.path.is_none() {
                    m.derive_from_proxy = true;
                }
                m
            }
            None => MetricsConfig {
                path: None,
                format: "none".into(),
                derive_from_proxy: true,
                ..MetricsConfig::default()
            },
        }
    }

    fn allowed_path_prefixes(&self) -> Vec<&'static str> {
        self.allowed.clone()
    }

    fn admin_paths(&self) -> Vec<&'static str> {
        self.admin.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn targets() {
        assert_eq!(
            parse_target("kv_cache_usage_pct*100"),
            ("kv_cache_usage_pct".into(), 100.0)
        );
        assert_eq!(parse_target("x/1000"), ("x".into(), 0.001));
        let s = sample_from_values(&BTreeMap::from([
            ("active_sessions".into(), 2.0),
            ("kv_cache_usage_pct".into(), 12.5),
            ("custom_x".into(), 1.0),
        ]));
        assert_eq!(s.active_sessions, Some(2));
        assert_eq!(s.kv_cache_usage_pct, Some(12.5));
        assert_eq!(s.extra.unwrap()["custom_x"], json!(1.0));
    }
}
