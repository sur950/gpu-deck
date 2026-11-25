//! End-to-end: the real agent against the in-process fake hub over mTLS, with the
//! `gpudeck-fake-engine` binary as engine. Covers join (CA pinning, 0600 files), hello /
//! hello_ack / inventory / heartbeat / metrics_batch, load_model (dedicated + llama.cpp router
//! mode with router restart), config_push routing, the :9443 proxy (client-cert policy, CRL,
//! routing, model rewrite, path blocking, draining, streaming, accounting), command replay
//! idempotency, reconnect, crash → auto-restart, unload, start/stop_engine, rotate/install
//! cert with reconnect, and validates every frame the agent sent against the JSON Schemas.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use gpudeck_agent::backend::{DeviceReading, DeviceSource, GenericCatalog};
use gpudeck_agent::fakehub::FakeHub;
use gpudeck_agent::join::{join, JoinOptions};
use gpudeck_agent::{Agent, AgentConfig, RunOptions};
use gpudeck_protocol::*;
use jsonschema::{Retrieve, Uri};
use serde_json::{json, Value};
use sha2::Digest;
use uuid::Uuid;

const T: Duration = Duration::from_secs(30);

struct FakeDevices;

#[async_trait]
impl DeviceSource for FakeDevices {
    async fn detect(&self) -> Vec<Device> {
        vec![Device {
            device_id: None,
            vendor: DeviceVendor::Apple,
            index: 0,
            uuid: Some("apple-test-0".into()),
            name: "Apple Test GPU".into(),
            vram_total_bytes: 16 << 30,
            is_unified_memory: true,
            driver_version: None,
            runtime_version: Some("Metal 4".into()),
            compute_capability: Some("metal3".into()),
            pci_bus_id: None,
            numa_node: None,
            max_power_watts: None,
            core_count: Some(10),
            health: DeviceHealth::Healthy,
            health_reason: None,
            labels: Default::default(),
            extra: Default::default(),
        }]
    }
    async fn sample(&self) -> Vec<DeviceReading> {
        let mut s = GpuSample {
            utilization_pct: Some(12.5),
            memory_used_bytes: Some(1 << 30),
            memory_total_bytes: Some(16 << 30),
            ..Default::default()
        };
        s.ts = now();
        vec![DeviceReading {
            vendor: DeviceVendor::Apple,
            index: 0,
            uuid: Some("apple-test-0".into()),
            sample: s,
        }]
    }
    fn backends(&self) -> Vec<String> {
        vec!["fake".into()]
    }
}

/// Kills every engine the agent started if the test panics midway.
struct KillOnDrop(Arc<Agent>);

impl Drop for KillOnDrop {
    fn drop(&mut self) {
        let pids: Vec<u32> = self
            .0
            .instances
            .lock()
            .unwrap()
            .values()
            .filter_map(|i| i.proc.as_ref().and_then(|p| p.pid))
            .chain(
                self.0
                    .engine_procs
                    .lock()
                    .unwrap()
                    .values()
                    .filter_map(|p| p.proc.as_ref().and_then(|x| x.pid)),
            )
            .collect();
        for pid in pids {
            gpudeck_agent::supervisor::kill_group(pid, libc::SIGKILL);
        }
    }
}

fn schema_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../../libs/contracts/agent-protocol")
}

struct Local;
impl Retrieve for Local {
    fn retrieve(
        &self,
        uri: &Uri<String>,
    ) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        let name = uri
            .path()
            .as_str()
            .rsplit('/')
            .next()
            .unwrap_or_default()
            .to_string();
        Ok(serde_json::from_str(&std::fs::read_to_string(
            schema_dir().join(name),
        )?)?)
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
        "port_range": [10240, 10340]
    })
}

fn model_spec(file: &std::path::Path, sha: &str, served: &str) -> Value {
    json!({
        "model_id": Uuid::new_v4(), "model_version_id": Uuid::new_v4(), "name": "tiny", "served_model_name": served,
        "type": "chat", "format": "gguf",
        "files": [{"model_file_id": Uuid::new_v4(), "path": file.file_name().unwrap().to_string_lossy(), "format": "gguf", "size_bytes": 8, "sha256": sha}],
        "cache_dir": "", "source": {"kind": "local_path", "repo_id": file.parent().unwrap().display().to_string()},
        "config": {"num_hidden_layers": 2, "num_key_value_heads": 2, "head_dim": 8}
    })
}

fn cmd(v: Value) -> Command {
    serde_json::from_value(v).expect("command json")
}

async fn result(hub: &FakeHub, id: Uuid) -> CommandResult {
    hub.wait_result(id, Duration::from_secs(60))
        .await
        .unwrap_or_else(|| panic!("no terminal result for {id}"))
}

fn ok(r: &CommandResult) -> &JsonObject {
    assert_eq!(
        r.status,
        ResultStatus::Succeeded,
        "command failed: {:?}",
        r.error
    );
    r.result.as_ref().unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn agent_against_fake_hub() {
    gpudeck_agent::tls::init_crypto();
    let hub = FakeHub::start("127.0.0.1:0".parse().unwrap(), "gdj_testtoken0123456789")
        .await
        .unwrap();
    let dir = tempfile::tempdir().unwrap();
    let cfg_path = dir.path().join("agent.toml");
    let mut cfg = AgentConfig {
        // Tests never program the host firewall.
        firewall: "off".into(),
        state_dir: dir.path().join("state"),
        // Short idle-router grace so the idle stop is observable below.
        router_idle_stop_seconds: 2,
        ..Default::default()
    };

    // --- join: wrong pin refused, wrong token refused, then success
    let bad = join(
        &mut cfg.clone(),
        &cfg_path,
        JoinOptions {
            hub_url: hub.url(),
            token: "gdj_testtoken0123456789".into(),
            name: Some("t".into()),
            ca_fingerprint: Some("00".repeat(32)),
            proxy_port: 9443,
            labels: Default::default(),
        },
    )
    .await;
    assert!(bad.is_err(), "pinned join with wrong fingerprint must fail");
    let bad = join(
        &mut cfg.clone(),
        &cfg_path,
        JoinOptions {
            hub_url: hub.url(),
            token: "gdj_wrong".into(),
            name: None,
            ca_fingerprint: Some(hub.ca_fingerprint()),
            proxy_port: 9443,
            labels: Default::default(),
        },
    )
    .await;
    let msg = format!("{:#}", bad.unwrap_err());
    assert!(msg.contains("JOIN_TOKEN_INVALID"), "{msg}");
    let joined = join(
        &mut cfg,
        &cfg_path,
        JoinOptions {
            hub_url: hub.url(),
            token: "gdj_testtoken0123456789".into(),
            name: Some("test-node".into()),
            ca_fingerprint: Some(hub.ca_fingerprint()),
            proxy_port: 9443,
            labels: [("zone".to_string(), "lab".to_string())].into(),
        },
    )
    .await
    .unwrap();
    let node = joined.node_id;
    for f in ["agent.key", "agent.crt", "ca.pem"] {
        gpudeck_agent::state::check_private_mode(&cfg.state_dir.join(f)).unwrap();
    }
    let reloaded = AgentConfig::load(&cfg_path).unwrap();
    assert_eq!(reloaded.node_id, Some(node));

    // --- run
    cfg.prom_port = 0;
    let agent = Agent::new(
        cfg.clone(),
        cfg_path.clone(),
        Arc::new(FakeDevices),
        Arc::new(GenericCatalog),
        RunOptions {
            proxy_listen: Some("127.0.0.1:0".parse().unwrap()),
            no_prom: true,
            ..Default::default()
        },
    )
    .unwrap();
    let a2 = agent.clone();
    let run = tokio::spawn(async move { a2.run().await });
    let _guard = KillOnDrop(agent.clone());

    let hello = hub
        .wait_for(0, T, |e| matches!(e.message, Message::Hello(_)))
        .await
        .expect("hello");
    let Message::Hello(h) = &hello.message else {
        unreachable!()
    };
    assert_eq!(hello.node_id, Some(node));
    assert_eq!(h.labels.get("zone").map(String::as_str), Some("lab"));
    let inv = hub
        .wait_for(
            0,
            T,
            |e| matches!(&e.message, Message::Inventory(i) if !i.devices.is_empty()),
        )
        .await
        .expect("inventory");
    let Message::Inventory(i) = inv.message else {
        unreachable!()
    };
    assert_eq!(i.devices[0].name, "Apple Test GPU");
    assert_eq!(
        i.devices[0].device_id,
        Some(Uuid::new_v5(&node, b"apple:0")),
        "inventory carries the stable device id"
    );
    assert!(
        h.capabilities
            .commands
            .contains(&gpudeck_protocol::CommandName::ScanPath)
            && h.capabilities
                .commands
                .contains(&gpudeck_protocol::CommandName::StartSshInstance)
    );
    hub.wait_for(0, T, |e| matches!(e.message, Message::Heartbeat(_)))
        .await
        .expect("heartbeat");
    let mb = hub
        .wait_for(
            0,
            T,
            |e| matches!(&e.message, Message::MetricsBatch(b) if !b.gpu.is_empty()),
        )
        .await
        .expect("metrics");
    let Message::MetricsBatch(b) = mb.message else {
        unreachable!()
    };
    assert_eq!(b.gpu[0].utilization_pct, Some(12.5));
    assert_eq!(
        b.gpu[0].device_id,
        Uuid::new_v5(&node, b"apple:0"),
        "device id falls back to uuid5(node, vendor:index)"
    );
    hub.wait_for(
        0,
        T,
        |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::AgentStarted),
    )
    .await
    .expect("agent_started");

    // --- load_model (dedicated): local file referenced in place, template forced to loopback
    let mdir = tempfile::tempdir().unwrap();
    let mfile = mdir.path().join("tiny.gguf");
    std::fs::write(&mfile, b"GGUFtest").unwrap();
    let sha = hex::encode(sha2::Sha256::digest(b"GGUFtest"));
    let inst = Uuid::new_v4();
    let dep = Uuid::new_v4();
    let dev = Uuid::new_v4();
    let load = json!({"command": "load_model", "timeout_seconds": 120, "args": {
        "instance_id": inst, "deployment_id": dep,
        "engine": engine_spec("custom", vec![engine_bin(), "--host".into(), "0.0.0.0".into(), "--port".into(), "{{port}}".into(), "{{flags}}".into()], false),
        "model": model_spec(&mfile, &sha, "tiny-q4"),
        "devices": [{"device_id": dev, "index": 0, "vendor": "apple"}],
        "port": 0, "launch": {"context_size": 1024, "max_concurrency": 2}
    }});
    let load_cmd = cmd(load.clone());
    let load_env = Envelope::new(Some(node), Message::Command(load_cmd.clone()));
    let load_id = load_env.id;
    hub.state.pending.lock().unwrap().push(load_env.clone());
    hub.send_envelope(load_env.clone());
    let accepted = hub.wait_for(0, T, |e| e.correlation_id == Some(load_id) && matches!(&e.message, Message::CommandResult(r) if r.status == ResultStatus::Accepted)).await;
    assert!(accepted.is_some(), "accepted ack");
    let r = result(&hub, load_id).await;
    let res = ok(&r);
    let port = res["port"].as_u64().unwrap() as u16;
    let pid = res["pid"].as_u64().unwrap() as u32;
    assert!((10240..=10340).contains(&port));
    assert_eq!(res["served_model_name"], "tiny-q4");
    assert!(std::net::TcpStream::connect(("127.0.0.1", port)).is_ok());
    let starts = |hub: &FakeHub| {
        hub.received().iter().filter(|e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::EngineStarted && ev.instance_id == Some(inst))).count()
    };
    assert_eq!(starts(&hub), 1);

    // replay of the same command id: no second execution, same terminal result re-sent
    let before = hub.received().len();
    hub.send_envelope(load_env.clone());
    let replay = hub
        .wait_for(before, T, |e| e.correlation_id == Some(load_id))
        .await
        .expect("replayed result");
    assert!(
        matches!(&replay.message, Message::CommandResult(r) if r.status == ResultStatus::Succeeded)
    );
    assert_eq!(starts(&hub), 1, "replay must not start the engine again");

    // --- config_push routing
    hub.send(Some(node), Message::ConfigPush(serde_json::from_value(json!({
        "config_version": 3, "full": true,
        "routing": [{"instance_id": inst, "deployment_id": dep, "port": port, "served_model_name": "tiny-q4", "allowed_path_prefixes": ["/v1/chat/completions", "/health"], "max_concurrent": 1}],
        "settings": {"metrics_interval_seconds": 1, "proxy": {"queue_timeout_seconds": 0.3}}
    })).unwrap()));
    hub.wait_for(0, T, |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::ConfigApplied && ev.details.as_ref().and_then(|d| d.get("config_version")) == Some(&json!(3)))).await.expect("config_applied");

    // --- proxy
    let proxy_port = agent.proxy_addr.lock().unwrap().unwrap().port();
    let api = hub.issue_client("api-layer", "gpudeck-services");
    let client_for = |issued: &gpudeck_agent::testpki::Issued| {
        let tls = gpudeck_agent::tls::node_proxy_client_config(
            &hub.ca_pem(),
            &issued.cert_pem,
            issued.key_pem.as_deref().unwrap(),
            &node.to_string(),
        )
        .unwrap();
        reqwest::Client::builder()
            .use_preconfigured_tls(tls)
            .no_proxy()
            .build()
            .unwrap()
    };
    let client = client_for(&api);
    let base = format!("https://127.0.0.1:{proxy_port}");
    let health: Value = client
        .get(format!("{base}/_health"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(health["status"], "ok");
    let req_id = Uuid::new_v4().to_string();
    let r = client
        .post(format!("{base}/instances/{inst}/v1/chat/completions"))
        .header("x-request-id", &req_id)
        .header("x-gpudeck-api-key-id", "k1")
        .json(&json!({"model": "public-name", "messages": [{"role": "user", "content": "hi"}]}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    assert_eq!(r.headers()["x-gpudeck-instance"], inst.to_string());
    assert_eq!(r.headers()["x-gpudeck-node"], node.to_string());
    assert_eq!(r.headers()["x-request-id"], req_id.as_str());
    // The node's share of the request, for the API layer's Server-Timing (serving.md §10).
    let timing = r.headers()["x-gpudeck-proxy-timing"]
        .to_str()
        .unwrap()
        .to_owned();
    let durations: Vec<(&str, f64)> = timing
        .split(", ")
        .map(|part| {
            let (name, dur) = part.split_once(";dur=").expect("name;dur=ms");
            (name, dur.parse::<f64>().expect("milliseconds"))
        })
        .collect();
    assert_eq!(
        durations.iter().map(|(n, _)| *n).collect::<Vec<_>>(),
        ["queue", "engine", "node"]
    );
    assert!(
        durations[2].1 >= durations[1].1,
        "node time includes the engine's: {timing}"
    );
    let body: Value = r.json().await.unwrap();
    assert_eq!(
        body["model"], "tiny-q4",
        "model rewritten to served_model_name"
    );
    // HTTP/2 (ALPN h2): the API layer can multiplex concurrent requests over one connection.
    let h2 = {
        let mut tls = gpudeck_agent::tls::node_proxy_client_config(
            &hub.ca_pem(),
            &api.cert_pem,
            api.key_pem.as_deref().unwrap(),
            &node.to_string(),
        )
        .unwrap();
        tls.alpn_protocols = vec![b"h2".to_vec()];
        reqwest::Client::builder()
            .use_preconfigured_tls(tls)
            .no_proxy()
            .http2_prior_knowledge()
            .build()
            .unwrap()
    };
    let calls = (0..4).map(|_| {
        h2.post(format!("{base}/instances/{inst}/v1/chat/completions"))
            .json(&json!({"model": "public-name", "messages": []}))
            .send()
    });
    for r in futures::future::join_all(calls).await {
        let r = r.unwrap();
        assert_eq!(r.version(), reqwest::Version::HTTP_2);
        assert_eq!(r.status(), 200);
        assert_eq!(r.json::<Value>().await.unwrap()["model"], "tiny-q4");
    }
    // streaming passes through chunk by chunk
    let r = client
        .post(format!("{base}/instances/{inst}/v1/chat/completions"))
        .json(&json!({"model": "x", "stream": true, "messages": []}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.headers()["content-type"], "text/event-stream");
    let text = r.text().await.unwrap();
    assert!(text.contains(" fake") && text.contains("[DONE]") && text.contains("\"usage\""));
    // engine admin paths and unknown instances
    assert_eq!(
        client
            .get(format!("{base}/instances/{inst}/slots"))
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    assert_eq!(
        client
            .get(format!("{base}/instances/{inst}/metrics"))
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    assert_eq!(
        client
            .post(format!("{base}/instances/{inst}/models/load"))
            .body("{}")
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    assert_eq!(
        client
            .post(format!("{base}/instances/{inst}/v1/embeddings"))
            .body("{}")
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    let nf = client
        .get(format!(
            "{base}/instances/{}/v1/chat/completions",
            Uuid::new_v4()
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(nf.status(), 404);
    assert_eq!(
        nf.json::<Value>().await.unwrap()["error"]["code"],
        "instance_not_found"
    );
    let ih: Value = client
        .get(format!("{base}/instances/{inst}/_health"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(ih["state"], "ready");
    // max_concurrent=1 + queue timeout 0.3 s: a held request makes the next one 503 instance_busy
    let c2 = client.clone();
    let u2 = format!("{base}/instances/{inst}/v1/chat/completions");
    let held = tokio::spawn(async move {
        c2.post(u2)
            .json(&json!({"model": "x", "sleep_ms": 1500}))
            .send()
            .await
            .unwrap()
            .status()
    });
    tokio::time::sleep(Duration::from_millis(300)).await;
    let busy = client
        .post(format!("{base}/instances/{inst}/v1/chat/completions"))
        .json(&json!({"model": "x"}))
        .send()
        .await
        .unwrap();
    assert_eq!(busy.status(), 503);
    assert_eq!(
        busy.json::<Value>().await.unwrap()["error"]["code"],
        "instance_busy"
    );
    assert_eq!(held.await.unwrap(), 200);
    // node certificates (wrong O) are refused in the handshake
    let other = hub.issue_client("node-x", "gpudeck-nodes");
    assert!(client_for(&other)
        .get(format!("{base}/_health"))
        .send()
        .await
        .is_err());
    // a revoked API-layer cert is refused after a CRL push
    let revoked = hub.issue_client("api-layer", "gpudeck-services");
    assert!(client_for(&revoked)
        .get(format!("{base}/_health"))
        .send()
        .await
        .is_ok());
    hub.send(Some(node), Message::ConfigPush(serde_json::from_value(json!({"config_version": 4, "full": false, "crl": {"generated_at": now(), "serials": [revoked.serial_hex]}})).unwrap()));
    hub.wait_for(0, T, |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::ConfigApplied && ev.details.as_ref().and_then(|d| d.get("config_version")) == Some(&json!(4)))).await.expect("crl applied");
    assert!(
        client_for(&revoked)
            .get(format!("{base}/_health"))
            .send()
            .await
            .is_err(),
        "revoked cert refused"
    );
    assert!(
        client.get(format!("{base}/_health")).send().await.is_ok(),
        "routing kept after delta push"
    );

    // proxy-derived engine metrics
    let em = hub
        .wait_for(0, T, |e| matches!(&e.message, Message::MetricsBatch(b) if b.engine.iter().any(|s| s.instance_id == inst && s.requests_total.unwrap_or(0) >= 3)))
        .await
        .expect("engine metrics");
    let Message::MetricsBatch(b) = em.message else {
        unreachable!()
    };
    let s = b.engine.iter().find(|s| s.instance_id == inst).unwrap();
    assert_eq!(s.deployment_id, dep);
    assert!(s.prompt_tokens_total.unwrap() >= 14 && s.generated_tokens_total.unwrap() >= 10);
    assert!(s.cached_tokens_total.unwrap() >= 2);
    assert_eq!(s.health, Some(InstanceHealth::Healthy));
    assert!(s.process_rss_bytes.unwrap_or(0) > 0);

    // --- drain: proxy answers 503 + Retry-After, then resume
    let d = hub.command(node, cmd(json!({"command": "drain_instance", "args": {"instance_id": inst, "timeout_seconds": 5}})));
    assert_eq!(ok(&result(&hub, d).await)["drained"], true);
    let r = client
        .post(format!("{base}/instances/{inst}/v1/chat/completions"))
        .json(&json!({"model": "x"}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 503);
    assert_eq!(r.headers()["retry-after"], "2");
    let d = hub.command(
        node,
        cmd(json!({"command": "drain_instance", "args": {"instance_id": inst, "resume": true}})),
    );
    ok(&result(&hub, d).await);
    assert_eq!(
        client
            .post(format!("{base}/instances/{inst}/v1/chat/completions"))
            .json(&json!({"model": "x"}))
            .send()
            .await
            .unwrap()
            .status(),
        200
    );

    // --- reconnect after a dropped connection
    let conns = hub.connections();
    hub.kick();
    let n = hub.received().len();
    hub.wait_for(n, T, |e| matches!(e.message, Message::Hello(_)))
        .await
        .expect("hello after reconnect");
    hub.wait_for(n, T, |e| matches!(e.message, Message::Inventory(_)))
        .await
        .expect("inventory after reconnect");
    for _ in 0..50 {
        if hub.connections() > conns {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert!(hub.connections() > conns);

    // --- crash → engine_crashed → auto-restart → ready
    unsafe { libc::kill(pid as i32, libc::SIGKILL) };
    hub.wait_for(0, T, |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::EngineCrashed && ev.instance_id == Some(inst))).await.expect("engine_crashed");
    let n = hub.received().len();
    hub.wait_for(n, Duration::from_secs(40), |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::InstanceStateChanged && ev.instance_id == Some(inst) && ev.state.as_ref().is_some_and(|s| s.to == "ready")))
        .await
        .expect("restarted after crash");
    let new_pid = agent
        .instances
        .lock()
        .unwrap()
        .get(&inst)
        .unwrap()
        .proc
        .as_ref()
        .unwrap()
        .pid
        .unwrap();
    assert_ne!(new_pid, pid);

    // --- unload
    let u = hub.command(node, cmd(json!({"command": "unload_model", "args": {"instance_id": inst, "drain_timeout_seconds": 5}})));
    let r = result(&hub, u).await;
    assert_eq!(ok(&r)["drained"], true);
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert!(
        !gpudeck_agent::supervisor::pid_alive(new_pid),
        "engine stopped"
    );
    assert!(!dir
        .path()
        .join("state/instances")
        .join(format!("{inst}.json"))
        .exists());

    // --- router mode: start_engine, load (router restart registers the model), unload, stop
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
    let s = hub.command(node, cmd(json!({"command": "start_engine", "args": {"engine_process_id": ep, "engine": router_engine, "devices": [{"device_id": dev, "index": 0, "vendor": "apple"}], "port": 0, "launch": {}}})));
    let rp = ok(&result(&hub, s).await)["port"].as_u64().unwrap() as u16;
    let rinst = Uuid::new_v4();
    let l = hub.command(node, cmd(json!({"command": "load_model", "args": {
        "instance_id": rinst, "deployment_id": dep, "engine": router_engine, "model": model_spec(&mfile, &sha, "tiny-router"),
        "devices": [{"device_id": dev, "index": 0, "vendor": "apple"}], "port": 0, "engine_process_id": ep, "launch": {"context_size": 2048}
    }})));
    let r = result(&hub, l).await;
    assert_eq!(ok(&r)["port"].as_u64().unwrap() as u16, rp);
    let preset = std::fs::read_to_string(
        dir.path()
            .join(format!("state/engines/llama_cpp/routers/{ep}/preset.ini")),
    )
    .unwrap();
    assert!(
        preset.contains("[tiny-router]") && preset.contains("ctx-size = 2048"),
        "{preset}"
    );
    let models: Value = reqwest::get(format!("http://127.0.0.1:{rp}/models"))
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(models["data"][0]["status"]["value"], "loaded");
    let inv = hub.wait_for(0, T, |e| matches!(&e.message, Message::Inventory(i) if i.engine_processes.iter().any(|p| p.engine_process_id == ep && p.instance_ids.contains(&rinst)))).await;
    assert!(inv.is_some(), "inventory lists the router and its instance");
    let u = hub.command(node, cmd(json!({"command": "unload_model", "args": {"instance_id": rinst, "force": true, "stop_engine_if_empty": true}})));
    ok(&result(&hub, u).await);
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert!(
        agent.engine_procs.lock().unwrap().is_empty(),
        "router stopped when empty"
    );

    // --- idle router: the last instance leaves without stop_engine_if_empty (e.g. a swap that
    // moved it away while another load was pending); the agent stops it after the grace.
    let ep2 = Uuid::new_v4();
    let s = hub.command(node, cmd(json!({"command": "start_engine", "args": {"engine_process_id": ep2, "engine": router_engine, "devices": [{"device_id": dev, "index": 0, "vendor": "apple"}], "port": 0, "launch": {}}})));
    ok(&result(&hub, s).await);
    let rinst2 = Uuid::new_v4();
    let l = hub.command(node, cmd(json!({"command": "load_model", "args": {
        "instance_id": rinst2, "deployment_id": dep, "engine": router_engine, "model": model_spec(&mfile, &sha, "tiny-router-2"),
        "devices": [{"device_id": dev, "index": 0, "vendor": "apple"}], "port": 0, "engine_process_id": ep2, "launch": {}
    }})));
    ok(&result(&hub, l).await);
    let n = hub.received().len();
    let u = hub.command(node, cmd(json!({"command": "unload_model", "args": {"instance_id": rinst2, "force": true, "stop_engine_if_empty": false}})));
    ok(&result(&hub, u).await);
    assert!(
        agent.engine_procs.lock().unwrap().contains_key(&ep2),
        "router kept during the idle grace"
    );
    let stopped = hub
        .wait_for(n, T, |e| {
            matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::EngineStopped && ev.engine_process_id == Some(ep2))
        })
        .await;
    assert!(
        stopped.is_some(),
        "idle router stopped after router_idle_stop_seconds"
    );
    assert!(agent.engine_procs.lock().unwrap().is_empty());

    // --- cert rotation: rotate → CSR, hub signs, install → reconnect with the new cert
    let old_fp = agent.identity().fingerprint();
    let rc = hub.command(
        node,
        cmd(json!({"command": "rotate_cert", "args": {"reason": "test"}})),
    );
    let csr = ok(&result(&hub, rc).await)["csr_pem"]
        .as_str()
        .unwrap()
        .to_string();
    let signed = hub
        .state
        .ca
        .sign_csr(&csr, &node.to_string(), "gpudeck-nodes", 400)
        .unwrap();
    let conns = hub.connections();
    let ic = hub.command(node, cmd(json!({"command": "install_cert", "args": {"certificate_pem": signed.cert_pem, "ca_bundle_pem": hub.ca_pem(), "serial": signed.serial_hex, "not_after": signed.not_after}})));
    let fp = ok(&result(&hub, ic).await)["fingerprint_sha256"]
        .as_str()
        .unwrap()
        .to_string();
    assert_ne!(fp, old_fp);
    let n = hub.received().len();
    let h2 = hub
        .wait_for(n, T, |e| matches!(e.message, Message::Hello(_)))
        .await
        .expect("hello with new cert");
    let Message::Hello(h2) = h2.message else {
        unreachable!()
    };
    assert_eq!(
        h2.proxy.cert_fingerprint_sha256.as_deref(),
        Some(fp.as_str())
    );
    for _ in 0..50 {
        if hub.connections() > conns {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert!(hub.connections() > conns);
    hub.wait_for(
        0,
        T,
        |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::CertRotated),
    )
    .await
    .expect("cert_rotated");

    // --- update_config, evict (in-use protection is covered in unit tests), cancel
    let uc = hub.command(
        node,
        cmd(json!({"command": "update_config", "args": {"settings": {"log_forward": false}}})),
    );
    ok(&result(&hub, uc).await);
    let cancel = hub.command(
        node,
        cmd(json!({"command": "cancel", "args": {"target_command_id": Uuid::new_v4()}})),
    );
    assert_eq!(ok(&result(&hub, cancel).await)["cancelled"], false);

    // --- scan_path / hash_files (protocol 1.1) over the hub connection
    let sdir = tempfile::tempdir().unwrap();
    let store = sdir.path().join(".ollama/models");
    let blob =
        b"GGUF\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00";
    let digest = hex::encode(sha2::Sha256::digest(blob));
    std::fs::create_dir_all(store.join("blobs")).unwrap();
    std::fs::write(store.join(format!("blobs/sha256-{digest}")), blob).unwrap();
    std::fs::create_dir_all(store.join("manifests/registry.ollama.ai/library/tiny")).unwrap();
    std::fs::write(store.join("manifests/registry.ollama.ai/library/tiny/latest"), json!({"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": format!("sha256:{digest}"), "size": blob.len()}]}).to_string()).unwrap();
    let sc = hub.command(
        node,
        cmd(json!({"command": "scan_path", "args": {"path": sdir.path().display().to_string()}})),
    );
    let scan = ok(&result(&hub, sc).await).clone();
    assert_eq!(scan["candidates"][0]["kind"], "ollama_blob", "{scan:?}");
    assert_eq!(
        scan["candidates"][0]["suggested"]["ollama_name"],
        "tiny:latest"
    );
    let hf = hub.command(node, cmd(json!({"command": "hash_files", "args": {"root": store.join("blobs").display().to_string(), "paths": [format!("sha256-{digest}"), "nope"]}})));
    let hashed = ok(&result(&hub, hf).await).clone();
    assert_eq!(hashed["hashes"][format!("sha256-{digest}")], digest);
    assert_eq!(hashed["files"][1]["error"]["code"], "path_not_found");
    let bad = hub.command(
        node,
        cmd(json!({"command": "scan_path", "args": {"path": "relative"}})),
    );
    assert_eq!(result(&hub, bad).await.error.unwrap().code, "invalid_args");

    // --- shutdown, then validate every frame against the contract schemas
    agent.shutdown.cancel();
    let _ = tokio::time::timeout(Duration::from_secs(20), run).await;
    let schema: Value =
        serde_json::from_str(&std::fs::read_to_string(schema_dir().join("envelope.json")).unwrap())
            .unwrap();
    let validator = jsonschema::options()
        .with_retriever(Local)
        .build(&schema)
        .unwrap();
    let raw = hub.received_raw();
    assert!(raw.len() > 20);
    let mut kinds = std::collections::BTreeSet::new();
    for text in raw {
        let v: Value = serde_json::from_str(&text).unwrap();
        kinds.insert(v["type"].as_str().unwrap().to_string());
        let errs: Vec<String> = validator
            .iter_errors(&v)
            .map(|e| format!("{e} at {}", e.instance_path()))
            .collect();
        assert!(
            errs.is_empty(),
            "schema violation in {}: {}",
            text.chars().take(600).collect::<String>(),
            errs.join("; ")
        );
    }
    for k in [
        "hello",
        "inventory",
        "heartbeat",
        "metrics_batch",
        "event",
        "command_result",
    ] {
        assert!(kinds.contains(k), "no {k} frame seen");
    }
}
