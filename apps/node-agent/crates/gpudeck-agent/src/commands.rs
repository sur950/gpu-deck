//! Command execution (`protocol.md` §3–4): idempotent by envelope id (persisted in
//! `commands.json`, a replayed id gets the latest known result instead of a second run),
//! `accepted` immediately, commands touching the same instance / engine process run in arrival
//! order, others concurrently up to `max_concurrent_commands`, per-command timeout, `cancel`,
//! progress as `running` results, terminal results re-sent until delivered.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use gpudeck_protocol::{
    error_code, Command, CommandKind, CommandName, CommandResult, ErrorInfo, ResultStatus,
};
use serde_json::{json, Value};
use tokio::sync::{watch, Semaphore};
use tokio_util::sync::CancellationToken;
use tracing::{info, warn};
use uuid::Uuid;

use crate::agent::Agent;
use crate::state::{CommandStore, StatePaths};

pub type ProgressFn = Arc<dyn Fn(Option<f64>, String) + Send + Sync>;

/// Execution context handed to every command handler.
#[derive(Clone)]
pub struct Ctx {
    pub command_id: Option<Uuid>,
    pub cancel: CancellationToken,
    progress: Option<ProgressFn>,
}

impl Ctx {
    /// For agent-internal operations (restore, auto-restart): no progress reporting.
    pub fn internal() -> Self {
        Self {
            command_id: None,
            cancel: CancellationToken::new(),
            progress: None,
        }
    }

    pub fn new(command_id: Uuid, cancel: CancellationToken, progress: ProgressFn) -> Self {
        Self {
            command_id: Some(command_id),
            cancel,
            progress: Some(progress),
        }
    }

    pub fn progress(&self, pct: Option<f64>, msg: impl Into<String>) {
        if let Some(p) = &self.progress {
            p(pct, msg.into());
        }
    }

    pub fn progress_fn(&self) -> ProgressFn {
        self.progress.clone().unwrap_or_else(|| Arc::new(|_, _| {}))
    }
}

pub struct CommandRunner {
    store: Mutex<CommandStore>,
    path: PathBuf,
    running: Mutex<HashMap<Uuid, CancellationToken>>,
    chains: Mutex<HashMap<String, watch::Receiver<bool>>>,
    sem: Arc<Semaphore>,
}

impl CommandRunner {
    pub fn new(paths: &StatePaths, max_concurrent: usize) -> Self {
        let path = paths.commands_json();
        let mut store = CommandStore::load(&path);
        // Commands that were running when the agent stopped can never finish: fail them so the
        // hub gets a terminal result for every id.
        let stale: Vec<Uuid> = store
            .records
            .values()
            .filter(|r| {
                r.last_result
                    .as_ref()
                    .is_none_or(|x| !x.status.is_terminal())
            })
            .map(|r| r.id)
            .collect();
        for id in stale {
            let name = store
                .records
                .get(&id)
                .and_then(|r| r.last_result.as_ref().map(|x| x.command))
                .or_else(|| {
                    store.records.get(&id).and_then(|r| {
                        serde_json::from_value::<CommandName>(Value::String(r.command.clone())).ok()
                    })
                });
            if let Some(name) = name {
                store.set_result(
                    id,
                    CommandResult::failed(
                        name,
                        ErrorInfo::new(
                            error_code::INTERNAL,
                            "agent restarted while the command was running",
                        )
                        .retryable(),
                    ),
                );
            }
        }
        let _ = store.save(&path);
        Self {
            store: Mutex::new(store),
            path,
            running: Mutex::new(HashMap::new()),
            chains: Mutex::new(HashMap::new()),
            sem: Arc::new(Semaphore::new(max_concurrent.max(1))),
        }
    }

    fn store(&self) -> std::sync::MutexGuard<'_, CommandStore> {
        self.store.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn running_ids(&self) -> Vec<Uuid> {
        self.running
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .keys()
            .cloned()
            .collect()
    }

    pub fn undelivered(&self) -> Vec<(Uuid, CommandResult)> {
        self.store().undelivered()
    }

    pub fn mark_delivered(&self, ids: &[Uuid]) {
        let mut s = self.store();
        for id in ids {
            s.mark_delivered(*id);
        }
        let _ = s.save(&self.path);
    }

    pub fn last_result(&self, id: Uuid) -> Option<CommandResult> {
        self.store().get(id).and_then(|r| r.last_result.clone())
    }

    fn record(&self, id: Uuid, res: &CommandResult, persist: bool) {
        let mut s = self.store();
        s.set_result(id, res.clone());
        if persist {
            if let Err(e) = s.save(&self.path) {
                warn!("commands.json save failed: {e}");
            }
        }
    }

    /// Handle a `command` frame.
    pub fn submit(&self, agent: &Arc<Agent>, id: Uuid, cmd: Command) {
        let name = cmd.kind.name();
        {
            let mut s = self.store();
            if let Some(rec) = s.get(id) {
                // Replay: never execute twice; answer with the latest known result.
                let res = rec
                    .last_result
                    .clone()
                    .unwrap_or_else(|| CommandResult::accepted(name));
                drop(s);
                info!(command = %name, %id, status = ?res.status, "replayed command; re-sending latest result");
                agent.outbox.result(id, res);
                return;
            }
            s.insert(id, &name.to_string());
            let _ = s.save(&self.path);
        }
        info!(command = %name, %id, args = %crate::state::redact_for_log(&cmd.kind), "command received");
        let mut accepted = CommandResult::accepted(name);
        accepted.instance_id = cmd.kind.instance_id();
        self.record(id, &accepted, true);
        agent.outbox.result(id, accepted.clone());

        // Ordering keys.
        let keys: Vec<String> = cmd
            .kind
            .instance_id()
            .map(|i| format!("i:{i}"))
            .into_iter()
            .chain(cmd.kind.engine_process_id().map(|e| format!("e:{e}")))
            .chain(cmd.kind.ssh_instance_id().map(|s| format!("s:{s}")))
            .collect();
        let (tx, rx) = watch::channel(false);
        let prev: Vec<watch::Receiver<bool>> = {
            let mut chains = self.chains.lock().unwrap_or_else(|e| e.into_inner());
            chains.retain(|_, r| !*r.borrow());
            keys.iter()
                .filter_map(|k| {
                    let p = chains.get(k).cloned();
                    chains.insert(k.clone(), rx.clone());
                    p
                })
                .collect()
        };
        let cancel = CancellationToken::new();
        self.running
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(id, cancel.clone());
        let agent = agent.clone();
        let sem = self.sem.clone();
        let started_at = accepted.started_at;
        tokio::spawn(async move {
            for mut p in prev {
                while !*p.borrow() {
                    if p.changed().await.is_err() {
                        break;
                    }
                }
            }
            let unbounded = matches!(
                cmd.kind,
                CommandKind::Cancel(_)
                    | CommandKind::UpdateConfig(_)
                    | CommandKind::DrainInstance(_)
            );
            let _permit = if unbounded {
                None
            } else {
                sem.acquire_owned().await.ok()
            };
            let instance_id = cmd.kind.instance_id();
            let a2 = agent.clone();
            let last_sent = Arc::new(Mutex::new(Instant::now() - Duration::from_secs(5)));
            let progress: ProgressFn = Arc::new(move |pct, msg| {
                let mut l = last_sent.lock().unwrap_or_else(|e| e.into_inner());
                if l.elapsed() < Duration::from_millis(900) {
                    return;
                }
                *l = Instant::now();
                let mut r = CommandResult::running(name, pct, msg);
                r.instance_id = instance_id;
                r.started_at = started_at;
                a2.commands.record(id, &r, false);
                a2.outbox.result(id, r);
            });
            let ctx = Ctx::new(id, cancel.clone(), progress);
            let timeout = Duration::from_secs(cmd.timeout_seconds.max(1));
            let out = tokio::time::timeout(timeout, execute(&agent, &ctx, cmd.kind)).await;
            let out = match out {
                Ok(r) => r,
                Err(_) => {
                    cancel.cancel();
                    Err(ErrorInfo::new(
                        error_code::TIMEOUT,
                        format!("command exceeded timeout_seconds={}", timeout.as_secs()),
                    ))
                }
            };
            let mut res = match out {
                Ok(v) => CommandResult::succeeded(name, v),
                Err(e) => {
                    warn!(command = %name, %id, error = %e, "command failed");
                    CommandResult::failed(name, e)
                }
            };
            res.instance_id = instance_id;
            res.started_at = started_at;
            let _ = tx.send(true);
            agent
                .commands
                .running
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .remove(&id);
            agent.commands.record(id, &res, true);
            info!(command = %name, %id, status = ?res.status, "command finished");
            agent.outbox.result(id, res);
        });
    }

    fn cancel(&self, target: Uuid) -> bool {
        match self
            .running
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&target)
        {
            Some(t) => {
                t.cancel();
                true
            }
            None => false,
        }
    }
}

async fn execute(agent: &Arc<Agent>, ctx: &Ctx, kind: CommandKind) -> Result<Value, ErrorInfo> {
    match kind {
        CommandKind::LoadModel(a) => agent.load_model(ctx, a).await,
        CommandKind::UnloadModel(a) => agent.unload_model(ctx, a).await,
        CommandKind::DrainInstance(a) => agent.drain_instance(ctx, a).await,
        CommandKind::StartEngine(a) => agent.start_engine(ctx, a).await,
        CommandKind::StopEngine(a) => agent.stop_engine(ctx, a).await,
        CommandKind::RestartInstance(a) => agent.restart_instance(ctx, a).await,
        CommandKind::PrefetchModel(a) => {
            let p = ctx.progress_fn();
            let r = agent
                .cache
                .prefetch(&a, &ctx.cancel, &|pct, m| p(Some(pct), m))
                .await;
            agent.check_cache_pressure();
            agent.inventory_dirty.notify_one();
            r
        }
        CommandKind::EvictCache(a) => {
            let r = agent.cache.evict(&a, &agent.files_in_use());
            agent.inventory_dirty.notify_one();
            r
        }
        CommandKind::RunBenchmark(a) => crate::benchmark::run(agent, ctx, a).await,
        CommandKind::UpdateConfig(a) => {
            let Some(settings) = a.settings else {
                return Ok(json!({"applied": false}));
            };
            agent.merge_settings(&settings);
            Ok(json!({"applied": true, "settings": settings}))
        }
        CommandKind::RotateCert(a) => crate::certs::rotate(agent, a).await,
        CommandKind::InstallCert(a) => crate::certs::install(agent, a).await,
        CommandKind::Cancel(a) => {
            Ok(json!({"cancelled": agent.commands.cancel(a.target_command_id)}))
        }
        CommandKind::ScanPath(a) => crate::scan::scan_path(ctx, a).await,
        CommandKind::HashFiles(a) => {
            crate::scan::hash_files(ctx, a, Some(agent.cache.clone())).await
        }
        CommandKind::StartSshInstance(a) => agent.start_ssh_instance(ctx, a).await,
        CommandKind::StopSshInstance(a) => agent.stop_ssh_instance(ctx, a).await,
    }
}

/// True when the status means the command is finished.
pub fn is_terminal(s: ResultStatus) -> bool {
    s.is_terminal()
}
