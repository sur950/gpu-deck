//! SGLang and SGLang Diffusion adapters.
//!
//! LLM: `python3 -m sglang.launch_server --model-path <m> --host --port --served-model-name
//! --context-length --tp-size --pp-size --mem-fraction-static --max-running-requests
//! [--tool-call-parser] [--reasoning-parser] [--speculative-*] [--disaggregation-*]
//! --enable-metrics`; Prometheus `sglang:*` on `/metrics`.
//!
//! Diffusion: `sglang serve --model-path <m> --host --port` (OpenAI `/v1/images/*`,
//! `/v1/videos`); no Prometheus metrics → proxy-derived. Runs on CUDA/ROCm and Apple Silicon (MPS).
//!
//! Status: fixture-only.

use std::time::Instant;

use anyhow::bail;
use async_trait::async_trait;
use serde_json::json;

use crate::flags::Flags;
use crate::http::{self, split_model, url};
use crate::kv;
use crate::launch::{base_env, bind_host, finish};
use crate::presets;
use crate::prometheus::{self, Snapshot};
use crate::state::{self, Hints, Prev};
use crate::types::*;
use crate::vllm::memory_fraction;
use crate::window::{hist_ms, PrevBuilder};

#[derive(Debug, Clone)]
pub struct SglangAdapter {
    kind: EngineKind,
}

impl SglangAdapter {
    pub fn new(kind: EngineKind) -> Self {
        assert!(matches!(
            kind,
            EngineKind::Sglang | EngineKind::SglangDiffusion
        ));
        SglangAdapter { kind }
    }

    fn diffusion(&self) -> bool {
        self.kind == EngineKind::SglangDiffusion
    }

    pub fn build_args(&self, spec: &LaunchSpec) -> anyhow::Result<(String, Vec<String>, Hints)> {
        let mut f = Flags::new(&[&["--tp-size", "--tp"], &["--pp-size", "--pp"]]);
        let (program, mut args) = if self.diffusion() {
            (
                spec.executable.clone().unwrap_or_else(|| "sglang".into()),
                vec!["serve".to_string()],
            )
        } else {
            if spec.vendor() == Some(Vendor::Apple) {
                bail!("SGLang LLM serving needs CUDA/ROCm; use mlx or llama.cpp on Apple Silicon");
            }
            (
                spec.executable.clone().unwrap_or_else(|| "python3".into()),
                vec!["-m".to_string(), "sglang.launch_server".to_string()],
            )
        };
        f.set("--model-path", spec.model_path.clone());
        f.set("--host", bind_host(spec))
            .set("--port", spec.port as u64);
        if spec.trust_remote_code {
            f.set("--trust-remote-code", true);
        }
        if spec.tp_size > 1 {
            f.set("--tp-size", spec.tp_size as u64);
        }
        if !self.diffusion() {
            f.set("--served-model-name", spec.served_model_name.clone());
            f.set("--enable-metrics", true);
            if let Some(c) = spec.context_size {
                f.set("--context-length", c as u64);
            }
            if spec.pp_size > 1 {
                f.set("--pp-size", spec.pp_size as u64);
            }
            if let Some(frac) = memory_fraction(spec) {
                f.set("--mem-fraction-static", json!(frac));
            }
            match spec.model_type {
                ModelType::Embedding => {
                    f.set("--is-embedding", true);
                }
                ModelType::Chat | ModelType::Thinking | ModelType::Vision => {
                    let parser = spec.tool_parser.clone().or_else(|| {
                        presets::default_tool_parser(self.kind, spec.model_family.as_deref())
                            .map(str::to_string)
                    });
                    if let Some(p) = parser.filter(|p| p != "none") {
                        f.set("--tool-call-parser", p);
                    }
                    let reasoning = spec.reasoning_parser.clone().or_else(|| {
                        presets::default_reasoning_parser(self.kind, spec.model_family.as_deref())
                            .map(str::to_string)
                    });
                    if let Some(r) = reasoning.filter(|r| r != "none") {
                        f.set("--reasoning-parser", r);
                    }
                }
                other => bail!(
                    "SGLang does not serve {other:?} models (use sglang_diffusion for image/video)"
                ),
            }
            let sd = &spec.spec_decoding;
            match sd.method.as_deref() {
                None | Some("none") => {}
                Some(m @ ("eagle" | "eagle3" | "mtp" | "draft_model" | "ngram")) => {
                    let algo = match m {
                        "eagle" | "mtp" => "EAGLE",
                        "eagle3" => "EAGLE3",
                        "ngram" => "NGRAM",
                        _ => "STANDALONE",
                    };
                    f.set("--speculative-algorithm", algo);
                    if let Some(p) = &sd.draft_model_path {
                        f.set("--speculative-draft-model-path", p.clone());
                    }
                    f.set(
                        "--speculative-num-draft-tokens",
                        sd.num_draft_tokens.unwrap_or(4) as u64,
                    );
                }
                Some(other) => bail!("unsupported spec_decoding method {other:?} for SGLang"),
            }
            if spec.pd_split {
                let role = spec.pd_role.clone().unwrap_or_else(|| "prefill".into());
                f.set("--disaggregation-mode", role);
                let backend = spec
                    .pd_config
                    .get("transfer_backend")
                    .and_then(|v| v.as_str())
                    .unwrap_or("mooncake")
                    .to_string();
                f.set("--disaggregation-transfer-backend", backend);
            }
            if spec.kv_sharing {
                f.set("--enable-hierarchical-cache", true);
            }
        } else if !matches!(spec.model_type, ModelType::Image | ModelType::Video) {
            bail!(
                "sglang_diffusion serves image/video models, not {:?}",
                spec.model_type
            );
        }
        if let Some(p) = spec.preset {
            f.merge_defaults(&presets::preset_flags(self.kind, p));
        }
        f.merge_user(&spec.launch_flags);
        if let (Some(n), false) = (spec.max_concurrency, self.diffusion()) {
            f.set("--max-running-requests", n as u64);
        }
        args.extend(f.to_args());
        let kv_dtype = f.get_str("--kv-cache-dtype");
        let hints = Hints {
            served_model_name: Some(spec.served_model_name.clone()),
            context_size: f.get_u64("--context-length"),
            max_concurrency: f.get_u64("--max-running-requests"),
            kv_unified: true,
            kv_bytes_per_token: kv::bytes_per_token(
                &spec.model_config,
                kv_dtype.as_deref(),
                kv_dtype.as_deref(),
            ),
            memory_fraction: f.get_f64("--mem-fraction-static"),
            router_mode: false,
            models_dir: None,
        };
        Ok((program, args, hints))
    }
}

/// Map a `sglang:*` scrape into an `EngineSample`.
pub fn sample_from(
    snap: &Snapshot,
    hints: Option<&Hints>,
    prev: Option<&Prev>,
    now: Instant,
) -> (EngineSample, Prev) {
    let mut s = EngineSample::now();
    let mut next = PrevBuilder::default();
    s.active_sessions = snap.sum("sglang:num_running_reqs").map(|v| v as u64);
    s.waiting_sessions = snap.sum("sglang:num_queue_reqs").map(|v| v as u64);
    s.requests_total = snap.sum("sglang:num_requests_total").map(|v| v as u64);
    let prompt = next.counter("prompt", snap.sum("sglang:prompt_tokens_total"));
    let gen = next.counter("gen", snap.sum("sglang:generation_tokens_total"));
    s.prompt_tokens_total = prompt.map(|v| v as u64);
    s.generated_tokens_total = gen.map(|v| v as u64);
    s.cached_tokens_total = snap.sum("sglang:cached_tokens_total").map(|v| v as u64);
    s.prompt_tps = state::rate(prev, "prompt", prompt, now);
    s.generation_tps = snap
        .sum("sglang:gen_throughput")
        .or_else(|| state::rate(prev, "gen", gen, now));
    (s.ttft_ms_avg, s.ttft_ms_p50, s.ttft_ms_p95) = hist_ms(
        snap,
        prev,
        &mut next,
        &["sglang:time_to_first_token_seconds"],
    );
    (s.e2e_ms_avg, s.e2e_ms_p50, s.e2e_ms_p95) = hist_ms(
        snap,
        prev,
        &mut next,
        &["sglang:e2e_request_latency_seconds"],
    );
    s.tpot_ms_avg = hist_ms(
        snap,
        prev,
        &mut next,
        &[
            "sglang:inter_token_latency_seconds",
            "sglang:time_per_output_token_seconds",
        ],
    )
    .0;
    s.queue_ms_avg = hist_ms(snap, prev, &mut next, &["sglang:queue_time_seconds"]).0;
    s.kv_cache_used_tokens = snap.sum("sglang:num_used_tokens").map(|v| v as u64);
    s.kv_cache_total_tokens = snap.get("sglang:max_total_num_tokens").map(|v| v as u64);
    s.kv_cache_usage_pct = snap
        .get("sglang:token_usage")
        .map(|v| v * 100.0)
        .or_else(|| match (s.kv_cache_used_tokens, s.kv_cache_total_tokens) {
            (Some(u), Some(t)) if t > 0 => Some(u as f64 * 100.0 / t as f64),
            _ => None,
        });
    s.prefix_cache_hit_rate = snap.get("sglang:cache_hit_rate");
    if let Some(v) = snap.get("sglang:spec_accept_length") {
        s.extra_insert("spec_accept_length", json!(v));
    }
    if let (Some(u), Some(b)) = (
        s.kv_cache_used_tokens,
        hints.and_then(|h| h.kv_bytes_per_token),
    ) {
        s.kv_cache_bytes = Some(u * b);
    }
    if let Some(max) = hints.and_then(|h| h.max_concurrency) {
        s.slots_total = Some(max);
        s.slots_idle = s.active_sessions.map(|a| max.saturating_sub(a));
    }
    (s, next.build(now))
}

#[async_trait]
impl EngineAdapter for SglangAdapter {
    fn kind(&self) -> EngineKind {
        self.kind
    }

    fn command(&self, spec: &LaunchSpec) -> anyhow::Result<ProcessSpec> {
        let (program, args, hints) = self.build_args(spec)?;
        state::register(&spec.base_url(), hints);
        let image = if self.diffusion() {
            None
        } else {
            Some("lmsysorg/sglang:latest")
        };
        finish(spec, program, args, base_env(spec), image)
    }

    async fn health(&self, base_url: &str) -> HealthStatus {
        http::probe(base_url, &self.health_config()).await
    }

    async fn scrape(&self, base_url: &str) -> anyhow::Result<EngineSample> {
        let (base, _) = split_model(base_url);
        if self.diffusion() {
            // No engine metrics: everything is proxy-derived.
            let mut s = EngineSample::now();
            s.health = Some(self.health(&base).await.instance_health());
            return Ok(s);
        }
        let text = http::get_ok(&url(&base, "/metrics", None)).await?;
        let snap = prometheus::parse(&text);
        let now = Instant::now();
        let prev = state::peek_prev(&base);
        let (mut s, next) = sample_from(&snap, state::hints(&base).as_ref(), prev.as_ref(), now);
        state::swap_prev(&base, next);
        s.health = Some(InstanceHealth::Healthy);
        Ok(s)
    }

    fn supports_hot_swap(&self) -> bool {
        false
    }

    async fn load_model(&self, _base_url: &str, model: &ModelRef) -> anyhow::Result<()> {
        bail!(
            "{} cannot load models into a running process ({})",
            self.kind,
            model.name
        )
    }

    async fn unload_model(&self, _base_url: &str, model: &str) -> anyhow::Result<()> {
        bail!(
            "{} cannot unload models from a running process ({model})",
            self.kind
        )
    }

    fn health_config(&self) -> HealthConfig {
        HealthConfig {
            startup_timeout_seconds: 1800.0,
            ..HealthConfig::default()
        }
    }

    fn metrics_config(&self) -> MetricsConfig {
        if self.diffusion() {
            MetricsConfig {
                path: None,
                format: "none".into(),
                derive_from_proxy: true,
                ..MetricsConfig::default()
            }
        } else {
            MetricsConfig {
                metric_prefix: Some("sglang:".into()),
                ..MetricsConfig::default()
            }
        }
    }

    fn allowed_path_prefixes(&self) -> Vec<&'static str> {
        if self.diffusion() {
            vec!["/v1/images/", "/v1/videos", "/v1/models"]
        } else {
            vec!["/v1/", "/generate", "/v1/rerank"]
        }
    }

    fn admin_paths(&self) -> Vec<&'static str> {
        vec![
            "/metrics",
            "/health",
            "/health_generate",
            "/flush_cache",
            "/update_weights_from_disk",
            "/get_server_info",
            "/start_profile",
            "/stop_profile",
            "/abort_request",
        ]
    }
}
