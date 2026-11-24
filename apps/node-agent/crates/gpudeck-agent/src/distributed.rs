//! Cross-node pipeline parallel (`launch.distributed`, `protocol.md` §9). One `load_model` per
//! node, same `instance_id`; rank 0 (head) serves the HTTP API, ranks > 0 are headless workers.
//!
//! [`prepare`] folds the group into the launch before the command line is built (so both the
//! agent's template renderer and the engine adapters see it): engine flags (vLLM `--nnodes/--node-rank/
//! --master-addr/--master-port[/--headless]`, SGLang `--nnodes/--node-rank/--dist-init-addr`,
//! llama.cpp head `--rpc <peers>`) and the rendezvous env on every rank. llama.cpp workers do not
//! run `llama-server` at all but `rpc-server` ([`llama_rpc_worker`]).

use std::net::{IpAddr, SocketAddr};
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{anyhow, Result};
use gpudeck_protocol::{DistributedSpec, EngineName, EngineRuntime};
use serde_json::Value;

use crate::backend::{EngineLaunch, ProcSpec};

/// How a worker rank proves it is up.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WorkerReady {
    /// llama.cpp `rpc-server`: the RPC port accepts TCP.
    Tcp(SocketAddr),
    /// Headless engines: the process is still alive after this long.
    AliveFor(Duration),
}

pub fn is_worker(l: &gpudeck_protocol::LaunchSpec) -> bool {
    l.distributed.as_ref().is_some_and(|d| d.rank > 0)
}

/// The address this node uses for group traffic.
pub fn bind_address(d: &DistributedSpec, proxy_address: Option<&str>, ips: &[String]) -> String {
    d.bind_address
        .clone()
        .or_else(|| {
            proxy_address
                .filter(|a| a.parse::<IpAddr>().is_ok())
                .map(str::to_string)
        })
        .or_else(|| {
            ips.iter()
                .find(|a| {
                    a.parse::<IpAddr>()
                        .map(|ip| ip.is_ipv4() && !ip.is_loopback())
                        .unwrap_or(false)
                })
                .cloned()
        })
        .unwrap_or_else(|| "127.0.0.1".into())
}

/// `addr:rpc_port` of every worker rank (llama.cpp head `--rpc`).
pub fn rpc_peers(d: &DistributedSpec) -> Vec<String> {
    let mut peers: Vec<_> = d.others().filter(|p| p.rank > 0).collect();
    peers.sort_by_key(|p| p.rank);
    peers
        .into_iter()
        .map(|p| {
            format!(
                "{}:{}",
                p.address,
                p.rpc_port.unwrap_or(DistributedSpec::DEFAULT_RPC_PORT)
            )
        })
        .collect()
}

/// Peer addresses (for the egress lock).
pub fn peer_addresses(d: &DistributedSpec) -> Vec<String> {
    let mut v: Vec<String> = d.others().map(|p| p.address.clone()).collect();
    if !d.is_head() && !v.contains(&d.master_addr) {
        v.push(d.master_addr.clone());
    }
    v.sort();
    v.dedup();
    v
}

/// Rendezvous env for every rank.
pub fn env(d: &DistributedSpec) -> Vec<(String, String)> {
    let mut e = vec![
        ("MASTER_ADDR".to_string(), d.master_addr.clone()),
        ("MASTER_PORT".into(), d.master_port.to_string()),
        ("WORLD_SIZE".into(), d.world_size.to_string()),
        ("RANK".into(), d.rank.to_string()),
        ("NODE_RANK".into(), d.rank.to_string()),
        (
            "GPUDECK_DISTRIBUTED".into(),
            serde_json::to_string(d).unwrap_or_default(),
        ),
    ];
    if let Some(i) = &d.nccl_socket_ifname {
        e.push(("NCCL_SOCKET_IFNAME".into(), i.clone()));
        e.push(("GLOO_SOCKET_IFNAME".into(), i.clone()));
    }
    e
}

fn set_flag(l: &mut EngineLaunch, key: &str, v: Value) {
    let present = l
        .launch
        .flags
        .keys()
        .any(|k| k.trim_start_matches('-') == key);
    if !present {
        l.launch.flags.insert(key.to_string(), v);
    }
}

/// Validate the group and fold it into `l` (flags + env). No-op without `launch.distributed`.
pub fn prepare(l: &mut EngineLaunch) -> Result<()> {
    let Some(d) = l.launch.distributed.clone() else {
        return Ok(());
    };
    if d.world_size < 2 || d.rank >= d.world_size {
        return Err(anyhow!(
            "distributed: rank {} not in world_size {}",
            d.rank,
            d.world_size
        ));
    }
    if l.router.is_some() {
        return Err(anyhow!(
            "distributed launches cannot use a shared router process"
        ));
    }
    for (k, v) in env(&d) {
        l.launch.env.entry(k).or_insert(v);
    }
    match l.engine.name {
        EngineName::Vllm | EngineName::VllmOmni => {
            set_flag(l, "nnodes", d.world_size.into());
            set_flag(l, "node-rank", d.rank.into());
            set_flag(l, "master-addr", d.master_addr.clone().into());
            set_flag(l, "master-port", d.master_port.into());
            if l.launch.pp_size <= 1 {
                l.launch.pp_size = d.world_size;
            }
            if !d.is_head() {
                set_flag(l, "headless", true.into());
            }
        }
        EngineName::Sglang | EngineName::SglangDiffusion => {
            set_flag(l, "nnodes", d.world_size.into());
            set_flag(l, "node-rank", d.rank.into());
            set_flag(
                l,
                "dist-init-addr",
                format!("{}:{}", d.master_addr, d.master_port).into(),
            );
            if l.launch.pp_size <= 1 {
                l.launch.pp_size = d.world_size;
            }
        }
        EngineName::LlamaCpp if d.is_head() => {
            let peers = rpc_peers(&d);
            if peers.is_empty() {
                return Err(anyhow!(
                    "distributed llama.cpp head needs at least one worker peer"
                ));
            }
            set_flag(l, "rpc", peers.join(",").into());
        }
        _ => {}
    }
    Ok(())
}

/// llama.cpp's RPC worker binary: `ggml-rpc-server` in current releases (b12500 ships it under
/// that name), `rpc-server` in older builds.
pub const RPC_SERVER_NAMES: [&str; 2] = ["ggml-rpc-server", "rpc-server"];

/// The RPC worker binary next to the llama-server binary (or on PATH).
pub fn rpc_server_executable(llama_server: Option<&Path>) -> Option<PathBuf> {
    if let Some(dir) = llama_server.and_then(Path::parent) {
        for name in RPC_SERVER_NAMES {
            let p = dir.join(name);
            if p.is_file() {
                return Some(p);
            }
        }
    }
    RPC_SERVER_NAMES
        .iter()
        .find_map(|name| crate::template::resolve_executable(name, None))
}

/// llama.cpp worker rank: `ggml-rpc-server --host <bind> --port <rpc_port>` with the assigned devices
/// visible. Returns the process and how to tell it is ready.
pub fn llama_rpc_worker(
    l: &EngineLaunch,
    d: &DistributedSpec,
    bind: &str,
    configured_server: Option<&str>,
) -> Result<(ProcSpec, WorkerReady)> {
    if l.engine.runtime == EngineRuntime::Container {
        return Err(anyhow!("llama.cpp RPC workers run natively only"));
    }
    let server = l
        .executable
        .clone()
        .or_else(|| crate::template::resolve_executable("llama-server", configured_server));
    let exe = rpc_server_executable(server.as_deref()).ok_or_else(|| anyhow!("ggml-rpc-server / rpc-server not found next to llama-server or on PATH (build llama.cpp with -DGGML_RPC=ON)"))?;
    let port = d.rpc_port.unwrap_or(DistributedSpec::DEFAULT_RPC_PORT);
    let mut env: std::collections::BTreeMap<String, String> =
        crate::template::device_env(&l.devices);
    for (k, v) in &l.launch.env {
        env.insert(k.clone(), v.clone());
    }
    if let Some(lib) = l
        .library_dir
        .clone()
        .or_else(|| crate::template::library_dir_for(&exe))
    {
        let key = if cfg!(target_os = "macos") {
            "DYLD_LIBRARY_PATH"
        } else {
            "LD_LIBRARY_PATH"
        };
        env.insert(key.into(), lib.display().to_string());
    }
    let ip: IpAddr = bind
        .parse()
        .map_err(|_| anyhow!("distributed bind_address {bind:?} is not an IP"))?;
    let probe = if ip.is_unspecified() {
        SocketAddr::from(([127, 0, 0, 1], port))
    } else {
        SocketAddr::new(ip, port)
    };
    let args = vec![
        "--host".to_string(),
        bind.to_string(),
        "--port".into(),
        port.to_string(),
    ];
    Ok((
        ProcSpec {
            program: exe.display().to_string(),
            args,
            env,
            cwd: None,
            container: None,
        },
        WorkerReady::Tcp(probe),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpudeck_protocol::{
        DistributedPeer, EngineSpec, HealthConfig, LaunchSpec, LaunchTemplate, MetricsConfig,
    };
    use uuid::Uuid;

    fn spec(rank: u32) -> DistributedSpec {
        DistributedSpec {
            group_id: None,
            rank,
            world_size: 3,
            master_addr: "10.0.0.5".into(),
            master_port: 29500,
            bind_address: None,
            rpc_port: Some(50060),
            nccl_socket_ifname: Some("eth1".into()),
            peers: vec![
                DistributedPeer {
                    rank: 0,
                    node_id: Uuid::new_v4(),
                    address: "10.0.0.5".into(),
                    rpc_port: None,
                    device_ids: vec![],
                },
                DistributedPeer {
                    rank: 2,
                    node_id: Uuid::new_v4(),
                    address: "10.0.0.7".into(),
                    rpc_port: None,
                    device_ids: vec![],
                },
                DistributedPeer {
                    rank: 1,
                    node_id: Uuid::new_v4(),
                    address: "10.0.0.6".into(),
                    rpc_port: Some(50060),
                    device_ids: vec![],
                },
            ],
        }
    }

    fn launch(name: EngineName, d: DistributedSpec) -> EngineLaunch {
        EngineLaunch {
            engine: EngineSpec {
                engine_version_id: Uuid::new_v4(),
                name,
                version: "1".into(),
                runtime: EngineRuntime::Native,
                image: None,
                artifact: None,
                launch_template: LaunchTemplate {
                    command: vec!["srv".into(), "{{flags}}".into()],
                    env: Default::default(),
                    working_dir: None,
                    router_mode: false,
                },
                health: HealthConfig::default(),
                metrics: MetricsConfig::default(),
                port_range: [10240, 10340],
                capabilities: Default::default(),
            },
            launch: LaunchSpec {
                distributed: Some(d),
                ..Default::default()
            },
            model: None,
            served_model_name: Some("m".into()),
            model_path: Some("/m/x.gguf".into()),
            model_paths: vec![],
            model_dir: None,
            draft_model_path: None,
            port: 10240,
            devices: vec![],
            executable: None,
            library_dir: None,
            cache_dir: "/c".into(),
            router: None,
            instance_id: Some(Uuid::new_v4()),
            engine_process_id: None,
        }
    }

    #[test]
    fn vllm_and_sglang_flags() {
        let mut l = launch(EngineName::Vllm, spec(1));
        prepare(&mut l).unwrap();
        let f = &l.launch.flags;
        assert_eq!(f["nnodes"], 3);
        assert_eq!(f["node-rank"], 1);
        assert_eq!(f["master-addr"], "10.0.0.5");
        assert_eq!(f["master-port"], 29500);
        assert_eq!(f["headless"], true);
        assert_eq!(l.launch.pp_size, 3);
        assert_eq!(l.launch.env["RANK"], "1");
        assert_eq!(l.launch.env["NCCL_SOCKET_IFNAME"], "eth1");
        assert!(l.launch.env["GPUDECK_DISTRIBUTED"].contains("29500"));
        let args = crate::template::render_args(&l.engine.launch_template.command, &l, "127.0.0.1")
            .unwrap();
        assert!(
            args.windows(2).any(|w| w == ["--node-rank", "1"])
                && args.contains(&"--headless".to_string()),
            "{args:?}"
        );

        let mut l = launch(EngineName::Sglang, spec(0));
        l.launch.flags.insert("--nnodes".into(), 9.into());
        prepare(&mut l).unwrap();
        assert_eq!(l.launch.flags["--nnodes"], 9, "operator flags win");
        assert!(!l.launch.flags.contains_key("nnodes"));
        assert_eq!(l.launch.flags["dist-init-addr"], "10.0.0.5:29500");
        assert!(!l.launch.flags.contains_key("headless"));
    }

    #[test]
    fn llama_rpc() {
        let mut l = launch(EngineName::LlamaCpp, spec(0));
        prepare(&mut l).unwrap();
        assert_eq!(l.launch.flags["rpc"], "10.0.0.6:50060,10.0.0.7:50052");
        assert_eq!(peer_addresses(&spec(0)), vec!["10.0.0.6", "10.0.0.7"]);
        assert_eq!(peer_addresses(&spec(2)), vec!["10.0.0.5", "10.0.0.6"]);
        assert!(is_worker(&launch(EngineName::LlamaCpp, spec(2)).launch));
        // Worker: rpc-server from a fake llama.cpp build dir.
        let dir = tempfile::tempdir().unwrap();
        for b in ["llama-server", "rpc-server"] {
            std::fs::write(dir.path().join(b), "#!/bin/sh\n").unwrap();
        }
        let mut w = launch(EngineName::LlamaCpp, spec(1));
        w.executable = Some(dir.path().join("llama-server"));
        let d = w.launch.distributed.clone().unwrap();
        let (p, ready) = llama_rpc_worker(&w, &d, "10.0.0.6", None).unwrap();
        assert!(p.program.ends_with("rpc-server"));
        assert_eq!(p.args, vec!["--host", "10.0.0.6", "--port", "50060"]);
        assert_eq!(ready, WorkerReady::Tcp("10.0.0.6:50060".parse().unwrap()));
        assert_eq!(bind_address(&d, Some("10.9.9.9"), &[]), "10.9.9.9");
        assert_eq!(
            bind_address(&d, None, &["fe80::1".into(), "192.168.1.4".into()]),
            "192.168.1.4"
        );
        // Invalid groups.
        let mut bad = spec(3);
        bad.world_size = 3;
        assert!(prepare(&mut launch(EngineName::Vllm, bad)).is_err());
        let mut lonely = spec(0);
        lonely.peers.retain(|p| p.rank == 0);
        assert!(prepare(&mut launch(EngineName::LlamaCpp, lonely)).is_err());
    }

    const RPC_HELP: &str =
        include_str!("../tests/fixtures/llama_cpp_b12500/ggml-rpc-server-help.txt");
    const SERVER_HELP: &str =
        include_str!("../tests/fixtures/llama_cpp_b12500/llama-server-help.txt");

    /// Every `--flag` in `args` is listed in the captured `--help` of the pinned release.
    fn assert_flags_known(args: &[String], help: &str) {
        for flag in args.iter().filter(|a| a.starts_with("--")) {
            let flag = flag.split('=').next().unwrap();
            let known = help
                .lines()
                .any(|line| line.split([' ', ',']).any(|tok| tok == flag));
            assert!(known, "{flag} is not a b12500 flag");
        }
    }

    /// Cross-node pipeline parallel with the pinned llama.cpp b12500: the head turns the
    /// router-mode template into a single-model server with `--rpc`, workers run
    /// `ggml-rpc-server` (the b12500 name); every flag exists in that release's `--help`
    /// (fixtures captured from the release binaries).
    #[test]
    fn llama_b12500_pipeline_parallel_flags() {
        let mut head = launch(EngineName::LlamaCpp, spec(0));
        // The seeded b12500 template (app/services/engines/seed.py) + its default flags.
        head.engine.launch_template = LaunchTemplate {
            command: [
                "llama-server",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "--models-dir",
                "{{model_dir}}",
                "--metrics",
                "--slots",
                "{{flags}}",
            ]
            .map(String::from)
            .to_vec(),
            env: Default::default(),
            working_dir: None,
            router_mode: true,
        };
        for (k, v) in [
            ("jinja", Value::Bool(true)),
            ("cont-batching", Value::Bool(true)),
            ("flash-attn", Value::from("auto")),
        ] {
            head.launch.flags.insert(k.into(), v);
        }
        head.launch.context_size = Some(4096);
        head.launch.max_concurrency = Some(4);
        head.model_dir = Some("/m".into());
        prepare(&mut head).unwrap();
        let dir = tempfile::tempdir().unwrap();
        for b in ["llama-server", "ggml-rpc-server"] {
            std::fs::write(dir.path().join(b), "#!/bin/sh\n").unwrap();
        }
        head.executable = Some(dir.path().join("llama-server"));
        let p = crate::template::build_proc(&head, None, None).unwrap();
        let args = p.args.join(" ");
        assert!(!args.contains("--models-dir"), "{args}");
        assert!(args.contains("--model /m/x.gguf"), "{args}");
        assert!(
            args.contains("--rpc 10.0.0.6:50060,10.0.0.7:50052"),
            "{args}"
        );
        assert!(
            args.contains("--alias m") && args.contains("--ctx-size 4096"),
            "{args}"
        );
        assert_flags_known(&p.args, SERVER_HELP);

        let mut w = launch(EngineName::LlamaCpp, spec(1));
        w.executable = Some(dir.path().join("llama-server"));
        let d = w.launch.distributed.clone().unwrap();
        let (p, _) = llama_rpc_worker(&w, &d, "10.0.0.6", None).unwrap();
        assert!(p.program.ends_with("/ggml-rpc-server"), "{}", p.program);
        assert_flags_known(&p.args, RPC_HELP);
        assert!(RPC_HELP.contains("default: 50052"));
        assert_eq!(DistributedSpec::DEFAULT_RPC_PORT, 50052);
    }
}
