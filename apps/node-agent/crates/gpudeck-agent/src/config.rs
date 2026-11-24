//! `agent.toml` (keys per `libs/contracts/env.md` §13) with `GPUDECK_AGENT_<KEY>` env overrides.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// Default config file location per platform (`protocol.md` §7).
pub fn default_config_path() -> PathBuf {
    if cfg!(target_os = "macos") {
        PathBuf::from("/usr/local/etc/gpudeck/agent.toml")
    } else {
        PathBuf::from("/etc/gpudeck/agent.toml")
    }
}

pub fn default_state_dir() -> PathBuf {
    if cfg!(target_os = "macos") {
        PathBuf::from("/usr/local/var/gpudeck")
    } else {
        PathBuf::from("/var/lib/gpudeck")
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct AgentConfig {
    /// `https://host:8443` (set by `join`).
    pub hub_url: Option<String>,
    /// Set by `join`.
    pub node_id: Option<Uuid>,
    pub state_dir: PathBuf,
    /// Defaults to `<state_dir>/cache`.
    pub cache_dir: Option<PathBuf>,
    /// Defaults to 70 % of the cache volume.
    pub cache_limit_bytes: Option<u64>,
    pub proxy_port: u16,
    /// Advertised proxy host when it differs from the connection source.
    pub proxy_address: Option<String>,
    /// Advertised proxy port when it differs from `proxy_port` (the node is reached through a
    /// port forward / NAT mapping, e.g. an SSH `-L 19443:127.0.0.1:9443` tunnel).
    pub proxy_advertise_port: Option<u16>,
    /// Bind address for the proxy listener. `auto` (default): `proxy_address` when it is an IP
    /// of this host (e.g. `127.0.0.1` behind an SSH tunnel), else all interfaces (`0.0.0.0`;
    /// firewall the port to the API layer, see docs/components/node-agent.md).
    pub proxy_bind: String,
    pub labels: BTreeMap<String, String>,
    pub log_level: String,
    /// Preinstalled engine binaries, keyed by engine name (`llama_cpp`, `mlx`, ...).
    pub engine_paths: BTreeMap<String, String>,
    /// `auto` | `pf` | `nftables` | `none`.
    pub egress_lock: String,
    pub prom_port: u16,
    pub max_concurrent_commands: usize,
    pub max_concurrent_downloads: usize,
    pub event_buffer_max: usize,
    /// Pin the hub CA at join (sha256 hex of the CA cert DER).
    pub ca_fingerprint: Option<String>,
    /// Node name hint used at join.
    pub name: Option<String>,
    /// Force the container CLI (`docker` / `podman` / `nerdctl`); default: first found on PATH.
    pub container_runtime: Option<String>,
    /// Listen address of the SSH-instance forwarders (protocol.md §8). Default: `proxy_address`
    /// when it is an IP, else `0.0.0.0`.
    pub ssh_bind: Option<String>,
    /// Host ports handed out to SSH instances (inclusive), opened inbound by the egress lock.
    pub ssh_port_range: [u16; 2],
    /// Extra host directories SSH instances may bind-mount (the cache dir is always allowed, read-only).
    pub ssh_bind_roots: Vec<String>,
    /// Stop a llama.cpp router (shared engine process) after it has had no instance for this
    /// many seconds; 0 keeps empty routers running.
    pub router_idle_stop_seconds: u64,
    /// Leave engines running when the agent exits (restart, upgrade, crash) and re-adopt them on
    /// the next start. `false` stops every engine on a clean agent shutdown.
    pub keep_engines_on_exit: bool,
    /// Engine process isolation on Linux: `auto` (a transient `systemd-run --scope` per engine
    /// when the agent runs under systemd and may create scopes), `systemd` (always), `none`.
    pub engine_scope: String,
    /// Warm-up requests after a model loads (first user requests then skip one-time kernel
    /// JIT): `auto` (llama.cpp on NVIDIA GPUs), `on` (every chat / embedding model), `off`.
    pub engine_warmup: String,
    /// sha256 check of in-place local model files (`local_path` sources) on load: `background`
    /// (start the engine, verify afterwards and report a mismatch), `blocking`, `off`. Files
    /// already hashed (`hash_files`, an earlier load) with the same size + mtime are not re-read.
    pub verify_local_files: String,
    /// llama.cpp router: a model the running router does not know is started as its own
    /// `llama-server` while the router hosts other models (a router restart would reload them).
    pub router_overflow: bool,
    /// Model servers GPU Deck did not start and only observes (`[[observe]]`, read-only).
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub observe: Vec<ObserveConfig>,
}

/// One `[[observe]]` entry: an external model server the agent watches without controlling it
/// (`protocol.md` §12). The agent only GETs `<url><metrics_path>`, `<url>/health` and
/// `<url>/v1/models`, and/or tails `log_file` read-only; it never starts, stops or signals the
/// process and never binds its port.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObserveConfig {
    /// Unique per node: `[A-Za-z0-9][A-Za-z0-9._-]*`, at most 64 characters.
    pub name: String,
    /// `vllm` | `sglang` | `llamacpp` | `tgi` | `openai` | `generic`.
    pub engine: gpudeck_protocol::ObservedEngineKind,
    /// Base URL, e.g. `http://127.0.0.1:8000` (http or https; no credentials).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    /// Prometheus path (default `/metrics`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metrics_path: Option<String>,
    /// Model id to show when the server does not answer `GET /v1/models`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    /// Server log to follow read-only: vLLM / SGLang stats lines, access-log lines (uvicorn,
    /// gunicorn, nginx) and startup lines; the lines are also shipped to the hub (redacted). With
    /// no `url` the agent makes no network request for this entry.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_file: Option<PathBuf>,
    /// TCP port the server listens on, to find its processes (and their GPU memory). Defaults to
    /// the port of a local `url`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub listen_port: Option<u16>,
    /// Alternative process match: processes whose command line contains this text (matched on
    /// the node, never reported).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pid_cmd_contains: Option<String>,
    /// Seconds between polls / reported samples (default 10, 2-300).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interval_seconds: Option<f64>,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            hub_url: None,
            node_id: None,
            state_dir: default_state_dir(),
            cache_dir: None,
            cache_limit_bytes: None,
            proxy_port: 9443,
            proxy_address: None,
            proxy_advertise_port: None,
            proxy_bind: "auto".into(),
            labels: BTreeMap::new(),
            log_level: "info".into(),
            engine_paths: BTreeMap::new(),
            egress_lock: "auto".into(),
            prom_port: 9100,
            max_concurrent_commands: 4,
            max_concurrent_downloads: 2,
            event_buffer_max: 10_000,
            ca_fingerprint: None,
            name: None,
            container_runtime: None,
            ssh_bind: None,
            ssh_port_range: [2200, 2299],
            ssh_bind_roots: vec![],
            router_idle_stop_seconds: 120,
            keep_engines_on_exit: true,
            engine_scope: "auto".into(),
            engine_warmup: "auto".into(),
            verify_local_files: "background".into(),
            router_overflow: true,
            observe: vec![],
        }
    }
}

impl AgentConfig {
    /// Load `path` (missing file = defaults), then apply env overrides.
    pub fn load(path: &Path) -> Result<Self> {
        let mut cfg = if path.exists() {
            let text = std::fs::read_to_string(path)
                .with_context(|| format!("read {}", path.display()))?;
            toml::from_str(&text).with_context(|| format!("parse {}", path.display()))?
        } else {
            Self::default()
        };
        cfg.apply_env(|k| std::env::var(k).ok());
        cfg.apply_engine_path_env(std::env::vars());
        Ok(cfg)
    }

    /// Apply `GPUDECK_AGENT_<KEY>` overrides. `get` is injectable for tests.
    pub fn apply_env(&mut self, get: impl Fn(&str) -> Option<String>) {
        let g = |k: &str| get(&format!("GPUDECK_AGENT_{k}")).filter(|v| !v.trim().is_empty());
        if let Some(v) = g("HUB_URL") {
            self.hub_url = Some(v);
        }
        if let Some(v) = g("NODE_ID").and_then(|v| v.parse().ok()) {
            self.node_id = Some(v);
        }
        if let Some(v) = g("STATE_DIR") {
            self.state_dir = PathBuf::from(v);
        }
        if let Some(v) = g("CACHE_DIR") {
            self.cache_dir = Some(PathBuf::from(v));
        }
        if let Some(v) = g("CACHE_LIMIT_BYTES").and_then(|v| v.parse().ok()) {
            self.cache_limit_bytes = Some(v);
        }
        if let Some(v) = g("PROXY_PORT").and_then(|v| v.parse().ok()) {
            self.proxy_port = v;
        }
        if let Some(v) = g("PROXY_ADDRESS") {
            self.proxy_address = Some(v);
        }
        if let Some(v) = g("PROXY_ADVERTISE_PORT").and_then(|v| v.parse().ok()) {
            self.proxy_advertise_port = Some(v);
        }
        if let Some(v) = g("PROXY_BIND") {
            self.proxy_bind = v;
        }
        if let Some(v) = g("LOG_LEVEL") {
            self.log_level = v;
        }
        if let Some(v) = g("EGRESS_LOCK") {
            self.egress_lock = v;
        }
        if let Some(v) = g("PROM_PORT").and_then(|v| v.parse().ok()) {
            self.prom_port = v;
        }
        if let Some(v) = g("MAX_CONCURRENT_COMMANDS").and_then(|v| v.parse().ok()) {
            self.max_concurrent_commands = v;
        }
        if let Some(v) = g("MAX_CONCURRENT_DOWNLOADS").and_then(|v| v.parse().ok()) {
            self.max_concurrent_downloads = v;
        }
        if let Some(v) = g("EVENT_BUFFER_MAX").and_then(|v| v.parse().ok()) {
            self.event_buffer_max = v;
        }
        if let Some(v) = g("CA_FINGERPRINT") {
            self.ca_fingerprint = Some(v);
        }
        if let Some(v) = g("NAME") {
            self.name = Some(v);
        }
        if let Some(v) = g("CONTAINER_RUNTIME") {
            self.container_runtime = Some(v);
        }
        if let Some(v) = g("SSH_BIND") {
            self.ssh_bind = Some(v);
        }
        if let Some((a, b)) = g("SSH_PORT_RANGE").and_then(|v| {
            v.split_once('-')
                .map(|(a, b)| (a.trim().parse().ok(), b.trim().parse().ok()))
        }) {
            if let (Some(a), Some(b)) = (a, b) {
                self.ssh_port_range = [a, b];
            }
        }
        if let Some(v) = g("SSH_BIND_ROOTS") {
            self.ssh_bind_roots = v
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();
        }
        if let Some(v) = g("ROUTER_IDLE_STOP_SECONDS").and_then(|v| v.parse().ok()) {
            self.router_idle_stop_seconds = v;
        }
        let flag = |v: String| {
            matches!(
                v.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        };
        if let Some(v) = g("KEEP_ENGINES_ON_EXIT") {
            self.keep_engines_on_exit = flag(v);
        }
        if let Some(v) = g("ENGINE_SCOPE") {
            self.engine_scope = v;
        }
        if let Some(v) = g("ENGINE_WARMUP") {
            self.engine_warmup = v;
        }
        if let Some(v) = g("VERIFY_LOCAL_FILES") {
            self.verify_local_files = v;
        }
        if let Some(v) = g("ROUTER_OVERFLOW") {
            self.router_overflow = flag(v);
        }
        if let Some(v) = g("LABELS") {
            // "k=v,k2=v2"
            for pair in v.split(',') {
                if let Some((k, val)) = pair.split_once('=') {
                    self.labels
                        .insert(k.trim().to_string(), val.trim().to_string());
                }
            }
        }
    }

    /// `GPUDECK_AGENT_ENGINE_PATHS_<ENGINE>=/path` (env.md §14), e.g. `..._LLAMA_CPP`.
    pub fn apply_engine_path_env(&mut self, vars: impl Iterator<Item = (String, String)>) {
        for (k, v) in vars {
            if let Some(engine) = k.strip_prefix("GPUDECK_AGENT_ENGINE_PATHS_") {
                if !v.trim().is_empty() {
                    let path = crate::template::expand_home(v.trim()).display().to_string();
                    self.engine_paths.insert(engine.to_ascii_lowercase(), path);
                }
            }
        }
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("create {}", parent.display()))?;
        }
        let text = toml::to_string_pretty(self)?;
        crate::state::write_private(path, text.as_bytes(), 0o644)
    }

    pub fn cache_dir(&self) -> PathBuf {
        self.cache_dir
            .clone()
            .unwrap_or_else(|| self.state_dir.join("cache"))
    }

    pub fn engines_dir(&self) -> PathBuf {
        self.state_dir.join("engines")
    }

    /// Proxy listen address for `proxy_bind` (`auto`: `proxy_address` when it is one of
    /// `local_ips` or loopback, else `0.0.0.0`).
    pub fn proxy_bind_addr(&self, local_ips: &[String]) -> String {
        if self.proxy_bind.trim() != "auto" && !self.proxy_bind.trim().is_empty() {
            return self.proxy_bind.trim().to_string();
        }
        if let Some(a) = self.proxy_address.as_deref().map(str::trim) {
            if let Ok(ip) = a.parse::<std::net::IpAddr>() {
                if ip.is_loopback() || local_ips.iter().any(|l| l == a) {
                    return a.to_string();
                }
            }
        }
        "0.0.0.0".into()
    }

    /// The engine binary configured for `engine`, if any.
    pub fn engine_path(&self, engine: &str) -> Option<&str> {
        self.engine_paths.get(engine).map(String::as_str)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn toml_round_trip_and_env_overrides() {
        let text = r#"
hub_url = "https://hub:8443"
node_id = "6f1c2b2e-0b2b-4d8e-9e0e-1b2c3d4e5f60"
state_dir = "/tmp/gd"
proxy_port = 9444
labels = { zone = "office" }
[engine_paths]
llama_cpp = "/opt/llama/llama-server"
"#;
        let mut cfg: AgentConfig = toml::from_str(text).unwrap();
        assert_eq!(cfg.proxy_port, 9444);
        assert_eq!(
            cfg.engine_path("llama_cpp"),
            Some("/opt/llama/llama-server")
        );
        assert_eq!(cfg.cache_dir(), PathBuf::from("/tmp/gd/cache"));
        cfg.apply_env(|k| match k {
            "GPUDECK_AGENT_PROXY_PORT" => Some("9445".into()),
            "GPUDECK_AGENT_PROXY_ADVERTISE_PORT" => Some("19443".into()),
            "GPUDECK_AGENT_LABELS" => Some("rack=r1, zone=dc".into()),
            "GPUDECK_AGENT_EGRESS_LOCK" => Some("none".into()),
            "GPUDECK_AGENT_SSH_PORT_RANGE" => Some("3200-3210".into()),
            "GPUDECK_AGENT_ROUTER_IDLE_STOP_SECONDS" => Some("30".into()),
            "GPUDECK_AGENT_KEEP_ENGINES_ON_EXIT" => Some("false".into()),
            "GPUDECK_AGENT_ENGINE_WARMUP" => Some("off".into()),
            _ => None,
        });
        assert_eq!(cfg.proxy_port, 9445);
        assert_eq!(cfg.proxy_advertise_port, Some(19443));
        assert_eq!(cfg.labels["zone"], "dc");
        assert_eq!(cfg.labels["rack"], "r1");
        assert_eq!(cfg.egress_lock, "none");
        assert_eq!(cfg.ssh_port_range, [3200, 3210]);
        assert_eq!(cfg.router_idle_stop_seconds, 30);
        assert!(!cfg.keep_engines_on_exit);
        assert_eq!(cfg.engine_warmup, "off");
        assert_eq!(AgentConfig::default().router_idle_stop_seconds, 120);
        let d = AgentConfig::default();
        assert!(d.keep_engines_on_exit && d.router_overflow);
        assert_eq!(
            (
                d.engine_scope.as_str(),
                d.engine_warmup.as_str(),
                d.verify_local_files.as_str()
            ),
            ("auto", "auto", "background")
        );
        cfg.apply_engine_path_env(
            vec![
                (
                    "GPUDECK_AGENT_ENGINE_PATHS_MLX".to_string(),
                    "/x/mlx_lm.server".to_string(),
                ),
                ("OTHER".into(), "y".into()),
            ]
            .into_iter(),
        );
        assert_eq!(cfg.engine_path("mlx"), Some("/x/mlx_lm.server"));
        let back: AgentConfig = toml::from_str(&toml::to_string(&cfg).unwrap()).unwrap();
        assert_eq!(back, cfg);
    }

    #[test]
    fn observe_entries_parse_and_round_trip() {
        let text = r#"
node_id = "6f1c2b2e-0b2b-4d8e-9e0e-1b2c3d4e5f60"

[[observe]]
name = "main"
engine = "vllm"
url = "http://127.0.0.1:8000"

[[observe]]
name = "logs-only"
engine = "vllm"
log_file = "/var/log/engines/main.log"
model = "Qwen/Qwen3-8B"
"#;
        let cfg: AgentConfig = toml::from_str(text).unwrap();
        assert_eq!(cfg.observe.len(), 2);
        assert_eq!(
            cfg.observe[0].engine,
            gpudeck_protocol::ObservedEngineKind::Vllm
        );
        assert_eq!(cfg.observe[0].url.as_deref(), Some("http://127.0.0.1:8000"));
        assert_eq!(
            cfg.observe[1].log_file.as_deref(),
            Some(Path::new("/var/log/engines/main.log"))
        );
        let back: AgentConfig = toml::from_str(&toml::to_string_pretty(&cfg).unwrap()).unwrap();
        assert_eq!(back, cfg);
        // Unknown engine kinds and keys are config errors.
        assert!(
            toml::from_str::<AgentConfig>("[[observe]]\nname = \"x\"\nengine = \"ollama\"\n")
                .is_err()
        );
        assert!(toml::from_str::<AgentConfig>(
            "[[observe]]\nname = \"x\"\nengine = \"vllm\"\npid = 1\n"
        )
        .is_err());
        // A config without observers writes no `observe` key.
        assert!(!toml::to_string(&AgentConfig::default())
            .unwrap()
            .contains("observe"));
    }

    #[test]
    fn proxy_bind_auto() {
        let ips = vec!["10.0.0.5".to_string(), "203.0.113.7".to_string()];
        let mut c = AgentConfig::default();
        assert_eq!(c.proxy_bind_addr(&ips), "0.0.0.0");
        c.proxy_address = Some("127.0.0.1".into());
        assert_eq!(
            c.proxy_bind_addr(&ips),
            "127.0.0.1",
            "SSH tunnel: loopback only"
        );
        c.proxy_address = Some("10.0.0.5".into());
        assert_eq!(
            c.proxy_bind_addr(&ips),
            "10.0.0.5",
            "private address of this host"
        );
        c.proxy_address = Some("198.51.100.1".into());
        assert_eq!(c.proxy_bind_addr(&ips), "0.0.0.0", "NAT address: not local");
        c.proxy_address = Some("gpu-1.internal".into());
        assert_eq!(c.proxy_bind_addr(&ips), "0.0.0.0");
        c.proxy_bind = "10.0.0.5".into();
        assert_eq!(c.proxy_bind_addr(&ips), "10.0.0.5");
    }

    #[test]
    fn unknown_keys_are_rejected() {
        assert!(toml::from_str::<AgentConfig>("bogus = 1").is_err());
    }
}
