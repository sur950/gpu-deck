//! Engines outlive the agent: a dedicated instance, a llama.cpp-style router with a model and a
//! router model run as its own server (`router_overflow`) keep running when the agent stops; the
//! next agent process re-adopts them (same pids and ports, no reload, no `engine_started`),
//! detects a crash of an adopted engine and restarts it on the same port, and relaunches an
//! engine that died while no agent ran on its previous port. Also: warm-up requests reach the
//! engine before `ready`, and loading a second model into a busy router does not restart it.
//! Fake hub over mTLS + the `gpudeck-fake-engine` binary.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use gpudeck_agent::backend::{DeviceReading, DeviceSource, GenericCatalog};
use gpudeck_agent::fakehub::FakeHub;
use gpudeck_agent::join::{join, JoinOptions};
use gpudeck_agent::supervisor::{kill_group, pid_alive};
use gpudeck_agent::{Agent, AgentConfig, RunOptions};
use gpudeck_protocol::*;
use serde_json::{json, Value};
use sha2::Digest;
use uuid::Uuid;

const T: Duration = Duration::from_secs(30);

struct NoGpus;

#[async_trait]
impl DeviceSource for NoGpus {
    async fn detect(&self) -> Vec<Device> {
        vec![]
    }
    async fn sample(&self) -> Vec<DeviceReading> {
        vec![]
    }
    fn backends(&self) -> Vec<String> {
        vec![]
    }
}

/// Kills every pid it was given when the test ends (also on panic).
struct Reaper(std::sync::Mutex<Vec<u32>>);

impl Reaper {
    fn add(&self, pid: u32) {
        self.0.lock().unwrap().push(pid);
    }
}

impl Drop for Reaper {
    fn drop(&mut self) {
        for pid in self.0.lock().unwrap().iter() {
            kill_group(*pid, libc::SIGKILL);
        }
    }
}

fn engine_bin() -> String {
    env!("CARGO_BIN_EXE_gpudeck-fake-engine").to_string()
}

fn engine_spec(name: &str, cmd: Vec<String>, router: bool) -> Value {
    json!({
        "engine_version_id": Uuid::new_v4(), "name": name, "version": "test", "runtime": "native",
        "launch_template": {"command": cmd, "router_mode": router},
        "health": {"path": "/health", "interval_seconds": 1, "startup_timeout_seconds": 20, "failure_threshold": 2},
        "metrics": {"path": null, "format": "none", "derive_from_proxy": true},
        "port_range": [11240, 11340]
    })
}

fn model_spec(file: &std::path::Path, sha: &str, served: &str) -> Value {
    json!({
        "model_id": Uuid::new_v4(), "model_version_id": Uuid::new_v4(), "name": served, "served_model_name": served,
        "type": "chat", "format": "gguf",
        "files": [{"model_file_id": Uuid::new_v4(), "path": file.file_name().unwrap().to_string_lossy(), "format": "gguf", "size_bytes": 8, "sha256": sha}],
        "cache_dir": "", "source": {"kind": "local_path", "repo_id": file.parent().unwrap().display().to_string()},
        "config": {}
    })
}

fn cmd(v: Value) -> Command {
    serde_json::from_value(v).expect("command json")
}

async fn ok(hub: &FakeHub, id: Uuid) -> JsonObject {
    let r = hub
        .wait_result(id, Duration::from_secs(60))
        .await
        .unwrap_or_else(|| panic!("no terminal result for {id}"));
    assert_eq!(
        r.status,
        ResultStatus::Succeeded,
        "command failed: {:?}",
        r.error
    );
    r.result.unwrap()
}

async fn start(
    cfg: &AgentConfig,
    path: &std::path::Path,
) -> (Arc<Agent>, tokio::task::JoinHandle<anyhow::Result<()>>) {
    let agent = Agent::new(
        cfg.clone(),
        path.to_path_buf(),
        Arc::new(NoGpus),
        Arc::new(GenericCatalog),
        RunOptions {
            proxy_listen: Some("127.0.0.1:0".parse().unwrap()),
            no_prom: true,
            ..Default::default()
        },
    )
    .unwrap();
    let a = agent.clone();
    let run = tokio::spawn(async move { a.run().await });
    (agent, run)
}

async fn stop(agent: &Arc<Agent>, run: tokio::task::JoinHandle<anyhow::Result<()>>) {
    agent.shutdown.cancel();
    let _ = tokio::time::timeout(Duration::from_secs(20), run).await;
}

async fn stats(port: u16) -> Value {
    reqwest::get(format!("http://127.0.0.1:{port}/fake/stats"))
        .await
        .unwrap()
        .json()
        .await
        .unwrap()
}

fn inst_pid(agent: &Agent, id: Uuid) -> Option<u32> {
    agent
        .instances
        .lock()
        .unwrap()
        .get(&id)
        .and_then(|i| i.proc.as_ref().and_then(|p| p.pid))
}

fn inst_state(agent: &Agent, id: Uuid) -> Option<(InstanceState, u16)> {
    agent
        .instances
        .lock()
        .unwrap()
        .get(&id)
        .map(|i| (i.state, i.port))
}

fn starts_since(hub: &FakeHub, from: usize) -> usize {
    hub.received()
        .iter()
        .skip(from)
        .filter(|e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::EngineStarted))
        .count()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn engines_survive_agent_restarts() {
    gpudeck_agent::tls::init_crypto();
    let reaper = Reaper(Default::default());
    let hub = FakeHub::start("127.0.0.1:0".parse().unwrap(), "gdj_restarttoken0123456789")
        .await
        .unwrap();
    let dir = tempfile::tempdir().unwrap();
    let cfg_path = dir.path().join("agent.toml");
    let mut cfg = AgentConfig {
        state_dir: dir.path().join("state"),
        router_idle_stop_seconds: 0,
        engine_warmup: "on".into(),
        prom_port: 0,
        ..Default::default()
    };
    let joined = join(
        &mut cfg,
        &cfg_path,
        JoinOptions {
            hub_url: hub.url(),
            token: "gdj_restarttoken0123456789".into(),
            name: Some("restart-node".into()),
            ca_fingerprint: Some(hub.ca_fingerprint()),
            proxy_port: 9443,
            labels: Default::default(),
        },
    )
    .await
    .unwrap();
    let node = joined.node_id;
    let mdir = tempfile::tempdir().unwrap();
    let mfile = mdir.path().join("tiny.gguf");
    std::fs::write(&mfile, b"GGUFtest").unwrap();
    let sha = hex::encode(sha2::Sha256::digest(b"GGUFtest"));
    let dev = Uuid::new_v4();
    let devices = json!([{"device_id": dev, "index": 0, "vendor": "apple"}]);

    // ---------------------------------------------------------------- agent A: start engines
    let (a, run_a) = start(&cfg, &cfg_path).await;
    hub.wait_for(0, T, |e| matches!(e.message, Message::Hello(_)))
        .await
        .expect("hello A");
    let dedicated = Uuid::new_v4();
    let dep = Uuid::new_v4();
    let l = hub.command(node, cmd(json!({"command": "load_model", "args": {
        "instance_id": dedicated, "deployment_id": dep,
        "engine": engine_spec("custom", vec![engine_bin(), "--port".into(), "{{port}}".into()], false),
        "model": model_spec(&mfile, &sha, "tiny-dedicated"),
        "devices": devices, "port": 0, "launch": {"context_size": 1024}
    }})));
    let r = ok(&hub, l).await;
    let d_port = r["port"].as_u64().unwrap() as u16;
    let d_pid = r["pid"].as_u64().unwrap() as u32;
    reaper.add(d_pid);
    // Warm-up (engine_warmup = on): two prompts (short + capped by the 1024 context) before ready.
    assert_eq!(
        stats(d_port).await["chat"],
        2,
        "warm-up requests reached the engine"
    );
    // Output goes to files under <state_dir>/logs (no pipe to break when the agent exits).
    let log = cfg.state_dir.join(format!("logs/{dedicated}.log"));
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert!(
        std::fs::read_to_string(&log)
            .unwrap()
            .contains("fake engine listening"),
        "engine stdout in {}",
        log.display()
    );

    let ep = Uuid::new_v4();
    let router_engine = engine_spec(
        "llama_cpp",
        vec![
            engine_bin(),
            "--port".into(),
            "{{port}}".into(),
            "--models-dir".into(),
            "{{model_dir}}".into(),
            "{{flags}}".into(),
        ],
        true,
    );
    let s = hub.command(node, cmd(json!({"command": "start_engine", "args": {"engine_process_id": ep, "engine": router_engine, "devices": devices, "port": 0, "launch": {}}})));
    let r_port = ok(&hub, s).await["port"].as_u64().unwrap() as u16;
    let m1 = Uuid::new_v4();
    let l = hub.command(node, cmd(json!({"command": "load_model", "args": {
        "instance_id": m1, "deployment_id": dep, "engine": router_engine, "model": model_spec(&mfile, &sha, "tiny-one"),
        "devices": devices, "port": 0, "engine_process_id": ep, "launch": {}
    }})));
    assert_eq!(ok(&hub, l).await["port"].as_u64().unwrap() as u16, r_port);
    let router_pid = a.engine_procs.lock().unwrap()[&ep]
        .proc
        .as_ref()
        .unwrap()
        .pid
        .unwrap();
    reaper.add(router_pid);
    // Warm-up through the router (model routed by name).
    assert_eq!(stats(r_port).await["chat"], 3);

    // A second model while the router hosts one: no router restart (it would reload tiny-one);
    // it runs as its own llama-server, reported under the router.
    let mark = hub.received().len();
    let m2 = Uuid::new_v4();
    let l = hub.command(node, cmd(json!({"command": "load_model", "args": {
        "instance_id": m2, "deployment_id": dep, "engine": router_engine, "model": model_spec(&mfile, &sha, "tiny-two"),
        "devices": devices, "port": 0, "engine_process_id": ep, "launch": {}
    }})));
    let r = ok(&hub, l).await;
    let m2_port = r["port"].as_u64().unwrap() as u16;
    let m2_pid = inst_pid(&a, m2).unwrap();
    reaper.add(m2_pid);
    assert_ne!(m2_port, r_port, "own llama-server");
    assert_eq!(r["engine_process_id"], json!(ep));
    assert_eq!(
        a.engine_procs.lock().unwrap()[&ep]
            .proc
            .as_ref()
            .unwrap()
            .pid,
        Some(router_pid),
        "router not restarted"
    );
    let m1_changed = hub.received().iter().skip(mark).any(|e| {
        matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::InstanceStateChanged && ev.instance_id == Some(m1))
    });
    assert!(!m1_changed, "the co-hosted model was not reloaded");
    let preset = std::fs::read_to_string(
        cfg.state_dir
            .join(format!("engines/llama_cpp/routers/{ep}/preset.ini")),
    )
    .unwrap();
    assert!(
        preset.contains("[tiny-one]") && !preset.contains("[tiny-two]"),
        "{preset}"
    );
    let reply: Value = reqwest::Client::new()
        .post(format!("http://127.0.0.1:{m2_port}/v1/chat/completions"))
        .json(&json!({"model": "tiny-two", "messages": []}))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(reply["model"], "tiny-two");
    let inv = hub.wait_for(mark, T, |e| matches!(&e.message, Message::Inventory(i) if i.engine_processes.iter().any(|p| p.engine_process_id == ep && p.instance_ids.contains(&m2)))).await;
    assert!(inv.is_some(), "overflow instance listed under its router");

    // ---------------------------------------------------------------- agent A stops
    stop(&a, run_a).await;
    for pid in [d_pid, router_pid, m2_pid] {
        assert!(
            pid_alive(pid),
            "engine {pid} kept running after the agent stopped"
        );
    }

    // ---------------------------------------------------------------- agent B re-adopts
    let mark = hub.received().len();
    let (b, run_b) = start(&cfg, &cfg_path).await;
    // Frames of agent A may still arrive: look only after B's hello.
    let hello_b = hub
        .wait_for(mark, T, |e| matches!(e.message, Message::Hello(_)))
        .await
        .expect("hello B");
    let mark = hub
        .received()
        .iter()
        .position(|e| e.id == hello_b.id)
        .unwrap();
    let inv = hub
        .wait_for(mark, T, |e| {
            matches!(&e.message, Message::Inventory(i) if [dedicated, m1, m2].iter().all(|id| i.instances.iter().any(|s| s.instance_id == *id && s.state == InstanceState::Ready)))
        })
        .await;
    assert!(
        inv.is_some(),
        "first inventory after the restart lists the running instances"
    );
    assert_eq!(inst_pid(&b, dedicated), Some(d_pid));
    assert_eq!(
        inst_state(&b, dedicated),
        Some((InstanceState::Ready, d_port))
    );
    assert_eq!(inst_pid(&b, m2), Some(m2_pid));
    assert_eq!(inst_state(&b, m2), Some((InstanceState::Ready, m2_port)));
    assert_eq!(inst_state(&b, m1), Some((InstanceState::Ready, r_port)));
    assert_eq!(
        b.engine_procs.lock().unwrap()[&ep]
            .proc
            .as_ref()
            .unwrap()
            .pid,
        Some(router_pid)
    );
    assert!(
        b.instances.lock().unwrap()[&dedicated]
            .proc
            .as_ref()
            .unwrap()
            .adopted
    );
    tokio::time::sleep(Duration::from_millis(500)).await;
    assert_eq!(starts_since(&hub, mark), 0, "nothing was relaunched");
    assert_eq!(
        stats(d_port).await["chat"],
        2,
        "no new warm-up: the engine was not restarted"
    );

    // A crash of an adopted engine is noticed (pid polling) and restarted on the same port.
    let mark = hub.received().len();
    unsafe { libc::kill(d_pid as i32, libc::SIGKILL) };
    hub.wait_for(mark, T, |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::EngineCrashed && ev.instance_id == Some(dedicated)))
        .await
        .expect("crash of the adopted engine detected");
    hub.wait_for(mark, Duration::from_secs(40), |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::InstanceStateChanged && ev.instance_id == Some(dedicated) && ev.state.as_ref().is_some_and(|s| s.to == "ready")))
        .await
        .expect("restarted after the crash");
    let d_pid2 = inst_pid(&b, dedicated).unwrap();
    reaper.add(d_pid2);
    assert_ne!(d_pid2, d_pid);
    assert_eq!(
        inst_state(&b, dedicated).unwrap().1,
        d_port,
        "same port after a restart"
    );

    // ---------------------------------------------------------------- engine dies while no agent runs
    stop(&b, run_b).await;
    kill_group(d_pid2, libc::SIGKILL);
    for _ in 0..50 {
        if !pid_alive(d_pid2) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let mark = hub.received().len();
    let (c, run_c) = start(&cfg, &cfg_path).await;
    hub.wait_for(mark, Duration::from_secs(40), |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::InstanceStateChanged && ev.instance_id == Some(dedicated) && ev.state.as_ref().is_some_and(|s| s.to == "ready")))
        .await
        .expect("relaunched");
    let d_pid3 = inst_pid(&c, dedicated).unwrap();
    reaper.add(d_pid3);
    assert_ne!(d_pid3, d_pid2);
    assert_eq!(
        inst_state(&c, dedicated).unwrap().1,
        d_port,
        "relaunched on its previous port"
    );
    // The others were adopted again.
    assert_eq!(inst_pid(&c, m2), Some(m2_pid));
    assert_eq!(
        c.engine_procs.lock().unwrap()[&ep]
            .proc
            .as_ref()
            .unwrap()
            .pid,
        Some(router_pid)
    );
    assert_eq!(
        starts_since(&hub, mark),
        1,
        "only the dead engine was started"
    );

    // ---------------------------------------------------------------- router dies while no agent runs
    // Relaunched with the models it had registered: reloading tiny-one goes through the
    // router's load API (one router start, no restart for the model, no overflow).
    let u = hub.command(
        node,
        cmd(json!({"command": "unload_model", "args": {"instance_id": m2, "force": true}})),
    );
    ok(&hub, u).await;
    stop(&c, run_c).await;
    kill_group(router_pid, libc::SIGKILL);
    for _ in 0..50 {
        if !pid_alive(router_pid) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let mark = hub.received().len();
    let (d, run_d) = start(&cfg, &cfg_path).await;
    hub.wait_for(mark, Duration::from_secs(40), |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::InstanceStateChanged && ev.instance_id == Some(m1) && ev.state.as_ref().is_some_and(|s| s.to == "ready")))
        .await
        .expect("router model reloaded");
    let router_pid2 = d.engine_procs.lock().unwrap()[&ep]
        .proc
        .as_ref()
        .unwrap()
        .pid
        .unwrap();
    reaper.add(router_pid2);
    assert_ne!(router_pid2, router_pid);
    assert_eq!(
        d.engine_procs.lock().unwrap()[&ep].port,
        r_port,
        "router relaunched on its previous port"
    );
    let router_starts = hub
        .received()
        .iter()
        .skip(mark)
        .filter(|e| {
            matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::EngineStarted && ev.engine_process_id == Some(ep) && ev.instance_id.is_none())
        })
        .count();
    assert_eq!(
        router_starts, 1,
        "no second router restart to register the model"
    );
    assert_eq!(
        inst_pid(&d, dedicated),
        Some(d_pid3),
        "dedicated adopted again"
    );

    // ---------------------------------------------------------------- cleanup
    stop(&d, run_d).await;
    d.stop_all_engines().await;
    for pid in [d_pid3, router_pid2, m2_pid] {
        for _ in 0..50 {
            if !pid_alive(pid) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        assert!(!pid_alive(pid), "engine {pid} stopped");
    }
}
