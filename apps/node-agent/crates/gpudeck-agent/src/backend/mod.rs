//! Device plugins and engine adapters as the agent sees them.
//!
//! The real implementations live in the plugin crates (`gpudeck-devices`,
//! `gpudeck-engines`) and are bridged in [`plugins`] (cargo feature `plugins`, on by default). The agent
//! core only talks to the two traits below, expressed in protocol types, so it can be built and
//! tested without the plugins (`--no-default-features`): then no device plugin is loaded and every engine
//! uses [`generic::GenericDriver`] (HTTP health probe + proxy-derived metrics).

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;

use async_trait::async_trait;
use gpudeck_protocol::{
    Device, DeviceAssignment, DeviceVendor, EngineName, EngineSample, EngineSpec, GpuSample,
    HealthConfig, LaunchSpec, MetricsConfig, ModelSpec, SessionSample,
};
use uuid::Uuid;

pub mod generic;
#[cfg(feature = "plugins")]
pub mod plugins;

// ---------------------------------------------------------------------------
// Devices
// ---------------------------------------------------------------------------

/// One GPU reading before the agent attaches the hub's `device_id`.
#[derive(Debug, Clone, PartialEq)]
pub struct DeviceReading {
    pub vendor: DeviceVendor,
    pub index: u32,
    pub uuid: Option<String>,
    /// `device_id` is `Uuid::nil()` here; the metrics loop fills it in.
    pub sample: GpuSample,
}

#[async_trait]
pub trait DeviceSource: Send + Sync {
    /// Devices for `inventory.devices` (called at start and on every inventory refresh).
    async fn detect(&self) -> Vec<Device>;
    /// One sample per device (called every metrics interval).
    async fn sample(&self) -> Vec<DeviceReading>;
    /// Plugin backends in use (`iokit`, `nvml`, ...), for logs and `status`.
    fn backends(&self) -> Vec<String>;
}

/// Used when built without the plugins: no accelerators are reported.
pub struct NoDevices;

#[async_trait]
impl DeviceSource for NoDevices {
    async fn detect(&self) -> Vec<Device> {
        vec![]
    }
    async fn sample(&self) -> Vec<DeviceReading> {
        vec![]
    }
    fn backends(&self) -> Vec<String> {
        vec![]
    }
}

// ---------------------------------------------------------------------------
// Engines
// ---------------------------------------------------------------------------

/// Everything resolved by the agent for one engine launch (dedicated instance or router process).
#[derive(Debug, Clone)]
pub struct EngineLaunch {
    pub engine: EngineSpec,
    pub launch: LaunchSpec,
    /// `None` for a router process started without a model.
    pub model: Option<ModelSpec>,
    pub served_model_name: Option<String>,
    /// Primary model file (GGUF) or the model directory.
    pub model_path: Option<PathBuf>,
    pub model_paths: Vec<PathBuf>,
    pub model_dir: Option<PathBuf>,
    pub draft_model_path: Option<PathBuf>,
    pub port: u16,
    pub devices: Vec<DeviceAssignment>,
    /// Resolved executable (engine_paths / artifact entrypoint / PATH).
    pub executable: Option<PathBuf>,
    /// Shared-library dir of a native engine (llama.cpp needs DYLD_/LD_LIBRARY_PATH).
    pub library_dir: Option<PathBuf>,
    pub cache_dir: PathBuf,
    /// llama.cpp router mode: models dir + preset INI managed by the agent.
    pub router: Option<RouterFiles>,
    pub instance_id: Option<Uuid>,
    pub engine_process_id: Option<Uuid>,
}

#[derive(Debug, Clone)]
pub struct RouterFiles {
    pub models_dir: PathBuf,
    pub preset_path: PathBuf,
}

/// A fully resolved process: `program args...` with `env` in `cwd`. Containers run through the
/// container CLI in the foreground (`docker run --rm ...`), so they are supervised the same way.
#[derive(Debug, Clone, PartialEq)]
pub struct ProcSpec {
    pub program: String,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub cwd: Option<PathBuf>,
    pub container: Option<ContainerRun>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ContainerRun {
    /// `docker` / `podman`.
    pub runtime: String,
    pub name: String,
}

impl ProcSpec {
    pub fn display(&self) -> String {
        std::iter::once(self.program.as_str())
            .chain(self.args.iter().map(String::as_str))
            .collect::<Vec<_>>()
            .join(" ")
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Health {
    Healthy,
    /// Answering but still loading (llama.cpp 503, router `loading`).
    Loading,
    Unhealthy(String),
}

/// One scrape: the engine sample (ids are filled by the caller) and per-session samples.
#[derive(Debug, Clone, Default)]
pub struct Scrape {
    pub engine: EngineSample,
    pub sessions: Vec<SessionSample>,
}

/// Proxy-side view of one instance, merged into scrapes (`metrics.md` §4).
#[derive(Debug, Clone, Default)]
pub struct ProxySnapshot {
    pub requests_total: u64,
    pub requests_success_total: u64,
    pub errors_total: u64,
    pub prompt_tokens_total: u64,
    pub generated_tokens_total: u64,
    pub cached_tokens_total: Option<u64>,
    pub draft_tokens_total: u64,
    pub draft_accepted_total: u64,
    pub active: u64,
    pub waiting: u64,
    pub max_concurrent: Option<u64>,
    pub window: Vec<crate::proxy::stats::RequestRecord>,
    pub window_seconds: f64,
    pub inflight: Vec<crate::proxy::stats::Inflight>,
    pub kv_bytes_per_token: Option<u64>,
    pub context_size: Option<u64>,
}

#[async_trait]
pub trait EngineDriver: Send + Sync {
    fn name(&self) -> EngineName;

    /// Build the process for a launch whose template is `["{{adapter}}"]` (the adapter decides
    /// the full command line). Templates with a real argv are rendered by the agent instead.
    fn adapter_command(&self, launch: &EngineLaunch) -> anyhow::Result<ProcSpec>;

    /// Probe health. `model` is the router model id in router mode.
    async fn health(&self, base_url: &str, health: &HealthConfig, model: Option<&str>) -> Health;

    /// Scrape engine metrics. `model` is the router model id in router mode.
    async fn scrape(
        &self,
        base_url: &str,
        metrics: &MetricsConfig,
        model: Option<&str>,
    ) -> anyhow::Result<Scrape>;

    fn supports_hot_swap(&self) -> bool;
    async fn load_model(
        &self,
        base_url: &str,
        name: &str,
        path: Option<&str>,
    ) -> anyhow::Result<()>;
    async fn unload_model(&self, base_url: &str, name: &str) -> anyhow::Result<()>;
    /// Router mode: model ids the running router knows and whether each is loaded.
    async fn router_models(&self, base_url: &str) -> anyhow::Result<Vec<RouterModel>>;

    /// Fill what the engine did not report from the proxy (never overwrites engine values unless
    /// `derive_from_proxy`).
    fn merge_proxy(&self, scrape: &mut Scrape, snap: &ProxySnapshot, derive_from_proxy: bool);

    /// Engine-side admin endpoints the proxy must never forward.
    fn admin_paths(&self) -> Vec<String>;

    /// Model name the engine itself answers to, when it differs from the served name (the proxy
    /// rewrites the request `model` to it; MLX answers to `default_model`). `None` = served name.
    fn upstream_model_name(&self, _launch: &EngineLaunch) -> Option<String> {
        None
    }

    /// A process for `launch` is up at `base_url` (lets the adapter keep scrape hints).
    fn register(&self, _base_url: &str, _launch: &EngineLaunch) {}

    /// The process at `base_url` stopped.
    fn forget(&self, _base_url: &str) {}
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RouterModel {
    pub id: String,
    /// `unloaded | loading | loaded | sleeping`.
    pub status: String,
    pub failed: bool,
}

/// Engine driver lookup by name.
pub trait EngineCatalog: Send + Sync {
    fn driver(&self, name: EngineName) -> Arc<dyn EngineDriver>;

    /// Map the `/metrics` body of an observed (external) engine `name` (`observe.rs`). `None` when
    /// this build has no parser for `kind` (no plugins) or the body has no known metric.
    fn observed_sample(
        &self,
        _kind: gpudeck_protocol::ObservedEngineKind,
        _name: &str,
        _metrics_text: &str,
    ) -> Option<EngineSample> {
        None
    }

    /// An observed engine was removed from the config: drop its scrape history.
    fn forget_observed(&self, _name: &str) {}
}

/// Catalog of generic drivers (no plugins).
pub struct GenericCatalog;

impl EngineCatalog for GenericCatalog {
    fn driver(&self, name: EngineName) -> Arc<dyn EngineDriver> {
        Arc::new(generic::GenericDriver::new(name))
    }
}

/// The default backends for this build.
pub fn default_backends() -> (Arc<dyn DeviceSource>, Arc<dyn EngineCatalog>) {
    #[cfg(feature = "plugins")]
    {
        (
            Arc::new(plugins::PluginDevices::detect()),
            Arc::new(plugins::PluginCatalog::new()),
        )
    }
    #[cfg(not(feature = "plugins"))]
    {
        (Arc::new(NoDevices), Arc::new(GenericCatalog))
    }
}

/// Engine-side admin paths blocked at the proxy for every engine (`protocol.md` §6).
pub const ALWAYS_BLOCKED_PATHS: &[&str] =
    &["/slots", "/models", "/metrics", "/props", "/lora-adapters"];
