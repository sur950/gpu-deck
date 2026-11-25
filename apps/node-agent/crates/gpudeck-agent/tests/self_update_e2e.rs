//! Agent self-update end to end (`protocol.md` §14) with the real `gpudeck-agent` binary run as
//! a service (a small supervisor restarts it like systemd / launchd) against the fake hub over
//! mTLS:
//!
//! - the release key reaches the node in the CA-signed security policy at join;
//! - `stage_update` downloads from the hub with the download token, verifies the signed manifest
//!   and the sha256, checks the binary runs; a wrong token or a foreign signing key fails;
//! - `apply_update` of a version that never reconnects is rolled back automatically and the
//!   previous agent reports `rolled_back`;
//! - `apply_update` of a working version (the same build reporting 9.9.9 through the debug-only
//!   `GPUDECK_AGENT_TEST_VERSION`) swaps the binary, the new agent re-adopts the running engine
//!   (same pid, no `engine_started`), reconnects, passes the health check and reports
//!   `succeeded` with every step;
//! - N-1: against a hub that does not list `agent_updates` the agent sends none of the new
//!   fields (an older hub would reject them).

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use base64::Engine as _;
use gpudeck_agent::backend::{DeviceReading, DeviceSource, GenericCatalog};
use gpudeck_agent::fakehub::FakeHub;
use gpudeck_agent::join::{join, JoinOptions};
use gpudeck_agent::update::build_target;
use gpudeck_agent::{Agent, AgentConfig, RunOptions};
use gpudeck_protocol::*;
use ring::signature::KeyPair;
use serde_json::{json, Value};
use sha2::Digest;
use uuid::Uuid;

const T: Duration = Duration::from_secs(30);

struct Key {
    pair: ring::signature::Ed25519KeyPair,
    public: String,
}

fn key() -> Key {
    let rng = ring::rand::SystemRandom::new();
    let pkcs8 = ring::signature::Ed25519KeyPair::generate_pkcs8(&rng).unwrap();
    let pair = ring::signature::Ed25519KeyPair::from_pkcs8(pkcs8.as_ref()).unwrap();
    let public = base64::engine::general_purpose::STANDARD.encode(pair.public_key().as_ref());
    Key { pair, public }
}

fn signed_manifest(signer: &Key, version: &str, bytes: &[u8]) -> SignedReleaseManifest {
    let manifest = ReleaseManifest {
        schema: 1,
        product: "gpudeck-agent".into(),
        version: version.into(),
        protocol_version: 1,
        created_at: Some(now()),
        artifacts: vec![ReleaseArtifact {
            target: build_target(),
            file: Some(format!("gpudeck-agent-{version}-{}.tar.gz", build_target())),
            size_bytes: None,
            sha256: None,
            binary_sha256: hex::encode(sha2::Sha256::digest(bytes)),
            binary_size_bytes: bytes.len() as u64,
        }],
    };
    let doc = serde_json::to_vec(&manifest).unwrap();
    let b64 = base64::engine::general_purpose::STANDARD;
    SignedReleaseManifest {
        manifest: b64.encode(&doc),
        signature: b64.encode(signer.pair.sign(&doc).as_ref()),
        algorithm: "ed25519".into(),
    }
}

fn copy_exec(from: &Path, to: &Path) {
    use std::os::unix::fs::PermissionsExt;
    std::fs::copy(from, to).unwrap();
    std::fs::set_permissions(to, std::fs::Permissions::from_mode(0o755)).unwrap();
}

/// Runs `bin --config <cfg> run` and restarts it whenever it exits.
struct Supervisor {
    stop: Arc<AtomicBool>,
    pids: Arc<Mutex<Vec<u32>>>,
    task: tokio::task::JoinHandle<()>,
}

impl Supervisor {
    fn start(bin: PathBuf, cfg: PathBuf, log: PathBuf) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let pids = Arc::new(Mutex::new(vec![]));
        let (s, p) = (stop.clone(), pids.clone());
        let task = tokio::spawn(async move {
            while !s.load(Ordering::SeqCst) {
                let out = std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&log)
                    .unwrap();
                let child = tokio::process::Command::new(&bin)
                    .arg("--config")
                    .arg(&cfg)
                    .arg("run")
                    .env("GPUDECK_AGENT_SUPERVISED", "1")
                    .env_remove("GPUDECK_AGENT_TEST_VERSION")
                    .env_remove("KUBERNETES_SERVICE_HOST")
                    .env_remove("XPC_SERVICE_NAME")
                    .env("RUST_LOG", "info")
                    .stdout(out.try_clone().unwrap())
                    .stderr(out)
                    .kill_on_drop(true)
                    .spawn();
                if let Ok(mut c) = child {
                    if let Some(pid) = c.id() {
                        p.lock().unwrap().push(pid);
                    }
                    loop {
                        tokio::select! {
                            _ = c.wait() => break,
                            _ = tokio::time::sleep(Duration::from_millis(100)) => {
                                if s.load(Ordering::SeqCst) {
                                    let _ = c.kill().await;
                                    return;
                                }
                            }
                        }
                    }
                }
                tokio::time::sleep(Duration::from_millis(200)).await;
            }
        });
        Self { stop, pids, task }
    }

    async fn stop(self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = tokio::time::timeout(Duration::from_secs(10), self.task).await;
        for pid in self.pids.lock().unwrap().iter() {
            // SAFETY: test-owned child pids.
            unsafe { libc::kill(*pid as libc::pid_t, libc::SIGKILL) };
        }
    }
}

fn cmd(v: Value) -> Command {
    serde_json::from_value(v).expect("command json")
}

fn update_event(e: &Envelope, phase: &str) -> bool {
    matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::AgentUpdate
        && ev.details.as_ref().and_then(|d| d.get("phase")).and_then(|p| p.as_str()) == Some(phase))
}

fn engine_started_since(hub: &FakeHub, from: usize) -> usize {
    hub.received()
        .iter()
        .skip(from)
        .filter(|e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::EngineStarted))
        .count()
}

fn pid_alive(pid: u32) -> bool {
    // SAFETY: signal 0 only checks existence.
    unsafe { libc::kill(pid as libc::pid_t, 0) == 0 }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn stage_apply_rollback_and_succeed_with_the_real_agent() {
    gpudeck_agent::tls::init_crypto();
    let token = "gdj_selfupdatetoken0123456789";
    let hub = FakeHub::start("127.0.0.1:0".parse().unwrap(), token)
        .await
        .unwrap();
    let release = key();
    // The release key travels in the CA-signed security policy (join response + config_push).
    *hub.state.policy_template.lock().unwrap() = Some(SecurityPolicy {
        release: Some(ReleasePolicy {
            public_keys: vec![release.public.clone()],
        }),
        ..Default::default()
    });
    let dir = tempfile::tempdir().unwrap();
    let bin_dir = dir.path().join("bin");
    std::fs::create_dir_all(&bin_dir).unwrap();
    let built = PathBuf::from(env!("CARGO_BIN_EXE_gpudeck-agent"));
    let bin = bin_dir.join("gpudeck-agent");
    let real = bin_dir.join("agent-real");
    copy_exec(&built, &bin);
    copy_exec(&built, &real);
    let original = std::fs::read(&bin).unwrap();
    let cfg_path = dir.path().join("agent.toml");
    let state = dir.path().join("state");
    let mut cfg = AgentConfig {
        firewall: "off".into(),
        state_dir: state.clone(),
        prom_port: 0,
        update_helper: "self".into(),
        engine_warmup: "off".into(),
        ..Default::default()
    };
    let joined = join(
        &mut cfg,
        &cfg_path,
        JoinOptions {
            hub_url: hub.url(),
            token: token.into(),
            name: Some("update-node".into()),
            ca_fingerprint: Some(hub.ca_fingerprint()),
            proxy_port: 9443,
            labels: Default::default(),
        },
    )
    .await
    .unwrap();
    let node = joined.node_id;
    let sup = Supervisor::start(bin.clone(), cfg_path.clone(), dir.path().join("agent.log"));

    // A fresh node does not know the hub's features yet: no agent_update in its first hello,
    // but in the inventory that follows hello_ack.
    let hello = hub
        .wait_for(0, T, |e| matches!(e.message, Message::Hello(_)))
        .await
        .expect("hello");
    let Message::Hello(h) = &hello.message else {
        unreachable!()
    };
    assert!(h.agent_update.is_none());
    assert!(h.capabilities.commands.contains(&CommandName::StageUpdate));
    let inv = hub
        .wait_for(
            0,
            T,
            |e| matches!(&e.message, Message::Inventory(i) if i.agent_update.is_some()),
        )
        .await
        .expect("inventory with agent_update");
    let Message::Inventory(i) = &inv.message else {
        unreachable!()
    };
    let st = i.agent_update.clone().unwrap();
    assert_eq!(st.mode, AgentUpdateMode::SelfUpdate, "{st:?}");
    assert_eq!(
        st.release_keys, 1,
        "release key pinned from the signed policy"
    );
    assert_eq!(st.target.as_deref(), Some(build_target().as_str()));

    // A running engine (fake engine binary) that must survive every update.
    let engine_bin = env!("CARGO_BIN_EXE_gpudeck-fake-engine").to_string();
    let mdir = tempfile::tempdir().unwrap();
    let mfile = mdir.path().join("tiny.gguf");
    std::fs::write(&mfile, b"GGUFtest").unwrap();
    let sha = hex::encode(sha2::Sha256::digest(b"GGUFtest"));
    let inst = Uuid::new_v4();
    let l = hub.command(node, cmd(json!({"command": "load_model", "args": {
        "instance_id": inst, "deployment_id": Uuid::new_v4(),
        "engine": {
            "engine_version_id": Uuid::new_v4(), "name": "custom", "version": "test", "runtime": "native",
            "launch_template": {"command": [engine_bin, "--port", "{{port}}"], "router_mode": false},
            "health": {"path": "/health", "interval_seconds": 1, "startup_timeout_seconds": 20, "failure_threshold": 2},
            "metrics": {"path": null, "format": "none", "derive_from_proxy": true},
            "port_range": [11640, 11740]
        },
        "model": {
            "model_id": Uuid::new_v4(), "model_version_id": Uuid::new_v4(), "name": "tiny", "served_model_name": "tiny",
            "type": "chat", "format": "gguf",
            "files": [{"model_file_id": Uuid::new_v4(), "path": "tiny.gguf", "format": "gguf", "size_bytes": 8, "sha256": sha}],
            "cache_dir": "", "source": {"kind": "local_path", "repo_id": mdir.path().display().to_string()},
            "config": {}
        },
        "devices": [{"device_id": Uuid::new_v4(), "index": 0, "vendor": "apple"}],
        "port": 0, "launch": {"context_size": 1024}
    }})));
    let r = hub.wait_result(l, Duration::from_secs(60)).await.unwrap();
    assert_eq!(r.status, ResultStatus::Succeeded, "{:?}", r.error);
    let engine_pid = r.result.as_ref().unwrap()["pid"].as_u64().unwrap() as u32;
    let _engine = KillOnDrop(engine_pid);

    let target = build_target();
    let stage = |version: &str, manifest: SignedReleaseManifest, tok: &str| {
        cmd(
            json!({"command": "stage_update", "timeout_seconds": 300, "args": {
                "version": version, "target": target, "manifest": manifest, "download_token": tok
            }}),
        )
    };
    let state_s = state.display().to_string();

    // ---------------------------------------------------------- staging refusals
    let bad = format!(
        "#!/bin/sh\ncase \"$1\" in version) echo \"gpudeck-agent 9.9.8 (protocol v1)\"; exit 0;; esac\n\
         U=\"{state_s}/update\"\n\
         printf '{{\"pid\":%s,\"version\":\"9.9.8\",\"started_at\":\"%s\"}}' $$ \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" > \"$U/.r.$$\" && mv \"$U/.r.$$\" \"$U/running.json\"\n\
         trap 'exit 0' TERM\nwhile true; do sleep 1 & wait $!; done\n"
    )
    .into_bytes();
    hub.serve_update("9.9.8", &target, "tok-9.9.8-0123456789", bad.clone());
    let id = hub.command(
        node,
        stage(
            "9.9.8",
            signed_manifest(&release, "9.9.8", &bad),
            "wrong-token-0123456789",
        ),
    );
    let r = hub.wait_result(id, T).await.unwrap();
    assert_eq!(r.error.unwrap().code, "artifact_download_failed");
    let id = hub.command(
        node,
        stage(
            "9.9.8",
            signed_manifest(&key(), "9.9.8", &bad),
            "tok-9.9.8-0123456789",
        ),
    );
    let r = hub.wait_result(id, T).await.unwrap();
    assert_eq!(r.error.unwrap().code, "signature_invalid");
    assert_eq!(hub.state.update_downloads.load(Ordering::SeqCst), 0);

    // ---------------------------------------------------------- a version that never comes up
    let id = hub.command(
        node,
        stage(
            "9.9.8",
            signed_manifest(&release, "9.9.8", &bad),
            "tok-9.9.8-0123456789",
        ),
    );
    let r = hub.wait_result(id, T).await.unwrap();
    assert_eq!(r.status, ResultStatus::Succeeded, "{:?}", r.error);
    assert_eq!(r.result.unwrap()["staged_version"], "9.9.8");
    assert_eq!(hub.state.update_downloads.load(Ordering::SeqCst), 1);
    hub.wait_for(0, T, |e| update_event(e, "staged"))
        .await
        .expect("staged event");
    hub.wait_for(0, T, |e| {
        matches!(&e.message, Message::Inventory(i)
            if i.agent_update.as_ref().and_then(|u| u.staged_version.as_deref()) == Some("9.9.8"))
    })
    .await
    .expect("inventory shows the staged version");
    assert_eq!(
        std::fs::read(&bin).unwrap(),
        original,
        "staging never touches the binary"
    );

    let from = hub.received().len();
    let connections = hub.connections();
    let id = hub.command(
        node,
        cmd(
            json!({"command": "apply_update", "timeout_seconds": 600, "args": {
                "version": "9.9.8", "health_timeout_seconds": 30
            }}),
        ),
    );
    hub.wait_for(from, T, |e| update_event(e, "swapping"))
        .await
        .expect("swapping");
    let r = hub
        .wait_result(id, Duration::from_secs(150))
        .await
        .expect("apply result after rollback");
    assert_eq!(r.status, ResultStatus::Failed);
    let e = r.error.unwrap();
    assert_eq!(e.code, "rolled_back", "{e:?}");
    let steps: Vec<String> = e.details.unwrap()["steps"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["phase"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(
        steps,
        [
            "verifying",
            "swapping",
            "restarting",
            "health_check",
            "rolling_back",
            "rolled_back"
        ]
    );
    hub.wait_for(from, T, |e| update_event(e, "rolled_back"))
        .await
        .expect("rolled_back event");
    assert!(
        hub.connections() > connections,
        "the previous agent reconnected"
    );
    assert_eq!(
        std::fs::read(&bin).unwrap(),
        original,
        "previous binary back"
    );
    assert!(pid_alive(engine_pid), "the engine kept running");

    // ---------------------------------------------------------- a working version
    let good = format!(
        "#!/bin/sh\nexport GPUDECK_AGENT_TEST_VERSION=9.9.9\nexec \"{}\" \"$@\"\n",
        real.display()
    )
    .into_bytes();
    hub.serve_update("9.9.9", &target, "tok-9.9.9-0123456789", good.clone());
    let id = hub.command(
        node,
        stage(
            "9.9.9",
            signed_manifest(&release, "9.9.9", &good),
            "tok-9.9.9-0123456789",
        ),
    );
    let r = hub.wait_result(id, T).await.unwrap();
    assert_eq!(r.status, ResultStatus::Succeeded, "{:?}", r.error);
    let from = hub.received().len();
    let id = hub.command(
        node,
        cmd(
            json!({"command": "apply_update", "timeout_seconds": 600, "args": {
                "version": "9.9.9", "health_timeout_seconds": 60, "run_id": Uuid::new_v4()
            }}),
        ),
    );
    let r = hub
        .wait_result(id, Duration::from_secs(150))
        .await
        .expect("apply result");
    assert_eq!(r.status, ResultStatus::Succeeded, "{:?}", r.error);
    let res = r.result.unwrap();
    assert_eq!(res["version"], "9.9.9");
    let hello = hub
        .wait_for(
            from,
            T,
            |e| matches!(&e.message, Message::Hello(h) if h.agent_version == "9.9.9"),
        )
        .await
        .expect("hello from the new agent");
    let Message::Hello(h) = &hello.message else {
        unreachable!()
    };
    assert!(
        h.agent_update.is_some(),
        "hub features are known after a restart: agent_update from the first hello"
    );
    for phase in ["swapping", "readopting", "health_check", "succeeded"] {
        hub.wait_for(from, T, |e| update_event(e, phase))
            .await
            .unwrap_or_else(|| panic!("no {phase} event"));
    }
    let readopt = hub
        .wait_for(from, T, |e| update_event(e, "readopting"))
        .await
        .unwrap();
    let Message::Event(ev) = &readopt.message else {
        unreachable!()
    };
    assert_eq!(ev.details.as_ref().unwrap()["adopted"], 1);
    // The engine was re-adopted (same pid), never restarted.
    assert!(pid_alive(engine_pid));
    assert_eq!(engine_started_since(&hub, from), 0);
    // The new agent's first inventory (the old one sent some before it was stopped).
    let after_hello = hub
        .received()
        .iter()
        .position(|e| e.id == hello.id)
        .unwrap();
    let inv = hub
        .wait_for(after_hello, T, |e| {
            matches!(&e.message, Message::Inventory(i) if i.instances.iter().any(|s| s.instance_id == inst))
        })
        .await
        .unwrap();
    let Message::Inventory(i) = &inv.message else {
        unreachable!()
    };
    let s = i.instances.iter().find(|s| s.instance_id == inst).unwrap();
    assert_eq!(s.pid, Some(engine_pid));
    assert_eq!(s.state, InstanceState::Ready);
    assert_eq!(std::fs::read(&bin).unwrap(), good, "new binary installed");
    assert_eq!(
        std::fs::read(bin_dir.join("gpudeck-agent.prev")).unwrap(),
        original,
        "previous binary kept for rollback"
    );
    let u = i.agent_update.clone().unwrap();
    assert_eq!(
        u.previous_version.as_deref(),
        Some(gpudeck_agent::join::AGENT_VERSION)
    );
    assert!(u.staged_version.is_none(), "{u:?}");

    // Every frame the agents sent validates against the contract (done by the schema tests for
    // the types; here: nothing unparseable reached the hub).
    sup.stop().await;
}

/// Kills a test-owned process (the engine outlives the agent by design) also on panic.
struct KillOnDrop(u32);

impl Drop for KillOnDrop {
    fn drop(&mut self) {
        // SAFETY: test-owned pid.
        unsafe { libc::kill(self.0 as libc::pid_t, libc::SIGKILL) };
    }
}

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

/// N-1: a hub that predates agent updates (no `agent_updates` in `hello_ack.features`) never
/// gets the new fields; once a hub lists the feature they appear (inventory first, then hello).
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn older_hub_gets_no_update_fields() {
    gpudeck_agent::tls::init_crypto();
    let token = "gdj_olderhubtoken0123456789";
    let hub = FakeHub::start("127.0.0.1:0".parse().unwrap(), token)
        .await
        .unwrap();
    hub.set_features(vec![
        hub_feature::OBSERVED_ENGINES.into(),
        hub_feature::GPU_PROCESSES.into(),
        hub_feature::SECURE_TRANSPORT.into(),
    ]);
    let dir = tempfile::tempdir().unwrap();
    let cfg_path = dir.path().join("agent.toml");
    let mut cfg = AgentConfig {
        firewall: "off".into(),
        state_dir: dir.path().join("state"),
        prom_port: 0,
        ..Default::default()
    };
    join(
        &mut cfg,
        &cfg_path,
        JoinOptions {
            hub_url: hub.url(),
            token: token.into(),
            name: Some("n1".into()),
            ca_fingerprint: Some(hub.ca_fingerprint()),
            proxy_port: 9443,
            labels: Default::default(),
        },
    )
    .await
    .unwrap();
    let agent = Agent::new(
        cfg.clone(),
        cfg_path.clone(),
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
    hub.wait_for(0, T, |e| matches!(e.message, Message::Inventory(_)))
        .await
        .expect("inventory");
    // Two more connections against the older hub.
    for _ in 0..2 {
        let n = hub.connections();
        hub.kick();
        hub.wait_for(hub.received().len(), Duration::from_secs(90), |e| {
            matches!(e.message, Message::Inventory(_))
        })
        .await
        .expect("inventory after reconnect");
        assert!(hub.connections() > n);
    }
    assert!(
        hub.received_raw()
            .iter()
            .all(|t| !t.contains("agent_update")),
        "no agent_update field or event kind sent to an older hub"
    );
    // The hub is upgraded: inventory carries it at once, hello from the next connection on.
    hub.set_features(vec![hub_feature::AGENT_UPDATES.into()]);
    let from = hub.received().len();
    hub.kick();
    let inv = hub
        .wait_for(
            from,
            Duration::from_secs(90),
            |e| matches!(&e.message, Message::Inventory(i) if i.agent_update.is_some()),
        )
        .await
        .expect("inventory with agent_update");
    let Message::Inventory(i) = &inv.message else {
        unreachable!()
    };
    assert_eq!(i.agent_update.as_ref().unwrap().release_keys, 0);
    let from = hub.received().len();
    hub.kick();
    hub.wait_for(
        from,
        Duration::from_secs(90),
        |e| matches!(&e.message, Message::Hello(h) if h.agent_update.is_some()),
    )
    .await
    .expect("hello with agent_update");
    agent.shutdown.cancel();
    let _ = tokio::time::timeout(Duration::from_secs(20), run).await;
}
