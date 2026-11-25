//! Metrics loop (every `metrics_interval`, default 2 s): device samples + engine scrapes merged
//! with proxy-derived values → `metrics_batch` (split under 1 MiB), plus the agent's own
//! Prometheus text. Also the per-instance health loop and periodic housekeeping (stopped
//! instance pruning, cache limits / pressure, certificate expiry).

use std::fmt::Write as _;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

use gpudeck_protocol::{
    hub_feature, AgentMetrics, EngineSample, Event, EventKind, GpuSample, InstanceHealth,
    InstanceState, JsonObject, Message, MetricsBatch, SessionSample, SPLIT_FRAME_BYTES,
};
use uuid::Uuid;

use crate::agent::Agent;
use crate::backend::{Health, Scrape};

/// Bytes of KV cache per token from `model_versions.config`:
/// `2 × n_layers × n_kv_heads × head_dim × dtype_bytes` (fp16 default).
pub fn kv_bytes_per_token(cfg: &JsonObject) -> Option<u64> {
    let g = |keys: &[&str]| {
        keys.iter()
            .find_map(|k| cfg.get(*k).and_then(|v| v.as_u64()))
    };
    let layers = g(&["n_layers", "num_hidden_layers", "block_count"])?;
    let kv_heads = g(&["n_kv_heads", "num_key_value_heads"])
        .or_else(|| g(&["num_attention_heads", "n_heads"]))?;
    let head_dim = g(&["head_dim"]).or_else(|| {
        Some(g(&["hidden_size", "n_embd"])? / g(&["num_attention_heads", "n_heads"])?.max(1))
    })?;
    let dtype = g(&["kv_dtype_bytes"]).unwrap_or(2);
    Some(2 * layers * kv_heads * head_dim * dtype)
}

struct Target {
    id: Uuid,
    deployment_id: Uuid,
    port: u16,
    router_model: Option<String>,
    driver: Arc<dyn crate::backend::EngineDriver>,
    metrics: gpudeck_protocol::MetricsConfig,
    health: InstanceHealth,
    pid: Option<u32>,
    max_concurrent: Option<u64>,
    context_size: Option<u64>,
    kv_bpt: Option<u64>,
    engine: gpudeck_protocol::EngineName,
    served: String,
}

impl Agent {
    fn scrape_targets(&self) -> Vec<Target> {
        let routers: std::collections::HashMap<Uuid, Option<u32>> = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .map(|(k, p)| (*k, p.proc.as_ref().and_then(|x| x.pid)))
            .collect();
        let g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
        g.values()
            .filter(|i| {
                matches!(i.state, InstanceState::Ready | InstanceState::Draining)
                    && !crate::distributed::is_worker(&i.args.launch)
            })
            .map(|i| {
                let route_max = self
                    .proxy
                    .route(&i.id)
                    .and_then(|r| r.max_concurrent)
                    .map(u64::from);
                Target {
                    id: i.id,
                    deployment_id: i.args.deployment_id,
                    port: i.port,
                    router_model: i.engine_process_id.map(|_| i.served_model_name.clone()),
                    driver: self.engines.driver(i.args.engine.name),
                    metrics: i.args.engine.metrics.clone(),
                    health: i.health,
                    pid: i.proc.as_ref().and_then(|p| p.pid).or_else(|| {
                        i.engine_process_id
                            .and_then(|e| routers.get(&e).cloned().flatten())
                    }),
                    max_concurrent: route_max.or(i.args.launch.max_concurrency.map(u64::from)),
                    context_size: i.args.launch.context_size,
                    kv_bpt: kv_bytes_per_token(&i.args.model.config),
                    engine: i.args.engine.name,
                    served: i.served_model_name.clone(),
                }
            })
            .collect()
    }

    /// One scrape cycle → a `MetricsBatch` (unsplit).
    pub async fn collect_metrics(&self, interval: f64) -> MetricsBatch {
        let rt = self.rt();
        let readings = self.devices.sample().await;
        let mut gpu: Vec<GpuSample> = readings
            .into_iter()
            .map(|r| {
                let mut s = r.sample;
                s.device_id = self.device_id(r.vendor.as_str(), r.index);
                let extra = s.extra.get_or_insert_with(JsonObject::new);
                extra.insert("vendor".into(), r.vendor.as_str().into());
                extra.insert("index".into(), r.index.into());
                if let Some(u) = &r.uuid {
                    extra.insert("uuid".into(), u.clone().into());
                }
                s
            })
            .collect();
        self.tag_gpu_processes(&mut gpu);
        // Observed (external) engines run next to the managed scrapes (`observe.rs`).
        let observe = rt.hub_has(hub_feature::OBSERVED_ENGINES) && !self.observer.is_empty();
        let mut gpu_procs: crate::observe::GpuProcs = Default::default();
        for g in &gpu {
            for p in g.processes.iter().flatten() {
                gpu_procs
                    .entry(p.pid)
                    .or_default()
                    .push((g.device_id, p.used_memory_bytes));
            }
        }
        if !rt.hub_has(hub_feature::GPU_PROCESSES) {
            for g in gpu.iter_mut() {
                g.processes = None;
            }
        }
        let ship_logs = self.log_forward.load(Ordering::Relaxed);
        let observed = async {
            if observe {
                Some(
                    self.observer
                        .cycle(&*self.engines, &gpu_procs, ship_logs)
                        .await,
                )
            } else {
                None
            }
        };
        let managed = self.scrape_managed(interval);
        let (observed, (mut engine, sessions)) = tokio::join!(observed, managed);
        if let Some(c) = observed {
            engine.extend(c.samples);
            for ev in c.log_events {
                self.outbox.event(ev);
            }
            if c.changed {
                self.inventory_dirty.notify_one();
            }
        }
        let (cpu, rss) = crate::system::self_usage();
        MetricsBatch {
            interval_seconds: interval,
            sequence: None,
            gpu,
            engine,
            sessions,
            agent: Some(AgentMetrics {
                cpu_pct: cpu,
                rss_bytes: rss,
                proxy_active_connections: Some(
                    self.proxy.active_connections.load(Ordering::Relaxed),
                ),
                proxy_requests_total: Some(self.proxy.requests_total.load(Ordering::Relaxed)),
                proxy_errors_total: Some(self.proxy.errors_total.load(Ordering::Relaxed)),
                scrape_errors_total: Some(self.scrape_errors.load(Ordering::Relaxed)),
            }),
        }
    }

    /// Tag NVML GPU processes with the managed instance (process tree of its engine) or the
    /// observed engine that holds them, so the hub can tell GPU Deck's memory from memory used
    /// outside GPU Deck. Read-only `/proc` walk; untagged processes are someone else's.
    fn tag_gpu_processes(&self, gpu: &mut [GpuSample]) {
        if !gpu
            .iter()
            .any(|g| g.processes.as_ref().is_some_and(|p| !p.is_empty()))
        {
            return;
        }
        let mut roots: Vec<(u32, Uuid)> = vec![];
        {
            let g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
            for i in g.values().filter(|i| i.state != InstanceState::Stopped) {
                if let Some(pid) = i.proc.as_ref().and_then(|p| p.pid) {
                    roots.push((pid, i.id));
                }
            }
        }
        {
            let g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
            for p in g.values() {
                if let (Some(pid), Some(first)) = (
                    p.proc.as_ref().and_then(|x| x.pid),
                    p.instance_ids.iter().next(),
                ) {
                    roots.push((pid, *first));
                }
            }
        }
        let mut owners = crate::observe::procs::owner_map(std::path::Path::new("/proc"), &roots);
        for (id, tree) in self.observer.process_trees() {
            for pid in tree {
                owners.entry(pid).or_insert(id);
            }
        }
        for g in gpu.iter_mut() {
            for p in g.processes.iter_mut().flatten() {
                p.instance_id = owners.get(&p.pid).copied();
            }
        }
    }

    /// Scrape every managed instance (engine metrics merged with proxy-derived values).
    async fn scrape_managed(&self, interval: f64) -> (Vec<EngineSample>, Vec<SessionSample>) {
        let mut engine = vec![];
        let mut sessions = vec![];
        let scrape_timeout = Duration::from_secs_f64((interval * 0.75).clamp(0.5, 5.0));
        for t in self.scrape_targets() {
            let base = format!("http://127.0.0.1:{}", t.port);
            let mut sc = match tokio::time::timeout(
                scrape_timeout,
                t.driver
                    .scrape(&base, &t.metrics, t.router_model.as_deref()),
            )
            .await
            {
                Ok(Ok(s)) => s,
                Ok(Err(e)) => {
                    self.scrape_errors.fetch_add(1, Ordering::Relaxed);
                    tracing::debug!(instance = %t.id, "scrape failed: {e:#}");
                    Scrape::default()
                }
                Err(_) => {
                    self.scrape_errors.fetch_add(1, Ordering::Relaxed);
                    Scrape::default()
                }
            };
            let mut snap = self.proxy.snapshot(t.id, t.max_concurrent);
            snap.context_size = t.context_size;
            snap.kv_bytes_per_token = t.kv_bpt;
            t.driver
                .merge_proxy(&mut sc, &snap, t.metrics.derive_from_proxy);
            let mut e: EngineSample = sc.engine;
            e.ts = gpudeck_protocol::now();
            e.instance_id = t.id;
            e.deployment_id = t.deployment_id;
            if e.health.is_none() {
                e.health = Some(t.health);
            }
            if e.kv_cache_bytes.is_none() {
                if let (Some(used), Some(bpt)) = (e.kv_cache_used_tokens, t.kv_bpt) {
                    e.kv_cache_bytes = Some(used * bpt);
                }
            }
            if e.process_rss_bytes.is_none() {
                e.process_rss_bytes = t.pid.and_then(crate::system::process_rss);
            }
            let extra = e.extra.get_or_insert_with(JsonObject::new);
            extra.insert("engine".into(), t.engine.as_str().into());
            extra.insert("served_model_name".into(), t.served.clone().into());
            if let Some(m) = &t.router_model {
                extra.insert("router_model".into(), m.clone().into());
            }
            {
                let mut g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
                if let Some(i) = g.get_mut(&t.id) {
                    i.last_vram = e.process_vram_bytes.or(e.process_rss_bytes);
                }
            }
            for mut s in sc.sessions {
                s.ts = e.ts;
                s.instance_id = t.id;
                sessions.push(s);
            }
            engine.push(e);
        }
        (engine, sessions)
    }

    pub fn check_cache_pressure(&self) {
        let freed = self.cache.enforce(&self.files_in_use());
        let min_free = self.cache.min_free_bytes();
        let free = self.cache.free_bytes();
        if freed > 0 || free.is_some_and(|f| min_free > 0 && f < min_free) {
            let mut d = JsonObject::new();
            d.insert("freed_bytes".into(), freed.into());
            d.insert("free_bytes".into(), free.into());
            d.insert("min_free_bytes".into(), min_free.into());
            d.insert("used_bytes".into(), self.cache.used_bytes().into());
            d.insert("limit_bytes".into(), self.cache.limit_bytes().into());
            self.event(
                Event::warning(
                    EventKind::CachePressure,
                    format!("model cache pressure: freed {freed} bytes"),
                )
                .details(d),
            );
            self.inventory_dirty.notify_one();
        }
    }
}

/// Split a batch so each frame stays under `SPLIT_FRAME_BYTES`.
pub fn split_batch(b: MetricsBatch) -> Vec<MetricsBatch> {
    let size = serde_json::to_vec(&b).map(|v| v.len()).unwrap_or(0);
    if size <= SPLIT_FRAME_BYTES || (b.gpu.len() + b.engine.len() + b.sessions.len()) <= 1 {
        return vec![b];
    }
    let parts = size.div_ceil(SPLIT_FRAME_BYTES) + 1;
    let chunk = |n: usize| n.div_ceil(parts).max(1);
    let (gc, ec, sc) = (
        chunk(b.gpu.len()),
        chunk(b.engine.len()),
        chunk(b.sessions.len()),
    );
    let mut gi = b.gpu.chunks(gc).map(|c| c.to_vec());
    let mut ei = b.engine.chunks(ec).map(|c| c.to_vec());
    let mut si = b.sessions.chunks(sc).map(|c| c.to_vec());
    let mut out = vec![];
    for i in 0..parts {
        let g: Vec<GpuSample> = gi.next().unwrap_or_default();
        let e: Vec<EngineSample> = ei.next().unwrap_or_default();
        let s: Vec<SessionSample> = si.next().unwrap_or_default();
        if g.is_empty() && e.is_empty() && s.is_empty() && i > 0 {
            continue;
        }
        out.push(MetricsBatch {
            interval_seconds: b.interval_seconds,
            sequence: None,
            gpu: g,
            engine: e,
            sessions: s,
            agent: if i == 0 { b.agent.clone() } else { None },
        });
    }
    out
}

pub async fn run(agent: Arc<Agent>) {
    loop {
        let interval = agent.rt().metrics_interval();
        let started = Instant::now();
        let batch = agent.collect_metrics(interval.as_secs_f64()).await;
        *agent.prom_text.lock().unwrap_or_else(|e| e.into_inner()) =
            prometheus_text(&agent, &batch);
        for mut b in split_batch(batch) {
            b.sequence = Some(agent.metrics_seq.fetch_add(1, Ordering::Relaxed));
            agent.outbox.send(Message::MetricsBatch(b));
        }
        let sleep = interval
            .saturating_sub(started.elapsed())
            .max(Duration::from_millis(100));
        tokio::select! {
            _ = tokio::time::sleep(sleep) => {}
            _ = agent.shutdown.cancelled() => return,
        }
    }
}

/// Probe instance health at `health.interval_seconds`; `failure_threshold` consecutive failures
/// flip it to unhealthy.
pub async fn health_loop(agent: Arc<Agent>) {
    loop {
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_millis(1000)) => {}
            _ = agent.shutdown.cancelled() => return,
        }
        let due: Vec<(
            Uuid,
            u16,
            Option<String>,
            gpudeck_protocol::EngineName,
            gpudeck_protocol::HealthConfig,
        )> = {
            let now = Instant::now();
            let g = agent.instances.lock().unwrap_or_else(|e| e.into_inner());
            g.values()
                .filter(|i| {
                    matches!(i.state, InstanceState::Ready | InstanceState::Draining)
                        && i.next_health <= now
                        && !crate::distributed::is_worker(&i.args.launch)
                })
                .map(|i| {
                    (
                        i.id,
                        i.port,
                        i.engine_process_id.map(|_| i.served_model_name.clone()),
                        i.args.engine.name,
                        i.args.engine.health.clone(),
                    )
                })
                .collect()
        };
        for (id, port, model, engine, hc) in due {
            let interval = agent
                .rt()
                .settings
                .health_default_interval_seconds
                .unwrap_or(hc.interval_seconds)
                .max(1.0);
            let h = agent
                .engines
                .driver(engine)
                .health(&format!("http://127.0.0.1:{port}"), &hc, model.as_deref())
                .await;
            let change = {
                let mut g = agent.instances.lock().unwrap_or_else(|e| e.into_inner());
                let Some(i) = g.get_mut(&id) else { continue };
                i.next_health = Instant::now() + Duration::from_secs_f64(interval);
                match h {
                    Health::Healthy => {
                        i.failures = 0;
                        (i.health != InstanceHealth::Healthy).then(|| {
                            let from = i.health;
                            i.health = InstanceHealth::Healthy;
                            (i.args.deployment_id, from, InstanceHealth::Healthy, None)
                        })
                    }
                    Health::Loading => None,
                    Health::Unhealthy(reason) => {
                        i.failures += 1;
                        (i.failures >= hc.failure_threshold
                            && i.health != InstanceHealth::Unhealthy)
                            .then(|| {
                                let from = i.health;
                                i.health = InstanceHealth::Unhealthy;
                                i.message = Some(reason.clone());
                                (
                                    i.args.deployment_id,
                                    from,
                                    InstanceHealth::Unhealthy,
                                    Some(reason),
                                )
                            })
                    }
                }
            };
            if let Some((dep, from, to, reason)) = change {
                agent.health_event(id, dep, from, to, reason);
                agent.inventory_dirty.notify_one();
            }
        }
    }
}

pub async fn housekeeping(agent: Arc<Agent>) {
    let mut last_cert_check: Option<Instant> = None;
    loop {
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_secs(30)) => {}
            _ = agent.shutdown.cancelled() => return,
        }
        {
            let mut g = agent.instances.lock().unwrap_or_else(|e| e.into_inner());
            g.retain(|_, i| {
                !(i.state == InstanceState::Stopped
                    && i.stopped_at
                        .is_some_and(|t| t.elapsed() > Duration::from_secs(600)))
            });
        }
        agent.check_cache_pressure();
        if last_cert_check.is_none_or(|t| t.elapsed() > Duration::from_secs(6 * 3600)) {
            last_cert_check = Some(Instant::now());
            crate::certs::check_expiry(&agent);
        }
    }
}

fn esc(s: &str) -> String {
    s.replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
}

/// Agent self metrics + last scraped values in Prometheus text format (loopback endpoint and
/// the proxy's `/_metrics`).
pub fn prometheus_text(agent: &Agent, b: &MetricsBatch) -> String {
    let mut o = String::new();
    let node = agent.node_id;
    let mut gauge = |name: &str, help: &str, rows: Vec<(String, f64)>| {
        if rows.is_empty() {
            return;
        }
        let _ = writeln!(o, "# HELP {name} {help}\n# TYPE {name} gauge");
        for (labels, v) in rows {
            let _ = writeln!(o, "{name}{{{labels}}} {v}");
        }
    };
    let base = format!("node_id=\"{node}\"");
    if let Some(a) = &b.agent {
        gauge(
            "gpudeck_agent_cpu_percent",
            "Agent CPU %",
            a.cpu_pct.map(|v| (base.clone(), v)).into_iter().collect(),
        );
        gauge(
            "gpudeck_agent_rss_bytes",
            "Agent resident memory",
            a.rss_bytes
                .map(|v| (base.clone(), v as f64))
                .into_iter()
                .collect(),
        );
        gauge(
            "gpudeck_agent_proxy_active_connections",
            "Open proxy connections",
            a.proxy_active_connections
                .map(|v| (base.clone(), v as f64))
                .into_iter()
                .collect(),
        );
        gauge(
            "gpudeck_agent_proxy_requests_total",
            "Proxy requests since start",
            a.proxy_requests_total
                .map(|v| (base.clone(), v as f64))
                .into_iter()
                .collect(),
        );
        gauge(
            "gpudeck_agent_proxy_errors_total",
            "Proxy errors since start",
            a.proxy_errors_total
                .map(|v| (base.clone(), v as f64))
                .into_iter()
                .collect(),
        );
        gauge(
            "gpudeck_agent_scrape_errors_total",
            "Engine scrape errors since start",
            a.scrape_errors_total
                .map(|v| (base.clone(), v as f64))
                .into_iter()
                .collect(),
        );
    }
    gauge(
        "gpudeck_agent_commands_running",
        "Commands executing",
        vec![(base.clone(), agent.commands.running_ids().len() as f64)],
    );
    gauge(
        "gpudeck_agent_hub_connected",
        "1 when connected to the hub",
        vec![(
            base.clone(),
            if agent.outbox.is_connected() {
                1.0
            } else {
                0.0
            },
        )],
    );
    gauge(
        "gpudeck_agent_tunnel_connections",
        "Open outbound serving tunnels to the API layer",
        vec![(
            base.clone(),
            agent
                .tunnels
                .connected
                .load(std::sync::atomic::Ordering::Relaxed) as f64,
        )],
    );
    gauge(
        "gpudeck_agent_ingress_rejected_total",
        "Inbound connection attempts rejected (source, rate limit, TLS identity) since start",
        vec![(
            base.clone(),
            agent
                .ingress
                .total
                .load(std::sync::atomic::Ordering::Relaxed) as f64,
        )],
    );
    gauge(
        "gpudeck_node_cache_used_bytes",
        "Model cache bytes",
        vec![(base.clone(), agent.cache.used_bytes() as f64)],
    );
    let gl = |s: &GpuSample| format!("{base},device_id=\"{}\"", s.device_id);
    gauge(
        "gpudeck_gpu_utilization_ratio",
        "GPU utilization 0-1",
        b.gpu
            .iter()
            .filter_map(|s| s.utilization_pct.map(|v| (gl(s), v / 100.0)))
            .collect(),
    );
    gauge(
        "gpudeck_gpu_memory_used_bytes",
        "GPU memory used",
        b.gpu
            .iter()
            .filter_map(|s| s.memory_used_bytes.map(|v| (gl(s), v as f64)))
            .collect(),
    );
    gauge(
        "gpudeck_gpu_memory_total_bytes",
        "GPU memory total",
        b.gpu
            .iter()
            .filter_map(|s| s.memory_total_bytes.map(|v| (gl(s), v as f64)))
            .collect(),
    );
    gauge(
        "gpudeck_gpu_power_watts",
        "GPU power",
        b.gpu
            .iter()
            .filter_map(|s| s.power_watts.map(|v| (gl(s), v)))
            .collect(),
    );
    gauge(
        "gpudeck_gpu_temperature_celsius",
        "GPU temperature",
        b.gpu
            .iter()
            .filter_map(|s| s.temperature_c.map(|v| (gl(s), v)))
            .collect(),
    );
    let el = |s: &EngineSample| {
        let model = s
            .extra
            .as_ref()
            .and_then(|e| e.get("served_model_name"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let engine = s
            .extra
            .as_ref()
            .and_then(|e| e.get("engine"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        format!(
            "{base},instance_id=\"{}\",deployment_id=\"{}\",model=\"{}\",engine=\"{}\"",
            s.instance_id,
            s.deployment_id,
            esc(model),
            engine
        )
    };
    macro_rules! eng {
        ($name:expr, $help:expr, $f:ident, $scale:expr) => {
            gauge(
                $name,
                $help,
                b.engine
                    .iter()
                    .filter_map(|s| s.$f.map(|v| (el(s), v as f64 * $scale)))
                    .collect(),
            );
        };
    }
    eng!(
        "gpudeck_engine_active_sessions",
        "Active sessions",
        active_sessions,
        1.0
    );
    eng!(
        "gpudeck_engine_waiting_sessions",
        "Waiting sessions",
        waiting_sessions,
        1.0
    );
    eng!(
        "gpudeck_engine_requests_total",
        "Requests since start",
        requests_total,
        1.0
    );
    eng!(
        "gpudeck_engine_errors_total",
        "Errors since start",
        errors_total,
        1.0
    );
    eng!(
        "gpudeck_engine_prompt_tokens_total",
        "Prompt tokens since start",
        prompt_tokens_total,
        1.0
    );
    eng!(
        "gpudeck_engine_generated_tokens_total",
        "Generated tokens since start",
        generated_tokens_total,
        1.0
    );
    eng!(
        "gpudeck_engine_cached_tokens_total",
        "Cached prompt tokens since start",
        cached_tokens_total,
        1.0
    );
    eng!(
        "gpudeck_engine_generation_tokens_per_second",
        "Generation throughput",
        generation_tps,
        1.0
    );
    eng!(
        "gpudeck_engine_prompt_tokens_per_second",
        "Prompt throughput",
        prompt_tps,
        1.0
    );
    eng!(
        "gpudeck_engine_ttft_seconds_p95",
        "TTFT p95",
        ttft_ms_p95,
        0.001
    );
    eng!(
        "gpudeck_engine_kv_cache_usage_ratio",
        "KV cache usage 0-1",
        kv_cache_usage_pct,
        0.01
    );
    eng!(
        "gpudeck_engine_process_rss_bytes",
        "Engine RSS",
        process_rss_bytes,
        1.0
    );
    gauge(
        "gpudeck_engine_healthy",
        "1 when the instance is healthy",
        b.engine
            .iter()
            .map(|s| {
                (
                    el(s),
                    if s.health == Some(InstanceHealth::Healthy) {
                        1.0
                    } else {
                        0.0
                    },
                )
            })
            .collect(),
    );
    o
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kv_estimate() {
        let cfg: JsonObject = serde_json::from_value(
            serde_json::json!({"num_hidden_layers": 28, "num_key_value_heads": 8, "head_dim": 128}),
        )
        .unwrap();
        assert_eq!(kv_bytes_per_token(&cfg), Some(2 * 28 * 8 * 128 * 2));
        let cfg: JsonObject = serde_json::from_value(
            serde_json::json!({"n_layers": 2, "num_attention_heads": 4, "hidden_size": 256}),
        )
        .unwrap();
        assert_eq!(kv_bytes_per_token(&cfg), Some(2 * 2 * 4 * 64 * 2));
        assert_eq!(kv_bytes_per_token(&JsonObject::new()), None);
    }

    #[test]
    fn split_large_batches() {
        let s = SessionSample::new(
            Uuid::new_v4(),
            "x".repeat(100),
            gpudeck_protocol::SessionState::Generating,
        );
        let b = MetricsBatch {
            interval_seconds: 2.0,
            sequence: None,
            gpu: vec![],
            engine: vec![],
            sessions: vec![s; 8000],
            agent: None,
        };
        let parts = split_batch(b);
        assert!(parts.len() > 1);
        assert_eq!(parts.iter().map(|p| p.sessions.len()).sum::<usize>(), 8000);
        for p in parts {
            assert!(serde_json::to_vec(&p).unwrap().len() <= SPLIT_FRAME_BYTES);
        }
    }
}
