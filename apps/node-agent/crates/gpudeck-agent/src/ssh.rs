//! SSH GPU instances (`start_ssh_instance` / `stop_ssh_instance`, `protocol.md` §8): a container
//! running sshd with the requested GPUs attached and limits applied. The container publishes
//! sshd on 127.0.0.1 only; the agent's TCP forwarder on `<bind>:<ssh_port>` is the only network
//! listener (source CIDR filter, connection accounting for idle detection, closed first on stop).
//! State lives in `<state_dir>/ssh/<id>/` and running containers are re-adopted after an agent
//! restart. A monitor loop checks health every 15 s and enforces `ttl_seconds`.

use std::collections::HashMap;
use std::net::{IpAddr, SocketAddr};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use base64::Engine as _;
use gpudeck_protocol::{
    error_code, DeviceVendor, ErrorInfo, Event, EventKind, InstanceHealth, JsonObject, Severity,
    SshHostKey, SshInstanceResult, SshInstanceState, SshInstanceStatus, SshVolumeType,
    StartSshInstanceArgs, StateChange, StopSshInstanceArgs, Timestamp,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::io::AsyncReadExt;
use tokio::net::{TcpListener, TcpStream};
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};
use uuid::Uuid;

use crate::agent::Agent;
use crate::commands::Ctx;

/// Label put on every SSH-instance container / volume.
pub const LABEL: &str = "io.github.sur950.gpudeck.ssh_instance";
pub const LABEL_NODE: &str = "io.github.sur950.gpudeck.node_id";
/// Where the authorized_keys file is mounted inside the container.
pub const KEYS_MOUNT: &str = "/etc/gpudeck/authorized_keys";
const MONITOR_INTERVAL: Duration = Duration::from_secs(15);
const CAPS: &[&str] = &[
    "CHOWN",
    "SETUID",
    "SETGID",
    "DAC_OVERRIDE",
    "FOWNER",
    "FSETID",
    "SYS_CHROOT",
    "AUDIT_WRITE",
    "NET_BIND_SERVICE",
    "KILL",
];

fn err(code: &str, msg: impl Into<String>) -> ErrorInfo {
    ErrorInfo::new(code, msg)
}

pub fn container_name(id: Uuid) -> String {
    format!("gpudeck-ssh-{id}")
}

/// Connection accounting of one forwarder.
#[derive(Debug, Default)]
pub struct ForwardStats {
    pub active: AtomicU64,
    pub total: AtomicU64,
    pub last: Mutex<Option<Timestamp>>,
}

/// Persisted + runtime state of one SSH instance.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SshRecord {
    pub args: StartSshInstanceArgs,
    pub runtime: String,
    pub container_name: String,
    #[serde(default)]
    pub container_id: Option<String>,
    /// Loopback port docker published sshd on.
    pub local_port: u16,
    pub bind: String,
    pub host_address: String,
    pub host_port: u16,
    #[serde(default)]
    pub host_keys: Vec<SshHostKey>,
    #[serde(default)]
    pub gpu_attached: bool,
    #[serde(default)]
    pub warnings: Vec<String>,
    #[serde(default)]
    pub started_at: Option<Timestamp>,
    #[serde(default)]
    pub expires_at: Option<Timestamp>,
    /// Named volumes created for this instance (and whether to keep them).
    #[serde(default)]
    pub volumes: Vec<(String, bool)>,
}

pub struct SshInstance {
    pub id: Uuid,
    pub state: SshInstanceState,
    pub health: InstanceHealth,
    pub message: Option<String>,
    pub record: Option<SshRecord>,
    pub forwarder: Option<CancellationToken>,
    pub stats: Arc<ForwardStats>,
}

#[derive(Default)]
pub struct SshManager {
    pub instances: Mutex<HashMap<Uuid, SshInstance>>,
}

impl SshManager {
    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<Uuid, SshInstance>> {
        self.instances.lock().unwrap_or_else(|e| e.into_inner())
    }
}

// ------------------------------------------------------------------------------- helpers

/// OpenSSH fingerprint (`SHA256:<base64 without padding>`) of a base64 key blob.
pub fn fingerprint(blob_b64: &str) -> Option<String> {
    let raw = base64::engine::general_purpose::STANDARD
        .decode(blob_b64.trim())
        .ok()?;
    let digest = Sha256::digest(&raw);
    Some(format!(
        "SHA256:{}",
        base64::engine::general_purpose::STANDARD_NO_PAD.encode(digest)
    ))
}

fn is_key_type(t: &str) -> bool {
    t.starts_with("ssh-") || t.starts_with("ecdsa-") || t.starts_with("sk-")
}

/// Host keys from `ssh-keyscan` output or `*.pub` files (`[host]:port type b64`, `type b64 comment`).
pub fn parse_host_keys(text: &str) -> Vec<SshHostKey> {
    let mut out: Vec<SshHostKey> = vec![];
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let toks: Vec<&str> = line.split_whitespace().collect();
        let Some(i) = toks.iter().position(|t| is_key_type(t)) else {
            continue;
        };
        let Some(b64) = toks.get(i + 1) else { continue };
        let Some(fp) = fingerprint(b64) else { continue };
        if out.iter().any(|k| k.fingerprint == fp) {
            continue;
        }
        out.push(SshHostKey {
            key_type: toks[i].to_string(),
            fingerprint: fp,
            public_key: Some(format!("{} {}", toks[i], b64)),
        });
    }
    // Preferred first: ed25519, ecdsa, rsa.
    out.sort_by_key(|k| {
        if k.key_type == "ssh-ed25519" {
            0
        } else if k.key_type.starts_with("ecdsa") {
            1
        } else {
            2
        }
    });
    out
}

/// Validate an OpenSSH public key line (`type base64 [comment]`).
pub fn valid_public_key(line: &str) -> bool {
    let toks: Vec<&str> = line.split_whitespace().collect();
    // Options prefix (`from="..." ssh-ed25519 ...`) is allowed.
    match toks.iter().position(|t| is_key_type(t)) {
        Some(i) => {
            toks.get(i + 1).is_some_and(|b| fingerprint(b).is_some()) && !line.contains('\n')
        }
        None => false,
    }
}

/// Fail closed: an empty list allows nobody (an instance without source CIDRs is unreachable
/// until CIDRs are set, `protocol.md` §8).
pub fn cidr_allows(cidrs: &[ipnet::IpNet], ip: IpAddr) -> bool {
    crate::security::ip_in(cidrs, ip)
}

/// Union of the source CIDRs of every persisted SSH instance (firewall inputs for the CLI).
pub fn persisted_cidrs(paths: &crate::state::StatePaths) -> Vec<String> {
    let Ok(rd) = std::fs::read_dir(paths.root.join("ssh")) else {
        return vec![];
    };
    let mut out: Vec<String> = rd
        .flatten()
        .filter_map(|e| {
            crate::state::read_json::<SshRecord>(&e.path().join("instance.json"))
                .ok()
                .flatten()
        })
        .flat_map(|r| r.args.allowed_source_cidrs)
        .collect();
    out.sort();
    out.dedup();
    out
}

/// Shared inputs of the forwarders: the policy's default CIDRs (used by instances that have
/// none of their own) and the rejected-attempt monitor.
#[derive(Clone)]
pub struct ForwardGuard {
    pub defaults: Arc<std::sync::RwLock<Vec<ipnet::IpNet>>>,
    pub monitor: Arc<crate::ingress::IngressMonitor>,
}

impl ForwardGuard {
    fn allows(&self, own: &[ipnet::IpNet], ip: IpAddr) -> bool {
        if !own.is_empty() {
            return cidr_allows(own, ip);
        }
        cidr_allows(&self.defaults.read().unwrap_or_else(|e| e.into_inner()), ip)
    }
}

pub fn parse_cidrs(v: &[String]) -> Result<Vec<ipnet::IpNet>, ErrorInfo> {
    v.iter()
        .map(|c| {
            let c = c.trim();
            c.parse::<ipnet::IpNet>()
                .or_else(|_| c.parse::<IpAddr>().map(ipnet::IpNet::from))
                .map_err(|_| err(error_code::INVALID_ARGS, format!("invalid CIDR {c:?}")))
        })
        .collect()
}

fn valid_user(u: &str) -> bool {
    let mut cs = u.chars();
    matches!(cs.next(), Some(c) if c.is_ascii_lowercase() || c == '_')
        && u.len() <= 32
        && cs.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
}

/// Variables the agent manages on SSH instance containers: keys-only login (no password, no sudo),
/// the user and its keys, GPU visibility and slice limits. User `env` never overrides them.
pub fn reserved_env_key(k: &str) -> bool {
    const KEYS: &[&str] = &[
        "PASSWORD_ACCESS",
        "USER_PASSWORD",
        "USER_PASSWORD_FILE",
        "SUDO_ACCESS",
        "USER_NAME",
        "PUBLIC_KEY",
        "PUBLIC_KEY_FILE",
        "PUBLIC_KEY_DIR",
        "PUBLIC_KEY_URL",
        "NVIDIA_DRIVER_CAPABILITIES",
    ];
    let u = k.to_ascii_uppercase();
    KEYS.contains(&u.as_str())
        || u.starts_with("GPUDECK_")
        || u.starts_with("CUDA_MPS_")
        || u.ends_with("_VISIBLE_DEVICES")
}

fn valid_volume_name(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '_' | '.' | '-'))
}

/// Run a container CLI command; stdout (trimmed) or an error with stderr.
async fn cli(runtime: &str, args: &[String], timeout: Duration) -> Result<String, String> {
    let fut = tokio::process::Command::new(runtime)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .output();
    match tokio::time::timeout(timeout, fut).await {
        Err(_) => Err(format!(
            "{runtime} {} timed out after {}s",
            args.first().map(String::as_str).unwrap_or(""),
            timeout.as_secs()
        )),
        Ok(Err(e)) => Err(format!("{runtime}: {e}")),
        Ok(Ok(o)) if o.status.success() => {
            Ok(String::from_utf8_lossy(&o.stdout).trim().to_string())
        }
        Ok(Ok(o)) => Err(String::from_utf8_lossy(&o.stderr)
            .trim()
            .chars()
            .take(600)
            .collect()),
    }
}

fn s(v: &str) -> String {
    v.to_string()
}

/// (`docker run` argv, gpu_attached, warnings, named volumes (name, keep)).
pub type RunArgs = (Vec<String>, bool, Vec<String>, Vec<(String, bool)>);

/// `docker run` argv (without the runtime) for an SSH instance. Pure, for tests.
#[allow(clippy::too_many_arguments)]
pub fn run_args(
    a: &StartSshInstanceArgs,
    node_id: Uuid,
    keys_file: &Path,
    keys: &str,
    cache_dir: &Path,
    device_cores: &HashMap<u32, i64>,
    mps_dir: Option<&Path>,
    os_linux: bool,
) -> RunArgs {
    let mut warnings = vec![];
    let id = a.ssh_instance_id;
    let name = container_name(id);
    let mut r = vec![
        s("run"),
        s("-d"),
        s("--name"),
        name.clone(),
        s("--hostname"),
        hostname_for(a),
    ];
    r.extend([
        s("--label"),
        format!("{LABEL}={id}"),
        s("--label"),
        format!("{LABEL_NODE}={node_id}"),
        s("--label"),
        s("gpudeck.managed=true"),
    ]);
    for (k, v) in &a.labels {
        r.extend([s("--label"), format!("gpudeck.label.{k}={v}")]);
    }
    r.extend([
        s("--restart"),
        s("no"),
        s("-p"),
        format!("127.0.0.1::{}", a.container_port),
    ]);
    r.extend([s("--cap-drop"), s("ALL")]);
    for c in CAPS {
        r.extend([s("--cap-add"), s(c)]);
    }
    r.extend([s("--security-opt"), s("no-new-privileges")]);
    // Limits.
    let l = &a.limits;
    if let Some(c) = l.cpu_cores {
        r.extend([s("--cpus"), format!("{c}")]);
    }
    if let Some(m) = l.memory_bytes {
        r.extend([
            s("--memory"),
            format!("{m}b"),
            s("--memory-swap"),
            format!("{m}b"),
        ]);
    }
    r.extend([
        s("--shm-size"),
        format!("{}b", l.shm_bytes.unwrap_or(1 << 30)),
    ]);
    r.extend([s("--pids-limit"), l.pids.unwrap_or(4096).to_string()]);
    // GPUs.
    let mut env: Vec<(String, String)> = vec![];
    let idx: Vec<String> = a.devices.iter().map(|d| d.index.to_string()).collect();
    let vendor = a.devices.first().map(|d| d.vendor);
    let mut gpu_attached = false;
    match vendor {
        Some(DeviceVendor::Nvidia) | Some(DeviceVendor::Thead) => {
            r.extend([s("--gpus"), format!("\"device={}\"", idx.join(","))]);
            env.push((s("NVIDIA_DRIVER_CAPABILITIES"), s("compute,utility")));
            gpu_attached = true;
        }
        Some(DeviceVendor::Amd) | Some(DeviceVendor::Hygon) => {
            r.extend([s("--device"), s("/dev/kfd")]);
            for d in &a.devices {
                r.extend([s("--device"), format!("/dev/dri/renderD{}", 128 + d.index)]);
            }
            r.extend([s("--group-add"), s("video")]);
            gpu_attached = true;
        }
        Some(DeviceVendor::Metax) => {
            r.extend([s("--device"), s("/dev/mxcd"), s("--device"), s("/dev/dri")]);
            env.push((s("MACA_VISIBLE_DEVICES"), idx.join(",")));
            gpu_attached = true;
            warnings.push(s(
                "MetaX passthrough uses /dev/mxcd + /dev/dri (fixture-verified only)",
            ));
        }
        Some(DeviceVendor::Moorethreads) => {
            r.extend([s("--device"), s("/dev/dri")]);
            env.push((s("MUSA_VISIBLE_DEVICES"), idx.join(",")));
            gpu_attached = true;
            warnings.push(s("Moore Threads passthrough uses /dev/dri + MUSA_VISIBLE_DEVICES (fixture-verified only)"));
        }
        Some(DeviceVendor::Apple) => warnings.push(s(
            "Apple GPUs cannot be passed into containers; the instance runs CPU-only",
        )),
        None => {}
    }
    if !os_linux && gpu_attached {
        gpu_attached = false;
        warnings.push(s(
            "GPU passthrough needs a Linux host; the instance runs without GPUs",
        ));
    }
    // VRAM / compute limits per device: slice wins, else limits.
    let per: Vec<(u32, Option<u64>, Option<u32>)> = a
        .devices
        .iter()
        .enumerate()
        .map(|(i, d)| {
            (
                i as u32,
                d.slice.as_ref().map(|x| x.vram_bytes).or(l.vram_bytes),
                d.slice
                    .as_ref()
                    .map(|x| x.compute_pct as u32)
                    .or(l.compute_pct),
            )
        })
        .collect();
    let vram: Vec<String> = per
        .iter()
        .filter_map(|(i, v, _)| v.map(|v| format!("{i}={}M", (v / (1 << 20)).max(1))))
        .collect();
    let compute = per.iter().filter_map(|(_, _, c)| *c).min();
    if !vram.is_empty() {
        env.push((
            s("GPUDECK_VRAM_LIMIT_BYTES"),
            per.iter()
                .filter_map(|(_, v, _)| *v)
                .min()
                .unwrap_or(0)
                .to_string(),
        ));
    }
    if let Some(c) = compute {
        env.push((s("GPUDECK_COMPUTE_PCT"), c.to_string()));
    }
    if matches!(vendor, Some(DeviceVendor::Nvidia)) && (!vram.is_empty() || compute.is_some()) {
        if !vram.is_empty() {
            env.push((s("CUDA_MPS_PINNED_DEVICE_MEM_LIMIT"), vram.join(",")));
        }
        if let Some(c) = compute {
            env.push((s("CUDA_MPS_ACTIVE_THREAD_PERCENTAGE"), c.to_string()));
        }
        match mps_dir {
            Some(d) => {
                r.extend([
                    s("-v"),
                    format!("{}:{}", d.display(), d.display()),
                    s("--ipc"),
                    s("host"),
                ]);
                env.push((s("CUDA_MPS_PIPE_DIRECTORY"), d.display().to_string()));
            }
            None => warnings.push(s(
                "VRAM/compute limits are advisory: the CUDA MPS daemon is not running on this node",
            )),
        }
    }
    if matches!(vendor, Some(DeviceVendor::Amd) | Some(DeviceVendor::Hygon)) {
        if let (Some(c), Some(d)) = (compute, a.devices.first()) {
            if let Some(cores) = device_cores.get(&d.index) {
                env.push((
                    s("GPUDECK_DEVICE_CORES"),
                    ((cores * c as i64) / 100).max(1).to_string(),
                ));
            }
        }
        if !vram.is_empty() {
            warnings.push(s("AMD VRAM limits are advisory (GPUDECK_VRAM_LIMIT_BYTES)"));
        }
    }
    // Keys and user.
    r.extend([s("-v"), format!("{}:{KEYS_MOUNT}:ro", keys_file.display())]);
    env.push((s("PUBLIC_KEY"), keys.to_string()));
    env.push((s("PUBLIC_KEY_FILE"), s(KEYS_MOUNT)));
    env.push((s("USER_NAME"), a.ssh_user.clone()));
    env.push((s("PASSWORD_ACCESS"), s("false")));
    env.push((s("SUDO_ACCESS"), s("false")));
    env.push((s("GPUDECK_SSH_USER"), a.ssh_user.clone()));
    env.push((s("GPUDECK_AUTHORIZED_KEYS_FILE"), s(KEYS_MOUNT)));
    env.push((s("GPUDECK_SSH_INSTANCE_ID"), id.to_string()));
    for (k, v) in &a.env {
        if reserved_env_key(k) {
            warnings.push(format!(
                "environment variable {k} is managed by GPU Deck and was ignored"
            ));
            continue;
        }
        env.retain(|(ek, _)| ek != k);
        env.push((k.clone(), v.clone()));
    }
    for (k, v) in env {
        r.extend([s("-e"), format!("{k}={v}")]);
    }
    // Volumes (validated before).
    let mut named = vec![];
    for v in &a.volumes {
        match v.kind {
            SshVolumeType::Volume => {
                let vname = format!("{name}-{}", v.source.as_deref().unwrap_or("data"));
                let ro = if v.read_only { ":ro" } else { "" };
                r.extend([
                    s("--mount"),
                    format!(
                        "type=volume,source={vname},target={}{}",
                        v.target,
                        if ro.is_empty() { "" } else { ",readonly" }
                    ),
                ]);
                named.push((vname, v.keep));
            }
            SshVolumeType::Bind => {
                let src = v.source.clone().unwrap_or_default();
                let force_ro = Path::new(&src).starts_with(cache_dir);
                let ro = if v.read_only || force_ro {
                    ",readonly"
                } else {
                    ""
                };
                r.extend([
                    s("--mount"),
                    format!("type=bind,source={src},target={}{ro}", v.target),
                ]);
            }
            SshVolumeType::Tmpfs => {
                let size = v
                    .size_bytes
                    .map(|b| format!(",tmpfs-size={b}"))
                    .unwrap_or_default();
                r.extend([
                    s("--mount"),
                    format!("type=tmpfs,target={}{size}", v.target),
                ]);
            }
        }
    }
    r.push(a.image.clone());
    (r, gpu_attached, warnings, named)
}

fn hostname_for(a: &StartSshInstanceArgs) -> String {
    let base: String = a
        .name
        .as_deref()
        .unwrap_or("gpudeck")
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' {
                c.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect();
    let base = base.trim_matches('-');
    let short = &a.ssh_instance_id.simple().to_string()[..8];
    let mut h = format!("{}-{short}", if base.is_empty() { "gpudeck" } else { base });
    h.truncate(63);
    h
}

/// Wait until something on `addr` sends an SSH banner.
async fn wait_banner(
    addr: SocketAddr,
    timeout: Duration,
    cancel: &CancellationToken,
    alive: impl Fn() -> futures::future::BoxFuture<'static, bool>,
) -> Result<(), ErrorInfo> {
    let started = Instant::now();
    let mut checks = 0u32;
    loop {
        if cancel.is_cancelled() {
            return Err(err(error_code::CANCELLED, "cancelled"));
        }
        if let Ok(Ok(mut st)) =
            tokio::time::timeout(Duration::from_secs(2), TcpStream::connect(addr)).await
        {
            let mut buf = [0u8; 64];
            if let Ok(Ok(n)) = tokio::time::timeout(Duration::from_secs(3), st.read(&mut buf)).await
            {
                if n >= 4 && &buf[..4] == b"SSH-" {
                    return Ok(());
                }
            }
        }
        checks += 1;
        if checks.is_multiple_of(5) && !alive().await {
            return Err(err(
                error_code::ENGINE_START_FAILED,
                "ssh container exited during startup",
            ));
        }
        if started.elapsed() > timeout {
            return Err(err(
                error_code::HEALTH_TIMEOUT,
                format!("no SSH banner on {addr} after {}s", timeout.as_secs()),
            ));
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}

/// Accept on `listener`, filter by CIDR (the instance's own, else the policy default; none =
/// nobody), splice to `target`, count connections. Refused clients get a reset and are counted
/// in the ingress monitor.
pub async fn forward(
    listener: TcpListener,
    target: SocketAddr,
    cidrs: Vec<ipnet::IpNet>,
    guard: ForwardGuard,
    stats: Arc<ForwardStats>,
    cancel: CancellationToken,
) {
    loop {
        let (mut client, peer) = tokio::select! {
            r = listener.accept() => match r { Ok(x) => x, Err(e) => { warn!("ssh forwarder accept: {e}"); tokio::time::sleep(Duration::from_millis(200)).await; continue; } },
            _ = cancel.cancelled() => return,
        };
        if !guard.allows(&cidrs, peer.ip()) {
            debug!(%peer, "ssh forwarder: client not in the allowed source CIDRs; reset");
            guard
                .monitor
                .record("ssh_instance", "source_not_allowed", peer.ip());
            let _ = client.set_zero_linger();
            drop(client);
            continue;
        }
        let stats = stats.clone();
        let cancel = cancel.clone();
        tokio::spawn(async move {
            let mut upstream = match TcpStream::connect(target).await {
                Ok(u) => u,
                Err(e) => {
                    warn!("ssh forwarder: connect {target}: {e}");
                    return;
                }
            };
            let _ = client.set_nodelay(true);
            let _ = upstream.set_nodelay(true);
            stats.active.fetch_add(1, Ordering::SeqCst);
            stats.total.fetch_add(1, Ordering::SeqCst);
            *stats.last.lock().unwrap_or_else(|e| e.into_inner()) = Some(gpudeck_protocol::now());
            tokio::select! {
                _ = tokio::io::copy_bidirectional(&mut client, &mut upstream) => {}
                _ = cancel.cancelled() => {}
            }
            stats.active.fetch_sub(1, Ordering::SeqCst);
            *stats.last.lock().unwrap_or_else(|e| e.into_inner()) = Some(gpudeck_protocol::now());
        });
    }
}

fn is_unspecified(bind: &str) -> bool {
    bind.parse::<IpAddr>()
        .map(|ip| ip.is_unspecified())
        .unwrap_or(false)
}

// ------------------------------------------------------------------------------- agent glue

impl Agent {
    fn ssh_dir(&self, id: Uuid) -> PathBuf {
        self.paths.root.join("ssh").join(id.to_string())
    }

    fn ssh_runtime(&self) -> Result<String, ErrorInfo> {
        self.container_runtime.clone().ok_or_else(|| {
            err(
                error_code::NO_CONTAINER_RUNTIME,
                "no container runtime (docker/podman/nerdctl) found on this node",
            )
        })
    }

    /// Forwarder listen address: args → `ssh_bind` → `proxy_address` (an IP of this host) →
    /// the first private address → loopback. A public or every-interface address is refused
    /// unless the node's own `ssh_bind` names it (local opt-in, never the control plane's).
    fn ssh_bind_address(&self, requested: Option<&str>) -> Result<String, ErrorInfo> {
        let local = crate::system::ip_addresses();
        let chosen = requested
            .map(str::to_string)
            .or_else(|| self.cfg.ssh_bind.clone())
            .or_else(|| {
                self.cfg
                    .proxy_address
                    .clone()
                    .filter(|a| local.iter().any(|l| l == a))
            })
            .or_else(|| crate::config::private_ip(&local))
            .unwrap_or_else(|| "127.0.0.1".into());
        let ip: IpAddr = chosen.parse().map_err(|_| {
            err(
                error_code::INVALID_ARGS,
                format!("bind_address {chosen:?} is not an IP address"),
            )
        })?;
        let exposed = ip.is_unspecified() || crate::agent::is_public_ip(ip);
        if exposed && self.cfg.ssh_bind.as_deref() != Some(chosen.as_str()) {
            return Err(err(
                error_code::INVALID_ARGS,
                format!("refusing to expose an SSH instance on {chosen}: set ssh_bind = \"{chosen}\" in agent.toml to allow it"),
            ));
        }
        Ok(chosen)
    }

    /// Source CIDRs of every live SSH instance plus the policy default (firewall inputs).
    pub fn ssh_source_cidrs(&self) -> Vec<String> {
        let mut v: Vec<String> = self
            .ssh
            .lock()
            .values()
            .filter_map(|i| i.record.as_ref())
            .flat_map(|r| r.args.allowed_source_cidrs.clone())
            .collect();
        if let Some(p) = self.policy() {
            if let Some(s) = p.ssh {
                v.extend(s.default_allowed_cidrs);
            }
        }
        v.sort();
        v.dedup();
        v
    }

    fn forward_guard(&self) -> ForwardGuard {
        ForwardGuard {
            defaults: self.ssh_defaults.clone(),
            monitor: self.ingress.clone(),
        }
    }

    /// Address users connect to.
    fn ssh_public_address(&self, bind: &str) -> String {
        if !is_unspecified(bind) {
            return bind.to_string();
        }
        self.cfg
            .proxy_address
            .clone()
            .or_else(|| {
                crate::system::ip_addresses().into_iter().find(|a| {
                    a.parse::<IpAddr>()
                        .map(|ip| ip.is_ipv4() && !ip.is_loopback())
                        .unwrap_or(false)
                })
            })
            .unwrap_or_else(|| "127.0.0.1".into())
    }

    fn ssh_ports_taken(&self) -> Vec<u16> {
        self.ssh
            .lock()
            .values()
            .filter_map(|i| i.record.as_ref().map(|r| r.host_port))
            .collect()
    }

    async fn ssh_listen(&self, bind: &str, requested: u16) -> Result<TcpListener, ErrorInfo> {
        let ip: IpAddr = bind.parse().map_err(|_| {
            err(
                error_code::INVALID_ARGS,
                format!("bind_address {bind:?} is not an IP address"),
            )
        })?;
        if requested != 0 {
            return TcpListener::bind((ip, requested)).await.map_err(|e| {
                err(
                    error_code::PORT_IN_USE,
                    format!("ssh port {requested}: {e}"),
                )
                .retryable()
            });
        }
        let [lo, hi] = self.cfg.ssh_port_range;
        let taken = self.ssh_ports_taken();
        for p in lo.min(hi)..=lo.max(hi) {
            // Never take a port the operator's sshd uses (the firewall keeps those open).
            if taken.contains(&p) || self.cfg.firewall_preserve_ports.contains(&p) {
                continue;
            }
            if let Ok(l) = TcpListener::bind((ip, p)).await {
                return Ok(l);
            }
        }
        Err(err(
            error_code::PORT_IN_USE,
            format!("no free port in ssh_port_range {lo}-{hi}"),
        )
        .retryable())
    }

    fn ssh_set_state(
        &self,
        id: Uuid,
        to: SshInstanceState,
        health: InstanceHealth,
        reason: Option<String>,
    ) {
        let (from, status) = {
            let mut g = self.ssh.lock();
            let Some(i) = g.get_mut(&id) else { return };
            let from = i.state;
            i.state = to;
            i.health = health;
            if reason.is_some() {
                i.message = reason.clone();
            }
            (from, status_of(i))
        };
        if from != to {
            let sev = match to {
                SshInstanceState::Error => Severity::Warning,
                _ => Severity::Info,
            };
            let mut ev = Event::new(
                EventKind::SshInstanceStateChanged,
                sev,
                format!("ssh instance {id}: {from} -> {to}"),
            );
            ev.ssh_instance_id = Some(id);
            ev.state = Some(StateChange {
                from: Some(from.to_string()),
                to: to.to_string(),
                reason,
            });
            ev.details = serde_json::to_value(&status)
                .ok()
                .and_then(|v| v.as_object().cloned());
            self.event(ev);
            self.inventory_dirty.notify_one();
        }
    }

    pub fn ssh_statuses(&self) -> Vec<SshInstanceStatus> {
        let mut v: Vec<SshInstanceStatus> = self.ssh.lock().values().map(status_of).collect();
        v.sort_by_key(|s| s.ssh_instance_id);
        v
    }

    fn validate_ssh(&self, a: &StartSshInstanceArgs) -> Result<(), ErrorInfo> {
        if a.image.trim().is_empty() {
            return Err(err(error_code::INVALID_ARGS, "image is empty"));
        }
        if a.image.starts_with('-') || a.image.chars().any(char::is_whitespace) {
            // The image is the last `docker run` argument: never let it parse as an option.
            return Err(err(
                error_code::INVALID_ARGS,
                format!("invalid image reference {:?}", a.image),
            ));
        }
        if !valid_user(&a.ssh_user) || a.ssh_user == "root" {
            return Err(err(
                error_code::INVALID_ARGS,
                format!("invalid ssh_user {:?}", a.ssh_user),
            ));
        }
        if a.authorized_keys.is_empty() || !a.authorized_keys.iter().all(|k| valid_public_key(k)) {
            return Err(err(
                error_code::INVALID_ARGS,
                "authorized_keys must be OpenSSH public key lines",
            ));
        }
        parse_cidrs(&a.allowed_source_cidrs)?;
        if let Some(b) = &a.bind_address {
            b.parse::<IpAddr>().map_err(|_| {
                err(
                    error_code::INVALID_ARGS,
                    format!("bind_address {b:?} is not an IP"),
                )
            })?;
        }
        if let Some(v) = a
            .devices
            .iter()
            .map(|d| d.vendor)
            .find(|v| Some(*v) != a.devices.first().map(|d| d.vendor))
        {
            return Err(err(
                error_code::INVALID_ARGS,
                format!("mixed device vendors ({v})"),
            ));
        }
        let cache = self.cache.dir.clone();
        let roots: Vec<PathBuf> = std::iter::once(cache)
            .chain(
                self.cfg
                    .ssh_bind_roots
                    .iter()
                    .map(|r| crate::template::expand_home(r)),
            )
            .collect();
        for v in &a.volumes {
            if !v.target.starts_with('/') || v.target.contains(',') {
                return Err(err(
                    error_code::INVALID_ARGS,
                    format!("volume target {:?} must be an absolute path", v.target),
                ));
            }
            match v.kind {
                SshVolumeType::Volume => {
                    if !valid_volume_name(v.source.as_deref().unwrap_or("data")) {
                        return Err(err(
                            error_code::INVALID_ARGS,
                            format!("volume source {:?} must match [a-z0-9_.-]", v.source),
                        ));
                    }
                }
                SshVolumeType::Bind => {
                    let src = v
                        .source
                        .as_deref()
                        .ok_or_else(|| err(error_code::INVALID_ARGS, "bind volume needs source"))?;
                    let p = Path::new(src);
                    if !p.is_absolute()
                        || src.contains(',')
                        || p.components()
                            .any(|c| matches!(c, std::path::Component::ParentDir))
                    {
                        return Err(err(
                            error_code::INVALID_ARGS,
                            format!("bind source {src:?} must be an absolute path"),
                        ));
                    }
                    if !roots.iter().any(|r| p.starts_with(r)) {
                        return Err(err(
                            error_code::INVALID_ARGS,
                            format!(
                                "bind source {src} is outside the cache dir and ssh_bind_roots"
                            ),
                        ));
                    }
                }
                SshVolumeType::Tmpfs => {}
            }
        }
        Ok(())
    }

    fn ssh_result(&self, id: Uuid) -> Value {
        let g = self.ssh.lock();
        let Some(r) = g.get(&id).and_then(|i| i.record.clone()) else {
            return json!({"ssh_instance_id": id});
        };
        let fp = r
            .host_keys
            .first()
            .map(|k| k.fingerprint.clone())
            .unwrap_or_default();
        let res = SshInstanceResult {
            ssh_instance_id: id,
            container_id: r.container_id.clone(),
            host_address: r.host_address.clone(),
            host_port: r.host_port,
            ssh_user: r.args.ssh_user.clone(),
            host_key_fingerprint: fp,
            host_keys: r.host_keys.clone(),
            ssh_command: Some(format!(
                "ssh -p {} {}@{}",
                r.host_port, r.args.ssh_user, r.host_address
            )),
            device_ids: r.args.devices.iter().map(|d| d.device_id).collect(),
            gpu_attached: r.gpu_attached,
            started_at: r.started_at,
            expires_at: r.expires_at,
            warnings: r.warnings.clone(),
        };
        serde_json::to_value(res).unwrap_or_else(|_| json!({"ssh_instance_id": id}))
    }

    pub async fn start_ssh_instance(
        self: &Arc<Self>,
        ctx: &Ctx,
        a: StartSshInstanceArgs,
    ) -> Result<Value, ErrorInfo> {
        let id = a.ssh_instance_id;
        let runtime = self.ssh_runtime()?;
        self.validate_ssh(&a)?;
        self.learn_device_ids(&a.devices);
        // Idempotent: same spec running → report it; anything else → replace.
        let existing = self.ssh.lock().get(&id).map(|i| {
            (
                i.state,
                i.record.as_ref().map(|r| r.args == a).unwrap_or(false),
            )
        });
        if let Some((state, same)) = existing {
            if state == SshInstanceState::Running && same {
                return Ok(self.ssh_result(id));
            }
            let _ = self
                .stop_ssh_instance(
                    &Ctx::internal(),
                    StopSshInstanceArgs {
                        ssh_instance_id: id,
                        force: true,
                        kill_grace_seconds: 5,
                        remove_volumes: false,
                    },
                )
                .await;
        }
        self.ssh.lock().insert(
            id,
            SshInstance {
                id,
                state: SshInstanceState::Stopped,
                health: InstanceHealth::Unknown,
                message: None,
                record: None,
                forwarder: None,
                stats: Arc::new(ForwardStats::default()),
            },
        );
        self.ssh_set_state(
            id,
            SshInstanceState::Starting,
            InstanceHealth::Unknown,
            Some("start_ssh_instance".into()),
        );
        match self.start_ssh_inner(ctx, &a, &runtime).await {
            Ok(()) => {
                self.ssh_set_state(
                    id,
                    SshInstanceState::Running,
                    InstanceHealth::Healthy,
                    Some("sshd ready".into()),
                );
                self.ssh_persist(id);
                Ok(self.ssh_result(id))
            }
            Err(e) => {
                warn!(ssh_instance = %id, "start failed: {e}");
                let name = container_name(id);
                let _ = cli(&runtime, &[s("rm"), s("-f"), name], Duration::from_secs(30)).await;
                if let Some(t) = self
                    .ssh
                    .lock()
                    .get_mut(&id)
                    .and_then(|i| i.forwarder.take())
                {
                    t.cancel();
                }
                self.ssh_set_state(
                    id,
                    SshInstanceState::Error,
                    InstanceHealth::Unhealthy,
                    Some(e.message.clone()),
                );
                Err(e)
            }
        }
    }

    async fn start_ssh_inner(
        self: &Arc<Self>,
        ctx: &Ctx,
        a: &StartSshInstanceArgs,
        runtime: &str,
    ) -> Result<(), ErrorInfo> {
        let id = a.ssh_instance_id;
        let name = container_name(id);
        let deadline = Duration::from_secs(a.health_timeout_seconds.max(5));
        let started = Instant::now();
        let bind = self.ssh_bind_address(a.bind_address.as_deref())?;
        let listener = self.ssh_listen(&bind, a.ssh_port).await?;
        let host_port = listener
            .local_addr()
            .map(|x| x.port())
            .unwrap_or(a.ssh_port);
        // authorized_keys (0644: sshd inside reads it as another uid).
        let dir = self.ssh_dir(id);
        std::fs::create_dir_all(&dir).map_err(|e| {
            err(
                error_code::INTERNAL,
                format!("create {}: {e}", dir.display()),
            )
        })?;
        let keys = a
            .authorized_keys
            .iter()
            .map(|k| k.trim())
            .collect::<Vec<_>>()
            .join("\n");
        let keys_file = dir.join("authorized_keys");
        crate::state::write_private(&keys_file, format!("{keys}\n").as_bytes(), 0o644).map_err(
            |e| {
                err(
                    error_code::INTERNAL,
                    format!("write authorized_keys: {e:#}"),
                )
            },
        )?;
        // Image.
        ctx.progress(Some(10.0), format!("ensuring image {}", a.image));
        if cli(
            runtime,
            &[s("image"), s("inspect"), a.image.clone()],
            Duration::from_secs(30),
        )
        .await
        .is_err()
        {
            ctx.progress(Some(15.0), format!("pulling {}", a.image));
            let left = deadline
                .saturating_sub(started.elapsed())
                .max(Duration::from_secs(30));
            cli(runtime, &[s("pull"), a.image.clone()], left)
                .await
                .map_err(|e| {
                    err(
                        error_code::IMAGE_PULL_FAILED,
                        format!("pull {}: {e}", a.image),
                    )
                    .retryable()
                })?;
        }
        if ctx.cancel.is_cancelled() {
            return Err(err(error_code::CANCELLED, "cancelled"));
        }
        let _ = cli(
            runtime,
            &[s("rm"), s("-f"), name.clone()],
            Duration::from_secs(30),
        )
        .await;
        let cores: HashMap<u32, i64> = self
            .last_devices
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .filter_map(|d| d.core_count.map(|c| (d.index, c)))
            .collect();
        let mps = Path::new("/tmp/nvidia-mps");
        let mps = (cfg!(target_os = "linux") && mps.join("control").exists()).then_some(mps);
        let (args, gpu_attached, mut warnings, volumes) = run_args(
            a,
            self.node_id,
            &keys_file,
            &keys,
            &self.cache.dir,
            &cores,
            mps,
            cfg!(target_os = "linux"),
        );
        if a.allowed_source_cidrs.is_empty()
            && self
                .ssh_defaults
                .read()
                .unwrap_or_else(|e| e.into_inner())
                .is_empty()
        {
            warnings.push("no allowed_source_cidrs: the instance accepts no connections until source CIDRs are set (per instance, or the cluster default)".into());
        }
        ctx.progress(Some(40.0), "starting container");
        let cid = cli(runtime, &args, Duration::from_secs(120))
            .await
            .map_err(|e| {
                err(
                    error_code::ENGINE_START_FAILED,
                    format!("{runtime} run: {e}"),
                )
            })?;
        let port_out = cli(
            runtime,
            &[s("port"), name.clone(), format!("{}/tcp", a.container_port)],
            Duration::from_secs(20),
        )
        .await
        .map_err(|e| {
            err(
                error_code::ENGINE_START_FAILED,
                format!("{runtime} port: {e}"),
            )
        })?;
        let local_port: u16 = port_out
            .lines()
            .filter_map(|l| {
                l.trim().rsplit_once(':').and_then(|(h, p)| {
                    (h.contains("127.0.0.1") || h == "localhost")
                        .then(|| p.parse().ok())
                        .flatten()
                })
            })
            .next()
            .ok_or_else(|| {
                err(
                    error_code::ENGINE_START_FAILED,
                    format!("cannot read published port: {port_out:?}"),
                )
            })?;
        let now = gpudeck_protocol::now();
        let record = SshRecord {
            args: a.clone(),
            runtime: runtime.to_string(),
            container_name: name.clone(),
            container_id: Some(cid.chars().take(64).collect()),
            local_port,
            bind: bind.clone(),
            host_address: self.ssh_public_address(&bind),
            host_port,
            host_keys: vec![],
            gpu_attached,
            warnings,
            started_at: Some(now),
            expires_at: a
                .ttl_seconds
                .map(|t| now + chrono::Duration::seconds(t as i64)),
            volumes,
        };
        // Forwarder first so the banner wait also proves the public path works.
        let token = self.shutdown.child_token();
        let stats = {
            let mut g = self.ssh.lock();
            let i = g
                .get_mut(&id)
                .ok_or_else(|| err(error_code::SSH_INSTANCE_NOT_FOUND, "ssh instance vanished"))?;
            i.record = Some(record);
            i.forwarder = Some(token.clone());
            i.stats.clone()
        };
        let cidrs = parse_cidrs(&a.allowed_source_cidrs)?;
        tokio::spawn(forward(
            listener,
            SocketAddr::from(([127, 0, 0, 1], local_port)),
            cidrs,
            self.forward_guard(),
            stats,
            token,
        ));
        // The firewall opens the SSH range only to the instances' CIDRs.
        self.apply_firewall();
        ctx.progress(Some(60.0), "waiting for sshd");
        let rt = runtime.to_string();
        let n2 = name.clone();
        let alive = move || -> futures::future::BoxFuture<'static, bool> {
            let rt = rt.clone();
            let n = n2.clone();
            Box::pin(async move {
                cli(
                    &rt,
                    &[s("inspect"), s("-f"), s("{{.State.Running}}"), n],
                    Duration::from_secs(10),
                )
                .await
                .map(|o| o == "true")
                .unwrap_or(false)
            })
        };
        let left = deadline
            .saturating_sub(started.elapsed())
            .max(Duration::from_secs(10));
        if let Err(mut e) = wait_banner(
            SocketAddr::from(([127, 0, 0, 1], local_port)),
            left,
            &ctx.cancel,
            alive,
        )
        .await
        {
            if let Ok(logs) = cli(
                runtime,
                &[s("logs"), s("--tail"), s("15"), name.clone()],
                Duration::from_secs(15),
            )
            .await
            {
                e.message = format!(
                    "{}; container log tail: {}",
                    e.message,
                    logs.lines().collect::<Vec<_>>().join(" | ")
                );
            }
            return Err(e);
        }
        ctx.progress(Some(85.0), "reading host keys");
        let keys = self.read_host_keys(runtime, &name, local_port).await;
        if keys.is_empty() {
            return Err(err(
                error_code::ENGINE_START_FAILED,
                "sshd is up but its host keys could not be read",
            ));
        }
        if let Some(r) = self.ssh.lock().get_mut(&id).and_then(|i| i.record.as_mut()) {
            r.host_keys = keys;
        }
        info!(ssh_instance = %id, %bind, host_port, local_port, "ssh instance running");
        Ok(())
    }

    async fn read_host_keys(&self, runtime: &str, name: &str, local_port: u16) -> Vec<SshHostKey> {
        if let Some(scan) = crate::system::which("ssh-keyscan") {
            for _ in 0..3 {
                let out = tokio::time::timeout(
                    Duration::from_secs(15),
                    tokio::process::Command::new(&scan)
                        .args(["-T", "5", "-p", &local_port.to_string(), "127.0.0.1"])
                        .stdin(Stdio::null())
                        .stderr(Stdio::null())
                        .output(),
                )
                .await;
                if let Ok(Ok(o)) = out {
                    let keys = parse_host_keys(&String::from_utf8_lossy(&o.stdout));
                    if !keys.is_empty() {
                        return keys;
                    }
                }
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        }
        let cmd = "cat /etc/ssh/ssh_host_*_key.pub /config/ssh_host_keys/ssh_host_*_key.pub 2>/dev/null; true";
        cli(
            runtime,
            &[s("exec"), name.to_string(), s("sh"), s("-c"), s(cmd)],
            Duration::from_secs(20),
        )
        .await
        .map(|o| parse_host_keys(&o))
        .unwrap_or_default()
    }

    fn ssh_persist(&self, id: Uuid) {
        let rec = self.ssh.lock().get(&id).and_then(|i| i.record.clone());
        if let Some(r) = rec {
            if let Err(e) =
                crate::state::write_json(&self.ssh_dir(id).join("instance.json"), &r, 0o600)
            {
                warn!("persist ssh instance {id}: {e:#}");
            }
        }
    }

    pub async fn stop_ssh_instance(
        self: &Arc<Self>,
        _ctx: &Ctx,
        a: StopSshInstanceArgs,
    ) -> Result<Value, ErrorInfo> {
        let id = a.ssh_instance_id;
        let info = {
            let mut g = self.ssh.lock();
            g.get_mut(&id)
                .map(|i| (i.forwarder.take(), i.record.clone(), i.state))
        };
        let persisted: Option<SshRecord> =
            crate::state::read_json(&self.ssh_dir(id).join("instance.json"))
                .ok()
                .flatten();
        let Some((fwd, record, _)) = info.or_else(|| {
            persisted
                .clone()
                .map(|r| (None, Some(r), SshInstanceState::Stopped))
        }) else {
            // Unknown id: still remove a leftover container by name.
            if let Some(rt) = self.container_runtime.clone() {
                let _ = cli(
                    &rt,
                    &[s("rm"), s("-f"), container_name(id)],
                    Duration::from_secs(30),
                )
                .await;
            }
            return Ok(
                json!({"ssh_instance_id": id, "stopped": false, "container_removed": false, "volumes_removed": []}),
            );
        };
        self.ssh_set_state(
            id,
            SshInstanceState::Stopping,
            InstanceHealth::Unknown,
            Some("stop_ssh_instance".into()),
        );
        if let Some(t) = fwd {
            t.cancel();
        }
        let record = record.or(persisted);
        let runtime = record
            .as_ref()
            .map(|r| r.runtime.clone())
            .or_else(|| self.container_runtime.clone());
        let name = container_name(id);
        let mut removed = false;
        let mut volumes_removed: Vec<String> = vec![];
        if let Some(rt) = runtime {
            if a.force {
                let _ = cli(&rt, &[s("kill"), name.clone()], Duration::from_secs(20)).await;
            } else {
                let _ = cli(
                    &rt,
                    &[
                        s("stop"),
                        s("-t"),
                        a.kill_grace_seconds.max(1).to_string(),
                        name.clone(),
                    ],
                    Duration::from_secs(a.kill_grace_seconds + 30),
                )
                .await;
            }
            removed = cli(
                &rt,
                &[s("rm"), s("-f"), name.clone()],
                Duration::from_secs(30),
            )
            .await
            .is_ok();
            if a.remove_volumes {
                for (v, keep) in record
                    .as_ref()
                    .map(|r| r.volumes.clone())
                    .unwrap_or_default()
                {
                    if !keep
                        && cli(
                            &rt,
                            &[s("volume"), s("rm"), s("-f"), v.clone()],
                            Duration::from_secs(30),
                        )
                        .await
                        .is_ok()
                    {
                        volumes_removed.push(v);
                    }
                }
            }
        }
        let _ = std::fs::remove_dir_all(self.ssh_dir(id));
        self.ssh_set_state(
            id,
            SshInstanceState::Stopped,
            InstanceHealth::Unknown,
            Some("stopped".into()),
        );
        self.ssh.lock().remove(&id);
        self.inventory_dirty.notify_one();
        Ok(
            json!({"ssh_instance_id": id, "stopped": true, "container_removed": removed, "volumes_removed": volumes_removed}),
        )
    }

    /// Re-adopt SSH containers after an agent restart (forwarder re-opened on the same port).
    pub async fn restore_ssh(self: &Arc<Self>) {
        let root = self.paths.root.join("ssh");
        let Ok(rd) = std::fs::read_dir(&root) else {
            return;
        };
        for e in rd.flatten() {
            let Ok(id) = e.file_name().to_string_lossy().parse::<Uuid>() else {
                continue;
            };
            let rec: Option<SshRecord> = crate::state::read_json(&e.path().join("instance.json"))
                .ok()
                .flatten();
            let Some(mut rec) = rec else { continue };
            self.ssh.lock().insert(
                id,
                SshInstance {
                    id,
                    state: SshInstanceState::Starting,
                    health: InstanceHealth::Unknown,
                    message: Some("re-adopting after agent restart".into()),
                    record: Some(rec.clone()),
                    forwarder: None,
                    stats: Arc::new(ForwardStats::default()),
                },
            );
            let rt = rec.runtime.clone();
            let running = cli(
                &rt,
                &[
                    s("inspect"),
                    s("-f"),
                    s("{{.State.Running}}"),
                    rec.container_name.clone(),
                ],
                Duration::from_secs(20),
            )
            .await;
            let ok = match running.as_deref() {
                Ok("true") => true,
                Ok(_) => cli(
                    &rt,
                    &[s("start"), rec.container_name.clone()],
                    Duration::from_secs(60),
                )
                .await
                .is_ok(),
                Err(_) => false,
            };
            if !ok {
                self.ssh_set_state(
                    id,
                    SshInstanceState::Error,
                    InstanceHealth::Unhealthy,
                    Some("container is gone after agent restart".into()),
                );
                continue;
            }
            if let Ok(out) = cli(
                &rt,
                &[
                    s("port"),
                    rec.container_name.clone(),
                    format!("{}/tcp", rec.args.container_port),
                ],
                Duration::from_secs(20),
            )
            .await
            {
                if let Some(p) = out
                    .lines()
                    .filter_map(|l| l.trim().rsplit_once(':').and_then(|(_, p)| p.parse().ok()))
                    .next()
                {
                    rec.local_port = p;
                }
            }
            let listener = match self.ssh_listen(&rec.bind, rec.host_port).await {
                Ok(l) => l,
                Err(e) => {
                    self.ssh_set_state(
                        id,
                        SshInstanceState::Error,
                        InstanceHealth::Unhealthy,
                        Some(format!("forwarder: {}", e.message)),
                    );
                    continue;
                }
            };
            let token = self.shutdown.child_token();
            let stats = {
                let mut g = self.ssh.lock();
                let Some(i) = g.get_mut(&id) else { continue };
                i.record = Some(rec.clone());
                i.forwarder = Some(token.clone());
                i.stats.clone()
            };
            let cidrs = parse_cidrs(&rec.args.allowed_source_cidrs).unwrap_or_default();
            tokio::spawn(forward(
                listener,
                SocketAddr::from(([127, 0, 0, 1], rec.local_port)),
                cidrs,
                self.forward_guard(),
                stats,
                token,
            ));
            self.ssh_persist(id);
            self.ssh_set_state(
                id,
                SshInstanceState::Running,
                InstanceHealth::Healthy,
                Some("re-adopted after agent restart".into()),
            );
        }
    }

    /// One monitor pass: TTL expiry and health (container running + loopback connect).
    pub async fn ssh_monitor_once(self: &Arc<Self>) {
        let items: Vec<(Uuid, SshInstanceState, Option<SshRecord>)> = self
            .ssh
            .lock()
            .values()
            .map(|i| (i.id, i.state, i.record.clone()))
            .collect();
        for (id, state, rec) in items {
            let Some(rec) = rec else { continue };
            if !matches!(state, SshInstanceState::Running | SshInstanceState::Error) {
                continue;
            }
            if rec.expires_at.is_some_and(|t| t <= gpudeck_protocol::now()) {
                info!(ssh_instance = %id, "ttl expired; stopping");
                let mut ev = Event::info(
                    EventKind::SshInstanceStateChanged,
                    format!("ssh instance {id}: ttl expired"),
                );
                ev.ssh_instance_id = Some(id);
                self.event(ev);
                let _ = self
                    .stop_ssh_instance(
                        &Ctx::internal(),
                        StopSshInstanceArgs {
                            ssh_instance_id: id,
                            force: false,
                            kill_grace_seconds: 10,
                            remove_volumes: true,
                        },
                    )
                    .await;
                continue;
            }
            let running = cli(
                &rec.runtime,
                &[
                    s("inspect"),
                    s("-f"),
                    s("{{.State.Running}}"),
                    rec.container_name.clone(),
                ],
                Duration::from_secs(10),
            )
            .await;
            let reach = tokio::time::timeout(
                Duration::from_secs(3),
                TcpStream::connect(SocketAddr::from(([127, 0, 0, 1], rec.local_port))),
            )
            .await
            .map(|r| r.is_ok())
            .unwrap_or(false);
            match (running.as_deref(), reach) {
                (Ok("true"), true) => {
                    if state != SshInstanceState::Running {
                        self.ssh_set_state(
                            id,
                            SshInstanceState::Running,
                            InstanceHealth::Healthy,
                            Some("healthy again".into()),
                        );
                    }
                }
                (Ok("true"), false) => self.ssh_set_state(
                    id,
                    SshInstanceState::Error,
                    InstanceHealth::Unhealthy,
                    Some("sshd not reachable on its loopback port".into()),
                ),
                _ => self.ssh_set_state(
                    id,
                    SshInstanceState::Error,
                    InstanceHealth::Unhealthy,
                    Some("container is not running".into()),
                ),
            }
        }
    }
}

/// Background loop: restore, then monitor every 15 s.
pub async fn run(agent: Arc<Agent>) {
    if agent.container_runtime.is_some() {
        agent.restore_ssh().await;
    }
    loop {
        tokio::select! {
            _ = tokio::time::sleep(MONITOR_INTERVAL) => agent.ssh_monitor_once().await,
            _ = agent.shutdown.cancelled() => return,
        }
    }
}

fn status_of(i: &SshInstance) -> SshInstanceStatus {
    let r = i.record.as_ref();
    SshInstanceStatus {
        ssh_instance_id: i.id,
        state: i.state,
        health: i.health,
        container_id: r.and_then(|r| r.container_id.clone()),
        image: r.map(|r| r.args.image.clone()),
        host_address: r.map(|r| r.host_address.clone()),
        host_port: r.map(|r| r.host_port),
        ssh_user: r.map(|r| r.args.ssh_user.clone()),
        host_key_fingerprint: r.and_then(|r| r.host_keys.first().map(|k| k.fingerprint.clone())),
        device_ids: r
            .map(|r| r.args.devices.iter().map(|d| d.device_id).collect())
            .unwrap_or_default(),
        gpu_attached: r.is_some_and(|r| r.gpu_attached),
        active_connections: i.stats.active.load(Ordering::SeqCst),
        connections_total: i.stats.total.load(Ordering::SeqCst),
        last_connection_at: if i.stats.active.load(Ordering::SeqCst) > 0 {
            Some(gpudeck_protocol::now())
        } else {
            *i.stats.last.lock().unwrap_or_else(|e| e.into_inner())
        },
        started_at: r.and_then(|r| r.started_at),
        expires_at: r.and_then(|r| r.expires_at),
        message: i.message.clone(),
    }
}

/// `details` helper for tests / events.
pub fn status_json(st: &SshInstanceStatus) -> JsonObject {
    serde_json::to_value(st)
        .ok()
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpudeck_protocol::{DeviceAssignment, Slice, SshVolume};

    const ED: &str =
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl test@x";

    #[test]
    fn fingerprints_and_keys() {
        // `ssh-keygen -lf` of this key prints SHA256:gUdc6SV0rcC6xNC9rJA7qNodRUVoLDlo5XAqUVUZZD4 is not
        // needed: check the format and that parsing is stable across both input shapes.
        let fp =
            fingerprint("AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl")
                .unwrap();
        assert!(fp.starts_with("SHA256:") && fp.len() == 7 + 43, "{fp}");
        let scan = "# 127.0.0.1:2222 SSH-2.0-OpenSSH_9.9\n[127.0.0.1]:2222 ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7\n[127.0.0.1]:2222 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n";
        let keys = parse_host_keys(scan);
        assert_eq!(keys.len(), 2);
        assert_eq!(keys[0].key_type, "ssh-ed25519");
        assert_eq!(keys[0].fingerprint, fp);
        let pubs = parse_host_keys(ED);
        assert_eq!(pubs[0].fingerprint, fp);
        assert!(valid_public_key(ED));
        assert!(valid_public_key(&format!("from=\"10.0.0.0/8\" {ED}")));
        assert!(!valid_public_key("not a key"));
        assert!(!valid_public_key("ssh-ed25519 !!!"));
    }

    #[test]
    fn cidrs_and_users() {
        let c = parse_cidrs(&["10.0.0.0/8".into(), "192.168.1.5".into()]).unwrap();
        assert!(cidr_allows(&c, "10.1.2.3".parse().unwrap()));
        assert!(cidr_allows(&c, "192.168.1.5".parse().unwrap()));
        assert!(!cidr_allows(&c, "172.16.0.1".parse().unwrap()));
        assert!(
            !cidr_allows(&[], "1.2.3.4".parse().unwrap()),
            "no CIDRs = nobody (fail closed)"
        );
        assert!(parse_cidrs(&["bogus".into()]).is_err());
        assert!(
            valid_user("gpudeck")
                && valid_user("a_b-1")
                && !valid_user("Root")
                && !valid_user("1x")
                && !valid_user("")
        );
    }

    #[test]
    fn user_env_cannot_override_managed_variables() {
        let mut a = StartSshInstanceArgs::new(Uuid::new_v4(), "img:1", vec![ED.into()]);
        for (k, v) in [
            ("PASSWORD_ACCESS", "true"),
            ("USER_PASSWORD", "hunter2"),
            ("SUDO_ACCESS", "true"),
            ("PUBLIC_KEY", "ssh-ed25519 AAAA attacker"),
            ("NVIDIA_VISIBLE_DEVICES", "all"),
            ("CUDA_MPS_ACTIVE_THREAD_PERCENTAGE", "100"),
            ("GPUDECK_SSH_USER", "root"),
            ("TZ", "UTC"),
        ] {
            a.env.insert(k.into(), v.into());
        }
        let (args, _, warnings, _) = run_args(
            &a,
            Uuid::new_v4(),
            Path::new("/s/keys"),
            ED,
            Path::new("/cache"),
            &HashMap::new(),
            None,
            true,
        );
        let envs: Vec<&String> = args
            .iter()
            .zip(args.iter().skip(1))
            .filter(|(f, _)| f.as_str() == "-e")
            .map(|(_, v)| v)
            .collect();
        let has = |kv: &str| envs.iter().any(|e| e.as_str() == kv);
        assert!(has("PASSWORD_ACCESS=false") && !has("PASSWORD_ACCESS=true"));
        assert!(has("SUDO_ACCESS=false") && !has("SUDO_ACCESS=true"));
        assert!(!envs.iter().any(|e| e.starts_with("USER_PASSWORD=")));
        assert!(!envs
            .iter()
            .any(|e| e.starts_with("NVIDIA_VISIBLE_DEVICES=")));
        assert!(!envs.iter().any(|e| e.starts_with("CUDA_MPS_")));
        assert!(has("GPUDECK_SSH_USER=gpudeck") && !has("GPUDECK_SSH_USER=root"));
        assert!(!envs.iter().any(|e| e.contains("attacker")));
        assert!(has("TZ=UTC"));
        assert_eq!(warnings.iter().filter(|w| w.contains("managed")).count(), 7);
    }

    #[test]
    fn docker_run_args() {
        let mut a = StartSshInstanceArgs::new(Uuid::new_v4(), "img:1", vec![ED.into()]);
        a.name = Some("Alice Dev".into());
        a.devices = vec![
            DeviceAssignment {
                device_id: Uuid::new_v4(),
                index: 0,
                vendor: DeviceVendor::Nvidia,
                slice: Some(Slice {
                    vram_bytes: 8 << 30,
                    compute_pct: 50,
                }),
            },
            DeviceAssignment {
                device_id: Uuid::new_v4(),
                index: 2,
                vendor: DeviceVendor::Nvidia,
                slice: None,
            },
        ];
        a.limits.cpu_cores = Some(4.0);
        a.limits.memory_bytes = Some(16 << 30);
        a.limits.vram_bytes = Some(4 << 30);
        a.volumes = vec![
            SshVolume {
                kind: SshVolumeType::Volume,
                source: Some("home".into()),
                target: "/home/x".into(),
                read_only: false,
                keep: true,
                size_bytes: None,
            },
            SshVolume {
                kind: SshVolumeType::Bind,
                source: Some("/cache/models".into()),
                target: "/models".into(),
                read_only: false,
                keep: false,
                size_bytes: None,
            },
            SshVolume {
                kind: SshVolumeType::Tmpfs,
                source: None,
                target: "/scratch".into(),
                read_only: false,
                keep: false,
                size_bytes: Some(1 << 30),
            },
        ];
        a.env.insert("TZ".into(), "UTC".into());
        let (args, gpu, warnings, vols) = run_args(
            &a,
            Uuid::new_v4(),
            Path::new("/s/keys"),
            ED,
            Path::new("/cache"),
            &HashMap::new(),
            None,
            true,
        );
        let j = args.join(" ");
        assert!(gpu);
        assert!(j.contains("--gpus \"device=0,2\""), "{j}");
        assert!(j.contains("-p 127.0.0.1::22"));
        assert!(
            j.contains("--cap-drop ALL")
                && j.contains("no-new-privileges")
                && !j.contains("--privileged")
        );
        assert!(j.contains("--cpus 4") && j.contains(&format!("--memory {}b", 16u64 << 30)));
        assert!(
            j.contains("CUDA_MPS_PINNED_DEVICE_MEM_LIMIT=0=8192M,1=4096M"),
            "{j}"
        );
        assert!(j.contains("CUDA_MPS_ACTIVE_THREAD_PERCENTAGE=50"));
        assert!(warnings.iter().any(|w| w.contains("MPS")));
        assert!(j.contains("type=bind,source=/cache/models,target=/models,readonly"));
        assert!(j.contains("type=tmpfs,target=/scratch,tmpfs-size=1073741824"));
        assert!(j.contains(&format!(
            "type=volume,source={}-home,target=/home/x",
            container_name(a.ssh_instance_id)
        )));
        assert_eq!(
            vols,
            vec![(format!("{}-home", container_name(a.ssh_instance_id)), true)]
        );
        assert!(
            j.contains(&format!("/s/keys:{KEYS_MOUNT}:ro"))
                && j.contains("USER_NAME=gpudeck")
                && j.contains("TZ=UTC")
        );
        assert!(j.contains("--hostname alice-dev-"));
        assert_eq!(args.last().unwrap(), "img:1");
        // AMD: per-device render nodes + CU count from the slice; macOS: no passthrough.
        a.devices = vec![DeviceAssignment {
            device_id: Uuid::new_v4(),
            index: 1,
            vendor: DeviceVendor::Amd,
            slice: Some(Slice {
                vram_bytes: 1 << 30,
                compute_pct: 25,
            }),
        }];
        let (args, _, _, _) = run_args(
            &a,
            Uuid::new_v4(),
            Path::new("/k"),
            ED,
            Path::new("/cache"),
            &HashMap::from([(1, 304)]),
            None,
            true,
        );
        let j = args.join(" ");
        assert!(
            j.contains("--device /dev/kfd --device /dev/dri/renderD129")
                && j.contains("GPUDECK_DEVICE_CORES=76"),
            "{j}"
        );
        let (_, gpu, warnings, _) = run_args(
            &a,
            Uuid::new_v4(),
            Path::new("/k"),
            ED,
            Path::new("/cache"),
            &HashMap::new(),
            None,
            false,
        );
        assert!(!gpu && warnings.iter().any(|w| w.contains("Linux")));
    }

    #[tokio::test]
    async fn forwarder_filters_and_counts() {
        let backend = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let target = backend.local_addr().unwrap();
        tokio::spawn(async move {
            while let Ok((mut c, _)) = backend.accept().await {
                tokio::spawn(async move {
                    use tokio::io::AsyncWriteExt;
                    let _ = c.write_all(b"SSH-2.0-test\r\n").await;
                    let mut b = [0u8; 16];
                    let _ = c.read(&mut b).await;
                });
            }
        });
        let front = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = front.local_addr().unwrap();
        let stats = Arc::new(ForwardStats::default());
        let cancel = CancellationToken::new();
        let guard = ForwardGuard {
            defaults: Arc::new(std::sync::RwLock::new(vec![])),
            monitor: Arc::new(crate::ingress::IngressMonitor::default()),
        };
        tokio::spawn(forward(
            front,
            target,
            parse_cidrs(&["127.0.0.0/8".into()]).unwrap(),
            guard.clone(),
            stats.clone(),
            cancel.clone(),
        ));
        wait_banner(
            addr,
            Duration::from_secs(5),
            &CancellationToken::new(),
            || Box::pin(async { true }),
        )
        .await
        .unwrap();
        let mut c = TcpStream::connect(addr).await.unwrap();
        let mut b = [0u8; 4];
        c.read_exact(&mut b).await.unwrap();
        assert_eq!(&b, b"SSH-");
        assert!(stats.total.load(Ordering::SeqCst) >= 2);
        assert!(stats.last.lock().unwrap().is_some());
        cancel.cancel();
        // A denied source never reaches the backend.
        let front = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = front.local_addr().unwrap();
        let stats2 = Arc::new(ForwardStats::default());
        tokio::spawn(forward(
            front,
            target,
            parse_cidrs(&["10.0.0.0/8".into()]).unwrap(),
            guard.clone(),
            stats2.clone(),
            CancellationToken::new(),
        ));
        let mut c = TcpStream::connect(addr).await.unwrap();
        let n = tokio::time::timeout(Duration::from_secs(2), c.read(&mut b))
            .await
            .unwrap()
            .unwrap_or(0);
        assert_eq!(n, 0);
        assert_eq!(stats2.total.load(Ordering::SeqCst), 0);
        // No CIDRs anywhere: nobody gets in; the policy default opens it without a restart.
        let front = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = front.local_addr().unwrap();
        let stats3 = Arc::new(ForwardStats::default());
        tokio::spawn(forward(
            front,
            target,
            vec![],
            guard.clone(),
            stats3.clone(),
            CancellationToken::new(),
        ));
        let mut c = TcpStream::connect(addr).await.unwrap();
        let n = tokio::time::timeout(Duration::from_secs(2), c.read(&mut b))
            .await
            .unwrap()
            .unwrap_or(0);
        assert_eq!(n, 0);
        assert_eq!(stats3.total.load(Ordering::SeqCst), 0);
        assert!(guard.monitor.take().unwrap().total >= 2);
        *guard.defaults.write().unwrap() = parse_cidrs(&["127.0.0.1".into()]).unwrap();
        let mut c = TcpStream::connect(addr).await.unwrap();
        c.read_exact(&mut b).await.unwrap();
        assert_eq!(&b, b"SSH-");
    }
}
