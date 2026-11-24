//! `gpudeck-agent` CLI: `join`, `run`, `status`, `stop-engines`, `version`.

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
        /// SHA-256 of the hub CA certificate (pins the CA; otherwise trust on first use).
        #[arg(long)]
        ca_fingerprint: Option<String>,
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
    /// Print agent and protocol versions.
    Version,
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
                "gpudeck-agent {AGENT_VERSION} (protocol v{})",
                gpudeck_protocol::PROTOCOL_VERSION
            );
            Ok(())
        }
        Cmd::Join {
            token,
            url,
            name,
            ca_fingerprint,
            labels,
            proxy_port,
            state_dir,
        } => {
            if let Some(s) = state_dir {
                cfg.state_dir = s;
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
    }
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
    match reqwest::Client::new()
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
