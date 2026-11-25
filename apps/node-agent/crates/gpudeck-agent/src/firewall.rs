//! Host firewall (nftables on Linux, a pf anchor on macOS): builds the plan from the agent
//! config, the verified security policy and the persisted state, renders it
//! (`egress.rs`), programs it and reports whether the loaded rules are the current plan.
//!
//! - Applied by default: the installer runs `gpudeck-agent firewall apply` as root, and the
//!   agent re-applies it on start and whenever the policy, SSH instances or distributed peers
//!   change (systemd grants it `CAP_NET_ADMIN`; elsewhere it only renders
//!   `<state_dir>/firewall.{nft,pf}`). `firewall = "off"` in `agent.toml` opts out.
//! - Never locks out the operator: the ports sshd listens on (`sshd_config`, `/proc`), the port
//!   of the SSH session running the installer (`SSH_CONNECTION`) and `firewall_preserve_ports`
//!   are accepted before any GPU Deck rule; the guard touches only GPU Deck ports, so other
//!   services are not affected unless the egress lock (inbound default deny) is enabled.
//! - Idempotent: nftables replaces table `inet gpudeck` atomically, pf replaces the anchor.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{anyhow, bail, Context, Result};
use gpudeck_protocol::{EgressLockKind, SecurityPolicy, ServingMode};

use crate::config::AgentConfig;
use crate::egress::{self, EgressPlan, Guard, Plan, NFT_TABLE, PF_ANCHOR};
use crate::state::StatePaths;

/// Inputs that change at run time (the running agent knows them; the CLI reads them from the
/// state directory).
#[derive(Debug, Clone, Default)]
pub struct Inputs {
    pub policy: Option<SecurityPolicy>,
    /// Union of the SSH instances' source CIDRs (plus the policy default).
    pub ssh_sources: Vec<String>,
    /// Distributed group peers.
    pub peers: Vec<String>,
    /// Egress destinations (resolved), when the egress lock is on.
    pub egress: Option<EgressPlan>,
    pub container_runtime: bool,
}

/// Ports sshd listens on: `sshd_config` (+ `sshd_config.d`), and on Linux the listening sockets
/// of processes named `sshd` (needs root to see other users' sockets), plus the server port of
/// `SSH_CONNECTION` (the session running this command). Always includes 22.
pub fn detect_ssh_ports() -> Vec<u16> {
    let mut ports: BTreeSet<u16> = BTreeSet::from([22]);
    let mut files = vec![PathBuf::from("/etc/ssh/sshd_config")];
    if let Ok(rd) = std::fs::read_dir("/etc/ssh/sshd_config.d") {
        files.extend(rd.flatten().map(|e| e.path()));
    }
    for f in files {
        if let Ok(text) = std::fs::read_to_string(&f) {
            ports.extend(parse_sshd_ports(&text));
        }
    }
    if let Some(p) = session_ssh_port(std::env::var("SSH_CONNECTION").ok().as_deref()) {
        ports.insert(p);
    }
    for l in crate::audit::listeners() {
        if l.process.as_deref() == Some("sshd") && !l.addr.is_loopback() {
            ports.insert(l.port);
        }
    }
    ports.into_iter().collect()
}

/// `Port 2222` lines (and `ListenAddress host:port`) of an sshd_config.
pub fn parse_sshd_ports(text: &str) -> Vec<u16> {
    let mut out = vec![];
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or("").trim();
        let mut it = line.split_whitespace();
        match it.next().map(str::to_ascii_lowercase).as_deref() {
            Some("port") => {
                if let Some(p) = it.next().and_then(|p| p.parse().ok()) {
                    out.push(p);
                }
            }
            Some("listenaddress") => {
                if let Some(a) = it.next() {
                    if let Some((_, p)) = a.rsplit_once(':') {
                        if !a.ends_with(']') {
                            if let Ok(p) = p.parse() {
                                out.push(p);
                            }
                        }
                    }
                }
            }
            _ => {}
        }
    }
    out
}

/// Server port (4th field) of `SSH_CONNECTION="client_ip client_port server_ip server_port"`.
pub fn session_ssh_port(v: Option<&str>) -> Option<u16> {
    v?.split_whitespace().nth(3)?.parse().ok()
}

/// The plan for this node.
pub fn build_plan(cfg: &AgentConfig, engine_range: [u16; 2], inputs: &Inputs) -> Plan {
    let mut preserve: Vec<u16> = cfg.firewall_preserve_ports.clone();
    preserve.push(22);
    preserve.sort_unstable();
    preserve.dedup();
    let proxy_sources = match cfg.serving_mode() {
        ServingMode::Tunnel => None,
        ServingMode::Direct => Some(
            crate::security::direct_sources(inputs.policy.as_ref())
                .iter()
                .map(|n| n.to_string())
                .collect(),
        ),
    };
    let mut closed = vec![[cfg.prom_port, cfg.prom_port], engine_range];
    closed.retain(|[a, b]| a <= b);
    Plan {
        guard: Guard {
            preserve_ports: preserve,
            proxy_port: cfg.proxy_port,
            proxy_sources,
            // Closed even without a container runtime: nothing of ours should listen there.
            ssh_ports: Some(cfg.ssh_port_range),
            ssh_sources: inputs.ssh_sources.clone(),
            closed,
            peers: inputs.peers.clone(),
        },
        egress: inputs.egress.clone(),
    }
}

/// `(file, text, program + args)` for the backend.
pub fn render(
    kind: EgressLockKind,
    paths: &StatePaths,
    plan: &Plan,
) -> Result<(PathBuf, String, Vec<String>)> {
    Ok(match kind {
        EgressLockKind::Pf => {
            let f = paths.root.join("firewall.pf");
            (
                f.clone(),
                egress::render_pf(plan),
                vec![
                    "/sbin/pfctl".into(),
                    "-a".into(),
                    PF_ANCHOR.into(),
                    "-f".into(),
                    f.display().to_string(),
                ],
            )
        }
        EgressLockKind::Nftables => {
            let f = paths.root.join("firewall.nft");
            (
                f.clone(),
                egress::render_nft(plan),
                vec!["nft".into(), "-f".into(), f.display().to_string()],
            )
        }
        _ => bail!("no firewall backend on this host (nftables on Linux, pf on macOS)"),
    })
}

/// Program the rules (needs root or CAP_NET_ADMIN).
pub async fn program(kind: EgressLockKind, cmd: &[String]) -> Result<()> {
    let out = tokio::process::Command::new(&cmd[0])
        .args(&cmd[1..])
        .output()
        .await
        .with_context(|| format!("run {}", cmd[0]))?;
    if !out.status.success() {
        bail!(
            "{} failed: {}",
            cmd[0],
            String::from_utf8_lossy(&out.stderr).trim()
        );
    }
    if kind == EgressLockKind::Pf {
        // Enable pf (idempotent; the stock pf.conf evaluates the com.apple/* anchors).
        let _ = tokio::process::Command::new("/sbin/pfctl")
            .arg("-E")
            .output()
            .await;
    }
    Ok(())
}

/// What is loaded right now.
#[derive(Debug, Clone, PartialEq)]
pub enum Loaded {
    /// Our rules with this plan hash.
    Plan(String),
    /// Our table / anchor exists but carries no plan marker (hand edited / older agent).
    Unknown,
    Absent,
}

pub async fn loaded(kind: EgressLockKind) -> Result<Loaded> {
    let (prog, args): (&str, Vec<&str>) = match kind {
        EgressLockKind::Nftables => ("nft", vec!["list", "table", "inet", NFT_TABLE]),
        EgressLockKind::Pf => ("/sbin/pfctl", vec!["-a", PF_ANCHOR, "-sr"]),
        _ => bail!("no firewall backend"),
    };
    let out = tokio::process::Command::new(prog)
        .args(&args)
        .output()
        .await
        .with_context(|| format!("run {prog}"))?;
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    let err = String::from_utf8_lossy(&out.stderr).to_string();
    if !out.status.success() {
        if err.contains("No such file") || err.contains("does not exist") {
            return Ok(Loaded::Absent);
        }
        if err.to_ascii_lowercase().contains("permission")
            || err.contains("Operation not permitted")
        {
            bail!("{prog}: permission denied (run as root)");
        }
        return Ok(Loaded::Absent);
    }
    Ok(parse_loaded(&text))
}

pub fn parse_loaded(text: &str) -> Loaded {
    for marker in ["gpudeck plan ", "gpudeck-plan-"] {
        if let Some(i) = text.find(marker) {
            let h: String = text[i + marker.len()..]
                .chars()
                .take_while(|c| c.is_ascii_hexdigit())
                .collect();
            if !h.is_empty() {
                return Loaded::Plan(h);
            }
        }
    }
    if text.trim().is_empty() {
        Loaded::Absent
    } else {
        Loaded::Unknown
    }
}

pub async fn remove(kind: EgressLockKind) -> Result<()> {
    let (prog, args): (&str, Vec<&str>) = match kind {
        EgressLockKind::Nftables => ("nft", vec!["delete", "table", "inet", NFT_TABLE]),
        EgressLockKind::Pf => ("/sbin/pfctl", vec!["-a", PF_ANCHOR, "-F", "all"]),
        _ => bail!("no firewall backend"),
    };
    let out = tokio::process::Command::new(prog)
        .args(&args)
        .output()
        .await
        .with_context(|| format!("run {prog}"))?;
    let err = String::from_utf8_lossy(&out.stderr);
    if !out.status.success() && !err.contains("No such file") && !err.contains("does not exist") {
        bail!("{prog}: {}", err.trim());
    }
    Ok(())
}

/// Inputs from the state directory (CLI: the agent may not be running).
pub fn inputs_from_state(cfg: &AgentConfig, paths: &StatePaths) -> Inputs {
    let policy = match (cfg.node_id, std::fs::read_to_string(paths.ca())) {
        (Some(node), Ok(ca)) => crate::security::load(&paths.security_policy(), &ca, node)
            .unwrap_or_else(|e| {
                tracing::warn!("security policy ignored: {e:#}");
                None
            }),
        _ => None,
    };
    let mut ssh_sources: Vec<String> = policy
        .as_ref()
        .and_then(|p| p.ssh.as_ref())
        .map(|s| s.default_allowed_cidrs.clone())
        .unwrap_or_default();
    ssh_sources.extend(crate::ssh::persisted_cidrs(paths));
    ssh_sources.sort();
    ssh_sources.dedup();
    let mut peers: Vec<String> = crate::state::load_instances(paths)
        .iter()
        .filter_map(|i| i.args.launch.distributed.as_ref())
        .flat_map(crate::distributed::peer_addresses)
        .collect();
    peers.sort();
    peers.dedup();
    Inputs {
        policy,
        ssh_sources,
        peers,
        egress: None,
        container_runtime: crate::system::container_runtime(cfg.container_runtime.as_deref())
            .is_some(),
    }
}

/// Engine port range from the persisted config (else the default).
pub fn engine_range(paths: &StatePaths) -> [u16; 2] {
    crate::state::load_config(paths)
        .and_then(|c| c.settings)
        .and_then(|s| s.engine_port_range)
        .unwrap_or([10240, 10340])
}

/// Egress destinations resolved from the persisted config (the egress lock part).
pub async fn egress_from_state(cfg: &AgentConfig, paths: &StatePaths) -> EgressPlan {
    let applied = crate::state::load_config(paths);
    egress_plan(cfg, applied.as_ref(), true).await
}

pub async fn egress_plan(
    cfg: &AgentConfig,
    applied: Option<&gpudeck_protocol::ConfigPush>,
    dns: bool,
) -> EgressPlan {
    let allowlist = applied
        .and_then(|c| c.egress_allowlist.clone())
        .unwrap_or_default();
    let ms = applied
        .and_then(|c| c.model_sources.clone())
        .unwrap_or_default();
    let mut dests = egress::destinations(
        cfg.hub_url.as_deref(),
        &allowlist,
        ms.huggingface_endpoint.as_deref(),
        ms.modelscope_endpoint.as_deref(),
    );
    // Tunnel endpoints (outbound to the API layer).
    if let (Some(node), Ok(ca)) = (
        cfg.node_id,
        std::fs::read_to_string(StatePaths::new(&cfg.state_dir).ca()),
    ) {
        if let Ok(Some(p)) = crate::security::load(
            &StatePaths::new(&cfg.state_dir).security_policy(),
            &ca,
            node,
        ) {
            dests.extend(crate::security::tunnel_endpoints(Some(&p)));
        }
    }
    EgressPlan {
        allow: egress::resolve_with(&dests, dns).await,
        dns: egress::resolvers(),
    }
}

impl crate::agent::Agent {
    /// Addresses of every peer of the cross-node groups this node is part of.
    pub fn distributed_peers(&self) -> Vec<String> {
        let mut v: Vec<String> = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .values()
            .filter(|i| i.state != gpudeck_protocol::InstanceState::Stopped)
            .filter_map(|i| i.args.launch.distributed.as_ref())
            .flat_map(crate::distributed::peer_addresses)
            .collect();
        v.sort();
        v.dedup();
        v
    }

    /// Render (always) and program (unless `firewall = "off"`; quietly skipped without
    /// privileges) the host firewall. Called on start and whenever the policy, SSH instances,
    /// distributed peers or egress inputs change.
    pub fn apply_firewall(self: &Arc<Self>) {
        let a = self.clone();
        tokio::spawn(async move {
            let _g = a.firewall_lock.lock().await;
            let kind = crate::egress::detect_kind(&a.cfg.egress_lock);
            if kind == EgressLockKind::None {
                return;
            }
            let egress_on = a.options.apply_egress_lock || a.cfg.firewall_egress_lock;
            let applied = a.applied.lock().unwrap_or_else(|e| e.into_inner()).clone();
            let mut inputs = Inputs {
                policy: a.policy(),
                ssh_sources: a.ssh_source_cidrs(),
                peers: vec![],
                egress: None,
                container_runtime: a.container_runtime.is_some(),
            };
            // Host names are resolved only when the egress lock is programmed.
            inputs.peers = egress::resolve_with(&a.distributed_peers(), egress_on)
                .await
                .into_iter()
                .map(|x| x.net)
                .collect();
            if egress_on {
                let mut e = egress_plan(&a.cfg, applied.as_ref(), true).await;
                e.allow.extend(
                    egress::resolve_with(
                        &crate::security::tunnel_endpoints(inputs.policy.as_ref()),
                        true,
                    )
                    .await,
                );
                inputs.egress = Some(e);
            }
            let plan = build_plan(&a.cfg, a.rt().port_range([10240, 10340]), &inputs);
            let (file, text, cmd) = match render(kind, &a.paths, &plan) {
                Ok(x) => x,
                Err(e) => {
                    tracing::debug!("firewall not rendered: {e:#}");
                    return;
                }
            };
            if let Err(e) = crate::state::write_private(&file, text.as_bytes(), 0o600) {
                tracing::warn!("write {}: {e}", file.display());
                return;
            }
            if !a.cfg.firewall_managed() && !egress_on {
                return;
            }
            let hash = plan.hash();
            if a.firewall_hash
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .as_deref()
                == Some(hash.as_str())
            {
                return;
            }
            match program(kind, &cmd).await {
                Ok(()) => {
                    *a.firewall_hash.lock().unwrap_or_else(|e| e.into_inner()) = Some(hash.clone());
                    tracing::info!(backend = %kind, plan = %hash, egress_lock = egress_on, "host firewall applied");
                    let mut d = gpudeck_protocol::JsonObject::new();
                    d.insert("backend".into(), kind.as_str().into());
                    d.insert("plan".into(), hash.into());
                    d.insert("egress_lock".into(), egress_on.into());
                    a.event(
                        gpudeck_protocol::Event::info(
                            gpudeck_protocol::EventKind::ConfigApplied,
                            "host firewall applied",
                        )
                        .details(d),
                    );
                }
                Err(e) => {
                    let msg = format!("{e:#}");
                    let unprivileged = msg.contains("Operation not permitted")
                        || msg.to_ascii_lowercase().contains("permission");
                    if egress_on {
                        tracing::error!("egress lock could not be applied: {msg}");
                        let mut d = gpudeck_protocol::JsonObject::new();
                        d.insert("backend".into(), kind.as_str().into());
                        d.insert("error".into(), msg.into());
                        a.event(
                            gpudeck_protocol::Event::critical(
                                gpudeck_protocol::EventKind::EgressBlocked,
                                "egress lock could not be applied",
                            )
                            .details(d),
                        );
                    } else if unprivileged {
                        tracing::info!("host firewall rendered to {} (the agent may not program it: run `gpudeck-agent firewall apply` as root)", file.display());
                    } else {
                        tracing::warn!("host firewall could not be applied: {msg}");
                    }
                }
            }
        });
    }

    /// Compatibility name: every change that used to re-render the egress lock re-applies the
    /// whole host firewall.
    pub fn apply_egress(self: &Arc<Self>) {
        self.apply_firewall();
    }
}

/// `gpudeck-agent firewall status`: prints what is loaded vs the current plan; exit code 0
/// only when the current plan is loaded.
pub async fn cli_status(cfg: &AgentConfig) -> Result<bool> {
    let paths = StatePaths::new(&cfg.state_dir);
    let kind = egress::detect_kind(&cfg.egress_lock);
    let inputs = inputs_from_state(cfg, &paths);
    let mut plan = build_plan(cfg, engine_range(&paths), &inputs);
    if cfg.firewall_egress_lock {
        plan.egress = Some(egress_from_state(cfg, &paths).await);
    }
    println!("backend         {kind}");
    println!(
        "managed         {}",
        if cfg.firewall_managed() {
            "yes (firewall = auto)"
        } else {
            "no (firewall = off)"
        }
    );
    println!("serving mode    {}", cfg.serving_mode().as_str());
    println!(
        "preserved ports {:?} (operator ssh)",
        plan.guard.preserve_ports
    );
    match &plan.guard.proxy_sources {
        None => println!("proxy :{}     closed (tunnel mode)", cfg.proxy_port),
        Some(s) => println!("proxy :{}     from {}", cfg.proxy_port, s.join(", ")),
    }
    match plan.guard.ssh_ports {
        Some([a, b]) if plan.guard.ssh_sources.is_empty() => {
            println!("ssh {a}-{b}    closed (no source CIDRs)")
        }
        Some([a, b]) => println!("ssh {a}-{b}    from {}", plan.guard.ssh_sources.join(", ")),
        None => println!("ssh instances   none (no container runtime)"),
    }
    println!(
        "egress lock     {}",
        if plan.egress.is_some() { "on" } else { "off" }
    );
    let want = plan.hash();
    let state = loaded(kind).await?;
    let ok = matches!(&state, Loaded::Plan(h) if *h == want);
    match state {
        Loaded::Plan(h) if h == want => println!("loaded          yes, current plan {h}"),
        Loaded::Plan(h) => println!("loaded          STALE plan {h} (current {want}): run `gpudeck-agent firewall apply`"),
        Loaded::Unknown => println!("loaded          rules present without a GPU Deck plan marker: run `gpudeck-agent firewall apply`"),
        Loaded::Absent => println!("loaded          NO: run `gpudeck-agent firewall apply` as root"),
    }
    Ok(ok)
}

/// `gpudeck-agent firewall apply`: detect and record the operator's SSH ports, render, program,
/// then verify the loaded plan. `dry_run` prints the rules only.
pub async fn cli_apply(
    cfg: &mut AgentConfig,
    config_path: &Path,
    extra_preserve: &[u16],
    egress_lock: bool,
    dry_run: bool,
) -> Result<()> {
    let paths = StatePaths::new(&cfg.state_dir);
    let kind = egress::detect_kind(&cfg.egress_lock);
    let mut preserve: BTreeSet<u16> = cfg.firewall_preserve_ports.iter().copied().collect();
    preserve.extend(detect_ssh_ports());
    preserve.extend(extra_preserve.iter().copied());
    let preserve: Vec<u16> = preserve.into_iter().collect();
    if preserve != cfg.firewall_preserve_ports && !dry_run {
        cfg.firewall_preserve_ports = preserve.clone();
        if let Err(e) = cfg.save(config_path) {
            eprintln!(
                "note: could not record firewall_preserve_ports in {}: {e:#}",
                config_path.display()
            );
        }
    } else {
        cfg.firewall_preserve_ports = preserve.clone();
    }
    if egress_lock && !cfg.firewall_egress_lock && !dry_run {
        cfg.firewall_egress_lock = true;
        let _ = cfg.save(config_path);
    }
    let inputs = inputs_from_state(cfg, &paths);
    let mut plan = build_plan(cfg, engine_range(&paths), &inputs);
    if egress_lock || cfg.firewall_egress_lock {
        plan.egress = Some(egress_from_state(cfg, &paths).await);
    }
    if dry_run {
        let text = match kind {
            EgressLockKind::Pf => egress::render_pf(&plan),
            _ => egress::render_nft(&plan),
        };
        print!("{text}");
        return Ok(());
    }
    if !cfg.firewall_managed() {
        println!("note: firewall = \"off\" in agent.toml: applying once by hand; the agent will not keep it updated");
    }
    paths.ensure()?;
    let (file, text, cmd) = render(kind, &paths, &plan)?;
    crate::state::write_private(&file, text.as_bytes(), 0o600)?;
    program(kind, &cmd).await?;
    let want = plan.hash();
    match loaded(kind).await? {
        Loaded::Plan(h) if h == want => {
            println!("host firewall applied ({kind}, plan {want}); operator ssh ports kept open: {preserve:?}");
            Ok(())
        }
        other => Err(anyhow!(
            "rules were loaded but verification found {other:?} (want plan {want})"
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ssh_port_detection() {
        let cfg = "# comment\nPort 2222\n  port 22 # trailing\nListenAddress 10.0.0.5:2022\nListenAddress [::1]\n#Port 99\n";
        assert_eq!(parse_sshd_ports(cfg), vec![2222, 22, 2022]);
        assert_eq!(
            session_ssh_port(Some("203.0.113.9 53122 10.0.0.5 2222")),
            Some(2222)
        );
        assert_eq!(session_ssh_port(Some("garbage")), None);
        assert_eq!(session_ssh_port(None), None);
    }

    #[test]
    fn plan_follows_mode_and_never_drops_preserved_ports() {
        let mut cfg = AgentConfig {
            firewall_preserve_ports: vec![2222],
            ..Default::default()
        };
        let inputs = Inputs {
            container_runtime: true,
            ssh_sources: vec!["198.51.100.0/24".into()],
            ..Default::default()
        };
        let p = build_plan(&cfg, [10240, 10340], &inputs);
        assert_eq!(p.guard.preserve_ports, vec![22, 2222]);
        assert!(p.guard.proxy_sources.is_none(), "tunnel: proxy port closed");
        assert_eq!(p.guard.ssh_ports, Some([2200, 2299]));
        assert!(p.guard.closed.contains(&[9100, 9100]) && p.guard.closed.contains(&[10240, 10340]));
        let nft = egress::render_nft(&p);
        let keep = nft.find("tcp dport { 22, 2222 } accept").unwrap();
        assert!(keep < nft.find("tcp dport 2200-2299").unwrap());
        cfg.serving_mode = "direct".into();
        let d = build_plan(&cfg, [10240, 10340], &Inputs::default());
        assert!(d
            .guard
            .proxy_sources
            .unwrap()
            .contains(&"10.0.0.0/8".to_string()));
        assert_eq!(
            d.guard.ssh_ports,
            Some([2200, 2299]),
            "the SSH-instance range is closed even without a container runtime"
        );
    }

    #[test]
    fn loaded_marker_parsing() {
        assert_eq!(
            parse_loaded("table inet gpudeck {\n chain input {\n iif \"lo\" accept comment \"gpudeck plan 0a1b2c3d4e5f\"\n"),
            Loaded::Plan("0a1b2c3d4e5f".into())
        );
        assert_eq!(
            parse_loaded("pass quick on lo0 all label \"gpudeck-plan-abc123\"\n"),
            Loaded::Plan("abc123".into())
        );
        assert_eq!(parse_loaded("block drop in all\n"), Loaded::Unknown);
        assert_eq!(parse_loaded(""), Loaded::Absent);
    }
}
