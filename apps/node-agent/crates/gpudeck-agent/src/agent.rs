//! The running agent: shared state plus the tasks started by `gpudeck-agent run`
//! (hub connection, metrics loop, health loop, mTLS proxy, local Prometheus endpoint, cache
//! housekeeping) and graceful shutdown.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};

use anyhow::{anyhow, Context, Result};
use gpudeck_protocol::{
    ConfigPush, Device, EgressLockKind, EngineCapability, EngineName, EngineRuntime, Event,
    EventKind, Intervals, LogLevel, SecurityPolicy, ServingMode, Settings, SignedPolicy, Timestamp,
};
use tokio_util::sync::CancellationToken;
use tracing::{info, warn};
use uuid::Uuid;

use crate::backend::{DeviceSource, EngineCatalog};
use crate::cache::CacheManager;
use crate::commands::CommandRunner;
use crate::config::AgentConfig;
use crate::instances::{EngineProc, Instance};
use crate::outbox::Outbox;
use crate::proxy::ProxyState;
use crate::state::StatePaths;
use crate::tls::Identity;

/// Options of `gpudeck-agent run`.
#[derive(Debug, Clone, Default)]
pub struct RunOptions {
    /// Program pf/nftables rules (`--apply-egress-lock`); otherwise they are rendered only.
    pub apply_egress_lock: bool,
    /// Override the proxy bind address (tests use 127.0.0.1:0).
    pub proxy_listen: Option<SocketAddr>,
    /// Disable the loopback Prometheus endpoint (tests).
    pub no_prom: bool,
}

/// Runtime knobs set by `hello_ack`, `config_push.settings` and `update_config`.
#[derive(Debug, Clone)]
pub struct Runtime {
    pub intervals: Intervals,
    pub settings: Settings,
    pub config_version: u64,
    /// hello_ack.drain: keep serving, accept no load_model.
    pub drain: bool,
    pub connected_since: Option<Instant>,
    pub hub_node_name: Option<String>,
    /// `hello_ack.features` of the current (or last) hub connection.
    pub hub_features: Vec<String>,
    /// `hub_features` came from a hub (this run or the last one, `hub_features.json`).
    pub hub_features_known: bool,
}

impl Runtime {
    pub fn metrics_interval(&self) -> Duration {
        Duration::from_secs_f64(
            self.settings
                .metrics_interval_seconds
                .unwrap_or(self.intervals.metrics_seconds)
                .max(0.5),
        )
    }
    pub fn heartbeat_interval(&self) -> Duration {
        Duration::from_secs(
            self.settings
                .heartbeat_interval_seconds
                .unwrap_or(self.intervals.heartbeat_seconds)
                .max(1),
        )
    }
    pub fn inventory_interval(&self) -> Duration {
        Duration::from_secs(
            self.settings
                .inventory_interval_seconds
                .unwrap_or(self.intervals.inventory_seconds)
                .max(10),
        )
    }
    pub fn port_range(&self, engine_range: [u16; 2]) -> [u16; 2] {
        self.settings.engine_port_range.unwrap_or(engine_range)
    }
    pub fn drain_default(&self) -> u64 {
        self.settings.drain_default_timeout_seconds.unwrap_or(120)
    }
    /// The hub accepts the optional protocol feature `name` (`hello_ack.features`).
    pub fn hub_has(&self, name: &str) -> bool {
        self.hub_features.iter().any(|f| f == name)
    }
    /// The hub may take the optional `name` in `hello`: it listed it, or no hub answered yet.
    pub fn hub_may_have(&self, name: &str) -> bool {
        !self.hub_features_known || self.hub_has(name)
    }
}

pub struct Agent {
    pub cfg: AgentConfig,
    pub config_path: PathBuf,
    pub paths: StatePaths,
    pub node_id: Uuid,
    pub started_at: Timestamp,
    pub started: Instant,
    pub options: RunOptions,
    pub outbox: Arc<Outbox>,
    pub devices: Arc<dyn DeviceSource>,
    pub engines: Arc<dyn EngineCatalog>,
    pub rt: RwLock<Runtime>,
    pub applied: Mutex<Option<ConfigPush>>,
    pub proxy: Arc<ProxyState>,
    pub identity: RwLock<Arc<Identity>>,
    pub instances: Mutex<HashMap<Uuid, Instance>>,
    pub engine_procs: Mutex<HashMap<Uuid, EngineProc>>,
    /// Engine processes whose `start_engine` is still running (artifact download, spawn).
    pub starting_engines: Mutex<HashSet<Uuid>>,
    /// Router presets to start with (relaunch after an agent restart: the router registers every
    /// model it had, so reloading them needs no further router restart).
    pub router_presets: Mutex<HashMap<Uuid, crate::template::RouterPreset>>,
    pub cache: Arc<CacheManager>,
    pub commands: CommandRunner,
    pub device_ids: Mutex<BTreeMap<String, Uuid>>,
    pub last_devices: Mutex<Vec<Device>>,
    pub shutdown: CancellationToken,
    /// Signalled to force a hub reconnect (cert rotation).
    pub reconnect: tokio::sync::Notify,
    /// Signalled when an inventory should be sent soon.
    pub inventory_dirty: tokio::sync::Notify,
    pub log_forward: Arc<AtomicBool>,
    pub scrape_errors: AtomicU64,
    pub metrics_seq: AtomicU64,
    pub http: reqwest::Client,
    pub prom_text: Mutex<String>,
    pub egress_kind: EgressLockKind,
    pub container_runtime: Option<String>,
    pub proxy_addr: Mutex<Option<SocketAddr>>,
    /// SSH GPU instances (`ssh.rs`).
    pub ssh: crate::ssh::SshManager,
    /// Observed (external) engines from `[[observe]]` (`observe.rs`), read-only.
    pub observer: Arc<crate::observe::Observer>,
    /// Verified, CA-signed security policy (`security.rs`).
    pub policy: RwLock<Option<SecurityPolicy>>,
    /// Signalled when the policy changed (tunnel supervisor).
    pub policy_changed: tokio::sync::Notify,
    /// Rejected inbound attempts (direct proxy, SSH forwarders).
    pub ingress: Arc<crate::ingress::IngressMonitor>,
    /// Outbound tunnel state.
    pub tunnels: Arc<crate::tunnel::TunnelStatus>,
    /// Policy default source CIDRs for SSH instances without their own.
    pub ssh_defaults: Arc<RwLock<Vec<ipnet::IpNet>>>,
    /// Serialises firewall updates; the hash of the last programmed plan.
    pub firewall_lock: tokio::sync::Mutex<()>,
    pub firewall_hash: Mutex<Option<String>>,
    /// Self-update mode and state (`update.rs`).
    pub updates: crate::update::Updates,
}

pub fn device_key(vendor: &str, index: u32) -> String {
    format!("{vendor}:{index}")
}

impl Agent {
    /// Build the agent from a joined config (identity must exist in `state_dir`).
    pub fn new(
        cfg: AgentConfig,
        config_path: PathBuf,
        devices: Arc<dyn DeviceSource>,
        engines: Arc<dyn EngineCatalog>,
        options: RunOptions,
    ) -> Result<Arc<Self>> {
        crate::tls::init_crypto();
        let node_id = cfg.node_id.ok_or_else(|| {
            anyhow!(
                "node_id missing in {}: run `gpudeck-agent join` first",
                config_path.display()
            )
        })?;
        let paths = StatePaths::new(&cfg.state_dir);
        paths.ensure()?;
        let identity = Identity::load(&paths)
            .context("load node identity (agent.key / agent.crt / ca.pem)")?;
        if identity.info.common_name.as_deref() != Some(node_id.to_string().as_str()) {
            warn!(cn = ?identity.info.common_name, %node_id, "certificate CN does not match node_id");
        }
        let outbox = Arc::new(Outbox::new(Some(node_id), cfg.event_buffer_max));
        let cache = Arc::new(CacheManager::open(
            cfg.cache_dir(),
            cfg.cache_limit_bytes,
            cfg.max_concurrent_downloads,
        )?);
        cache.set_verify_mode(&cfg.verify_local_files);
        let applied = crate::state::load_config(&paths);
        let mut settings = Settings::default();
        let mut config_version = 0;
        if let Some(c) = &applied {
            config_version = c.config_version;
            if let Some(s) = &c.settings {
                settings.merge(s);
            }
        }
        let proxy = Arc::new(ProxyState::new(node_id));
        let device_ids: BTreeMap<String, Uuid> =
            crate::state::read_json(&paths.root.join("devices.json"))
                .ok()
                .flatten()
                .unwrap_or_default();
        let egress_kind = crate::egress::detect_kind(&cfg.egress_lock);
        let hub_features: Option<Vec<String>> = crate::state::read_json(&paths.hub_features_json())
            .ok()
            .flatten();
        // The signed policy is re-verified against the pinned CA on every start.
        let policy = match std::fs::read_to_string(paths.ca()) {
            Ok(ca) => {
                crate::security::load(&paths.security_policy(), &ca, node_id).unwrap_or_else(|e| {
                    warn!("persisted security policy ignored: {e:#}");
                    None
                })
            }
            Err(_) => None,
        };
        let agent = Arc::new(Self {
            commands: CommandRunner::new(&paths, cfg.max_concurrent_commands),
            node_id,
            started_at: gpudeck_protocol::now(),
            started: Instant::now(),
            options,
            outbox,
            devices,
            engines,
            rt: RwLock::new(Runtime {
                intervals: Intervals::default(),
                settings,
                config_version,
                drain: false,
                connected_since: None,
                hub_node_name: None,
                hub_features_known: hub_features.is_some(),
                hub_features: hub_features.unwrap_or_default(),
            }),
            applied: Mutex::new(applied.clone()),
            proxy,
            identity: RwLock::new(Arc::new(identity)),
            instances: Mutex::new(HashMap::new()),
            engine_procs: Mutex::new(HashMap::new()),
            starting_engines: Mutex::new(HashSet::new()),
            router_presets: Mutex::new(HashMap::new()),
            cache,
            device_ids: Mutex::new(device_ids),
            last_devices: Mutex::new(vec![]),
            shutdown: CancellationToken::new(),
            reconnect: tokio::sync::Notify::new(),
            inventory_dirty: tokio::sync::Notify::new(),
            log_forward: Arc::new(AtomicBool::new(true)),
            scrape_errors: AtomicU64::new(0),
            metrics_seq: AtomicU64::new(0),
            http: crate::backend::generic::local_http_client(),
            prom_text: Mutex::new(String::new()),
            egress_kind,
            container_runtime: crate::system::container_runtime(cfg.container_runtime.as_deref()),
            proxy_addr: Mutex::new(None),
            ssh: crate::ssh::SshManager::default(),
            observer: Arc::new(crate::observe::Observer::new(&node_id, &cfg.observe)),
            policy: RwLock::new(policy),
            policy_changed: tokio::sync::Notify::new(),
            ingress: Arc::new(crate::ingress::IngressMonitor::default()),
            tunnels: Arc::new(crate::tunnel::TunnelStatus::default()),
            ssh_defaults: Arc::new(RwLock::new(vec![])),
            firewall_lock: tokio::sync::Mutex::new(()),
            firewall_hash: Mutex::new(None),
            updates: crate::update::Updates::new(&cfg),
            paths,
            config_path,
            cfg,
        });
        {
            let weak = Arc::downgrade(&agent);
            *agent
                .cache
                .on_change
                .lock()
                .unwrap_or_else(|e| e.into_inner()) = Some(Arc::new(move |item| {
                if let Some(a) = weak.upgrade() {
                    let mut ev = Event::info(
                        EventKind::CacheChanged,
                        format!("cache {} {}", item.local_path, item.state),
                    );
                    ev.cache_item = Some(item.clone());
                    a.outbox.event(ev);
                }
            }));
        }
        if let Some(c) = applied {
            // Re-apply the persisted config locally (routing, CRL, settings) before the hub answers.
            agent.apply_config_local(&c);
        }
        agent.apply_settings();
        agent.policy_applied();
        Ok(agent)
    }

    /// Effective serving mode (tests force direct through `RunOptions::proxy_listen`).
    pub fn serving_mode(&self) -> ServingMode {
        if self.options.proxy_listen.is_some() {
            ServingMode::Direct
        } else {
            self.cfg.serving_mode()
        }
    }

    /// The verified security policy, if any.
    pub fn policy(&self) -> Option<SecurityPolicy> {
        self.policy
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    /// Verify and adopt a signed policy (config_push / join). `Ok(false)` = unchanged.
    /// Refused: bad signature, another node's policy, a lower serial (rollback).
    pub fn apply_signed_policy(&self, signed: &SignedPolicy) -> Result<bool> {
        let ca = std::fs::read_to_string(self.paths.ca()).context("read ca.pem")?;
        let next = crate::security::verify(signed, &ca, self.node_id)?;
        let current = self.policy();
        crate::security::accept_newer(current.as_ref(), &next)?;
        if current.as_ref() == Some(&next) {
            return Ok(false);
        }
        crate::state::write_json(&self.paths.security_policy(), signed, 0o600)?;
        info!(
            serial = next.serial,
            hub_pins = crate::security::hub_pins(Some(&next)).len(),
            api_layer_pins = crate::security::api_layer_identity(Some(&next)).0.len(),
            tunnel_endpoints = ?crate::security::tunnel_endpoints(Some(&next)),
            "security policy applied"
        );
        *self.policy.write().unwrap_or_else(|e| e.into_inner()) = Some(next);
        self.policy_applied();
        self.policy_changed.notify_one();
        Ok(true)
    }

    /// Push the current policy into the components (proxy identity + sources, SSH defaults).
    fn policy_applied(&self) {
        let p = self.policy();
        self.proxy.apply_policy(p.as_ref());
        let defaults = p
            .as_ref()
            .and_then(|p| p.ssh.as_ref())
            .map(|s| crate::security::parse_nets(&s.default_allowed_cidrs).0)
            .unwrap_or_default();
        *self.ssh_defaults.write().unwrap_or_else(|e| e.into_inner()) = defaults;
    }

    /// `(interval, burst threshold)` of the rejected-attempt reports.
    pub fn ingress_report_params(&self) -> (Duration, u64) {
        let r = self
            .policy()
            .and_then(|p| p.ingress_report)
            .unwrap_or_default();
        (
            Duration::from_secs(
                r.interval_seconds
                    .unwrap_or(crate::security::DEFAULT_REPORT_INTERVAL_SECONDS)
                    .max(10),
            ),
            r.burst_threshold
                .unwrap_or(crate::security::DEFAULT_BURST_THRESHOLD)
                .max(1),
        )
    }

    pub fn rt(&self) -> Runtime {
        self.rt.read().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn identity(&self) -> Arc<Identity> {
        self.identity
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    pub fn event(&self, ev: Event) {
        self.outbox.event(ev);
    }

    /// Settings → components (log forwarding, cache limits, proxy knobs).
    pub fn apply_settings(&self) {
        let s = self.rt().settings;
        self.log_forward
            .store(s.log_forward.unwrap_or(true), Ordering::Relaxed);
        self.cache.set_limits(
            s.cache_limit_bytes.or(self.cfg.cache_limit_bytes),
            s.cache_min_free_bytes,
        );
        if let Some(p) = &s.proxy {
            self.proxy.apply_settings(p);
        }
        if let Some(level) = s.log_level {
            crate::logging::set_level(match level {
                LogLevel::Debug => "debug",
                LogLevel::Info => "info",
                LogLevel::Warn => "warn",
                LogLevel::Error => "error",
            });
        }
    }

    /// Hub `device_id` for a local device: learned from command assignments, else a stable
    /// UUIDv5 of (node_id, vendor:index).
    pub fn device_id(&self, vendor: &str, index: u32) -> Uuid {
        let key = device_key(vendor, index);
        if let Some(id) = self
            .device_ids
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&key)
        {
            return *id;
        }
        Uuid::new_v5(&self.node_id, key.as_bytes())
    }

    pub fn learn_device_ids(&self, assignments: &[gpudeck_protocol::DeviceAssignment]) {
        let mut changed = false;
        {
            let mut g = self.device_ids.lock().unwrap_or_else(|e| e.into_inner());
            for a in assignments {
                let key = device_key(a.vendor.as_str(), a.index);
                if g.get(&key) != Some(&a.device_id) {
                    g.insert(key, a.device_id);
                    changed = true;
                }
            }
        }
        if changed {
            let snapshot = self
                .device_ids
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .clone();
            let _ =
                crate::state::write_json(&self.paths.root.join("devices.json"), &snapshot, 0o600);
        }
    }

    /// Files used by instances that are not stopped (never evicted).
    pub fn files_in_use(&self) -> HashSet<Uuid> {
        self.instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .values()
            .filter(|i| !matches!(i.state, gpudeck_protocol::InstanceState::Stopped))
            .flat_map(|i| {
                i.args.model.files.iter().map(|f| f.model_file_id).chain(
                    i.args
                        .draft_model
                        .iter()
                        .flat_map(|d| d.files.iter().map(|f| f.model_file_id)),
                )
            })
            .collect()
    }

    /// Engines this node can run (`hello.capabilities.engines`).
    pub fn engine_capabilities(&self) -> Vec<EngineCapability> {
        let mut out = vec![];
        let known: &[(EngineName, &str)] = &[
            (EngineName::LlamaCpp, "llama-server"),
            (EngineName::Mlx, "mlx_lm.server"),
            (EngineName::MlxAudio, "mlx_audio.server"),
            (EngineName::WhisperCpp, "whisper-server"),
            (EngineName::StableDiffusionCpp, "sd-server"),
            (EngineName::Vllm, "vllm"),
            (EngineName::Sglang, "sglang"),
            (EngineName::TensorrtLlm, "trtllm-serve"),
        ];
        for (name, bin) in known {
            let configured = self.cfg.engine_path(name.as_str());
            if let Some(p) = crate::template::resolve_executable(bin, configured) {
                out.push(EngineCapability {
                    name: *name,
                    versions: vec![],
                    runtime: Some(EngineRuntime::Native),
                    path: Some(p.display().to_string()),
                });
            }
        }
        if self.container_runtime.is_some() {
            for name in [
                EngineName::Vllm,
                EngineName::VllmOmni,
                EngineName::Sglang,
                EngineName::SglangDiffusion,
                EngineName::TensorrtLlm,
                EngineName::Custom,
            ] {
                if !out.iter().any(|c| c.name == name) {
                    out.push(EngineCapability {
                        name,
                        versions: vec![],
                        runtime: Some(EngineRuntime::Container),
                        path: None,
                    });
                }
            }
        }
        out
    }

    /// Start every task and block until shutdown.
    pub async fn run(self: &Arc<Self>) -> Result<()> {
        info!(node_id = %self.node_id, hub = ?self.cfg.hub_url, "gpudeck-agent {} starting", crate::join::AGENT_VERSION);
        crate::update::mark_running(self);
        self.event(Event::info(
            EventKind::AgentStarted,
            format!(
                "gpudeck-agent {} started (pid {})",
                crate::join::AGENT_VERSION,
                std::process::id()
            ),
        ));
        let mode = self.serving_mode();
        let mut tasks = tokio::task::JoinSet::new();
        match mode {
            ServingMode::Direct => {
                let local_ips = crate::system::ip_addresses();
                let proxy_addr = match self.options.proxy_listen {
                    Some(a) => a,
                    None => {
                        let host = self.cfg.proxy_bind_addr(&local_ips);
                        let ip: std::net::IpAddr = host
                            .parse()
                            .with_context(|| format!("proxy_bind {host:?} is not an IP address"))?;
                        if ip.is_unspecified() || is_public_ip(ip) {
                            warn!(%ip, port = self.cfg.proxy_port, "direct mode: the mTLS proxy listens on a public / every interface (explicit proxy_bind); only the API layer's source addresses and key are accepted, and the firewall should restrict the port");
                        }
                        std::net::SocketAddr::new(ip, self.cfg.proxy_port)
                    }
                };
                let listener = tokio::net::TcpListener::bind(proxy_addr)
                    .await
                    .with_context(|| format!("bind proxy {proxy_addr}"))?;
                *self.proxy_addr.lock().unwrap_or_else(|e| e.into_inner()) =
                    listener.local_addr().ok();
                info!(
                    "serving mode direct: mTLS proxy listening on {} (sources: {})",
                    listener.local_addr()?,
                    self.proxy
                        .allowed_sources()
                        .iter()
                        .map(|n| n.to_string())
                        .collect::<Vec<_>>()
                        .join(", ")
                );
                tasks.spawn(crate::proxy::serve(self.clone(), listener));
            }
            ServingMode::Tunnel => {
                info!("serving mode tunnel: no inbound listener; requests arrive over outbound tunnels to the API layer");
                tasks.spawn(crate::tunnel::run(self.clone()));
            }
        }
        tasks.spawn(crate::ingress::report_loop(self.clone()));
        if !self.options.no_prom {
            let a = self.clone();
            tasks.spawn(async move { crate::prom::serve(a).await });
        }
        tasks.spawn(crate::metrics::run(self.clone()));
        tasks.spawn(crate::metrics::health_loop(self.clone()));
        tasks.spawn(crate::metrics::housekeeping(self.clone()));
        tasks.spawn(crate::ssh::run(self.clone()));
        tasks.spawn(crate::instances::idle_router_loop(self.clone()));
        // Engines started by the previous agent process are still running (they are detached):
        // re-adopt them (pid + start time + health) before the hub connects so the first
        // inventory lists them; relaunch only what is gone, on its previous port.
        let relaunch =
            match tokio::time::timeout(Duration::from_secs(20), self.adopt_running()).await {
                Ok(r) => r,
                Err(_) => {
                    warn!("re-adopting running engines took over 20 s; relaunching the rest");
                    crate::instances::Restore::default()
                }
            };
        // Self-update bookkeeping (protocol.md §14): an update in progress learns that its new
        // agent re-adopted the engines; results of a finished one are reported to the hub.
        {
            let adopted = self
                .instances
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .len()
                + self
                    .engine_procs
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .len();
            crate::update::on_start(self, adopted, relaunch.procs.len() + relaunch.insts.len());
        }
        tasks.spawn(crate::hub::run(self.clone()));
        let a = self.clone();
        tokio::spawn(async move { a.relaunch(relaunch).await });
        // Host firewall: rendered always, programmed unless `firewall = "off"` (and only when
        // the agent may). Re-asserted every 15 min (rules flushed by hand come back; CDN
        // addresses of the egress lock are re-resolved).
        self.apply_firewall();
        {
            let a = self.clone();
            tasks.spawn(async move {
                loop {
                    tokio::time::sleep(Duration::from_secs(900)).await;
                    *a.firewall_hash.lock().unwrap_or_else(|e| e.into_inner()) = None;
                    a.apply_firewall();
                }
            });
        }
        self.startup_self_check();

        self.shutdown.cancelled().await;
        info!("shutting down");
        self.event(Event::info(EventKind::AgentStopping, "agent stopping"));
        // Give the writer a moment to flush agent_stopping.
        tokio::time::sleep(Duration::from_millis(300)).await;
        tasks.abort_all();
        if self.cfg.keep_engines_on_exit {
            info!("leaving engines running (keep_engines_on_exit); the next agent start re-adopts them");
        } else {
            self.stop_all_engines().await;
        }
        Ok(())
    }

    /// Start-up self-check (`security audit` rules): log every GPU Deck listener reachable from
    /// the network; adopted engines listening off loopback were already stopped by
    /// `adopt_running`.
    fn startup_self_check(&self) {
        let ls = crate::audit::listeners();
        let range = self.rt().port_range([10240, 10340]);
        let engine_ports = crate::audit::engine_ports(&self.paths);
        for f in crate::audit::assess(&self.cfg, range, &engine_ports, &ls) {
            match f.level {
                crate::audit::Level::Exposed => warn!(
                    addr = %f.listener.addr, port = f.listener.port, process = ?f.listener.process,
                    "security self-check: {}", f.note
                ),
                crate::audit::Level::Warning => info!(
                    addr = %f.listener.addr, port = f.listener.port,
                    "security self-check: {}", f.note
                ),
                _ => {}
            }
        }
    }

    /// Stop every engine (agent shutdown with `keep_engines_on_exit = false`; normally engines
    /// keep running and the next agent start re-adopts them).
    pub async fn stop_all_engines(&self) {
        let procs: Vec<_> = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .values()
            .filter_map(|i| i.proc.clone())
            .collect();
        let routers: Vec<_> = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .values()
            .filter_map(|p| p.proc.clone())
            .collect();
        for p in procs.into_iter().chain(routers) {
            p.stop(Duration::from_secs(10)).await;
        }
    }
}

/// A globally routable address (not loopback, private, CGNAT, link-local or ULA).
pub fn is_public_ip(ip: std::net::IpAddr) -> bool {
    match ip {
        std::net::IpAddr::V4(v) => {
            let o = v.octets();
            !(v.is_loopback()
                || v.is_private()
                || v.is_link_local()
                || v.is_unspecified()
                || v.is_broadcast()
                || v.is_documentation()
                || (o[0] == 100 && (64..128).contains(&o[1])))
        }
        std::net::IpAddr::V6(v) => {
            let s = v.segments();
            !(v.is_loopback()
                || v.is_unspecified()
                || (s[0] & 0xfe00) == 0xfc00
                || (s[0] & 0xffc0) == 0xfe80)
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn public_ips() {
        for (a, want) in [
            ("10.0.0.1", false),
            ("192.168.1.2", false),
            ("100.64.3.4", false),
            ("169.254.1.1", false),
            ("127.0.0.1", false),
            ("8.8.8.8", true),
            ("fd00::1", false),
            ("fe80::1", false),
            ("2a01:4f8::1", true),
        ] {
            assert_eq!(super::is_public_ip(a.parse().unwrap()), want, "{a}");
        }
    }
}
