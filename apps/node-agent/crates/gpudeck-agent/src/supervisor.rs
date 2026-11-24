//! Engine process supervision.
//!
//! Engines are **detached** from the agent so they survive an agent restart or upgrade
//! (`systemctl restart gpudeck-agent`, a new binary): each one is spawned in its own session
//! (`setsid`, so it is also its own process group and stop/kill reach every child, e.g. llama.cpp
//! router children), optionally inside its own transient systemd scope (`systemd-run --scope`,
//! out of the agent's cgroup), and writes stdout / stderr to files under `<state_dir>/logs/`
//! instead of pipes (a pipe would break — SIGPIPE — when the agent exits). The agent follows those
//! files (tail ring + forwarded log events), watches for exit (`waitpid` for its own children,
//! polling pid + start time for processes it re-adopted after a restart), stops gracefully
//! (SIGTERM → grace → SIGKILL of the group, `docker stop` for containers), allocates free loopback
//! ports and checks that an engine is not reachable on a non-loopback address.

use std::collections::VecDeque;
use std::io::{Read, Seek, SeekFrom};
use std::net::{IpAddr, SocketAddr, TcpListener};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use gpudeck_protocol::{
    Event, EventKind, ExitInfo, LogBatch, LogLevel, LogLine, LogSource, LogStream, Severity,
};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::sync::{mpsc, watch};
use uuid::Uuid;

use crate::backend::ProcSpec;
use crate::outbox::Outbox;

const TAIL_LINES: usize = 50;
const MAX_LINE: usize = 16_000;
/// Engine output files are copied to `<file>.1` and truncated past this size.
pub const LOG_ROTATE_BYTES: u64 = 16 * 1024 * 1024;
/// How often an adopted process (not our child) is checked for exit.
const ADOPT_POLL: Duration = Duration::from_millis(250);
/// How often engine output files are read.
const TAIL_POLL: Duration = Duration::from_millis(150);

/// How a process ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Exit {
    pub code: Option<i32>,
    pub signal: Option<i32>,
}

impl Exit {
    pub fn to_info(&self, last_lines: Vec<String>) -> ExitInfo {
        let oom = self.signal == Some(libc::SIGKILL)
            && last_lines
                .iter()
                .any(|l| l.to_ascii_lowercase().contains("out of memory"));
        ExitInfo {
            code: self.code,
            signal: self.signal.map(signal_name),
            oom,
            last_lines,
        }
    }
    pub fn describe(&self) -> String {
        match (self.code, self.signal) {
            (Some(c), _) => format!("exit code {c}"),
            (None, Some(s)) => format!("killed by {}", signal_name(s)),
            _ => "exited".into(),
        }
    }
}

pub fn signal_name(s: i32) -> String {
    match s {
        libc::SIGTERM => "SIGTERM".into(),
        libc::SIGKILL => "SIGKILL".into(),
        libc::SIGINT => "SIGINT".into(),
        libc::SIGSEGV => "SIGSEGV".into(),
        libc::SIGABRT => "SIGABRT".into(),
        libc::SIGBUS => "SIGBUS".into(),
        other => format!("SIG{other}"),
    }
}

/// Where engine output goes besides the tail ring.
#[derive(Clone)]
pub struct LogTarget {
    pub outbox: Arc<Outbox>,
    pub instance_id: Option<Uuid>,
    pub engine_process_id: Option<Uuid>,
    pub deployment_id: Option<Uuid>,
    pub forward: Arc<AtomicBool>,
    pub max_lines: usize,
    pub flush: Duration,
    /// Engine stdout file (`<state_dir>/logs/<id>.log`; stderr goes to `<id>.err.log`). With a
    /// file the engine is detached and survives the agent; without one (tests) it writes to
    /// pipes and dies with the agent.
    pub file: Option<PathBuf>,
}

/// `<id>.log` → `<id>.err.log`.
pub fn stderr_path(stdout: &Path) -> PathBuf {
    stdout.with_extension("err.log")
}

/// Run engines in a transient systemd scope (`systemd-run --scope`) when the agent itself runs
/// under systemd and is allowed to create scopes (root, or a polkit rule). `mode`: `auto`
/// (probe once), `systemd` (always), anything else never.
pub fn scope_wrapper(mode: &str) -> bool {
    static PROBED: OnceLock<bool> = OnceLock::new();
    match mode {
        "systemd" => true,
        "auto" => *PROBED.get_or_init(|| {
            if !cfg!(target_os = "linux") || std::env::var_os("INVOCATION_ID").is_none() {
                return false;
            }
            let ok = std::process::Command::new("systemd-run")
                .args([
                    "--scope",
                    "--quiet",
                    "--collect",
                    "--no-ask-password",
                    "--",
                    "true",
                ])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .map(|s| s.success())
                .unwrap_or(false);
            tracing::info!(
                "engines run {} (systemd-run --scope {})",
                if ok {
                    "in their own transient systemd scopes"
                } else {
                    "in the agent's unit (KillMode=process keeps them across agent restarts)"
                },
                if ok { "allowed" } else { "not allowed" }
            );
            ok
        }),
        _ => false,
    }
}

/// Process liveness + identity: the start time distinguishes a reused pid.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProcState {
    Gone,
    /// Alive; start-time token (`/proc/<pid>/stat` starttime, macOS `pbi_start_tv*`).
    Alive(u64),
    /// Alive, identity unknown on this platform.
    AliveUnknown,
}

pub fn proc_state(pid: u32) -> ProcState {
    #[cfg(target_os = "linux")]
    {
        match std::fs::read_to_string(format!("/proc/{pid}/stat")) {
            Ok(stat) => {
                // Fields after the `(comm)`; comm may contain spaces and parentheses.
                let Some(rest) = stat.rfind(')').map(|i| &stat[i + 1..]) else {
                    return ProcState::AliveUnknown;
                };
                let f: Vec<&str> = rest.split_whitespace().collect();
                if matches!(f.first(), Some(&"Z") | Some(&"X")) {
                    return ProcState::Gone;
                }
                match f.get(19).and_then(|v| v.parse::<u64>().ok()) {
                    Some(t) => ProcState::Alive(t),
                    None => ProcState::AliveUnknown,
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => ProcState::Gone,
            Err(_) => {
                if pid_alive(pid) {
                    ProcState::AliveUnknown
                } else {
                    ProcState::Gone
                }
            }
        }
    }
    #[cfg(target_os = "macos")]
    {
        // SAFETY: proc_pidinfo fills at most `size` bytes of a zeroed proc_bsdinfo.
        unsafe {
            let mut info: libc::proc_bsdinfo = std::mem::zeroed();
            let size = std::mem::size_of::<libc::proc_bsdinfo>() as libc::c_int;
            let n = libc::proc_pidinfo(
                pid as libc::c_int,
                libc::PROC_PIDTBSDINFO,
                0,
                &mut info as *mut _ as *mut libc::c_void,
                size,
            );
            if n == size {
                if info.pbi_status == libc::SZOMB {
                    return ProcState::Gone;
                }
                return ProcState::Alive(
                    info.pbi_start_tvsec
                        .wrapping_mul(1_000_000)
                        .wrapping_add(info.pbi_start_tvusec),
                );
            }
        }
        if pid_alive(pid) {
            ProcState::AliveUnknown
        } else {
            ProcState::Gone
        }
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    {
        if pid_alive(pid) {
            ProcState::AliveUnknown
        } else {
            ProcState::Gone
        }
    }
}

/// Start-time token of a live process (persisted with its pid).
pub fn start_token(pid: u32) -> Option<u64> {
    match proc_state(pid) {
        ProcState::Alive(t) => Some(t),
        _ => None,
    }
}

/// `pid` is alive and (when both are known) still the process that had `token`.
pub fn same_process(pid: u32, token: Option<u64>) -> bool {
    match (proc_state(pid), token) {
        (ProcState::Gone, _) => false,
        (ProcState::Alive(t), Some(want)) => t == want,
        _ => true,
    }
}

pub struct ProcHandle {
    pub spec: ProcSpec,
    pub pid: Option<u32>,
    /// Start-time token of `pid` (pid-reuse guard for re-adoption after an agent restart).
    pub start_token: Option<u64>,
    pub started: Instant,
    /// Started by an earlier agent process and re-adopted (exit status is not observable).
    pub adopted: bool,
    tail: Arc<Mutex<VecDeque<String>>>,
    exit_rx: watch::Receiver<Option<Exit>>,
    stop_requested: AtomicBool,
}

impl std::fmt::Debug for ProcHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ProcHandle")
            .field("pid", &self.pid)
            .field("program", &self.spec.program)
            .field("adopted", &self.adopted)
            .finish()
    }
}

impl ProcHandle {
    pub fn exited(&self) -> Option<Exit> {
        self.exit_rx.borrow().clone()
    }

    pub fn is_running(&self) -> bool {
        self.exited().is_none()
    }

    pub fn stop_requested(&self) -> bool {
        self.stop_requested.load(Ordering::SeqCst)
    }

    pub fn last_lines(&self) -> Vec<String> {
        self.tail
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .cloned()
            .collect()
    }

    pub async fn wait(&self) -> Exit {
        let mut rx = self.exit_rx.clone();
        loop {
            if let Some(e) = rx.borrow().clone() {
                return e;
            }
            if rx.changed().await.is_err() {
                return rx.borrow().clone().unwrap_or(Exit {
                    code: None,
                    signal: None,
                });
            }
        }
    }

    /// Graceful stop: SIGTERM (or `docker stop`) → wait `grace` → SIGKILL the process group.
    pub async fn stop(&self, grace: Duration) -> Exit {
        self.stop_requested.store(true, Ordering::SeqCst);
        if let Some(e) = self.exited() {
            return e;
        }
        if let Some(c) = &self.spec.container {
            let _ = tokio::process::Command::new(&c.runtime)
                .args(["stop", "-t", &grace.as_secs().max(1).to_string(), &c.name])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .await;
        }
        self.signal(libc::SIGTERM);
        if let Ok(e) = tokio::time::timeout(grace, self.wait()).await {
            return e;
        }
        tracing::warn!(pid = ?self.pid, "engine did not stop within {:?}; SIGKILL", grace);
        self.signal(libc::SIGKILL);
        match tokio::time::timeout(Duration::from_secs(10), self.wait()).await {
            Ok(e) => e,
            Err(_) => Exit {
                code: None,
                signal: Some(libc::SIGKILL),
            },
        }
    }

    /// Send a signal to the whole process group (only while it is still the same process).
    pub fn signal(&self, sig: i32) {
        if let Some(pid) = self.pid {
            if self.exited().is_none() && same_process(pid, self.start_token) {
                kill_group(pid, sig);
            }
        }
    }
}

pub fn kill_group(pid: u32, sig: i32) {
    // SAFETY: plain libc kill(2) on a process group id we created (setsid / process_group(0)).
    unsafe {
        if libc::kill(-(pid as i32), sig) != 0 {
            libc::kill(pid as i32, sig);
        }
    }
}

pub fn pid_alive(pid: u32) -> bool {
    // SAFETY: signal 0 only checks existence/permission.
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

/// Stop a process left by an earlier agent (identity checked): SIGTERM the group, wait up to
/// `grace`, then SIGKILL. Returns once it is gone (or after the SIGKILL grace).
pub async fn stop_detached(pid: u32, token: Option<u64>, grace: Duration) {
    if !same_process(pid, token) {
        return;
    }
    kill_group(pid, libc::SIGTERM);
    let until = Instant::now() + grace;
    while Instant::now() < until {
        if !same_process(pid, token) {
            return;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    if same_process(pid, token) {
        kill_group(pid, libc::SIGKILL);
        for _ in 0..50 {
            if !same_process(pid, token) {
                return;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }
}

fn open_append(p: &Path) -> std::io::Result<std::fs::File> {
    if let Some(d) = p.parent() {
        std::fs::create_dir_all(d)?;
    }
    let mut o = std::fs::OpenOptions::new();
    o.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    o.open(p)
}

/// Spawn `spec` detached (own session, output to the log target's files) or, without a file,
/// attached with piped output (tests). `scope`: wrap it in `systemd-run --scope`.
pub fn spawn(spec: &ProcSpec, log: Option<LogTarget>) -> Result<Arc<ProcHandle>> {
    spawn_with(spec, log, false)
}

pub fn spawn_with(spec: &ProcSpec, log: Option<LogTarget>, scope: bool) -> Result<Arc<ProcHandle>> {
    let files = log.as_ref().and_then(|l| l.file.clone());
    let mut cmd = if scope && files.is_some() {
        let mut c = tokio::process::Command::new("systemd-run");
        c.args([
            "--scope",
            "--quiet",
            "--collect",
            "--no-ask-password",
            "--slice=gpudeck-engines.slice",
        ]);
        c.arg(format!(
            "--description=GPU Deck engine {}",
            log.as_ref()
                .and_then(|l| l.instance_id.or(l.engine_process_id))
                .map(|u| u.to_string())
                .unwrap_or_default()
        ));
        c.arg("--").arg(&spec.program);
        c
    } else {
        tokio::process::Command::new(&spec.program)
    };
    cmd.args(&spec.args)
        .envs(&spec.env)
        .stdin(Stdio::null())
        .kill_on_drop(false);
    let mut offsets = None;
    match &files {
        Some(out) => {
            let err = stderr_path(out);
            let fo = open_append(out).with_context(|| format!("open {}", out.display()))?;
            let fe = open_append(&err).with_context(|| format!("open {}", err.display()))?;
            offsets = Some((
                fo.metadata().map(|m| m.len()).unwrap_or(0),
                fe.metadata().map(|m| m.len()).unwrap_or(0),
            ));
            cmd.stdout(Stdio::from(fo)).stderr(Stdio::from(fe));
            #[cfg(unix)]
            // SAFETY: setsid(2) is async-signal-safe; it makes the child a session and process
            // group leader so it is not part of the agent's session/group (signals, launchd).
            unsafe {
                cmd.pre_exec(|| {
                    if libc::setsid() == -1 {
                        return Err(std::io::Error::last_os_error());
                    }
                    Ok(())
                });
            }
        }
        None => {
            cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
            #[cfg(unix)]
            cmd.process_group(0);
        }
    }
    if let Some(cwd) = &spec.cwd {
        cmd.current_dir(cwd);
    }
    let mut child = cmd
        .spawn()
        .with_context(|| format!("spawn {}", spec.program))?;
    let pid = child.id();
    let token = pid.and_then(start_token);
    let tail = Arc::new(Mutex::new(VecDeque::with_capacity(TAIL_LINES)));
    let (exit_tx, exit_rx) = watch::channel(None);

    let (line_tx, line_rx) = mpsc::unbounded_channel::<(LogStream, String)>();
    match (&files, offsets) {
        (Some(out), Some((o_off, e_off))) => {
            tokio::spawn(tail_file(
                out.clone(),
                o_off,
                LogStream::Stdout,
                tail.clone(),
                line_tx.clone(),
                exit_rx.clone(),
            ));
            tokio::spawn(tail_file(
                stderr_path(out),
                e_off,
                LogStream::Stderr,
                tail.clone(),
                line_tx.clone(),
                exit_rx.clone(),
            ));
        }
        _ => {
            if let Some(out) = child.stdout.take() {
                tokio::spawn(read_lines(
                    out,
                    LogStream::Stdout,
                    tail.clone(),
                    line_tx.clone(),
                ));
            }
            if let Some(err) = child.stderr.take() {
                tokio::spawn(read_lines(
                    err,
                    LogStream::Stderr,
                    tail.clone(),
                    line_tx.clone(),
                ));
            }
        }
    }
    drop(line_tx);
    tokio::spawn(forward_logs(line_rx, log));
    tokio::spawn(async move {
        let status = child.wait().await;
        let exit = match status {
            Ok(st) => {
                #[cfg(unix)]
                let signal = std::os::unix::process::ExitStatusExt::signal(&st);
                #[cfg(not(unix))]
                let signal = None;
                Exit {
                    code: st.code(),
                    signal,
                }
            }
            Err(_) => Exit {
                code: None,
                signal: None,
            },
        };
        let _ = exit_tx.send(Some(exit));
    });
    Ok(Arc::new(ProcHandle {
        spec: spec.clone(),
        pid,
        start_token: token,
        started: Instant::now(),
        adopted: false,
        tail,
        exit_rx,
        stop_requested: AtomicBool::new(false),
    }))
}

/// Re-adopt an engine started by an earlier agent process: `pid` must still be the same process
/// (`token`). Its output files (from the log target) are followed from their current end (the
/// last lines fill the tail ring, unforwarded); exit is detected by polling. `None` when the
/// process is gone or is another process now.
pub fn adopt(
    spec: &ProcSpec,
    pid: u32,
    token: Option<u64>,
    log: Option<LogTarget>,
) -> Option<Arc<ProcHandle>> {
    if !same_process(pid, token) {
        return None;
    }
    let token = token.or_else(|| start_token(pid));
    let tail = Arc::new(Mutex::new(VecDeque::with_capacity(TAIL_LINES)));
    let (exit_tx, exit_rx) = watch::channel(None);
    let (line_tx, line_rx) = mpsc::unbounded_channel::<(LogStream, String)>();
    if let Some(out) = log.as_ref().and_then(|l| l.file.clone()) {
        for (path, stream) in [
            (out.clone(), LogStream::Stdout),
            (stderr_path(&out), LogStream::Stderr),
        ] {
            let len = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
            prefill_tail(&path, len, &tail);
            tokio::spawn(tail_file(
                path,
                len,
                stream,
                tail.clone(),
                line_tx.clone(),
                exit_rx.clone(),
            ));
        }
    }
    drop(line_tx);
    tokio::spawn(forward_logs(line_rx, log));
    tokio::spawn(async move {
        loop {
            if !same_process(pid, token) {
                let _ = exit_tx.send(Some(Exit {
                    code: None,
                    signal: None,
                }));
                return;
            }
            if exit_tx.is_closed() {
                return;
            }
            tokio::time::sleep(ADOPT_POLL).await;
        }
    });
    Some(Arc::new(ProcHandle {
        spec: spec.clone(),
        pid: Some(pid),
        start_token: token,
        started: Instant::now(),
        adopted: true,
        tail,
        exit_rx,
        stop_requested: AtomicBool::new(false),
    }))
}

fn push_tail(tail: &Mutex<VecDeque<String>>, line: &str) {
    let mut t = tail.lock().unwrap_or_else(|e| e.into_inner());
    if t.len() >= TAIL_LINES {
        t.pop_front();
    }
    t.push_back(line.to_string());
}

fn clip(mut line: String) -> String {
    if line.len() > MAX_LINE {
        let mut cut = MAX_LINE;
        while !line.is_char_boundary(cut) {
            cut -= 1;
        }
        line.truncate(cut);
    }
    line
}

/// Fill the tail ring with the last lines before `end` (adoption: context for a later crash).
fn prefill_tail(path: &Path, end: u64, tail: &Mutex<VecDeque<String>>) {
    let Ok(mut f) = std::fs::File::open(path) else {
        return;
    };
    let from = end.saturating_sub(16 * 1024);
    if f.seek(SeekFrom::Start(from)).is_err() {
        return;
    }
    let mut buf = vec![];
    let _ = f.take(end - from).read_to_end(&mut buf);
    let text = String::from_utf8_lossy(&buf);
    let mut lines: Vec<&str> = text.lines().collect();
    if from > 0 && !lines.is_empty() {
        lines.remove(0); // partial first line
    }
    for l in lines.iter().rev().take(TAIL_LINES).rev() {
        push_tail(tail, &clip(l.to_string()));
    }
}

/// Follow an engine output file from `offset`: complete lines go to the tail ring and `tx`.
/// Handles truncation, copies + truncates the file past [`LOG_ROTATE_BYTES`], and ends after a
/// final read once the process exited.
async fn tail_file(
    path: PathBuf,
    mut offset: u64,
    stream: LogStream,
    tail: Arc<Mutex<VecDeque<String>>>,
    tx: mpsc::UnboundedSender<(LogStream, String)>,
    exit: watch::Receiver<Option<Exit>>,
) {
    let mut partial: Vec<u8> = vec![];
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let done = exit.borrow().is_some();
        if let Ok(mut f) = std::fs::File::open(&path) {
            let len = f.metadata().map(|m| m.len()).unwrap_or(0);
            if len < offset {
                offset = 0; // truncated (rotation, or by hand)
                partial.clear();
            }
            if f.seek(SeekFrom::Start(offset)).is_ok() {
                loop {
                    let n = match f.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => n,
                    };
                    offset += n as u64;
                    partial.extend_from_slice(&buf[..n]);
                    while let Some(i) = partial.iter().position(|b| *b == b'\n') {
                        let raw: Vec<u8> = partial.drain(..=i).collect();
                        let line = clip(
                            String::from_utf8_lossy(&raw[..raw.len() - 1])
                                .trim_end_matches('\r')
                                .to_string(),
                        );
                        push_tail(&tail, &line);
                        if tx.send((stream, line)).is_err() {
                            return;
                        }
                    }
                    if partial.len() > MAX_LINE {
                        let line = clip(String::from_utf8_lossy(&partial).into_owned());
                        partial.clear();
                        push_tail(&tail, &line);
                        let _ = tx.send((stream, line));
                    }
                }
            }
            if !done && offset >= LOG_ROTATE_BYTES && offset >= len {
                let _ = std::fs::copy(&path, path.with_extension("log.1"));
                if let Ok(f) = std::fs::OpenOptions::new().write(true).open(&path) {
                    let _ = f.set_len(0);
                }
                offset = 0;
            }
        }
        if done {
            if !partial.is_empty() {
                let line = clip(String::from_utf8_lossy(&partial).into_owned());
                push_tail(&tail, &line);
                let _ = tx.send((stream, line));
            }
            return;
        }
        let mut e = exit.clone();
        tokio::select! {
            _ = tokio::time::sleep(TAIL_POLL) => {}
            _ = e.changed() => {}
        }
    }
}

async fn read_lines<R: tokio::io::AsyncRead + Unpin>(
    r: R,
    stream: LogStream,
    tail: Arc<Mutex<VecDeque<String>>>,
    tx: mpsc::UnboundedSender<(LogStream, String)>,
) {
    let mut lines = BufReader::new(r).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let line = clip(line);
        push_tail(&tail, &line);
        if tx.send((stream, line)).is_err() {
            break;
        }
    }
}

pub fn guess_level(line: &str) -> LogLevel {
    let l = line.to_ascii_lowercase();
    if l.contains("error") || l.contains("panic") || l.contains("fatal") || l.starts_with("e ") {
        LogLevel::Error
    } else if l.contains("warn") || l.starts_with("w ") {
        LogLevel::Warn
    } else if l.contains("debug") {
        LogLevel::Debug
    } else {
        LogLevel::Info
    }
}

async fn forward_logs(
    mut rx: mpsc::UnboundedReceiver<(LogStream, String)>,
    target: Option<LogTarget>,
) {
    let Some(t) = target else {
        while rx.recv().await.is_some() {}
        return;
    };
    let mut pending: Vec<(LogStream, LogLine)> = vec![];
    let mut bytes = 0usize;
    let mut ticker = tokio::time::interval(t.flush);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    loop {
        tokio::select! {
            msg = rx.recv() => {
                match msg {
                    Some((stream, text)) => {
                        if t.forward.load(Ordering::Relaxed) {
                            bytes += text.len();
                            pending.push((stream, LogLine { ts: gpudeck_protocol::now(), level: Some(guess_level(&text)), stream: Some(stream), text }));
                            if pending.len() >= t.max_lines.clamp(1, 500) || bytes > 512 * 1024 {
                                flush(&t, &mut pending);
                                bytes = 0;
                            }
                        }
                    }
                    None => { flush(&t, &mut pending); return; }
                }
            }
            _ = ticker.tick() => { flush(&t, &mut pending); bytes = 0; }
        }
    }
}

fn flush(t: &LogTarget, pending: &mut Vec<(LogStream, LogLine)>) {
    if pending.is_empty() {
        return;
    }
    // One event per stream (the schema puts `stream` on the batch).
    for stream in [LogStream::Stdout, LogStream::Stderr] {
        let lines: Vec<LogLine> = pending
            .iter()
            .filter(|(s, _)| *s == stream)
            .map(|(_, l)| l.clone())
            .collect();
        if lines.is_empty() {
            continue;
        }
        let mut ev = Event::new(
            EventKind::Log,
            Severity::Info,
            format!("{} engine log lines", lines.len()),
        );
        ev.instance_id = t.instance_id;
        ev.engine_process_id = t.engine_process_id;
        ev.deployment_id = t.deployment_id;
        ev.log = Some(LogBatch {
            source: LogSource::Engine,
            stream: Some(stream),
            lines,
        });
        t.outbox.event(ev);
    }
    pending.clear();
}

/// First free loopback port in `[lo, hi]` not in `taken`.
pub fn free_port(range: [u16; 2], taken: &[u16]) -> Option<u16> {
    let (lo, hi) = (range[0].min(range[1]), range[0].max(range[1]));
    (lo..=hi).find(|p| !taken.contains(p) && port_free(*p))
}

pub fn port_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// True if something answers on `port` at any of the host's non-loopback addresses — an engine
/// that ignored the bind flags and listens on a network interface.
pub async fn exposed_on_network(port: u16, addrs: &[String]) -> Option<String> {
    for a in addrs {
        let Ok(ip) = a.parse::<IpAddr>() else {
            continue;
        };
        if ip.is_loopback() {
            continue;
        }
        let sa = SocketAddr::new(ip, port);
        if let Ok(Ok(_)) = tokio::time::timeout(
            Duration::from_millis(300),
            tokio::net::TcpStream::connect(sa),
        )
        .await
        {
            return Some(sa.to_string());
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    fn sh(script: &str) -> ProcSpec {
        ProcSpec {
            program: "/bin/sh".into(),
            args: vec!["-c".into(), script.into()],
            env: BTreeMap::new(),
            cwd: None,
            container: None,
        }
    }

    #[tokio::test]
    async fn spawn_capture_and_exit() {
        let outbox = Arc::new(Outbox::new(None, 100));
        outbox.set_connected(true);
        let target = LogTarget {
            outbox: outbox.clone(),
            instance_id: Some(Uuid::new_v4()),
            engine_process_id: None,
            deployment_id: None,
            forward: Arc::new(AtomicBool::new(true)),
            max_lines: 100,
            flush: Duration::from_millis(50),
            file: None,
        };
        let h = spawn(&sh("echo hello; echo oops error >&2; exit 3"), Some(target)).unwrap();
        let exit = h.wait().await;
        assert_eq!(exit.code, Some(3));
        tokio::time::sleep(Duration::from_millis(150)).await;
        let lines = h.last_lines();
        assert!(lines.contains(&"hello".to_string()) && lines.contains(&"oops error".to_string()));
        let mut n = 0;
        while !outbox.is_empty() {
            let env = outbox.next().await;
            if let gpudeck_protocol::Message::Event(e) = env.message {
                n += e.log.unwrap().lines.len();
            }
        }
        assert_eq!(n, 2);
    }

    #[tokio::test]
    async fn graceful_stop_kills_group() {
        // Parent ignores TERM; child sleeps. Group SIGKILL after grace must end both.
        let h = spawn(&sh("trap '' TERM; sleep 30 & wait"), None).unwrap();
        tokio::time::sleep(Duration::from_millis(200)).await;
        let started = Instant::now();
        let exit = h.stop(Duration::from_millis(300)).await;
        assert!(started.elapsed() < Duration::from_secs(5));
        assert!(exit.signal.is_some() || exit.code.is_some());
        assert!(h.stop_requested());
        let h2 = spawn(&sh("sleep 30"), None).unwrap();
        let exit = h2.stop(Duration::from_secs(5)).await;
        assert_eq!(exit.signal, Some(libc::SIGTERM));
        assert!(!pid_alive(h2.pid.unwrap()) || h2.exited().is_some());
    }

    #[test]
    fn ports() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let busy = l.local_addr().unwrap().port();
        assert!(!port_free(busy));
        let p = free_port([busy, busy.saturating_add(20)], &[busy + 1]).unwrap();
        assert!(p != busy && p != busy + 1);
    }

    #[test]
    fn levels() {
        assert_eq!(
            guess_level("srv  load_model: ERROR failed"),
            LogLevel::Error
        );
        assert_eq!(guess_level("W warning: x"), LogLevel::Warn);
        assert_eq!(guess_level("main: server is listening"), LogLevel::Info);
    }
}
