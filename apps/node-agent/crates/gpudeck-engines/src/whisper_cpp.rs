//! whisper.cpp `whisper-server` (speech to text).
//!
//! Command: `whisper-server -m <ggml model> --host 127.0.0.1 --port P --inference-path
//! /v1/audio/transcriptions [-t N] [-fa]`, so the OpenAI transcription path maps 1:1 onto the
//! server's inference endpoint (multipart `file`, `response_format`, `language`...). Health:
//! `GET /` (the bundled page answers 200 once the model is loaded, `metrics.md` §2). No metrics
//! → proxy-derived (`media_units` = audio seconds). `POST /load` (model replacement) is an
//! admin path the proxy never forwards; `load_model` uses it to swap the single model.
//! Status: fixture-only (whisper-server is not built on this Mac).

use async_trait::async_trait;

use crate::flags::Flags;
use crate::http::{self, split_model, url};
use crate::launch::{add_library_path, base_env, bind_host, finish};
use crate::mlx::proxy_only_sample;
use crate::presets;
use crate::state::{self, Hints};
use crate::types::*;

pub const INFERENCE_PATH: &str = "/v1/audio/transcriptions";

#[derive(Debug, Default, Clone)]
pub struct WhisperCppAdapter;

impl WhisperCppAdapter {
    pub fn new() -> Self {
        WhisperCppAdapter
    }
}

#[async_trait]
impl EngineAdapter for WhisperCppAdapter {
    fn kind(&self) -> EngineKind {
        EngineKind::WhisperCpp
    }

    fn command(&self, spec: &LaunchSpec) -> anyhow::Result<ProcessSpec> {
        if spec.model_type != ModelType::Stt {
            anyhow::bail!("whisper.cpp serves stt models, not {:?}", spec.model_type);
        }
        let mut f = Flags::new(&[
            &["--model", "-m"],
            &["--threads", "-t"],
            &["--flash-attn", "-fa"],
            &["--processors", "-p"],
            &["--beam-size", "-bs"],
        ]);
        f.set("--model", spec.model_path.clone())
            .set("--host", bind_host(spec))
            .set("--port", spec.port as u64);
        f.set("--inference-path", INFERENCE_PATH);
        if spec.devices.is_empty() {
            f.set("--no-gpu", true);
        }
        if let Some(p) = spec.preset {
            f.merge_defaults(&presets::preset_flags(EngineKind::WhisperCpp, p));
        }
        f.merge_user(&spec.launch_flags);
        state::register(
            &spec.base_url(),
            Hints {
                served_model_name: Some(spec.served_model_name.clone()),
                max_concurrency: Some(1),
                ..Hints::default()
            },
        );
        let mut env = base_env(spec);
        add_library_path(&mut env, spec, "libwhisper");
        let program = spec
            .executable
            .clone()
            .unwrap_or_else(|| "whisper-server".into());
        finish(spec, program, f.to_args(), env, None)
    }

    async fn health(&self, base_url: &str) -> HealthStatus {
        http::probe(base_url, &self.health_config()).await
    }

    async fn scrape(&self, base_url: &str) -> anyhow::Result<EngineSample> {
        proxy_only_sample(self, base_url).await
    }

    fn supports_hot_swap(&self) -> bool {
        false
    }

    /// Replace the served model in place (`POST /load`, multipart `model=<path>`).
    async fn load_model(&self, base_url: &str, model: &ModelRef) -> anyhow::Result<()> {
        let (base, _) = split_model(base_url);
        let path = model
            .path
            .clone()
            .ok_or_else(|| anyhow::anyhow!("whisper.cpp /load needs the model path"))?;
        let form = reqwest::multipart::Form::new().text("model", path);
        let resp = http::client()
            .post(url(&base, "/load", None))
            .multipart(form)
            .timeout(std::time::Duration::from_secs(300))
            .send()
            .await?;
        if !resp.status().is_success() {
            anyhow::bail!("whisper.cpp /load: {}", resp.status());
        }
        Ok(())
    }

    async fn unload_model(&self, _base_url: &str, model: &str) -> anyhow::Result<()> {
        anyhow::bail!("whisper-server cannot unload its model ({model}); stop the instance")
    }

    fn health_config(&self) -> HealthConfig {
        HealthConfig {
            path: "/".into(),
            startup_timeout_seconds: 120.0,
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

    fn allowed_path_prefixes(&self) -> Vec<&'static str> {
        vec![INFERENCE_PATH]
    }

    fn admin_paths(&self) -> Vec<&'static str> {
        vec!["/load", "/inference"]
    }
}
