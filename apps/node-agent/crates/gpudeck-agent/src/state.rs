//! `<state_dir>` layout and persistence helpers (`protocol.md` §7):
//! `agent.key`, `agent.crt`, `ca.pem`, `config.json`, `commands.json`, `instances/<id>.json`,
//! `engine_processes/<id>.json`, `status.json`.

use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use gpudeck_protocol::{
    CommandKind, CommandResult, ConfigPush, LoadModelArgs, StartEngineArgs, Timestamp,
};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use uuid::Uuid;

/// Max command records kept in `commands.json`.
pub const COMMANDS_KEEP: usize = 1000;

#[derive(Debug, Clone)]
pub struct StatePaths {
    pub root: PathBuf,
}

impl StatePaths {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn key(&self) -> PathBuf {
        self.root.join("agent.key")
    }
    pub fn cert(&self) -> PathBuf {
        self.root.join("agent.crt")
    }
    pub fn ca(&self) -> PathBuf {
        self.root.join("ca.pem")
    }
    pub fn pending_key(&self) -> PathBuf {
        self.root.join("agent.key.next")
    }
    pub fn config_json(&self) -> PathBuf {
        self.root.join("config.json")
    }
    pub fn commands_json(&self) -> PathBuf {
        self.root.join("commands.json")
    }
    pub fn status_json(&self) -> PathBuf {
        self.root.join("status.json")
    }
    pub fn instances_dir(&self) -> PathBuf {
        self.root.join("instances")
    }
    pub fn instance_json(&self, id: Uuid) -> PathBuf {
        self.instances_dir().join(format!("{id}.json"))
    }
    pub fn engine_processes_dir(&self) -> PathBuf {
        self.root.join("engine_processes")
    }
    pub fn engine_process_json(&self, id: Uuid) -> PathBuf {
        self.engine_processes_dir().join(format!("{id}.json"))
    }
    pub fn engines_dir(&self) -> PathBuf {
        self.root.join("engines")
    }
    pub fn routers_dir(&self) -> PathBuf {
        self.engines_dir().join("llama_cpp").join("routers")
    }
    pub fn logs_dir(&self) -> PathBuf {
        self.root.join("logs")
    }
    /// `TMPDIR` of engines (outlives the agent; the systemd unit also binds it at `/tmp`).
    pub fn tmp_dir(&self) -> PathBuf {
        self.root.join("tmp")
    }
    /// `hello_ack.features` of the last hub connection.
    pub fn hub_features_json(&self) -> PathBuf {
        self.root.join("hub_features.json")
    }

    /// Create the directory tree with owner-only permissions (independent of the umask the
    /// installer ran with: `join` under a 0000 umask left the subdirectories world-writable).
    pub fn ensure(&self) -> Result<()> {
        for d in [
            self.root.clone(),
            self.instances_dir(),
            self.engine_processes_dir(),
            self.engines_dir(),
            self.logs_dir(),
            self.tmp_dir(),
        ] {
            std::fs::create_dir_all(&d).with_context(|| format!("create {}", d.display()))?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o700));
            }
        }
        Ok(())
    }
}

/// Write a file atomically (tmp + rename) with the given mode (unix).
pub fn write_private(path: &Path, bytes: &[u8], mode: u32) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    }
    // Unique per call: concurrent writers of the same file (e.g. two egress renders) must not
    // rename each other's temp file away.
    static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let seq = SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let tmp = path.with_extension(format!("tmp.{}.{seq}", std::process::id()));
    {
        use std::io::Write;
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(mode);
        }
        #[cfg(not(unix))]
        let _ = mode;
        let mut f = opts
            .open(&tmp)
            .with_context(|| format!("open {}", tmp.display()))?;
        f.write_all(bytes)?;
        f.sync_all()?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(mode))?;
    }
    std::fs::rename(&tmp, path).with_context(|| format!("rename to {}", path.display()))?;
    Ok(())
}

pub fn write_json<T: Serialize>(path: &Path, value: &T, mode: u32) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(value)?;
    write_private(path, &bytes, mode)
}

pub fn read_json<T: DeserializeOwned>(path: &Path) -> Result<Option<T>> {
    if !path.exists() {
        return Ok(None);
    }
    let bytes = std::fs::read(path).with_context(|| format!("read {}", path.display()))?;
    Ok(Some(
        serde_json::from_slice(&bytes).with_context(|| format!("parse {}", path.display()))?,
    ))
}

/// Check that a secret file is not group/world readable (unix).
pub fn check_private_mode(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(path)?.permissions().mode() & 0o777;
        if mode & 0o077 != 0 {
            anyhow::bail!("{} has mode {:o}; expected 0600", path.display(), mode);
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// commands.json — seen command ids and their latest results (idempotency across restarts)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CommandRecord {
    pub id: Uuid,
    pub command: String,
    pub received_at: Timestamp,
    /// Latest result sent (or to be sent) for this id. `None` while accepted-but-not-run.
    pub last_result: Option<CommandResult>,
    /// True when a terminal result has not yet been delivered on a connection that stayed up.
    #[serde(default)]
    pub pending_delivery: bool,
}

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct CommandStore {
    #[serde(default)]
    pub order: VecDeque<Uuid>,
    #[serde(default)]
    pub records: HashMap<Uuid, CommandRecord>,
}

impl CommandStore {
    pub fn load(path: &Path) -> Self {
        read_json(path).ok().flatten().unwrap_or_default()
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        write_json(path, self, 0o600)
    }

    pub fn get(&self, id: Uuid) -> Option<&CommandRecord> {
        self.records.get(&id)
    }

    /// Register a new command id. Returns false if it was already seen.
    pub fn insert(&mut self, id: Uuid, command: &str) -> bool {
        if self.records.contains_key(&id) {
            return false;
        }
        self.records.insert(
            id,
            CommandRecord {
                id,
                command: command.to_string(),
                received_at: gpudeck_protocol::now(),
                last_result: None,
                pending_delivery: false,
            },
        );
        self.order.push_back(id);
        while self.order.len() > COMMANDS_KEEP {
            if let Some(old) = self.order.pop_front() {
                self.records.remove(&old);
            }
        }
        true
    }

    pub fn set_result(&mut self, id: Uuid, result: CommandResult) {
        if let Some(rec) = self.records.get_mut(&id) {
            rec.pending_delivery = result.status.is_terminal();
            rec.last_result = Some(result);
        }
    }

    pub fn mark_delivered(&mut self, id: Uuid) {
        if let Some(rec) = self.records.get_mut(&id) {
            rec.pending_delivery = false;
        }
    }

    /// Terminal results that still need (re)delivery.
    pub fn undelivered(&self) -> Vec<(Uuid, CommandResult)> {
        self.order
            .iter()
            .filter_map(|id| self.records.get(id))
            .filter(|r| r.pending_delivery)
            .filter_map(|r| r.last_result.clone().map(|res| (r.id, res)))
            .collect()
    }
}

// ---------------------------------------------------------------------------
// Persisted specs for restart (`instances/<id>.json`, `engine_processes/<id>.json`)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PersistedInstance {
    pub args: LoadModelArgs,
    #[serde(default)]
    pub port: u16,
    /// Engine process (dedicated instances; the router's pid for router instances).
    #[serde(default)]
    pub pid: Option<u32>,
    /// Start-time token of `pid` (`supervisor::start_token`): a reused pid is never adopted.
    #[serde(default)]
    pub start_token: Option<u64>,
    /// A llama.cpp router model run as its own `llama-server` (`router_overflow`).
    #[serde(default)]
    pub overflow: bool,
    #[serde(default)]
    pub updated_at: Option<Timestamp>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PersistedEngineProcess {
    pub args: StartEngineArgs,
    #[serde(default)]
    pub port: u16,
    #[serde(default)]
    pub pid: Option<u32>,
    #[serde(default)]
    pub start_token: Option<u64>,
    #[serde(default)]
    pub updated_at: Option<Timestamp>,
}

/// What is persisted about the running process of an instance / engine process.
#[derive(Debug, Clone, Copy, Default)]
pub struct ProcRecord {
    pub port: u16,
    pub pid: Option<u32>,
    pub start_token: Option<u64>,
    pub overflow: bool,
}

pub fn save_instance(paths: &StatePaths, args: &LoadModelArgs, rec: ProcRecord) -> Result<()> {
    let p = PersistedInstance {
        args: args.redacted(),
        port: rec.port,
        pid: rec.pid,
        start_token: rec.start_token,
        overflow: rec.overflow,
        updated_at: Some(gpudeck_protocol::now()),
    };
    write_json(&paths.instance_json(args.instance_id), &p, 0o600)
}

pub fn remove_instance(paths: &StatePaths, id: Uuid) {
    let _ = std::fs::remove_file(paths.instance_json(id));
}

pub fn load_instances(paths: &StatePaths) -> Vec<PersistedInstance> {
    load_dir(&paths.instances_dir())
}

pub fn save_engine_process(
    paths: &StatePaths,
    args: &StartEngineArgs,
    rec: ProcRecord,
) -> Result<()> {
    let p = PersistedEngineProcess {
        args: args.clone(),
        port: rec.port,
        pid: rec.pid,
        start_token: rec.start_token,
        updated_at: Some(gpudeck_protocol::now()),
    };
    write_json(
        &paths.engine_process_json(args.engine_process_id),
        &p,
        0o600,
    )
}

pub fn remove_engine_process(paths: &StatePaths, id: Uuid) {
    let _ = std::fs::remove_file(paths.engine_process_json(id));
}

pub fn load_engine_processes(paths: &StatePaths) -> Vec<PersistedEngineProcess> {
    load_dir(&paths.engine_processes_dir())
}

fn load_dir<T: DeserializeOwned>(dir: &Path) -> Vec<T> {
    let mut out = vec![];
    if let Ok(rd) = std::fs::read_dir(dir) {
        for entry in rd.flatten() {
            let p = entry.path();
            if p.extension().and_then(|e| e.to_str()) == Some("json") {
                if let Ok(Some(v)) = read_json::<T>(&p) {
                    out.push(v);
                }
            }
        }
    }
    out
}

/// Last applied `config_push` (merged), persisted as `config.json`.
pub fn save_config(paths: &StatePaths, cfg: &ConfigPush) -> Result<()> {
    write_json(&paths.config_json(), cfg, 0o600)
}

pub fn load_config(paths: &StatePaths) -> Option<ConfigPush> {
    read_json(&paths.config_json()).ok().flatten()
}

/// Redacted copy of a command for logging / persistence.
pub fn redact_for_log(kind: &CommandKind) -> String {
    serde_json::to_string(&kind.redacted()).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpudeck_protocol::{CommandName, ErrorInfo};

    #[test]
    fn command_store_dedups_and_persists() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("commands.json");
        let mut store = CommandStore::load(&path);
        let id = Uuid::new_v4();
        assert!(store.insert(id, "load_model"));
        assert!(!store.insert(id, "load_model"));
        store.set_result(id, CommandResult::accepted(CommandName::LoadModel));
        assert!(store.undelivered().is_empty());
        store.set_result(
            id,
            CommandResult::failed(CommandName::LoadModel, ErrorInfo::new("x", "y")),
        );
        assert_eq!(store.undelivered().len(), 1);
        store.save(&path).unwrap();
        check_private_mode(&path).unwrap();
        let mut again = CommandStore::load(&path);
        assert_eq!(again.undelivered().len(), 1);
        again.mark_delivered(id);
        assert!(again.undelivered().is_empty());
        assert!(!again.insert(id, "load_model"));
    }

    #[test]
    fn command_store_caps_records() {
        let mut store = CommandStore::default();
        for _ in 0..(COMMANDS_KEEP + 10) {
            store.insert(Uuid::new_v4(), "cancel");
        }
        assert_eq!(store.records.len(), COMMANDS_KEEP);
        assert_eq!(store.order.len(), COMMANDS_KEEP);
    }

    #[cfg(unix)]
    #[test]
    fn ensure_makes_every_state_dir_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let paths = StatePaths::new(dir.path().join("state"));
        paths.ensure().unwrap();
        for d in [
            paths.root.clone(),
            paths.instances_dir(),
            paths.engine_processes_dir(),
            paths.engines_dir(),
            paths.logs_dir(),
            paths.tmp_dir(),
        ] {
            let mode = std::fs::metadata(&d).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o700, "{}", d.display());
        }
    }

    #[test]
    fn write_private_sets_mode() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("k");
        write_private(&p, b"secret", 0o600).unwrap();
        check_private_mode(&p).unwrap();
        assert_eq!(std::fs::read(&p).unwrap(), b"secret");
    }
}
