//! Instance and shared-engine lifecycle: `load_model`, `unload_model`, `drain_instance`,
//! `start_engine`, `stop_engine`, `restart_instance`, crash handling with a bounded restart
//! policy, llama.cpp router management (per-router preset INI, restart when a new model must be
//! registered — the router reads its model list only at start, see `metrics.md`), native engine
//! artifacts, and restoring instances after an agent restart.

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use gpudeck_protocol::{
    error_code, DrainInfo, DrainInstanceArgs, EngineRuntime, EngineSpec, ErrorInfo, Event,
    EventKind, HealthConfig, InstanceHealth, InstanceState, LoadModelArgs, RestartInstanceArgs,
    StartEngineArgs, StateChange, StopEngineArgs, Timestamp, UnloadModelArgs,
};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
use tracing::{info, warn};
use uuid::Uuid;

use crate::agent::Agent;
use crate::backend::{EngineDriver, EngineLaunch, Health, ProcSpec, RouterFiles};
use crate::cache::Resolved;
use crate::commands::Ctx;
use crate::proxy::LocalRoute;
use crate::supervisor::{self, LogTarget, ProcHandle};

/// Restarts allowed per instance / router within `RESTART_WINDOW` before giving up.
pub const MAX_RESTARTS: usize = 3;
pub const RESTART_WINDOW: Duration = Duration::from_secs(600);

pub struct Instance {
    pub id: Uuid,
    pub args: LoadModelArgs,
    pub state: InstanceState,
    pub health: InstanceHealth,
    pub port: u16,
    pub proc: Option<Arc<ProcHandle>>,
    pub engine_process_id: Option<Uuid>,
    pub served_model_name: String,
    pub message: Option<String>,
    pub ready_at: Option<Timestamp>,
    pub stopped_at: Option<Instant>,
    pub launch: Option<EngineLaunch>,
    pub failures: u32,
    pub restarts: Vec<Instant>,
    pub generation: u64,
    pub last_vram: Option<u64>,
    pub next_health: Instant,
    /// A llama.cpp router model running as its own `llama-server` (agent setting
    /// `router_overflow`): reported under its router, but stopped / restarted on its own.
    pub overflow: bool,
}

/// Specs left by the previous agent process whose engines could not be re-adopted (gone,
/// another process now, unhealthy): relaunched in the background by [`Agent::relaunch`].
#[derive(Debug, Default)]
pub struct Restore {
    pub procs: Vec<crate::state::PersistedEngineProcess>,
    pub insts: Vec<crate::state::PersistedInstance>,
}

pub struct EngineProc {
    pub id: Uuid,
    pub args: StartEngineArgs,
    pub port: u16,
    pub proc: Option<Arc<ProcHandle>>,
    pub launch: EngineLaunch,
    /// served model name → preset INI keys.
    pub models: BTreeMap<String, BTreeMap<String, String>>,
    pub instance_ids: BTreeSet<Uuid>,
    pub generation: u64,
    pub restarts: Vec<Instant>,
    pub healthy: bool,
    /// Since when no instance uses it (idle stop, `router_idle_stop_seconds`).
    pub empty_since: Option<Instant>,
}

fn ierr(code: &str, msg: impl Into<String>) -> ErrorInfo {
    ErrorInfo::new(code, msg)
}

fn base_url(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

/// Run `engine` from its official image instead of a native build: container runtime, the
/// image, and the program inside it in place of the template's first word.
fn use_container(engine: &mut EngineSpec, c: &gpudeck_protocol::ArtifactContainer) {
    engine.runtime = EngineRuntime::Container;
    engine.image = Some(c.image.clone());
    engine.artifact = None;
    if let (Some(program), Some(first)) =
        (&c.entrypoint, engine.launch_template.command.first_mut())
    {
        *first = program.clone();
    }
}

impl Agent {
    // ------------------------------------------------------------------ state helpers

    pub fn set_state(
        &self,
        id: Uuid,
        to: InstanceState,
        health: Option<InstanceHealth>,
        reason: Option<String>,
    ) {
        let (from, dep, hfrom) = {
            let mut g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
            let Some(i) = g.get_mut(&id) else { return };
            let from = i.state;
            let hfrom = i.health;
            i.state = to;
            if let Some(h) = health {
                i.health = h;
            }
            if to == InstanceState::Stopped {
                i.stopped_at = Some(Instant::now());
            }
            if reason.is_some() {
                i.message = reason.clone();
            }
            (from, i.args.deployment_id, hfrom)
        };
        if from != to {
            let sev = if to == InstanceState::Error {
                gpudeck_protocol::Severity::Warning
            } else {
                gpudeck_protocol::Severity::Info
            };
            let mut ev = Event::new(
                EventKind::InstanceStateChanged,
                sev,
                format!("instance {id}: {from} -> {to}"),
            )
            .instance(id)
            .deployment(dep);
            ev.state = Some(StateChange {
                from: Some(from.to_string()),
                to: to.to_string(),
                reason: reason.clone(),
            });
            self.event(ev);
            self.inventory_dirty.notify_one();
        }
        if let Some(h) = health {
            if h != hfrom {
                self.health_event(id, dep, hfrom, h, reason);
            }
        }
    }

    pub fn health_event(
        &self,
        id: Uuid,
        dep: Uuid,
        from: InstanceHealth,
        to: InstanceHealth,
        reason: Option<String>,
    ) {
        let sev = if to == InstanceHealth::Unhealthy {
            gpudeck_protocol::Severity::Warning
        } else {
            gpudeck_protocol::Severity::Info
        };
        let mut ev = Event::new(
            EventKind::InstanceHealthChanged,
            sev,
            format!("instance {id}: health {from} -> {to}"),
        )
        .instance(id)
        .deployment(dep);
        ev.state = Some(StateChange {
            from: Some(from.to_string()),
            to: to.to_string(),
            reason,
        });
        self.event(ev);
    }

    fn ports_taken(&self) -> Vec<u16> {
        let mut v: Vec<u16> = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .values()
            .filter(|i| {
                i.state != InstanceState::Stopped && (i.engine_process_id.is_none() || i.overflow)
            })
            .map(|i| i.port)
            .collect();
        v.extend(
            self.engine_procs
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .values()
                .map(|p| p.port),
        );
        // Ports of observed (external) servers are never bound, not even to test them.
        v.extend(self.observer.local_ports());
        v
    }

    fn alloc_port(&self, requested: u16, range: [u16; 2]) -> Result<u16, ErrorInfo> {
        let taken = self.ports_taken();
        if requested != 0 {
            if taken.contains(&requested) || !supervisor::port_free(requested) {
                return Err(ierr(
                    error_code::PORT_IN_USE,
                    format!("port {requested} is in use"),
                )
                .retryable());
            }
            return Ok(requested);
        }
        let range = self.rt().port_range(range);
        supervisor::free_port(range, &taken).ok_or_else(|| {
            ierr(
                error_code::PORT_IN_USE,
                format!("no free port in {}-{}", range[0], range[1]),
            )
            .retryable()
        })
    }

    fn log_target(
        &self,
        instance_id: Option<Uuid>,
        engine_process_id: Option<Uuid>,
        deployment_id: Option<Uuid>,
    ) -> LogTarget {
        let s = self.rt().settings;
        let file_id = instance_id.or(engine_process_id).unwrap_or_default();
        LogTarget {
            outbox: self.outbox.clone(),
            instance_id,
            engine_process_id,
            deployment_id,
            forward: self.log_forward.clone(),
            max_lines: s.log_chunk_max_lines.unwrap_or(100) as usize,
            flush: Duration::from_millis(s.log_flush_interval_ms.unwrap_or(1000).max(100)),
            file: Some(self.paths.logs_dir().join(format!("{file_id}.log"))),
        }
    }

    // ------------------------------------------------------------------ artifacts

    /// Native engine artifact → `<state_dir>/engines/<name>/<version>/` (download, sha256
    /// check, unpack / venv / source build: `crate::artifact`). Returns the entrypoint, or
    /// `None` when the engine is preinstalled (engine_paths) / containerised / has no artifact.
    ///
    /// The build is chosen for this host (`crate::engine_compat`): the pinned one, an older
    /// fallback the host's glibc runs, or, when no native build fits and a container runtime
    /// is available, the engine's official image: `engine` is then switched to the container
    /// runtime in place. Otherwise the error says what the host lacks and the ways out.
    pub async fn ensure_artifact(
        &self,
        engine: &mut EngineSpec,
        cancel: &CancellationToken,
    ) -> Result<Option<PathBuf>, ErrorInfo> {
        use crate::engine_compat::{self as compat, Choice};
        if engine.runtime == EngineRuntime::Container {
            return Ok(None);
        }
        let Some(full) = engine.artifact.clone() else {
            return Ok(None);
        };
        if let Some(c) = self.cfg.engine_path(engine.name.as_str()) {
            if crate::template::expand_home(c).is_file() {
                return Ok(None);
            }
        }
        let linux = cfg!(target_os = "linux");
        let has_runtime = self.container_runtime.is_some();
        let name = engine.name.as_str().to_string();
        let art = match compat::choose(
            &name,
            &engine.version,
            &full,
            compat::host_glibc(),
            linux,
            has_runtime,
        ) {
            Choice::Native(a) => *a,
            Choice::Container(c) => {
                warn!(engine = %name, image = %c.image, "no native build runs on this host (glibc); using the official image");
                use_container(engine, &c);
                return Ok(None);
            }
            Choice::Incompatible(msg) => {
                return Err(ierr(error_code::UNSUPPORTED_ENGINE, msg));
            }
        };
        let mut dir = self
            .paths
            .engines_dir()
            .join(engine.name.as_str())
            .join(&engine.version);
        if art.sha256 != full.sha256 {
            // An older-release fallback never shares the folder of the pinned build.
            dir = dir.join(format!("compat-{}", &art.sha256[..12]));
        }
        let marker = dir.join(".gpudeck-ready");
        let file_name = crate::artifact::url_file_name(&art.url);
        let entry = dir.join(art.entrypoint.clone().unwrap_or_else(|| file_name.clone()));
        let ready = marker.exists()
            && std::fs::read_to_string(&marker)
                .map(|m| m.trim() == art.sha256)
                .unwrap_or(false)
            && entry.exists();
        if !ready {
            if !crate::artifact::is_pinned(&art.sha256) {
                warn!(url = %art.url, "engine artifact has no pinned sha256; refusing it");
            }
            if art.url != full.url {
                info!(engine = %name, url = %art.url, "host glibc too old for the pinned build; installing the older compatible build");
            }
            crate::artifact::install(&art, &dir, &entry, cancel).await?;
            let _ = std::fs::write(&marker, &art.sha256);
        }
        let missing = compat::missing_libraries(&entry);
        if !missing.is_empty() {
            if let (Some(c), true) = (&full.container, has_runtime) {
                warn!(engine = %name, ?missing, image = %c.image, "native build cannot start here; using the official image");
                use_container(engine, c);
                return Ok(None);
            }
            return Err(ierr(
                error_code::UNSUPPORTED_ENGINE,
                compat::missing_libraries_message(
                    &name,
                    &engine.version,
                    &entry,
                    &missing,
                    &full,
                    has_runtime,
                ),
            ));
        }
        Ok(Some(entry))
    }

    fn make_proc(
        &self,
        l: &EngineLaunch,
        driver: &Arc<dyn EngineDriver>,
    ) -> Result<ProcSpec, ErrorInfo> {
        let tpl = &l.engine.launch_template.command;
        let r = if tpl.len() == 1 && tpl[0] == crate::template::ADAPTER_PLACEHOLDER {
            driver.adapter_command(l)
        } else {
            crate::template::build_proc(
                l,
                self.cfg.engine_path(l.engine.name.as_str()),
                self.container_runtime.as_deref(),
            )
        };
        let mut spec = r.map_err(|e| ierr(error_code::ENGINE_START_FAILED, format!("{e:#}")))?;
        crate::engine_env::prepare_native(
            &mut spec,
            l.engine.name,
            &l.devices,
            &self.paths.tmp_dir(),
        )
        .map_err(|m| ierr(error_code::UNSUPPORTED_ENGINE, m))?;
        Ok(spec)
    }

    /// Spawn an engine process detached from the agent (own session, output files, optional
    /// systemd scope) so it survives agent restarts and upgrades.
    fn spawn_engine(&self, spec: &ProcSpec, log: LogTarget) -> Result<Arc<ProcHandle>, ErrorInfo> {
        supervisor::spawn_with(
            spec,
            Some(log),
            supervisor::scope_wrapper(&self.cfg.engine_scope),
        )
        .map_err(|e| ierr(error_code::ENGINE_START_FAILED, format!("{e:#}")))
    }

    /// `prev` when it is still free (stable engine ports across agent restarts), else 0 (any).
    fn stable_port(&self, prev: u16) -> u16 {
        if prev != 0 && !self.ports_taken().contains(&prev) && supervisor::port_free(prev) {
            prev
        } else {
            0
        }
    }

    /// Persist an instance's spec + process identity (`instances/<id>.json`).
    pub fn persist_instance(&self, id: Uuid) {
        let Some((args, port, proc, ep, overflow)) = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&id)
            .map(|i| {
                (
                    i.args.clone(),
                    i.port,
                    i.proc.clone(),
                    i.engine_process_id,
                    i.overflow,
                )
            })
        else {
            return;
        };
        let proc = proc.or_else(|| {
            ep.and_then(|ep| {
                self.engine_procs
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .get(&ep)
                    .and_then(|p| p.proc.clone())
            })
        });
        let rec = crate::state::ProcRecord {
            port,
            pid: proc.as_ref().and_then(|p| p.pid),
            start_token: proc.as_ref().and_then(|p| p.start_token),
            overflow,
        };
        if let Err(e) = crate::state::save_instance(&self.paths, &args, rec) {
            warn!(instance = %id, "persist instance: {e:#}");
        }
    }

    /// Warm-up requests before the instance is reported ready (`crate::warmup`).
    async fn warm_up(&self, ctx: &Ctx, args: &LoadModelArgs, port: u16, model: &str) {
        if !crate::warmup::enabled(&self.cfg.engine_warmup, args.engine.name, &args.devices) {
            return;
        }
        let reqs = crate::warmup::plan(model, args.model.model_type, args.launch.context_size);
        if reqs.is_empty() {
            return;
        }
        ctx.progress(Some(92.0), "warming up the engine (first-request kernels)");
        let (ok, took) = crate::warmup::run(&base_url(port), &reqs, Duration::from_secs(300)).await;
        info!(instance = %args.instance_id, "warm-up: {ok}/{} requests in {:.1}s", reqs.len(), took.as_secs_f64());
    }

    /// Poll health until healthy, process exit, timeout or cancel.
    #[allow(clippy::too_many_arguments)]
    async fn wait_healthy(
        &self,
        driver: &Arc<dyn EngineDriver>,
        port: u16,
        health: &HealthConfig,
        model: Option<&str>,
        proc: Option<&Arc<ProcHandle>>,
        timeout: Duration,
        cancel: &CancellationToken,
    ) -> Result<(), ErrorInfo> {
        let started = Instant::now();
        let base = base_url(port);
        let mut last;
        loop {
            if cancel.is_cancelled() {
                return Err(ierr(error_code::CANCELLED, "cancelled"));
            }
            if let Some(p) = proc {
                if let Some(exit) = p.exited() {
                    let lines = p.last_lines();
                    return Err(ierr(
                        error_code::ENGINE_START_FAILED,
                        format!(
                            "engine exited during startup ({}): {}",
                            exit.describe(),
                            lines
                                .iter()
                                .rev()
                                .take(5)
                                .rev()
                                .cloned()
                                .collect::<Vec<_>>()
                                .join(" | ")
                        ),
                    ));
                }
            }
            match driver.health(&base, health, model).await {
                Health::Healthy => return Ok(()),
                Health::Loading => last = "loading".into(),
                Health::Unhealthy(r) => last = r,
            }
            if started.elapsed() > timeout {
                return Err(ierr(
                    error_code::HEALTH_TIMEOUT,
                    format!("not healthy after {}s ({last})", timeout.as_secs()),
                ));
            }
            tokio::select! {
                _ = tokio::time::sleep(Duration::from_millis(500)) => {}
                _ = cancel.cancelled() => {}
            }
        }
    }

    // ------------------------------------------------------------------ load_model

    pub async fn load_model(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: LoadModelArgs,
    ) -> Result<Value, ErrorInfo> {
        if self.rt().drain {
            return Err(ierr(
                error_code::INVALID_ARGS,
                "node is draining: load_model refused",
            ));
        }
        let started = Instant::now();
        let id = args.instance_id;
        self.learn_device_ids(&args.devices);
        // Idempotent: the same spec already ready → report it.
        let existing = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&id)
            .map(|i| (i.state, i.args.redacted() == args.redacted()));
        if let Some((state, same)) = existing {
            if state == InstanceState::Ready && same {
                return Ok(self.load_result(id, started));
            }
            if state != InstanceState::Stopped {
                let _ = self
                    .unload_model(
                        &Ctx::internal(),
                        UnloadModelArgs {
                            instance_id: id,
                            force: true,
                            drain_timeout_seconds: 0,
                            kill_grace_seconds: 15,
                            stop_engine_if_empty: false,
                        },
                    )
                    .await;
            }
        }
        let served = args.model.served_model_name.clone();
        self.instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(
                id,
                Instance {
                    id,
                    args: args.clone(),
                    state: InstanceState::Pending,
                    health: InstanceHealth::Unknown,
                    port: 0,
                    proc: None,
                    engine_process_id: args.engine_process_id,
                    served_model_name: served.clone(),
                    message: None,
                    ready_at: None,
                    stopped_at: None,
                    launch: None,
                    failures: 0,
                    restarts: vec![],
                    generation: 0,
                    last_vram: None,
                    next_health: Instant::now(),
                    overflow: false,
                },
            );
        self.set_state(
            id,
            InstanceState::Loading,
            Some(InstanceHealth::Unknown),
            Some("load_model".into()),
        );
        let res = self.load_inner(ctx, &args).await;
        match res {
            Ok((port, _pid)) => {
                let files: Vec<Uuid> = args.model.files.iter().map(|f| f.model_file_id).collect();
                self.cache.touch(&files);
                if !crate::distributed::is_worker(&args.launch) {
                    // The engine may answer to another name than the served one (MLX: `default_model`).
                    let launch = self
                        .instances
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .get(&id)
                        .and_then(|i| i.launch.clone());
                    let upstream = launch
                        .and_then(|l| {
                            self.engines
                                .driver(args.engine.name)
                                .upstream_model_name(&l)
                        })
                        .unwrap_or(served);
                    self.warm_up(ctx, &args, port, &upstream).await;
                    self.proxy.set_local(
                        id,
                        LocalRoute {
                            port,
                            engine: args.engine.name,
                            served_model_name: upstream,
                            max_concurrency: args.launch.max_concurrency,
                        },
                    );
                    self.proxy.set_draining(id, false);
                }
                if args.launch.distributed.is_some() {
                    self.apply_egress();
                }
                {
                    let mut g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
                    if let Some(i) = g.get_mut(&id) {
                        i.port = port;
                        i.ready_at = Some(gpudeck_protocol::now());
                        i.failures = 0;
                        i.next_health = Instant::now()
                            + Duration::from_secs_f64(args.engine.health.interval_seconds.max(1.0));
                    }
                }
                self.persist_instance(id);
                info!(instance = %id, port, "instance ready in {:.1}s", started.elapsed().as_secs_f64());
                self.set_state(
                    id,
                    InstanceState::Ready,
                    Some(InstanceHealth::Healthy),
                    Some("healthy".into()),
                );
                Ok(self.load_result(id, started))
            }
            Err(e) => {
                let proc = self
                    .instances
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .get(&id)
                    .and_then(|i| i.proc.clone());
                if let Some(p) = proc {
                    p.stop(Duration::from_secs(5)).await;
                }
                self.set_state(
                    id,
                    InstanceState::Error,
                    Some(InstanceHealth::Unhealthy),
                    Some(e.message.clone()),
                );
                Err(e)
            }
        }
    }

    fn load_result(&self, id: Uuid, started: Instant) -> Value {
        let g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
        let Some(i) = g.get(&id) else {
            return json!({"instance_id": id});
        };
        let pid = match (&i.proc, i.engine_process_id) {
            (Some(p), _) => p.pid,
            (None, Some(ep)) => self
                .engine_procs
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get(&ep)
                .and_then(|p| p.proc.as_ref().and_then(|p| p.pid)),
            _ => None,
        };
        let weights: u64 = i.args.model.files.iter().map(|f| f.size_bytes).sum();
        json!({
            "instance_id": id,
            "port": i.port,
            "pid": pid,
            "engine_process_id": i.engine_process_id,
            "served_model_name": i.served_model_name,
            "ready_at": i.ready_at,
            "load_seconds": started.elapsed().as_secs_f64(),
            "vram_bytes_used": i.last_vram.unwrap_or(weights),
            "distributed_rank": i.args.launch.distributed.as_ref().map(|d| d.rank),
        })
    }

    async fn load_inner(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: &LoadModelArgs,
    ) -> Result<(u16, Option<u32>), ErrorInfo> {
        ctx.progress(Some(1.0), "ensuring model files in cache");
        let t_files = Instant::now();
        let p = ctx.progress_fn();
        let resolved = self
            .cache
            .ensure_model(&args.model, false, &ctx.cancel, &|pct, m| {
                p(Some(pct * 0.6), m)
            })
            .await?;
        let draft = match &args.draft_model {
            Some(d) => Some(
                self.cache
                    .ensure_model(d, false, &ctx.cancel, &|_, _| {})
                    .await?,
            ),
            None => None,
        };
        info!(instance = %args.instance_id, files = resolved.files.len(), "model files ready in {:.2}s", t_files.elapsed().as_secs_f64());
        ctx.progress(Some(60.0), "starting engine");
        if args.launch.distributed.is_some() && args.engine_process_id.is_some() {
            return Err(ierr(
                error_code::DISTRIBUTED_UNSUPPORTED,
                "distributed launches cannot load into a shared engine process",
            ));
        }
        match args.engine_process_id {
            Some(ep) => {
                self.load_into_router(ctx, args, &resolved, draft.as_ref(), ep)
                    .await
            }
            None => {
                self.load_dedicated(ctx, args, &resolved, draft.as_ref())
                    .await
            }
        }
    }

    fn instance_launch(
        &self,
        args: &LoadModelArgs,
        resolved: &Resolved,
        draft: Option<&Resolved>,
        port: u16,
        exe: Option<PathBuf>,
    ) -> EngineLaunch {
        EngineLaunch {
            engine: args.engine.clone(),
            launch: args.launch.clone(),
            model: Some(args.model.redacted()),
            served_model_name: Some(args.model.served_model_name.clone()),
            model_path: Some(resolved.primary.clone()),
            model_paths: resolved.files.iter().map(|(_, p)| p.clone()).collect(),
            model_dir: Some(resolved.dir.clone()),
            draft_model_path: draft.map(|d| d.primary.clone()),
            port,
            devices: args.devices.clone(),
            executable: exe,
            library_dir: None,
            cache_dir: self.cache.dir.clone(),
            router: None,
            instance_id: Some(args.instance_id),
            engine_process_id: None,
        }
    }

    async fn load_dedicated(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: &LoadModelArgs,
        resolved: &Resolved,
        draft: Option<&Resolved>,
    ) -> Result<(u16, Option<u32>), ErrorInfo> {
        let port = self.alloc_port(args.port, args.engine.port_range)?;
        let mut engine = args.engine.clone();
        let exe = self.ensure_artifact(&mut engine, &ctx.cancel).await?;
        let mut launch = self.instance_launch(args, resolved, draft, port, exe);
        launch.engine = engine;
        crate::distributed::prepare(&mut launch)
            .map_err(|e| ierr(error_code::DISTRIBUTED_UNSUPPORTED, format!("{e:#}")))?;
        let timeout = Duration::from_secs(args.health_timeout_seconds.max(5));
        let proc = if crate::distributed::is_worker(&args.launch) {
            self.spawn_worker(
                args.instance_id,
                launch,
                timeout,
                &ctx.cancel,
                ctx.command_id,
            )
            .await?
        } else {
            self.spawn_instance(
                args.instance_id,
                launch,
                timeout,
                &ctx.cancel,
                ctx.command_id,
            )
            .await?
        };
        Ok((port, proc.pid))
    }

    /// Spawn a headless worker rank of a cross-node group (`protocol.md` §9) and wait until it is
    /// up: llama.cpp `rpc-server` accepts TCP on its RPC port, other engines stay alive 5 s.
    async fn spawn_worker(
        self: &Arc<Self>,
        id: Uuid,
        launch: EngineLaunch,
        timeout: Duration,
        cancel: &CancellationToken,
        command: Option<Uuid>,
    ) -> Result<Arc<ProcHandle>, ErrorInfo> {
        use crate::distributed::{llama_rpc_worker, WorkerReady};
        let d = launch
            .launch
            .distributed
            .clone()
            .ok_or_else(|| ierr(error_code::INVALID_ARGS, "not a distributed launch"))?;
        let bind = crate::distributed::bind_address(
            &d,
            self.cfg.proxy_address.as_deref(),
            &crate::system::ip_addresses(),
        );
        let (spec, ready) = if launch.engine.name == gpudeck_protocol::EngineName::LlamaCpp {
            llama_rpc_worker(&launch, &d, &bind, self.cfg.engine_path("llama_cpp"))
                .map_err(|e| ierr(error_code::DISTRIBUTED_UNSUPPORTED, format!("{e:#}")))?
        } else {
            let driver = self.engines.driver(launch.engine.name);
            (
                self.make_proc(&launch, &driver)?,
                WorkerReady::AliveFor(Duration::from_secs(5)),
            )
        };
        let dep = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&id)
            .map(|i| i.args.deployment_id);
        info!(instance = %id, rank = d.rank, cmd = %spec.display(), "starting distributed worker rank");
        let proc = self.spawn_engine(&spec, self.log_target(Some(id), None, dep))?;
        let generation = {
            let mut g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
            let i = g
                .get_mut(&id)
                .ok_or_else(|| ierr(error_code::INSTANCE_NOT_FOUND, "instance vanished"))?;
            i.proc = Some(proc.clone());
            i.port = launch.port;
            i.launch = Some(launch.clone());
            i.generation += 1;
            i.generation
        };
        let mut ev = Event::info(
            EventKind::EngineStarted,
            format!(
                "{} worker rank {} started for instance {id} (pid {:?})",
                launch.engine.name, d.rank, proc.pid
            ),
        )
        .instance(id);
        ev.deployment_id = dep;
        ev.command_id = command;
        ev.details = json!({"pid": proc.pid, "rank": d.rank, "world_size": d.world_size, "command": spec.display()}).as_object().cloned();
        self.event(ev);
        let started = Instant::now();
        let ok = loop {
            if cancel.is_cancelled() {
                break Err(ierr(error_code::CANCELLED, "cancelled"));
            }
            if let Some(exit) = proc.exited() {
                break Err(ierr(
                    error_code::ENGINE_START_FAILED,
                    format!(
                        "worker exited during startup ({}): {}",
                        exit.describe(),
                        proc.last_lines()
                            .iter()
                            .rev()
                            .take(5)
                            .rev()
                            .cloned()
                            .collect::<Vec<_>>()
                            .join(" | ")
                    ),
                ));
            }
            let up = match &ready {
                WorkerReady::Tcp(addr) => matches!(
                    tokio::time::timeout(
                        Duration::from_millis(500),
                        tokio::net::TcpStream::connect(*addr)
                    )
                    .await,
                    Ok(Ok(_))
                ),
                WorkerReady::AliveFor(d) => started.elapsed() >= *d,
            };
            if up {
                break Ok(());
            }
            if started.elapsed() > timeout {
                break Err(ierr(
                    error_code::HEALTH_TIMEOUT,
                    format!(
                        "worker rank {} not ready after {}s",
                        d.rank,
                        timeout.as_secs()
                    ),
                ));
            }
            tokio::time::sleep(Duration::from_millis(300)).await;
        };
        if let Err(e) = ok {
            proc.stop(Duration::from_secs(5)).await;
            return Err(e);
        }
        let me = self.clone();
        let p2 = proc.clone();
        tokio::spawn(async move { me.watch_instance(id, generation, p2).await });
        Ok(proc)
    }

    /// Spawn (or respawn) a dedicated instance process and wait until it is healthy.
    async fn spawn_instance(
        self: &Arc<Self>,
        id: Uuid,
        launch: EngineLaunch,
        timeout: Duration,
        cancel: &CancellationToken,
        command: Option<Uuid>,
    ) -> Result<Arc<ProcHandle>, ErrorInfo> {
        let driver = self.engines.driver(launch.engine.name);
        let spec = self.make_proc(&launch, &driver)?;
        let dep = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&id)
            .map(|i| i.args.deployment_id);
        info!(instance = %id, cmd = %spec.display(), "starting engine");
        let proc = self.spawn_engine(&spec, self.log_target(Some(id), None, dep))?;
        let port = launch.port;
        let health = launch.engine.health.clone();
        let generation = {
            let mut g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
            let i = g
                .get_mut(&id)
                .ok_or_else(|| ierr(error_code::INSTANCE_NOT_FOUND, "instance vanished"))?;
            i.proc = Some(proc.clone());
            i.port = port;
            i.launch = Some(launch.clone());
            i.generation += 1;
            i.generation
        };
        let mut ev = Event::info(
            EventKind::EngineStarted,
            format!(
                "{} started for instance {id} on 127.0.0.1:{port} (pid {:?})",
                launch.engine.name, proc.pid
            ),
        )
        .instance(id);
        ev.deployment_id = dep;
        ev.command_id = command;
        ev.details = json!({"pid": proc.pid, "port": port, "engine": launch.engine.name, "command": spec.display()}).as_object().cloned();
        self.event(ev);
        if let Err(e) = self
            .wait_healthy(&driver, port, &health, None, Some(&proc), timeout, cancel)
            .await
        {
            proc.stop(Duration::from_secs(5)).await;
            return Err(e);
        }
        let addrs = crate::system::ip_addresses();
        if let Some(addr) = supervisor::exposed_on_network(port, &addrs).await {
            proc.stop(Duration::from_secs(5)).await;
            return Err(ierr(error_code::ENGINE_START_FAILED, format!("engine is reachable on a network interface ({addr}); engines must listen on 127.0.0.1 only")));
        }
        driver.register(&base_url(port), &launch);
        let me = self.clone();
        let p2 = proc.clone();
        tokio::spawn(async move { me.watch_instance(id, generation, p2).await });
        Ok(proc)
    }

    fn watch_instance(
        self: Arc<Self>,
        id: Uuid,
        generation: u64,
        proc: Arc<ProcHandle>,
    ) -> futures::future::BoxFuture<'static, ()> {
        Box::pin(async move {
            let exit = proc.wait().await;
            if proc.stop_requested() || self.shutdown.is_cancelled() {
                return;
            }
            let (dep, launch, restarts) = {
                let mut g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
                let Some(i) = g.get_mut(&id) else { return };
                if i.generation != generation {
                    return;
                }
                i.restarts.retain(|t| t.elapsed() < RESTART_WINDOW);
                (i.args.deployment_id, i.launch.clone(), i.restarts.len())
            };
            let info = exit.to_info(proc.last_lines());
            let mut ev = Event::critical(
                EventKind::EngineCrashed,
                format!("engine for instance {id} crashed: {}", exit.describe()),
            )
            .instance(id)
            .deployment(dep);
            ev.exit = Some(info);
            self.event(ev);
            self.set_state(
                id,
                InstanceState::Error,
                Some(InstanceHealth::Unhealthy),
                Some(format!("engine crashed: {}", exit.describe())),
            );
            let Some(launch) = launch else { return };
            if restarts >= MAX_RESTARTS {
                warn!(instance = %id, "restart limit reached ({MAX_RESTARTS} in {:?}); leaving instance in error", RESTART_WINDOW);
                return;
            }
            let backoff = Duration::from_secs(5 * (1 << restarts));
            tokio::select! {
                _ = tokio::time::sleep(backoff) => {}
                _ = self.shutdown.cancelled() => return,
            }
            {
                let mut g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
                match g.get_mut(&id) {
                    Some(i) if i.generation == generation && i.state == InstanceState::Error => {
                        i.restarts.push(Instant::now())
                    }
                    _ => return,
                }
            }
            info!(instance = %id, "restarting crashed engine (attempt {})", restarts + 1);
            self.set_state(
                id,
                InstanceState::Loading,
                Some(InstanceHealth::Unknown),
                Some(format!("auto-restart {}", restarts + 1)),
            );
            let timeout =
                Duration::from_secs_f64(launch.engine.health.startup_timeout_seconds.max(5.0));
            let respawn = if crate::distributed::is_worker(&launch.launch) {
                self.spawn_worker(id, launch, timeout, &self.shutdown.child_token(), None)
                    .await
            } else {
                self.spawn_instance(id, launch, timeout, &self.shutdown.child_token(), None)
                    .await
            };
            match respawn {
                Ok(_) => {
                    self.persist_instance(id);
                    self.set_state(
                        id,
                        InstanceState::Ready,
                        Some(InstanceHealth::Healthy),
                        Some("restarted after crash".into()),
                    );
                }
                Err(e) => self.set_state(
                    id,
                    InstanceState::Error,
                    Some(InstanceHealth::Unhealthy),
                    Some(format!("auto-restart failed: {}", e.message)),
                ),
            }
        })
    }

    // ------------------------------------------------------------------ router mode

    async fn load_into_router(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: &LoadModelArgs,
        resolved: &Resolved,
        draft: Option<&Resolved>,
        ep: Uuid,
    ) -> Result<(u16, Option<u32>), ErrorInfo> {
        self.wait_engine_process(ep, &ctx.cancel).await?;
        let (port, engine) = {
            let g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
            let p = g.get(&ep).ok_or_else(|| {
                ierr(
                    error_code::ENGINE_PROCESS_NOT_FOUND,
                    format!("engine process {ep} is not running on this node"),
                )
            })?;
            (p.port, p.launch.engine.clone())
        };
        let name = args.model.served_model_name.clone();
        let launch = self.instance_launch(args, resolved, draft, port, None);
        let entry = crate::template::router_entry(&launch);
        let driver = self.engines.driver(engine.name);
        if !driver.supports_hot_swap() {
            return Err(ierr(
                error_code::UNSUPPORTED_ENGINE,
                format!(
                    "{} does not support loading models into a running process",
                    engine.name
                ),
            ));
        }
        let base = base_url(port);
        let known = driver
            .router_models(&base)
            .await
            .map(|ms| ms.iter().any(|m| m.id == name))
            .unwrap_or(false);
        let unchanged = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .is_some_and(|p| p.models.get(&name) == Some(&entry));
        if (!known || !unchanged)
            && self.cfg.router_overflow
            && engine.name == gpudeck_protocol::EngineName::LlamaCpp
            && self.router_hosts_others(ep, args.instance_id)
        {
            // Registering the model needs a router restart (it reads its presets only at
            // start), which would reload every co-hosted model: run this one on its own.
            return self.load_overflow(ctx, args, resolved, draft, ep).await;
        }
        let changed = {
            let mut g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
            let p = g.get_mut(&ep).ok_or_else(|| {
                ierr(
                    error_code::ENGINE_PROCESS_NOT_FOUND,
                    "engine process vanished",
                )
            })?;
            let changed = p.models.get(&name) != Some(&entry);
            p.models.insert(name.clone(), entry);
            p.instance_ids.insert(args.instance_id);
            changed
        };
        {
            let mut g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(i) = g.get_mut(&args.instance_id) {
                i.port = port;
                i.launch = Some(launch.clone());
                i.engine_process_id = Some(ep);
            }
        }
        if !known || changed {
            ctx.progress(
                Some(65.0),
                "registering model with the llama.cpp router (restart)",
            );
            self.reconfigure_router(ep, Some(args.instance_id), &ctx.cancel)
                .await?;
        } else {
            self.write_router_preset(ep)?;
        }
        ctx.progress(Some(75.0), format!("loading {name} into router"));
        let path = resolved.primary.display().to_string();
        driver
            .load_model(&base, &name, Some(&path))
            .await
            .map_err(|e| ierr(error_code::ENGINE_START_FAILED, format!("{e:#}")))?;
        let router_proc = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .and_then(|p| p.proc.clone());
        self.wait_healthy(
            &driver,
            port,
            &engine.health,
            Some(&name),
            router_proc.as_ref(),
            Duration::from_secs(args.health_timeout_seconds.max(5)),
            &ctx.cancel,
        )
        .await?;
        let mut ev = Event::info(
            EventKind::EngineStarted,
            format!(
                "model {name} loaded into router {ep} for instance {}",
                args.instance_id
            ),
        )
        .instance(args.instance_id)
        .deployment(args.deployment_id)
        .engine_process(ep);
        ev.command_id = ctx.command_id;
        self.event(ev);
        Ok((port, router_proc.and_then(|p| p.pid)))
    }

    fn write_router_preset(&self, ep: Uuid) -> Result<(), ErrorInfo> {
        let (path, entries) = {
            let g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
            let p = g.get(&ep).ok_or_else(|| {
                ierr(
                    error_code::ENGINE_PROCESS_NOT_FOUND,
                    "engine process vanished",
                )
            })?;
            let path = p
                .launch
                .router
                .as_ref()
                .map(|r| r.preset_path.clone())
                .ok_or_else(|| ierr(error_code::INTERNAL, "router files missing"))?;
            (
                path,
                p.models
                    .iter()
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect::<Vec<_>>(),
            )
        };
        std::fs::write(&path, crate::template::router_preset_ini(&entries)).map_err(|e| {
            ierr(
                error_code::INTERNAL,
                format!("write {}: {e}", path.display()),
            )
        })
    }

    /// Router `ep` serves models of other instances (loaded or loading, not overflow ones).
    fn router_hosts_others(&self, ep: Uuid, except: Uuid) -> bool {
        let ids = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .map(|p| p.instance_ids.clone())
            .unwrap_or_default();
        let g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
        ids.iter().filter(|i| **i != except).any(|i| {
            g.get(i).is_some_and(|x| {
                !x.overflow
                    && matches!(
                        x.state,
                        InstanceState::Ready | InstanceState::Loading | InstanceState::Draining
                    )
            })
        })
    }

    /// A model the router does not know while it hosts others: start it as its own
    /// single-model `llama-server` (the router's build and engine spec; `--models-dir` becomes
    /// `--model`) instead of restarting the router. Reported under the router
    /// (`engine_process_id`), stopped and crash-restarted like a dedicated instance.
    async fn load_overflow(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: &LoadModelArgs,
        resolved: &Resolved,
        draft: Option<&Resolved>,
        ep: Uuid,
    ) -> Result<(u16, Option<u32>), ErrorInfo> {
        let id = args.instance_id;
        let (engine, exe, lib) = {
            let g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
            let p = g.get(&ep).ok_or_else(|| {
                ierr(
                    error_code::ENGINE_PROCESS_NOT_FOUND,
                    "engine process vanished",
                )
            })?;
            (
                p.launch.engine.clone(),
                p.launch.executable.clone(),
                p.launch.library_dir.clone(),
            )
        };
        let port = self.alloc_port(args.port, engine.port_range)?;
        let mut launch = self.instance_launch(args, resolved, draft, port, exe);
        launch.engine = engine;
        launch.library_dir = lib;
        {
            let mut g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(i) = g.get_mut(&id) {
                i.overflow = true;
                i.engine_process_id = Some(ep);
            }
        }
        if let Some(p) = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get_mut(&ep)
        {
            p.instance_ids.insert(id);
        }
        info!(instance = %id, engine_process = %ep, port, "router hosts other models: starting {} in its own llama-server (no router restart)", args.model.served_model_name);
        ctx.progress(
            Some(65.0),
            "the router hosts other models: starting this one in its own llama-server",
        );
        let timeout = Duration::from_secs(args.health_timeout_seconds.max(5));
        match self
            .spawn_instance(id, launch, timeout, &ctx.cancel, ctx.command_id)
            .await
        {
            Ok(proc) => Ok((port, proc.pid)),
            Err(e) => {
                if let Some(p) = self
                    .engine_procs
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .get_mut(&ep)
                {
                    p.instance_ids.remove(&id);
                }
                Err(e)
            }
        }
    }

    /// Restart a router so it picks up a changed preset; co-hosted ready instances are drained
    /// (bounded by the default drain timeout), then re-loaded and resumed.
    async fn reconfigure_router(
        self: &Arc<Self>,
        ep: Uuid,
        except: Option<Uuid>,
        cancel: &CancellationToken,
    ) -> Result<(), ErrorInfo> {
        self.write_router_preset(ep)?;
        let co: Vec<(Uuid, String)> = {
            let ids = self
                .engine_procs
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get(&ep)
                .map(|p| p.instance_ids.clone())
                .unwrap_or_default();
            let g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
            ids.iter()
                .filter(|i| Some(**i) != except)
                .filter_map(|i| g.get(i))
                .filter(|i| i.state == InstanceState::Ready && !i.overflow)
                .map(|i| (i.id, i.served_model_name.clone()))
                .collect()
        };
        for (id, _) in &co {
            self.proxy.set_draining(*id, true);
        }
        let drain_for = Duration::from_secs(self.rt().drain_default());
        for (id, _) in &co {
            let _ = self.wait_drained(*id, drain_for, cancel, false).await;
            self.set_state(
                *id,
                InstanceState::Loading,
                Some(InstanceHealth::Unknown),
                Some("router restart to register a new model".into()),
            );
        }
        let old = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .and_then(|p| p.proc.clone());
        if let Some(p) = old {
            p.stop(Duration::from_secs(15)).await;
        }
        self.spawn_router(ep, cancel).await?;
        let (port, engine) = {
            let g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
            let p = g.get(&ep).ok_or_else(|| {
                ierr(
                    error_code::ENGINE_PROCESS_NOT_FOUND,
                    "engine process vanished",
                )
            })?;
            (p.port, p.launch.engine.clone())
        };
        let driver = self.engines.driver(engine.name);
        for (id, name) in co {
            // A co-hosted instance unloaded meanwhile (rolling update, swap) is not reloaded:
            // waiting for it would block the requested load until the startup timeout and
            // then mark a stopped instance as failed.
            if !self.hosted_by(ep, id) {
                continue;
            }
            let reload = async {
                driver
                    .load_model(&base_url(port), &name, None)
                    .await
                    .map_err(|e| ierr(error_code::ENGINE_START_FAILED, format!("{e:#}")))?;
                self.wait_healthy(
                    &driver,
                    port,
                    &engine.health,
                    Some(&name),
                    None,
                    Duration::from_secs_f64(engine.health.startup_timeout_seconds),
                    cancel,
                )
                .await
            };
            let gone = async {
                while self.hosted_by(ep, id) {
                    tokio::time::sleep(Duration::from_millis(500)).await;
                }
            };
            let r = tokio::select! {
                r = reload => r,
                _ = gone => continue,
            };
            if !self.hosted_by(ep, id) {
                continue;
            }
            self.proxy.set_draining(id, false);
            match r {
                Ok(()) => self.set_state(
                    id,
                    InstanceState::Ready,
                    Some(InstanceHealth::Healthy),
                    Some("reloaded after router restart".into()),
                ),
                Err(e) => self.set_state(
                    id,
                    InstanceState::Error,
                    Some(InstanceHealth::Unhealthy),
                    Some(e.message),
                ),
            }
        }
        Ok(())
    }

    /// `load_model` can arrive before (or while) the `start_engine` of its router runs: commands
    /// are dispatched independently and a first start downloads the engine artifact. Wait for the
    /// engine process while its start is in flight, and a short grace period for a start that
    /// has not arrived yet.
    async fn wait_engine_process(
        &self,
        ep: Uuid,
        cancel: &CancellationToken,
    ) -> Result<(), ErrorInfo> {
        let grace = Instant::now() + Duration::from_secs(30);
        loop {
            let running = self
                .engine_procs
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .contains_key(&ep);
            if running {
                return Ok(());
            }
            let starting = self
                .starting_engines
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .contains(&ep);
            if !starting && Instant::now() >= grace {
                return Err(ierr(
                    error_code::ENGINE_PROCESS_NOT_FOUND,
                    format!("engine process {ep} is not running on this node"),
                ));
            }
            tokio::select! {
                _ = tokio::time::sleep(Duration::from_millis(250)) => {}
                _ = cancel.cancelled() => return Err(ierr(error_code::CANCELLED, "cancelled")),
            }
        }
    }

    /// Whether `id` is still one of the instances served by router `ep` (unload removes it).
    fn hosted_by(&self, ep: Uuid, id: Uuid) -> bool {
        self.engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .is_some_and(|p| p.instance_ids.contains(&id))
    }

    /// Spawn the router process of `ep` (fresh or restart) and wait for `/health`.
    async fn spawn_router(
        self: &Arc<Self>,
        ep: Uuid,
        cancel: &CancellationToken,
    ) -> Result<Arc<ProcHandle>, ErrorInfo> {
        self.write_router_preset(ep)?;
        let launch = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .map(|p| p.launch.clone())
            .ok_or_else(|| {
                ierr(
                    error_code::ENGINE_PROCESS_NOT_FOUND,
                    "engine process vanished",
                )
            })?;
        let driver = self.engines.driver(launch.engine.name);
        let spec = self.make_proc(&launch, &driver)?;
        info!(engine_process = %ep, cmd = %spec.display(), "starting engine process");
        let proc = self.spawn_engine(&spec, self.log_target(None, Some(ep), None))?;
        let generation = {
            let mut g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
            let p = g.get_mut(&ep).ok_or_else(|| {
                ierr(
                    error_code::ENGINE_PROCESS_NOT_FOUND,
                    "engine process vanished",
                )
            })?;
            p.proc = Some(proc.clone());
            p.generation += 1;
            p.healthy = false;
            p.generation
        };
        let mut ev = Event::info(
            EventKind::EngineStarted,
            format!(
                "{} engine process {ep} started on 127.0.0.1:{} (pid {:?})",
                launch.engine.name, launch.port, proc.pid
            ),
        )
        .engine_process(ep);
        ev.details = json!({"pid": proc.pid, "port": launch.port, "command": spec.display()})
            .as_object()
            .cloned();
        self.event(ev);
        let timeout =
            Duration::from_secs_f64(launch.engine.health.startup_timeout_seconds.max(5.0));
        if let Err(e) = self
            .wait_healthy(
                &driver,
                launch.port,
                &launch.engine.health,
                None,
                Some(&proc),
                timeout,
                cancel,
            )
            .await
        {
            proc.stop(Duration::from_secs(5)).await;
            return Err(e);
        }
        if let Some(addr) =
            supervisor::exposed_on_network(launch.port, &crate::system::ip_addresses()).await
        {
            proc.stop(Duration::from_secs(5)).await;
            return Err(ierr(
                error_code::ENGINE_START_FAILED,
                format!("engine is reachable on a network interface ({addr})"),
            ));
        }
        let args = {
            let mut g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
            g.get_mut(&ep).map(|p| {
                p.healthy = true;
                p.args.clone()
            })
        };
        if let Some(args) = args {
            let rec = crate::state::ProcRecord {
                port: launch.port,
                pid: proc.pid,
                start_token: proc.start_token,
                overflow: false,
            };
            let _ = crate::state::save_engine_process(&self.paths, &args, rec);
        }
        driver.register(&base_url(launch.port), &launch);
        let me = self.clone();
        let p2 = proc.clone();
        tokio::spawn(async move { me.watch_router(ep, generation, p2).await });
        Ok(proc)
    }

    fn watch_router(
        self: Arc<Self>,
        ep: Uuid,
        generation: u64,
        proc: Arc<ProcHandle>,
    ) -> futures::future::BoxFuture<'static, ()> {
        Box::pin(async move {
            let exit = proc.wait().await;
            if proc.stop_requested() || self.shutdown.is_cancelled() {
                return;
            }
            let (ids, restarts) = {
                let mut g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
                let Some(p) = g.get_mut(&ep) else { return };
                if p.generation != generation {
                    return;
                }
                p.healthy = false;
                p.restarts.retain(|t| t.elapsed() < RESTART_WINDOW);
                (p.instance_ids.clone(), p.restarts.len())
            };
            let mut ev = Event::critical(
                EventKind::EngineCrashed,
                format!("engine process {ep} crashed: {}", exit.describe()),
            )
            .engine_process(ep);
            ev.exit = Some(exit.to_info(proc.last_lines()));
            self.event(ev);
            let live: Vec<Uuid> = {
                let g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
                ids.iter()
                    .filter(|i| {
                        g.get(i)
                            .is_some_and(|x| x.state != InstanceState::Stopped && !x.overflow)
                    })
                    .cloned()
                    .collect()
            };
            for id in &live {
                self.set_state(
                    *id,
                    InstanceState::Error,
                    Some(InstanceHealth::Unhealthy),
                    Some("engine process crashed".into()),
                );
            }
            if restarts >= MAX_RESTARTS {
                return;
            }
            tokio::select! {
                _ = tokio::time::sleep(Duration::from_secs(5 * (1 << restarts))) => {}
                _ = self.shutdown.cancelled() => return,
            }
            if let Some(p) = self
                .engine_procs
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get_mut(&ep)
            {
                if p.generation != generation {
                    return;
                }
                p.restarts.push(Instant::now());
            }
            let cancel = self.shutdown.child_token();
            if self.spawn_router(ep, &cancel).await.is_err() {
                return;
            }
            let (port, engine) = match self
                .engine_procs
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get(&ep)
            {
                Some(p) => (p.port, p.launch.engine.clone()),
                None => return,
            };
            let driver = self.engines.driver(engine.name);
            for id in live {
                let name = self
                    .instances
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .get(&id)
                    .map(|i| i.served_model_name.clone());
                let Some(name) = name else { continue };
                self.set_state(
                    id,
                    InstanceState::Loading,
                    Some(InstanceHealth::Unknown),
                    Some("reloading after engine restart".into()),
                );
                let r = async {
                    driver
                        .load_model(&base_url(port), &name, None)
                        .await
                        .map_err(|e| ierr(error_code::ENGINE_START_FAILED, format!("{e:#}")))?;
                    self.wait_healthy(
                        &driver,
                        port,
                        &engine.health,
                        Some(&name),
                        None,
                        Duration::from_secs_f64(engine.health.startup_timeout_seconds),
                        &cancel,
                    )
                    .await
                }
                .await;
                match r {
                    Ok(()) => self.set_state(
                        id,
                        InstanceState::Ready,
                        Some(InstanceHealth::Healthy),
                        Some("reloaded after engine restart".into()),
                    ),
                    Err(e) => self.set_state(
                        id,
                        InstanceState::Error,
                        Some(InstanceHealth::Unhealthy),
                        Some(e.message),
                    ),
                }
            }
        })
    }

    pub async fn start_engine(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: StartEngineArgs,
    ) -> Result<Value, ErrorInfo> {
        let ep = args.engine_process_id;
        self.starting_engines
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(ep);
        let out = self.start_engine_inner(ctx, args).await;
        self.starting_engines
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .remove(&ep);
        out
    }

    async fn start_engine_inner(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: StartEngineArgs,
    ) -> Result<Value, ErrorInfo> {
        let ep = args.engine_process_id;
        self.learn_device_ids(&args.devices);
        let existing = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .map(|p| {
                (
                    p.args == args,
                    p.proc.as_ref().is_some_and(|x| x.is_running()),
                    p.port,
                    p.proc.as_ref().and_then(|x| x.pid),
                )
            });
        if let Some((same, running, port, pid)) = existing {
            if same && running {
                return Ok(json!({"engine_process_id": ep, "port": port, "pid": pid}));
            }
            let _ = self
                .stop_engine(
                    &Ctx::internal(),
                    StopEngineArgs {
                        engine_process_id: ep,
                        force: true,
                        drain_timeout_seconds: 0,
                        kill_grace_seconds: 15,
                    },
                )
                .await;
        }
        let port = self.alloc_port(args.port, args.engine.port_range)?;
        let mut engine = args.engine.clone();
        let exe = self.ensure_artifact(&mut engine, &ctx.cancel).await?;
        let launch = self.router_launch(&args, engine, exe, port)?;
        let models = self
            .router_presets
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .remove(&ep)
            .unwrap_or_default();
        self.engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(
                ep,
                EngineProc {
                    id: ep,
                    args: args.clone(),
                    port,
                    proc: None,
                    launch,
                    models,
                    instance_ids: BTreeSet::new(),
                    generation: 0,
                    restarts: vec![],
                    healthy: false,
                    empty_since: None,
                },
            );
        ctx.progress(Some(20.0), "starting engine process");
        match self.spawn_router(ep, &ctx.cancel).await {
            Ok(proc) => {
                let rec = crate::state::ProcRecord {
                    port,
                    pid: proc.pid,
                    start_token: proc.start_token,
                    overflow: false,
                };
                let _ = crate::state::save_engine_process(&self.paths, &args, rec);
                self.inventory_dirty.notify_one();
                Ok(json!({"engine_process_id": ep, "port": port, "pid": proc.pid}))
            }
            Err(e) => {
                self.engine_procs
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .remove(&ep);
                Err(e)
            }
        }
    }

    /// The launch of a shared engine process (llama.cpp router: models dir + preset INI under
    /// `<state_dir>/engines/llama_cpp/routers/<id>/`).
    fn router_launch(
        &self,
        args: &StartEngineArgs,
        engine: EngineSpec,
        exe: Option<PathBuf>,
        port: u16,
    ) -> Result<EngineLaunch, ErrorInfo> {
        let ep = args.engine_process_id;
        let dir = self.paths.routers_dir().join(ep.to_string());
        let models_dir = dir.join("models");
        std::fs::create_dir_all(&models_dir).map_err(|e| {
            ierr(
                error_code::INTERNAL,
                format!("create {}: {e}", models_dir.display()),
            )
        })?;
        // Router mode: the agent manages the preset; other shared engines (custom engines that
        // load models via their own API) get the same files.
        Ok(EngineLaunch {
            engine,
            launch: args.launch.clone(),
            model: None,
            served_model_name: None,
            model_path: None,
            model_paths: vec![],
            model_dir: Some(models_dir.clone()),
            draft_model_path: None,
            port,
            devices: args.devices.clone(),
            executable: exe,
            library_dir: None,
            cache_dir: self.cache.dir.clone(),
            router: Some(RouterFiles {
                models_dir,
                preset_path: dir.join("preset.ini"),
            }),
            instance_id: None,
            engine_process_id: Some(ep),
        })
    }

    pub async fn stop_engine(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: StopEngineArgs,
    ) -> Result<Value, ErrorInfo> {
        let ep = args.engine_process_id;
        let Some(ids) = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .map(|p| p.instance_ids.clone())
        else {
            crate::state::remove_engine_process(&self.paths, ep);
            return Ok(json!({"engine_process_id": ep, "stopped": true, "already_stopped": true}));
        };
        let live: Vec<Uuid> = {
            let g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
            ids.iter()
                .filter(|i| g.get(i).is_some_and(|x| x.state != InstanceState::Stopped))
                .cloned()
                .collect()
        };
        if !args.force {
            for id in &live {
                self.proxy.set_draining(*id, true);
            }
            for id in &live {
                let (drained, a, w, _) = self
                    .wait_drained(
                        *id,
                        Duration::from_secs(args.drain_timeout_seconds),
                        &ctx.cancel,
                        true,
                    )
                    .await?;
                if !drained {
                    for i in &live {
                        self.proxy.set_draining(*i, false);
                    }
                    return Err(ierr(
                        error_code::DRAIN_TIMEOUT,
                        format!("instance {id} still has {a} active / {w} waiting sessions"),
                    ));
                }
            }
        }
        for id in &live {
            self.set_state(
                *id,
                InstanceState::Unloading,
                None,
                Some("stop_engine".into()),
            );
        }
        // Router models running as their own llama-server (overflow) stop with their router.
        let overflow: Vec<Arc<ProcHandle>> = {
            let g = self.instances.lock().unwrap_or_else(|e| e.into_inner());
            live.iter()
                .filter_map(|i| g.get(i))
                .filter(|i| i.overflow)
                .filter_map(|i| i.proc.clone())
                .collect()
        };
        for p in overflow {
            p.stop(Duration::from_secs(args.kill_grace_seconds.max(1)))
                .await;
        }
        let proc = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .and_then(|p| p.proc.clone());
        let exit = match proc {
            Some(p) => Some(
                p.stop(Duration::from_secs(args.kill_grace_seconds.max(1)))
                    .await,
            ),
            None => None,
        };
        if let Some((port, engine)) = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .map(|p| (p.port, p.launch.engine.name))
        {
            self.engines.driver(engine).forget(&base_url(port));
        }
        for id in &live {
            self.proxy.remove_local(id);
            crate::state::remove_instance(&self.paths, *id);
            self.set_state(
                *id,
                InstanceState::Stopped,
                Some(InstanceHealth::Unknown),
                Some("engine process stopped".into()),
            );
        }
        self.engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .remove(&ep);
        crate::state::remove_engine_process(&self.paths, ep);
        let mut ev = Event::info(
            EventKind::EngineStopped,
            format!("engine process {ep} stopped"),
        )
        .engine_process(ep);
        ev.exit = exit.map(|e| e.to_info(vec![]));
        ev.command_id = ctx.command_id;
        self.event(ev);
        self.inventory_dirty.notify_one();
        Ok(json!({"engine_process_id": ep, "stopped": true, "instances_stopped": live}))
    }

    // ------------------------------------------------------------------ drain / unload / restart

    /// Wait until the proxy sees no active or waiting requests for `id`.
    /// Returns (drained, active, waiting, elapsed_seconds).
    pub async fn wait_drained(
        &self,
        id: Uuid,
        timeout: Duration,
        cancel: &CancellationToken,
        progress_events: bool,
    ) -> Result<(bool, u64, u64, f64), ErrorInfo> {
        let started = Instant::now();
        let mut last_event = Instant::now();
        let dep = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&id)
            .map(|i| i.args.deployment_id);
        loop {
            let (a, w) = self.proxy.counts(&id);
            let el = started.elapsed().as_secs_f64();
            if a == 0 && w == 0 {
                return Ok((true, 0, 0, el));
            }
            if started.elapsed() >= timeout {
                return Ok((false, a, w, el));
            }
            if progress_events && last_event.elapsed() >= Duration::from_secs(5) {
                last_event = Instant::now();
                let mut ev = Event::info(
                    EventKind::DrainProgress,
                    format!("draining {id}: {a} active, {w} waiting"),
                )
                .instance(id);
                ev.deployment_id = dep;
                ev.drain = Some(DrainInfo {
                    active_sessions: a,
                    waiting_sessions: w,
                    elapsed_seconds: el,
                    timed_out: false,
                });
                self.event(ev);
            }
            tokio::select! {
                _ = tokio::time::sleep(Duration::from_millis(200)) => {}
                _ = cancel.cancelled() => return Err(ierr(error_code::CANCELLED, "cancelled")),
            }
        }
    }

    pub async fn drain_instance(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: DrainInstanceArgs,
    ) -> Result<Value, ErrorInfo> {
        let id = args.instance_id;
        let Some((state, dep)) = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&id)
            .map(|i| (i.state, i.args.deployment_id))
        else {
            return Err(ierr(
                error_code::INSTANCE_NOT_FOUND,
                format!("instance {id} not on this node"),
            ));
        };
        if args.resume {
            self.proxy.set_draining(id, false);
            if state == InstanceState::Draining {
                self.set_state(
                    id,
                    InstanceState::Ready,
                    None,
                    Some("drain cancelled".into()),
                );
            }
            let (a, w) = self.proxy.counts(&id);
            return Ok(
                json!({"instance_id": id, "drained": false, "resumed": true, "active_sessions": a, "waiting_sessions": w, "elapsed_seconds": 0}),
            );
        }
        self.proxy.set_draining(id, true);
        if state == InstanceState::Ready {
            self.set_state(
                id,
                InstanceState::Draining,
                None,
                Some("drain_instance".into()),
            );
        }
        let (drained, a, w, el) = self
            .wait_drained(
                id,
                Duration::from_secs(args.timeout_seconds),
                &ctx.cancel,
                true,
            )
            .await?;
        let mut ev = Event::info(
            EventKind::DrainCompleted,
            format!(
                "drain of {id} {}",
                if drained { "completed" } else { "timed out" }
            ),
        )
        .instance(id)
        .deployment(dep);
        ev.drain = Some(DrainInfo {
            active_sessions: a,
            waiting_sessions: w,
            elapsed_seconds: el,
            timed_out: !drained,
        });
        ev.command_id = ctx.command_id;
        self.event(ev);
        let res = json!({"instance_id": id, "drained": drained, "active_sessions": a, "waiting_sessions": w, "elapsed_seconds": el});
        if !drained && args.wait_for_zero {
            return Err(ierr(
                error_code::DRAIN_TIMEOUT,
                format!("{a} active / {w} waiting sessions after {el:.0}s"),
            )
            .with_details(res.as_object().cloned().unwrap_or_default()));
        }
        Ok(res)
    }

    pub async fn unload_model(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: UnloadModelArgs,
    ) -> Result<Value, ErrorInfo> {
        let id = args.instance_id;
        let info = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&id)
            .map(|i| {
                (
                    i.state,
                    i.proc.clone(),
                    i.engine_process_id,
                    i.served_model_name.clone(),
                    i.last_vram,
                    i.args.deployment_id,
                    i.overflow,
                )
            });
        let Some((state, proc, ep, name, vram, dep, overflow)) = info else {
            crate::state::remove_instance(&self.paths, id);
            return Ok(
                json!({"instance_id": id, "drained": true, "active_at_unload": 0, "freed_vram_bytes": 0, "already_stopped": true}),
            );
        };
        if state == InstanceState::Stopped {
            return Ok(
                json!({"instance_id": id, "drained": true, "active_at_unload": 0, "freed_vram_bytes": 0, "already_stopped": true}),
            );
        }
        let mut drained = true;
        self.proxy.set_draining(id, true);
        if !args.force {
            if state == InstanceState::Ready {
                self.set_state(
                    id,
                    InstanceState::Draining,
                    None,
                    Some("unload_model".into()),
                );
            }
            let (ok, a, w, el) = self
                .wait_drained(
                    id,
                    Duration::from_secs(args.drain_timeout_seconds),
                    &ctx.cancel,
                    true,
                )
                .await?;
            if !ok {
                self.proxy.set_draining(id, false);
                if state == InstanceState::Ready {
                    self.set_state(
                        id,
                        InstanceState::Ready,
                        None,
                        Some("unload aborted: drain timeout".into()),
                    );
                }
                return Err(ierr(
                    error_code::DRAIN_TIMEOUT,
                    format!("{a} active / {w} waiting sessions after {el:.0}s"),
                ));
            }
        } else {
            drained = self.proxy.counts(&id) == (0, 0);
        }
        let (active_at_unload, _) = self.proxy.counts(&id);
        self.set_state(
            id,
            InstanceState::Unloading,
            None,
            Some("unload_model".into()),
        );
        let mut exit = None;
        if overflow {
            // Its own llama-server: stop it, then the router bookkeeping below.
            if let Some(p) = &proc {
                exit = Some(
                    p.stop(Duration::from_secs(args.kill_grace_seconds.max(1)))
                        .await,
                );
            }
            let port = self
                .instances
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get(&id)
                .map(|i| (i.port, i.args.engine.name));
            if let Some((port, engine)) = port {
                self.engines.driver(engine).forget(&base_url(port));
            }
        }
        if let Some(ep) = ep {
            let (port, engine) = match self
                .engine_procs
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get(&ep)
            {
                Some(p) => (Some(p.port), Some(p.launch.engine.name)),
                None => (None, None),
            };
            if let (Some(port), Some(engine)) = (port, engine) {
                if overflow {
                    // not loaded in the router
                } else if let Err(e) = self
                    .engines
                    .driver(engine)
                    .unload_model(&base_url(port), &name)
                    .await
                {
                    warn!(instance = %id, "router unload: {e:#}");
                }
                let shared_names: BTreeSet<String> = self
                    .instances
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .values()
                    .filter(|o| {
                        o.id != id
                            && o.engine_process_id == Some(ep)
                            && o.state != InstanceState::Stopped
                            && !o.overflow
                    })
                    .map(|o| o.served_model_name.clone())
                    .collect();
                let empty = {
                    let mut g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
                    match g.get_mut(&ep) {
                        Some(p) => {
                            p.instance_ids.remove(&id);
                            if !shared_names.contains(&name) {
                                p.models.remove(&name);
                            }
                            p.instance_ids.is_empty()
                        }
                        None => false,
                    }
                };
                if !overflow {
                    let _ = self.write_router_preset(ep);
                }
                if empty && args.stop_engine_if_empty {
                    let _ = self
                        .stop_engine(
                            ctx,
                            StopEngineArgs {
                                engine_process_id: ep,
                                force: true,
                                drain_timeout_seconds: 0,
                                kill_grace_seconds: args.kill_grace_seconds,
                            },
                        )
                        .await;
                }
            }
        } else if let (false, Some(p)) = (overflow, proc) {
            exit = Some(
                p.stop(Duration::from_secs(args.kill_grace_seconds.max(1)))
                    .await,
            );
            let port = self
                .instances
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get(&id)
                .map(|i| (i.port, i.args.engine.name));
            if let Some((port, engine)) = port {
                self.engines.driver(engine).forget(&base_url(port));
            }
        }
        self.proxy.remove_local(&id);
        crate::state::remove_instance(&self.paths, id);
        self.set_state(
            id,
            InstanceState::Stopped,
            Some(InstanceHealth::Unknown),
            Some("unloaded".into()),
        );
        if self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&id)
            .is_some_and(|i| i.args.launch.distributed.is_some())
        {
            self.apply_egress();
        }
        let mut ev = Event::info(EventKind::EngineStopped, format!("instance {id} unloaded"))
            .instance(id)
            .deployment(dep);
        ev.exit = exit.map(|e| e.to_info(vec![]));
        ev.command_id = ctx.command_id;
        self.event(ev);
        Ok(
            json!({"instance_id": id, "drained": drained, "active_at_unload": active_at_unload, "freed_vram_bytes": vram.unwrap_or(0)}),
        )
    }

    pub async fn restart_instance(
        self: &Arc<Self>,
        ctx: &Ctx,
        args: RestartInstanceArgs,
    ) -> Result<Value, ErrorInfo> {
        let id = args.instance_id;
        let held = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&id)
            .map(|i| i.args.clone());
        let spec = match held {
            Some(a) => a,
            None => crate::state::load_instances(&self.paths)
                .into_iter()
                .find(|p| p.args.instance_id == id)
                .map(|p| p.args)
                .ok_or_else(|| {
                    ierr(
                        error_code::INSTANCE_NOT_FOUND,
                        format!("no spec held for instance {id}"),
                    )
                })?,
        };
        if self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .contains_key(&id)
        {
            if !args.force {
                self.proxy.set_draining(id, true);
                let (ok, a, w, el) = self
                    .wait_drained(
                        id,
                        Duration::from_secs(args.drain_timeout_seconds),
                        &ctx.cancel,
                        true,
                    )
                    .await?;
                if !ok {
                    self.proxy.set_draining(id, false);
                    return Err(ierr(
                        error_code::DRAIN_TIMEOUT,
                        format!("{a} active / {w} waiting sessions after {el:.0}s"),
                    ));
                }
            }
            self.unload_model(
                ctx,
                UnloadModelArgs {
                    instance_id: id,
                    force: true,
                    drain_timeout_seconds: 0,
                    kill_grace_seconds: 15,
                    stop_engine_if_empty: false,
                },
            )
            .await?;
        }
        self.load_model(ctx, spec).await
    }

    // ------------------------------------------------------------------ idle routers

    /// Whether router `ep` serves or is about to serve an instance (hosted, loading into it,
    /// or its own start still running).
    fn router_busy(&self, ep: Uuid) -> bool {
        let hosted = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&ep)
            .is_some_and(|p| !p.instance_ids.is_empty());
        hosted
            || self
                .instances
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .values()
                .any(|i| i.engine_process_id == Some(ep) && i.state != InstanceState::Stopped)
            || self
                .starting_engines
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .contains(&ep)
    }

    /// Shared engine processes (llama.cpp routers) that have had no instance for `grace`.
    /// Marks newly empty ones and clears the mark of busy ones.
    pub fn idle_routers(&self, now: Instant, grace: Duration) -> Vec<Uuid> {
        let ids: Vec<Uuid> = self
            .engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .keys()
            .cloned()
            .collect();
        let mut idle = vec![];
        for ep in ids {
            let busy = self.router_busy(ep);
            let mut g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
            let Some(p) = g.get_mut(&ep) else { continue };
            if busy {
                p.empty_since = None;
                continue;
            }
            let since = *p.empty_since.get_or_insert(now);
            if now.saturating_duration_since(since) >= grace {
                idle.push(ep);
            }
        }
        idle
    }

    /// Stop routers left without instances for `router_idle_stop_seconds` (after their last
    /// model moved away, a failed first load, or a restore nobody loads into again). The
    /// control plane also asks for the stop when it unloads the last instance
    /// (`stop_engine_if_empty`); this catches what that misses.
    pub async fn stop_idle_routers(self: &Arc<Self>) -> Vec<Uuid> {
        let grace = self.cfg.router_idle_stop_seconds;
        if grace == 0 {
            return vec![];
        }
        let mut stopped = vec![];
        for ep in self.idle_routers(Instant::now(), Duration::from_secs(grace)) {
            if self.router_busy(ep) {
                continue;
            }
            info!(engine_process = %ep, "stopping llama.cpp router: no instance for {grace}s");
            match self
                .stop_engine(
                    &Ctx::internal(),
                    StopEngineArgs {
                        engine_process_id: ep,
                        force: true,
                        drain_timeout_seconds: 0,
                        kill_grace_seconds: 15,
                    },
                )
                .await
            {
                Ok(_) => stopped.push(ep),
                Err(e) => warn!(engine_process = %ep, "idle router stop failed: {}", e.message),
            }
        }
        stopped
    }

    // ------------------------------------------------------------------ restore after agent restart

    /// Re-adopt the engines the previous agent process left running (they are detached, see
    /// `supervisor`): a persisted pid that is still the same process (start time) and answers
    /// its health check is taken over as is — same pid, same port, no reload. Router instances
    /// are adopted when their router was and still has the model loaded. Returns what could
    /// not be adopted, for [`Agent::relaunch`].
    pub async fn adopt_running(self: &Arc<Self>) -> Restore {
        let procs = crate::state::load_engine_processes(&self.paths);
        let insts = crate::state::load_instances(&self.paths);
        let mut out = Restore::default();
        if procs.is_empty() && insts.is_empty() {
            return out;
        }
        info!(
            "previous agent run left {} engine process(es) and {} instance(s) in {}",
            procs.len(),
            insts.len(),
            self.paths.root.display()
        );
        // Self-check: an engine left running that listens on a network interface is not taken
        // over; it is stopped and relaunched (bind flags forced to loopback).
        let addrs = crate::system::ip_addresses();
        let mut procs_ok = vec![];
        for p in procs {
            match supervisor::exposed_on_network(p.port, &addrs).await {
                Some(addr) if p.port != 0 => {
                    warn!(engine_process = %p.args.engine_process_id, %addr, "security self-check: engine reachable on a network interface; stopping and relaunching it on loopback");
                    out.procs.push(p);
                }
                _ => procs_ok.push(p),
            }
        }
        let mut insts_ok = vec![];
        for i in insts {
            let own = i.args.engine_process_id.is_none() || i.overflow;
            let worker = i
                .args
                .launch
                .distributed
                .as_ref()
                .is_some_and(|d| d.rank > 0);
            if own && !worker && i.port != 0 {
                if let Some(addr) = supervisor::exposed_on_network(i.port, &addrs).await {
                    warn!(instance = %i.args.instance_id, %addr, "security self-check: engine reachable on a network interface; stopping and relaunching it on loopback");
                    out.insts.push(i);
                    continue;
                }
            }
            insts_ok.push(i);
        }
        let (procs, insts) = (procs_ok, insts_ok);
        for p in procs {
            if self.adopt_engine_process(&p).await {
                info!(engine_process = %p.args.engine_process_id, pid = ?p.pid, port = p.port, "re-adopted running engine process");
            } else {
                out.procs.push(p);
            }
        }
        for i in insts {
            if self.adopt_instance(&i).await {
                info!(instance = %i.args.instance_id, pid = ?i.pid, port = i.port, "re-adopted running instance");
            } else {
                out.insts.push(i);
            }
        }
        if !out.procs.is_empty() || !out.insts.is_empty() {
            info!(
                "{} engine process(es) and {} instance(s) are not running any more; relaunching them",
                out.procs.len(),
                out.insts.len()
            );
        }
        self.inventory_dirty.notify_one();
        out
    }

    /// Start again what [`Agent::adopt_running`] could not take over, on the previous ports
    /// when they are free. A leftover process that failed adoption (unhealthy) is stopped first.
    pub async fn relaunch(self: &Arc<Self>, r: Restore) {
        for p in r.procs {
            if let Some(pid) = p.pid {
                supervisor::stop_detached(pid, p.start_token, Duration::from_secs(10)).await;
            }
            let mut a = p.args.clone();
            a.port = self.stable_port(p.port);
            // Start it with the models it had registered: their reloads below then go through
            // the router's load API (no restart, no overflow).
            let preset = self
                .paths
                .routers_dir()
                .join(p.args.engine_process_id.to_string())
                .join("preset.ini");
            if let Ok(text) = std::fs::read_to_string(&preset) {
                let models = crate::template::parse_router_preset_ini(&text);
                if !models.is_empty() {
                    self.router_presets
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .insert(p.args.engine_process_id, models);
                }
            }
            if let Err(e) = self.start_engine(&Ctx::internal(), a).await {
                warn!(engine_process = %p.args.engine_process_id, "restore failed: {e}");
            }
        }
        for i in r.insts {
            let own_process = i.args.engine_process_id.is_none() || i.overflow;
            if own_process {
                if let Some(pid) = i.pid {
                    supervisor::stop_detached(pid, i.start_token, Duration::from_secs(10)).await;
                }
            }
            let mut a = i.args.clone();
            if own_process {
                a.port = self.stable_port(i.port);
            }
            if let Err(e) = self.load_model(&Ctx::internal(), a).await {
                warn!(instance = %i.args.instance_id, "restore failed: {e}");
            }
        }
    }

    async fn adopt_engine_process(
        self: &Arc<Self>,
        p: &crate::state::PersistedEngineProcess,
    ) -> bool {
        let ep = p.args.engine_process_id;
        let Some(pid) = p.pid else { return false };
        if p.port == 0 || !supervisor::same_process(pid, p.start_token) {
            return false;
        }
        let mut engine = p.args.engine.clone();
        let Ok(exe) = self
            .ensure_artifact(&mut engine, &CancellationToken::new())
            .await
        else {
            return false;
        };
        let Ok(launch) = self.router_launch(&p.args, engine, exe, p.port) else {
            return false;
        };
        let driver = self.engines.driver(launch.engine.name);
        match driver
            .health(&base_url(p.port), &launch.engine.health, None)
            .await
        {
            Health::Healthy => {}
            other => {
                warn!(engine_process = %ep, pid, port = p.port, "running engine process not healthy ({other:?}); restarting it");
                return false;
            }
        }
        let Ok(spec) = self.make_proc(&launch, &driver) else {
            return false;
        };
        let Some(proc) = supervisor::adopt(
            &spec,
            pid,
            p.start_token,
            Some(self.log_target(None, Some(ep), None)),
        ) else {
            return false;
        };
        let models = launch
            .router
            .as_ref()
            .and_then(|r| std::fs::read_to_string(&r.preset_path).ok())
            .map(|t| crate::template::parse_router_preset_ini(&t))
            .unwrap_or_default();
        driver.register(&base_url(p.port), &launch);
        self.engine_procs
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(
                ep,
                EngineProc {
                    id: ep,
                    args: p.args.clone(),
                    port: p.port,
                    proc: Some(proc.clone()),
                    launch,
                    models,
                    instance_ids: BTreeSet::new(),
                    generation: 1,
                    restarts: vec![],
                    healthy: true,
                    empty_since: None,
                },
            );
        let rec = crate::state::ProcRecord {
            port: p.port,
            pid: Some(pid),
            start_token: proc.start_token,
            overflow: false,
        };
        let _ = crate::state::save_engine_process(&self.paths, &p.args, rec);
        let me = self.clone();
        tokio::spawn(async move { me.watch_router(ep, 1, proc).await });
        true
    }

    async fn adopt_instance(self: &Arc<Self>, i: &crate::state::PersistedInstance) -> bool {
        let args = &i.args;
        let id = args.instance_id;
        let cancel = CancellationToken::new();
        let router = args.engine_process_id.filter(|_| !i.overflow);
        // The router (instance in a router) must have been adopted; the process of a dedicated /
        // overflow instance must still be the same one.
        let router_port = match router {
            Some(ep) => match self
                .engine_procs
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get(&ep)
            {
                Some(p) => Some(p.port),
                None => return false,
            },
            None => {
                match i.pid {
                    Some(pid) if i.port != 0 && supervisor::same_process(pid, i.start_token) => {}
                    _ => return false,
                }
                None
            }
        };
        let Ok(resolved) = self
            .cache
            .ensure_model(&args.model, false, &cancel, &|_, _| {})
            .await
        else {
            return false;
        };
        let draft = match &args.draft_model {
            Some(d) => match self.cache.ensure_model(d, false, &cancel, &|_, _| {}).await {
                Ok(r) => Some(r),
                Err(_) => return false,
            },
            None => None,
        };
        let served = args.model.served_model_name.clone();
        let mut instance = Instance {
            id,
            args: args.clone(),
            state: InstanceState::Ready,
            health: InstanceHealth::Healthy,
            port: i.port,
            proc: None,
            engine_process_id: args.engine_process_id,
            served_model_name: served.clone(),
            message: Some("re-adopted after agent restart".into()),
            ready_at: Some(gpudeck_protocol::now()),
            stopped_at: None,
            launch: None,
            failures: 0,
            restarts: vec![],
            generation: 1,
            last_vram: None,
            next_health: Instant::now(),
            overflow: i.overflow,
        };
        let worker = crate::distributed::is_worker(&args.launch);
        let mut loading = false;
        match (router, router_port) {
            (Some(ep), Some(port)) => {
                let (engine, driver) = {
                    let g = self.engine_procs.lock().unwrap_or_else(|e| e.into_inner());
                    let Some(p) = g.get(&ep) else { return false };
                    (
                        p.launch.engine.clone(),
                        self.engines.driver(p.launch.engine.name),
                    )
                };
                let loaded = driver
                    .router_models(&base_url(port))
                    .await
                    .map(|ms| {
                        ms.iter()
                            .any(|m| m.id == served && m.status == "loaded" && !m.failed)
                    })
                    .unwrap_or(false);
                if !loaded
                    || driver
                        .health(&base_url(port), &engine.health, Some(&served))
                        .await
                        != Health::Healthy
                {
                    return false;
                }
                instance.port = port;
                instance.launch =
                    Some(self.instance_launch(args, &resolved, draft.as_ref(), port, None));
                if let Some(p) = self
                    .engine_procs
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .get_mut(&ep)
                {
                    p.instance_ids.insert(id);
                }
            }
            _ => {
                let Some(pid) = i.pid else { return false };
                // Same engine choice as the original launch (an overflow instance runs its
                // router's build).
                let overflow_engine = args.engine_process_id.and_then(|ep| {
                    self.engine_procs
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .get(&ep)
                        .map(|p| {
                            (
                                p.launch.engine.clone(),
                                p.launch.executable.clone(),
                                p.launch.library_dir.clone(),
                            )
                        })
                });
                let (engine, exe, lib) = match overflow_engine.filter(|_| i.overflow) {
                    Some(x) => x,
                    None => {
                        let mut engine = args.engine.clone();
                        let Ok(exe) = self.ensure_artifact(&mut engine, &cancel).await else {
                            return false;
                        };
                        (engine, exe, None)
                    }
                };
                let mut launch = self.instance_launch(args, &resolved, draft.as_ref(), i.port, exe);
                launch.engine = engine;
                launch.library_dir = lib;
                if crate::distributed::prepare(&mut launch).is_err() {
                    return false;
                }
                let driver = self.engines.driver(launch.engine.name);
                if !worker {
                    match driver
                        .health(&base_url(i.port), &launch.engine.health, None)
                        .await
                    {
                        Health::Healthy => {}
                        Health::Loading => loading = true,
                        Health::Unhealthy(why) => {
                            warn!(instance = %id, pid, port = i.port, "running engine not healthy ({why}); restarting it");
                            return false;
                        }
                    }
                }
                // Workers are re-created with the same spec they were started with.
                let spec = if worker && launch.engine.name == gpudeck_protocol::EngineName::LlamaCpp
                {
                    let Some(d) = launch.launch.distributed.clone() else {
                        return false;
                    };
                    let bind = crate::distributed::bind_address(
                        &d,
                        self.cfg.proxy_address.as_deref(),
                        &crate::system::ip_addresses(),
                    );
                    match crate::distributed::llama_rpc_worker(
                        &launch,
                        &d,
                        &bind,
                        self.cfg.engine_path("llama_cpp"),
                    ) {
                        Ok((s, _)) => s,
                        Err(_) => return false,
                    }
                } else {
                    match self.make_proc(&launch, &driver) {
                        Ok(s) => s,
                        Err(_) => return false,
                    }
                };
                let Some(proc) = supervisor::adopt(
                    &spec,
                    pid,
                    i.start_token,
                    Some(self.log_target(Some(id), None, Some(args.deployment_id))),
                ) else {
                    return false;
                };
                if !worker {
                    driver.register(&base_url(i.port), &launch);
                }
                instance.proc = Some(proc.clone());
                instance.launch = Some(launch);
                if loading {
                    instance.state = InstanceState::Loading;
                    instance.health = InstanceHealth::Unknown;
                    instance.ready_at = None;
                }
                if let Some(ep) = args.engine_process_id.filter(|_| i.overflow) {
                    if let Some(p) = self
                        .engine_procs
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .get_mut(&ep)
                    {
                        p.instance_ids.insert(id);
                    }
                }
                let me = self.clone();
                tokio::spawn(async move { me.watch_instance(id, 1, proc).await });
            }
        }
        let port = instance.port;
        let launch = instance.launch.clone();
        self.instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(id, instance);
        self.learn_device_ids(&args.devices);
        if !worker {
            let upstream = launch
                .and_then(|l| {
                    self.engines
                        .driver(args.engine.name)
                        .upstream_model_name(&l)
                })
                .unwrap_or(served);
            self.proxy.set_local(
                id,
                LocalRoute {
                    port,
                    engine: args.engine.name,
                    served_model_name: upstream,
                    max_concurrency: args.launch.max_concurrency,
                },
            );
            self.proxy.set_draining(id, false);
        }
        if args.launch.distributed.is_some() {
            self.apply_egress();
        }
        self.persist_instance(id);
        if loading {
            // It was still starting when the previous agent stopped: wait for it here.
            let me = self.clone();
            let health = args.engine.health.clone();
            let timeout = Duration::from_secs(args.health_timeout_seconds.max(5));
            let engine = args.engine.name;
            tokio::spawn(async move {
                let driver = me.engines.driver(engine);
                let proc = me
                    .instances
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .get(&id)
                    .and_then(|i| i.proc.clone());
                let r = me
                    .wait_healthy(
                        &driver,
                        port,
                        &health,
                        None,
                        proc.as_ref(),
                        timeout,
                        &me.shutdown.child_token(),
                    )
                    .await;
                match r {
                    Ok(()) => me.set_state(
                        id,
                        InstanceState::Ready,
                        Some(InstanceHealth::Healthy),
                        Some("re-adopted after agent restart".into()),
                    ),
                    Err(e) => me.set_state(
                        id,
                        InstanceState::Error,
                        Some(InstanceHealth::Unhealthy),
                        Some(e.message),
                    ),
                }
            });
        }
        true
    }
}

/// Background loop: stop idle routers (`router_idle_stop_seconds`, 0 = never).
pub async fn idle_router_loop(agent: Arc<Agent>) {
    let grace = agent.cfg.router_idle_stop_seconds;
    if grace == 0 {
        return;
    }
    let tick = Duration::from_millis((grace * 250).clamp(250, 15_000));
    loop {
        tokio::time::sleep(tick).await;
        agent.stop_idle_routers().await;
    }
}
