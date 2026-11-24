//! Engine adapter types: what the agent gives an adapter (`LaunchSpec`), what it gets back
//! (`ProcessSpec`, `HealthStatus`, `EngineSample` + `SlotSample`), and the `EngineAdapter` trait.
//!
//! `EngineSample` / `SlotSample` mirror `common.json#/$defs/EngineSample` and `SessionSample`
//! minus the ids the agent adds (`instance_id`, `deployment_id`); every metric the engine does
//! not expose is `None` → `null`, per `metrics.md`.

use std::collections::BTreeMap;

use async_trait::async_trait;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

pub use gpudeck_devices::{AssignedDevice, Slice, Vendor};

/// Engine family — values match `common.json#/$defs/engine_name`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineKind {
    LlamaCpp,
    Vllm,
    VllmOmni,
    Sglang,
    SglangDiffusion,
    TensorrtLlm,
    Mlx,
    MlxAudio,
    WhisperCpp,
    StableDiffusionCpp,
    Custom,
}

impl EngineKind {
    pub const ALL: [EngineKind; 11] = [
        EngineKind::LlamaCpp,
        EngineKind::Vllm,
        EngineKind::VllmOmni,
        EngineKind::Sglang,
        EngineKind::SglangDiffusion,
        EngineKind::TensorrtLlm,
        EngineKind::Mlx,
        EngineKind::MlxAudio,
        EngineKind::WhisperCpp,
        EngineKind::StableDiffusionCpp,
        EngineKind::Custom,
    ];

    pub fn as_str(&self) -> &'static str {
        match self {
            EngineKind::LlamaCpp => "llama_cpp",
            EngineKind::Vllm => "vllm",
            EngineKind::VllmOmni => "vllm_omni",
            EngineKind::Sglang => "sglang",
            EngineKind::SglangDiffusion => "sglang_diffusion",
            EngineKind::TensorrtLlm => "tensorrt_llm",
            EngineKind::Mlx => "mlx",
            EngineKind::MlxAudio => "mlx_audio",
            EngineKind::WhisperCpp => "whisper_cpp",
            EngineKind::StableDiffusionCpp => "stable_diffusion_cpp",
            EngineKind::Custom => "custom",
        }
    }
}

impl std::fmt::Display for EngineKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

impl std::str::FromStr for EngineKind {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s
            .trim()
            .to_ascii_lowercase()
            .replace(['-', '.'], "_")
            .as_str()
        {
            "llama_cpp" | "llamacpp" | "llama" => Ok(EngineKind::LlamaCpp),
            "vllm" => Ok(EngineKind::Vllm),
            "vllm_omni" => Ok(EngineKind::VllmOmni),
            "sglang" => Ok(EngineKind::Sglang),
            "sglang_diffusion" => Ok(EngineKind::SglangDiffusion),
            "tensorrt_llm" | "trtllm" | "tensorrt" => Ok(EngineKind::TensorrtLlm),
            "mlx" | "mlx_lm" => Ok(EngineKind::Mlx),
            "mlx_audio" => Ok(EngineKind::MlxAudio),
            "whisper_cpp" | "whisper" => Ok(EngineKind::WhisperCpp),
            "stable_diffusion_cpp" | "sd_cpp" | "sdcpp" => Ok(EngineKind::StableDiffusionCpp),
            "custom" => Ok(EngineKind::Custom),
            other => Err(format!("unknown engine {other:?}")),
        }
    }
}

/// `common.json#/$defs/model_type`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum ModelType {
    #[default]
    Chat,
    Thinking,
    Vision,
    Embedding,
    Rerank,
    Stt,
    Tts,
    Image,
    Video,
}

/// `LaunchSpec.preset` in the protocol.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Preset {
    LowLatency,
    HighThroughput,
}

/// `common.json#/$defs/engine_runtime`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum Runtime {
    #[default]
    Native,
    Container,
}

/// `common.json#/$defs/instance_health`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InstanceHealth {
    Healthy,
    Unhealthy,
    Unknown,
}

/// `SessionSample.state`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionState {
    Idle,
    Waiting,
    Processing,
    Generating,
}

/// llama.cpp control vector (`LaunchSpec.control_vectors[]`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ControlVector {
    pub path: String,
    #[serde(default = "default_scale")]
    pub scale: f64,
    /// `[start, end]` layer range (`--control-vector-layer-range`).
    #[serde(default)]
    pub layer_range: Option<(u32, u32)>,
}

fn default_scale() -> f64 {
    1.0
}

/// Speculative decoding settings (`LaunchSpec.spec_decoding` in the protocol, free-form JSON).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct SpecDecoding {
    /// `draft_model`, `ngram`, `eagle`, `eagle3`, `mtp`, `medusa`; `None` = off.
    #[serde(default)]
    pub method: Option<String>,
    /// Local path of the draft model files (resolved by the agent from `draft_model`).
    #[serde(default)]
    pub draft_model_path: Option<String>,
    #[serde(default)]
    pub num_draft_tokens: Option<u32>,
    #[serde(default)]
    pub extra: serde_json::Map<String, serde_json::Value>,
}

/// Health probe settings — `common.json#/$defs/HealthConfig`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HealthConfig {
    #[serde(default = "default_health_path")]
    pub path: String,
    #[serde(default = "default_get")]
    pub method: String,
    #[serde(default = "default_interval")]
    pub interval_seconds: f64,
    #[serde(default = "default_timeout")]
    pub timeout_seconds: f64,
    #[serde(default = "default_statuses")]
    pub healthy_statuses: Vec<u16>,
    #[serde(default = "default_startup")]
    pub startup_timeout_seconds: f64,
    #[serde(default = "default_failures")]
    pub failure_threshold: u32,
}

fn default_health_path() -> String {
    "/health".into()
}
fn default_get() -> String {
    "GET".into()
}
fn default_interval() -> f64 {
    5.0
}
fn default_timeout() -> f64 {
    3.0
}
fn default_statuses() -> Vec<u16> {
    vec![200]
}
fn default_startup() -> f64 {
    600.0
}
fn default_failures() -> u32 {
    3
}

impl Default for HealthConfig {
    fn default() -> Self {
        HealthConfig {
            path: default_health_path(),
            method: default_get(),
            interval_seconds: default_interval(),
            timeout_seconds: default_timeout(),
            healthy_statuses: default_statuses(),
            startup_timeout_seconds: default_startup(),
            failure_threshold: default_failures(),
        }
    }
}

/// Metrics scrape settings — `common.json#/$defs/MetricsConfig`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MetricsConfig {
    /// Prometheus/JSON endpoint; `None` when the engine has none.
    pub path: Option<String>,
    /// `prometheus`, `json` or `none`.
    #[serde(default = "default_format")]
    pub format: String,
    /// llama.cpp `/slots` or equivalent per-session endpoint.
    #[serde(default)]
    pub slots_path: Option<String>,
    #[serde(default = "default_scrape")]
    pub scrape_interval_seconds: f64,
    /// True = request/latency/token metrics come from the agent proxy (`ProxyStats`).
    #[serde(default)]
    pub derive_from_proxy: bool,
    #[serde(default)]
    pub metric_prefix: Option<String>,
}

fn default_format() -> String {
    "prometheus".into()
}
fn default_scrape() -> f64 {
    2.0
}

impl Default for MetricsConfig {
    fn default() -> Self {
        MetricsConfig {
            path: Some("/metrics".into()),
            format: default_format(),
            slots_path: None,
            scrape_interval_seconds: default_scrape(),
            derive_from_proxy: false,
            metric_prefix: None,
        }
    }
}

/// Container-specific settings when `runtime == Container` (also used by `custom` engines).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ContainerConfig {
    pub image: String,
    /// `docker` (default) or `podman`.
    #[serde(default)]
    pub runtime: Option<String>,
    /// Extra `docker run` args (e.g. `--ipc=host`).
    #[serde(default)]
    pub run_args: Vec<String>,
    /// Extra host→container bind mounts (`host:container[:ro]`).
    #[serde(default)]
    pub volumes: Vec<String>,
    /// Override the image entrypoint/command (`{{var}}` placeholders allowed).
    #[serde(default)]
    pub command: Vec<String>,
    #[serde(default)]
    pub shm_size: Option<String>,
}

/// `custom` engine description: container image + how to start, probe and scrape it
/// (`engine_versions.launch_template/health_config/metrics_config` for `engines.name = custom`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct CustomEngineConfig {
    /// argv with `{{port}}`, `{{model_path}}`, `{{model_dir}}`, `{{context_size}}`,
    /// `{{served_model_name}}`, `{{tp_size}}`, `{{pp_size}}`, `{{device_indices}}`,
    /// `{{cache_dir}}`, `{{host}}`, `{{flags}}` placeholders.
    pub command: Vec<String>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    #[serde(default)]
    pub working_dir: Option<String>,
    #[serde(default)]
    pub health: HealthConfig,
    #[serde(default)]
    pub metrics: MetricsConfig,
    /// Map engine metric names → canonical names (`metrics.md`) for Prometheus/JSON scrapes.
    #[serde(default)]
    pub metric_map: BTreeMap<String, String>,
    /// Path prefixes the proxy may forward (`/v1/` by default).
    #[serde(default)]
    pub allowed_path_prefixes: Vec<String>,
    #[serde(default)]
    pub supports_hot_swap: bool,
    /// Hot-swap admin calls when `supports_hot_swap` (`POST <load_path> {"model": name, "path": p}`).
    #[serde(default)]
    pub load_path: Option<String>,
    #[serde(default)]
    pub unload_path: Option<String>,
}

/// Everything an adapter needs to build a launch command. Field names follow the protocol's
/// `LoadModelArgs` / `LaunchSpec` / `ModelSpec` / `EngineSpec`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LaunchSpec {
    pub engine: EngineKind,
    /// Model id the engine answers to (`--alias` / `--served-model-name`).
    pub served_model_name: String,
    /// Primary model file (GGUF) or directory (safetensors / MLX snapshot).
    pub model_path: String,
    /// Every model file (sharded GGUF/safetensors); first = `model_path`.
    #[serde(default)]
    pub model_paths: Vec<String>,
    #[serde(default)]
    pub model_type: ModelType,
    /// Model family hint for tool-parser defaults (`qwen3`, `llama3`, `mistral`, ...).
    #[serde(default)]
    pub model_family: Option<String>,
    /// `model_versions.config` (n_layers, n_kv_heads, head_dim, ...) for KV estimates.
    #[serde(default)]
    pub model_config: serde_json::Map<String, serde_json::Value>,
    /// Multimodal projector (llama.cpp `--mmproj`).
    #[serde(default)]
    pub mmproj_path: Option<String>,
    #[serde(default = "default_host")]
    pub host: String,
    pub port: u16,
    #[serde(default)]
    pub devices: Vec<AssignedDevice>,
    /// VRAM of one assigned device (`devices.vram_total_bytes`), used to turn a slice into a memory
    /// fraction (`--gpu-memory-utilization`, `--mem-fraction-static`). `None` = unknown.
    #[serde(default)]
    pub device_vram_total_bytes: Option<u64>,
    /// Engine CLI flags `{"flag": value}`; `true` = bare flag, `false`/`null` = omit, arrays repeat
    /// the flag. Keys may be given with or without leading dashes. These override preset flags.
    #[serde(default)]
    pub launch_flags: BTreeMap<String, serde_json::Value>,
    /// Tool-call parser; `None` = family default (`presets::default_tool_parser`).
    #[serde(default)]
    pub tool_parser: Option<String>,
    #[serde(default)]
    pub reasoning_parser: Option<String>,
    #[serde(default)]
    pub context_size: Option<u32>,
    #[serde(default)]
    pub control_vectors: Vec<ControlVector>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    #[serde(default)]
    pub preset: Option<Preset>,
    /// Parallel slots / max-num-seqs hint.
    #[serde(default)]
    pub max_concurrency: Option<u32>,
    #[serde(default = "one")]
    pub tp_size: u32,
    #[serde(default = "one")]
    pub pp_size: u32,
    #[serde(default)]
    pub spec_decoding: SpecDecoding,
    #[serde(default)]
    pub pd_split: bool,
    /// `prefill` | `decode` when `pd_split`.
    #[serde(default)]
    pub pd_role: Option<String>,
    #[serde(default)]
    pub pd_config: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    pub kv_sharing: bool,
    #[serde(default)]
    pub kv_sharing_config: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    pub trust_remote_code: bool,
    /// llama.cpp router mode: one server process hosts many models.
    #[serde(default)]
    pub router_mode: bool,
    /// Router `--models-dir` (router mode) — the agent symlinks model files into it.
    #[serde(default)]
    pub models_dir: Option<String>,
    /// Router `--models-preset` INI path (per-model flags); see `llama_cpp::router_preset_ini`.
    #[serde(default)]
    pub models_preset: Option<String>,
    /// Max models loaded at once in router mode (`--models-max`).
    #[serde(default)]
    pub models_max: Option<u32>,
    /// Executable override (artifact entrypoint); `None` = adapter default on `PATH`.
    #[serde(default)]
    pub executable: Option<String>,
    /// Directory holding the engine's shared libraries (llama.cpp `DYLD_LIBRARY_PATH`/`LD_LIBRARY_PATH`).
    #[serde(default)]
    pub library_dir: Option<String>,
    #[serde(default)]
    pub working_dir: Option<String>,
    /// Model cache root on the node (`{{cache_dir}}`; HF_HOME for python engines).
    #[serde(default)]
    pub cache_dir: Option<String>,
    #[serde(default)]
    pub runtime: Runtime,
    #[serde(default)]
    pub container: Option<ContainerConfig>,
    /// Required when `engine == Custom`.
    #[serde(default)]
    pub custom: Option<CustomEngineConfig>,
    /// Engine version string (for logs / `extra`).
    #[serde(default)]
    pub engine_version: Option<String>,
}

fn default_host() -> String {
    "127.0.0.1".into()
}
fn one() -> u32 {
    1
}

impl LaunchSpec {
    /// A spec with defaults for everything but the identity fields.
    pub fn new(
        engine: EngineKind,
        served_model_name: impl Into<String>,
        model_path: impl Into<String>,
        port: u16,
    ) -> Self {
        let model_path = model_path.into();
        LaunchSpec {
            engine,
            served_model_name: served_model_name.into(),
            model_paths: vec![model_path.clone()],
            model_path,
            model_type: ModelType::Chat,
            model_family: None,
            model_config: serde_json::Map::new(),
            mmproj_path: None,
            host: default_host(),
            port,
            devices: Vec::new(),
            device_vram_total_bytes: None,
            launch_flags: BTreeMap::new(),
            tool_parser: None,
            reasoning_parser: None,
            context_size: None,
            control_vectors: Vec::new(),
            env: BTreeMap::new(),
            preset: None,
            max_concurrency: None,
            tp_size: 1,
            pp_size: 1,
            spec_decoding: SpecDecoding::default(),
            pd_split: false,
            pd_role: None,
            pd_config: serde_json::Map::new(),
            kv_sharing: false,
            kv_sharing_config: serde_json::Map::new(),
            trust_remote_code: false,
            router_mode: false,
            models_dir: None,
            models_preset: None,
            models_max: None,
            executable: None,
            library_dir: None,
            working_dir: None,
            cache_dir: None,
            runtime: Runtime::Native,
            container: None,
            custom: None,
            engine_version: None,
        }
    }

    /// Vendor of the assigned devices (first device), `None` when none assigned.
    pub fn vendor(&self) -> Option<Vendor> {
        self.devices.first().map(|d| d.vendor)
    }

    /// Device indices as `"0,1"`.
    pub fn device_indices(&self) -> String {
        self.devices
            .iter()
            .map(|d| d.index.to_string())
            .collect::<Vec<_>>()
            .join(",")
    }

    /// Total VRAM budget across assigned slices (`None` when any device is unsliced).
    pub fn vram_budget_bytes(&self) -> Option<u64> {
        if self.devices.is_empty() {
            return None;
        }
        self.devices
            .iter()
            .map(|d| d.slice.map(|s| s.vram_bytes))
            .sum()
    }

    /// Base URL the agent uses to reach the engine (`http://127.0.0.1:<port>`).
    pub fn base_url(&self) -> String {
        format!("http://{}:{}", self.host, self.port)
    }
}

/// How to run a container (`ProcessSpec.container`). `ProcessSpec.program/args` already contain
/// the equivalent `docker run ...` argv so the supervisor can treat both uniformly.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ContainerSpec {
    /// `docker` or `podman`.
    pub runtime: String,
    pub image: String,
    pub name: String,
    /// `host_path:container_path[:ro]`.
    pub volumes: Vec<String>,
    /// `host_port:container_port` (host network is used on Linux instead when `host_network`).
    pub ports: Vec<String>,
    pub host_network: bool,
    /// `--gpus` / `--device` arguments.
    pub device_args: Vec<String>,
    pub env: BTreeMap<String, String>,
    /// Command run inside the container (after the image).
    pub command: Vec<String>,
    pub extra_args: Vec<String>,
}

/// A fully resolved launch: `program args...` with `env`, in `cwd`. For containers, `program` is
/// the container runtime and `container` describes the pieces.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProcessSpec {
    pub program: String,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub cwd: Option<String>,
    #[serde(default)]
    pub container: Option<ContainerSpec>,
}

impl ProcessSpec {
    pub fn is_container(&self) -> bool {
        self.container.is_some()
    }

    /// Shell-ish rendering for logs (no quoting guarantees).
    pub fn display_command(&self) -> String {
        std::iter::once(self.program.clone())
            .chain(self.args.iter().cloned())
            .collect::<Vec<_>>()
            .join(" ")
    }
}

/// Health probe outcome.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum HealthStatus {
    Healthy,
    /// Engine answered but is still loading the model (llama.cpp 503, vLLM startup).
    Loading,
    Unhealthy {
        reason: String,
    },
}

impl HealthStatus {
    pub fn is_healthy(&self) -> bool {
        matches!(self, HealthStatus::Healthy)
    }
    pub fn is_loading(&self) -> bool {
        matches!(self, HealthStatus::Loading)
    }
    pub fn unhealthy(reason: impl Into<String>) -> Self {
        HealthStatus::Unhealthy {
            reason: reason.into(),
        }
    }
    /// Protocol `instance_health` value.
    pub fn instance_health(&self) -> InstanceHealth {
        match self {
            HealthStatus::Healthy => InstanceHealth::Healthy,
            HealthStatus::Loading => InstanceHealth::Unknown,
            HealthStatus::Unhealthy { .. } => InstanceHealth::Unhealthy,
        }
    }
}

/// One per-slot / per-session sample (`SessionSample` minus `ts`, `instance_id`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SlotSample {
    pub slot_id: Option<u32>,
    pub session_key: String,
    pub request_id: Option<String>,
    pub state: SessionState,
    pub prompt_tokens: Option<u64>,
    pub generated_tokens: Option<u64>,
    pub cached_tokens: Option<u64>,
    pub kv_cache_tokens: Option<u64>,
    pub kv_cache_bytes: Option<u64>,
    pub context_size: Option<u64>,
    pub ttft_ms: Option<f64>,
    pub elapsed_ms: Option<f64>,
    pub tokens_per_second: Option<f64>,
    #[serde(default)]
    pub extra: Option<serde_json::Map<String, serde_json::Value>>,
}

impl SlotSample {
    pub fn new(session_key: impl Into<String>, state: SessionState) -> Self {
        SlotSample {
            slot_id: None,
            session_key: session_key.into(),
            request_id: None,
            state,
            prompt_tokens: None,
            generated_tokens: None,
            cached_tokens: None,
            kv_cache_tokens: None,
            kv_cache_bytes: None,
            context_size: None,
            ttft_ms: None,
            elapsed_ms: None,
            tokens_per_second: None,
            extra: None,
        }
    }
}

/// One engine scrape (`EngineSample` minus ids) plus the per-slot samples taken at the same time.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct EngineSample {
    #[serde(default = "Utc::now")]
    pub ts: DateTime<Utc>,
    pub active_sessions: Option<u64>,
    pub waiting_sessions: Option<u64>,
    pub slots_total: Option<u64>,
    pub slots_idle: Option<u64>,
    pub requests_total: Option<u64>,
    pub requests_success_total: Option<u64>,
    pub errors_total: Option<u64>,
    pub prompt_tokens_total: Option<u64>,
    pub generated_tokens_total: Option<u64>,
    pub cached_tokens_total: Option<u64>,
    pub prompt_tps: Option<f64>,
    pub generation_tps: Option<f64>,
    pub ttft_ms_avg: Option<f64>,
    pub ttft_ms_p50: Option<f64>,
    pub ttft_ms_p95: Option<f64>,
    pub e2e_ms_avg: Option<f64>,
    pub e2e_ms_p50: Option<f64>,
    pub e2e_ms_p95: Option<f64>,
    pub tpot_ms_avg: Option<f64>,
    pub queue_ms_avg: Option<f64>,
    pub kv_cache_used_tokens: Option<u64>,
    pub kv_cache_total_tokens: Option<u64>,
    pub kv_cache_usage_pct: Option<f64>,
    pub kv_cache_bytes: Option<u64>,
    pub prefix_cache_hit_rate: Option<f64>,
    pub spec_accept_rate: Option<f64>,
    pub process_vram_bytes: Option<u64>,
    pub process_rss_bytes: Option<u64>,
    pub health: Option<InstanceHealth>,
    #[serde(default)]
    pub extra: Option<serde_json::Map<String, serde_json::Value>>,
    /// Per-slot / per-session samples (`metrics_batch.sessions[]`).
    #[serde(default)]
    pub sessions: Vec<SlotSample>,
}

impl EngineSample {
    pub fn now() -> Self {
        EngineSample {
            ts: Utc::now(),
            ..Default::default()
        }
    }

    /// Insert a key into `extra`.
    pub fn extra_insert(&mut self, key: &str, value: serde_json::Value) {
        self.extra
            .get_or_insert_with(serde_json::Map::new)
            .insert(key.to_string(), value);
    }

    /// The `EngineSample` wire object for `metrics_batch.engine[]` (no `sessions`).
    pub fn to_wire(&self, instance_id: &str, deployment_id: &str) -> serde_json::Value {
        let mut v = serde_json::to_value(self).unwrap_or(serde_json::Value::Null);
        if let serde_json::Value::Object(ref mut m) = v {
            m.remove("sessions");
            m.insert("instance_id".into(), instance_id.into());
            m.insert("deployment_id".into(), deployment_id.into());
            m.insert(
                "ts".into(),
                self.ts
                    .to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
                    .into(),
            );
        }
        v
    }

    /// The `SessionSample` wire objects for `metrics_batch.sessions[]`.
    pub fn sessions_to_wire(&self, instance_id: &str) -> Vec<serde_json::Value> {
        let ts = self.ts.to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
        self.sessions
            .iter()
            .filter_map(|s| {
                let mut v = serde_json::to_value(s).ok()?;
                if let serde_json::Value::Object(ref mut m) = v {
                    m.insert("ts".into(), ts.clone().into());
                    m.insert("instance_id".into(), instance_id.into());
                }
                Some(v)
            })
            .collect()
    }
}

/// A model to load/unload in an engine that supports hot swap (llama.cpp router, custom).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelRef {
    /// Router model id / served name.
    pub name: String,
    /// Local path (file or directory) when the engine needs it (custom engines; llama.cpp when the
    /// agent must first register a preset).
    #[serde(default)]
    pub path: Option<String>,
    #[serde(default)]
    pub extra: serde_json::Map<String, serde_json::Value>,
}

impl ModelRef {
    pub fn named(name: impl Into<String>) -> Self {
        ModelRef {
            name: name.into(),
            path: None,
            extra: serde_json::Map::new(),
        }
    }
}

/// Engine adapter: command building, health, metric scraping and (where supported) hot swap.
///
/// Adapters are stateless with respect to processes (the agent supervises them) but may keep
/// per-`base_url` scrape state to turn counters/histograms into rates and percentiles.
#[async_trait]
pub trait EngineAdapter: Send + Sync {
    fn kind(&self) -> EngineKind;

    /// Build the launch command (`program`, `args`, `env`, `cwd`) — or a container run — for `spec`.
    fn command(&self, spec: &LaunchSpec) -> anyhow::Result<ProcessSpec>;

    /// Probe the engine's health endpoint.
    async fn health(&self, base_url: &str) -> HealthStatus;

    /// Scrape metrics (+ slot/session samples) into the `metrics.md` catalog.
    async fn scrape(&self, base_url: &str) -> anyhow::Result<EngineSample>;

    /// True when models can be loaded/unloaded in a running process (llama.cpp router mode).
    fn supports_hot_swap(&self) -> bool;

    async fn load_model(&self, base_url: &str, model: &ModelRef) -> anyhow::Result<()>;

    async fn unload_model(&self, base_url: &str, model: &str) -> anyhow::Result<()>;

    /// Default health probe settings for `engine_versions.health_config`.
    fn health_config(&self) -> HealthConfig {
        HealthConfig::default()
    }

    /// Default scrape settings for `engine_versions.metrics_config`.
    fn metrics_config(&self) -> MetricsConfig {
        MetricsConfig::default()
    }

    /// True when request/latency/token metrics must come from the agent proxy (`ProxyStats`).
    fn derive_from_proxy(&self) -> bool {
        self.metrics_config().derive_from_proxy
    }

    /// Model name the engine itself answers to, for the proxy's `rewrite_model` (MLX serves the
    /// model given by `--model` as `default_model`; mlx-audio loads models by path per request).
    fn upstream_model_name(&self, spec: &LaunchSpec) -> String {
        spec.served_model_name.clone()
    }

    /// Path prefixes the agent proxy may forward to this engine from the network.
    fn allowed_path_prefixes(&self) -> Vec<&'static str> {
        vec!["/v1/"]
    }

    /// Engine-side admin endpoints the proxy must never expose.
    fn admin_paths(&self) -> Vec<&'static str> {
        vec![
            "/metrics",
            "/slots",
            "/props",
            "/models/load",
            "/models/unload",
            "/health",
        ]
    }

    /// Merge proxy-derived counters into a scraped sample (fills everything the engine did not
    /// provide; never overwrites engine-provided values).
    fn apply_proxy_stats(&self, sample: &mut EngineSample, stats: &crate::proxy::ProxyStats) {
        crate::proxy::merge_proxy_stats(sample, stats, self.derive_from_proxy());
    }
}
