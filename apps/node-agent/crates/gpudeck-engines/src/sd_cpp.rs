//! stable-diffusion.cpp server (`sd-server`, image generation).
//!
//! Command: `sd-server -m <model> --listen-ip 127.0.0.1 --listen-port P [flags]` (FLUX-style
//! split weights via `launch_flags`: `diffusion-model`, `vae`, `clip_l`, `t5xxl`). Serves the
//! OpenAI `/v1/images/generations` (and `/v1/images/edits` on newer builds) plus the A1111-style
//! `/sdapi/v1/*`. Health `GET /`. No metrics → proxy-derived (`media_units` = images).
//! Status: fixture-only; flag names unverified until the server is built here.

use async_trait::async_trait;

use crate::flags::Flags;
use crate::http;
use crate::launch::{add_library_path, base_env, bind_host, finish};
use crate::mlx::proxy_only_sample;
use crate::state::{self, Hints};
use crate::types::*;

#[derive(Debug, Default, Clone)]
pub struct SdCppAdapter;

impl SdCppAdapter {
    pub fn new() -> Self {
        SdCppAdapter
    }
}

#[async_trait]
impl EngineAdapter for SdCppAdapter {
    fn kind(&self) -> EngineKind {
        EngineKind::StableDiffusionCpp
    }

    fn command(&self, spec: &LaunchSpec) -> anyhow::Result<ProcessSpec> {
        if spec.model_type != ModelType::Image {
            anyhow::bail!(
                "stable-diffusion.cpp serves image models, not {:?}",
                spec.model_type
            );
        }
        let mut f = Flags::new(&[&["--model", "-m"], &["--threads", "-t"]]);
        let split_weights = spec
            .launch_flags
            .keys()
            .any(|k| k.trim_start_matches('-') == "diffusion-model");
        if !split_weights {
            f.set("--model", spec.model_path.clone());
        }
        f.set("--listen-ip", bind_host(spec))
            .set("--listen-port", spec.port as u64);
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
        add_library_path(&mut env, spec, "libstable-diffusion");
        let program = spec
            .executable
            .clone()
            .unwrap_or_else(|| "sd-server".into());
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

    async fn load_model(&self, _base_url: &str, model: &ModelRef) -> anyhow::Result<()> {
        anyhow::bail!("sd-server serves one model per process ({})", model.name)
    }

    async fn unload_model(&self, _base_url: &str, model: &str) -> anyhow::Result<()> {
        anyhow::bail!("sd-server cannot unload its model ({model})")
    }

    fn health_config(&self) -> HealthConfig {
        HealthConfig {
            path: "/".into(),
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

    fn allowed_path_prefixes(&self) -> Vec<&'static str> {
        vec!["/v1/images/", "/sdapi/v1/txt2img", "/sdapi/v1/img2img"]
    }

    fn admin_paths(&self) -> Vec<&'static str> {
        vec!["/sdapi/v1/options", "/sdapi/v1/refresh-checkpoints"]
    }
}
