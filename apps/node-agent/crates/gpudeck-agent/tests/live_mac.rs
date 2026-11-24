//! Live check on a Mac with the preinstalled engines (ignored by default):
//!
//! `cargo test -p gpudeck-agent --test live_mac -- --ignored --nocapture`
//!
//! Runs the real `gpudeck-agent` binary (join + run) against the in-process fake hub, with the real
//! device plugins (Apple GPU) and engine adapters, starts `llama-server` in router mode
//! (`~/.unsloth/llama.cpp/build/bin`), loads the Ollama `qwen3:0.6b` GGUF blob in place, pushes
//! routing, sends chat completions through the :9443 mTLS proxy, checks engine/session metrics,
//! restarts the agent (the router and its model keep running and are re-adopted: same pid, no
//! reload, chat still answers), then unloads, stops and shuts everything down.

use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use gpudeck_agent::fakehub::FakeHub;
use gpudeck_protocol::*;
use serde_json::{json, Value};
use uuid::Uuid;

const QWEN_BLOB: &str = "sha256-7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa";
const QWEN_SHA: &str = "7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa";

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap())
}

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

fn cmd(v: Value) -> Command {
    serde_json::from_value(v).expect("command")
}

async fn done(hub: &FakeHub, id: Uuid, secs: u64) -> JsonObject {
    let r = hub
        .wait_result(id, Duration::from_secs(secs))
        .await
        .unwrap_or_else(|| panic!("no result for {id}"));
    assert_eq!(
        r.status,
        ResultStatus::Succeeded,
        "{:?}: {:?}",
        r.command,
        r.error
    );
    r.result.unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "needs llama-server and the qwen3:0.6b Ollama blob on this machine"]
async fn live_router_llama_cpp_through_proxy() {
    let llama = home().join(".unsloth/llama.cpp/build/bin/llama-server");
    let blobs = home().join(".ollama/models/blobs");
    assert!(
        llama.exists() && blobs.join(QWEN_BLOB).exists(),
        "missing llama-server or qwen3 blob"
    );
    gpudeck_agent::tls::init_crypto();
    let token = "gdj_live0123456789abcdef";
    let hub = FakeHub::start("127.0.0.1:0".parse().unwrap(), token)
        .await
        .unwrap();
    hub.set_intervals(Intervals {
        heartbeat_seconds: 5,
        metrics_seconds: 1.0,
        inventory_seconds: 30,
        command_ack_timeout_seconds: 10,
    });
    let dir = tempfile::tempdir().unwrap();
    let cfg = dir.path().join("agent.toml");
    let proxy_port = free_port();
    std::fs::write(&cfg, format!("state_dir = \"{}\"\nproxy_port = {proxy_port}\nproxy_bind = \"127.0.0.1\"\nprom_port = {}\nlog_level = \"info\"\n[engine_paths]\nllama_cpp = \"{}\"\n", dir.path().join("state").display(), free_port(), llama.display())).unwrap();
    let bin = env!("CARGO_BIN_EXE_gpudeck-agent");
    let out = std::process::Command::new(bin)
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "join",
            "--url",
            &hub.url(),
            "--token",
            token,
            "--name",
            "mac-live",
            "--ca-fingerprint",
            &hub.ca_fingerprint(),
        ])
        .output()
        .unwrap();
    println!("join: {}", String::from_utf8_lossy(&out.stdout));
    assert!(
        out.status.success(),
        "join failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let log = std::fs::File::create(dir.path().join("agent.log")).unwrap();
    let mut agent = tokio::process::Command::new(bin)
        .args(["--config", cfg.to_str().unwrap(), "run"])
        .stdout(Stdio::from(log.try_clone().unwrap()))
        .stderr(Stdio::from(log))
        .kill_on_drop(true)
        .spawn()
        .unwrap();

    let hello = hub
        .wait_for(0, Duration::from_secs(30), |e| {
            matches!(e.message, Message::Hello(_))
        })
        .await
        .expect("hello");
    let node = hello.node_id.unwrap();
    let Message::Hello(h) = hello.message else {
        unreachable!()
    };
    println!(
        "hello: {} {} {:?} engines={:?}",
        h.hostname,
        h.os,
        h.arch,
        h.capabilities
            .engines
            .iter()
            .map(|e| e.name)
            .collect::<Vec<_>>()
    );
    let inv = hub
        .wait_for(
            0,
            Duration::from_secs(30),
            |e| matches!(&e.message, Message::Inventory(i) if !i.devices.is_empty()),
        )
        .await
        .expect("inventory with devices");
    let Message::Inventory(i) = inv.message else {
        unreachable!()
    };
    let dev = &i.devices[0];
    println!(
        "device: {} {} vram={} cores={:?} unified={}",
        dev.vendor, dev.name, dev.vram_total_bytes, dev.core_count, dev.is_unified_memory
    );
    assert_eq!(dev.vendor, DeviceVendor::Apple);
    let g = hub
        .wait_for(
            0,
            Duration::from_secs(30),
            |e| matches!(&e.message, Message::MetricsBatch(b) if !b.gpu.is_empty()),
        )
        .await
        .expect("gpu metrics");
    let Message::MetricsBatch(b) = g.message else {
        unreachable!()
    };
    println!(
        "gpu sample: util={:?}% mem_used={:?} power={:?}",
        b.gpu[0].utilization_pct, b.gpu[0].memory_used_bytes, b.gpu[0].power_watts
    );

    // Router process (template as seeded by the control plane).
    let dev_id = Uuid::new_v4();
    let engine = json!({
        "engine_version_id": Uuid::new_v4(), "name": "llama_cpp", "version": "b8798", "runtime": "native",
        "launch_template": {"command": ["llama-server", "--host", "127.0.0.1", "--port", "{{port}}", "--models-dir", "{{model_dir}}", "--metrics", "--slots", "{{flags}}"], "router_mode": true},
        "health": {"path": "/health", "startup_timeout_seconds": 120},
        "metrics": {"path": "/metrics", "format": "prometheus", "slots_path": "/slots", "metric_prefix": "llamacpp:"}
    });
    let ep = Uuid::new_v4();
    let s = hub.command(node, cmd(json!({"command": "start_engine", "args": {"engine_process_id": ep, "engine": engine, "devices": [{"device_id": dev_id, "index": 0, "vendor": "apple"}], "port": 0, "launch": {"flags": {"jinja": true}}}})));
    let r = done(&hub, s, 120).await;
    let router_port = r["port"].as_u64().unwrap();
    println!("router: port {router_port} pid {}", r["pid"]);

    let inst = Uuid::new_v4();
    let dep = Uuid::new_v4();
    let model = json!({
        "model_id": Uuid::new_v4(), "model_version_id": Uuid::new_v4(), "name": "qwen3:0.6b", "served_model_name": "qwen3-0.6b",
        "type": "chat", "format": "gguf",
        "files": [{"model_file_id": Uuid::new_v4(), "path": QWEN_BLOB, "format": "gguf", "size_bytes": 522640096u64, "sha256": QWEN_SHA}],
        "cache_dir": "", "source": {"kind": "local_path", "repo_id": blobs.display().to_string()},
        "config": {"num_hidden_layers": 28, "num_key_value_heads": 8, "head_dim": 128}
    });
    let l = hub.command(node, cmd(json!({"command": "load_model", "timeout_seconds": 300, "args": {
        "instance_id": inst, "deployment_id": dep, "engine": engine, "model": model,
        "devices": [{"device_id": dev_id, "index": 0, "vendor": "apple"}], "port": 0, "engine_process_id": ep,
        "launch": {"context_size": 8192, "max_concurrency": 2, "flags": {"n-gpu-layers": 99}}
    }})));
    let r = done(&hub, l, 300).await;
    println!("load_model: {}", serde_json::to_string(&r).unwrap());
    assert_eq!(r["port"].as_u64().unwrap(), router_port);
    // The router restarted to register the model: its pid is the one load_model reports.
    let router_pid = r["pid"].as_u64().unwrap() as u32;

    hub.send(Some(node), Message::ConfigPush(serde_json::from_value(json!({
        "config_version": 1, "full": true,
        "routing": [{"instance_id": inst, "deployment_id": dep, "port": router_port, "served_model_name": "qwen3-0.6b", "engine": "llama_cpp", "allowed_path_prefixes": ["/v1/chat/completions", "/v1/completions", "/health"]}]
    })).unwrap()));
    hub.wait_for(
        0,
        Duration::from_secs(10),
        |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::ConfigApplied),
    )
    .await
    .expect("config applied");

    let api = hub.issue_client("api-layer", "gpudeck-services");
    let tls = gpudeck_agent::tls::node_proxy_client_config(
        &hub.ca_pem(),
        &api.cert_pem,
        api.key_pem.as_deref().unwrap(),
        &node.to_string(),
    )
    .unwrap();
    let client = reqwest::Client::builder()
        .use_preconfigured_tls(tls)
        .no_proxy()
        .timeout(Duration::from_secs(120))
        .build()
        .unwrap();
    let url = format!("https://127.0.0.1:{proxy_port}/instances/{inst}/v1/chat/completions");
    let t0 = std::time::Instant::now();
    let r = client.post(&url).header("x-request-id", Uuid::new_v4().to_string()).json(&json!({"model": "qwen3:0.6b", "messages": [{"role": "user", "content": "Say hello in five words. /no_think"}], "max_tokens": 64})).send().await.unwrap();
    assert_eq!(r.status(), 200, "{:?}", r.text().await);
    let body: Value = r.json().await.unwrap();
    println!(
        "chat ({} ms): model={} usage={} content={:?}",
        t0.elapsed().as_millis(),
        body["model"],
        body["usage"],
        body["choices"][0]["message"]["content"]
    );
    assert_eq!(body["model"], "qwen3-0.6b");
    let r = client.post(&url).json(&json!({"model": "qwen3:0.6b", "stream": true, "stream_options": {"include_usage": true}, "messages": [{"role": "user", "content": "Count to ten. /no_think"}], "max_tokens": 64})).send().await.unwrap();
    let text = r.text().await.unwrap();
    println!("stream: {} SSE data frames", text.matches("data: ").count());
    assert!(text.contains("[DONE]"));
    let blocked = client
        .get(format!(
            "https://127.0.0.1:{proxy_port}/instances/{inst}/slots"
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(blocked.status(), 403);

    let em = hub
        .wait_for(0, Duration::from_secs(30), |e| matches!(&e.message, Message::MetricsBatch(b) if b.engine.iter().any(|s| s.instance_id == inst && s.generated_tokens_total.unwrap_or(0) > 0 && s.requests_total.unwrap_or(0) >= 2)))
        .await
        .expect("engine metrics");
    let Message::MetricsBatch(b) = em.message else {
        unreachable!()
    };
    let s = b.engine.iter().find(|s| s.instance_id == inst).unwrap();
    println!(
        "engine sample: requests={:?} prompt={:?} generated={:?} cached={:?} slots={:?}/{:?} kv_total={:?} gen_tps={:?} ttft_p95={:?} rss={:?} health={:?}",
        s.requests_total, s.prompt_tokens_total, s.generated_tokens_total, s.cached_tokens_total, s.slots_idle, s.slots_total, s.kv_cache_total_tokens, s.generation_tps, s.ttft_ms_p95, s.process_rss_bytes, s.health
    );
    assert!(s.prompt_tokens_total.unwrap_or(0) > 0);
    assert_eq!(s.health, Some(InstanceHealth::Healthy));

    // Agent restart (upgrade): the router and its model keep serving and are re-adopted.
    unsafe { libc::kill(agent.id().unwrap() as i32, libc::SIGTERM) };
    tokio::time::timeout(Duration::from_secs(30), agent.wait())
        .await
        .expect("agent exits")
        .unwrap();
    assert!(
        gpudeck_agent::supervisor::pid_alive(router_pid),
        "router kept running while the agent was down"
    );
    let mark = hub.received().len();
    let t_restart = std::time::Instant::now();
    let log = std::fs::OpenOptions::new()
        .append(true)
        .open(dir.path().join("agent.log"))
        .unwrap();
    agent = tokio::process::Command::new(bin)
        .args(["--config", cfg.to_str().unwrap(), "run"])
        .stdout(Stdio::from(log.try_clone().unwrap()))
        .stderr(Stdio::from(log))
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    hub.wait_for(mark, Duration::from_secs(30), |e| {
        matches!(&e.message, Message::Inventory(i) if i.instances.iter().any(|s| s.instance_id == inst && s.state == InstanceState::Ready))
    })
    .await
    .expect("re-adopted instance in the first inventory");
    println!(
        "agent restarted, instance ready again after {} ms",
        t_restart.elapsed().as_millis()
    );
    let r = client.post(&url).json(&json!({"model": "qwen3:0.6b", "messages": [{"role": "user", "content": "Say hi. /no_think"}], "max_tokens": 16})).send().await.unwrap();
    assert_eq!(r.status(), 200, "chat after the agent restart");
    assert!(gpudeck_agent::supervisor::pid_alive(router_pid));
    let started_again =
        hub.received().iter().skip(mark).any(
            |e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::EngineStarted),
        );
    assert!(!started_again, "nothing relaunched after the agent restart");

    let u = hub.command(node, cmd(json!({"command": "unload_model", "args": {"instance_id": inst, "drain_timeout_seconds": 10}})));
    println!(
        "unload: {}",
        serde_json::to_string(&done(&hub, u, 60).await).unwrap()
    );
    let st = hub.command(
        node,
        cmd(json!({"command": "stop_engine", "args": {"engine_process_id": ep}})),
    );
    done(&hub, st, 60).await;

    // Graceful agent shutdown.
    unsafe { libc::kill(agent.id().unwrap() as i32, libc::SIGTERM) };
    let status = tokio::time::timeout(Duration::from_secs(30), agent.wait())
        .await
        .expect("agent exits")
        .unwrap();
    println!("agent exited: {status}");
    let left = std::process::Command::new("pgrep")
        .args(["-f", &format!("--port {router_port}")])
        .output()
        .unwrap();
    assert!(
        String::from_utf8_lossy(&left.stdout).trim().is_empty(),
        "router still running"
    );
    let log = std::fs::read_to_string(dir.path().join("agent.log")).unwrap_or_default();
    println!(
        "--- agent log (tail) ---\n{}",
        log.lines()
            .rev()
            .take(25)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect::<Vec<_>>()
            .join("\n")
    );
}
