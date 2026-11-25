//! `command` (hub -> agent) and `command_result` (agent -> hub).

use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

use crate::common::*;

macro_rules! string_enum_cmd {
    ($name:ident { $($variant:ident => $wire:literal),+ $(,)? }) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
        pub enum $name {
            $(#[serde(rename = $wire)] $variant),+
        }

        impl $name {
            pub fn as_str(&self) -> &'static str {
                match self { $(Self::$variant => $wire),+ }
            }
        }
    };
}
use crate::messages::Settings;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LoadModelArgs {
    pub instance_id: Uuid,
    pub deployment_id: Uuid,
    pub engine: EngineSpec,
    pub model: ModelSpec,
    pub devices: Vec<DeviceAssignment>,
    /// 0 = agent picks a free port in `engine.port_range`.
    pub port: u16,
    #[serde(default)]
    pub engine_process_id: Option<Uuid>,
    pub launch: LaunchSpec,
    #[serde(default)]
    pub draft_model: Option<ModelSpec>,
    #[serde(default = "LoadModelArgs::default_health_timeout")]
    pub health_timeout_seconds: u64,
}

impl LoadModelArgs {
    fn default_health_timeout() -> u64 {
        600
    }

    /// Copy without secrets (safe to persist).
    pub fn redacted(&self) -> Self {
        Self {
            model: self.model.redacted(),
            draft_model: self.draft_model.as_ref().map(ModelSpec::redacted),
            ..self.clone()
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UnloadModelArgs {
    pub instance_id: Uuid,
    #[serde(default)]
    pub force: bool,
    #[serde(default = "default_120")]
    pub drain_timeout_seconds: u64,
    #[serde(default = "default_15")]
    pub kill_grace_seconds: u64,
    #[serde(default)]
    pub stop_engine_if_empty: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DrainInstanceArgs {
    pub instance_id: Uuid,
    #[serde(default = "default_120")]
    pub timeout_seconds: u64,
    #[serde(default = "crate::common::default_true")]
    pub wait_for_zero: bool,
    #[serde(default)]
    pub resume: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StartEngineArgs {
    pub engine_process_id: Uuid,
    pub engine: EngineSpec,
    pub devices: Vec<DeviceAssignment>,
    pub port: u16,
    pub launch: LaunchSpec,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StopEngineArgs {
    pub engine_process_id: Uuid,
    #[serde(default)]
    pub force: bool,
    #[serde(default = "default_120")]
    pub drain_timeout_seconds: u64,
    #[serde(default = "default_15")]
    pub kill_grace_seconds: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RestartInstanceArgs {
    pub instance_id: Uuid,
    #[serde(default = "default_120")]
    pub drain_timeout_seconds: u64,
    #[serde(default)]
    pub force: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PrefetchModelArgs {
    pub model_version_id: Uuid,
    pub files: Vec<ModelFileRef>,
    pub cache_dir: String,
    #[serde(default)]
    pub source: Option<ModelSource>,
    #[serde(default)]
    pub pin: bool,
    #[serde(default)]
    pub pin_reason: Option<String>,
    #[serde(default)]
    pub allow_pickle: bool,
    #[serde(default)]
    pub max_bandwidth_bytes_per_s: Option<u64>,
}

impl PrefetchModelArgs {
    pub fn redacted(&self) -> Self {
        Self {
            source: self.source.as_ref().map(ModelSource::redacted),
            ..self.clone()
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct EvictCacheArgs {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_version_id: Option<Uuid>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_file_ids: Option<Vec<Uuid>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unpinned_older_than_seconds: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_free_bytes: Option<u64>,
    #[serde(default)]
    pub unpin: bool,
    #[serde(default)]
    pub force: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BenchmarkWorkload {
    #[serde(default = "BenchmarkWorkload::default_input")]
    pub input_tokens: u64,
    #[serde(default = "BenchmarkWorkload::default_output")]
    pub output_tokens: u64,
    #[serde(default = "BenchmarkWorkload::default_requests")]
    pub requests_per_level: u64,
    #[serde(default = "BenchmarkWorkload::default_dataset")]
    pub dataset: String,
    #[serde(default)]
    pub prompts: Vec<String>,
    #[serde(default = "crate::common::default_true")]
    pub stream: bool,
}

impl BenchmarkWorkload {
    fn default_input() -> u64 {
        512
    }
    fn default_output() -> u64 {
        128
    }
    fn default_requests() -> u64 {
        32
    }
    fn default_dataset() -> String {
        "synthetic".into()
    }
}

impl Default for BenchmarkWorkload {
    fn default() -> Self {
        Self {
            input_tokens: Self::default_input(),
            output_tokens: Self::default_output(),
            requests_per_level: Self::default_requests(),
            dataset: Self::default_dataset(),
            prompts: vec![],
            stream: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct TargetSlo {
    #[serde(default)]
    pub ttft_ms_p95: Option<f64>,
    #[serde(default)]
    pub e2e_ms_p95: Option<f64>,
    #[serde(default)]
    pub tpot_ms_p95: Option<f64>,
    #[serde(default)]
    pub min_generation_tps: Option<f64>,
    #[serde(default = "TargetSlo::default_error_rate")]
    pub max_error_rate_pct: f64,
}

impl TargetSlo {
    fn default_error_rate() -> f64 {
        1.0
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct AutoTune {
    #[serde(default)]
    pub flag_variants: Vec<JsonObject>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunBenchmarkArgs {
    pub benchmark_run_id: Uuid,
    pub instance_id: Uuid,
    pub port: u16,
    pub served_model_name: String,
    pub model_type: ModelType,
    #[serde(default = "RunBenchmarkArgs::default_endpoint")]
    pub endpoint: String,
    pub concurrency_levels: Vec<u32>,
    #[serde(default)]
    pub workload: BenchmarkWorkload,
    pub duration_seconds: u64,
    #[serde(default)]
    pub target_slo: TargetSlo,
    #[serde(default)]
    pub auto_tune: Option<AutoTune>,
}

impl RunBenchmarkArgs {
    fn default_endpoint() -> String {
        "/v1/chat/completions".into()
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct UpdateConfigArgs {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub settings: Option<Settings>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RotateCertArgs {
    #[serde(default = "RotateCertArgs::default_key_type")]
    pub key_type: KeyType,
    #[serde(default)]
    pub reason: Option<String>,
}

impl RotateCertArgs {
    fn default_key_type() -> KeyType {
        KeyType::EcdsaP256
    }
}

impl Default for RotateCertArgs {
    fn default() -> Self {
        Self {
            key_type: Self::default_key_type(),
            reason: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InstallCertArgs {
    pub certificate_pem: String,
    pub ca_bundle_pem: String,
    pub serial: String,
    pub not_after: Timestamp,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CancelArgs {
    pub target_command_id: Uuid,
}

// ---------------------------------------------------------------------------
// scan_path / hash_files (agent >= 1.1.0, protocol.md §11)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScanPathArgs {
    pub path: String,
    #[serde(default = "crate::common::default_true")]
    pub recursive: bool,
    #[serde(default = "ScanPathArgs::default_depth")]
    pub max_depth: u32,
    #[serde(default = "ScanPathArgs::default_entries")]
    pub max_entries: u64,
    #[serde(default = "crate::common::default_true")]
    pub read_metadata: bool,
}

impl ScanPathArgs {
    fn default_depth() -> u32 {
        4
    }
    fn default_entries() -> u64 {
        20_000
    }

    pub fn new(path: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            recursive: true,
            max_depth: 4,
            max_entries: 20_000,
            read_metadata: true,
        }
    }
}

string_enum_cmd!(ScanKind { Plain => "plain", OllamaBlob => "ollama_blob", Lmstudio => "lmstudio", HfCache => "hf_cache", MlxDir => "mlx_dir" });

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScanFile {
    pub path: String,
    pub absolute_path: String,
    pub size_bytes: u64,
    pub format: ModelFileFormat,
    #[serde(default)]
    pub sha256: Option<String>,
    #[serde(default = "crate::common::default_true")]
    pub is_required: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScanCandidate {
    pub path: String,
    pub kind: ScanKind,
    pub format: ModelFileFormat,
    pub files: Vec<ScanFile>,
    pub total_size_bytes: u64,
    #[serde(default)]
    pub suggested: JsonObject,
    #[serde(default)]
    pub metadata: JsonObject,
}

/// `command.json#/$defs/ScanPathResult`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScanPathResult {
    pub path: String,
    pub candidates: Vec<ScanCandidate>,
    pub truncated: bool,
    #[serde(default)]
    pub visited: u64,
    #[serde(default)]
    pub seconds: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HashFilesArgs {
    #[serde(default)]
    pub root: Option<String>,
    pub paths: Vec<String>,
    #[serde(default = "HashFilesArgs::default_algorithm")]
    pub algorithm: String,
    #[serde(default = "HashFilesArgs::default_interval")]
    pub progress_interval_seconds: f64,
}

impl HashFilesArgs {
    fn default_algorithm() -> String {
        "sha256".into()
    }
    fn default_interval() -> f64 {
        2.0
    }

    pub fn new(root: Option<String>, paths: Vec<String>) -> Self {
        Self {
            root,
            paths,
            algorithm: Self::default_algorithm(),
            progress_interval_seconds: Self::default_interval(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HashedFile {
    pub path: String,
    pub sha256: Option<String>,
    pub size_bytes: Option<u64>,
    #[serde(default)]
    pub error: Option<ErrorInfo>,
}

/// `command.json#/$defs/HashFilesResult`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HashFilesResult {
    #[serde(default)]
    pub root: Option<String>,
    pub files: Vec<HashedFile>,
    pub hashes: std::collections::BTreeMap<String, String>,
    pub bytes_hashed: u64,
    #[serde(default)]
    pub seconds: f64,
}

// ---------------------------------------------------------------------------
// SSH GPU instances (agent >= 1.1.0, protocol.md §8)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct SshInstanceLimits {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub vram_bytes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compute_pct: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cpu_cores: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub memory_bytes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub shm_bytes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pids: Option<u64>,
}

string_enum_cmd!(SshVolumeType { Volume => "volume", Bind => "bind", Tmpfs => "tmpfs" });

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SshVolume {
    #[serde(default = "SshVolume::default_type", rename = "type")]
    pub kind: SshVolumeType,
    #[serde(default)]
    pub source: Option<String>,
    pub target: String,
    #[serde(default)]
    pub read_only: bool,
    #[serde(default)]
    pub keep: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub size_bytes: Option<u64>,
}

impl SshVolume {
    fn default_type() -> SshVolumeType {
        SshVolumeType::Volume
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StartSshInstanceArgs {
    pub ssh_instance_id: Uuid,
    #[serde(default)]
    pub name: Option<String>,
    pub image: String,
    #[serde(default)]
    pub devices: Vec<DeviceAssignment>,
    #[serde(default)]
    pub limits: SshInstanceLimits,
    pub authorized_keys: Vec<String>,
    #[serde(default = "StartSshInstanceArgs::default_user")]
    pub ssh_user: String,
    #[serde(default)]
    pub ssh_port: u16,
    #[serde(default = "StartSshInstanceArgs::default_container_port")]
    pub container_port: u16,
    #[serde(default)]
    pub bind_address: Option<String>,
    #[serde(default)]
    pub allowed_source_cidrs: Vec<String>,
    #[serde(default)]
    pub env: Labels,
    #[serde(default)]
    pub volumes: Vec<SshVolume>,
    #[serde(default)]
    pub ttl_seconds: Option<u64>,
    #[serde(default)]
    pub labels: Labels,
    #[serde(default = "StartSshInstanceArgs::default_health_timeout")]
    pub health_timeout_seconds: u64,
}

impl StartSshInstanceArgs {
    fn default_user() -> String {
        "gpudeck".into()
    }
    fn default_container_port() -> u16 {
        22
    }
    fn default_health_timeout() -> u64 {
        180
    }

    pub fn new(
        ssh_instance_id: Uuid,
        image: impl Into<String>,
        authorized_keys: Vec<String>,
    ) -> Self {
        Self {
            ssh_instance_id,
            name: None,
            image: image.into(),
            devices: vec![],
            limits: SshInstanceLimits::default(),
            authorized_keys,
            ssh_user: Self::default_user(),
            ssh_port: 0,
            container_port: Self::default_container_port(),
            bind_address: None,
            allowed_source_cidrs: vec![],
            env: Labels::new(),
            volumes: vec![],
            ttl_seconds: None,
            labels: Labels::new(),
            health_timeout_seconds: Self::default_health_timeout(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StopSshInstanceArgs {
    pub ssh_instance_id: Uuid,
    #[serde(default)]
    pub force: bool,
    #[serde(default = "StopSshInstanceArgs::default_grace")]
    pub kill_grace_seconds: u64,
    #[serde(default = "crate::common::default_true")]
    pub remove_volumes: bool,
}

impl StopSshInstanceArgs {
    fn default_grace() -> u64 {
        10
    }

    pub fn new(ssh_instance_id: Uuid) -> Self {
        Self {
            ssh_instance_id,
            force: false,
            kill_grace_seconds: 10,
            remove_volumes: true,
        }
    }
}

/// `stage_update` (`protocol.md` §14): download + verify + stage a release binary.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StageUpdateArgs {
    pub version: String,
    pub target: String,
    pub manifest: crate::common::SignedReleaseManifest,
    pub download_token: String,
    #[serde(default)]
    pub allow_downgrade: bool,
}

/// `apply_update` (`protocol.md` §14): swap to the staged version, restart, health check,
/// roll back on failure.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ApplyUpdateArgs {
    pub version: String,
    #[serde(default = "ApplyUpdateArgs::default_health_timeout")]
    pub health_timeout_seconds: u64,
    #[serde(default)]
    pub run_id: Option<Uuid>,
}

impl ApplyUpdateArgs {
    pub fn default_health_timeout() -> u64 {
        120
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SshHostKey {
    #[serde(rename = "type")]
    pub key_type: String,
    pub fingerprint: String,
    #[serde(default)]
    pub public_key: Option<String>,
}

/// `command.json#/$defs/SshInstanceResult`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SshInstanceResult {
    pub ssh_instance_id: Uuid,
    #[serde(default)]
    pub container_id: Option<String>,
    pub host_address: String,
    pub host_port: u16,
    pub ssh_user: String,
    pub host_key_fingerprint: String,
    #[serde(default)]
    pub host_keys: Vec<SshHostKey>,
    #[serde(default)]
    pub ssh_command: Option<String>,
    #[serde(default)]
    pub device_ids: Vec<Uuid>,
    #[serde(default)]
    pub gpu_attached: bool,
    #[serde(default)]
    pub started_at: Option<Timestamp>,
    #[serde(default)]
    pub expires_at: Option<Timestamp>,
    #[serde(default)]
    pub warnings: Vec<String>,
}

fn default_120() -> u64 {
    120
}

fn default_15() -> u64 {
    15
}

/// The command discriminator (`command`) plus its typed `args`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "command", content = "args", rename_all = "snake_case")]
pub enum CommandKind {
    LoadModel(LoadModelArgs),
    UnloadModel(UnloadModelArgs),
    DrainInstance(DrainInstanceArgs),
    StartEngine(StartEngineArgs),
    StopEngine(StopEngineArgs),
    RestartInstance(RestartInstanceArgs),
    PrefetchModel(PrefetchModelArgs),
    EvictCache(EvictCacheArgs),
    RunBenchmark(RunBenchmarkArgs),
    UpdateConfig(UpdateConfigArgs),
    RotateCert(RotateCertArgs),
    InstallCert(InstallCertArgs),
    Cancel(CancelArgs),
    ScanPath(ScanPathArgs),
    HashFiles(HashFilesArgs),
    StartSshInstance(StartSshInstanceArgs),
    StopSshInstance(StopSshInstanceArgs),
    StageUpdate(StageUpdateArgs),
    ApplyUpdate(ApplyUpdateArgs),
}

/// Command names as they appear on the wire (also used in `command_result.command`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CommandName {
    LoadModel,
    UnloadModel,
    DrainInstance,
    StartEngine,
    StopEngine,
    RestartInstance,
    PrefetchModel,
    EvictCache,
    RunBenchmark,
    UpdateConfig,
    RotateCert,
    InstallCert,
    Cancel,
    ScanPath,
    HashFiles,
    StartSshInstance,
    StopSshInstance,
    StageUpdate,
    ApplyUpdate,
}

impl CommandName {
    /// Every command, in schema order (`hello.capabilities.commands`).
    pub const ALL: &'static [CommandName] = &[
        Self::LoadModel,
        Self::UnloadModel,
        Self::DrainInstance,
        Self::StartEngine,
        Self::StopEngine,
        Self::RestartInstance,
        Self::PrefetchModel,
        Self::EvictCache,
        Self::RunBenchmark,
        Self::UpdateConfig,
        Self::RotateCert,
        Self::InstallCert,
        Self::Cancel,
        Self::ScanPath,
        Self::HashFiles,
        Self::StartSshInstance,
        Self::StopSshInstance,
        Self::StageUpdate,
        Self::ApplyUpdate,
    ];
}

impl CommandKind {
    pub fn name(&self) -> CommandName {
        match self {
            Self::LoadModel(_) => CommandName::LoadModel,
            Self::UnloadModel(_) => CommandName::UnloadModel,
            Self::DrainInstance(_) => CommandName::DrainInstance,
            Self::StartEngine(_) => CommandName::StartEngine,
            Self::StopEngine(_) => CommandName::StopEngine,
            Self::RestartInstance(_) => CommandName::RestartInstance,
            Self::PrefetchModel(_) => CommandName::PrefetchModel,
            Self::EvictCache(_) => CommandName::EvictCache,
            Self::RunBenchmark(_) => CommandName::RunBenchmark,
            Self::UpdateConfig(_) => CommandName::UpdateConfig,
            Self::RotateCert(_) => CommandName::RotateCert,
            Self::InstallCert(_) => CommandName::InstallCert,
            Self::Cancel(_) => CommandName::Cancel,
            Self::ScanPath(_) => CommandName::ScanPath,
            Self::HashFiles(_) => CommandName::HashFiles,
            Self::StartSshInstance(_) => CommandName::StartSshInstance,
            Self::StopSshInstance(_) => CommandName::StopSshInstance,
            Self::StageUpdate(_) => CommandName::StageUpdate,
            Self::ApplyUpdate(_) => CommandName::ApplyUpdate,
        }
    }

    /// The instance this command touches (serialization key), if any.
    pub fn instance_id(&self) -> Option<Uuid> {
        match self {
            Self::LoadModel(a) => Some(a.instance_id),
            Self::UnloadModel(a) => Some(a.instance_id),
            Self::DrainInstance(a) => Some(a.instance_id),
            Self::RestartInstance(a) => Some(a.instance_id),
            Self::RunBenchmark(a) => Some(a.instance_id),
            _ => None,
        }
    }

    /// The shared engine process this command touches (serialization key), if any.
    pub fn engine_process_id(&self) -> Option<Uuid> {
        match self {
            Self::LoadModel(a) => a.engine_process_id,
            Self::StartEngine(a) => Some(a.engine_process_id),
            Self::StopEngine(a) => Some(a.engine_process_id),
            _ => None,
        }
    }

    /// The SSH instance this command touches (serialization key), if any.
    pub fn ssh_instance_id(&self) -> Option<Uuid> {
        match self {
            Self::StartSshInstance(a) => Some(a.ssh_instance_id),
            Self::StopSshInstance(a) => Some(a.ssh_instance_id),
            _ => None,
        }
    }

    /// Copy with secrets stripped.
    pub fn redacted(&self) -> Self {
        match self {
            Self::LoadModel(a) => Self::LoadModel(a.redacted()),
            Self::PrefetchModel(a) => Self::PrefetchModel(a.redacted()),
            Self::StageUpdate(a) => {
                let mut a = a.clone();
                a.download_token = "***".into();
                a.manifest.manifest = format!("<{} base64 chars>", a.manifest.manifest.len());
                Self::StageUpdate(a)
            }
            other => other.clone(),
        }
    }
}

impl std::fmt::Display for CommandName {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = serde_json::to_value(self)
            .ok()
            .and_then(|v| v.as_str().map(str::to_owned));
        f.write_str(s.as_deref().unwrap_or("?"))
    }
}

/// `command` payload.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Command {
    #[serde(default = "Command::default_timeout")]
    pub timeout_seconds: u64,
    #[serde(default)]
    pub swap_operation_id: Option<Uuid>,
    #[serde(flatten)]
    pub kind: CommandKind,
}

impl Command {
    fn default_timeout() -> u64 {
        900
    }

    pub fn new(kind: CommandKind) -> Self {
        Self {
            timeout_seconds: Self::default_timeout(),
            swap_operation_id: None,
            kind,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResultStatus {
    Accepted,
    Running,
    Succeeded,
    Failed,
    Rejected,
    Cancelled,
}

impl ResultStatus {
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            Self::Succeeded | Self::Failed | Self::Rejected | Self::Cancelled
        )
    }
}

/// `command_result` payload (`envelope.correlation_id` = command id).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandResult {
    pub command: CommandName,
    pub status: ResultStatus,
    #[serde(default)]
    pub progress_pct: Option<f64>,
    #[serde(default)]
    pub progress_message: Option<String>,
    #[serde(default)]
    pub result: Option<JsonObject>,
    #[serde(default)]
    pub error: Option<ErrorInfo>,
    #[serde(default)]
    pub started_at: Option<Timestamp>,
    #[serde(default)]
    pub finished_at: Option<Timestamp>,
    #[serde(default)]
    pub instance_id: Option<Uuid>,
}

impl CommandResult {
    fn base(command: CommandName, status: ResultStatus) -> Self {
        Self {
            command,
            status,
            progress_pct: None,
            progress_message: None,
            result: None,
            error: None,
            started_at: None,
            finished_at: None,
            instance_id: None,
        }
    }

    pub fn accepted(command: CommandName) -> Self {
        Self {
            started_at: Some(crate::now()),
            ..Self::base(command, ResultStatus::Accepted)
        }
    }

    pub fn running(command: CommandName, pct: Option<f64>, message: impl Into<String>) -> Self {
        let mut msg: String = message.into();
        if msg.len() > 512 {
            msg.truncate(512);
        }
        Self {
            progress_pct: pct.map(|p| p.clamp(0.0, 100.0)),
            progress_message: Some(msg),
            ..Self::base(command, ResultStatus::Running)
        }
    }

    pub fn succeeded(command: CommandName, result: Value) -> Self {
        let result = match result {
            Value::Object(m) => Some(m),
            Value::Null => Some(JsonObject::new()),
            other => {
                let mut m = JsonObject::new();
                m.insert("value".into(), other);
                Some(m)
            }
        };
        Self {
            result,
            progress_pct: Some(100.0),
            finished_at: Some(crate::now()),
            ..Self::base(command, ResultStatus::Succeeded)
        }
    }

    pub fn failed(command: CommandName, error: ErrorInfo) -> Self {
        Self {
            error: Some(error),
            finished_at: Some(crate::now()),
            ..Self::base(command, ResultStatus::Failed)
        }
    }

    pub fn rejected(command: CommandName, error: ErrorInfo) -> Self {
        Self {
            error: Some(error),
            finished_at: Some(crate::now()),
            ..Self::base(command, ResultStatus::Rejected)
        }
    }

    pub fn cancelled(command: CommandName) -> Self {
        Self {
            error: Some(ErrorInfo::new(error_code::CANCELLED, "cancelled")),
            finished_at: Some(crate::now()),
            ..Self::base(command, ResultStatus::Cancelled)
        }
    }

    pub fn with_instance(mut self, instance_id: Uuid) -> Self {
        self.instance_id = Some(instance_id);
        self
    }

    pub fn with_started_at(mut self, ts: Timestamp) -> Self {
        self.started_at = Some(ts);
        self
    }
}
