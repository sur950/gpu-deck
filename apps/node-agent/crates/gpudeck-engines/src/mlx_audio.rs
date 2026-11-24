//! mlx-audio (`mlx_audio.server`), Apple Silicon only: OpenAI-style `/v1/audio/speech` (TTS:
//! Kokoro, ...) and `/v1/audio/transcriptions` (STT: Whisper, Parakeet...). Models are named per
//! request (HF repo id or local path) and loaded on first use, so the upstream model name is the
//! model path. No metrics endpoint → proxy-derived (`media_units` from audio duration / input
//! characters, `metrics.md` §4). Status: fixture-only (mlx-audio is not installed on this Mac).

use async_trait::async_trait;

use crate::flags::Flags;
use crate::http::{self, split_model, url};
use crate::launch::{base_env, finish};
use crate::mlx::proxy_only_sample;
use crate::state::{self, Hints};
use crate::types::*;

#[derive(Debug, Default, Clone)]
pub struct MlxAudioAdapter;

impl MlxAudioAdapter {
    pub fn new() -> Self {
        MlxAudioAdapter
    }
}

#[async_trait]
impl EngineAdapter for MlxAudioAdapter {
    fn kind(&self) -> EngineKind {
        EngineKind::MlxAudio
    }

    fn command(&self, spec: &LaunchSpec) -> anyhow::Result<ProcessSpec> {
        if let Some(v) = spec.vendor() {
            if v != Vendor::Apple {
                anyhow::bail!("mlx-audio runs on Apple Silicon only (got {v})");
            }
        }
        if spec.runtime == Runtime::Container {
            anyhow::bail!("mlx-audio cannot run in a container");
        }
        if !matches!(spec.model_type, ModelType::Tts | ModelType::Stt) {
            anyhow::bail!("mlx-audio serves tts/stt models, not {:?}", spec.model_type);
        }
        let mut f = Flags::new(&[]);
        f.set("--host", spec.host.clone())
            .set("--port", spec.port as u64);
        f.merge_user(&spec.launch_flags);
        state::register(
            &spec.base_url(),
            Hints {
                served_model_name: Some(spec.model_path.clone()),
                max_concurrency: spec.max_concurrency.map(u64::from),
                ..Hints::default()
            },
        );
        let program = spec
            .executable
            .clone()
            .unwrap_or_else(|| "mlx_audio.server".into());
        finish(spec, program, f.to_args(), base_env(spec), None)
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

    /// Warm the model so the first request does not pay the load (`POST /v1/models?model_name=`).
    async fn load_model(&self, base_url: &str, model: &ModelRef) -> anyhow::Result<()> {
        let (base, _) = split_model(base_url);
        let name = model.path.clone().unwrap_or_else(|| model.name.clone());
        let u = format!(
            "{}?model_name={}",
            url(&base, "/v1/models", None),
            http::percent_encode(&name)
        );
        let (code, body) = http::post_json(
            &u,
            &serde_json::json!({}),
            std::time::Duration::from_secs(600),
        )
        .await?;
        if !(200..300).contains(&code) {
            anyhow::bail!(
                "mlx-audio preload {name}: {code} {}",
                http::truncate(&body, 300)
            );
        }
        Ok(())
    }

    async fn unload_model(&self, base_url: &str, model: &str) -> anyhow::Result<()> {
        let (base, _) = split_model(base_url);
        let u = format!(
            "{}?model_name={}",
            url(&base, "/v1/models", None),
            http::percent_encode(model)
        );
        let resp = http::client()
            .delete(&u)
            .timeout(http::SCRAPE_TIMEOUT)
            .send()
            .await?;
        if !resp.status().is_success() && resp.status().as_u16() != 404 {
            anyhow::bail!("mlx-audio unload {model}: {}", resp.status());
        }
        Ok(())
    }

    fn health_config(&self) -> HealthConfig {
        HealthConfig {
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

    fn upstream_model_name(&self, spec: &LaunchSpec) -> String {
        spec.model_path.clone()
    }

    fn allowed_path_prefixes(&self) -> Vec<&'static str> {
        vec!["/v1/audio/"]
    }

    fn admin_paths(&self) -> Vec<&'static str> {
        vec!["/health", "/v1/models"]
    }
}
