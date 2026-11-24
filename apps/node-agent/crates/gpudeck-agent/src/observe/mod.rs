//! Observed (external) engines: model servers GPU Deck did not start (`agent.toml` `[[observe]]`,
//! `protocol.md` §12). Strictly read-only:
//!
//! * network: only `GET <url><metrics_path>` (default `/metrics`), `GET <url>/health` and
//!   `GET <url>/v1/models`, 2 s timeout, no redirects, no proxy, bodies capped, one poll every
//!   `interval_seconds` (default 10) and exponential backoff (2 s doubling to 60 s) while the
//!   server does not answer;
//! * `log_file`: opened read-only, never locked or written, followed from the end, rotation and
//!   truncation detected by inode / size, bounded reads ([`log`]). New lines are parsed (engine
//!   stats, access lines) and shipped to the hub as `event(log)` for the external instance:
//!   redacted, progress bars and probe lines dropped, rate limited. With no `url` the entry makes
//!   no network request at all;
//! * processes: found read-only through `/proc` (`listen_port` / `pid_cmd_contains`, [`procs`])
//!   to attribute NVML per-process GPU memory; never signalled. Observed local ports are kept out
//!   of the agent's own port allocation, so it never binds (or bind-tests) them.
//!
//! Samples (`EngineSample.observed = true`, instance id `uuid5(node_id, "observe:<name>")`),
//! `inventory.observed_engines` and the log events are sent only when the hub listed the
//! `observed_engines` feature in `hello_ack`.

pub mod log;
pub mod procs;

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use gpudeck_protocol::{
    EngineSample, Event, EventKind, InstanceHealth, JsonObject, LogBatch, LogLine, LogSource,
    LogStream, ObservedEngine, ObservedEngineKind, Severity, Timestamp,
};
use uuid::Uuid;

use crate::backend::EngineCatalog;
use crate::config::ObserveConfig;
use log::{LogStats, LogTail};

/// Per-request timeout (the contract caps it at 2 s).
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(2);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(1);
const MAX_METRICS_BYTES: usize = 8 * 1024 * 1024;
const MAX_SMALL_BYTES: usize = 1024 * 1024;
const MODELS_REFRESH: Duration = Duration::from_secs(60);
const PROCS_REFRESH: Duration = Duration::from_secs(30);
const MAX_MODELS: usize = 64;
const DEFAULT_INTERVAL: f64 = 10.0;
/// Log shipping limits per entry and cycle (a cycle is the metrics interval, 2 s by default).
const SHIP_MAX_LINES: usize = 200;
const SHIP_MAX_BYTES: usize = 128 * 1024;
const SHIP_MAX_LINE: usize = 4096;

/// Backoff after `failures` consecutive failed polls: 2, 4, 8, ... capped at 60 seconds.
pub fn backoff(failures: u32) -> Duration {
    Duration::from_secs(2u64.saturating_pow(failures.clamp(1, 6)).min(60))
}

/// `uuid5(node_id, "observe:<name>")`: the observed engine's instance id.
pub fn observe_id(node_id: &Uuid, name: &str) -> Uuid {
    Uuid::new_v5(node_id, format!("observe:{name}").as_bytes())
}

/// `uuid5(node_id, "observe-deployment:<name>")`.
pub fn observe_deployment_id(node_id: &Uuid, name: &str) -> Uuid {
    Uuid::new_v5(node_id, format!("observe-deployment:{name}").as_bytes())
}

/// A validated `[[observe]]` entry.
#[derive(Debug, Clone, PartialEq)]
pub struct Target {
    pub name: String,
    pub kind: ObservedEngineKind,
    pub id: Uuid,
    pub deployment_id: Uuid,
    /// `scheme://host:port[/prefix]`, no trailing slash, no credentials or query.
    pub base: Option<String>,
    /// `host:port` (what the hub sees).
    pub endpoint: Option<String>,
    /// Port of a loopback / local endpoint (kept out of the agent's port allocation).
    pub local_port: Option<u16>,
    pub metrics_path: String,
    pub model: Option<String>,
    pub log_file: Option<PathBuf>,
    pub listen_port: Option<u16>,
    pub pid_cmd_contains: Option<String>,
    pub interval: Duration,
}

impl Target {
    pub fn metrics_kind(&self) -> bool {
        matches!(
            self.kind,
            ObservedEngineKind::Vllm
                | ObservedEngineKind::Sglang
                | ObservedEngineKind::Llamacpp
                | ObservedEngineKind::Tgi
        )
    }

    fn sources(&self) -> Vec<String> {
        let mut s = vec![];
        if self.base.is_some() {
            if self.metrics_kind() {
                s.push("metrics".to_string());
            }
            s.push("health".into());
            if self.kind != ObservedEngineKind::Generic {
                s.push("models".into());
            }
        }
        if self.log_file.is_some() {
            s.push("log".into());
        }
        s
    }
}

fn valid_name(name: &str) -> bool {
    let mut chars = name.chars();
    name.len() <= 64
        && chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

/// Validate one entry. Errors are explained in the agent log and the entry is skipped.
pub fn target(node_id: &Uuid, c: &ObserveConfig) -> Result<Target, String> {
    let n = &c.name;
    if !valid_name(n) {
        return Err(format!(
            "observe name {n:?} must match [A-Za-z0-9][A-Za-z0-9._-]* (max 64)"
        ));
    }
    if c.url.is_none() && c.log_file.is_none() {
        return Err(format!("observe {n:?}: set url and/or log_file"));
    }
    let (mut base, mut endpoint, mut local_port) = (None, None, None);
    if let Some(raw) = &c.url {
        let u = reqwest::Url::parse(raw.trim())
            .map_err(|e| format!("observe {n:?}: invalid url: {e}"))?;
        if !matches!(u.scheme(), "http" | "https") {
            return Err(format!("observe {n:?}: url must be http or https"));
        }
        if !u.username().is_empty() || u.password().is_some() {
            return Err(format!(
                "observe {n:?}: credentials in the url are not allowed"
            ));
        }
        let host = u
            .host_str()
            .ok_or_else(|| format!("observe {n:?}: url has no host"))?
            .to_string();
        let port = u
            .port_or_known_default()
            .ok_or_else(|| format!("observe {n:?}: url has no port"))?;
        let prefix = u.path().trim_end_matches('/');
        base = Some(format!("{}://{host}:{port}{prefix}", u.scheme()));
        endpoint = Some(format!("{host}:{port}"));
        let bare = host.trim_start_matches('[').trim_end_matches(']');
        let local = matches!(bare, "localhost" | "0.0.0.0" | "::")
            || bare
                .parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback());
        local_port = local.then_some(port);
    }
    let metrics_path = c.metrics_path.clone().unwrap_or_else(|| "/metrics".into());
    if !metrics_path.starts_with('/') || metrics_path.contains("..") || metrics_path.contains("://")
    {
        return Err(format!(
            "observe {n:?}: metrics_path must be an absolute path such as /metrics"
        ));
    }
    let interval = c.interval_seconds.unwrap_or(DEFAULT_INTERVAL);
    if !(2.0..=300.0).contains(&interval) {
        return Err(format!(
            "observe {n:?}: interval_seconds must be between 2 and 300"
        ));
    }
    Ok(Target {
        name: n.clone(),
        kind: c.engine,
        id: observe_id(node_id, n),
        deployment_id: observe_deployment_id(node_id, n),
        base,
        endpoint,
        local_port,
        metrics_path,
        model: c
            .model
            .clone()
            .map(|m| m.trim().chars().take(512).collect::<String>())
            .filter(|m| !m.is_empty()),
        log_file: c
            .log_file
            .as_ref()
            .map(|p| crate::template::expand_home(&p.display().to_string())),
        listen_port: c.listen_port.or(local_port),
        pid_cmd_contains: c
            .pid_cmd_contains
            .clone()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty()),
        interval: Duration::from_secs_f64(interval),
    })
}

/// GPU processes from this cycle's device samples: pid → `(device_id, used bytes)` per device.
pub type GpuProcs = HashMap<u32, Vec<(Uuid, Option<u64>)>>;

// ------------------------------------------------------------------------------------ state

#[derive(Debug)]
struct State {
    health: InstanceHealth,
    message: Option<String>,
    models: Vec<String>,
    version: Option<String>,
    last_ok_at: Option<Timestamp>,
    failures: u32,
    next_try: Option<Instant>,
    next_poll: Option<Instant>,
    models_at: Option<Instant>,
    tail: Option<LogTail>,
    log: LogStats,
    procs: procs::Match,
    procs_at: Option<Instant>,
    procs_note: Option<String>,
    device_ids: Vec<Uuid>,
}

impl State {
    fn new(t: &Target) -> Self {
        Self {
            health: InstanceHealth::Unknown,
            message: None,
            models: t.model.iter().cloned().collect(),
            version: None,
            last_ok_at: None,
            failures: 0,
            next_try: None,
            next_poll: None,
            models_at: None,
            tail: t.log_file.as_ref().map(|p| LogTail::new(p.clone())),
            log: LogStats::default(),
            procs: procs::Match::default(),
            procs_at: None,
            procs_note: None,
            device_ids: vec![],
        }
    }
}

/// What one cycle produced.
#[derive(Debug, Default)]
pub struct Cycle {
    /// One sample per entry that was due this cycle.
    pub samples: Vec<EngineSample>,
    /// Health, models, version, pid or devices of an entry changed: send a fresh inventory.
    pub changed: bool,
    /// `event(log)` frames with the new log lines (only when shipping was asked for).
    pub log_events: Vec<Event>,
}

/// The observed engines of this agent and their last known state.
pub struct Observer {
    targets: Vec<Target>,
    state: std::sync::Mutex<BTreeMap<String, State>>,
    http: reqwest::Client,
    proc_root: PathBuf,
}

/// Outcome of one HTTP GET.
enum Got {
    Ok(String),
    Status(u16),
    Failed(String),
}

fn why(e: &reqwest::Error) -> String {
    if e.is_connect() {
        "connection refused".into()
    } else if e.is_timeout() {
        "timeout".into()
    } else {
        e.to_string()
    }
}

impl Observer {
    /// Build from the config, logging and skipping invalid or duplicate entries.
    pub fn new(node_id: &Uuid, entries: &[ObserveConfig]) -> Self {
        let mut targets: Vec<Target> = vec![];
        for c in entries {
            match target(node_id, c) {
                Ok(t) if targets.iter().any(|x| x.name == t.name) => {
                    tracing::warn!(
                        "observe {:?} is configured twice; keeping the first",
                        t.name
                    )
                }
                Ok(t) => targets.push(t),
                Err(e) => tracing::warn!("{e}; entry ignored"),
            }
        }
        let state = targets
            .iter()
            .map(|t| (t.name.clone(), State::new(t)))
            .collect();
        let http = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(REQUEST_TIMEOUT)
            .connect_timeout(CONNECT_TIMEOUT)
            .user_agent(concat!(
                "gpudeck-agent/",
                env!("CARGO_PKG_VERSION"),
                " (observe)"
            ))
            .build()
            .unwrap_or_default();
        Self {
            targets,
            state: std::sync::Mutex::new(state),
            http,
            proc_root: PathBuf::from("/proc"),
        }
    }

    /// Use another procfs root (tests).
    pub fn with_proc_root(mut self, root: PathBuf) -> Self {
        self.proc_root = root;
        self
    }

    pub fn is_empty(&self) -> bool {
        self.targets.is_empty()
    }

    pub fn targets(&self) -> &[Target] {
        &self.targets
    }

    /// Ports of observed local servers: the agent never binds (or bind-tests) them.
    pub fn local_ports(&self) -> Vec<u16> {
        self.targets
            .iter()
            .flat_map(|t| [t.local_port, t.listen_port])
            .flatten()
            .collect::<BTreeSet<u16>>()
            .into_iter()
            .collect()
    }

    /// `(observe id, matched process tree)` per entry, from the last process lookup.
    pub fn process_trees(&self) -> Vec<(Uuid, BTreeSet<u32>)> {
        let g = self.state.lock().unwrap_or_else(|e| e.into_inner());
        self.targets
            .iter()
            .filter_map(|t| g.get(&t.name).map(|s| (t.id, s.procs.tree.clone())))
            .filter(|(_, tree)| !tree.is_empty())
            .collect()
    }

    /// `inventory.observed_engines` / `hello.observed_engines`.
    pub fn snapshots(&self) -> Vec<ObservedEngine> {
        let g = self.state.lock().unwrap_or_else(|e| e.into_inner());
        self.targets
            .iter()
            .map(|t| {
                let st = g.get(&t.name);
                ObservedEngine {
                    observe_id: t.id,
                    deployment_id: t.deployment_id,
                    name: t.name.clone(),
                    engine: t.kind,
                    endpoint: t.endpoint.clone(),
                    sources: t.sources(),
                    models: st.map(|s| s.models.clone()).unwrap_or_default(),
                    version: st.and_then(|s| s.version.clone()),
                    health: st.map(|s| s.health).unwrap_or(InstanceHealth::Unknown),
                    message: st
                        .and_then(|s| s.message.clone())
                        .map(|m| m.chars().take(1024).collect()),
                    last_ok_at: st.and_then(|s| s.last_ok_at),
                    pid: st.and_then(|s| s.procs.pid),
                    device_ids: st.map(|s| s.device_ids.clone()).unwrap_or_default(),
                }
            })
            .collect()
    }

    async fn get(&self, url: &str, cap: usize) -> Got {
        let mut resp = match self.http.get(url).send().await {
            Ok(r) => r,
            Err(e) => return Got::Failed(why(&e)),
        };
        let status = resp.status().as_u16();
        if !(200..300).contains(&status) {
            return Got::Status(status);
        }
        let mut body: Vec<u8> = vec![];
        loop {
            match resp.chunk().await {
                Ok(Some(c)) => {
                    if body.len() + c.len() > cap {
                        return Got::Failed(format!("response larger than {cap} bytes"));
                    }
                    body.extend_from_slice(&c);
                }
                Ok(None) => break,
                Err(e) => return Got::Failed(format!("body: {}", why(&e))),
            }
        }
        Got::Ok(String::from_utf8_lossy(&body).into_owned())
    }

    /// One cycle over every entry (called every metrics interval). `gpu` are this cycle's GPU
    /// processes; `ship_logs` turns new log lines into `event(log)` frames.
    pub async fn cycle(
        &self,
        engines: &dyn EngineCatalog,
        gpu: &GpuProcs,
        ship_logs: bool,
    ) -> Cycle {
        let polls = self
            .targets
            .iter()
            .map(|t| self.poll(t, engines, gpu, ship_logs));
        let mut out = Cycle::default();
        for (sample, changed, events) in futures::future::join_all(polls).await {
            out.samples.extend(sample);
            out.changed |= changed;
            out.log_events.extend(events);
        }
        out
    }

    async fn network(&self, t: &Target, base: &str, want_models: bool) -> NetResult {
        let mut r = NetResult::default();
        if t.metrics_kind() {
            match self
                .get(&format!("{base}{}", t.metrics_path), MAX_METRICS_BYTES)
                .await
            {
                Got::Ok(text) => {
                    r.metrics = Some(text);
                    r.health = Some((InstanceHealth::Healthy, None));
                }
                Got::Status(code) => {
                    r.health = Some((
                        InstanceHealth::Unhealthy,
                        Some(format!("GET {} -> {code}", t.metrics_path)),
                    ))
                }
                Got::Failed(w) => {
                    r.health = Some((
                        InstanceHealth::Unhealthy,
                        Some(format!("GET {}: {w}", t.metrics_path)),
                    ))
                }
            }
        }
        if t.kind == ObservedEngineKind::Openai {
            r.health = Some(
                match self
                    .get(&format!("{base}/v1/models"), MAX_SMALL_BYTES)
                    .await
                {
                    Got::Ok(body) => {
                        r.models = Some(parse_models(&body));
                        (InstanceHealth::Healthy, None)
                    }
                    Got::Status(code) => (
                        InstanceHealth::Unhealthy,
                        Some(format!("GET /v1/models -> {code}")),
                    ),
                    Got::Failed(w) => (
                        InstanceHealth::Unhealthy,
                        Some(format!("GET /v1/models: {w}")),
                    ),
                },
            );
        } else if !matches!(r.health, Some((InstanceHealth::Healthy, _))) {
            // generic, or metrics unavailable: the health endpoint decides.
            let metrics_err = r.health.take().and_then(|(_, m)| m);
            r.health = Some(
                match self.get(&format!("{base}/health"), MAX_SMALL_BYTES).await {
                    Got::Ok(_) => (InstanceHealth::Healthy, metrics_err),
                    Got::Status(503) => (
                        InstanceHealth::Unknown,
                        Some("GET /health -> 503 (starting or busy)".into()),
                    ),
                    Got::Status(code) => (
                        InstanceHealth::Unhealthy,
                        Some(format!("GET /health -> {code}")),
                    ),
                    Got::Failed(w) => {
                        (InstanceHealth::Unhealthy, Some(format!("GET /health: {w}")))
                    }
                },
            );
        }
        let up = matches!(r.health, Some((InstanceHealth::Healthy, _)));
        if up && want_models && r.models.is_none() && t.kind != ObservedEngineKind::Generic {
            if let Got::Ok(body) = self
                .get(&format!("{base}/v1/models"), MAX_SMALL_BYTES)
                .await
            {
                r.models = Some(parse_models(&body));
            }
        }
        r
    }

    async fn poll(
        &self,
        t: &Target,
        engines: &dyn EngineCatalog,
        gpu: &GpuProcs,
        ship_logs: bool,
    ) -> (Option<EngineSample>, bool, Vec<Event>) {
        let now = Instant::now();
        let (due, backing_off, want_models, want_procs, tail) = {
            let mut g = self.state.lock().unwrap_or_else(|e| e.into_inner());
            let st = g.entry(t.name.clone()).or_insert_with(|| State::new(t));
            let due = st.next_poll.is_none_or(|n| n <= now);
            if due {
                st.next_poll = Some(now + t.interval);
            }
            (
                due,
                st.next_try.is_some_and(|n| n > now),
                st.models_at.is_none_or(|a| a.elapsed() >= MODELS_REFRESH),
                due && (t.listen_port.is_some() || t.pid_cmd_contains.is_some())
                    && st.procs_at.is_none_or(|a| a.elapsed() >= PROCS_REFRESH),
                st.tail.take(),
            )
        };

        // Network: only when due, configured and not backing off.
        let net = match (&t.base, due && !backing_off) {
            (Some(base), true) => Some(self.network(t, base, want_models).await),
            _ => None,
        };

        // Log file and /proc: blocking reads off the async threads.
        let proc_root = self.proc_root.clone();
        let (listen_port, cmd) = (t.listen_port, t.pid_cmd_contains.clone());
        let blocking = tokio::task::spawn_blocking(move || {
            let mut tail = tail;
            let read = tail.as_mut().map(|t| t.poll());
            let m = want_procs.then(|| {
                if cfg!(target_os = "linux") || proc_root != std::path::Path::new("/proc") {
                    procs::resolve(&proc_root, listen_port, cmd.as_deref(), std::process::id())
                } else {
                    procs::Match {
                        note: Some("process matching needs Linux /proc".into()),
                        ..Default::default()
                    }
                }
            });
            (tail, read, m)
        })
        .await;
        let (tail, read, matched) = match blocking {
            Ok(x) => x,
            Err(e) => {
                tracing::debug!(observe = %t.name, "blocking poll failed: {e}");
                (None, None, None)
            }
        };

        let mut g = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let st = g.entry(t.name.clone()).or_insert_with(|| State::new(t));
        let before = (
            st.health,
            st.models.clone(),
            st.version.clone(),
            st.procs.pid,
            st.device_ids.clone(),
        );
        // ---- log
        let mut events = vec![];
        let mut log_health: Option<(InstanceHealth, Option<String>)> = None;
        if let Some(read) = read {
            match read {
                Ok(read) => {
                    for line in &read.bootstrap {
                        st.log.ingest(t.kind, line, now, true);
                    }
                    for line in &read.lines {
                        st.log.ingest(t.kind, line, now, false);
                    }
                    log_health = Some(st.log.health());
                    if ship_logs {
                        events = log_events(t, &read.lines, read.skipped_bytes);
                    }
                }
                Err(e) => {
                    log_health = Some((
                        InstanceHealth::Unhealthy,
                        Some(format!(
                            "log file {}: {e}",
                            t.log_file
                                .as_deref()
                                .unwrap_or(std::path::Path::new(""))
                                .display()
                        )),
                    ))
                }
            }
        }
        st.tail = tail.or_else(|| t.log_file.as_ref().map(|p| LogTail::new(p.clone())));
        st.log.tick(now);
        if st.log.version.is_some() {
            st.version = st.log.version.clone();
        }
        // ---- processes
        if let Some(m) = matched {
            if m.note != st.procs_note {
                if let Some(n) = &m.note {
                    tracing::warn!(observe = %t.name, "{n}");
                }
                st.procs_note = m.note.clone();
            }
            st.procs = m;
            st.procs_at = Some(now);
        }
        let mut vram: Option<u64> = None;
        let mut devices: BTreeSet<Uuid> = BTreeSet::new();
        let mut gpu_pids: Vec<u32> = vec![];
        for pid in &st.procs.tree {
            if let Some(uses) = gpu.get(pid) {
                gpu_pids.push(*pid);
                for (dev, bytes) in uses {
                    devices.insert(*dev);
                    if let Some(b) = bytes {
                        vram = Some(vram.unwrap_or(0) + b);
                    }
                }
            }
        }
        if !gpu.is_empty() || st.procs.tree.is_empty() {
            st.device_ids = devices.into_iter().collect();
        }
        // ---- models
        if let Some(m) = net.as_ref().and_then(|n| n.models.clone()) {
            st.models_at = Some(now);
            st.models = merge_models(t.model.as_deref(), m);
        } else if let Some(m) = st.log.model.clone() {
            if !st.models.contains(&m) && (st.models.is_empty() || t.model.is_some()) {
                st.models = merge_models(t.model.as_deref(), vec![m]);
            }
        }
        // ---- health
        match (net.as_ref().and_then(|n| n.health.clone()), backing_off) {
            (Some((h, msg)), _) => {
                st.health = h;
                st.message = msg;
                if h == InstanceHealth::Unhealthy {
                    st.failures += 1;
                    st.next_try = Some(now + backoff(st.failures));
                } else {
                    st.failures = 0;
                    st.next_try = None;
                }
            }
            (None, true) => {} // keep the last network verdict while backing off
            (None, false) if t.base.is_none() => {
                if let Some((h, msg)) = log_health {
                    st.health = h;
                    st.message = msg;
                }
            }
            (None, false) => {}
        }
        if st.health == InstanceHealth::Healthy {
            st.last_ok_at = Some(gpudeck_protocol::now());
        }
        let changed = before
            != (
                st.health,
                st.models.clone(),
                st.version.clone(),
                st.procs.pid,
                st.device_ids.clone(),
            );
        if !due {
            return (None, changed, events);
        }

        // ---- the sample
        let mut s = net
            .as_ref()
            .and_then(|n| n.metrics.as_deref())
            .and_then(|text| engines.observed_sample(t.kind, &t.name, text))
            .unwrap_or_default();
        let from_log = st.log.fill(&mut s, now);
        s.ts = gpudeck_protocol::now();
        s.instance_id = t.id;
        s.deployment_id = t.deployment_id;
        s.observed = true;
        s.health = Some(st.health);
        if s.process_vram_bytes.is_none() {
            s.process_vram_bytes = vram;
        }
        if s.process_rss_bytes.is_none() {
            s.process_rss_bytes = st.procs.pid.and_then(crate::system::process_rss);
        }
        let extra = s.extra.get_or_insert_with(JsonObject::new);
        extra.insert("engine".into(), t.kind.as_str().into());
        extra.insert("observed_name".into(), t.name.clone().into());
        if let Some(m) = st.models.first() {
            extra.insert("served_model_name".into(), m.clone().into());
        }
        if let Some(ep) = &t.endpoint {
            extra.insert("endpoint".into(), ep.clone().into());
        }
        if !gpu_pids.is_empty() {
            gpu_pids.sort_unstable();
            extra.insert("gpu_pids".into(), serde_json::json!(gpu_pids));
        }
        if from_log {
            extra.insert("log_derived".into(), true.into());
        }
        (Some(s), changed, events)
    }
}

#[derive(Debug, Default)]
struct NetResult {
    metrics: Option<String>,
    health: Option<(InstanceHealth, Option<String>)>,
    models: Option<Vec<String>>,
}

/// New log lines of `t` → `event(log)` frames for its external instance: redacted, progress
/// bars and probe lines dropped, at most [`SHIP_MAX_LINES`] lines / [`SHIP_MAX_BYTES`] per cycle.
pub fn log_events(t: &Target, lines: &[String], skipped_bytes: u64) -> Vec<Event> {
    let mut out: Vec<LogLine> = vec![];
    let mut bytes = 0usize;
    let mut dropped = 0usize;
    for raw in lines {
        let Some(text) = log::shippable(raw, SHIP_MAX_LINE) else {
            continue;
        };
        if out.len() >= SHIP_MAX_LINES || bytes + text.len() > SHIP_MAX_BYTES {
            dropped += 1;
            continue;
        }
        bytes += text.len();
        out.push(LogLine {
            ts: gpudeck_protocol::now(),
            level: Some(crate::supervisor::guess_level(&text)),
            stream: Some(LogStream::App),
            text,
        });
    }
    if dropped > 0 || skipped_bytes > 0 {
        out.push(LogLine {
            ts: gpudeck_protocol::now(),
            level: Some(gpudeck_protocol::LogLevel::Warn),
            stream: Some(LogStream::App),
            text: format!(
                "[gpudeck] {dropped} log lines and {skipped_bytes} bytes not forwarded (rate limit)"
            ),
        });
    }
    if out.is_empty() {
        return vec![];
    }
    let mut ev = Event::new(
        EventKind::Log,
        Severity::Info,
        format!("{} log lines from observed {}", out.len(), t.name),
    );
    ev.instance_id = Some(t.id);
    ev.deployment_id = Some(t.deployment_id);
    ev.log = Some(LogBatch {
        source: LogSource::Engine,
        stream: Some(LogStream::App),
        lines: out,
    });
    vec![ev]
}

/// `GET /v1/models` → model ids (`data[].id`), at most 64.
pub fn parse_models(body: &str) -> Vec<String> {
    let v: serde_json::Value = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(_) => return vec![],
    };
    v.get("data")
        .or_else(|| v.get("models"))
        .and_then(|d| d.as_array())
        .map(|list| {
            list.iter()
                .filter_map(|m| {
                    m.get("id")
                        .or_else(|| m.get("name"))
                        .and_then(|x| x.as_str())
                })
                .filter(|x| !x.is_empty())
                .map(|x| x.chars().take(512).collect())
                .take(MAX_MODELS)
                .collect()
        })
        .unwrap_or_default()
}

/// The configured model first, then the discovered ones (deduplicated).
fn merge_models(configured: Option<&str>, found: Vec<String>) -> Vec<String> {
    let mut out: Vec<String> = configured.map(str::to_owned).into_iter().collect();
    for m in found {
        if !out.contains(&m) {
            out.push(m);
        }
    }
    out.truncate(MAX_MODELS);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    pub(crate) fn cfg(name: &str, engine: ObservedEngineKind, url: Option<&str>) -> ObserveConfig {
        ObserveConfig {
            name: name.into(),
            engine,
            url: url.map(str::to_owned),
            metrics_path: None,
            model: None,
            log_file: None,
            listen_port: None,
            pid_cmd_contains: None,
            interval_seconds: None,
        }
    }

    #[test]
    fn ids_are_uuid5_of_the_node() {
        let node: Uuid = "6f1c2b2e-0b2b-4d8e-9e0e-1b2c3d4e5f60".parse().unwrap();
        // Python: uuid.uuid5(UUID(node), "observe:main") — the values of the protocol example.
        assert_eq!(
            observe_id(&node, "main").to_string(),
            "4be123d1-7ce4-5141-bc91-0147ebbcfd96"
        );
        assert_eq!(
            observe_deployment_id(&node, "main").to_string(),
            "35ebcad7-77e0-5851-b72f-15ef8f42d19a"
        );
    }

    #[test]
    fn targets_validate_urls_names_and_intervals() {
        let node = Uuid::new_v4();
        let t = target(
            &node,
            &cfg(
                "main",
                ObservedEngineKind::Vllm,
                Some("http://127.0.0.1:8000/"),
            ),
        )
        .unwrap();
        assert_eq!(t.base.as_deref(), Some("http://127.0.0.1:8000"));
        assert_eq!(t.endpoint.as_deref(), Some("127.0.0.1:8000"));
        assert_eq!((t.local_port, t.listen_port), (Some(8000), Some(8000)));
        assert_eq!(t.metrics_path, "/metrics");
        assert_eq!(t.interval, Duration::from_secs(10));
        let t = target(
            &node,
            &cfg(
                "p",
                ObservedEngineKind::Tgi,
                Some("https://gpu-box.lan/tgi?x=1"),
            ),
        )
        .unwrap();
        assert_eq!(t.base.as_deref(), Some("https://gpu-box.lan:443/tgi"));
        assert_eq!(t.endpoint.as_deref(), Some("gpu-box.lan:443"));
        assert_eq!((t.local_port, t.listen_port), (None, None));
        for bad in [
            cfg(
                "x",
                ObservedEngineKind::Vllm,
                Some("http://u:p@127.0.0.1:8000"),
            ),
            cfg("x", ObservedEngineKind::Vllm, Some("ftp://127.0.0.1")),
            cfg("x", ObservedEngineKind::Vllm, None),
            cfg("-bad", ObservedEngineKind::Vllm, Some("http://127.0.0.1:1")),
            cfg("a b", ObservedEngineKind::Vllm, Some("http://127.0.0.1:1")),
        ] {
            assert!(target(&node, &bad).is_err(), "{bad:?}");
        }
        let mut c = cfg("x", ObservedEngineKind::Vllm, Some("http://127.0.0.1:1"));
        c.metrics_path = Some("http://evil/metrics".into());
        assert!(target(&node, &c).is_err());
        let mut c = cfg("x", ObservedEngineKind::Vllm, Some("http://127.0.0.1:1"));
        c.interval_seconds = Some(0.5);
        assert!(target(&node, &c).is_err());
        // Log-only generic entry with its own listen port.
        let mut c = cfg("img", ObservedEngineKind::Generic, None);
        c.log_file = Some("/var/log/engines/img.log".into());
        c.listen_port = Some(8100);
        let t = target(&node, &c).unwrap();
        assert_eq!((t.base.clone(), t.listen_port), (None, Some(8100)));
        assert_eq!(t.sources(), vec!["log".to_string()]);
        // Duplicates keep the first entry; observed ports are reserved.
        let o = Observer::new(
            &node,
            &[
                cfg("a", ObservedEngineKind::Generic, Some("http://127.0.0.1:9")),
                cfg("a", ObservedEngineKind::Vllm, Some("http://127.0.0.1:8")),
                c,
            ],
        );
        assert_eq!(o.targets().len(), 2);
        assert_eq!(o.local_ports(), vec![9, 8100]);
    }

    #[test]
    fn backoff_doubles_to_a_minute() {
        assert_eq!(backoff(1), Duration::from_secs(2));
        assert_eq!(backoff(2), Duration::from_secs(4));
        assert_eq!(backoff(5), Duration::from_secs(32));
        assert_eq!(backoff(6), Duration::from_secs(60));
        assert_eq!(backoff(50), Duration::from_secs(60));
    }

    #[test]
    fn models_parse() {
        let body = include_str!("../../../../fixtures/engines/vllm/v1_models.json");
        assert_eq!(parse_models(body), vec!["Qwen/Qwen3-8B".to_string()]);
        assert!(parse_models("not json").is_empty());
        assert_eq!(
            merge_models(Some("a"), vec!["b".into(), "a".into()]),
            vec!["a".to_string(), "b".to_string()]
        );
    }

    #[test]
    fn log_events_are_redacted_filtered_and_capped() {
        let node = Uuid::new_v4();
        let mut c = cfg("emb", ObservedEngineKind::Generic, None);
        c.log_file = Some("/tmp/emb.log".into());
        let t = target(&node, &c).unwrap();
        let mut lines = vec![
            "INFO:     127.0.0.1:1 - \"GET /health HTTP/1.1\" 200 OK".to_string(),
            " 40%|████      | 2/5 [00:01<00:01,  2.00it/s]".to_string(),
            "loaded with api_key=abcd1234efgh".to_string(),
        ];
        lines.extend((0..300).map(|i| format!("line {i}")));
        let evs = log_events(&t, &lines, 0);
        assert_eq!(evs.len(), 1);
        let ev = &evs[0];
        assert_eq!(ev.instance_id, Some(t.id));
        assert_eq!(ev.deployment_id, Some(t.deployment_id));
        let batch = ev.log.as_ref().unwrap();
        assert_eq!(batch.lines[0].text, "loaded with api_key=[REDACTED]");
        assert_eq!(
            batch.lines.len(),
            SHIP_MAX_LINES + 1,
            "capped + one summary line"
        );
        assert!(batch.lines.last().unwrap().text.contains("not forwarded"));
        assert!(log_events(&t, &["   ".to_string()], 0).is_empty());
    }
}
