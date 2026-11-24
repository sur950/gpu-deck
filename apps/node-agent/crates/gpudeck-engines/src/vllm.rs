//! vLLM (V1 engine) and vLLM-Omni adapters.
//!
//! Command: `vllm serve <model> --host --port --served-model-name <name> --max-model-len <ctx>
//! --tensor-parallel-size --pipeline-parallel-size --gpu-memory-utilization <slice share>
//! --max-num-seqs <concurrency> [--enable-auto-tool-choice --tool-call-parser P]
//! [--reasoning-parser R] [--speculative-config JSON] [--kv-transfer-config JSON]`; vLLM-Omni adds
//! `--omni`. Metrics: Prometheus `vllm:*` on `/metrics` (`metrics.md` §2). Status: fixture-only
//! (no NVIDIA/AMD GPU on the reference Mac).

use std::time::Instant;

use anyhow::bail;
use async_trait::async_trait;
use serde_json::{json, Value};

use crate::flags::Flags;
use crate::http::{self, split_model, url};
use crate::kv;
use crate::launch::{base_env, bind_host, finish};
use crate::presets;
use crate::prometheus::{self, Snapshot};
use crate::state::{self, Hints, Prev};
use crate::types::*;
use crate::window::{delta_ratio, hist_ms, PrevBuilder};

#[derive(Debug, Clone)]
pub struct VllmAdapter {
    kind: EngineKind,
}

impl VllmAdapter {
    pub fn new(kind: EngineKind) -> Self {
        assert!(matches!(kind, EngineKind::Vllm | EngineKind::VllmOmni));
        VllmAdapter { kind }
    }

    pub fn build_args(&self, spec: &LaunchSpec) -> anyhow::Result<(Vec<String>, Hints)> {
        if spec.vendor() == Some(Vendor::Apple) {
            bail!("vLLM does not run on Apple Silicon GPUs; use mlx or llama.cpp");
        }
        let mut f = Flags::new(&[
            &["--tensor-parallel-size", "-tp"],
            &["--pipeline-parallel-size", "-pp"],
        ]);
        f.set("--host", bind_host(spec))
            .set("--port", spec.port as u64);
        f.set("--served-model-name", spec.served_model_name.clone());
        if self.kind == EngineKind::VllmOmni {
            f.set("--omni", true);
        }
        if let Some(c) = spec.context_size {
            f.set("--max-model-len", c as u64);
        }
        if spec.tp_size > 1 {
            f.set("--tensor-parallel-size", spec.tp_size as u64);
        }
        if spec.pp_size > 1 {
            f.set("--pipeline-parallel-size", spec.pp_size as u64);
        }
        if let Some(frac) = memory_fraction(spec) {
            f.set("--gpu-memory-utilization", json!(frac));
        }
        if spec.trust_remote_code {
            f.set("--trust-remote-code", true);
        }
        match spec.model_type {
            ModelType::Embedding => {
                f.set("--task", "embed");
            }
            ModelType::Rerank => {
                f.set("--task", "score");
            }
            ModelType::Chat | ModelType::Thinking | ModelType::Vision => {
                let parser = spec.tool_parser.clone().or_else(|| {
                    presets::default_tool_parser(self.kind, spec.model_family.as_deref())
                        .map(str::to_string)
                });
                if let Some(p) = parser.filter(|p| p != "none") {
                    f.set("--enable-auto-tool-choice", true)
                        .set("--tool-call-parser", p);
                }
                let reasoning = spec.reasoning_parser.clone().or_else(|| {
                    presets::default_reasoning_parser(self.kind, spec.model_family.as_deref())
                        .map(str::to_string)
                });
                if let Some(r) = reasoning.filter(|r| r != "none") {
                    f.set("--reasoning-parser", r);
                }
            }
            ModelType::Tts | ModelType::Image | ModelType::Video | ModelType::Stt
                if self.kind == EngineKind::VllmOmni => {}
            other => bail!("{} does not serve {other:?} models", self.kind),
        }
        if let Some(sc) = speculative_config(spec)? {
            f.set("--speculative-config", sc);
        }
        if let Some(kvc) = kv_transfer_config(spec) {
            f.set("--kv-transfer-config", kvc);
        }
        if let Some(p) = spec.preset {
            f.merge_defaults(&presets::preset_flags(self.kind, p));
        }
        f.merge_user(&spec.launch_flags);
        if let Some(n) = spec.max_concurrency {
            f.set("--max-num-seqs", n as u64);
        }
        let mut args = vec!["serve".to_string(), spec.model_path.clone()];
        args.extend(f.to_args());
        let kv_dtype = f.get_str("--kv-cache-dtype");
        let hints = Hints {
            served_model_name: Some(spec.served_model_name.clone()),
            context_size: f.get_u64("--max-model-len"),
            max_concurrency: f.get_u64("--max-num-seqs"),
            kv_unified: true,
            kv_bytes_per_token: kv::bytes_per_token(
                &spec.model_config,
                kv_dtype.as_deref(),
                kv_dtype.as_deref(),
            ),
            memory_fraction: f.get_f64("--gpu-memory-utilization"),
            router_mode: false,
            models_dir: None,
        };
        Ok((args, hints))
    }
}

/// `--gpu-memory-utilization` / `--mem-fraction-static` from the slice: slice VRAM ÷ device VRAM,
/// rounded down to 2 decimals (`None` = whole device, engine default).
pub fn memory_fraction(spec: &LaunchSpec) -> Option<f64> {
    let budget = spec.vram_budget_bytes()?;
    let total = spec.device_vram_total_bytes? * spec.devices.len() as u64;
    if total == 0 {
        return None;
    }
    let f = (budget as f64 / total as f64 * 100.0).floor() / 100.0;
    Some(f.clamp(0.05, 0.95))
}

/// vLLM `--speculative-config` JSON from `spec_decoding`.
pub fn speculative_config(spec: &LaunchSpec) -> anyhow::Result<Option<Value>> {
    let sd = &spec.spec_decoding;
    let Some(method) = sd.method.as_deref().filter(|m| *m != "none") else {
        return Ok(None);
    };
    let mut cfg = serde_json::Map::new();
    match method {
        "draft_model" => {
            cfg.insert(
                "model".into(),
                json!(sd
                    .draft_model_path
                    .clone()
                    .ok_or_else(|| anyhow::anyhow!("draft_model_path required"))?),
            );
        }
        "ngram" => {
            cfg.insert("method".into(), json!("ngram"));
            cfg.insert("prompt_lookup_max".into(), json!(4));
        }
        "eagle" | "eagle3" | "medusa" | "mtp" => {
            cfg.insert("method".into(), json!(method));
            if let Some(p) = &sd.draft_model_path {
                cfg.insert("model".into(), json!(p));
            }
        }
        other => bail!("unsupported spec_decoding method {other:?} for vLLM"),
    }
    cfg.insert(
        "num_speculative_tokens".into(),
        json!(sd.num_draft_tokens.unwrap_or(5)),
    );
    for (k, v) in &sd.extra {
        cfg.insert(k.clone(), v.clone());
    }
    Ok(Some(Value::Object(cfg)))
}

/// `--kv-transfer-config` for prefill/decode disaggregation (NIXL) or KV sharing (LMCache).
pub fn kv_transfer_config(spec: &LaunchSpec) -> Option<Value> {
    if spec.pd_split {
        let role = match spec.pd_role.as_deref() {
            Some("prefill") => "kv_producer",
            Some("decode") => "kv_consumer",
            _ => "kv_both",
        };
        let mut cfg = serde_json::Map::from_iter([
            ("kv_connector".to_string(), json!("NixlConnector")),
            ("kv_role".to_string(), json!(role)),
        ]);
        cfg.extend(spec.pd_config.clone());
        return Some(Value::Object(cfg));
    }
    if spec.kv_sharing {
        let mut cfg = serde_json::Map::from_iter([
            ("kv_connector".to_string(), json!("LMCacheConnectorV1")),
            ("kv_role".to_string(), json!("kv_both")),
        ]);
        cfg.extend(spec.kv_sharing_config.clone());
        return Some(Value::Object(cfg));
    }
    None
}

/// Map a `vllm:*` scrape into an `EngineSample`.
pub fn sample_from(
    snap: &Snapshot,
    hints: Option<&Hints>,
    prev: Option<&Prev>,
    now: Instant,
) -> (EngineSample, Prev) {
    let mut s = EngineSample::now();
    let mut next = PrevBuilder::default();
    s.active_sessions = snap.get("vllm:num_requests_running").map(|v| v as u64);
    s.waiting_sessions = snap.get("vllm:num_requests_waiting").map(|v| v as u64);
    s.requests_success_total = next
        .counter("succ", snap.sum("vllm:request_success_total"))
        .map(|v| v as u64);
    // Every finished request (all `finished_reason`s), including traffic that does not pass the
    // agent proxy (benchmarks hit the engine on loopback): the proxy's own count would stay 0
    // and requests/min with it.
    s.requests_total = s.requests_success_total;
    let prompt = next.counter("prompt", snap.sum("vllm:prompt_tokens_total"));
    let gen = next.counter("gen", snap.sum("vllm:generation_tokens_total"));
    s.prompt_tokens_total = prompt.map(|v| v as u64);
    s.generated_tokens_total = gen.map(|v| v as u64);
    s.prompt_tps = state::rate(prev, "prompt", prompt, now);
    s.generation_tps = state::rate(prev, "gen", gen, now);
    let hits = next.counter(
        "hits",
        snap.sum("vllm:prefix_cache_hits_total")
            .or_else(|| snap.sum("vllm:gpu_prefix_cache_hits_total")),
    );
    let queries = next.counter(
        "queries",
        snap.sum("vllm:prefix_cache_queries_total")
            .or_else(|| snap.sum("vllm:gpu_prefix_cache_queries_total")),
    );
    s.cached_tokens_total = hits.map(|v| v as u64);
    s.prefix_cache_hit_rate = delta_ratio(prev, "hits", hits, "queries", queries);
    let acc = next.counter(
        "spec_acc",
        snap.sum("vllm:spec_decode_num_accepted_tokens_total"),
    );
    let draft = next.counter(
        "spec_draft",
        snap.sum("vllm:spec_decode_num_draft_tokens_total"),
    );
    s.spec_accept_rate = delta_ratio(prev, "spec_acc", acc, "spec_draft", draft);

    (s.ttft_ms_avg, s.ttft_ms_p50, s.ttft_ms_p95) =
        hist_ms(snap, prev, &mut next, &["vllm:time_to_first_token_seconds"]);
    (s.e2e_ms_avg, s.e2e_ms_p50, s.e2e_ms_p95) =
        hist_ms(snap, prev, &mut next, &["vllm:e2e_request_latency_seconds"]);
    s.tpot_ms_avg = hist_ms(
        snap,
        prev,
        &mut next,
        &[
            "vllm:inter_token_latency_seconds",
            "vllm:time_per_output_token_seconds",
            "vllm:request_time_per_output_token_seconds",
        ],
    )
    .0;
    s.queue_ms_avg = hist_ms(snap, prev, &mut next, &["vllm:request_queue_time_seconds"]).0;

    let usage = snap.first_of(&["vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"]);
    s.kv_cache_usage_pct = usage.map(|u| u * 100.0);
    if let Some(info) = snap.labels_of("vllm:cache_config_info") {
        let blocks = info
            .get("num_gpu_blocks")
            .and_then(|v| v.parse::<u64>().ok());
        let bs = info.get("block_size").and_then(|v| v.parse::<u64>().ok());
        if let (Some(b), Some(sz)) = (blocks, bs) {
            s.kv_cache_total_tokens = Some(b * sz);
        }
    }
    if let (Some(t), Some(u)) = (s.kv_cache_total_tokens, usage) {
        s.kv_cache_used_tokens = Some((t as f64 * u).round() as u64);
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
    if let Some(p) = snap.sum("vllm:num_preemptions_total") {
        s.extra_insert("num_preemptions_total", json!(p));
    }
    // vLLM-Omni: async job queue depth and anything under its own prefix.
    for sample in &snap.samples {
        if sample.name.starts_with("vllm_omni:") || sample.name.starts_with("vllm:omni_") {
            s.extra_insert(&sample.name, json!(sample.value));
        }
    }
    (s, next.build(now))
}

#[async_trait]
impl EngineAdapter for VllmAdapter {
    fn kind(&self) -> EngineKind {
        self.kind
    }

    fn command(&self, spec: &LaunchSpec) -> anyhow::Result<ProcessSpec> {
        let (args, hints) = self.build_args(spec)?;
        state::register(&spec.base_url(), hints);
        let program = spec.executable.clone().unwrap_or_else(|| "vllm".into());
        let image = match (self.kind, spec.vendor()) {
            (EngineKind::Vllm, Some(Vendor::Amd)) => Some("rocm/vllm:latest"),
            (EngineKind::Vllm, _) => Some("vllm/vllm-openai:latest"),
            _ => None,
        };
        finish(spec, program, args, base_env(spec), image)
    }

    async fn health(&self, base_url: &str) -> HealthStatus {
        http::probe(base_url, &self.health_config()).await
    }

    async fn scrape(&self, base_url: &str) -> anyhow::Result<EngineSample> {
        let (base, _) = split_model(base_url);
        let text = http::get_ok(&url(&base, "/metrics", None)).await?;
        let snap = prometheus::parse(&text);
        let now = Instant::now();
        let hints = state::hints(&base);
        let prev = state::peek_prev(&base);
        let (mut s, next) = sample_from(&snap, hints.as_ref(), prev.as_ref(), now);
        state::swap_prev(&base, next);
        s.health = Some(InstanceHealth::Healthy);
        Ok(s)
    }

    fn supports_hot_swap(&self) -> bool {
        false
    }

    async fn load_model(&self, _base_url: &str, model: &ModelRef) -> anyhow::Result<()> {
        bail!(
            "{} cannot load models into a running process ({}); start a new instance",
            self.kind,
            model.name
        )
    }

    async fn unload_model(&self, _base_url: &str, model: &str) -> anyhow::Result<()> {
        bail!(
            "{} cannot unload models from a running process ({model}); stop the instance",
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
        MetricsConfig {
            metric_prefix: Some("vllm:".into()),
            ..MetricsConfig::default()
        }
    }

    fn allowed_path_prefixes(&self) -> Vec<&'static str> {
        match self.kind {
            EngineKind::VllmOmni => vec!["/v1/"],
            _ => vec!["/v1/", "/pooling", "/score", "/rerank", "/classify"],
        }
    }

    fn admin_paths(&self) -> Vec<&'static str> {
        vec![
            "/metrics",
            "/health",
            "/load",
            "/ping",
            "/server_info",
            "/reset_prefix_cache",
            "/sleep",
            "/wake_up",
            "/collective_rpc",
            "/start_profile",
            "/stop_profile",
        ]
    }
}
