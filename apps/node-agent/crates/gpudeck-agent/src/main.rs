//! `gpudeck-agent` CLI: `join`, `run`, `status`, `stop-engines`, `firewall`, `security`,
//! `update`, `version`.

use std::collections::BTreeMap;
use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use gpudeck_agent::config::{default_config_path, AgentConfig};
use gpudeck_agent::join::{join, JoinOptions, AGENT_VERSION};
use gpudeck_agent::state::StatePaths;
use gpudeck_agent::{Agent, RunOptions};

#[derive(Parser)]
#[command(name = "gpudeck-agent", version = AGENT_VERSION, about = "GPU Deck node agent")]
struct Cli {
    /// agent.toml path (default /etc/gpudeck/agent.toml, /usr/local/etc/gpudeck/agent.toml on macOS).
    #[arg(long, global = true, env = "GPUDECK_AGENT_CONFIG")]
    config: Option<PathBuf>,
    /// Log as JSON lines.
    #[arg(long, global = true)]
    log_json: bool,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Join the control plane with a one-time token (writes key, cert, CA and agent.toml).
    Join {
        #[arg(long, env = "GPUDECK_AGENT_JOIN_TOKEN")]
        token: String,
        /// Hub URL, e.g. https://hub:8443.
        #[arg(long, alias = "hub", env = "GPUDECK_AGENT_HUB_URL")]
        url: String,
        #[arg(long)]
        name: Option<String>,
        /// SHA-256 of the hub CA certificate (required: pins the control plane's CA before the
        /// join token or anything else is sent).
        #[arg(long)]
        ca_fingerprint: Option<String>,
        /// Join without --ca-fingerprint (trust on first use). Only on a network you trust.
        #[arg(long)]
        insecure_skip_ca_pin: bool,
        /// `tunnel` (default: the agent dials out, no inbound port) or `direct` (the API layer
        /// dials the mTLS proxy on proxy_port; low-latency private networks).
        #[arg(long, value_parser = ["tunnel", "direct"])]
        serving_mode: Option<String>,
        /// Do not manage the host firewall (`firewall = "off"` in agent.toml).
        #[arg(long)]
        no_firewall: bool,
        /// Node label k=v (repeatable).
        #[arg(long = "label")]
        labels: Vec<String>,
        #[arg(long)]
        proxy_port: Option<u16>,
        #[arg(long)]
        state_dir: Option<PathBuf>,
    },
    /// Run the agent (hub connection, engines, metrics, proxy).
    Run {
        /// Program pf (macOS) / nftables (Linux) egress rules. Needs root / CAP_NET_ADMIN.
        #[arg(long)]
        apply_egress_lock: bool,
    },
    /// Show identity, hub, cache and persisted instances.
    Status,
    /// Stop the engines the agent started. Engines keep running when the agent stops or restarts
    /// (the next `run` re-adopts them); use this before maintenance. Their specs are kept, so
    /// the next `run` starts them again (unload them from the control plane to remove them).
    StopEngines {
        /// Seconds to wait after SIGTERM before SIGKILL.
        #[arg(long, default_value_t = 20)]
        grace_seconds: u64,
    },
    /// Host firewall for the agent's ports (nftables / pf).
    Firewall {
        #[command(subcommand)]
        action: FirewallCmd,
    },
    /// Security checks and recovery.
    Security {
        #[command(subcommand)]
        action: SecurityCmd,
    },
    /// Self-update (driven by the control plane: Update button or a cluster's rolling update).
    Update {
        #[command(subcommand)]
        action: UpdateCmd,
    },
    /// Print agent and protocol versions.
    Version,
}

#[derive(Subcommand)]
enum FirewallCmd {
    /// Show what is loaded vs the current plan (exit 1 when not applied or stale).
    Status,
    /// Detect and keep the operator's SSH ports open, render and program the rules, verify.
    Apply {
        /// Print the rules instead of programming them.
        #[arg(long)]
        dry_run: bool,
        /// Also lock egress (outbound only to the hub, model sources and the allow-list;
        /// inbound default deny). Recorded in agent.toml.
        #[arg(long)]
        egress_lock: bool,
        /// Extra TCP port to always accept (repeatable), e.g. a non-standard sshd port.
        #[arg(long = "preserve-port")]
        preserve_ports: Vec<u16>,
    },
    /// Remove GPU Deck's rules (table `inet gpudeck` / anchor `com.apple/gpudeck`).
    Remove,
}

#[derive(Subcommand)]
enum UpdateCmd {
    /// Show the update mode, the staged and the previous version.
    Status,
    /// Apply the staged update requested in <state_dir>/update/request.json: verify the
    /// signature again, swap the binary atomically (keeping <binary>.prev), restart the
    /// service, wait for the new agent to report healthy, roll back otherwise. Run by the
    /// update helper (systemd path unit / launchd daemon) or by the agent itself.
    Apply {
        /// How to restart the agent: systemd:<unit>, launchd:<domain>/<label> or signal
        /// (default: systemd:gpudeck-agent.service as root on Linux, else signal).
        #[arg(long)]
        restart: Option<String>,
    },
    /// Install the root-owned update helper for this binary and config (the installers run it
    /// as root): systemd gpudeck-agent-update.path + .service, or the launchd daemon
    /// io.github.sur950.gpudeck-agent-update.
    InstallHelper {
        /// How the helper restarts the agent: systemd:<unit> / launchd:<domain>/<label>.
        #[arg(long)]
        restart: Option<String>,
        /// Print the unit files instead of installing them.
        #[arg(long)]
        dry_run: bool,
    },
}

#[derive(Subcommand)]
enum SecurityCmd {
    /// List listening sockets and flag anything of GPU Deck's reachable from the network
    /// (exit 1 when something is exposed).
    Audit,
    /// Forget the signed security policy (hub / API-layer key pins). Recovery after a key
    /// rotation this node missed; the next config_push brings a fresh policy.
    ResetPolicy,
}

fn config_path(cli: &Cli) -> PathBuf {
    cli.config.clone().unwrap_or_else(default_config_path)
}

fn main() -> Result<()> {
    // PID 1 in a container: stay a minimal init (signal forwarding, reaping orphaned engine
    // children) and run the agent as our child. Before any thread or runtime exists.
    if let Some(code) = gpudeck_agent::init::maybe_run_as_init() {
        std::process::exit(code);
    }
    run()
}

#[tokio::main]
async fn run() -> Result<()> {
    let cli = Cli::parse();
    let path = config_path(&cli);
    let mut cfg = AgentConfig::load(&path)?;
    gpudeck_agent::logging::init(&cfg.log_level, cli.log_json);
    gpudeck_agent::tls::init_crypto();
    match cli.cmd {
        Cmd::Version => {
            println!(
                "gpudeck-agent {} (protocol v{})",
                gpudeck_agent::update::current_version(),
                gpudeck_protocol::PROTOCOL_VERSION
            );
            Ok(())
        }
        Cmd::Join {
            token,
            url,
            name,
            ca_fingerprint,
            insecure_skip_ca_pin,
            serving_mode,
            no_firewall,
            labels,
            proxy_port,
            state_dir,
        } => {
            if let Some(s) = state_dir {
                cfg.state_dir = s;
            }
            if ca_fingerprint.is_none() && cfg.ca_fingerprint.is_none() && !insecure_skip_ca_pin {
                bail!("refusing to join without --ca-fingerprint: it pins the control plane's CA before the join token is sent (copy it from the join dialog, or `GET <hub>/agent/v1/ca`). Pass --insecure-skip-ca-pin only on a network you trust");
            }
            if let Some(m) = serving_mode {
                cfg.serving_mode = m;
            }
            if no_firewall {
                cfg.firewall = "off".into();
            }
            let mut l: BTreeMap<String, String> = cfg.labels.clone();
            for kv in labels {
                let Some((k, v)) = kv.split_once('=') else {
                    bail!("--label expects k=v, got {kv}")
                };
                l.insert(k.to_string(), v.to_string());
            }
            cfg.labels = l.clone();
            let opts = JoinOptions {
                hub_url: url,
                token,
                name,
                ca_fingerprint,
                proxy_port: proxy_port.unwrap_or(cfg.proxy_port),
                labels: l,
            };
            let r = join(&mut cfg, &path, opts).await?;
            println!(
                "joined as {} (node_id {}), certificate valid until {}",
                r.node_name, r.node_id, r.not_after
            );
            println!(
                "config written to {}; start with: gpudeck-agent run",
                path.display()
            );
            Ok(())
        }
        Cmd::Run { apply_egress_lock } => {
            if cfg.node_id.is_none() {
                // Non-interactive join (DaemonSet): GPUDECK_AGENT_JOIN_TOKEN + GPUDECK_AGENT_HUB_URL.
                match (std::env::var("GPUDECK_AGENT_JOIN_TOKEN").ok(), cfg.hub_url.clone()) {
                    (Some(token), Some(url)) => {
                        let insecure = std::env::var("GPUDECK_AGENT_INSECURE_SKIP_CA_PIN").is_ok_and(|v| v == "1");
                        if cfg.ca_fingerprint.is_none() && !insecure {
                            bail!("automatic join needs GPUDECK_AGENT_CA_FINGERPRINT (pins the control plane's CA); set GPUDECK_AGENT_INSECURE_SKIP_CA_PIN=1 only on a network you trust");
                        }
                        let opts = JoinOptions { hub_url: url, token, name: cfg.name.clone(), ca_fingerprint: cfg.ca_fingerprint.clone(), proxy_port: cfg.proxy_port, labels: cfg.labels.clone() };
                        join(&mut cfg, &path, opts).await.context("automatic join")?;
                    }
                    _ => bail!("not joined: run `gpudeck-agent join --token <gdj_...> --url https://hub:8443` first"),
                }
            }
            let (devices, engines) = gpudeck_agent::backend::default_backends();
            let agent = Agent::new(
                cfg,
                path,
                devices,
                engines,
                RunOptions {
                    apply_egress_lock,
                    ..Default::default()
                },
            )?;
            let a = agent.clone();
            tokio::spawn(async move {
                wait_for_signal().await;
                a.shutdown.cancel();
            });
            agent.run().await
        }
        Cmd::Status => status(&cfg, &path).await,
        Cmd::StopEngines { grace_seconds } => stop_engines(&cfg, grace_seconds).await,
        Cmd::Firewall { action } => match action {
            FirewallCmd::Status => {
                if !gpudeck_agent::firewall::cli_status(&cfg).await? {
                    std::process::exit(1);
                }
                Ok(())
            }
            FirewallCmd::Apply {
                dry_run,
                egress_lock,
                preserve_ports,
            } => {
                gpudeck_agent::firewall::cli_apply(
                    &mut cfg,
                    &path,
                    &preserve_ports,
                    egress_lock,
                    dry_run,
                )
                .await
            }
            FirewallCmd::Remove => {
                let kind = gpudeck_agent::egress::detect_kind(&cfg.egress_lock);
                gpudeck_agent::firewall::remove(kind).await?;
                println!("GPU Deck firewall rules removed ({kind})");
                Ok(())
            }
        },
        Cmd::Update { action } => match action {
            UpdateCmd::Status => {
                gpudeck_agent::update::print_status(&cfg);
                Ok(())
            }
            UpdateCmd::InstallHelper { restart, dry_run } => {
                gpudeck_agent::update::install_helper(&cfg, &path, restart.as_deref(), dry_run)
                    .await
            }
            UpdateCmd::Apply { restart } => {
                let opts = gpudeck_agent::update::ApplyOptions::from_cli(restart.as_deref())?;
                let r = gpudeck_agent::update::run_applier(&cfg, &opts).await?;
                println!(
                    "update {}: {} ({} -> {}){}",
                    r.request_id,
                    r.outcome,
                    r.from_version,
                    r.version,
                    r.message.map(|m| format!(": {m}")).unwrap_or_default()
                );
                if r.outcome != "succeeded" {
                    std::process::exit(1);
                }
                Ok(())
            }
        },
        Cmd::Security { action } => match action {
            SecurityCmd::Audit => security_audit(&cfg).await,
            SecurityCmd::ResetPolicy => {
                let p = StatePaths::new(&cfg.state_dir).security_policy();
                match std::fs::remove_file(&p) {
                    Ok(()) => println!("removed {} (restart the agent; the next config_push brings a fresh policy)", p.display()),
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => println!("no policy at {}", p.display()),
                    Err(e) => bail!("remove {}: {e}", p.display()),
                }
                Ok(())
            }
        },
    }
}

async fn security_audit(cfg: &AgentConfig) -> Result<()> {
    use gpudeck_agent::audit;
    let paths = StatePaths::new(&cfg.state_dir);
    let policy = match (cfg.node_id, std::fs::read_to_string(paths.ca())) {
        (Some(node), Ok(ca)) => gpudeck_agent::security::load(&paths.security_policy(), &ca, node),
        _ => Ok(None),
    };
    println!("serving mode    {}", cfg.serving_mode().as_str());
    match &policy {
        Ok(Some(p)) => {
            println!(
                "policy          serial {} issued {} (signature verified)",
                p.serial, p.issued_at
            );
            println!(
                "pins            hub {} key(s), API layer {} key(s)",
                gpudeck_agent::security::hub_pins(Some(p)).len(),
                gpudeck_agent::security::api_layer_identity(Some(p)).0.len()
            );
            println!(
                "tunnel          {:?}",
                gpudeck_agent::security::tunnel_endpoints(Some(p))
            );
        }
        Ok(None) => println!("policy          none (hub and API-layer keys not pinned yet)"),
        Err(e) => println!("policy          INVALID: {e:#}"),
    }
    let kind = gpudeck_agent::egress::detect_kind(&cfg.egress_lock);
    match gpudeck_agent::firewall::loaded(kind).await {
        Ok(gpudeck_agent::firewall::Loaded::Plan(h)) => {
            println!("firewall        {kind}, plan {h} loaded")
        }
        Ok(other) => println!(
            "firewall        {kind}: {other:?} (run `gpudeck-agent firewall apply` as root)"
        ),
        Err(e) => println!("firewall        {kind}: unknown ({e:#})"),
    }
    println!();
    let range = gpudeck_agent::firewall::engine_range(&paths);
    let findings = audit::assess(
        cfg,
        range,
        &audit::engine_ports(&paths),
        &audit::listeners(),
    );
    let worst = audit::print_report(&findings);
    if worst >= audit::Level::Exposed {
        println!("\nresult: EXPOSED (see above)");
        std::process::exit(1);
    }
    println!(
        "\nresult: {}",
        if worst >= audit::Level::Warning {
            "warnings"
        } else {
            "nothing of GPU Deck's is reachable from the network except what the mode requires"
        }
    );
    Ok(())
}

async fn wait_for_signal() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        let mut term = signal(SignalKind::terminate()).expect("SIGTERM handler");
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = term.recv() => {}
        }
    }
    #[cfg(not(unix))]
    let _ = tokio::signal::ctrl_c().await;
}

async fn stop_engines(cfg: &AgentConfig, grace_seconds: u64) -> Result<()> {
    use gpudeck_agent::supervisor::{same_process, stop_detached};
    let paths = StatePaths::new(&cfg.state_dir);
    let mut targets: Vec<(String, u32, Option<u64>)> = vec![];
    for i in gpudeck_agent::state::load_instances(&paths) {
        // Router instances live in their router's process (stopped below).
        if i.args.engine_process_id.is_none() || i.overflow {
            if let Some(pid) = i.pid {
                targets.push((
                    format!("instance {}", i.args.instance_id),
                    pid,
                    i.start_token,
                ));
            }
        }
    }
    for p in gpudeck_agent::state::load_engine_processes(&paths) {
        if let Some(pid) = p.pid {
            targets.push((
                format!("engine process {}", p.args.engine_process_id),
                pid,
                p.start_token,
            ));
        }
    }
    let grace = std::time::Duration::from_secs(grace_seconds.max(1));
    let mut stopped = 0;
    for (what, pid, token) in targets {
        if !same_process(pid, token) {
            println!("{what}: not running (pid {pid})");
            continue;
        }
        println!("{what}: stopping pid {pid}");
        stop_detached(pid, token, grace).await;
        stopped += 1;
    }
    println!("{stopped} engine process(es) stopped");
    Ok(())
}

async fn status(cfg: &AgentConfig, path: &std::path::Path) -> Result<()> {
    println!("config      {}", path.display());
    println!("hub_url     {}", cfg.hub_url.as_deref().unwrap_or("-"));
    println!(
        "node_id     {}",
        cfg.node_id
            .map(|u| u.to_string())
            .unwrap_or_else(|| "- (not joined)".into())
    );
    println!("state_dir   {}", cfg.state_dir.display());
    println!("serving     {}", cfg.serving_mode().as_str());
    println!("cache_dir   {}", cfg.cache_dir().display());
    let paths = StatePaths::new(&cfg.state_dir);
    match gpudeck_agent::tls::Identity::load(&paths) {
        Ok(id) => println!(
            "certificate CN={} O={} not_after={} sha256={}",
            id.info.common_name.unwrap_or_default(),
            id.info.organization.unwrap_or_default(),
            id.info.not_after,
            id.info.fingerprint_sha256
        ),
        Err(e) => println!("certificate unavailable: {e:#}"),
    }
    let insts = gpudeck_agent::state::load_instances(&paths);
    println!("instances   {}", insts.len());
    let running = |pid: Option<u32>, token: Option<u64>| match pid {
        Some(p) if gpudeck_agent::supervisor::same_process(p, token) => format!("pid {p} running"),
        Some(p) => format!("pid {p} gone"),
        None => "no pid".into(),
    };
    for i in insts {
        println!(
            "  {} {} {} port {} ({})",
            i.args.instance_id,
            i.args.engine.name,
            i.args.model.served_model_name,
            i.port,
            running(i.pid, i.start_token)
        );
    }
    for p in gpudeck_agent::state::load_engine_processes(&paths) {
        println!(
            "  engine process {} {} port {} ({})",
            p.args.engine_process_id,
            p.args.engine.name,
            p.port,
            running(p.pid, p.start_token)
        );
    }
    let url = format!("http://127.0.0.1:{}/metrics", cfg.prom_port);
    let client = gpudeck_agent::tls::http_client_builder().build()?;
    match client
        .get(&url)
        .timeout(std::time::Duration::from_secs(2))
        .send()
        .await
    {
        Ok(r) => {
            let text = r.text().await.unwrap_or_default();
            let connected = text
                .lines()
                .find(|l| l.starts_with("gpudeck_agent_hub_connected"))
                .map(|l| l.ends_with(" 1"))
                .unwrap_or(false);
            if let Some(l) = text
                .lines()
                .find(|l| l.starts_with("gpudeck_agent_tunnel_connections"))
            {
                println!(
                    "tunnels     {} connected",
                    l.rsplit(' ').next().unwrap_or("0")
                );
            }
            println!(
                "agent       running (hub {})",
                if connected {
                    "connected"
                } else {
                    "disconnected"
                }
            );
        }
        Err(_) => println!("agent       not running (no answer on {url})"),
    }
    Ok(())
}
