//! Message payloads: `hello`, `hello_ack`, `heartbeat`, `inventory`, `metrics_batch`, `event`,
//! `config_push`. Commands live in [`crate::command`].

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::common::*;

// ---------------------------------------------------------------------------
// hello (agent -> hub)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EngineCapability {
    pub name: EngineName,
    #[serde(default)]
    pub versions: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub runtime: Option<EngineRuntime>,
    #[serde(default)]
    pub path: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KubernetesInfo {
    pub node_name: String,
    pub namespace: String,
    pub pod: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CloudInfo {
    pub provider: String,
    pub instance_id: String,
    pub instance_type: String,
    pub region: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Capabilities {
    pub engines: Vec<EngineCapability>,
    pub runtimes: Vec<EngineRuntime>,
    #[serde(default)]
    pub container_runtime: Option<String>,
    /// Linux: host glibc (`"2.35"`); None on macOS / musl hosts.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub glibc_version: Option<String>,
    #[serde(default)]
    pub cuda_version: Option<String>,
    #[serde(default)]
    pub rocm_version: Option<String>,
    #[serde(default)]
    pub musa_version: Option<String>,
    #[serde(default)]
    pub metal: bool,
    #[serde(default = "Capabilities::default_egress")]
    pub egress_lock: EgressLockKind,
    #[serde(default)]
    pub kubernetes: Option<KubernetesInfo>,
    #[serde(default)]
    pub cloud: Option<CloudInfo>,
    /// Commands this build implements (agent >= 1.1.0).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub commands: Vec<crate::command::CommandName>,
    /// `start_ssh_instance` can run here (container runtime reachable).
    #[serde(default)]
    pub ssh_instances: bool,
}

impl Capabilities {
    fn default_egress() -> EgressLockKind {
        EgressLockKind::None
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CacheSummary {
    pub dir: String,
    pub limit_bytes: u64,
    pub used_bytes: u64,
    #[serde(default)]
    pub free_bytes: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProxyInfo {
    pub port: u16,
    #[serde(default)]
    pub address: Option<String>,
    #[serde(default)]
    pub cert_fingerprint_sha256: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Hello {
    pub agent_version: String,
    pub protocol_version: u32,
    pub os: NodeOs,
    #[serde(default)]
    pub os_version: Option<String>,
    pub arch: NodeArch,
    #[serde(default)]
    pub kernel_version: Option<String>,
    pub hostname: String,
    #[serde(default)]
    pub ip_addresses: Vec<String>,
    #[serde(default)]
    pub cpu_cores: Option<u64>,
    #[serde(default)]
    pub memory_total_bytes: Option<u64>,
    #[serde(default)]
    pub boot_id: Option<String>,
    pub started_at: Timestamp,
    pub capabilities: Capabilities,
    pub cache: CacheSummary,
    pub proxy: ProxyInfo,
    #[serde(default)]
    pub config_version: u64,
    #[serde(default)]
    pub labels: Labels,
    /// Observed (external) engines (added 2026-11-23); sent only to hubs that listed the
    /// `observed_engines` feature on an earlier `hello_ack`.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub observed_engines: Vec<ObservedEngine>,
}

// ---------------------------------------------------------------------------
// hello_ack (hub -> agent)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Intervals {
    #[serde(default = "Intervals::default_heartbeat")]
    pub heartbeat_seconds: u64,
    #[serde(default = "Intervals::default_metrics")]
    pub metrics_seconds: f64,
    #[serde(default = "Intervals::default_inventory")]
    pub inventory_seconds: u64,
    #[serde(default = "Intervals::default_ack_timeout")]
    pub command_ack_timeout_seconds: u64,
}

impl Intervals {
    fn default_heartbeat() -> u64 {
        10
    }
    fn default_metrics() -> f64 {
        2.0
    }
    fn default_inventory() -> u64 {
        60
    }
    fn default_ack_timeout() -> u64 {
        10
    }
}

impl Default for Intervals {
    fn default() -> Self {
        Self {
            heartbeat_seconds: Self::default_heartbeat(),
            metrics_seconds: Self::default_metrics(),
            inventory_seconds: Self::default_inventory(),
            command_ack_timeout_seconds: Self::default_ack_timeout(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HelloAck {
    pub accepted: bool,
    #[serde(default)]
    pub reason: Option<String>,
    pub server_time: Timestamp,
    pub node_id: Uuid,
    #[serde(default)]
    pub node_name: Option<String>,
    #[serde(default)]
    pub cluster_id: Option<Uuid>,
    pub intervals: Intervals,
    pub config_version: u64,
    #[serde(default)]
    pub min_agent_version: Option<String>,
    #[serde(default)]
    pub drain: bool,
    /// Optional protocol features the hub accepts (added 2026-11-23): see [`hub_feature`].
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub features: Vec<String>,
}

/// `hello_ack.features` names.
pub mod hub_feature {
    /// `hello/inventory.observed_engines`, `EngineSample.observed`.
    pub const OBSERVED_ENGINES: &str = "observed_engines";
    /// `GpuSample.processes`.
    pub const GPU_PROCESSES: &str = "gpu_processes";
}

// ---------------------------------------------------------------------------
// heartbeat (agent -> hub)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HeartbeatInstance {
    pub instance_id: Uuid,
    pub state: InstanceState,
    pub health: InstanceHealth,
    #[serde(default)]
    pub active_sessions: Option<u64>,
    #[serde(default)]
    pub waiting_sessions: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct NodeLoad {
    pub cpu_pct: Option<f64>,
    pub memory_used_bytes: Option<u64>,
    pub cache_used_bytes: Option<u64>,
    pub disk_free_bytes: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Heartbeat {
    pub uptime_seconds: u64,
    pub config_version: u64,
    pub instances: Vec<HeartbeatInstance>,
    #[serde(default)]
    pub running_commands: Vec<Uuid>,
    #[serde(default)]
    pub load: Option<NodeLoad>,
}

// ---------------------------------------------------------------------------
// inventory (agent -> hub)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CacheInventory {
    pub dir: String,
    pub limit_bytes: u64,
    pub used_bytes: u64,
    pub items: Vec<CacheItem>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EngineProcessInfo {
    pub engine_process_id: Uuid,
    pub engine: EngineName,
    #[serde(default)]
    pub engine_version_id: Option<Uuid>,
    pub port: u16,
    pub pid: Option<u32>,
    #[serde(default)]
    pub device_ids: Vec<Uuid>,
    #[serde(default)]
    pub instance_ids: Vec<Uuid>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct SystemInfo {
    pub cpu_cores: Option<u64>,
    pub memory_total_bytes: Option<u64>,
    pub memory_used_bytes: Option<u64>,
    pub load_avg_1m: Option<f64>,
    pub uptime_seconds: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Inventory {
    pub devices: Vec<Device>,
    pub disks: Vec<Disk>,
    pub cache: CacheInventory,
    pub instances: Vec<InstanceStatus>,
    #[serde(default)]
    pub engine_processes: Vec<EngineProcessInfo>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub ssh_instances: Vec<SshInstanceStatus>,
    /// Observed (external) engines (added 2026-11-23), authoritative for this node.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub observed_engines: Vec<ObservedEngine>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub system: Option<SystemInfo>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub config_version: Option<u64>,
}

// ---------------------------------------------------------------------------
// metrics_batch (agent -> hub)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct AgentMetrics {
    pub cpu_pct: Option<f64>,
    pub rss_bytes: Option<u64>,
    pub proxy_active_connections: Option<u64>,
    pub proxy_requests_total: Option<u64>,
    pub proxy_errors_total: Option<u64>,
    pub scrape_errors_total: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MetricsBatch {
    pub interval_seconds: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sequence: Option<u64>,
    pub gpu: Vec<GpuSample>,
    pub engine: Vec<EngineSample>,
    pub sessions: Vec<SessionSample>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent: Option<AgentMetrics>,
}

// ---------------------------------------------------------------------------
// event (agent -> hub)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventKind {
    AgentStarted,
    AgentStopping,
    EngineStarted,
    EngineStopped,
    EngineCrashed,
    InstanceStateChanged,
    InstanceHealthChanged,
    DeviceHealthChanged,
    DeviceError,
    CacheChanged,
    CachePressure,
    DrainProgress,
    DrainCompleted,
    BenchmarkProgress,
    ConfigApplied,
    ConfigRejected,
    CertRotated,
    CertExpiring,
    EgressBlocked,
    SshInstanceStateChanged,
    Log,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Severity {
    Info,
    Warning,
    Critical,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StateChange {
    pub from: Option<String>,
    pub to: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DrainInfo {
    pub active_sessions: u64,
    pub waiting_sessions: u64,
    pub elapsed_seconds: f64,
    #[serde(default)]
    pub timed_out: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ExitInfo {
    pub code: Option<i32>,
    pub signal: Option<String>,
    #[serde(default)]
    pub oom: bool,
    #[serde(default)]
    pub last_lines: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LogSource {
    Engine,
    Agent,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LogBatch {
    pub source: LogSource,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stream: Option<LogStream>,
    pub lines: Vec<LogLine>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Event {
    pub kind: EventKind,
    pub severity: Severity,
    pub message: String,
    #[serde(default)]
    pub instance_id: Option<Uuid>,
    #[serde(default)]
    pub deployment_id: Option<Uuid>,
    #[serde(default)]
    pub engine_process_id: Option<Uuid>,
    #[serde(default)]
    pub device_id: Option<Uuid>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ssh_instance_id: Option<Uuid>,
    #[serde(default)]
    pub command_id: Option<Uuid>,
    #[serde(default)]
    pub state: Option<StateChange>,
    #[serde(default)]
    pub drain: Option<DrainInfo>,
    #[serde(default)]
    pub cache_item: Option<CacheItem>,
    #[serde(default)]
    pub exit: Option<ExitInfo>,
    #[serde(default)]
    pub log: Option<LogBatch>,
    #[serde(default)]
    pub details: Option<JsonObject>,
}

impl Event {
    pub fn new(kind: EventKind, severity: Severity, message: impl Into<String>) -> Self {
        let mut message: String = message.into();
        if message.len() > 2048 {
            message.truncate(2048);
        }
        Self {
            kind,
            severity,
            message,
            instance_id: None,
            deployment_id: None,
            engine_process_id: None,
            device_id: None,
            ssh_instance_id: None,
            command_id: None,
            state: None,
            drain: None,
            cache_item: None,
            exit: None,
            log: None,
            details: None,
        }
    }

    pub fn info(kind: EventKind, message: impl Into<String>) -> Self {
        Self::new(kind, Severity::Info, message)
    }

    pub fn warning(kind: EventKind, message: impl Into<String>) -> Self {
        Self::new(kind, Severity::Warning, message)
    }

    pub fn critical(kind: EventKind, message: impl Into<String>) -> Self {
        Self::new(kind, Severity::Critical, message)
    }

    pub fn instance(mut self, instance_id: Uuid) -> Self {
        self.instance_id = Some(instance_id);
        self
    }

    pub fn deployment(mut self, deployment_id: Uuid) -> Self {
        self.deployment_id = Some(deployment_id);
        self
    }

    pub fn engine_process(mut self, id: Uuid) -> Self {
        self.engine_process_id = Some(id);
        self
    }

    pub fn command(mut self, id: Uuid) -> Self {
        self.command_id = Some(id);
        self
    }

    pub fn details(mut self, details: JsonObject) -> Self {
        self.details = Some(details);
        self
    }
}

// ---------------------------------------------------------------------------
// config_push (hub -> agent)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RoutingEntry {
    pub instance_id: Uuid,
    pub deployment_id: Uuid,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_type: Option<ModelType>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub engine: Option<EngineName>,
    pub port: u16,
    pub served_model_name: String,
    #[serde(default = "crate::common::default_true")]
    pub rewrite_model: bool,
    pub allowed_path_prefixes: Vec<String>,
    #[serde(default = "crate::common::default_true")]
    pub accepting: bool,
    #[serde(default)]
    pub max_concurrent: Option<u32>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct Crl {
    pub generated_at: Option<Timestamp>,
    #[serde(default)]
    pub serials: Vec<String>,
    #[serde(default)]
    pub fingerprints_sha256: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ProxySettings {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub allowed_client_subjects: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub queue_timeout_seconds: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request_timeout_seconds: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_body_bytes: Option<u64>,
}

/// `config_push.settings` / `update_config.args.settings`: only present keys change.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct Settings {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub heartbeat_interval_seconds: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metrics_interval_seconds: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub inventory_interval_seconds: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_level: Option<LogLevel>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_forward: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_chunk_max_lines: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_flush_interval_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cache_limit_bytes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cache_min_free_bytes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub engine_port_range: Option<[u16; 2]>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proxy: Option<ProxySettings>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub drain_default_timeout_seconds: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub health_default_interval_seconds: Option<f64>,
}

impl Settings {
    /// Overlay `other` on `self`: present keys in `other` win.
    pub fn merge(&mut self, other: &Settings) {
        macro_rules! take {
            ($($f:ident),+) => { $( if other.$f.is_some() { self.$f = other.$f.clone(); } )+ };
        }
        take!(
            heartbeat_interval_seconds,
            metrics_interval_seconds,
            inventory_interval_seconds,
            log_level,
            log_forward,
            log_chunk_max_lines,
            log_flush_interval_ms,
            cache_limit_bytes,
            cache_min_free_bytes,
            engine_port_range,
            drain_default_timeout_seconds,
            health_default_interval_seconds
        );
        if let Some(p) = &other.proxy {
            let mine = self.proxy.get_or_insert_with(Default::default);
            if p.allowed_client_subjects.is_some() {
                mine.allowed_client_subjects = p.allowed_client_subjects.clone();
            }
            if p.queue_timeout_seconds.is_some() {
                mine.queue_timeout_seconds = p.queue_timeout_seconds;
            }
            if p.request_timeout_seconds.is_some() {
                mine.request_timeout_seconds = p.request_timeout_seconds;
            }
            if p.max_body_bytes.is_some() {
                mine.max_body_bytes = p.max_body_bytes;
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ModelSources {
    #[serde(default)]
    pub huggingface_endpoint: Option<String>,
    #[serde(default)]
    pub modelscope_endpoint: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConfigPush {
    pub config_version: u64,
    pub full: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub routing: Option<Vec<RoutingEntry>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub egress_allowlist: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub engine_catalog: Option<Vec<EngineSpec>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub crl: Option<Crl>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ca_bundle_pem: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub settings: Option<Settings>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_sources: Option<ModelSources>,
}
