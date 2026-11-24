//! GPU Deck node agent — engine adapters.
//!
//! One adapter per engine family (`EngineKind`). Each builds the launch command from a
//! `LaunchSpec`, probes health, scrapes `/metrics` (+ `/slots`) into the `metrics.md` catalog and,
//! where the engine supports it, loads/unloads models in a running process (llama.cpp router mode).
//!
//! | Engine | Metrics source | Hot swap | Verified on this Mac |
//! | --- | --- | --- | --- |
//! | llama.cpp (b8798) | `/metrics` Prometheus + `/slots` JSON, `?model=` in router mode (`llama_cpp::instance_url`) | router `POST /models/load` / `/models/unload` | real |
//! | MLX (`mlx_lm.server` 0.29.1, model addressed as `default_model`) | proxy-derived (`ProxyStats`) | no | real |
//! | mlx-audio | proxy-derived | no | fixture-only (documented format) |
//! | vLLM / vLLM-Omni | `/metrics` Prometheus `vllm:*` | no | fixture-only |
//! | SGLang / SGLang Diffusion | `/metrics` Prometheus `sglang:*` | no | fixture-only |
//! | TensorRT-LLM (`trtllm-serve`) | `/metrics` JSON iteration stats (or Prometheus) | no | fixture-only |
//! | whisper.cpp server | proxy-derived | no | fixture-only |
//! | stable-diffusion.cpp server | proxy-derived | no | fixture-only |
//! | custom | per `CustomEngineConfig` (Prometheus with `metric_map`, JSON, or proxy) | optional | fixture-only |
//!
//! Contract for the agent:
//! * call `command(spec)` once per process; it also registers per-base-URL scrape hints
//!   (context, slots, KV bytes/token) used by `scrape`. Call [`forget`] when the process stops;
//! * router-mode llama.cpp instances are addressed as `base?model=<name>` for `health`/`scrape`;
//! * for engines with `derive_from_proxy()`, merge the proxy counters with
//!   `apply_proxy_stats(&mut sample, &stats)` after `scrape`;
//! * the proxy rewrites the request `model` to `upstream_model_name(spec)` and forwards only
//!   `allowed_path_prefixes()`, never `admin_paths()`.

pub mod custom;
pub mod flags;
pub mod http;
pub mod kv;
pub mod launch;
pub mod llama_cpp;
pub mod mlx;
pub mod mlx_audio;
pub mod observe;
pub mod presets;
pub mod prometheus;
pub mod proxy;
pub mod sd_cpp;
pub mod sglang;
pub mod state;
pub mod tensorrt_llm;
pub mod types;
pub mod vllm;
pub mod whisper_cpp;
pub mod window;

pub use proxy::{InflightRequest, ProxyStats, RequestRecord};
pub use state::forget;
pub use types::*;

/// The adapter for an engine kind. Adapters are cheap to create; the agent may keep one per
/// kind (they hold only scrape state keyed by base URL).
pub fn adapter_for(kind: EngineKind) -> Box<dyn EngineAdapter> {
    match kind {
        EngineKind::LlamaCpp => Box::new(llama_cpp::LlamaCppAdapter::new()),
        EngineKind::Vllm => Box::new(vllm::VllmAdapter::new(EngineKind::Vllm)),
        EngineKind::VllmOmni => Box::new(vllm::VllmAdapter::new(EngineKind::VllmOmni)),
        EngineKind::Sglang => Box::new(sglang::SglangAdapter::new(EngineKind::Sglang)),
        EngineKind::SglangDiffusion => {
            Box::new(sglang::SglangAdapter::new(EngineKind::SglangDiffusion))
        }
        EngineKind::TensorrtLlm => Box::new(tensorrt_llm::TensorrtLlmAdapter::new()),
        EngineKind::Mlx => Box::new(mlx::MlxAdapter::new()),
        EngineKind::MlxAudio => Box::new(mlx_audio::MlxAudioAdapter::new()),
        EngineKind::WhisperCpp => Box::new(whisper_cpp::WhisperCppAdapter::new()),
        EngineKind::StableDiffusionCpp => Box::new(sd_cpp::SdCppAdapter::new()),
        EngineKind::Custom => Box::new(custom::CustomAdapter::new()),
    }
}

/// All adapters, in `EngineKind::ALL` order.
pub fn all_adapters() -> Vec<Box<dyn EngineAdapter>> {
    EngineKind::ALL.iter().map(|k| adapter_for(*k)).collect()
}
