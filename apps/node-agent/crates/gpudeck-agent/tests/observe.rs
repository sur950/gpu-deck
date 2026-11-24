//! Observed (external) engines against a fake vLLM server serving the vLLM `/metrics` fixtures
//! (`fixtures/engines/vllm`, documented format; no real vLLM on the reference Mac). Checks the
//! read-only contract: only `GET /metrics`, `/health` and `/v1/models` reach the server, ids are
//! the protocol's uuid5 values, and a server that goes away is backed off.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use gpudeck_agent::backend::EngineCatalog;
use gpudeck_agent::config::ObserveConfig;
use gpudeck_agent::observe::{self, GpuProcs, Observer};
use gpudeck_protocol::{InstanceHealth, ObservedEngineKind};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use uuid::Uuid;

fn fixture(rel: &str) -> String {
    std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../fixtures/engines")
            .join(rel),
    )
    .unwrap()
}

type Log = Arc<Mutex<Vec<String>>>;

/// A fake vLLM: `/metrics` alternates the two fixtures, `/v1/models` the models fixture.
async fn fake_vllm() -> (u16, Log, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let log: Log = Arc::default();
    let seen = log.clone();
    let task = tokio::spawn(async move {
        let mut n = 0usize;
        loop {
            let Ok((mut sock, _)) = listener.accept().await else {
                return;
            };
            let mut buf = vec![0u8; 8192];
            let len = sock.read(&mut buf).await.unwrap_or(0);
            let head = String::from_utf8_lossy(&buf[..len]).to_string();
            let request_line = head.lines().next().unwrap_or("").to_string();
            seen.lock().unwrap().push(request_line.clone());
            let path = request_line.split(' ').nth(1).unwrap_or("");
            let (status, body) = match path {
                "/metrics" => {
                    n += 1;
                    let f = if n % 2 == 1 {
                        "vllm/metrics_1.txt"
                    } else {
                        "vllm/metrics_2.txt"
                    };
                    ("200 OK", fixture(f))
                }
                "/health" => ("200 OK", String::new()),
                "/v1/models" => ("200 OK", fixture("vllm/v1_models.json")),
                _ => ("404 Not Found", String::new()),
            };
            let resp = format!(
                "HTTP/1.1 {status}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            );
            let _ = sock.write_all(resp.as_bytes()).await;
        }
    });
    (port, log, task)
}

fn entry(name: &str, kind: ObservedEngineKind, url: String) -> ObserveConfig {
    ObserveConfig {
        name: name.into(),
        engine: kind,
        url: Some(url),
        metrics_path: None,
        model: None,
        log_file: None,
        listen_port: None,
        pid_cmd_contains: None,
        interval_seconds: Some(2.0),
    }
}

fn catalog() -> Arc<dyn EngineCatalog> {
    gpudeck_agent::backend::default_backends().1
}

#[tokio::test]
async fn scrapes_a_fake_vllm_read_only() {
    let (port, log, server) = fake_vllm().await;
    let node: Uuid = "6f1c2b2e-0b2b-4d8e-9e0e-1b2c3d4e5f60".parse().unwrap();
    let obs = Observer::new(
        &node,
        &[entry(
            "main",
            ObservedEngineKind::Vllm,
            format!("http://127.0.0.1:{port}"),
        )],
    );
    let cat = catalog();
    let gpu = GpuProcs::default();
    let first = obs.cycle(&*cat, &gpu, true).await;
    assert_eq!(first.samples.len(), 1);
    assert!(first.changed, "health and models became known");
    let s = &first.samples[0];
    assert!(s.observed);
    assert_eq!(s.instance_id, observe::observe_id(&node, "main"));
    assert_eq!(
        s.instance_id.to_string(),
        "4be123d1-7ce4-5141-bc91-0147ebbcfd96"
    );
    assert_eq!(
        s.deployment_id,
        observe::observe_deployment_id(&node, "main")
    );
    assert_eq!(s.health, Some(InstanceHealth::Healthy));
    #[cfg(feature = "plugins")]
    {
        assert_eq!(s.active_sessions, Some(4));
        assert_eq!(s.prompt_tokens_total, Some(52000));
    }
    let extra = s.extra.as_ref().unwrap();
    assert_eq!(extra["engine"], "vllm");
    assert_eq!(extra["served_model_name"], "Qwen/Qwen3-8B");
    let snap = &obs.snapshots()[0];
    assert_eq!(snap.models, vec!["Qwen/Qwen3-8B".to_string()]);
    assert_eq!(
        snap.endpoint.as_deref(),
        Some(&*format!("127.0.0.1:{port}"))
    );
    assert_eq!(snap.health, InstanceHealth::Healthy);

    // Not due again before interval_seconds (2 s): no request, no sample.
    let quiet = obs.cycle(&*cat, &gpu, true).await;
    assert!(quiet.samples.is_empty());
    tokio::time::sleep(Duration::from_millis(2100)).await;
    let second = obs.cycle(&*cat, &gpu, true).await;
    #[cfg(feature = "plugins")]
    assert!(second.samples[0].generation_tps.unwrap_or(0.0) > 0.0);
    assert_eq!(second.samples.len(), 1);

    // Read-only: GETs to the three allowed paths only.
    let requests = log.lock().unwrap().clone();
    assert!(!requests.is_empty());
    for r in &requests {
        let mut parts = r.split(' ');
        assert_eq!(parts.next(), Some("GET"), "{r}");
        assert!(
            matches!(parts.next(), Some("/metrics" | "/health" | "/v1/models")),
            "{r}"
        );
    }

    // The server goes away: unhealthy, then backoff (no request while backing off).
    server.abort();
    let _ = server.await;
    tokio::time::sleep(Duration::from_millis(2100)).await;
    let down = obs.cycle(&*cat, &gpu, true).await;
    assert_eq!(down.samples[0].health, Some(InstanceHealth::Unhealthy));
    assert!(down.changed);
    assert!(obs.snapshots()[0].message.is_some());
}

#[tokio::test]
async fn generic_health_only_and_gpu_memory_attribution() {
    let (port, log, server) = fake_vllm().await;
    let node = Uuid::new_v4();
    let mut e = entry(
        "img",
        ObservedEngineKind::Generic,
        format!("http://127.0.0.1:{port}"),
    );
    e.model = Some("example-image-model".into());
    let obs = Observer::new(&node, &[e]);
    let cat = catalog();
    let c = obs.cycle(&*cat, &GpuProcs::default(), false).await;
    let s = &c.samples[0];
    assert_eq!(s.health, Some(InstanceHealth::Healthy));
    assert_eq!(s.active_sessions, None, "generic = health only");
    assert_eq!(
        s.extra.as_ref().unwrap()["served_model_name"],
        "example-image-model"
    );
    // generic never asks for /metrics or /v1/models.
    assert_eq!(
        log.lock().unwrap().clone(),
        vec!["GET /health HTTP/1.1".to_string()]
    );
    server.abort();
}

/// VRAM of the listener's process tree (vLLM: the EngineCore child holds it) from the NVML
/// per-process list; other GPU processes are not attributed. Uses a fake procfs.
#[cfg(unix)]
#[tokio::test]
async fn gpu_memory_of_the_listener_process_tree() {
    let root = tempfile::tempdir().unwrap();
    let r = root.path();
    std::fs::create_dir_all(r.join("net")).unwrap();
    // 127.0.0.1:8100 (0x1FA4) LISTEN, inode 4343.
    std::fs::write(
        r.join("net/tcp"),
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n   0: 0100007F:1FA4 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 4343 1 0 100 0 0 10 0\n",
    )
    .unwrap();
    let mk = |pid: u32, ppid: u32| {
        let d = r.join(pid.to_string());
        std::fs::create_dir_all(d.join("fd")).unwrap();
        std::fs::write(d.join("stat"), format!("{pid} (python) S {ppid} 1 1")).unwrap();
        std::fs::write(d.join("cmdline"), "python\0server.py\0").unwrap();
        d
    };
    let api = mk(500, 1);
    std::os::unix::fs::symlink("socket:[4343]", api.join("fd/7")).unwrap();
    mk(501, 500); // child holding VRAM
    mk(900, 1); // unrelated GPU process (another tenant)
    let logf = r.join("img.log");
    std::fs::write(&logf, "").unwrap();
    let node = Uuid::new_v4();
    let e = ObserveConfig {
        name: "img".into(),
        engine: ObservedEngineKind::Generic,
        url: None,
        metrics_path: None,
        model: Some("example-image-model".into()),
        log_file: Some(logf.clone()),
        listen_port: Some(8100),
        pid_cmd_contains: None,
        interval_seconds: Some(2.0),
    };
    let obs = Observer::new(&node, &[e]).with_proc_root(r.to_path_buf());
    let (dev0, dev1) = (Uuid::new_v4(), Uuid::new_v4());
    let mut gpu = GpuProcs::default();
    gpu.insert(501, vec![(dev0, Some(30 << 30)), (dev1, Some(2 << 30))]);
    gpu.insert(900, vec![(dev0, Some(512 << 20))]);
    let c = obs.cycle(&*catalog(), &gpu, true).await;
    let s = &c.samples[0];
    assert_eq!(s.process_vram_bytes, Some(32 << 30));
    assert_eq!(
        s.extra.as_ref().unwrap()["gpu_pids"],
        serde_json::json!([501])
    );
    let snap = &obs.snapshots()[0];
    assert_eq!(snap.pid, Some(500));
    let mut want = vec![dev0, dev1];
    want.sort();
    assert_eq!(snap.device_ids, want);
    assert_eq!(snap.models, vec!["example-image-model".to_string()]);
    assert!(c.log_events.is_empty(), "nothing new in the log yet");
    // New access lines are counted and shipped (redacted) for the external instance.
    std::fs::write(
        &logf,
        "INFO:     127.0.0.1:1 - \"POST /v1/edit HTTP/1.1\" 200 OK\nusing token=abcdefgh1234\n",
    )
    .unwrap();
    tokio::time::sleep(Duration::from_millis(2100)).await;
    let c = obs.cycle(&*catalog(), &gpu, true).await;
    assert_eq!(c.samples[0].requests_total, Some(1));
    let ev = &c.log_events[0];
    assert_eq!(ev.instance_id, Some(observe::observe_id(&node, "img")));
    let lines: Vec<String> = ev
        .log
        .as_ref()
        .unwrap()
        .lines
        .iter()
        .map(|l| l.text.clone())
        .collect();
    assert_eq!(lines[1], "using token=[REDACTED]");
}
