//! Hub connection (`protocol.md` §1, §3): mTLS WebSocket to `wss://<hub>:8443/agent/v1/ws`
//! (TLS 1.3, node certificate), `hello` → `hello_ack`, inventory + undelivered terminal results
//! after every (re)connect, heartbeats, periodic and on-change inventories, inbound `command`
//! and `config_push`, reconnect with exponential backoff (1 s ×2, full jitter, cap 60 s, reset
//! after 60 s up).

use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use futures::{SinkExt, StreamExt};
use gpudeck_protocol::{
    hub_feature, CacheInventory, CacheSummary, Capabilities, ConfigPush, EngineProcessInfo,
    EngineRuntime, Envelope, Event, EventKind, Heartbeat, HeartbeatInstance, Hello, HelloAck,
    InstanceState, InstanceStatus, Inventory, KubernetesInfo, Message, ProxyInfo, Settings,
    HUB_WS_PATH, PROTOCOL_VERSION,
};
use rand::Rng;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::tungstenite::protocol::{CloseFrame, WebSocketConfig};
use tokio_tungstenite::tungstenite::Message as Ws;
use tracing::{debug, info, warn};
use uuid::Uuid;

use crate::agent::Agent;

/// Reconnect delay: exponential from 1 s, factor 2, cap 60 s, full jitter (min 100 ms).
pub fn backoff(attempt: u32) -> Duration {
    let cap = 60f64.min(2f64.powi(attempt.min(10) as i32));
    let secs = rand::thread_rng().gen_range(0.0..=cap);
    Duration::from_secs_f64(secs.max(0.1))
}

/// `https://hub:8443` → `wss://hub:8443/agent/v1/ws`.
pub fn ws_url(hub_url: &str) -> String {
    let u = hub_url.trim_end_matches('/');
    if u.starts_with("wss://") || u.starts_with("ws://") {
        return if u.ends_with(HUB_WS_PATH) {
            u.to_string()
        } else {
            format!("{u}{HUB_WS_PATH}")
        };
    }
    let rest = u
        .strip_prefix("https://")
        .or_else(|| u.strip_prefix("http://"))
        .unwrap_or(u);
    format!("wss://{rest}{HUB_WS_PATH}")
}

pub async fn run(agent: Arc<Agent>) {
    let mut attempt = 0u32;
    loop {
        if agent.shutdown.is_cancelled() {
            return;
        }
        let started = Instant::now();
        match connect_once(&agent).await {
            Ok(why) => info!("hub connection ended: {why}"),
            Err(e) => warn!("hub connection failed: {e:#}"),
        }
        agent.outbox.set_connected(false);
        agent
            .rt
            .write()
            .unwrap_or_else(|e| e.into_inner())
            .connected_since = None;
        if started.elapsed() >= Duration::from_secs(60) {
            attempt = 0;
        }
        let delay = backoff(attempt);
        attempt = attempt.saturating_add(1);
        debug!("reconnecting to hub in {:?}", delay);
        tokio::select! {
            _ = tokio::time::sleep(delay) => {}
            _ = agent.shutdown.cancelled() => return,
        }
    }
}

async fn connect_once(agent: &Arc<Agent>) -> Result<String> {
    let hub = agent
        .cfg
        .hub_url
        .clone()
        .ok_or_else(|| anyhow!("hub_url not configured"))?;
    let url = ws_url(&hub);
    let identity = agent.identity();
    let tls = identity.hub_client_config()?;
    let req = url.as_str().into_client_request().context("ws request")?;
    let mut wscfg = WebSocketConfig::default();
    wscfg.max_message_size = Some(gpudeck_protocol::MAX_FRAME_BYTES);
    wscfg.max_frame_size = Some(gpudeck_protocol::MAX_FRAME_BYTES);
    let connector = tokio_tungstenite::Connector::Rustls(Arc::new(tls));
    let (ws, _) = tokio::time::timeout(
        Duration::from_secs(20),
        tokio_tungstenite::connect_async_tls_with_config(req, Some(wscfg), true, Some(connector)),
    )
    .await
    .map_err(|_| anyhow!("connect timeout"))?
    .with_context(|| format!("connect {url}"))?;
    let (mut sink, mut stream) = ws.split();

    // The first hello carries device runtime versions (capabilities.cuda_version, ...).
    if agent
        .last_devices
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .is_empty()
    {
        let devices = agent.devices.detect().await;
        agent.note_device_health(&devices);
    }
    let hello = agent.build_hello();
    let probing_observed = !hello.observed_engines.is_empty() && !agent.rt().hub_features_known;
    let hello_env = agent.outbox.envelope(Message::Hello(hello));
    sink.send(Ws::Text(hello_env.to_json()?.into()))
        .await
        .context("send hello")?;
    let ack: HelloAck = tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            match stream.next().await {
                Some(Ok(Ws::Text(t))) => {
                    let env = Envelope::from_json(&t).context("parse frame")?;
                    match env.message {
                        Message::HelloAck(a) => return Ok::<_, anyhow::Error>(a),
                        other => debug!("frame before hello_ack ignored: {}", other.type_name()),
                    }
                }
                Some(Ok(Ws::Close(f))) => bail!(
                    "hub closed before hello_ack: {}",
                    describe_close(f.as_ref())
                ),
                Some(Ok(_)) => {}
                Some(Err(e)) => bail!("read: {e}"),
                None => bail!("hub closed the connection"),
            }
        }
    })
    .await
    .map_err(|_| anyhow!("no hello_ack within 30 s"))
    .and_then(|r| r)
    .inspect_err(|_| {
        if probing_observed {
            // A hub that predates observed engines may reject the field: omit it next time.
            agent
                .rt
                .write()
                .unwrap_or_else(|e| e.into_inner())
                .hub_features_known = true;
        }
    })?;
    if !ack.accepted {
        bail!(
            "hello rejected by hub: {}",
            ack.reason.unwrap_or_else(|| "unspecified".into())
        );
    }
    if ack.node_id != agent.node_id {
        warn!(hub = %ack.node_id, local = %agent.node_id, "hello_ack node_id differs from local node_id");
    }
    {
        let mut rt = agent.rt.write().unwrap_or_else(|e| e.into_inner());
        rt.intervals = ack.intervals.clone();
        rt.drain = ack.drain;
        rt.connected_since = Some(Instant::now());
        rt.hub_node_name = ack.node_name.clone();
        rt.hub_features = ack.features.clone();
        rt.hub_features_known = true;
    }
    let _ = crate::state::write_json(&agent.paths.hub_features_json(), &ack.features, 0o600);
    if !agent.observer.is_empty()
        && !ack
            .features
            .iter()
            .any(|f| f == hub_feature::OBSERVED_ENGINES)
    {
        warn!("[[observe]] is configured but the hub does not accept observed engines (upgrade the control plane); they are not reported");
    }
    info!(node = ?ack.node_name, config_version = ack.config_version, drain = ack.drain, "connected to hub");
    agent.outbox.set_connected(true);
    agent
        .outbox
        .send(Message::Inventory(agent.build_inventory().await));
    for (id, res) in agent.commands.undelivered() {
        agent.outbox.result(id, res);
    }

    // Writer: outbox → socket; tracks terminal results for delivery confirmation.
    let (stop_tx, mut stop_rx) = tokio::sync::oneshot::channel::<Option<CloseFrame>>();
    let a = agent.clone();
    let mut writer = tokio::spawn(async move {
        let mut sent: Vec<(Uuid, Instant)> = vec![];
        let mut tick = tokio::time::interval(Duration::from_secs(1));
        loop {
            tokio::select! {
                env = a.outbox.next() => {
                    let terminal = match &env.message { Message::CommandResult(r) if r.status.is_terminal() => env.correlation_id, _ => None };
                    let text = match env.to_json() { Ok(t) => t, Err(e) => { warn!("serialize {}: {e}", env.message.type_name()); continue; } };
                    if let Err(e) = sink.send(Ws::Text(text.into())).await {
                        a.outbox.requeue_front(env);
                        return Err(anyhow!("write: {e}"));
                    }
                    if let Some(id) = terminal { sent.push((id, Instant::now())); }
                }
                _ = tick.tick() => {
                    // A terminal result is delivered once its connection stayed up 5 s after the write.
                    let done: Vec<Uuid> = sent.iter().filter(|(_, t)| t.elapsed() >= Duration::from_secs(5)).map(|(id, _)| *id).collect();
                    if !done.is_empty() {
                        sent.retain(|(_, t)| t.elapsed() < Duration::from_secs(5));
                        a.commands.mark_delivered(&done);
                    }
                }
                frame = &mut stop_rx => {
                    let _ = sink.send(Ws::Close(frame.ok().flatten())).await;
                    let _ = sink.close().await;
                    return Ok(());
                }
            }
        }
    });

    let rt = agent.rt();
    let mut hb = tokio::time::interval(rt.heartbeat_interval());
    let mut inv = tokio::time::interval_at(
        tokio::time::Instant::now() + rt.inventory_interval(),
        rt.inventory_interval(),
    );
    hb.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let result: Result<String> = loop {
        tokio::select! {
            msg = stream.next() => match msg {
                Some(Ok(Ws::Text(t))) => agent.handle_frame(&t).await,
                Some(Ok(Ws::Close(f))) => break Ok(format!("closed by hub: {}", describe_close(f.as_ref()))),
                Some(Ok(_)) => {}
                Some(Err(e)) => break Err(anyhow!("read: {e}")),
                None => break Ok("connection closed".into()),
            },
            _ = hb.tick() => agent.outbox.send(Message::Heartbeat(agent.build_heartbeat())),
            _ = inv.tick() => agent.outbox.send(Message::Inventory(agent.build_inventory().await)),
            _ = agent.inventory_dirty.notified() => {
                tokio::time::sleep(Duration::from_millis(300)).await;
                agent.outbox.send(Message::Inventory(agent.build_inventory().await));
            }
            _ = agent.reconnect.notified() => {
                let _ = stop_tx.send(Some(CloseFrame { code: CloseCode::Normal, reason: "reconnecting with a new certificate".into() }));
                let _ = tokio::time::timeout(Duration::from_secs(2), &mut writer).await;
                return Ok("reconnect requested".into());
            }
            _ = agent.shutdown.cancelled() => {
                tokio::time::sleep(Duration::from_millis(200)).await;
                let _ = stop_tx.send(Some(CloseFrame { code: CloseCode::Away, reason: "agent stopping".into() }));
                let _ = tokio::time::timeout(Duration::from_secs(2), &mut writer).await;
                return Ok("shutdown".into());
            }
            w = &mut writer => break match w { Ok(Ok(())) => Ok("writer stopped".into()), Ok(Err(e)) => Err(e), Err(e) => Err(anyhow!("writer task: {e}")) },
        }
    };
    agent.outbox.set_connected(false);
    writer.abort();
    result
}

fn describe_close(f: Option<&CloseFrame>) -> String {
    match f {
        Some(f) => {
            let code: u16 = f.code.into();
            let meaning = match code {
                4001 => " (identity mismatch)",
                4002 => " (protocol error)",
                4003 => " (rejected)",
                4004 => " (superseded by another connection)",
                1012 => " (hub restarting)",
                _ => "",
            };
            format!("{code}{meaning} {}", f.reason)
        }
        None => "no close frame".into(),
    }
}

impl Agent {
    pub async fn handle_frame(self: &Arc<Self>, text: &str) {
        let env = match Envelope::from_json(text) {
            Ok(e) => e,
            Err(e) => {
                warn!("unparseable frame from hub: {e}");
                return;
            }
        };
        match env.message {
            Message::Command(cmd) => self.commands.submit(self, env.id, cmd),
            Message::ConfigPush(cp) => self.apply_config_push(cp),
            Message::HelloAck(a) => {
                let mut rt = self.rt.write().unwrap_or_else(|e| e.into_inner());
                rt.intervals = a.intervals;
                rt.drain = a.drain;
                rt.hub_features = a.features.clone();
                rt.hub_features_known = true;
                drop(rt);
                let _ =
                    crate::state::write_json(&self.paths.hub_features_json(), &a.features, 0o600);
            }
            other => debug!("ignoring {} from hub", other.type_name()),
        }
    }

    /// `config_push` (§ config_push.json): monotonic version, full replace or section merge,
    /// persisted to `config.json`, answered with `config_applied` / `config_rejected`.
    pub fn apply_config_push(self: &Arc<Self>, cp: ConfigPush) {
        let held = self.rt().config_version;
        if cp.config_version <= held && !cp.full {
            debug!(
                version = cp.config_version,
                held, "stale config_push ignored"
            );
            let mut d = serde_json::Map::new();
            d.insert("config_version".into(), held.into());
            d.insert("ignored_version".into(), cp.config_version.into());
            self.event(
                Event::info(
                    EventKind::ConfigApplied,
                    format!(
                        "config_push v{} ignored (holding v{held})",
                        cp.config_version
                    ),
                )
                .details(d),
            );
            return;
        }
        if let Some(pem) = &cp.ca_bundle_pem {
            if crate::pki::parse_cert_bundle(pem).is_err() {
                let mut d = serde_json::Map::new();
                d.insert("config_version".into(), cp.config_version.into());
                d.insert(
                    "error".into(),
                    "ca_bundle_pem does not contain a certificate".into(),
                );
                self.event(
                    Event::warning(
                        EventKind::ConfigRejected,
                        format!(
                            "config_push v{} rejected: invalid CA bundle",
                            cp.config_version
                        ),
                    )
                    .details(d),
                );
                return;
            }
        }
        let merged = {
            let mut g = self.applied.lock().unwrap_or_else(|e| e.into_inner());
            let merged = match (cp.full, g.take()) {
                (true, _) | (false, None) => {
                    let mut c = cp.clone();
                    c.full = true;
                    c
                }
                (false, Some(mut prev)) => {
                    prev.config_version = cp.config_version;
                    if cp.routing.is_some() {
                        prev.routing = cp.routing.clone();
                    }
                    if cp.egress_allowlist.is_some() {
                        prev.egress_allowlist = cp.egress_allowlist.clone();
                    }
                    if cp.engine_catalog.is_some() {
                        prev.engine_catalog = cp.engine_catalog.clone();
                    }
                    if cp.crl.is_some() {
                        prev.crl = cp.crl.clone();
                    }
                    if cp.ca_bundle_pem.is_some() {
                        prev.ca_bundle_pem = cp.ca_bundle_pem.clone();
                    }
                    if cp.model_sources.is_some() {
                        prev.model_sources = cp.model_sources.clone();
                    }
                    if let Some(s) = &cp.settings {
                        prev.settings.get_or_insert_with(Settings::default).merge(s);
                    }
                    prev
                }
            };
            *g = Some(merged.clone());
            merged
        };
        if let Err(e) = crate::state::save_config(&self.paths, &merged) {
            warn!("save config.json: {e}");
        }
        {
            let mut rt = self.rt.write().unwrap_or_else(|e| e.into_inner());
            rt.config_version = cp.config_version;
            rt.settings = merged.settings.clone().unwrap_or_default();
        }
        self.apply_config_local(&merged);
        self.apply_settings();
        if cp.egress_allowlist.is_some() || cp.model_sources.is_some() {
            self.apply_egress();
        }
        let mut d = serde_json::Map::new();
        d.insert("config_version".into(), cp.config_version.into());
        d.insert(
            "routes".into(),
            merged.routing.as_ref().map(|r| r.len()).unwrap_or(0).into(),
        );
        self.event(
            Event::info(
                EventKind::ConfigApplied,
                format!("config v{} applied", cp.config_version),
            )
            .details(d),
        );
        self.inventory_dirty.notify_one();
    }

    /// Push a (merged) config into the components.
    pub fn apply_config_local(&self, c: &ConfigPush) {
        self.proxy.set_routing(c.routing.as_deref().unwrap_or(&[]));
        if let Some(crl) = &c.crl {
            self.proxy.set_crl(&crl.serials, &crl.fingerprints_sha256);
        }
        if let Some(ms) = &c.model_sources {
            self.cache.set_sources(ms.clone());
        }
        if let Some(pem) = &c.ca_bundle_pem {
            if let Err(e) = self.merge_ca_bundle(pem) {
                warn!("CA bundle update failed: {e:#}");
            }
        }
        if let Some(s) = &c.settings {
            self.rt.write().unwrap_or_else(|e| e.into_inner()).settings = s.clone();
        }
    }

    /// Trust the union of the current and the pushed CA bundle (CA rotation).
    fn merge_ca_bundle(&self, pem: &str) -> Result<()> {
        let current = std::fs::read_to_string(self.paths.ca()).unwrap_or_default();
        let have: Vec<String> = crate::pki::parse_cert_bundle(&current)
            .map(|v| {
                v.iter()
                    .map(|c| crate::pki::fingerprint_sha256(c))
                    .collect()
            })
            .unwrap_or_default();
        let new_certs = crate::pki::parse_cert_bundle(pem)?;
        if new_certs
            .iter()
            .all(|c| have.contains(&crate::pki::fingerprint_sha256(c)))
        {
            return Ok(());
        }
        let combined = format!("{}\n{}", current.trim_end(), pem.trim());
        crate::state::write_private(&self.paths.ca(), combined.as_bytes(), 0o600)?;
        let id = crate::tls::Identity::load(&self.paths)?;
        *self.identity.write().unwrap_or_else(|e| e.into_inner()) = Arc::new(id);
        self.proxy.invalidate_tls();
        Ok(())
    }

    /// `update_config` command.
    pub fn merge_settings(&self, s: &Settings) {
        {
            let mut rt = self.rt.write().unwrap_or_else(|e| e.into_inner());
            rt.settings.merge(s);
        }
        let mut g = self.applied.lock().unwrap_or_else(|e| e.into_inner());
        let c = g.get_or_insert_with(|| ConfigPush {
            config_version: self.rt().config_version.max(1),
            full: true,
            routing: None,
            egress_allowlist: None,
            engine_catalog: None,
            crl: None,
            ca_bundle_pem: None,
            settings: None,
            model_sources: None,
        });
        c.settings.get_or_insert_with(Settings::default).merge(s);
        let _ = crate::state::save_config(&self.paths, c);
        drop(g);
        self.apply_settings();
    }

    /// User-space runtime (CUDA / ROCm / MUSA) of the first detected device of `vendor`.
    fn device_runtime_version(&self, vendor: &str) -> Option<String> {
        self.last_devices
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .find(|d| d.vendor.as_str() == vendor)
            .and_then(|d| d.runtime_version.clone())
            .filter(|v| !v.is_empty())
    }

    pub fn build_hello(&self) -> Hello {
        let h = crate::system::host_info();
        let mut runtimes = vec![EngineRuntime::Native];
        if self.container_runtime.is_some() {
            runtimes.push(EngineRuntime::Container);
        }
        let kubernetes = std::env::var("KUBERNETES_SERVICE_HOST")
            .ok()
            .map(|_| KubernetesInfo {
                node_name: std::env::var("NODE_NAME").unwrap_or_default(),
                namespace: std::env::var("POD_NAMESPACE").unwrap_or_default(),
                pod: std::env::var("POD_NAME").unwrap_or_else(|_| h.hostname.clone()),
            });
        let proxy_port = self
            .proxy_addr
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .map(|a| a.port())
            .unwrap_or(self.cfg.proxy_port);
        // Behind a port forward the control plane must dial the forwarded port.
        let proxy_port = self.cfg.proxy_advertise_port.unwrap_or(proxy_port);
        Hello {
            agent_version: crate::join::AGENT_VERSION.into(),
            protocol_version: PROTOCOL_VERSION,
            os: h.os,
            os_version: h.os_version,
            arch: h.arch,
            kernel_version: h.kernel_version,
            hostname: h.hostname,
            ip_addresses: h.ip_addresses,
            cpu_cores: h.cpu_cores,
            memory_total_bytes: h.memory_total_bytes,
            boot_id: h.boot_id,
            started_at: self.started_at,
            capabilities: Capabilities {
                engines: self.engine_capabilities(),
                runtimes,
                container_runtime: self
                    .container_runtime
                    .as_deref()
                    .map(crate::system::container_runtime_label),
                glibc_version: crate::engine_compat::host_glibc_string(),
                cuda_version: self.device_runtime_version("nvidia"),
                rocm_version: self.device_runtime_version("amd"),
                musa_version: self.device_runtime_version("moorethreads"),
                metal: cfg!(target_os = "macos"),
                egress_lock: self.egress_kind,
                kubernetes,
                cloud: None,
                commands: gpudeck_protocol::CommandName::ALL.to_vec(),
                ssh_instances: self.container_runtime.is_some(),
            },
            cache: CacheSummary {
                dir: self.cache.dir.display().to_string(),
                limit_bytes: self.cache.limit_bytes(),
                used_bytes: self.cache.used_bytes(),
                free_bytes: self.cache.free_bytes(),
            },
            proxy: ProxyInfo {
                port: proxy_port,
                address: self.cfg.proxy_address.clone(),
                cert_fingerprint_sha256: Some(self.identity().fingerprint()),
            },
            config_version: self.rt().config_version,
            labels: self.cfg.labels.clone(),
            // From the first hello (the control plane shows them as soon as the node
            // connects); only withheld from a hub known not to take them (older hubs reject
            // unknown hello fields; the last hello_ack.features is kept in hub_features.json).
            observed_engines: if self.rt().hub_may_have(hub_feature::OBSERVED_ENGINES) {
                self.observer.snapshots()
            } else {
                vec![]
            },
        }
    }

    pub fn instance_statuses(&self) -> Vec<InstanceStatus> {
        let g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
        let mut out: Vec<InstanceStatus> = g
            .values()
            .map(|i| {
                let (a, w) = self.proxy.counts(&i.id);
                InstanceStatus {
                    instance_id: i.id,
                    deployment_id: Some(i.args.deployment_id),
                    engine_process_id: i.engine_process_id,
                    state: i.state,
                    health: i.health,
                    port: (i.port != 0).then_some(i.port),
                    served_model_name: Some(i.served_model_name.clone()),
                    pid: i.proc.as_ref().and_then(|p| p.pid),
                    container_id: i
                        .proc
                        .as_ref()
                        .and_then(|p| p.spec.container.as_ref().map(|c| c.name.clone())),
                    device_ids: i.args.devices.iter().map(|d| d.device_id).collect(),
                    active_sessions: Some(a),
                    waiting_sessions: Some(w),
                    message: i.message.clone(),
                    distributed_rank: i.args.launch.distributed.as_ref().map(|d| d.rank),
                }
            })
            .collect();
        out.sort_by_key(|s| s.instance_id);
        out
    }

    pub async fn build_inventory(&self) -> Inventory {
        let mut devices = self.devices.detect().await;
        for d in devices.iter_mut() {
            d.device_id = Some(self.device_id(d.vendor.as_str(), d.index));
        }
        self.note_device_health(&devices);
        let engine_processes = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .values()
            .map(|p| EngineProcessInfo {
                engine_process_id: p.id,
                engine: p.launch.engine.name,
                engine_version_id: Some(p.launch.engine.engine_version_id),
                port: p.port,
                pid: p.proc.as_ref().and_then(|x| x.pid),
                device_ids: p.args.devices.iter().map(|d| d.device_id).collect(),
                instance_ids: p.instance_ids.iter().cloned().collect(),
            })
            .collect();
        Inventory {
            devices,
            disks: crate::system::disks(&self.cache.dir),
            cache: CacheInventory {
                dir: self.cache.dir.display().to_string(),
                limit_bytes: self.cache.limit_bytes(),
                used_bytes: self.cache.used_bytes(),
                items: self.cache.items(),
            },
            instances: self.instance_statuses(),
            engine_processes,
            ssh_instances: self.ssh_statuses(),
            observed_engines: if self.rt().hub_has(hub_feature::OBSERVED_ENGINES) {
                self.observer.snapshots()
            } else {
                vec![]
            },
            system: Some(crate::system::system_info()),
            config_version: Some(self.rt().config_version),
        }
    }

    /// Emit `device_health_changed` when a device's health differs from the previous inventory.
    fn note_device_health(&self, devices: &[gpudeck_protocol::Device]) {
        let mut last = self.last_devices.lock().unwrap_or_else(|e| e.into_inner());
        for d in devices {
            let prev = last
                .iter()
                .find(|p| p.vendor == d.vendor && p.index == d.index)
                .map(|p| p.health);
            if let Some(prev) = prev {
                if prev != d.health {
                    let sev = if d.health == gpudeck_protocol::DeviceHealth::Unhealthy {
                        gpudeck_protocol::Severity::Critical
                    } else {
                        gpudeck_protocol::Severity::Warning
                    };
                    let mut ev = Event::new(
                        EventKind::DeviceHealthChanged,
                        sev,
                        format!("{} {} health {prev} -> {}", d.vendor, d.index, d.health),
                    );
                    ev.device_id = Some(self.device_id(d.vendor.as_str(), d.index));
                    ev.state = Some(gpudeck_protocol::StateChange {
                        from: Some(prev.to_string()),
                        to: d.health.to_string(),
                        reason: d.health_reason.clone(),
                    });
                    self.event(ev);
                }
            }
        }
        *last = devices.to_vec();
    }

    pub fn build_heartbeat(&self) -> Heartbeat {
        let instances = self
            .instance_statuses()
            .into_iter()
            .filter(|s| s.state != InstanceState::Stopped)
            .map(|s| HeartbeatInstance {
                instance_id: s.instance_id,
                state: s.state,
                health: s.health,
                active_sessions: s.active_sessions,
                waiting_sessions: s.waiting_sessions,
            })
            .collect();
        Heartbeat {
            uptime_seconds: self.started.elapsed().as_secs(),
            config_version: self.rt().config_version,
            instances,
            running_commands: self.commands.running_ids(),
            load: Some(crate::system::node_load(
                self.cache.used_bytes(),
                &self.cache.dir,
            )),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn urls_and_backoff() {
        assert_eq!(ws_url("https://hub:8443"), "wss://hub:8443/agent/v1/ws");
        assert_eq!(ws_url("https://hub:8443/"), "wss://hub:8443/agent/v1/ws");
        assert_eq!(
            ws_url("wss://hub:8443/agent/v1/ws"),
            "wss://hub:8443/agent/v1/ws"
        );
        for a in 0..20 {
            let d = backoff(a);
            assert!(d >= Duration::from_millis(100) && d <= Duration::from_secs(60));
        }
        assert!(backoff(0) <= Duration::from_secs(1));
    }
}
