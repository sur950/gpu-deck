//! MLX (`mlx_lm.server`, verified with mlx-lm 0.29.1 / mlx 0.30.6 on this Mac).
//!
//! Command: `mlx_lm.server --model <dir> --host 127.0.0.1 --port P [--trust-remote-code]
//! [--draft-model D --num-draft-tokens N] [--chat-template-args JSON] [--max-tokens N]`.
//!
//! Findings:
//! * the model given by `--model` is addressed as **`default_model`** in request bodies; any other
//!   `model` value makes the server try to load that name from the Hugging Face cache. The proxy
//!   must rewrite `model` to [`ENGINE_MODEL_NAME`] (`EngineAdapter::upstream_model_name`).
//!   `GET /v1/models` lists the HF cache only (empty for a local path).
//! * `GET /health` → `{"status": "ok"}`; there is no `/metrics` (404) — every metric is derived
//!   by the agent proxy (`derive_from_proxy = true`, `metrics.md` §4): tokens from `usage` (the
//!   final SSE chunk carries `usage` when `stream_options.include_usage` is set), TTFT from the
//!   first content chunk, TPOT = (e2e − ttft)/output tokens, KV ≈ (prompt + generated) × bytes per
//!   token, slots = `max_concurrency`.
//! * 0.29 batches concurrent requests (`BatchGenerator`), so `active_sessions` can exceed 1.
//! * Known engine bug (0.29.1 + Gemma 3 sliding-window cache): the second request that reuses the
//!   prompt cache can crash the generation thread (`BatchRotatingKVCache.merge` broadcast error)
//!   and leave later requests hanging; health stays `ok`. The proxy's request timeout surfaces it
//!   as errors; restart policy recovers the instance.

use async_trait::async_trait;
use serde_json::json;

use crate::flags::Flags;
use crate::http::{self, split_model, url};
use crate::kv;
use crate::launch::{base_env, finish};
use crate::state::{self, Hints};
use crate::types::*;

/// Name `mlx_lm.server` answers to for its `--model`.
pub const ENGINE_MODEL_NAME: &str = "default_model";

#[derive(Debug, Default, Clone)]
pub struct MlxAdapter;

impl MlxAdapter {
    pub fn new() -> Self {
        MlxAdapter
    }

    pub fn build_args(spec: &LaunchSpec) -> anyhow::Result<Vec<String>> {
        if let Some(v) = spec.vendor() {
            if v != Vendor::Apple {
                anyhow::bail!("MLX runs on Apple Silicon only (got {v})");
            }
        }
        if spec.runtime == Runtime::Container {
            anyhow::bail!("MLX cannot run in a container (no Metal in Linux VMs)");
        }
        if !matches!(
            spec.model_type,
            ModelType::Chat | ModelType::Thinking | ModelType::Vision
        ) {
            anyhow::bail!(
                "mlx_lm.server serves chat/thinking models, not {:?} (use mlx_audio for speech)",
                spec.model_type
            );
        }
        if spec.tp_size > 1 || spec.pp_size > 1 || spec.pd_split {
            anyhow::bail!("mlx_lm.server is single-device (no tp/pp/pd split)");
        }
        let mut f = Flags::new(&[]);
        f.set("--model", spec.model_path.clone())
            .set("--host", spec.host.clone())
            .set("--port", spec.port as u64);
        if spec.trust_remote_code {
            f.set("--trust-remote-code", true);
        }
        match spec.spec_decoding.method.as_deref() {
            None | Some("none") => {}
            Some("draft_model") => {
                let p =
                    spec.spec_decoding.draft_model_path.clone().ok_or_else(|| {
                        anyhow::anyhow!("spec_decoding.draft_model_path is required")
                    })?;
                f.set("--draft-model", p);
                if let Some(n) = spec.spec_decoding.num_draft_tokens {
                    f.set("--num-draft-tokens", n as u64);
                }
            }
            Some(other) => anyhow::bail!(
                "mlx_lm.server supports spec_decoding draft_model only, not {other:?}"
            ),
        }
        f.merge_user(&spec.launch_flags);
        Ok(f.to_args())
    }
}

/// Sample for engines without metrics: health only; the agent merges `ProxyStats`.
pub(crate) async fn proxy_only_sample(
    adapter: &dyn EngineAdapter,
    base_url: &str,
) -> anyhow::Result<EngineSample> {
    let h = adapter.health(base_url).await;
    if let HealthStatus::Unhealthy { reason } = &h {
        if reason.contains("connection refused") {
            anyhow::bail!("{} not reachable: {reason}", adapter.kind());
        }
    }
    let mut s = EngineSample::now();
    s.health = Some(h.instance_health());
    if let Some(hints) = state::hints(base_url) {
        if let Some(c) = hints.max_concurrency {
            s.slots_total = Some(c);
        }
        s.extra_insert("derived_from_proxy", json!(true));
    }
    Ok(s)
}

#[async_trait]
impl EngineAdapter for MlxAdapter {
    fn kind(&self) -> EngineKind {
        EngineKind::Mlx
    }

    fn command(&self, spec: &LaunchSpec) -> anyhow::Result<ProcessSpec> {
        let args = Self::build_args(spec)?;
        state::register(
            &spec.base_url(),
            Hints {
                served_model_name: Some(ENGINE_MODEL_NAME.into()),
                context_size: spec.context_size.map(u64::from),
                max_concurrency: spec.max_concurrency.map(u64::from),
                kv_unified: true,
                kv_bytes_per_token: kv::bytes_per_token(&spec.model_config, None, None),
                memory_fraction: None,
                router_mode: false,
                models_dir: None,
            },
        );
        let program = spec
            .executable
            .clone()
            .unwrap_or_else(|| "mlx_lm.server".into());
        finish(spec, program, args, base_env(spec), None)
    }

    async fn health(&self, base_url: &str) -> HealthStatus {
        let (base, _) = split_model(base_url);
        match http::get_text(&url(&base, "/health", None)).await {
            Ok((200, _)) => HealthStatus::Healthy,
            // mlx-lm < 0.22 has no /health: fall back to /v1/models.
            Ok((404, _)) => match http::get_text(&url(&base, "/v1/models", None)).await {
                Ok((200, _)) => HealthStatus::Healthy,
                Ok((code, _)) => HealthStatus::unhealthy(format!("GET /v1/models -> {code}")),
                Err(e) => HealthStatus::unhealthy(e.to_string()),
            },
            Ok((503, _)) => HealthStatus::Loading,
            Ok((code, _)) => HealthStatus::unhealthy(format!("GET /health -> {code}")),
            Err(e) => HealthStatus::unhealthy(format!("GET /health: {}", root_cause(&e))),
        }
    }

    async fn scrape(&self, base_url: &str) -> anyhow::Result<EngineSample> {
        proxy_only_sample(self, base_url).await
    }

    fn supports_hot_swap(&self) -> bool {
        false
    }

    async fn load_model(&self, _base_url: &str, model: &ModelRef) -> anyhow::Result<()> {
        anyhow::bail!(
            "mlx_lm.server serves one model per process ({}); start a new instance",
            model.name
        )
    }

    async fn unload_model(&self, _base_url: &str, model: &str) -> anyhow::Result<()> {
        anyhow::bail!("mlx_lm.server cannot unload its model ({model}); stop the instance")
    }

    fn health_config(&self) -> HealthConfig {
        HealthConfig {
            startup_timeout_seconds: 300.0,
            ..HealthConfig::default()
        }
    }

    fn metrics_config(&self) -> MetricsConfig {
        MetricsConfig {
            path: None,
            format: "none".into(),
            slots_path: None,
            scrape_interval_seconds: 2.0,
            derive_from_proxy: true,
            metric_prefix: None,
        }
    }

    fn upstream_model_name(&self, _spec: &LaunchSpec) -> String {
        ENGINE_MODEL_NAME.into()
    }

    fn admin_paths(&self) -> Vec<&'static str> {
        vec!["/health"]
    }
}

/// "connection refused" etc. from an anyhow chain.
pub(crate) fn root_cause(e: &anyhow::Error) -> String {
    let s = format!("{e:#}");
    if s.contains("onnection refused")
        || s.contains("error sending request") && s.contains("connect")
    {
        "connection refused".into()
    } else {
        s
    }
}
