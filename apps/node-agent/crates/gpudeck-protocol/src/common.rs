//! Shared definitions (`common.json`): enums and the reusable objects referenced by every
//! message schema. Field names match the JSON Schema (and the Postgres columns) 1:1.

use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

/// RFC 3339 timestamp with timezone (`common.json#/$defs/timestamp`).
pub type Timestamp = DateTime<Utc>;

/// `labels` objects: string -> string.
pub type Labels = BTreeMap<String, String>;

/// Free-form JSON object (`extra`, `details`, `config`, ...).
pub type JsonObject = serde_json::Map<String, Value>;

macro_rules! string_enum {
    ($(#[$meta:meta])* $name:ident { $($variant:ident => $wire:literal),+ $(,)? }) => {
        $(#[$meta])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
        pub enum $name {
            $(#[serde(rename = $wire)] $variant),+
        }

        impl $name {
            /// Wire (JSON) spelling of the variant.
            pub fn as_str(&self) -> &'static str {
                match self { $(Self::$variant => $wire),+ }
            }

            /// Every variant, in schema order.
            pub const ALL: &'static [$name] = &[$(Self::$variant),+];
        }

        impl std::fmt::Display for $name {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str(self.as_str())
            }
        }

        impl std::str::FromStr for $name {
            type Err = String;
            fn from_str(s: &str) -> Result<Self, Self::Err> {
                match s {
                    $($wire => Ok(Self::$variant),)+
                    other => Err(format!("unknown {} value: {other}", stringify!($name))),
                }
            }
        }
    };
}

string_enum!(NodeOs { Linux => "linux", Macos => "macos" });
string_enum!(NodeArch { X86_64 => "x86_64", Aarch64 => "aarch64" });
string_enum!(DeviceVendor {
    Nvidia => "nvidia", Amd => "amd", Hygon => "hygon", Metax => "metax",
    Moorethreads => "moorethreads", Thead => "thead", Apple => "apple",
});
string_enum!(DeviceHealth { Healthy => "healthy", Degraded => "degraded", Unhealthy => "unhealthy", Unknown => "unknown" });
string_enum!(InstanceState {
    Pending => "pending", Loading => "loading", Ready => "ready", Draining => "draining",
    Unloading => "unloading", Stopped => "stopped", Error => "error",
});
string_enum!(InstanceHealth { Healthy => "healthy", Unhealthy => "unhealthy", Unknown => "unknown" });
string_enum!(
    /// Engine family of an observed (external) model server (`agent.toml` `[[observe]]`).
    ObservedEngineKind {
        Vllm => "vllm", Sglang => "sglang", Llamacpp => "llamacpp", Tgi => "tgi",
        Openai => "openai", Generic => "generic",
    }
);
string_enum!(EngineName {
    LlamaCpp => "llama_cpp", Vllm => "vllm", VllmOmni => "vllm_omni", Sglang => "sglang",
    SglangDiffusion => "sglang_diffusion", TensorrtLlm => "tensorrt_llm", Mlx => "mlx",
    MlxAudio => "mlx_audio", WhisperCpp => "whisper_cpp", StableDiffusionCpp => "stable_diffusion_cpp",
    Custom => "custom",
});
string_enum!(EngineRuntime { Native => "native", Container => "container" });
string_enum!(ModelType {
    Chat => "chat", Thinking => "thinking", Vision => "vision", Embedding => "embedding",
    Rerank => "rerank", Stt => "stt", Tts => "tts", Image => "image", Video => "video",
});
string_enum!(ModelFileFormat { Gguf => "gguf", Safetensors => "safetensors", Mlx => "mlx", Pickle => "pickle", Other => "other" });
string_enum!(ModelSourceKind { Huggingface => "huggingface", Modelscope => "modelscope", LocalPath => "local_path" });
string_enum!(CacheState {
    Queued => "queued", Downloading => "downloading", Ready => "ready", Evicting => "evicting",
    Evicted => "evicted", Error => "error",
});
string_enum!(LogLevel { Debug => "debug", Info => "info", Warn => "warn", Error => "error" });
string_enum!(LogStream { Stdout => "stdout", Stderr => "stderr", App => "app" });
string_enum!(ArtifactKind { Tarball => "tarball", Zip => "zip", Binary => "binary", Wheel => "wheel", PythonVenv => "python_venv", Source => "source" });
string_enum!(HttpMethod { Get => "GET", Post => "POST" });
string_enum!(MetricsFormat { Prometheus => "prometheus", Json => "json", None => "none" });
string_enum!(SessionState { Idle => "idle", Waiting => "waiting", Processing => "processing", Generating => "generating" });
string_enum!(EgressLockKind { Pf => "pf", Nftables => "nftables", Iptables => "iptables", None => "none" });
string_enum!(KeyType { EcdsaP256 => "ecdsa_p256", Ed25519 => "ed25519", Rsa3072 => "rsa_3072" });

/// Common device format produced by every vendor plugin (`devices` table).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Device {
    /// Stable id `uuid5(node_id, "<vendor>:<index>")` (protocol.md §10); filled by the agent.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub device_id: Option<Uuid>,
    pub vendor: DeviceVendor,
    pub index: u32,
    #[serde(default)]
    pub uuid: Option<String>,
    pub name: String,
    pub vram_total_bytes: u64,
    #[serde(default)]
    pub is_unified_memory: bool,
    #[serde(default)]
    pub driver_version: Option<String>,
    #[serde(default)]
    pub runtime_version: Option<String>,
    #[serde(default)]
    pub compute_capability: Option<String>,
    #[serde(default)]
    pub pci_bus_id: Option<String>,
    #[serde(default)]
    pub numa_node: Option<i64>,
    #[serde(default)]
    pub max_power_watts: Option<f64>,
    #[serde(default)]
    pub core_count: Option<i64>,
    pub health: DeviceHealth,
    #[serde(default)]
    pub health_reason: Option<String>,
    #[serde(default)]
    pub labels: Labels,
    #[serde(default)]
    pub extra: JsonObject,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Disk {
    pub mount: String,
    #[serde(default)]
    pub device: Option<String>,
    #[serde(default)]
    pub fs: Option<String>,
    pub total_bytes: u64,
    pub free_bytes: u64,
    #[serde(default)]
    pub is_cache_dir: bool,
}

/// One cached model file on the node (`node_model_cache` row).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CacheItem {
    pub model_file_id: Uuid,
    pub model_version_id: Uuid,
    pub local_path: String,
    pub size_bytes: u64,
    pub state: CacheState,
    #[serde(default)]
    pub progress_pct: f64,
    #[serde(default)]
    pub sha256_verified: bool,
    #[serde(default)]
    pub pinned: bool,
    #[serde(default)]
    pub last_used_at: Option<Timestamp>,
    #[serde(default)]
    pub error_message: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Slice {
    pub vram_bytes: u64,
    pub compute_pct: u8,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DeviceAssignment {
    pub device_id: Uuid,
    pub index: u32,
    pub vendor: DeviceVendor,
    #[serde(default)]
    pub slice: Option<Slice>,
}

/// Pinned native engine build (`common.json#/$defs/Artifact`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Artifact {
    pub url: String,
    pub sha256: String,
    pub kind: ArtifactKind,
    #[serde(default)]
    pub entrypoint: Option<String>,
    #[serde(default)]
    pub size_bytes: Option<u64>,
    /// Leading path components dropped when unpacking.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub strip_components: Option<u32>,
    /// Further archives unpacked after the main one (e.g. a CUDA runtime bundle).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub parts: Vec<ArtifactPart>,
    /// `python_venv`: extras installed with the wheel.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub pip_extras: Vec<String>,
    /// `python_venv`: `==3.12` (exact) or `>=3.11` (minimum).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub python: Option<String>,
    /// `source`: how to build it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub build: Option<ArtifactBuild>,
    /// Linux: oldest host glibc the build runs on (`"2.38"`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_glibc: Option<String>,
    /// Same-accelerator builds for older systems, tried in order when the host cannot run this.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub fallbacks: Vec<Artifact>,
    /// Official image of the engine, used when no native build fits and a container runtime
    /// is available.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub container: Option<ArtifactContainer>,
}

/// Container fallback of an [`Artifact`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ArtifactContainer {
    /// `image:tag@sha256:<index digest>`.
    pub image: String,
    /// Program inside the image that replaces the launch template's first word.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub entrypoint: Option<String>,
    /// Engine build inside the image when it differs from the engine version.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
}

/// Extra archive of an [`Artifact`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ArtifactPart {
    pub url: String,
    pub sha256: String,
    pub kind: ArtifactKind,
    #[serde(default)]
    pub size_bytes: Option<u64>,
    #[serde(default)]
    pub dest: Option<String>,
    #[serde(default)]
    pub strip_components: Option<u32>,
}

/// Build recipe of a `source` artifact (CMake only).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ArtifactBuild {
    pub system: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub targets: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HealthConfig {
    #[serde(default = "HealthConfig::default_path")]
    pub path: String,
    #[serde(default = "HealthConfig::default_method")]
    pub method: HttpMethod,
    #[serde(default = "HealthConfig::default_interval")]
    pub interval_seconds: f64,
    #[serde(default = "HealthConfig::default_timeout")]
    pub timeout_seconds: f64,
    #[serde(default = "HealthConfig::default_statuses")]
    pub healthy_statuses: Vec<u16>,
    #[serde(default = "HealthConfig::default_startup_timeout")]
    pub startup_timeout_seconds: f64,
    #[serde(default = "HealthConfig::default_failure_threshold")]
    pub failure_threshold: u32,
}

impl HealthConfig {
    fn default_path() -> String {
        "/health".into()
    }
    fn default_method() -> HttpMethod {
        HttpMethod::Get
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
    fn default_startup_timeout() -> f64 {
        600.0
    }
    fn default_failure_threshold() -> u32 {
        3
    }
}

impl Default for HealthConfig {
    fn default() -> Self {
        Self {
            path: Self::default_path(),
            method: Self::default_method(),
            interval_seconds: Self::default_interval(),
            timeout_seconds: Self::default_timeout(),
            healthy_statuses: Self::default_statuses(),
            startup_timeout_seconds: Self::default_startup_timeout(),
            failure_threshold: Self::default_failure_threshold(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MetricsConfig {
    #[serde(default = "MetricsConfig::default_path")]
    pub path: Option<String>,
    #[serde(default = "MetricsConfig::default_format")]
    pub format: MetricsFormat,
    #[serde(default)]
    pub slots_path: Option<String>,
    #[serde(default = "MetricsConfig::default_interval")]
    pub scrape_interval_seconds: f64,
    #[serde(default)]
    pub derive_from_proxy: bool,
    #[serde(default)]
    pub metric_prefix: Option<String>,
}

impl MetricsConfig {
    fn default_path() -> Option<String> {
        Some("/metrics".into())
    }
    fn default_format() -> MetricsFormat {
        MetricsFormat::Prometheus
    }
    fn default_interval() -> f64 {
        2.0
    }
}

impl Default for MetricsConfig {
    fn default() -> Self {
        Self {
            path: Self::default_path(),
            format: Self::default_format(),
            slots_path: None,
            scrape_interval_seconds: Self::default_interval(),
            derive_from_proxy: false,
            metric_prefix: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LaunchTemplate {
    /// argv with `{{var}}` placeholders.
    pub command: Vec<String>,
    #[serde(default)]
    pub env: Labels,
    #[serde(default)]
    pub working_dir: Option<String>,
    #[serde(default)]
    pub router_mode: bool,
}

/// Everything the agent needs to run an engine version.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EngineSpec {
    pub engine_version_id: Uuid,
    pub name: EngineName,
    pub version: String,
    pub runtime: EngineRuntime,
    #[serde(default)]
    pub image: Option<String>,
    #[serde(default)]
    pub artifact: Option<Artifact>,
    pub launch_template: LaunchTemplate,
    pub health: HealthConfig,
    pub metrics: MetricsConfig,
    #[serde(default = "EngineSpec::default_port_range")]
    pub port_range: [u16; 2],
    #[serde(default)]
    pub capabilities: JsonObject,
}

impl EngineSpec {
    pub fn default_port_range() -> [u16; 2] {
        [10240, 10340]
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelFileRef {
    pub model_file_id: Uuid,
    pub path: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub format: Option<ModelFileFormat>,
    pub size_bytes: u64,
    #[serde(default)]
    pub sha256: Option<String>,
    #[serde(default = "default_true")]
    pub is_required: bool,
}

pub(crate) fn default_true() -> bool {
    true
}

/// Where to fetch files from. `token` must never be logged or persisted.
#[derive(Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelSource {
    pub kind: ModelSourceKind,
    pub repo_id: String,
    #[serde(default)]
    pub revision: Option<String>,
    #[serde(default)]
    pub endpoint: Option<String>,
    #[serde(default)]
    pub token: Option<String>,
}

impl ModelSource {
    /// Copy with the secret removed (what gets persisted to `commands.json`).
    pub fn redacted(&self) -> Self {
        Self {
            token: None,
            ..self.clone()
        }
    }
}

impl std::fmt::Debug for ModelSource {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ModelSource")
            .field("kind", &self.kind)
            .field("repo_id", &self.repo_id)
            .field("revision", &self.revision)
            .field("endpoint", &self.endpoint)
            .field("token", &self.token.as_ref().map(|_| "<redacted>"))
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelSpec {
    pub model_id: Uuid,
    pub model_version_id: Uuid,
    pub name: String,
    pub served_model_name: String,
    #[serde(rename = "type")]
    pub model_type: ModelType,
    pub format: ModelFileFormat,
    pub files: Vec<ModelFileRef>,
    pub cache_dir: String,
    #[serde(default)]
    pub source: Option<ModelSource>,
    #[serde(default)]
    pub config: JsonObject,
    #[serde(default)]
    pub trust_remote_code: bool,
    #[serde(default)]
    pub allow_pickle: bool,
}

impl ModelSpec {
    pub fn redacted(&self) -> Self {
        Self {
            source: self.source.as_ref().map(ModelSource::redacted),
            ..self.clone()
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ControlVector {
    pub path: String,
    #[serde(default = "ControlVector::default_scale")]
    pub scale: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub layer_range: Option<[i64; 2]>,
}

impl ControlVector {
    fn default_scale() -> f64 {
        1.0
    }
}

/// Per-deployment launch settings.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LaunchSpec {
    #[serde(default)]
    pub flags: JsonObject,
    #[serde(default)]
    pub env: Labels,
    #[serde(default)]
    pub context_size: Option<u64>,
    #[serde(default)]
    pub tool_parser: Option<String>,
    #[serde(default)]
    pub control_vectors: Vec<ControlVector>,
    #[serde(default = "LaunchSpec::default_one")]
    pub tp_size: u32,
    #[serde(default = "LaunchSpec::default_one")]
    pub pp_size: u32,
    #[serde(default)]
    pub preset: Option<String>,
    #[serde(default)]
    pub pd_split: bool,
    #[serde(default)]
    pub pd_role: Option<String>,
    #[serde(default)]
    pub pd_config: JsonObject,
    #[serde(default)]
    pub spec_decoding: JsonObject,
    #[serde(default)]
    pub kv_sharing: bool,
    #[serde(default)]
    pub kv_sharing_config: JsonObject,
    #[serde(default)]
    pub max_concurrency: Option<u32>,
    /// Cross-node pipeline-parallel membership (`DistributedSpec`); `None` = single node.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub distributed: Option<DistributedSpec>,
}

/// One rank of a cross-node group (`common.json#/$defs/DistributedSpec`, protocol.md §9).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DistributedSpec {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group_id: Option<Uuid>,
    pub rank: u32,
    pub world_size: u32,
    pub master_addr: String,
    pub master_port: u16,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bind_address: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rpc_port: Option<u16>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub nccl_socket_ifname: Option<String>,
    #[serde(default)]
    pub peers: Vec<DistributedPeer>,
}

impl DistributedSpec {
    /// Default llama.cpp `rpc-server` port.
    pub const DEFAULT_RPC_PORT: u16 = 50052;

    pub fn is_head(&self) -> bool {
        self.rank == 0
    }

    /// Peers other than this rank.
    pub fn others(&self) -> impl Iterator<Item = &DistributedPeer> {
        self.peers.iter().filter(move |p| p.rank != self.rank)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DistributedPeer {
    pub rank: u32,
    pub node_id: Uuid,
    pub address: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rpc_port: Option<u16>,
    #[serde(default)]
    pub device_ids: Vec<Uuid>,
}

impl LaunchSpec {
    fn default_one() -> u32 {
        1
    }
}

impl Default for LaunchSpec {
    fn default() -> Self {
        Self {
            flags: JsonObject::new(),
            env: Labels::new(),
            context_size: None,
            tool_parser: None,
            control_vectors: vec![],
            tp_size: 1,
            pp_size: 1,
            preset: None,
            pd_split: false,
            pd_role: None,
            pd_config: JsonObject::new(),
            spec_decoding: JsonObject::new(),
            kv_sharing: false,
            kv_sharing_config: JsonObject::new(),
            max_concurrency: None,
            distributed: None,
        }
    }
}

/// `metric_samples_gpu` row.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct GpuSample {
    #[serde(default = "crate::now")]
    pub ts: Timestamp,
    pub device_id: Uuid,
    pub utilization_pct: Option<f64>,
    pub memory_util_pct: Option<f64>,
    pub memory_used_bytes: Option<u64>,
    pub memory_total_bytes: Option<u64>,
    pub power_watts: Option<f64>,
    pub power_limit_watts: Option<f64>,
    pub temperature_c: Option<f64>,
    pub memory_temperature_c: Option<f64>,
    pub sm_clock_mhz: Option<u64>,
    pub memory_clock_mhz: Option<u64>,
    pub fan_pct: Option<f64>,
    pub encoder_pct: Option<f64>,
    pub decoder_pct: Option<f64>,
    pub pcie_tx_bytes_per_s: Option<u64>,
    pub pcie_rx_bytes_per_s: Option<u64>,
    pub ecc_errors_total: Option<u64>,
    pub error_count: Option<u64>,
    pub throttle_reasons: Option<Vec<String>>,
    pub process_count: Option<u64>,
    /// Processes holding memory on this GPU (added 2026-11-23, NVIDIA/NVML). Sent only when the
    /// hub lists the `gpu_processes` feature in `hello_ack.features`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub processes: Option<Vec<GpuProcess>>,
    pub extra: Option<JsonObject>,
}

/// One process using a GPU: pid, memory and the executable basename (never its arguments).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct GpuProcess {
    pub pid: u32,
    #[serde(default)]
    pub used_memory_bytes: Option<u64>,
    #[serde(default)]
    pub name: Option<String>,
    /// Managed instance / observed engine whose process tree holds this process (`None` = a
    /// process GPU Deck neither runs nor observes).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instance_id: Option<Uuid>,
}

/// A model server GPU Deck did not start and only observes (`protocol.md` §12).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ObservedEngine {
    /// `uuid5(node_id, "observe:<name>")`, used as the instance id.
    pub observe_id: Uuid,
    /// `uuid5(node_id, "observe-deployment:<name>")`.
    pub deployment_id: Uuid,
    pub name: String,
    pub engine: ObservedEngineKind,
    #[serde(default)]
    pub endpoint: Option<String>,
    #[serde(default)]
    pub sources: Vec<String>,
    #[serde(default)]
    pub models: Vec<String>,
    #[serde(default)]
    pub version: Option<String>,
    pub health: InstanceHealth,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub last_ok_at: Option<Timestamp>,
    /// Main process matched by `listen_port` / `pid_cmd_contains`.
    #[serde(default)]
    pub pid: Option<u32>,
    /// Devices where the matched processes hold GPU memory.
    #[serde(default)]
    pub device_ids: Vec<Uuid>,
}

/// `metric_samples_engine` row. `*_total` are monotonic counters since engine start.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct EngineSample {
    #[serde(default = "crate::now")]
    pub ts: Timestamp,
    pub instance_id: Uuid,
    pub deployment_id: Uuid,
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
    /// True for an observed (external) engine (added 2026-11-23).
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub observed: bool,
    pub extra: Option<JsonObject>,
}

/// `metric_samples_session` row (one per active slot/session per scrape).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionSample {
    #[serde(default = "crate::now")]
    pub ts: Timestamp,
    pub instance_id: Uuid,
    pub slot_id: Option<i64>,
    pub session_key: String,
    pub request_id: Option<Uuid>,
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
    pub extra: Option<JsonObject>,
}

impl SessionSample {
    pub fn new(instance_id: Uuid, session_key: impl Into<String>, state: SessionState) -> Self {
        Self {
            ts: crate::now(),
            instance_id,
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

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InstanceStatus {
    pub instance_id: Uuid,
    #[serde(default)]
    pub deployment_id: Option<Uuid>,
    #[serde(default)]
    pub engine_process_id: Option<Uuid>,
    pub state: InstanceState,
    pub health: InstanceHealth,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub served_model_name: Option<String>,
    #[serde(default)]
    pub pid: Option<u32>,
    #[serde(default)]
    pub container_id: Option<String>,
    #[serde(default)]
    pub device_ids: Vec<Uuid>,
    #[serde(default)]
    pub active_sessions: Option<u64>,
    #[serde(default)]
    pub waiting_sessions: Option<u64>,
    #[serde(default)]
    pub message: Option<String>,
    /// Rank when this node runs one stage of a cross-node group.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub distributed_rank: Option<u32>,
}

string_enum!(SshInstanceState { Starting => "starting", Running => "running", Stopping => "stopping", Stopped => "stopped", Error => "error" });

/// `common.json#/$defs/SshInstanceStatus`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SshInstanceStatus {
    pub ssh_instance_id: Uuid,
    pub state: SshInstanceState,
    pub health: InstanceHealth,
    #[serde(default)]
    pub container_id: Option<String>,
    #[serde(default)]
    pub image: Option<String>,
    #[serde(default)]
    pub host_address: Option<String>,
    #[serde(default)]
    pub host_port: Option<u16>,
    #[serde(default)]
    pub ssh_user: Option<String>,
    #[serde(default)]
    pub host_key_fingerprint: Option<String>,
    #[serde(default)]
    pub device_ids: Vec<Uuid>,
    #[serde(default)]
    pub gpu_attached: bool,
    #[serde(default)]
    pub active_connections: u64,
    #[serde(default)]
    pub connections_total: u64,
    #[serde(default)]
    pub last_connection_at: Option<Timestamp>,
    #[serde(default)]
    pub started_at: Option<Timestamp>,
    #[serde(default)]
    pub expires_at: Option<Timestamp>,
    #[serde(default)]
    pub message: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LogLine {
    pub ts: Timestamp,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub level: Option<LogLevel>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stream: Option<LogStream>,
    pub text: String,
}

/// Stable machine error codes listed in `common.json#/$defs/ErrorInfo`.
pub mod error_code {
    pub const INVALID_ARGS: &str = "invalid_args";
    pub const UNSUPPORTED_ENGINE: &str = "unsupported_engine";
    pub const ARTIFACT_DOWNLOAD_FAILED: &str = "artifact_download_failed";
    pub const ARTIFACT_CHECKSUM_MISMATCH: &str = "artifact_checksum_mismatch";
    pub const MODEL_DOWNLOAD_FAILED: &str = "model_download_failed";
    pub const MODEL_CHECKSUM_MISMATCH: &str = "model_checksum_mismatch";
    pub const PICKLE_BLOCKED: &str = "pickle_blocked";
    pub const INSUFFICIENT_VRAM: &str = "insufficient_vram";
    pub const INSUFFICIENT_DISK: &str = "insufficient_disk";
    pub const PORT_IN_USE: &str = "port_in_use";
    pub const ENGINE_START_FAILED: &str = "engine_start_failed";
    pub const ENGINE_CRASHED: &str = "engine_crashed";
    pub const HEALTH_TIMEOUT: &str = "health_timeout";
    pub const DRAIN_TIMEOUT: &str = "drain_timeout";
    pub const INSTANCE_NOT_FOUND: &str = "instance_not_found";
    pub const ENGINE_PROCESS_NOT_FOUND: &str = "engine_process_not_found";
    pub const CACHE_ITEM_NOT_FOUND: &str = "cache_item_not_found";
    pub const CACHE_IN_USE: &str = "cache_in_use";
    pub const EGRESS_BLOCKED: &str = "egress_blocked";
    pub const TIMEOUT: &str = "timeout";
    pub const CANCELLED: &str = "cancelled";
    pub const INTERNAL: &str = "internal";
    pub const PATH_NOT_FOUND: &str = "path_not_found";
    pub const PERMISSION_DENIED: &str = "permission_denied";
    pub const NO_CONTAINER_RUNTIME: &str = "no_container_runtime";
    pub const IMAGE_PULL_FAILED: &str = "image_pull_failed";
    pub const SSH_INSTANCE_NOT_FOUND: &str = "ssh_instance_not_found";
    pub const DISTRIBUTED_UNSUPPORTED: &str = "distributed_unsupported";
    /// Agent updates (`protocol.md` §14).
    pub const UPDATE_UNSUPPORTED: &str = "update_unsupported";
    pub const SIGNATURE_INVALID: &str = "signature_invalid";
    pub const NOT_STAGED: &str = "not_staged";
    pub const UPDATE_IN_PROGRESS: &str = "update_in_progress";
    pub const ROLLED_BACK: &str = "rolled_back";
}

// ---------------------------------------------------------------------------
// Agent updates (`protocol.md` §14)
// ---------------------------------------------------------------------------

/// `common.json#/$defs/ReleaseManifest`: what the release workflow signs.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReleaseManifest {
    pub schema: u32,
    pub product: String,
    pub version: String,
    #[serde(default = "ReleaseManifest::default_protocol")]
    pub protocol_version: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_at: Option<Timestamp>,
    pub artifacts: Vec<ReleaseArtifact>,
}

impl ReleaseManifest {
    fn default_protocol() -> u32 {
        1
    }

    /// The artifact for `target` (exact triple).
    pub fn artifact(&self, target: &str) -> Option<&ReleaseArtifact> {
        self.artifacts.iter().find(|a| a.target == target)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReleaseArtifact {
    pub target: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub file: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub size_bytes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    pub binary_sha256: String,
    pub binary_size_bytes: u64,
}

/// `common.json#/$defs/SignedReleaseManifest`: manifest bytes + detached ed25519 signature.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SignedReleaseManifest {
    /// Standard base64 of the manifest file bytes.
    pub manifest: String,
    /// Standard base64 of the 64-byte signature.
    pub signature: String,
    /// Always `ed25519`.
    pub algorithm: String,
}

string_enum! {
    /// How this agent can update itself (`AgentUpdateStatus.mode`).
    AgentUpdateMode {
        Helper => "helper",
        SelfUpdate => "self",
        Managed => "managed",
        Unsupported => "unsupported",
    }
}

string_enum! {
    /// `AgentUpdateStatus.state`.
    AgentUpdateState {
        Idle => "idle",
        Staging => "staging",
        Staged => "staged",
        Applying => "applying",
    }
}

/// `common.json#/$defs/AgentUpdateStatus` (`hello.agent_update`, `inventory.agent_update`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentUpdateStatus {
    pub mode: AgentUpdateMode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target: Option<String>,
    #[serde(default = "AgentUpdateStatus::default_state")]
    pub state: AgentUpdateState,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub staged_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub staged_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub staged_at: Option<Timestamp>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous_version: Option<String>,
    #[serde(default)]
    pub release_keys: u32,
}

impl AgentUpdateStatus {
    fn default_state() -> AgentUpdateState {
        AgentUpdateState::Idle
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ErrorInfo {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub details: Option<JsonObject>,
}

impl ErrorInfo {
    pub fn new(code: &str, message: impl Into<String>) -> Self {
        let mut message: String = message.into();
        if message.len() > 2048 {
            message.truncate(2048);
        }
        Self {
            code: code.to_string(),
            message,
            retryable: false,
            details: None,
        }
    }

    pub fn retryable(mut self) -> Self {
        self.retryable = true;
        self
    }

    pub fn with_details(mut self, details: JsonObject) -> Self {
        self.details = Some(details);
        self
    }
}

impl std::fmt::Display for ErrorInfo {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for ErrorInfo {}
