//! Live engine tests (ignored by default; run with `cargo test -p gpudeck-engines --test live --
//! --ignored --test-threads=1`). Each test starts the real engine on a random loopback port using
//! the adapter's `command()`, waits for health, sends one chat completion, scrapes, and stops the
//! process. Paths default to the reference Mac and can be overridden:
//!
//! | Variable | Default |
//! | --- | --- |
//! | `GPUDECK_LIVE_LLAMA_SERVER` | `~/.unsloth/llama.cpp/build/bin/llama-server` |
//! | `GPUDECK_LIVE_GGUF` | qwen3:0.6b blob in `~/.ollama/models/blobs` |
//! | `GPUDECK_LIVE_MLX_SERVER` | `~/.venv/bin/mlx_lm.server` |
//! | `GPUDECK_LIVE_MLX_MODEL` | `~/.lmstudio/models/mlx-community/gemma-3-1b-it-qat-4bit` |

use std::path::PathBuf;
use std::process::Stdio;
use std::time::{Duration, Instant};

use gpudeck_devices::{AssignedDevice, Vendor};
use gpudeck_engines::*;
use serde_json::json;

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap())
}

fn env_path(var: &str, default: PathBuf) -> PathBuf {
    std::env::var(var).map(PathBuf::from).unwrap_or(default)
}

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

struct Running {
    child: tokio::process::Child,
}

impl Running {
    async fn start(p: &ProcessSpec, log: &str) -> Running {
        let log = std::fs::File::create(std::env::temp_dir().join(log)).unwrap();
        let mut cmd = tokio::process::Command::new(&p.program);
        cmd.args(&p.args)
            .envs(&p.env)
            .stdin(Stdio::null())
            .stdout(Stdio::from(log.try_clone().unwrap()))
            .stderr(Stdio::from(log))
            .kill_on_drop(true);
        if let Some(cwd) = &p.cwd {
            cmd.current_dir(cwd);
        }
        Running {
            child: cmd
                .spawn()
                .unwrap_or_else(|e| panic!("spawn {}: {e}", p.program)),
        }
    }

    async fn stop(mut self) {
        let _ = self.child.kill().await;
        let _ = self.child.wait().await;
    }
}

async fn wait_healthy(a: &dyn EngineAdapter, url: &str, timeout: Duration) -> HealthStatus {
    let deadline = Instant::now() + timeout;
    loop {
        let h = a.health(url).await;
        if h.is_healthy() || Instant::now() > deadline {
            return h;
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
    }
}

/// One non-streaming chat completion; returns the proxy-style record.
async fn chat(base: &str, model: &str) -> (serde_json::Value, RequestRecord) {
    let t0 = Instant::now();
    let resp = reqwest::Client::new()
        .post(format!("{base}/v1/chat/completions"))
        .json(&json!({"model": model, "messages": [{"role": "user", "content": "Say hi in three words. /no_think"}], "max_tokens": 24}))
        .timeout(Duration::from_secs(120))
        .send()
        .await
        .unwrap();
    let status = resp.status().as_u16();
    let body: serde_json::Value = resp.json().await.unwrap();
    let e2e = t0.elapsed().as_secs_f64() * 1000.0;
    let mut r = RequestRecord {
        e2e_ms: e2e,
        ttft_ms: Some(e2e),
        status,
        ..Default::default()
    };
    r.absorb_usage(&body);
    (body, r)
}

#[tokio::test]
#[ignore = "starts the real llama-server"]
async fn live_llama_cpp_single_model() {
    let exe = env_path(
        "GPUDECK_LIVE_LLAMA_SERVER",
        home().join(".unsloth/llama.cpp/build/bin/llama-server"),
    );
    let gguf = env_path(
        "GPUDECK_LIVE_GGUF",
        home().join(".ollama/models/blobs/sha256-7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa"),
    );
    let port = free_port();
    let mut spec = LaunchSpec::new(
        EngineKind::LlamaCpp,
        "qwen3-0.6b",
        gguf.to_string_lossy(),
        port,
    );
    spec.executable = Some(exe.to_string_lossy().into_owned());
    spec.devices = vec![AssignedDevice {
        vendor: Vendor::Apple,
        index: 0,
        uuid: None,
        slice: None,
    }];
    spec.context_size = Some(2048);
    spec.max_concurrency = Some(2);
    spec.model_family = Some("qwen3".into());
    spec.model_config = json!({"num_hidden_layers": 28, "num_attention_heads": 16, "num_key_value_heads": 8, "head_dim": 128})
        .as_object()
        .unwrap()
        .clone();
    let a = adapter_for(EngineKind::LlamaCpp);
    let p = a.command(&spec).unwrap();
    let run = Running::start(&p, "gpudeck-live-llama.log").await;
    let base = spec.base_url();
    let h = wait_healthy(a.as_ref(), &base, Duration::from_secs(120)).await;
    assert!(h.is_healthy(), "{h:?}");
    let first = a.scrape(&base).await.unwrap();
    assert_eq!(first.slots_total, Some(2));
    assert_eq!(first.kv_cache_total_tokens, Some(4096));
    let (body, rec) = chat(&base, &a.upstream_model_name(&spec)).await;
    assert_eq!(rec.status, 200, "{body}");
    assert!(rec.completion_tokens.unwrap_or(0) > 0);
    tokio::time::sleep(Duration::from_millis(300)).await;
    let s = a.scrape(&base).await.unwrap();
    println!("{}", serde_json::to_string_pretty(&s).unwrap());
    assert!(s.prompt_tokens_total.unwrap() >= rec.prompt_tokens.unwrap());
    assert!(s.generated_tokens_total.unwrap() >= rec.completion_tokens.unwrap());
    assert!(
        s.generation_tps.unwrap() > 0.0,
        "counter rate between scrapes"
    );
    assert_eq!(s.active_sessions, Some(0));
    run.stop().await;
    forget(&base);
    assert!(!a.health(&base).await.is_healthy());
}

#[tokio::test]
#[ignore = "starts the real llama-server in router mode"]
async fn live_llama_cpp_router_mode() {
    let exe = env_path(
        "GPUDECK_LIVE_LLAMA_SERVER",
        home().join(".unsloth/llama.cpp/build/bin/llama-server"),
    );
    let gguf = env_path(
        "GPUDECK_LIVE_GGUF",
        home().join(".ollama/models/blobs/sha256-7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa"),
    );
    let dir = tempfile::tempdir().unwrap();
    std::os::unix::fs::symlink(&gguf, dir.path().join("qwen3-0.6b.gguf")).unwrap();
    let port = free_port();
    let mut spec = LaunchSpec::new(EngineKind::LlamaCpp, "router", "", port);
    spec.executable = Some(exe.to_string_lossy().into_owned());
    spec.devices = vec![AssignedDevice {
        vendor: Vendor::Apple,
        index: 0,
        uuid: None,
        slice: None,
    }];
    spec.router_mode = true;
    spec.models_dir = Some(dir.path().to_string_lossy().into_owned());
    spec.models_max = Some(2);
    spec.context_size = Some(2048);
    spec.max_concurrency = Some(2);
    let a = adapter_for(EngineKind::LlamaCpp);
    let p = a.command(&spec).unwrap();
    let run = Running::start(&p, "gpudeck-live-llama-router.log").await;
    let base = spec.base_url();
    assert!(wait_healthy(a.as_ref(), &base, Duration::from_secs(60))
        .await
        .is_healthy());
    let inst = llama_cpp::instance_url(&base, Some("qwen3-0.6b"));
    assert!(!a.health(&inst).await.is_healthy(), "not loaded yet");
    let err = a
        .load_model(&base, &ModelRef::named("not-registered"))
        .await
        .unwrap_err();
    assert!(
        err.downcast_ref::<llama_cpp::RouterError>().is_some(),
        "{err}"
    );
    a.load_model(&base, &ModelRef::named("qwen3-0.6b"))
        .await
        .unwrap();
    assert!(a.health(&inst).await.is_healthy());
    let (body, rec) = chat(&base, "qwen3-0.6b").await;
    assert_eq!(rec.status, 200, "{body}");
    let s = a.scrape(&inst).await.unwrap();
    println!("{}", serde_json::to_string_pretty(&s).unwrap());
    assert_eq!(s.slots_total, Some(2));
    assert!(s.generated_tokens_total.unwrap() > 0);
    a.unload_model(&base, "qwen3-0.6b").await.unwrap();
    assert!(!a.health(&inst).await.is_healthy());
    run.stop().await;
    forget(&base);
}

#[tokio::test]
#[ignore = "starts the real mlx_lm.server"]
async fn live_mlx() {
    let exe = env_path(
        "GPUDECK_LIVE_MLX_SERVER",
        home().join(".venv/bin/mlx_lm.server"),
    );
    let model = env_path(
        "GPUDECK_LIVE_MLX_MODEL",
        home().join(".lmstudio/models/mlx-community/gemma-3-1b-it-qat-4bit"),
    );
    let port = free_port();
    let mut spec = LaunchSpec::new(EngineKind::Mlx, "gemma-3-1b", model.to_string_lossy(), port);
    spec.executable = Some(exe.to_string_lossy().into_owned());
    spec.devices = vec![AssignedDevice {
        vendor: Vendor::Apple,
        index: 0,
        uuid: None,
        slice: None,
    }];
    spec.context_size = Some(4096);
    spec.max_concurrency = Some(1);
    let a = adapter_for(EngineKind::Mlx);
    let p = a.command(&spec).unwrap();
    let run = Running::start(&p, "gpudeck-live-mlx.log").await;
    let base = spec.base_url();
    let h = wait_healthy(a.as_ref(), &base, Duration::from_secs(180)).await;
    assert!(h.is_healthy(), "{h:?}");
    let (body, rec) = chat(&base, &a.upstream_model_name(&spec)).await;
    assert_eq!(rec.status, 200, "{body}");
    assert_eq!(body["model"], "default_model");
    assert!(rec.completion_tokens.unwrap_or(0) > 0);
    let mut stats = ProxyStats {
        max_concurrent: Some(1),
        context_size: Some(4096),
        ..Default::default()
    };
    stats.record(rec);
    let mut s = a.scrape(&base).await.unwrap();
    a.apply_proxy_stats(&mut s, &stats.take_window(2.0));
    println!("{}", serde_json::to_string_pretty(&s).unwrap());
    assert_eq!(s.health, Some(InstanceHealth::Healthy));
    assert_eq!(s.requests_total, Some(1));
    assert!(s.generated_tokens_total.unwrap() > 0);
    assert_eq!(s.slots_total, Some(1));
    run.stop().await;
    forget(&base);
    assert!(!a.health(&base).await.is_healthy());
}
