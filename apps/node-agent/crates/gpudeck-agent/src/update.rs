//! Agent self-update (`protocol.md` §14): pre-staged, signature-verified binaries, an atomic swap
//! that keeps the previous binary, a restart that leaves the engines running (they are re-adopted
//! like after any agent restart), a health check and an automatic rollback.
//!
//! - **Stage** (`stage_update`, in the background, ahead of time): the hub sends the release
//!   manifest signed with the release key (ed25519) and a short-lived download token. The agent
//!   verifies the signature with a release key pinned on the node (the CA-signed security policy's
//!   `release.public_keys`, plus `agent.toml` `release_public_keys`), checks free disk space,
//!   downloads `GET <hub_url>/agent/v1/updates/<version>/<target>` from the hub it joined (the
//!   pinned hub connection; never a URL from a message), verifies the sha256 the manifest names,
//!   checks that the binary runs on this host (`version`) and keeps it in
//!   `<state_dir>/update/staged/<version>/`. The running binary is never touched.
//! - **Apply** (`apply_update`): nothing is downloaded. The agent re-verifies the staged binary
//!   and writes `<state_dir>/update/request.json`. The **applier** (`gpudeck-agent update apply`)
//!   then verifies again, against trust anchored outside the agent's writable state when it runs
//!   as root (the release keys of `agent.toml`, and policy keys only from a policy that verifies
//!   against the CA certificate whose fingerprint `agent.toml` pins), copies the binary next to the
//!   installed one, keeps the installed one as `<binary>.prev` (hard link), renames the new one
//!   over it (atomic), restarts the service and waits until the new agent has re-adopted its
//!   engines, reconnected to the hub and written `update/health.json`. Otherwise it renames
//!   `.prev` back, restarts again and reports `rolled_back`.
//! - **Who swaps**: `helper` mode, a root-owned systemd path unit (`gpudeck-agent-update.path`) or
//!   launchd daemon (`io.github.sur950.gpudeck-agent-update`) installed by the installer runs the
//!   applier when `request.json` changes (the binary stays root-owned); `self` mode, the agent
//!   user owns the binary (per-user macOS install) and the agent starts the applier itself;
//!   `managed`, Kubernetes (the DaemonSet image is updated by Helm), never; `unsupported`,
//!   update by re-running the installer.
//! - **Reporting**: progress as `event(agent_update)` phases and the `apply_update` result sent by
//!   whichever agent runs after the restart (the new one, or the previous one after a rollback).

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use base64::Engine as _;
use gpudeck_protocol::{
    error_code, hub_feature, AgentUpdateMode, AgentUpdateState, AgentUpdateStatus, ApplyUpdateArgs,
    CommandName, CommandResult, ErrorInfo, Event, EventKind, JsonObject, ReleaseManifest,
    SecurityPolicy, Severity, SignedReleaseManifest, StageUpdateArgs, Timestamp,
};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tracing::{info, warn};
use uuid::Uuid;

use crate::agent::Agent;
use crate::commands::Ctx;
use crate::config::AgentConfig;
use crate::join::AGENT_VERSION;
use crate::state::StatePaths;

/// Hub path the staged binary is downloaded from (`<hub_url><prefix>/<version>/<target>`).
pub const UPDATE_PATH_PREFIX: &str = "/agent/v1/updates";
/// Header carrying the `stage_update` download token.
pub const TOKEN_HEADER: &str = "x-gpudeck-update-token";
/// Installed by the Linux installers: runs the applier as root when `request.json` changes.
pub const SYSTEMD_HELPER_PATH_UNIT: &str = "/etc/systemd/system/gpudeck-agent-update.path";
/// Installed by the macOS installer (system install): same, as a launchd daemon.
pub const LAUNCHD_HELPER_PLIST: &str =
    "/Library/LaunchDaemons/io.github.sur950.gpudeck-agent-update.plist";
/// Free space kept on top of two copies of the binary (staged + the copy next to the binary).
const DISK_MARGIN_BYTES: u64 = 64 << 20;
/// A request older than this is not applied (and no longer blocks a new one).
const REQUEST_STALE_SECONDS: i64 = 1800;
/// The update helper must answer a request (status.json) within this long.
const HELPER_PICKUP_SECONDS: u64 = 30;
/// Largest JSON file read from the update directory.
const MAX_JSON_BYTES: u64 = 1 << 20;
/// Largest staged binary accepted.
const MAX_BINARY_BYTES: u64 = 512 << 20;

// ---------------------------------------------------------------------------
// Versions and targets
// ---------------------------------------------------------------------------

/// The version this process reports for updates. Debug builds honour
/// `GPUDECK_AGENT_TEST_VERSION` (end-to-end tests run one build as two versions); release builds
/// always report the compiled version.
pub fn current_version() -> String {
    #[cfg(debug_assertions)]
    if let Ok(v) = std::env::var("GPUDECK_AGENT_TEST_VERSION") {
        if is_semver(&v) {
            return v;
        }
    }
    AGENT_VERSION.to_string()
}

/// Rust target triple of this build (the release artifact names use it).
pub fn build_target() -> String {
    let arch = if cfg!(target_arch = "x86_64") {
        "x86_64"
    } else if cfg!(target_arch = "aarch64") {
        "aarch64"
    } else {
        std::env::consts::ARCH
    };
    if cfg!(target_os = "macos") {
        format!("{arch}-apple-darwin")
    } else if cfg!(target_os = "linux") {
        let env = if cfg!(target_env = "musl") {
            "musl"
        } else {
            "gnu"
        };
        format!("{arch}-unknown-linux-{env}")
    } else {
        format!("{arch}-unknown-{}", std::env::consts::OS)
    }
}

/// `MAJOR.MINOR.PATCH[-pre]` (what release tags and manifests use).
pub fn is_semver(v: &str) -> bool {
    version_key(v).is_some()
}

fn version_key(v: &str) -> Option<(u64, u64, u64, bool, String)> {
    let v = v.trim().trim_start_matches('v');
    let (core, pre) = match v.split_once('-') {
        Some((c, p)) => (c, Some(p)),
        None => (v, None),
    };
    let mut it = core.split('.');
    let maj = it.next()?.parse().ok()?;
    let min = it.next()?.parse().ok()?;
    let pat = it.next()?.parse().ok()?;
    if it.next().is_some() {
        return None;
    }
    if let Some(p) = pre {
        if p.is_empty()
            || !p
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
        {
            return None;
        }
    }
    // A release sorts after its pre-releases.
    Some((maj, min, pat, pre.is_none(), pre.unwrap_or("").to_string()))
}

/// Semver order (`None` when either side is not a version).
pub fn compare_versions(a: &str, b: &str) -> Option<std::cmp::Ordering> {
    Some(version_key(a)?.cmp(&version_key(b)?))
}

fn safe_target(t: &str) -> bool {
    !t.is_empty()
        && t.len() <= 64
        && t.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-')
        && !t.starts_with('.')
}

// ---------------------------------------------------------------------------
// Release keys and manifest signatures
// ---------------------------------------------------------------------------

fn b64() -> base64::engine::GeneralPurpose {
    base64::engine::general_purpose::STANDARD
}

/// A 32-byte raw ed25519 public key from standard base64.
pub fn decode_public_key(k: &str) -> Result<Vec<u8>> {
    let raw = b64()
        .decode(k.trim())
        .context("release key is not base64")?;
    if raw.len() != 32 {
        bail!("release key must be 32 bytes (ed25519), got {}", raw.len());
    }
    Ok(raw)
}

/// Verify `signed` with any of `keys` and return the manifest (also checks its shape).
pub fn verify_manifest(signed: &SignedReleaseManifest, keys: &[String]) -> Result<ReleaseManifest> {
    if signed.algorithm != "ed25519" {
        bail!(
            "unsupported release signature algorithm {:?}",
            signed.algorithm
        );
    }
    if keys.is_empty() {
        bail!("no release signing key is pinned on this node");
    }
    let doc = b64()
        .decode(signed.manifest.trim())
        .context("manifest is not base64")?;
    let sig = b64()
        .decode(signed.signature.trim())
        .context("signature is not base64")?;
    let ok = keys
        .iter()
        .filter_map(|k| decode_public_key(k).ok())
        .any(|k| {
            ring::signature::UnparsedPublicKey::new(&ring::signature::ED25519, &k)
                .verify(&doc, &sig)
                .is_ok()
        });
    if !ok {
        bail!("the release manifest signature does not verify with a pinned release key");
    }
    let m: ReleaseManifest =
        serde_json::from_slice(&doc).context("the signed document is not a release manifest")?;
    if m.schema != 1 || m.product != "gpudeck-agent" {
        bail!(
            "unexpected manifest (schema {}, product {})",
            m.schema,
            m.product
        );
    }
    if !is_semver(&m.version) {
        bail!("manifest version {:?} is not a version", m.version);
    }
    for a in &m.artifacts {
        if a.binary_sha256.len() != 64 || !a.binary_sha256.chars().all(|c| c.is_ascii_hexdigit()) {
            bail!("manifest artifact {} has no valid binary_sha256", a.target);
        }
    }
    Ok(m)
}

fn push_key(out: &mut Vec<String>, k: &str) {
    let k = k.trim().to_string();
    if decode_public_key(&k).is_ok() && !out.contains(&k) {
        out.push(k);
    }
}

/// Release keys the agent trusts: `agent.toml` `release_public_keys` plus the verified policy's.
pub fn release_keys(cfg: &AgentConfig, policy: Option<&SecurityPolicy>) -> Vec<String> {
    let mut out = vec![];
    for k in &cfg.release_public_keys {
        push_key(&mut out, k);
    }
    if let Some(r) = policy.and_then(|p| p.release.as_ref()) {
        for k in &r.public_keys {
            push_key(&mut out, k);
        }
    }
    out
}

/// Release keys for the applier, anchored outside the agent's writable state: `agent.toml`
/// keys, and policy keys only when the policy verifies against a CA certificate whose
/// fingerprint equals the `ca_fingerprint` pinned in `agent.toml` (a CA appended to `ca.pem` by
/// anyone else does not count).
pub fn anchored_release_keys(cfg: &AgentConfig) -> Vec<String> {
    let mut out = vec![];
    for k in &cfg.release_public_keys {
        push_key(&mut out, k);
    }
    match anchored_policy(cfg) {
        Ok(Some(p)) => {
            for k in p.release.map(|r| r.public_keys).unwrap_or_default() {
                push_key(&mut out, &k);
            }
        }
        Ok(None) => {}
        Err(e) => warn!("security policy not used for release keys: {e:#}"),
    }
    out
}

fn anchored_policy(cfg: &AgentConfig) -> Result<Option<SecurityPolicy>> {
    let (Some(pin), Some(node)) = (cfg.ca_fingerprint.as_deref(), cfg.node_id) else {
        return Ok(None);
    };
    let paths = StatePaths::new(&cfg.state_dir);
    let ca = String::from_utf8(read_limited(&paths.ca(), MAX_JSON_BYTES)?.unwrap_or_default())
        .context("ca.pem is not UTF-8")?;
    let pinned: Vec<String> = crate::pki::parse_cert_bundle(&ca)?
        .iter()
        .filter(|c| crate::pki::fingerprint_eq(pin, &crate::pki::fingerprint_sha256(c)))
        .map(|c| der_to_pem(c))
        .collect();
    if pinned.is_empty() {
        bail!("no certificate in ca.pem matches the pinned CA fingerprint");
    }
    let Some(signed) =
        read_json_secure::<gpudeck_protocol::SignedPolicy>(&paths.security_policy())?
    else {
        return Ok(None);
    };
    crate::security::verify(&signed, &pinned.join("\n"), node).map(Some)
}

fn der_to_pem(der: &[u8]) -> String {
    let body = b64().encode(der);
    let mut out = String::from("-----BEGIN CERTIFICATE-----\n");
    for chunk in body.as_bytes().chunks(64) {
        out.push_str(std::str::from_utf8(chunk).unwrap_or_default());
        out.push('\n');
    }
    out.push_str("-----END CERTIFICATE-----\n");
    out
}

// ---------------------------------------------------------------------------
// Files in <state_dir>/update
// ---------------------------------------------------------------------------

/// `<state_dir>/update` layout.
#[derive(Debug, Clone)]
pub struct UpdatePaths {
    pub dir: PathBuf,
}

impl UpdatePaths {
    pub fn new(state: &StatePaths) -> Self {
        Self {
            dir: state.root.join("update"),
        }
    }
    /// What the agent asks the applier to do (watched by the update helper).
    pub fn request(&self) -> PathBuf {
        self.dir.join("request.json")
    }
    /// The applier's current phase.
    pub fn status(&self) -> PathBuf {
        self.dir.join("status.json")
    }
    /// The applier's outcome.
    pub fn result(&self) -> PathBuf {
        self.dir.join("result.json")
    }
    /// Written by the agent that runs after a restart once it is connected to the hub.
    pub fn health(&self) -> PathBuf {
        self.dir.join("health.json")
    }
    /// pid + version of the running agent (the applier signals it in `self` mode).
    pub fn running(&self) -> PathBuf {
        self.dir.join("running.json")
    }
    pub fn staged_json(&self) -> PathBuf {
        self.dir.join("staged.json")
    }
    pub fn staged_root(&self) -> PathBuf {
        self.dir.join("staged")
    }
    pub fn staged_dir(&self, version: &str) -> PathBuf {
        self.staged_root().join(version)
    }
    pub fn staging_dir(&self) -> PathBuf {
        self.dir.join("staging")
    }
    /// Version and path of the binary kept for rollback.
    pub fn previous(&self) -> PathBuf {
        self.dir.join("previous.json")
    }
    pub fn history(&self) -> PathBuf {
        self.dir.join("history")
    }
    pub fn applier_log(&self) -> PathBuf {
        self.dir.join("applier.log")
    }

    /// Create the directory (0700) when missing; refuse a symlink in its place.
    pub fn ensure(&self) -> Result<()> {
        match std::fs::symlink_metadata(&self.dir) {
            Ok(m) if m.file_type().is_symlink() => {
                bail!("{} is a symlink; refusing to use it", self.dir.display())
            }
            Ok(_) => {}
            Err(_) => std::fs::create_dir_all(&self.dir)
                .with_context(|| format!("create {}", self.dir.display()))?,
        }
        for d in [self.dir.clone(), self.history()] {
            std::fs::create_dir_all(&d).with_context(|| format!("create {}", d.display()))?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o700));
            }
        }
        Ok(())
    }

    /// The directory exists and is a real directory (the applier never creates it).
    pub fn check(&self) -> Result<()> {
        let m = std::fs::symlink_metadata(&self.dir)
            .with_context(|| format!("{} is missing", self.dir.display()))?;
        if !m.is_dir() || m.file_type().is_symlink() {
            bail!("{} is not a directory", self.dir.display());
        }
        Ok(())
    }
}

/// `update/staged.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StagedInfo {
    pub version: String,
    pub target: String,
    pub sha256: String,
    pub size_bytes: u64,
    pub staged_at: Timestamp,
}

/// `update/request.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UpdateRequest {
    pub id: Uuid,
    #[serde(default)]
    pub command_id: Option<Uuid>,
    #[serde(default)]
    pub run_id: Option<Uuid>,
    pub version: String,
    pub from_version: String,
    pub target: String,
    pub sha256: String,
    pub health_timeout_seconds: u64,
    pub requested_at: Timestamp,
    pub agent_pid: u32,
    pub mode: String,
}

impl UpdateRequest {
    fn stale(&self) -> bool {
        (gpudeck_protocol::now() - self.requested_at).num_seconds()
            > REQUEST_STALE_SECONDS.max(self.health_timeout_seconds as i64 * 3)
    }
}

/// `update/status.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ApplierStatus {
    pub request_id: Uuid,
    pub phase: String,
    pub at: Timestamp,
    #[serde(default)]
    pub message: Option<String>,
}

/// One step of an applied update.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Step {
    pub phase: String,
    pub at: Timestamp,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

/// `update/result.json`: `outcome` is `succeeded`, `rolled_back` or `failed`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UpdateResult {
    pub request_id: Uuid,
    #[serde(default)]
    pub command_id: Option<Uuid>,
    #[serde(default)]
    pub run_id: Option<Uuid>,
    pub outcome: String,
    pub version: String,
    pub from_version: String,
    #[serde(default)]
    pub error_code: Option<String>,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub steps: Vec<Step>,
    pub finished_at: Timestamp,
}

/// `update/health.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HealthMarker {
    pub request_id: Uuid,
    pub version: String,
    pub pid: u32,
    pub at: Timestamp,
    #[serde(default)]
    pub adopted: usize,
    #[serde(default)]
    pub relaunched: usize,
}

/// `update/running.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunningMarker {
    pub pid: u32,
    pub version: String,
    pub started_at: Timestamp,
}

/// `update/previous.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PreviousInfo {
    pub version: String,
    pub path: String,
    pub at: Timestamp,
}

/// Write `bytes` to `path` atomically: a fresh temp file opened with `O_CREAT|O_EXCL|O_NOFOLLOW`
/// (a planted symlink fails instead of being followed, which matters when the applier runs as
/// root in a directory the agent user owns), fsync, rename.
pub fn write_secure(path: &Path, bytes: &[u8], mode: u32) -> Result<()> {
    let dir = path
        .parent()
        .ok_or_else(|| anyhow!("no parent directory"))?;
    let name = path
        .file_name()
        .ok_or_else(|| anyhow!("no file name"))?
        .to_string_lossy();
    let tmp = dir.join(format!(
        ".{name}.{}.{}.tmp",
        std::process::id(),
        Uuid::new_v4().simple()
    ));
    {
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(mode).custom_flags(libc::O_NOFOLLOW);
        }
        let mut f = opts
            .open(&tmp)
            .with_context(|| format!("create {}", tmp.display()))?;
        f.write_all(bytes)?;
        f.sync_all()?;
    }
    std::fs::rename(&tmp, path).with_context(|| format!("rename to {}", path.display()))?;
    Ok(())
}

pub fn write_json_secure<T: Serialize>(path: &Path, v: &T) -> Result<()> {
    write_secure(path, &serde_json::to_vec_pretty(v)?, 0o644)
}

/// Read at most `max` bytes of a regular file without following a symlink (`None` = missing).
pub fn read_limited(path: &Path, max: u64) -> Result<Option<Vec<u8>>> {
    let mut opts = std::fs::OpenOptions::new();
    opts.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.custom_flags(libc::O_NOFOLLOW);
    }
    let f = match opts.open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(anyhow!("open {}: {e}", path.display())),
    };
    if !f.metadata()?.is_file() {
        bail!("{} is not a regular file", path.display());
    }
    let mut buf = vec![];
    f.take(max + 1).read_to_end(&mut buf)?;
    if buf.len() as u64 > max {
        bail!("{} is larger than {max} bytes", path.display());
    }
    Ok(Some(buf))
}

pub fn read_json_secure<T: DeserializeOwned>(path: &Path) -> Result<Option<T>> {
    match read_limited(path, MAX_JSON_BYTES)? {
        Some(b) => Ok(Some(
            serde_json::from_slice(&b).with_context(|| format!("parse {}", path.display()))?,
        )),
        None => Ok(None),
    }
}

fn read_opt<T: DeserializeOwned>(path: &Path) -> Option<T> {
    read_json_secure(path).ok().flatten()
}

/// sha256 + size of a file (not following a symlink).
pub fn file_sha256(path: &Path) -> Result<(String, u64)> {
    let mut opts = std::fs::OpenOptions::new();
    opts.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.custom_flags(libc::O_NOFOLLOW);
    }
    let mut f = opts
        .open(path)
        .with_context(|| format!("open {}", path.display()))?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    let mut n = 0u64;
    loop {
        let r = f.read(&mut buf)?;
        if r == 0 {
            break;
        }
        n += r as u64;
        h.update(&buf[..r]);
    }
    Ok((hex::encode(h.finalize()), n))
}

fn pid_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    // SAFETY: kill(2) with signal 0 only checks for existence / permission.
    let r = unsafe { libc::kill(pid as libc::pid_t, 0) };
    r == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

fn is_root() -> bool {
    // SAFETY: geteuid(2) has no preconditions.
    unsafe { libc::geteuid() == 0 }
}

// ---------------------------------------------------------------------------
// Mode
// ---------------------------------------------------------------------------

/// How this agent can be updated (`AgentUpdateStatus.mode` + reason).
#[derive(Debug, Clone, PartialEq)]
pub struct ModeInfo {
    pub mode: AgentUpdateMode,
    pub reason: Option<String>,
}

impl ModeInfo {
    fn new(mode: AgentUpdateMode, reason: Option<&str>) -> Self {
        Self {
            mode,
            reason: reason.map(str::to_owned),
        }
    }
    pub fn can_update(&self) -> bool {
        matches!(
            self.mode,
            AgentUpdateMode::Helper | AgentUpdateMode::SelfUpdate
        )
    }
}

/// The update helper the installer adds (systemd path unit / launchd daemon) exists.
pub fn helper_installed() -> bool {
    if cfg!(target_os = "macos") {
        Path::new(LAUNCHD_HELPER_PLIST).exists()
    } else {
        Path::new(SYSTEMD_HELPER_PATH_UNIT).exists()
    }
}

/// launchd label of this agent when launchd runs it as the GPU Deck agent job (an inherited
/// `XPC_SERVICE_NAME` of another app does not count).
pub fn launchd_label() -> Option<String> {
    std::env::var("XPC_SERVICE_NAME")
        .ok()
        .filter(|l| l.starts_with("io.github.sur950.gpudeck-agent"))
        .filter(|l| {
            l.chars()
                .all(|c| c.is_ascii_alphanumeric() || ".-_".contains(c))
        })
}

/// A service manager restarts the agent when it exits: a systemd system service
/// (`Restart=always`; `INVOCATION_ID` set and started by PID 1), the GPU Deck launchd job
/// (`KeepAlive`), or `GPUDECK_AGENT_SUPERVISED=1` for other supervisors.
pub fn supervised() -> bool {
    let set = |k: &str| std::env::var(k).is_ok_and(|v| !v.is_empty() && v != "0");
    // SAFETY: getppid(2) has no preconditions.
    let systemd = set("INVOCATION_ID") && unsafe { libc::getppid() } == 1;
    systemd || launchd_label().is_some() || set("GPUDECK_AGENT_SUPERVISED")
}

fn writable(path: &Path) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let Ok(c) = std::ffi::CString::new(path.as_os_str().as_bytes()) else {
        return false;
    };
    // SAFETY: access(2) on a NUL-terminated path.
    unsafe { libc::access(c.as_ptr(), libc::W_OK) == 0 }
}

/// The running binary and its directory are writable by this process.
pub fn binary_writable() -> bool {
    let Ok(exe) = std::env::current_exe().and_then(|p| p.canonicalize()) else {
        return false;
    };
    writable(&exe) && exe.parent().is_some_and(writable)
}

pub fn detect_mode(cfg: &AgentConfig) -> ModeInfo {
    use AgentUpdateMode::*;
    if std::env::var_os("KUBERNETES_SERVICE_HOST").is_some() {
        return ModeInfo::new(
            Managed,
            Some("runs in Kubernetes: update the DaemonSet image (Helm) instead"),
        );
    }
    let self_ok = || binary_writable() && supervised();
    match cfg.update_helper.as_str() {
        "off" | "none" | "false" => {
            ModeInfo::new(Unsupported, Some("update_helper = \"off\" in agent.toml"))
        }
        "helper" => ModeInfo::new(Helper, None),
        "self" if self_ok() => ModeInfo::new(SelfUpdate, None),
        "self" => ModeInfo::new(
            Unsupported,
            Some("update_helper = \"self\" but the agent cannot replace its binary or no service manager restarts it"),
        ),
        _ if helper_installed() => ModeInfo::new(Helper, None),
        _ if self_ok() => ModeInfo::new(SelfUpdate, None),
        _ if binary_writable() => ModeInfo::new(
            Unsupported,
            Some("not running under a service manager that restarts it (systemd, launchd)"),
        ),
        _ => ModeInfo::new(
            Unsupported,
            Some("the agent binary is root-owned and the update helper is not installed: re-run the installer (it adds the helper) or update manually"),
        ),
    }
}

/// Update state kept by the running agent.
pub struct Updates {
    pub mode: ModeInfo,
    /// A `stage_update` is downloading.
    pub staging: AtomicBool,
    /// Serialises stage / apply.
    pub lock: tokio::sync::Mutex<()>,
}

impl Updates {
    pub fn new(cfg: &AgentConfig) -> Self {
        Self {
            mode: detect_mode(cfg),
            staging: AtomicBool::new(false),
            lock: tokio::sync::Mutex::new(()),
        }
    }
}

/// A request that is waiting for (or being handled by) the applier.
fn pending_request(up: &UpdatePaths) -> Option<UpdateRequest> {
    let req: UpdateRequest = read_opt(&up.request())?;
    let done = read_opt::<UpdateResult>(&up.result()).is_some_and(|r| r.request_id == req.id);
    (!done && !req.stale()).then_some(req)
}

/// `hello.agent_update` / `inventory.agent_update`.
pub fn status(agent: &Agent) -> AgentUpdateStatus {
    let up = UpdatePaths::new(&agent.paths);
    let staged: Option<StagedInfo> =
        read_opt(&up.staged_json()).filter(|s: &StagedInfo| s.version != current_version());
    let previous: Option<PreviousInfo> = read_opt(&up.previous());
    let state = if pending_request(&up).is_some() {
        AgentUpdateState::Applying
    } else if agent.updates.staging.load(Ordering::Relaxed) {
        AgentUpdateState::Staging
    } else if staged.is_some() {
        AgentUpdateState::Staged
    } else {
        AgentUpdateState::Idle
    };
    AgentUpdateStatus {
        mode: agent.updates.mode.mode,
        reason: agent.updates.mode.reason.clone(),
        target: Some(build_target()),
        state,
        staged_version: staged.as_ref().map(|s| s.version.clone()),
        staged_sha256: staged.as_ref().map(|s| s.sha256.clone()),
        staged_at: staged.as_ref().map(|s| s.staged_at),
        previous_version: previous
            .map(|p| p.version)
            .filter(|v| *v != current_version()),
        release_keys: release_keys(&agent.cfg, agent.policy().as_ref()).len() as u32,
    }
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

/// `event(agent_update)`; only to a hub known to list `agent_updates`.
fn phase_event(
    agent: &Agent,
    phase: &str,
    severity: Severity,
    message: String,
    req: Option<&UpdateRequest>,
    extra: JsonObject,
) {
    if !agent.rt().hub_has(hub_feature::AGENT_UPDATES) {
        return;
    }
    let mut d = JsonObject::new();
    d.insert("phase".into(), phase.into());
    d.insert(
        "at".into(),
        serde_json::to_value(gpudeck_protocol::now()).unwrap_or_default(),
    );
    if let Some(r) = req {
        d.insert("version".into(), r.version.clone().into());
        d.insert("from_version".into(), r.from_version.clone().into());
        d.insert("request_id".into(), r.id.to_string().into());
        if let Some(run) = r.run_id {
            d.insert("run_id".into(), run.to_string().into());
        }
    }
    for (k, v) in extra {
        d.insert(k, v);
    }
    let mut ev = Event::new(EventKind::AgentUpdate, severity, message).details(d);
    ev.command_id = req.and_then(|r| r.command_id);
    agent.event(ev);
}

fn err(code: &str, msg: impl Into<String>) -> ErrorInfo {
    ErrorInfo::new(code, msg)
}

// ---------------------------------------------------------------------------
// stage_update
// ---------------------------------------------------------------------------

/// `stage_update`: verify, download, verify, preflight, keep in `update/staged/<version>/`.
pub async fn stage(agent: &Arc<Agent>, ctx: &Ctx, a: StageUpdateArgs) -> Result<Value, ErrorInfo> {
    let mode = agent.updates.mode.clone();
    if !mode.can_update() {
        return Err(err(
            error_code::UPDATE_UNSUPPORTED,
            format!(
                "this agent does not update itself ({}): {}",
                mode.mode,
                mode.reason.unwrap_or_default()
            ),
        ));
    }
    let target = build_target();
    if a.target != target || !safe_target(&a.target) {
        return Err(err(
            error_code::INVALID_ARGS,
            format!("target {} is not this agent's ({target})", a.target),
        ));
    }
    let keys = release_keys(&agent.cfg, agent.policy().as_ref());
    let manifest = verify_manifest(&a.manifest, &keys)
        .map_err(|e| err(error_code::SIGNATURE_INVALID, format!("{e:#}")))?;
    if manifest.version != a.version {
        return Err(err(
            error_code::INVALID_ARGS,
            format!("manifest is for {}, not {}", manifest.version, a.version),
        ));
    }
    let running = current_version();
    match compare_versions(&a.version, &running) {
        Some(std::cmp::Ordering::Greater) => {}
        Some(std::cmp::Ordering::Less) if a.allow_downgrade => {}
        _ => {
            return Err(err(
                error_code::INVALID_ARGS,
                format!("{} is not newer than the running {running}", a.version),
            ))
        }
    }
    let art = manifest.artifact(&target).cloned().ok_or_else(|| {
        err(
            error_code::INVALID_ARGS,
            format!("release {} has no build for {target}", a.version),
        )
    })?;
    if art.binary_size_bytes == 0 || art.binary_size_bytes > MAX_BINARY_BYTES {
        return Err(err(error_code::INVALID_ARGS, "binary size out of range"));
    }
    let _guard = agent.updates.lock.lock().await;
    let up = UpdatePaths::new(&agent.paths);
    up.ensure()
        .map_err(|e| err(error_code::INTERNAL, format!("{e:#}")))?;
    let want = art.binary_sha256.to_ascii_lowercase();
    // Already staged (a retry, or a second control-plane process): verify the file again.
    if let Some(st) = read_opt::<StagedInfo>(&up.staged_json()) {
        if st.version == a.version && st.sha256 == want {
            if let Ok((sha, _)) = file_sha256(&up.staged_dir(&a.version).join("gpudeck-agent")) {
                if sha == want {
                    return Ok(staged_result(&st, true));
                }
            }
        }
    }
    let need = art.binary_size_bytes * 2 + DISK_MARGIN_BYTES;
    if let Some(free) = crate::system::disk_free(&up.dir) {
        if free < need {
            return Err(err(
                error_code::INSUFFICIENT_DISK,
                format!("{free} bytes free on the state volume, staging needs {need}"),
            ));
        }
    }
    agent.updates.staging.store(true, Ordering::Relaxed);
    agent.inventory_dirty.notify_one();
    let mut extra = JsonObject::new();
    extra.insert("version".into(), a.version.clone().into());
    phase_event(
        agent,
        "staging",
        Severity::Info,
        format!("downloading agent {} in the background", a.version),
        None,
        extra,
    );
    let out = stage_download(agent, ctx, &a, &up, &want, art.binary_size_bytes).await;
    agent.updates.staging.store(false, Ordering::Relaxed);
    agent.inventory_dirty.notify_one();
    let mut extra = JsonObject::new();
    extra.insert("version".into(), a.version.clone().into());
    match out {
        Ok(st) => {
            phase_event(
                agent,
                "staged",
                Severity::Info,
                format!("agent {} staged and verified", st.version),
                None,
                extra,
            );
            Ok(staged_result(&st, false))
        }
        Err(e) => {
            extra.insert("error_code".into(), e.code.clone().into());
            phase_event(
                agent,
                "stage_failed",
                Severity::Warning,
                format!("staging agent {} failed: {}", a.version, e.message),
                None,
                extra,
            );
            Err(e)
        }
    }
}

fn staged_result(st: &StagedInfo, already: bool) -> Value {
    json!({
        "staged_version": st.version,
        "sha256": st.sha256,
        "size_bytes": st.size_bytes,
        "staged_at": st.staged_at,
        "already_staged": already,
    })
}

async fn stage_download(
    agent: &Arc<Agent>,
    ctx: &Ctx,
    a: &StageUpdateArgs,
    up: &UpdatePaths,
    want: &str,
    size: u64,
) -> Result<StagedInfo, ErrorInfo> {
    let fail = |code: &str, e: anyhow::Error| err(code, format!("{e:#}"));
    let hub = agent
        .cfg
        .hub_url
        .clone()
        .ok_or_else(|| err(error_code::INTERNAL, "hub_url not configured"))?;
    let tls = agent
        .identity()
        .hub_client_config_pinned(&crate::security::hub_pins(agent.policy().as_ref()))
        .map_err(|e| fail(error_code::INTERNAL, e))?;
    // Only ever the hub this node joined: the URL is built here, never taken from a message.
    let client = reqwest::Client::builder()
        .use_preconfigured_tls(tls)
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(20))
        .read_timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| fail(error_code::INTERNAL, e.into()))?;
    let url = format!(
        "{}{UPDATE_PATH_PREFIX}/{}/{}",
        hub.trim_end_matches('/'),
        urlencoding::encode(&a.version),
        urlencoding::encode(&a.target)
    );
    std::fs::create_dir_all(up.staging_dir()).map_err(|e| fail(error_code::INTERNAL, e.into()))?;
    let partial = up.staging_dir().join(format!("{}.partial", a.version));
    let _ = std::fs::remove_file(&partial);
    ctx.progress(Some(1.0), format!("downloading agent {}", a.version));
    let mut resp = client
        .get(&url)
        .header(TOKEN_HEADER, &a.download_token)
        .send()
        .await
        .map_err(|e| fail(error_code::ARTIFACT_DOWNLOAD_FAILED, e.into()))?;
    if !resp.status().is_success() {
        return Err(err(
            error_code::ARTIFACT_DOWNLOAD_FAILED,
            format!("GET {url}: HTTP {}", resp.status()),
        ));
    }
    let mut f = {
        let mut o = std::fs::OpenOptions::new();
        o.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            o.mode(0o700).custom_flags(libc::O_NOFOLLOW);
        }
        o.open(&partial)
            .map_err(|e| fail(error_code::INTERNAL, e.into()))?
    };
    let mut h = Sha256::new();
    let mut n = 0u64;
    let result: Result<(), ErrorInfo> = async {
        loop {
            let chunk = tokio::select! {
                c = resp.chunk() => c.map_err(|e| fail(error_code::ARTIFACT_DOWNLOAD_FAILED, e.into()))?,
                _ = ctx.cancel.cancelled() => return Err(err(error_code::CANCELLED, "cancelled")),
            };
            let Some(chunk) = chunk else { break };
            n += chunk.len() as u64;
            if n > size {
                return Err(err(
                    error_code::ARTIFACT_CHECKSUM_MISMATCH,
                    format!("download is larger than the {size} bytes the manifest names"),
                ));
            }
            h.update(&chunk);
            f.write_all(&chunk)
                .map_err(|e| fail(error_code::INTERNAL, e.into()))?;
            ctx.progress(
                Some(1.0 + 89.0 * n as f64 / size as f64),
                format!("downloading agent {} ({n}/{size} bytes)", a.version),
            );
        }
        f.sync_all()
            .map_err(|e| fail(error_code::INTERNAL, e.into()))?;
        let got = hex::encode(h.clone().finalize());
        if got != want || n != size {
            return Err(err(
                error_code::ARTIFACT_CHECKSUM_MISMATCH,
                format!("downloaded binary sha256 {got} ({n} bytes) does not match the signed manifest"),
            ));
        }
        Ok(())
    }
    .await;
    drop(f);
    if let Err(e) = result {
        let _ = std::fs::remove_file(&partial);
        return Err(e);
    }
    // Does it run here (glibc, architecture)? `version` only: no state is touched.
    ctx.progress(Some(92.0), "checking that the new binary runs on this host");
    let check = tokio::time::timeout(
        Duration::from_secs(20),
        tokio::process::Command::new(&partial)
            .arg("version")
            .env_remove("GPUDECK_AGENT_CONFIG")
            .kill_on_drop(true)
            .output(),
    )
    .await;
    let ok = match &check {
        Ok(Ok(o)) => o.status.success() && String::from_utf8_lossy(&o.stdout).contains(&a.version),
        _ => false,
    };
    if !ok {
        let _ = std::fs::remove_file(&partial);
        let detail = match check {
            Ok(Ok(o)) => format!(
                "exit {:?}: {}{}",
                o.status.code(),
                String::from_utf8_lossy(&o.stdout).trim(),
                String::from_utf8_lossy(&o.stderr).trim()
            ),
            Ok(Err(e)) => e.to_string(),
            Err(_) => "no answer within 20 s".into(),
        };
        return Err(err(
            error_code::UPDATE_UNSUPPORTED,
            format!(
                "agent {} does not run on this host ({}): {detail}",
                a.version,
                build_target()
            ),
        ));
    }
    let dir = up.staged_dir(&a.version);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).map_err(|e| fail(error_code::INTERNAL, e.into()))?;
    std::fs::rename(&partial, dir.join("gpudeck-agent"))
        .map_err(|e| fail(error_code::INTERNAL, e.into()))?;
    write_json_secure(&dir.join("manifest.json"), &a.manifest)
        .map_err(|e| fail(error_code::INTERNAL, e))?;
    let st = StagedInfo {
        version: a.version.clone(),
        target: a.target.clone(),
        sha256: want.to_string(),
        size_bytes: size,
        staged_at: gpudeck_protocol::now(),
    };
    write_json_secure(&up.staged_json(), &st).map_err(|e| fail(error_code::INTERNAL, e))?;
    // One staged version at a time.
    if let Ok(rd) = std::fs::read_dir(up.staged_root()) {
        for e in rd.flatten() {
            if e.file_name().to_string_lossy() != a.version {
                let _ = std::fs::remove_dir_all(e.path());
            }
        }
    }
    info!(version = %st.version, sha256 = %st.sha256, "agent update staged");
    Ok(st)
}

/// The staged binary of `version` still matches a signed manifest (before every swap).
pub fn verify_staged(
    up: &UpdatePaths,
    version: &str,
    keys: &[String],
    target: &str,
) -> Result<(String, u64), ErrorInfo> {
    if !is_semver(version) {
        return Err(err(error_code::INVALID_ARGS, "invalid version"));
    }
    let dir = up.staged_dir(version);
    let signed: SignedReleaseManifest = read_json_secure(&dir.join("manifest.json"))
        .map_err(|e| err(error_code::NOT_STAGED, format!("{e:#}")))?
        .ok_or_else(|| err(error_code::NOT_STAGED, format!("{version} is not staged")))?;
    let m = verify_manifest(&signed, keys)
        .map_err(|e| err(error_code::SIGNATURE_INVALID, format!("{e:#}")))?;
    if m.version != version {
        return Err(err(
            error_code::SIGNATURE_INVALID,
            "the staged manifest names another version",
        ));
    }
    let art = m.artifact(target).ok_or_else(|| {
        err(
            error_code::SIGNATURE_INVALID,
            format!("the staged manifest has no build for {target}"),
        )
    })?;
    let (sha, n) = file_sha256(&dir.join("gpudeck-agent"))
        .map_err(|e| err(error_code::NOT_STAGED, format!("{e:#}")))?;
    if sha != art.binary_sha256.to_ascii_lowercase() || n != art.binary_size_bytes {
        return Err(err(
            error_code::ARTIFACT_CHECKSUM_MISMATCH,
            "the staged binary does not match its signed manifest",
        ));
    }
    Ok((sha, n))
}

// ---------------------------------------------------------------------------
// apply_update
// ---------------------------------------------------------------------------

/// `apply_update`: re-verify, write the request, start (self mode) or wake (helper mode) the
/// applier, report its phases until this process is restarted.
pub async fn apply(agent: &Arc<Agent>, ctx: &Ctx, a: ApplyUpdateArgs) -> Result<Value, ErrorInfo> {
    let mode = agent.updates.mode.clone();
    if !mode.can_update() {
        return Err(err(
            error_code::UPDATE_UNSUPPORTED,
            format!(
                "this agent does not update itself ({}): {}",
                mode.mode,
                mode.reason.unwrap_or_default()
            ),
        ));
    }
    let _guard = agent.updates.lock.lock().await;
    let up = UpdatePaths::new(&agent.paths);
    up.ensure()
        .map_err(|e| err(error_code::INTERNAL, format!("{e:#}")))?;
    if let Some(q) = pending_request(&up) {
        return Err(err(
            error_code::UPDATE_IN_PROGRESS,
            format!("an update to {} is already being applied", q.version),
        ));
    }
    let staged: StagedInfo = read_opt(&up.staged_json())
        .filter(|s: &StagedInfo| s.version == a.version)
        .ok_or_else(|| {
            err(
                error_code::NOT_STAGED,
                format!("{} is not staged", a.version),
            )
        })?;
    let keys = release_keys(&agent.cfg, agent.policy().as_ref());
    let target = build_target();
    let (sha, _) = verify_staged(&up, &a.version, &keys, &target)?;
    if sha != staged.sha256 {
        return Err(err(
            error_code::ARTIFACT_CHECKSUM_MISMATCH,
            "staged.json does not match the staged binary",
        ));
    }
    let req = UpdateRequest {
        id: Uuid::new_v4(),
        command_id: ctx.command_id,
        run_id: a.run_id,
        version: a.version.clone(),
        from_version: current_version(),
        target,
        sha256: sha,
        health_timeout_seconds: a.health_timeout_seconds.clamp(30, 900),
        requested_at: gpudeck_protocol::now(),
        agent_pid: std::process::id(),
        mode: mode.mode.to_string(),
    };
    archive(&up, None);
    write_json_secure(&up.request(), &req)
        .map_err(|e| err(error_code::INTERNAL, format!("{e:#}")))?;
    info!(version = %req.version, mode = %req.mode, request = %req.id, "applying agent update");
    phase_event(
        agent,
        "swapping",
        Severity::Info,
        format!("swapping agent {} -> {}", req.from_version, req.version),
        Some(&req),
        JsonObject::new(),
    );
    ctx.progress(Some(10.0), format!("swapping to {}", req.version));
    if mode.mode == AgentUpdateMode::SelfUpdate {
        if let Err(e) = spawn_applier(agent, &up) {
            let _ = std::fs::remove_file(up.request());
            return Err(err(
                error_code::INTERNAL,
                format!("start the updater: {e:#}"),
            ));
        }
    }
    let started = tokio::time::Instant::now();
    let deadline = started + Duration::from_secs(req.health_timeout_seconds * 2 + 300);
    let mut last_phase = String::new();
    loop {
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_millis(300)) => {}
            _ = ctx.cancel.cancelled() => {
                if read_opt::<ApplierStatus>(&up.status()).is_none() {
                    let _ = std::fs::remove_file(up.request());
                    return Err(err(error_code::CANCELLED, "cancelled before the swap started"));
                }
            }
        }
        if let Some(r) = read_opt::<UpdateResult>(&up.result()).filter(|r| r.request_id == req.id) {
            // The applier finished without restarting this process (verification or swap
            // failed, nothing changed).
            archive(&up, Some(&r));
            let code = r
                .error_code
                .clone()
                .unwrap_or_else(|| error_code::INTERNAL.into());
            let msg = r.message.clone().unwrap_or_else(|| r.outcome.clone());
            if r.outcome == "succeeded" {
                return Ok(json!({"version": r.version, "from_version": r.from_version}));
            }
            return Err(err(&code, msg));
        }
        match read_opt::<ApplierStatus>(&up.status()).filter(|s| s.request_id == req.id) {
            Some(st) if st.phase != last_phase => {
                last_phase = st.phase.clone();
                if st.phase == "restarting" {
                    phase_event(
                        agent,
                        "restarting",
                        Severity::Info,
                        format!("restarting the agent as {}", req.version),
                        Some(&req),
                        JsonObject::new(),
                    );
                    ctx.progress(Some(40.0), "restarting");
                } else {
                    ctx.progress(Some(20.0), st.phase.clone());
                }
            }
            Some(_) => {}
            None if started.elapsed() > Duration::from_secs(HELPER_PICKUP_SECONDS) => {
                let _ = std::fs::remove_file(up.request());
                let how = if mode.mode == AgentUpdateMode::Helper {
                    "the update helper did not pick up the request (check `systemctl status gpudeck-agent-update.path` / the launchd daemon, or re-run the installer)"
                } else {
                    "the updater did not start"
                };
                return Err(err(error_code::UPDATE_UNSUPPORTED, how));
            }
            None => {}
        }
        if tokio::time::Instant::now() > deadline {
            return Err(err(
                error_code::TIMEOUT,
                "the update did not finish in time",
            ));
        }
    }
}

/// `self` mode: start `gpudeck-agent update apply` detached (own session) so it outlives this
/// process. Under launchd the job is restarted with `launchctl kickstart -k` (also stops a new
/// agent that hangs); otherwise with SIGTERM to the running agent and its service manager's
/// restart policy.
fn spawn_applier(agent: &Agent, up: &UpdatePaths) -> Result<()> {
    // SAFETY: getuid(2) has no preconditions.
    let uid = unsafe { libc::getuid() };
    let restart = match launchd_label() {
        Some(label) => format!("launchd:gui/{uid}/{label}"),
        None => "signal".to_string(),
    };
    let exe = std::env::current_exe()?.canonicalize()?;
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(up.applier_log())?;
    let log2 = log.try_clone()?;
    let mut cmd = std::process::Command::new(&exe);
    cmd.arg("--config")
        .arg(&agent.config_path)
        .args(["update", "apply", "--restart"])
        .arg(&restart)
        .stdin(std::process::Stdio::null())
        .stdout(log)
        .stderr(log2);
    #[cfg(unix)]
    // SAFETY: setsid(2) is async-signal-safe; the applier must not share the agent's session
    // or process group (the agent is about to be stopped).
    unsafe {
        use std::os::unix::process::CommandExt;
        cmd.pre_exec(|| {
            if libc::setsid() == -1 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = cmd.spawn()?;
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(())
}

/// Move a finished (or abandoned) request / result into `history/` and clear the markers.
fn archive(up: &UpdatePaths, result: Option<&UpdateResult>) {
    let _ = std::fs::create_dir_all(up.history());
    let id = result
        .map(|r| r.request_id)
        .or_else(|| read_opt::<UpdateRequest>(&up.request()).map(|r| r.id));
    if let Some(id) = id {
        let _ = std::fs::rename(
            up.request(),
            up.history().join(format!("{id}.request.json")),
        );
        let _ = std::fs::rename(up.result(), up.history().join(format!("{id}.result.json")));
    } else {
        let _ = std::fs::remove_file(up.request());
        let _ = std::fs::remove_file(up.result());
    }
    let _ = std::fs::remove_file(up.status());
    let _ = std::fs::remove_file(up.health());
    // Keep the last 20 entries.
    if let Ok(rd) = std::fs::read_dir(up.history()) {
        let mut files: Vec<(std::time::SystemTime, PathBuf)> = rd
            .flatten()
            .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
            .collect();
        if files.len() > 40 {
            files.sort();
            for (_, p) in files.iter().take(files.len() - 40) {
                let _ = std::fs::remove_file(p);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// After a restart: report, health marker
// ---------------------------------------------------------------------------

/// Called first thing by `Agent::run`: record pid + version (the applier signals this process
/// in `self` mode, also when it later hangs).
pub fn mark_running(agent: &Agent) {
    let up = UpdatePaths::new(&agent.paths);
    if let Err(e) = up.ensure() {
        warn!("update directory: {e:#}");
        return;
    }
    let _ = write_json_secure(
        &up.running(),
        &RunningMarker {
            pid: std::process::id(),
            version: current_version(),
            started_at: gpudeck_protocol::now(),
        },
    );
}

/// Called once by `Agent::run` after engines were re-adopted, before the hub connects.
pub fn on_start(agent: &Arc<Agent>, adopted: usize, relaunched: usize) {
    let up = UpdatePaths::new(&agent.paths);
    if let Err(e) = up.ensure() {
        warn!("update directory: {e:#}");
        return;
    }
    let me = current_version();
    // After a successful update the staged copy of the running version is no longer needed.
    if read_opt::<StagedInfo>(&up.staged_json()).is_some_and(|s| s.version == me) {
        let _ = std::fs::remove_file(up.staged_json());
        let _ = std::fs::remove_dir_all(up.staged_dir(&me));
    }
    let req: Option<UpdateRequest> = read_opt(&up.request());
    let res: Option<UpdateResult> = read_opt(&up.result());
    let mut current_cmd = None;
    match (req, res) {
        (q, Some(r)) if q.as_ref().is_none_or(|q| q.id == r.request_id) => {
            report(agent, &up, &r, q.as_ref());
        }
        (Some(q), _) if q.stale() => {
            warn!(request = %q.id, "abandoning a stale agent update request");
            if let Some(cid) = q.command_id {
                agent.commands.complete(
                    agent,
                    cid,
                    CommandResult::failed(
                        CommandName::ApplyUpdate,
                        err(error_code::TIMEOUT, "the update request expired"),
                    ),
                );
            }
            archive(&up, None);
        }
        (Some(q), _) if q.version == me || q.from_version == me => {
            current_cmd = q.command_id;
            if q.version == me {
                let mut d = JsonObject::new();
                d.insert("adopted".into(), adopted.into());
                d.insert("relaunched".into(), relaunched.into());
                phase_event(
                    agent,
                    "readopting",
                    Severity::Info,
                    format!(
                        "agent {me} started: {adopted} engine process(es) re-adopted, {relaunched} relaunched"
                    ),
                    Some(&q),
                    d,
                );
            }
            let a = agent.clone();
            tokio::spawn(async move { candidate(a, q, adopted, relaunched).await });
        }
        _ => {}
    }
    // An apply_update that has no request any more cannot finish.
    for id in agent.commands.unfinished("apply_update") {
        if Some(id) != current_cmd {
            agent.commands.complete(
                agent,
                id,
                CommandResult::failed(
                    CommandName::ApplyUpdate,
                    err(
                        error_code::INTERNAL,
                        "the agent restarted while the update was being applied",
                    ),
                ),
            );
        }
    }
}

/// The agent that runs while an update is being applied (the new version, or the previous one
/// after a rollback): once connected to the hub, write the health marker the applier waits for,
/// then report the applier's result.
async fn candidate(agent: Arc<Agent>, req: UpdateRequest, adopted: usize, relaunched: usize) {
    let up = UpdatePaths::new(&agent.paths);
    let me = current_version();
    let deadline =
        tokio::time::Instant::now() + Duration::from_secs(req.health_timeout_seconds * 2 + 300);
    // Healthy = engines re-adopted (done before this runs) and the hub accepted our hello.
    loop {
        if agent.rt().connected_since.is_some() {
            break;
        }
        if tokio::time::Instant::now() > deadline || agent.shutdown.is_cancelled() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    let marker = HealthMarker {
        request_id: req.id,
        version: me.clone(),
        pid: std::process::id(),
        at: gpudeck_protocol::now(),
        adopted,
        relaunched,
    };
    if let Err(e) = write_json_secure(&up.health(), &marker) {
        warn!("write the update health marker: {e:#}");
    }
    if me == req.version {
        let mut d = JsonObject::new();
        d.insert("adopted".into(), adopted.into());
        d.insert("relaunched".into(), relaunched.into());
        phase_event(
            &agent,
            "health_check",
            Severity::Info,
            format!("agent {me} reconnected to the hub and reports healthy"),
            Some(&req),
            d,
        );
    }
    loop {
        if let Some(r) = read_opt::<UpdateResult>(&up.result()).filter(|r| r.request_id == req.id) {
            report(&agent, &up, &r, Some(&req));
            return;
        }
        if tokio::time::Instant::now() > deadline || agent.shutdown.is_cancelled() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
    }
}

/// Send the applier's outcome (event + the `apply_update` result), then archive the files.
fn report(agent: &Agent, up: &UpdatePaths, r: &UpdateResult, req: Option<&UpdateRequest>) {
    let steps = serde_json::to_value(&r.steps).unwrap_or_default();
    let mut d = JsonObject::new();
    d.insert("steps".into(), steps.clone());
    if let Some(c) = &r.error_code {
        d.insert("error_code".into(), c.clone().into());
    }
    let fallback;
    let req = match req {
        Some(q) => Some(q),
        None => {
            fallback = UpdateRequest {
                id: r.request_id,
                command_id: r.command_id,
                run_id: r.run_id,
                version: r.version.clone(),
                from_version: r.from_version.clone(),
                target: build_target(),
                sha256: String::new(),
                health_timeout_seconds: 0,
                requested_at: r.finished_at,
                agent_pid: 0,
                mode: String::new(),
            };
            Some(&fallback)
        }
    };
    let (severity, message) = match r.outcome.as_str() {
        "succeeded" => (
            Severity::Info,
            format!("agent updated {} -> {}", r.from_version, r.version),
        ),
        "rolled_back" => (
            Severity::Warning,
            format!(
                "agent update to {} rolled back to {}: {}",
                r.version,
                r.from_version,
                r.message.clone().unwrap_or_default()
            ),
        ),
        _ => (
            Severity::Critical,
            format!(
                "agent update to {} failed: {}",
                r.version,
                r.message.clone().unwrap_or_default()
            ),
        ),
    };
    info!(outcome = %r.outcome, version = %r.version, "agent update finished");
    phase_event(agent, &r.outcome, severity, message, req, d);
    if let Some(cid) = r.command_id {
        let res = if r.outcome == "succeeded" {
            CommandResult::succeeded(
                CommandName::ApplyUpdate,
                json!({"version": r.version, "from_version": r.from_version, "steps": steps}),
            )
        } else {
            let mut e = err(
                r.error_code.as_deref().unwrap_or(error_code::ROLLED_BACK),
                r.message.clone().unwrap_or_else(|| r.outcome.clone()),
            );
            let mut det = JsonObject::new();
            det.insert("outcome".into(), r.outcome.clone().into());
            det.insert("steps".into(), steps);
            e.details = Some(det);
            CommandResult::failed(CommandName::ApplyUpdate, e)
        };
        agent.commands.complete(agent, cid, res);
    }
    archive(up, Some(r));
    agent.inventory_dirty.notify_one();
}

// ---------------------------------------------------------------------------
// The applier (`gpudeck-agent update apply`)
// ---------------------------------------------------------------------------

/// How the applier restarts the agent service.
#[derive(Debug, Clone, PartialEq)]
pub enum RestartSpec {
    /// `systemctl restart <unit>` (root helper on Linux).
    Systemd(String),
    /// `launchctl kickstart -k <service-target>` (e.g. `gui/501/io.github.sur950.gpudeck-agent`).
    Launchd(String),
    /// SIGTERM to the running agent; its service manager starts it again (`self` mode).
    Signal,
}

impl std::str::FromStr for RestartSpec {
    type Err = anyhow::Error;
    fn from_str(s: &str) -> Result<Self> {
        let ok = |v: &str| {
            !v.is_empty()
                && v.len() <= 200
                && v.chars()
                    .all(|c| c.is_ascii_alphanumeric() || "@._-/".contains(c))
                && !v.contains("..")
        };
        match s.split_once(':') {
            Some(("systemd", u)) if ok(u) && !u.contains('/') => Ok(Self::Systemd(u.into())),
            Some(("launchd", t)) if ok(t) => Ok(Self::Launchd(t.into())),
            None if s == "signal" => Ok(Self::Signal),
            _ => bail!("--restart must be systemd:<unit>, launchd:<domain>/<label> or signal"),
        }
    }
}

/// Applier options (the CLI fills them from its own executable and flags).
#[derive(Debug, Clone)]
pub struct ApplyOptions {
    /// The installed binary to replace (the applier's own executable).
    pub binary: PathBuf,
    pub restart: RestartSpec,
}

impl ApplyOptions {
    /// From the CLI: the binary is this executable; the restart method defaults to the
    /// installer's systemd unit when root on Linux, else a signal (self mode).
    pub fn from_cli(restart: Option<&str>) -> Result<Self> {
        let binary = std::env::current_exe()?.canonicalize()?;
        let restart = match restart {
            Some(s) => s.parse()?,
            None if is_root() && cfg!(target_os = "linux") => {
                RestartSpec::Systemd("gpudeck-agent.service".into())
            }
            None if is_root() => bail!("pass --restart launchd:<domain>/<label>"),
            None => RestartSpec::Signal,
        };
        Ok(Self { binary, restart })
    }
}

struct Applier<'a> {
    up: UpdatePaths,
    req: UpdateRequest,
    steps: Vec<Step>,
    opts: &'a ApplyOptions,
}

impl Applier<'_> {
    fn phase(&mut self, phase: &str, message: Option<String>) {
        println!(
            "update {}: {phase}{}",
            self.req.id,
            message
                .as_deref()
                .map(|m| format!(" ({m})"))
                .unwrap_or_default()
        );
        let st = ApplierStatus {
            request_id: self.req.id,
            phase: phase.into(),
            at: gpudeck_protocol::now(),
            message: message.clone(),
        };
        if let Err(e) = write_json_secure(&self.up.status(), &st) {
            eprintln!("write status: {e:#}");
        }
        self.steps.push(Step {
            phase: phase.into(),
            at: st.at,
            message,
        });
    }

    fn finish(
        &mut self,
        outcome: &str,
        code: Option<&str>,
        message: Option<String>,
    ) -> UpdateResult {
        self.phase(outcome, message.clone());
        let r = UpdateResult {
            request_id: self.req.id,
            command_id: self.req.command_id,
            run_id: self.req.run_id,
            outcome: outcome.into(),
            version: self.req.version.clone(),
            from_version: self.req.from_version.clone(),
            error_code: code.map(str::to_owned),
            message,
            steps: self.steps.clone(),
            finished_at: gpudeck_protocol::now(),
        };
        if let Err(e) = write_json_secure(&self.up.result(), &r) {
            eprintln!("write result: {e:#}");
        }
        r
    }

    async fn restart(&self) -> Result<()> {
        match &self.opts.restart {
            RestartSpec::Systemd(unit) => run_cmd("systemctl", &["restart", unit]).await,
            RestartSpec::Launchd(target) => {
                run_cmd("launchctl", &["kickstart", "-k", target]).await
            }
            RestartSpec::Signal => {
                if is_root() {
                    bail!("as root the applier restarts the agent through its service manager (--restart systemd:... / launchd:...)");
                }
                let pid = read_opt::<RunningMarker>(&self.up.running())
                    .map(|r| r.pid)
                    .filter(|p| pid_alive(*p))
                    .unwrap_or(self.req.agent_pid);
                if pid == std::process::id() {
                    bail!("refusing to signal the applier itself");
                }
                if pid <= 1 || !pid_alive(pid) {
                    // Not running right now (crash loop): the service manager starts the
                    // binary that is in place on its next attempt.
                    println!("no running agent to signal; the service manager starts it");
                    return Ok(());
                }
                // SAFETY: kill(2) of the agent process (same user; checked alive above).
                if unsafe { libc::kill(pid as libc::pid_t, libc::SIGTERM) } != 0 {
                    bail!("signal pid {pid}: {}", std::io::Error::last_os_error());
                }
                Ok(())
            }
        }
    }

    async fn wait_health(&self, version: &str, since: Timestamp) -> Option<HealthMarker> {
        let deadline =
            tokio::time::Instant::now() + Duration::from_secs(self.req.health_timeout_seconds);
        loop {
            if let Some(h) = read_opt::<HealthMarker>(&self.up.health()) {
                if h.request_id == self.req.id
                    && h.version == version
                    && h.at >= since - chrono::Duration::seconds(5)
                    && pid_alive(h.pid)
                {
                    return Some(h);
                }
            }
            if tokio::time::Instant::now() > deadline {
                return None;
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
    }
}

async fn run_cmd(program: &str, args: &[&str]) -> Result<()> {
    let out = tokio::time::timeout(
        Duration::from_secs(90),
        tokio::process::Command::new(program)
            .args(args)
            .kill_on_drop(true)
            .output(),
    )
    .await
    .map_err(|_| anyhow!("{program} {} timed out", args.join(" ")))?
    .with_context(|| format!("run {program}"))?;
    if !out.status.success() {
        bail!(
            "{program} {}: exit {:?}: {}",
            args.join(" "),
            out.status.code(),
            String::from_utf8_lossy(&out.stderr).trim()
        );
    }
    Ok(())
}

/// Copy `src` to a new file `dst` (0755, `O_EXCL|O_NOFOLLOW` on both ends), hashing what was
/// written; the copy (not the agent-writable original) is what gets verified and installed.
fn copy_verified(src: &Path, dst: &Path, want: &str, size: u64) -> Result<()> {
    let mut ro = std::fs::OpenOptions::new();
    ro.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        ro.custom_flags(libc::O_NOFOLLOW);
    }
    let mut input = ro
        .open(src)
        .with_context(|| format!("open {}", src.display()))?;
    let mut wo = std::fs::OpenOptions::new();
    wo.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        wo.mode(0o755).custom_flags(libc::O_NOFOLLOW);
    }
    let mut out = wo
        .open(dst)
        .with_context(|| format!("create {}", dst.display()))?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    let mut n = 0u64;
    loop {
        let r = input.read(&mut buf)?;
        if r == 0 {
            break;
        }
        n += r as u64;
        if n > size {
            bail!("staged binary is larger than its manifest says");
        }
        h.update(&buf[..r]);
        out.write_all(&buf[..r])?;
    }
    out.sync_all()?;
    let got = hex::encode(h.finalize());
    if got != want || n != size {
        bail!("copied binary sha256 {got} does not match the signed manifest");
    }
    Ok(())
}

fn sync_dir(dir: &Path) {
    if let Ok(f) = std::fs::File::open(dir) {
        let _ = f.sync_all();
    }
}

/// Run the applier for `<state_dir>/update/request.json`: verify, swap, restart, health check,
/// roll back on failure. Writes `status.json` / `result.json` and returns the result.
pub async fn run_applier(cfg: &AgentConfig, opts: &ApplyOptions) -> Result<UpdateResult> {
    let up = UpdatePaths::new(&StatePaths::new(&cfg.state_dir));
    up.check()?;
    let req: UpdateRequest = read_json_secure(&up.request())?
        .ok_or_else(|| anyhow!("no update request in {}", up.dir.display()))?;
    if let Some(r) = read_opt::<UpdateResult>(&up.result()).filter(|r| r.request_id == req.id) {
        println!("update {} already finished: {}", req.id, r.outcome);
        return Ok(r);
    }
    let mut ap = Applier {
        up: up.clone(),
        req: req.clone(),
        steps: vec![],
        opts,
    };
    ap.phase("verifying", None);
    if req.stale() {
        return Ok(ap.finish(
            "failed",
            Some(error_code::TIMEOUT),
            Some("the update request expired".into()),
        ));
    }
    if !is_semver(&req.version) || !is_semver(&req.from_version) {
        return Ok(ap.finish(
            "failed",
            Some(error_code::INVALID_ARGS),
            Some("invalid version in the request".into()),
        ));
    }
    let keys = anchored_release_keys(cfg);
    let target = build_target();
    let (sha, size) = match verify_staged(&up, &req.version, &keys, &target) {
        Ok(v) => v,
        Err(e) => return Ok(ap.finish("failed", Some(&e.code), Some(e.message))),
    };
    if sha != req.sha256 {
        return Ok(ap.finish(
            "failed",
            Some(error_code::ARTIFACT_CHECKSUM_MISMATCH),
            Some("the staged binary is not the one the request names".into()),
        ));
    }
    let bin = &opts.binary;
    let (Some(dir), Some(name)) = (
        bin.parent(),
        bin.file_name().map(|n| n.to_string_lossy().to_string()),
    ) else {
        return Ok(ap.finish(
            "failed",
            Some(error_code::INTERNAL),
            Some(format!("bad binary path {}", bin.display())),
        ));
    };
    let new_tmp = dir.join(format!(".{name}.new.{}", std::process::id()));
    let prev = dir.join(format!("{name}.prev"));
    let _ = std::fs::remove_file(&new_tmp);
    if let Err(e) = copy_verified(
        &up.staged_dir(&req.version).join("gpudeck-agent"),
        &new_tmp,
        &sha,
        size,
    ) {
        let _ = std::fs::remove_file(&new_tmp);
        return Ok(ap.finish(
            "failed",
            Some(error_code::ARTIFACT_CHECKSUM_MISMATCH),
            Some(format!("{e:#}")),
        ));
    }
    // Same owner as the installed binary (root:root for the system installs).
    #[cfg(unix)]
    if is_root() {
        use std::os::unix::fs::MetadataExt;
        if let Ok(m) = std::fs::metadata(bin) {
            let _ = std::os::unix::fs::chown(&new_tmp, Some(m.uid()), Some(m.gid()));
        }
    }
    ap.phase(
        "swapping",
        Some(format!("{} -> {}", req.from_version, req.version)),
    );
    // Keep the installed binary as <name>.prev (a hard link: same file, no copy), then rename
    // the new one over it. Both renames are atomic; a crash in between leaves a working binary.
    let prev_tmp = dir.join(format!(".{name}.prev.{}", std::process::id()));
    let _ = std::fs::remove_file(&prev_tmp);
    let kept = std::fs::hard_link(bin, &prev_tmp)
        .or_else(|_| std::fs::copy(bin, &prev_tmp).map(|_| ()))
        .and_then(|_| std::fs::rename(&prev_tmp, &prev));
    if let Err(e) = kept {
        let _ = std::fs::remove_file(&new_tmp);
        let _ = std::fs::remove_file(&prev_tmp);
        return Ok(ap.finish(
            "failed",
            Some(error_code::INTERNAL),
            Some(format!("keep the previous binary: {e}")),
        ));
    }
    if let Err(e) = std::fs::rename(&new_tmp, bin) {
        let _ = std::fs::remove_file(&new_tmp);
        return Ok(ap.finish(
            "failed",
            Some(error_code::INTERNAL),
            Some(format!("install the new binary: {e}")),
        ));
    }
    sync_dir(dir);
    let _ = write_json_secure(
        &up.previous(),
        &PreviousInfo {
            version: req.from_version.clone(),
            path: prev.display().to_string(),
            at: gpudeck_protocol::now(),
        },
    );
    let _ = std::fs::remove_file(up.health());
    ap.phase("restarting", None);
    let since = gpudeck_protocol::now();
    let restarted = ap.restart().await;
    let healthy = match &restarted {
        Ok(()) => {
            ap.phase(
                "health_check",
                Some(format!(
                    "waiting up to {} s for agent {} to reconnect",
                    req.health_timeout_seconds, req.version
                )),
            );
            ap.wait_health(&req.version, since).await
        }
        Err(e) => {
            eprintln!("restart failed: {e:#}");
            None
        }
    };
    if let Some(h) = healthy {
        return Ok(ap.finish(
            "succeeded",
            None,
            Some(format!(
                "agent {} healthy (pid {}, {} engine process(es) re-adopted, {} relaunched)",
                h.version, h.pid, h.adopted, h.relaunched
            )),
        ));
    }
    let why = match restarted {
        Err(e) => format!("restart failed: {e:#}"),
        Ok(()) => format!(
            "agent {} did not reconnect and report healthy within {} s",
            req.version, req.health_timeout_seconds
        ),
    };
    ap.phase("rolling_back", Some(why.clone()));
    let failed = dir.join(format!("{name}.failed"));
    let _ = std::fs::remove_file(&failed);
    let _ = std::fs::hard_link(bin, &failed);
    if let Err(e) = std::fs::rename(&prev, bin) {
        return Ok(ap.finish(
            "failed",
            Some(error_code::INTERNAL),
            Some(format!("{why}; restoring the previous binary failed: {e}")),
        ));
    }
    sync_dir(dir);
    let _ = std::fs::remove_file(up.previous());
    let _ = std::fs::remove_file(up.health());
    let since = gpudeck_protocol::now();
    let back = match ap.restart().await {
        Ok(()) => ap.wait_health(&req.from_version, since).await.is_some(),
        Err(e) => {
            eprintln!("restart after rollback failed: {e:#}");
            false
        }
    };
    if back {
        Ok(ap.finish(
            "rolled_back",
            Some(error_code::ROLLED_BACK),
            Some(format!("{why}; rolled back to {}", req.from_version)),
        ))
    } else {
        Ok(ap.finish(
            "failed",
            Some(error_code::ROLLED_BACK),
            Some(format!("{why}; the previous binary ({}) is back in place but did not report healthy either", req.from_version)),
        ))
    }
}

// ---------------------------------------------------------------------------
// The update helper (`gpudeck-agent update install-helper`, run by the installers as root)
// ---------------------------------------------------------------------------

/// systemd units of the update helper: a path unit watching `request.json` and the oneshot
/// service that runs the applier as root with this binary and config.
pub fn systemd_helper_units(
    bin: &Path,
    config: &Path,
    state_dir: &Path,
    unit: &str,
) -> (String, String) {
    let service = format!(
        "# GPU Deck agent self-update (agent protocol §14): applies a staged, signature-verified\n\
         # release when the agent asks for it (request.json), as root because the agent binary is\n\
         # root-owned. Written by `gpudeck-agent update install-helper`.\n\
         [Unit]\n\
         Description=GPU Deck agent self-update (applies a verified, staged release)\n\
         Documentation=https://sur950.github.io/gpu-deck/docs/components/node-agent/\n\
         \n\
         [Service]\n\
         Type=oneshot\n\
         Environment=GPUDECK_AGENT_STATE_DIR={state}\n\
         ExecStart={bin} --config {config} update apply --restart systemd:{unit}\n\
         TimeoutStartSec=1800\n",
        bin = bin.display(),
        config = config.display(),
        state = state_dir.display(),
    );
    let path = format!(
        "# Starts gpudeck-agent-update.service when the agent requests an update.\n\
         [Unit]\n\
         Description=GPU Deck agent self-update request\n\
         \n\
         [Path]\n\
         PathChanged={state}/update/request.json\n\
         Unit=gpudeck-agent-update.service\n\
         \n\
         [Install]\n\
         WantedBy=multi-user.target\n",
        state = state_dir.display()
    );
    (service, path)
}

/// launchd daemon of the update helper (`WatchPaths` on `request.json`).
pub fn launchd_helper_plist(bin: &Path, config: &Path, state_dir: &Path, target: &str) -> String {
    let esc = |p: &Path| {
        p.display()
            .to_string()
            .replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
    };
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
         <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
         <!-- GPU Deck agent self-update (agent protocol §14): applies a staged, signature-verified\n\
         release when the agent asks for it. Written by `gpudeck-agent update install-helper`. -->\n\
         <plist version=\"1.0\"><dict>\n\
         \x20 <key>Label</key><string>io.github.sur950.gpudeck-agent-update</string>\n\
         \x20 <key>ProgramArguments</key><array><string>{bin}</string><string>--config</string><string>{config}</string><string>update</string><string>apply</string><string>--restart</string><string>launchd:{target}</string></array>\n\
         \x20 <key>EnvironmentVariables</key><dict><key>GPUDECK_AGENT_STATE_DIR</key><string>{state}</string></dict>\n\
         \x20 <key>WatchPaths</key><array><string>{state}/update/request.json</string></array>\n\
         \x20 <key>StandardOutPath</key><string>{state}/update/helper.log</string>\n\
         \x20 <key>StandardErrorPath</key><string>{state}/update/helper.log</string>\n\
         </dict></plist>\n",
        bin = esc(bin),
        config = esc(config),
        state = esc(state_dir),
    )
}

/// Install (or refresh) the root-owned update helper for this binary and config. `restart`
/// names how the agent service restarts (default: `systemd:gpudeck-agent.service` on Linux; on
/// macOS the GPU Deck LaunchDaemon when installed, else the LaunchAgent of the state directory's
/// owner). `dry_run` prints instead of writing.
pub async fn install_helper(
    cfg: &AgentConfig,
    config_path: &Path,
    restart: Option<&str>,
    dry_run: bool,
) -> Result<()> {
    use std::os::unix::fs::MetadataExt;
    if std::env::var_os("KUBERNETES_SERVICE_HOST").is_some() {
        bail!("in Kubernetes the DaemonSet image is updated by Helm; no update helper");
    }
    if !dry_run && !is_root() {
        bail!("run as root (the helper replaces the root-owned agent binary)");
    }
    let bin = std::env::current_exe()?.canonicalize()?;
    let config = std::path::absolute(config_path)?;
    let state = std::path::absolute(&cfg.state_dir)?;
    let owner = std::fs::metadata(&state).map(|m| (m.uid(), m.gid())).ok();
    let up = UpdatePaths::new(&StatePaths::new(&state));
    if !dry_run {
        up.ensure()?;
        if let Some((uid, gid)) = owner {
            std::os::unix::fs::chown(&up.dir, Some(uid), Some(gid))?;
            std::os::unix::fs::chown(up.history(), Some(uid), Some(gid))?;
        }
    }
    if cfg!(target_os = "macos") {
        let target = match restart {
            Some(s) => match s.parse::<RestartSpec>()? {
                RestartSpec::Launchd(t) => t,
                _ => bail!("on macOS --restart must be launchd:<domain>/<label>"),
            },
            None if Path::new("/Library/LaunchDaemons/io.github.sur950.gpudeck-agent.plist")
                .exists() =>
            {
                "system/io.github.sur950.gpudeck-agent".to_string()
            }
            None => format!(
                "gui/{}/io.github.sur950.gpudeck-agent",
                owner.map(|o| o.0).unwrap_or(0)
            ),
        };
        let plist = launchd_helper_plist(&bin, &config, &state, &target);
        if dry_run {
            println!("{LAUNCHD_HELPER_PLIST}:\n{plist}");
            return Ok(());
        }
        write_secure(Path::new(LAUNCHD_HELPER_PLIST), plist.as_bytes(), 0o644)?;
        let _ = run_cmd(
            "launchctl",
            &["bootout", "system/io.github.sur950.gpudeck-agent-update"],
        )
        .await;
        run_cmd("launchctl", &["bootstrap", "system", LAUNCHD_HELPER_PLIST]).await?;
        println!("update helper installed: {LAUNCHD_HELPER_PLIST} (restarts {target})");
    } else {
        let unit = match restart {
            Some(s) => match s.parse::<RestartSpec>()? {
                RestartSpec::Systemd(u) => u,
                _ => bail!("on Linux --restart must be systemd:<unit>"),
            },
            None => "gpudeck-agent.service".to_string(),
        };
        let (service, path) = systemd_helper_units(&bin, &config, &state, &unit);
        let service_file = Path::new(SYSTEMD_HELPER_PATH_UNIT).with_extension("service");
        if dry_run {
            println!(
                "{}:\n{service}\n{SYSTEMD_HELPER_PATH_UNIT}:\n{path}",
                service_file.display()
            );
            return Ok(());
        }
        write_secure(&service_file, service.as_bytes(), 0o644)?;
        write_secure(Path::new(SYSTEMD_HELPER_PATH_UNIT), path.as_bytes(), 0o644)?;
        run_cmd("systemctl", &["daemon-reload"]).await?;
        run_cmd(
            "systemctl",
            &["enable", "--now", "gpudeck-agent-update.path"],
        )
        .await?;
        println!("update helper installed: gpudeck-agent-update.path (restarts {unit})");
    }
    Ok(())
}

/// `gpudeck-agent update status`.
pub fn print_status(cfg: &AgentConfig) {
    let up = UpdatePaths::new(&StatePaths::new(&cfg.state_dir));
    let mode = detect_mode(cfg);
    println!("version     {} ({})", current_version(), build_target());
    println!(
        "mode        {}{}",
        mode.mode,
        mode.reason.map(|r| format!(" - {r}")).unwrap_or_default()
    );
    let policy = (|| {
        let paths = StatePaths::new(&cfg.state_dir);
        let ca = std::fs::read_to_string(paths.ca()).ok()?;
        crate::security::load(&paths.security_policy(), &ca, cfg.node_id?)
            .ok()
            .flatten()
    })();
    println!(
        "release keys {} pinned ({} anchored for the root helper)",
        release_keys(cfg, policy.as_ref()).len(),
        anchored_release_keys(cfg).len()
    );
    match read_opt::<StagedInfo>(&up.staged_json()) {
        Some(s) => println!(
            "staged      {} sha256 {} ({})",
            s.version, s.sha256, s.staged_at
        ),
        None => println!("staged      -"),
    }
    match read_opt::<PreviousInfo>(&up.previous()) {
        Some(p) => println!("previous    {} at {}", p.version, p.path),
        None => println!("previous    -"),
    }
    if let Some(q) = pending_request(&up) {
        let phase = read_opt::<ApplierStatus>(&up.status())
            .filter(|s| s.request_id == q.id)
            .map(|s| s.phase)
            .unwrap_or_else(|| "requested".into());
        println!("applying    {} -> {} ({phase})", q.from_version, q.version);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keypair() -> (ring::signature::Ed25519KeyPair, String) {
        use ring::signature::KeyPair;
        let rng = ring::rand::SystemRandom::new();
        let pkcs8 = ring::signature::Ed25519KeyPair::generate_pkcs8(&rng).unwrap();
        let kp = ring::signature::Ed25519KeyPair::from_pkcs8(pkcs8.as_ref()).unwrap();
        let public = b64().encode(kp.public_key().as_ref());
        (kp, public)
    }

    fn signed(kp: &ring::signature::Ed25519KeyPair, m: &ReleaseManifest) -> SignedReleaseManifest {
        let bytes = serde_json::to_vec(m).unwrap();
        SignedReleaseManifest {
            manifest: b64().encode(&bytes),
            signature: b64().encode(kp.sign(&bytes).as_ref()),
            algorithm: "ed25519".into(),
        }
    }

    fn manifest(version: &str) -> ReleaseManifest {
        ReleaseManifest {
            schema: 1,
            product: "gpudeck-agent".into(),
            version: version.into(),
            protocol_version: 1,
            created_at: None,
            artifacts: vec![gpudeck_protocol::ReleaseArtifact {
                target: build_target(),
                file: None,
                size_bytes: None,
                sha256: None,
                binary_sha256: "ab".repeat(32),
                binary_size_bytes: 3,
            }],
        }
    }

    #[test]
    fn versions_order_and_parse() {
        use std::cmp::Ordering::*;
        assert_eq!(compare_versions("1.1.0", "1.0.9"), Some(Greater));
        assert_eq!(compare_versions("1.0.0", "1.0.0-rc.1"), Some(Greater));
        assert_eq!(compare_versions("1.0.0-rc.2", "1.0.0-rc.1"), Some(Greater));
        assert_eq!(compare_versions("v2.0.0", "10.0.0"), Some(Less));
        assert_eq!(compare_versions("1.0", "1.0.0"), None);
        assert!(!is_semver("latest"));
        assert!(!is_semver("1.0.0-"));
        assert!(!is_semver("1.0.0-a/b"));
        let t = build_target();
        assert!(
            t.contains("-apple-darwin") || t.contains("-unknown-linux-"),
            "{t}"
        );
        assert!(safe_target(&t) && !safe_target("../x") && !safe_target(".x"));
    }

    #[test]
    fn manifest_signatures() {
        let (kp, public) = keypair();
        let (_, other) = keypair();
        let m = manifest("1.2.0");
        let s = signed(&kp, &m);
        assert_eq!(
            verify_manifest(&s, std::slice::from_ref(&public)).unwrap(),
            m
        );
        assert_eq!(
            verify_manifest(&s, &[other.clone(), public.clone()]).unwrap(),
            m
        );
        assert!(verify_manifest(&s, &[other]).is_err());
        assert!(verify_manifest(&s, &[]).is_err());
        // Tampered manifest bytes.
        let mut t = s.clone();
        let mut m2 = m.clone();
        m2.artifacts[0].binary_sha256 = "cd".repeat(32);
        t.manifest = b64().encode(serde_json::to_vec(&m2).unwrap());
        assert!(verify_manifest(&t, std::slice::from_ref(&public)).is_err());
        // Wrong algorithm, garbage key.
        let mut a = s.clone();
        a.algorithm = "rsa".into();
        assert!(verify_manifest(&a, std::slice::from_ref(&public)).is_err());
        assert!(decode_public_key("AAAA").is_err());
        // A valid signature over something that is not a manifest.
        let bytes = br#"{"hello":1}"#;
        let junk = SignedReleaseManifest {
            manifest: b64().encode(bytes),
            signature: b64().encode(kp.sign(bytes).as_ref()),
            algorithm: "ed25519".into(),
        };
        assert!(verify_manifest(&junk, &[public]).is_err());
    }

    /// A manifest signed by the release tooling (`scripts/agent_release_manifest.py`, Python
    /// `cryptography`) verifies here (`ring`): same raw ed25519 over the exact file bytes.
    #[test]
    fn manifest_signed_by_the_release_tooling_verifies() {
        let public = "cZ3ydHwBZ+C0qJERfQfZXFgwpSppc/vjTHLy2HJ2q8I=".to_string();
        let signed = SignedReleaseManifest {
            manifest: "ewogICJzY2hlbWEiOiAxLAogICJwcm9kdWN0IjogImdwdWRlY2stYWdlbnQiLAogICJ2ZXJzaW9uIjogIjIuMC4wIiwKICAicHJvdG9jb2xfdmVyc2lvbiI6IDEsCiAgImFydGlmYWN0cyI6IFsKICAgIHsKICAgICAgInRhcmdldCI6ICJ4ODZfNjQtdW5rbm93bi1saW51eC1nbnUiLAogICAgICAiYmluYXJ5X3NoYTI1NiI6ICJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiIiwKICAgICAgImJpbmFyeV9zaXplX2J5dGVzIjogMwogICAgfQogIF0KfQo=".into(),
            signature: "udB6j2YMFQNskkbSeLDWEADrX/nG75PTKIMDdAx15Nzrxcki0uO4v4vzoXIOyNdIFxWVkluK1/iUNnxUUT5CAQ==".into(),
            algorithm: "ed25519".into(),
        };
        let m = verify_manifest(&signed, &[public]).unwrap();
        assert_eq!(m.version, "2.0.0");
        assert_eq!(
            m.artifact("x86_64-unknown-linux-gnu")
                .unwrap()
                .binary_size_bytes,
            3
        );
    }

    #[test]
    fn release_keys_from_config_and_anchored_policy() {
        use crate::testpki::TestCa;
        let dir = tempfile::tempdir().unwrap();
        let (_, k1) = keypair();
        let (_, k2) = keypair();
        let ca = TestCa::new("Update Test CA");
        let node = Uuid::new_v4();
        let paths = StatePaths::new(dir.path());
        paths.ensure().unwrap();
        std::fs::write(paths.ca(), ca.ca_pem()).unwrap();
        let policy = SecurityPolicy {
            v: 1,
            node_id: node,
            serial: 5,
            issued_at: gpudeck_protocol::now(),
            release: Some(gpudeck_protocol::ReleasePolicy {
                public_keys: vec![k2.clone(), "not-a-key".into()],
            }),
            ..Default::default()
        };
        let signed_policy = ca.sign_policy(&policy);
        std::fs::write(
            paths.security_policy(),
            serde_json::to_vec(&signed_policy).unwrap(),
        )
        .unwrap();
        let mut cfg = AgentConfig {
            state_dir: dir.path().to_path_buf(),
            node_id: Some(node),
            ca_fingerprint: Some(ca.fingerprint()),
            release_public_keys: vec![k1.clone(), k1.clone()],
            ..Default::default()
        };
        assert_eq!(
            release_keys(&cfg, Some(&policy)),
            vec![k1.clone(), k2.clone()]
        );
        assert_eq!(anchored_release_keys(&cfg), vec![k1.clone(), k2.clone()]);
        // A policy signed by a CA appended to ca.pem (not the pinned one) gives no keys.
        let rogue = TestCa::new("Rogue CA");
        std::fs::write(paths.ca(), format!("{}\n{}", ca.ca_pem(), rogue.ca_pem())).unwrap();
        std::fs::write(
            paths.security_policy(),
            serde_json::to_vec(&rogue.sign_policy(&policy)).unwrap(),
        )
        .unwrap();
        assert_eq!(anchored_release_keys(&cfg), vec![k1.clone()]);
        // No pin, no policy keys.
        cfg.ca_fingerprint = None;
        std::fs::write(
            paths.security_policy(),
            serde_json::to_vec(&signed_policy).unwrap(),
        )
        .unwrap();
        assert_eq!(anchored_release_keys(&cfg), vec![k1]);
    }

    #[test]
    fn secure_files_refuse_symlinks() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("victim");
        std::fs::write(&target, "keep").unwrap();
        let link = dir.path().join("status.json");
        std::os::unix::fs::symlink(&target, &link).unwrap();
        assert!(read_limited(&link, 100).is_err());
        // Writing replaces the link itself, never the file it points to.
        write_secure(&link, b"{}", 0o644).unwrap();
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "keep");
        assert!(!std::fs::symlink_metadata(&link)
            .unwrap()
            .file_type()
            .is_symlink());
        let up = UpdatePaths {
            dir: dir.path().join("update"),
        };
        std::os::unix::fs::symlink(dir.path(), &up.dir).unwrap();
        assert!(up.ensure().is_err());
        assert!(up.check().is_err());
    }

    #[test]
    fn restart_specs() {
        assert_eq!(
            "systemd:gpudeck-agent.service"
                .parse::<RestartSpec>()
                .unwrap(),
            RestartSpec::Systemd("gpudeck-agent.service".into())
        );
        assert_eq!(
            "launchd:gui/501/io.github.sur950.gpudeck-agent"
                .parse::<RestartSpec>()
                .unwrap(),
            RestartSpec::Launchd("gui/501/io.github.sur950.gpudeck-agent".into())
        );
        assert_eq!(
            "signal".parse::<RestartSpec>().unwrap(),
            RestartSpec::Signal
        );
        for bad in [
            "systemd:a b",
            "systemd:../x",
            "launchd:",
            "rm -rf",
            "systemd:x;y",
        ] {
            assert!(bad.parse::<RestartSpec>().is_err(), "{bad}");
        }
    }

    #[test]
    fn helper_units_run_the_installed_binary_as_root() {
        let (service, path) = systemd_helper_units(
            Path::new("/usr/local/bin/gpudeck-agent"),
            Path::new("/etc/gpudeck/agent.toml"),
            Path::new("/var/lib/gpudeck"),
            "gpudeck-agent.service",
        );
        assert!(service.contains("Type=oneshot"));
        assert!(service.contains(
            "ExecStart=/usr/local/bin/gpudeck-agent --config /etc/gpudeck/agent.toml update apply --restart systemd:gpudeck-agent.service"
        ));
        assert!(!service.contains("User="), "the helper runs as root");
        assert!(path.contains("PathChanged=/var/lib/gpudeck/update/request.json"));
        assert!(path.contains("Unit=gpudeck-agent-update.service"));
        let plist = launchd_helper_plist(
            Path::new("/usr/local/bin/gpudeck-agent"),
            Path::new("/usr/local/etc/gpudeck/agent.toml"),
            Path::new("/usr/local/var/gpudeck"),
            "gui/501/io.github.sur950.gpudeck-agent",
        );
        assert!(plist.contains("<string>launchd:gui/501/io.github.sur950.gpudeck-agent</string>"));
        assert!(plist.contains("<key>WatchPaths</key><array><string>/usr/local/var/gpudeck/update/request.json</string>"));
        // XML comments may not contain a double hyphen.
        let comment = &plist[plist.find("<!--").unwrap() + 4..plist.find("-->").unwrap()];
        assert!(!comment.contains("--"));
    }

    #[test]
    fn mode_respects_kubernetes_and_off() {
        let cfg = AgentConfig {
            update_helper: "off".into(),
            ..Default::default()
        };
        assert_eq!(detect_mode(&cfg).mode, AgentUpdateMode::Unsupported);
        let cfg = AgentConfig {
            update_helper: "helper".into(),
            ..Default::default()
        };
        if std::env::var_os("KUBERNETES_SERVICE_HOST").is_none() {
            assert_eq!(detect_mode(&cfg).mode, AgentUpdateMode::Helper);
        }
    }
}
