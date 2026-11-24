//! Adapter tests: command building per engine, metric mapping from recorded fixtures (real
//! llama.cpp / MLX captures, documented formats for the rest), and HTTP behaviour against a
//! scripted fake engine.

mod common;

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use common::{json, read, serve, Routes};
use gpudeck_devices::{AssignedDevice, Slice, Vendor};
use gpudeck_engines::state::{self, Hints};
use gpudeck_engines::*;
use serde_json::json;

fn apple() -> AssignedDevice {
    AssignedDevice {
        vendor: Vendor::Apple,
        index: 0,
        uuid: None,
        slice: None,
    }
}

fn nvidia(i: u32, slice: Option<Slice>) -> AssignedDevice {
    AssignedDevice {
        vendor: Vendor::Nvidia,
        index: i,
        uuid: Some(format!("GPU-{i}")),
        slice,
    }
}

fn pos(args: &[String], flag: &str) -> Option<String> {
    args.iter()
        .position(|a| a == flag)
        .and_then(|i| args.get(i + 1).cloned())
}

// ---------------------------------------------------------------- llama.cpp: command

#[test]
fn llama_single_model_command() {
    let mut spec = LaunchSpec::new(
        EngineKind::LlamaCpp,
        "qwen3-0.6b",
        "/blobs/sha256-7f40",
        10240,
    );
    spec.devices = vec![apple()];
    spec.context_size = Some(4096);
    spec.max_concurrency = Some(2);
    spec.model_family = Some("qwen3".into());
    spec.model_config = json!({"num_hidden_layers": 28, "num_attention_heads": 16, "num_key_value_heads": 8, "head_dim": 128}).as_object().unwrap().clone();
    spec.executable = Some("/opt/llama/bin/llama-server".into());
    spec.library_dir = Some("/opt/llama/bin".into());
    spec.control_vectors = vec![
        ControlVector {
            path: "/cv/happy.gguf".into(),
            scale: 0.8,
            layer_range: Some((10, 20)),
        },
        ControlVector {
            path: "/cv/calm.gguf".into(),
            scale: 1.0,
            layer_range: None,
        },
    ];
    let p = adapter_for(EngineKind::LlamaCpp).command(&spec).unwrap();
    assert_eq!(p.program, "/opt/llama/bin/llama-server");
    let a = &p.args;
    assert_eq!(pos(a, "--model").as_deref(), Some("/blobs/sha256-7f40"));
    assert_eq!(pos(a, "--alias").as_deref(), Some("qwen3-0.6b"));
    assert_eq!(pos(a, "--host").as_deref(), Some("127.0.0.1"));
    assert_eq!(pos(a, "--port").as_deref(), Some("10240"));
    assert_eq!(pos(a, "--parallel").as_deref(), Some("2"));
    assert_eq!(
        pos(a, "--ctx-size").as_deref(),
        Some("8192"),
        "per-request context × slots"
    );
    assert_eq!(pos(a, "--n-gpu-layers").as_deref(), Some("999"));
    assert_eq!(pos(a, "--reasoning-format").as_deref(), Some("deepseek"));
    for f in ["--metrics", "--slots", "--jinja"] {
        assert!(a.contains(&f.to_string()), "{f}");
    }
    assert_eq!(
        pos(a, "--control-vector-scaled").as_deref(),
        Some("/cv/happy.gguf:0.8,/cv/calm.gguf:1")
    );
    let i = a
        .iter()
        .position(|x| x == "--control-vector-layer-range")
        .unwrap();
    assert_eq!(&a[i + 1..i + 3], ["10", "20"]);
    let lib_var = if cfg!(target_os = "macos") {
        "DYLD_LIBRARY_PATH"
    } else {
        "LD_LIBRARY_PATH"
    };
    assert_eq!(p.env[lib_var], "/opt/llama/bin");
    assert!(p.container.is_none());
    let h = state::hints(&spec.base_url()).unwrap();
    assert_eq!(
        (h.context_size, h.max_concurrency, h.kv_bytes_per_token),
        (Some(4096), Some(2), Some(114_688))
    );
}

#[test]
fn llama_flags_presets_and_model_types() {
    let mut spec = LaunchSpec::new(EngineKind::LlamaCpp, "m", "/m.gguf", 10241);
    spec.devices = vec![nvidia(1, None)];
    spec.preset = Some(Preset::HighThroughput);
    spec.launch_flags = BTreeMap::from([
        ("ctk".to_string(), json!("q8_0")),
        ("-c".to_string(), json!(32768)),
        ("jinja".to_string(), json!(null)),
        ("ngl".to_string(), json!(40)),
    ]);
    let (a, h) = gpudeck_engines::llama_cpp::LlamaCppAdapter::build_args(&spec).unwrap();
    assert_eq!(pos(&a, "--parallel").as_deref(), Some("8"), "preset");
    assert_eq!(
        pos(&a, "-c").as_deref(),
        Some("32768"),
        "explicit total context kept"
    );
    assert_eq!(h.context_size, Some(4096), "32768 / 8 slots");
    assert_eq!(pos(&a, "-ngl").as_deref(), Some("40"));
    assert_eq!(pos(&a, "-ctk").as_deref(), Some("q8_0"));
    assert!(!a.contains(&"--jinja".to_string()), "null removes");
    assert_eq!(pos(&a, "--flash-attn").as_deref(), Some("on"));

    let mut emb = LaunchSpec::new(EngineKind::LlamaCpp, "e", "/e.gguf", 10242);
    emb.model_type = ModelType::Embedding;
    let (a, _) = gpudeck_engines::llama_cpp::LlamaCppAdapter::build_args(&emb).unwrap();
    assert!(a.contains(&"--embeddings".to_string()) && !a.contains(&"--jinja".to_string()));
    assert_eq!(
        pos(&a, "--n-gpu-layers").as_deref(),
        Some("0"),
        "no device assigned → CPU"
    );

    let mut tts = emb.clone();
    tts.model_type = ModelType::Tts;
    assert!(adapter_for(EngineKind::LlamaCpp).command(&tts).is_err());
    let mut sd = emb.clone();
    sd.model_type = ModelType::Chat;
    sd.spec_decoding = SpecDecoding {
        method: Some("eagle".into()),
        ..Default::default()
    };
    assert!(adapter_for(EngineKind::LlamaCpp).command(&sd).is_err());
    sd.spec_decoding = SpecDecoding {
        method: Some("draft_model".into()),
        draft_model_path: Some("/d.gguf".into()),
        num_draft_tokens: Some(8),
        ..Default::default()
    };
    let (a, _) = gpudeck_engines::llama_cpp::LlamaCppAdapter::build_args(&sd).unwrap();
    assert_eq!(pos(&a, "--model-draft").as_deref(), Some("/d.gguf"));
    assert_eq!(pos(&a, "--draft-max").as_deref(), Some("8"));
}

#[test]
fn llama_router_command_and_preset_ini() {
    let mut spec = LaunchSpec::new(EngineKind::LlamaCpp, "router", "", 10250);
    spec.devices = vec![apple()];
    spec.router_mode = true;
    assert!(
        adapter_for(EngineKind::LlamaCpp).command(&spec).is_err(),
        "router needs models_dir/preset"
    );
    spec.models_dir = Some("/state/router/models".into());
    spec.models_preset = Some("/state/router/models.ini".into());
    spec.models_max = Some(3);
    let p = adapter_for(EngineKind::LlamaCpp).command(&spec).unwrap();
    assert_eq!(
        pos(&p.args, "--models-dir").as_deref(),
        Some("/state/router/models")
    );
    assert_eq!(
        pos(&p.args, "--models-preset").as_deref(),
        Some("/state/router/models.ini")
    );
    assert_eq!(pos(&p.args, "--models-max").as_deref(), Some("3"));
    assert!(p.args.contains(&"--no-models-autoload".to_string()));
    assert!(!p.args.contains(&"--model".to_string()) && !p.args.contains(&"--alias".to_string()));

    let ini = gpudeck_engines::llama_cpp::router_preset_ini(
        &BTreeMap::from([("--n-gpu-layers".to_string(), json!(999))]),
        &[gpudeck_engines::llama_cpp::RouterModel {
            name: "qwen3-0.6b".into(),
            model_path: "/cache/q.gguf".into(),
            mmproj_path: None,
            flags: BTreeMap::from([
                ("ctx-size".to_string(), json!(8192)),
                ("cache-type-k".to_string(), json!("q8_0")),
                ("no-x".to_string(), json!(false)),
            ]),
            load_on_startup: false,
        }],
    );
    assert_eq!(
        ini,
        "version = 1\n\n[*]\nn-gpu-layers = 999\n\n[qwen3-0.6b]\nmodel = /cache/q.gguf\nload-on-startup = false\ncache-type-k = q8_0\nctx-size = 8192\n"
    );
    assert_eq!(
        gpudeck_engines::llama_cpp::instance_url("http://127.0.0.1:10250/", Some("org/m:Q4")),
        "http://127.0.0.1:10250?model=org%2Fm%3AQ4"
    );
}

// ---------------------------------------------------------------- llama.cpp: metrics (real captures)

#[test]
fn llama_metrics_and_slots_from_real_capture() {
    use gpudeck_engines::llama_cpp::{prev_from, sample_from};
    let t0 = Instant::now();
    let idle = prometheus::parse(&read("llama_cpp/metrics_idle.txt"));
    let busy = prometheus::parse(&read("llama_cpp/metrics_busy.txt"));
    let after = prometheus::parse(&read("llama_cpp/metrics_after.txt"));
    let hints = Hints {
        kv_bytes_per_token: Some(114_688),
        ..Default::default()
    };

    let s0 = sample_from(
        &idle,
        Some(&json("llama_cpp/slots_idle.json")),
        Some(&hints),
        None,
        t0,
    );
    assert_eq!(
        (s0.active_sessions, s0.waiting_sessions),
        (Some(0), Some(0))
    );
    assert_eq!((s0.slots_total, s0.slots_idle), (Some(2), Some(2)));
    assert_eq!(s0.kv_cache_total_tokens, Some(4096));
    assert_eq!(s0.kv_cache_used_tokens, Some(0));
    assert_eq!(s0.prompt_tps, None, "no rate without history");
    assert!(s0.sessions.is_empty());

    let p0 = prev_from(&idle, t0);
    let t1 = t0 + Duration::from_secs(2);
    let s1 = sample_from(
        &busy,
        Some(&json("llama_cpp/slots_busy.json")),
        Some(&hints),
        Some(&p0),
        t1,
    );
    assert_eq!(s1.active_sessions, Some(1));
    assert_eq!(s1.slots_idle, Some(1));
    assert_eq!(s1.prompt_tokens_total, Some(44));
    assert_eq!(s1.generated_tokens_total, Some(27));
    assert_eq!(s1.prompt_tps, Some(22.0));
    assert_eq!(s1.generation_tps, Some(13.5));
    assert_eq!(s1.sessions.len(), 1);
    let ss = &s1.sessions[0];
    assert_eq!(ss.slot_id, Some(1));
    assert_eq!(ss.session_key, "slot-1:31");
    assert_eq!(ss.state, SessionState::Generating);
    assert_eq!(ss.generated_tokens, Some(201));
    assert_eq!(ss.context_size, Some(2048));
    assert_eq!(ss.kv_cache_bytes, Some(201 * 114_688));
    assert_eq!(s1.kv_cache_used_tokens, Some(201));
    assert_eq!(
        s1.extra.as_ref().unwrap()["kv_cache_used_is_lower_bound"],
        json!(true)
    );
    assert!((s1.kv_cache_usage_pct.unwrap() - 201.0 * 100.0 / 4096.0).abs() < 1e-9);

    let p1 = prev_from(&busy, t1);
    let s2 = sample_from(
        &after,
        Some(&json("llama_cpp/slots_idle.json")),
        Some(&hints),
        Some(&p1),
        t1 + Duration::from_secs(8),
    );
    assert_eq!(s2.generation_tps, Some(600.0 / 8.0));
    let tpot = s2.tpot_ms_avg.unwrap();
    assert!((tpot - 3802.0 / 600.0).abs() < 1e-6, "tpot {tpot}");
    assert!(
        s2.extra.as_ref().unwrap()["decode_tps_per_slot"]
            .as_f64()
            .unwrap()
            > 150.0
    );

    // Wire shape matches common.json EngineSample (+ sessions split out).
    let wire = s1.to_wire(
        "6f1c2a3b-0000-4000-8000-000000000001",
        "6f1c2a3b-0000-4000-8000-000000000002",
    );
    assert!(wire.get("sessions").is_none() && wire["instance_id"].is_string());
    assert_eq!(
        s1.sessions_to_wire("6f1c2a3b-0000-4000-8000-000000000001")[0]["state"],
        "generating"
    );
}

#[test]
fn llama_kv_unified_uses_max_slot_ctx() {
    let slots = json("llama_cpp/router/slots.json");
    let hints = Hints {
        kv_unified: true,
        ..Default::default()
    };
    let s = gpudeck_engines::llama_cpp::sample_from(
        &prometheus::parse(&read("llama_cpp/router/metrics.txt")),
        Some(&slots),
        Some(&hints),
        None,
        Instant::now(),
    );
    assert_eq!(s.slots_total, Some(4));
    assert_eq!(s.kv_cache_total_tokens, Some(4096));
}

#[test]
fn llama_router_status_from_real_capture() {
    use gpudeck_engines::llama_cpp::router_status;
    assert_eq!(
        router_status(&json("llama_cpp/router/models_unloaded.json"), "qwen3-0.6b")
            .unwrap()
            .0,
        "unloaded"
    );
    assert_eq!(
        router_status(&json("llama_cpp/router/models_loading.json"), "qwen3-0.6b")
            .unwrap()
            .0,
        "loading"
    );
    assert_eq!(
        router_status(&json("llama_cpp/router/models_loaded.json"), "qwen3-0.6b")
            .unwrap()
            .0,
        "loaded"
    );
    assert!(router_status(&json("llama_cpp/router/models_loaded.json"), "other").is_none());
    let failed = json!({"data": [{"id": "m", "status": {"value": "unloaded", "failed": true, "exit_code": 1}}]});
    assert_eq!(
        router_status(&failed, "m"),
        Some(("unloaded".into(), true, Some(1)))
    );
}

// ---------------------------------------------------------------- llama.cpp: HTTP against a fake router

#[tokio::test]
async fn llama_router_load_scrape_unload_over_http() {
    let routes = Routes::default();
    routes
        .on(
            "POST /models/load",
            200,
            read("llama_cpp/router/load_response.json"),
        )
        .on(
            "GET /models",
            200,
            read("llama_cpp/router/models_loading.json"),
        )
        .on(
            "GET /models",
            200,
            read("llama_cpp/router/models_loaded.json"),
        )
        .on(
            "GET /metrics?model=qwen3-0.6b",
            200,
            read("llama_cpp/router/metrics.txt"),
        )
        .on(
            "GET /slots?model=qwen3-0.6b",
            200,
            read("llama_cpp/router/slots.json"),
        )
        .on("GET /props?model=qwen3-0.6b", 200, "{}".to_string())
        .on(
            "POST /models/unload",
            200,
            read("llama_cpp/router/unload_response.json"),
        )
        .on("GET /health", 200, read("llama_cpp/health.json"));
    let base = serve(routes.clone()).await;
    let a = adapter_for(EngineKind::LlamaCpp);
    let inst = gpudeck_engines::llama_cpp::instance_url(&base, Some("qwen3-0.6b"));
    a.load_model(&base, &ModelRef::named("qwen3-0.6b"))
        .await
        .unwrap();
    assert_eq!(a.health(&inst).await, HealthStatus::Healthy);
    assert_eq!(a.health(&base).await, HealthStatus::Healthy);
    let s = a.scrape(&inst).await.unwrap();
    assert_eq!(s.slots_total, Some(4));
    assert_eq!(s.prompt_tokens_total, Some(15));
    assert_eq!(s.health, Some(InstanceHealth::Healthy));
    // Unload: switch /models to unloaded.
    routes.on(
        "GET /models",
        200,
        read("llama_cpp/router/models_unloaded.json"),
    );
    // (the scripted list serves loaded once more, then unloaded)
    a.unload_model(&base, "qwen3-0.6b").await.unwrap();
    let posted: Vec<_> = routes
        .requests()
        .into_iter()
        .filter(|(k, _)| k.starts_with("POST"))
        .collect();
    assert_eq!(posted[0].1, r#"{"model":"qwen3-0.6b"}"#);
    assert_eq!(posted.len(), 2);
}

#[tokio::test]
async fn llama_router_errors_are_mapped() {
    let routes = Routes::default();
    routes
        .on("POST /models/load", 404, r#"{"error":{"message":"File Not Found","type":"not_found_error","code":404}}"#)
        .on("POST /models/unload", 400, r#"{"error":{"code":400,"message":"model is not loaded","type":"invalid_request_error"}}"#)
        .on("GET /health", 503, r#"{"error":{"code":503,"message":"Loading model"}}"#)
        .on("GET /models", 200, json!({"data": [{"id": "bad", "status": {"value": "unloaded", "failed": true, "exit_code": 1}}]}).to_string());
    let base = serve(routes).await;
    let a = adapter_for(EngineKind::LlamaCpp);
    let err = a
        .load_model(&base, &ModelRef::named("new-model"))
        .await
        .unwrap_err();
    assert!(
        err.downcast_ref::<gpudeck_engines::llama_cpp::RouterError>()
            .is_some(),
        "{err}"
    );
    a.unload_model(&base, "x").await.unwrap();
    assert_eq!(a.health(&base).await, HealthStatus::Loading);
    let h = a.health(&format!("{base}?model=bad")).await;
    assert!(
        matches!(h, HealthStatus::Unhealthy { ref reason } if reason.contains("failed")),
        "{h:?}"
    );
}

#[tokio::test]
async fn unreachable_engine_is_unhealthy_and_scrape_errors() {
    let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://{}", l.local_addr().unwrap());
    drop(l);
    for kind in [
        EngineKind::LlamaCpp,
        EngineKind::Vllm,
        EngineKind::Mlx,
        EngineKind::WhisperCpp,
    ] {
        let a = adapter_for(kind);
        assert!(
            matches!(a.health(&base).await, HealthStatus::Unhealthy { .. }),
            "{kind}"
        );
        assert!(a.scrape(&base).await.is_err(), "{kind}");
    }
}

// ---------------------------------------------------------------- MLX (real captures)

#[test]
fn mlx_command_and_upstream_name() {
    let mut spec = LaunchSpec::new(
        EngineKind::Mlx,
        "gemma-3-1b",
        "/models/gemma-3-1b-it-qat-4bit",
        10260,
    );
    spec.devices = vec![apple()];
    spec.executable = Some("/Users/x/.venv/bin/mlx_lm.server".into());
    spec.launch_flags = BTreeMap::from([(
        "chat-template-args".to_string(),
        json!({"enable_thinking": false}),
    )]);
    let a = adapter_for(EngineKind::Mlx);
    let p = a.command(&spec).unwrap();
    assert_eq!(p.program, "/Users/x/.venv/bin/mlx_lm.server");
    assert_eq!(
        pos(&p.args, "--model").as_deref(),
        Some("/models/gemma-3-1b-it-qat-4bit")
    );
    assert_eq!(pos(&p.args, "--port").as_deref(), Some("10260"));
    assert_eq!(
        pos(&p.args, "--chat-template-args").as_deref(),
        Some(r#"{"enable_thinking":false}"#)
    );
    assert_eq!(a.upstream_model_name(&spec), "default_model");
    assert!(a.derive_from_proxy());
    assert!(!a.supports_hot_swap());
    let mut bad = spec.clone();
    bad.devices = vec![nvidia(0, None)];
    assert!(a.command(&bad).is_err());
    bad = spec.clone();
    bad.runtime = Runtime::Container;
    assert!(a.command(&bad).is_err());
}

#[test]
fn mlx_usage_from_real_responses_feeds_proxy_stats() {
    let mut r = RequestRecord {
        e2e_ms: 400.0,
        ttft_ms: Some(80.0),
        status: 200,
        ..Default::default()
    };
    r.absorb_usage(&json("mlx/chat_completion.json"));
    assert_eq!((r.prompt_tokens, r.completion_tokens), (Some(15), Some(5)));
    // Streaming: the last data frame before [DONE] carries usage.
    let sse = read("mlx/chat_completion_stream.sse");
    let last = sse
        .lines()
        .filter_map(|l| l.strip_prefix("data: "))
        .filter(|l| *l != "[DONE]")
        .map(|l| serde_json::from_str::<serde_json::Value>(l).unwrap())
        .rfind(|v| v.get("usage").is_some())
        .unwrap();
    let mut rs = RequestRecord {
        e2e_ms: 900.0,
        ttft_ms: Some(100.0),
        status: 200,
        streamed: true,
        ..Default::default()
    };
    rs.absorb_usage(&last);
    assert_eq!(
        (rs.prompt_tokens, rs.completion_tokens),
        (Some(13), Some(26))
    );

    let mut stats = ProxyStats {
        max_concurrent: Some(1),
        context_size: Some(4096),
        kv_bytes_per_token: Some(26_624),
        ..Default::default()
    };
    stats.record(r);
    stats.record(rs);
    let snap = stats.take_window(2.0);
    let mut sample = EngineSample::now();
    adapter_for(EngineKind::Mlx).apply_proxy_stats(&mut sample, &snap);
    assert_eq!(sample.requests_total, Some(2));
    assert_eq!(sample.prompt_tokens_total, Some(28));
    assert_eq!(sample.generated_tokens_total, Some(31));
    assert_eq!(sample.slots_total, Some(1));
    assert_eq!(sample.kv_cache_total_tokens, Some(4096));
    assert_eq!(sample.ttft_ms_avg, Some(90.0));
}

#[test]
fn llama_usage_with_cache_timings_from_real_capture() {
    let mut r = RequestRecord::default();
    let sse = read("llama_cpp/chat_completion_stream.sse");
    let last = sse
        .lines()
        .filter_map(|l| l.strip_prefix("data: "))
        .filter(|l| *l != "[DONE]")
        .map(|l| serde_json::from_str::<serde_json::Value>(l).unwrap())
        .rfind(|v| v.get("usage").is_some())
        .unwrap();
    r.absorb_usage(&last);
    assert_eq!(
        (r.prompt_tokens, r.completion_tokens, r.cached_tokens),
        (Some(16), Some(18), Some(3))
    );
}

// ---------------------------------------------------------------- vLLM / vLLM-Omni

#[test]
fn vllm_command_with_slice_parsers_and_spec_decoding() {
    let mut spec = LaunchSpec::new(EngineKind::Vllm, "qwen3-8b", "/cache/qwen3-8b", 10270);
    spec.devices = vec![
        nvidia(
            0,
            Some(Slice {
                vram_bytes: 40 << 30,
                compute_pct: 50,
            }),
        ),
        nvidia(
            1,
            Some(Slice {
                vram_bytes: 40 << 30,
                compute_pct: 50,
            }),
        ),
    ];
    spec.device_vram_total_bytes = Some(80 << 30);
    spec.tp_size = 2;
    spec.context_size = Some(32768);
    spec.model_family = Some("qwen3".into());
    spec.max_concurrency = Some(64);
    spec.preset = Some(Preset::HighThroughput);
    spec.spec_decoding = SpecDecoding {
        method: Some("eagle3".into()),
        draft_model_path: Some("/cache/eagle3".into()),
        num_draft_tokens: Some(3),
        ..Default::default()
    };
    spec.cache_dir = Some("/cache".into());
    let p = adapter_for(EngineKind::Vllm).command(&spec).unwrap();
    assert_eq!(p.program, "vllm");
    assert_eq!(&p.args[..2], ["serve", "/cache/qwen3-8b"]);
    let a = &p.args;
    assert_eq!(pos(a, "--served-model-name").as_deref(), Some("qwen3-8b"));
    assert_eq!(pos(a, "--tensor-parallel-size").as_deref(), Some("2"));
    assert_eq!(pos(a, "--gpu-memory-utilization").as_deref(), Some("0.5"));
    assert_eq!(pos(a, "--max-model-len").as_deref(), Some("32768"));
    assert_eq!(
        pos(a, "--max-num-seqs").as_deref(),
        Some("64"),
        "max_concurrency beats preset"
    );
    assert_eq!(pos(a, "--tool-call-parser").as_deref(), Some("hermes"));
    assert_eq!(pos(a, "--reasoning-parser").as_deref(), Some("qwen3"));
    assert!(a.contains(&"--enable-auto-tool-choice".to_string()));
    assert!(a.contains(&"--enable-prefix-caching".to_string()));
    let sc: serde_json::Value =
        serde_json::from_str(&pos(a, "--speculative-config").unwrap()).unwrap();
    assert_eq!(
        sc,
        json!({"method": "eagle3", "model": "/cache/eagle3", "num_speculative_tokens": 3})
    );
    assert_eq!(p.env["CUDA_VISIBLE_DEVICES"], "0,1");
    assert_eq!(p.env["HF_HUB_OFFLINE"], "1");

    let mut pd = LaunchSpec::new(EngineKind::Vllm, "m", "/m", 10271);
    pd.pd_split = true;
    pd.pd_role = Some("decode".into());
    let p = adapter_for(EngineKind::Vllm).command(&pd).unwrap();
    let kvc: serde_json::Value =
        serde_json::from_str(&pos(&p.args, "--kv-transfer-config").unwrap()).unwrap();
    assert_eq!(kvc["kv_role"], "kv_consumer");

    let mut omni = LaunchSpec::new(EngineKind::VllmOmni, "qwen3-tts", "/m/tts", 10272);
    omni.model_type = ModelType::Tts;
    let p = adapter_for(EngineKind::VllmOmni).command(&omni).unwrap();
    assert!(p.args.contains(&"--omni".to_string()));
    let mut on_mac = omni.clone();
    on_mac.devices = vec![apple()];
    assert!(adapter_for(EngineKind::VllmOmni).command(&on_mac).is_err());
}

#[test]
fn vllm_container_command() {
    let mut spec = LaunchSpec::new(EngineKind::Vllm, "m", "/cache/v1/model", 10273);
    spec.devices = vec![nvidia(0, None), nvidia(3, None)];
    spec.runtime = Runtime::Container;
    spec.cache_dir = Some("/cache".into());
    let p = adapter_for(EngineKind::Vllm).command(&spec).unwrap();
    let c = p.container.as_ref().unwrap();
    assert_eq!(p.program, "docker");
    assert_eq!(c.image, "vllm/vllm-openai:latest");
    assert_eq!(c.name, "gpudeck-vllm-10273");
    assert_eq!(c.device_args, vec!["--gpus", "\"device=0,3\""]);
    assert!(c.volumes.contains(&"/cache:/cache:ro".to_string()));
    let i = p.args.iter().position(|a| a == "--entrypoint").unwrap();
    assert_eq!(
        &p.args[i + 1..i + 4],
        ["vllm", "vllm/vllm-openai:latest", "serve"]
    );
    if cfg!(target_os = "linux") {
        assert!(c.host_network && p.args.contains(&"--ipc=host".to_string()));
        assert_eq!(pos(&p.args, "--host").as_deref(), Some("127.0.0.1"));
    } else {
        assert_eq!(c.ports, vec!["127.0.0.1:10273:10273"]);
        assert_eq!(pos(&p.args, "--host").as_deref(), Some("0.0.0.0"));
    }
    let mut mac = spec.clone();
    mac.engine = EngineKind::LlamaCpp;
    mac.devices = vec![apple()];
    assert!(
        adapter_for(EngineKind::LlamaCpp).command(&mac).is_err(),
        "no Metal in containers"
    );
}

#[test]
fn vllm_metrics_window() {
    use gpudeck_engines::vllm::sample_from;
    let t0 = Instant::now();
    let hints = Hints {
        max_concurrency: Some(64),
        kv_bytes_per_token: Some(147_456),
        ..Default::default()
    };
    let (s1, p1) = sample_from(
        &prometheus::parse(&read("vllm/metrics_1.txt")),
        Some(&hints),
        None,
        t0,
    );
    assert_eq!(s1.active_sessions, Some(4));
    assert_eq!(s1.requests_success_total, Some(100));
    assert_eq!(
        s1.requests_total,
        Some(100),
        "engine count, also for requests that bypass the proxy"
    );
    assert_eq!(s1.ttft_ms_avg, None, "no window on first scrape");
    let (s2, _) = sample_from(
        &prometheus::parse(&read("vllm/metrics_2.txt")),
        Some(&hints),
        Some(&p1),
        t0 + Duration::from_secs(2),
    );
    assert_eq!(
        (s2.active_sessions, s2.waiting_sessions),
        (Some(6), Some(2))
    );
    assert_eq!((s2.slots_total, s2.slots_idle), (Some(64), Some(58)));
    assert_eq!(s2.prompt_tokens_total, Some(62400));
    assert_eq!(s2.prompt_tps, Some(5200.0));
    assert_eq!(s2.generation_tps, Some(1000.0));
    assert_eq!(s2.cached_tokens_total, Some(26000));
    assert!((s2.prefix_cache_hit_rate.unwrap() - 6000.0 / 10400.0).abs() < 1e-9);
    assert_eq!(s2.spec_accept_rate, Some(0.5));
    assert!((s2.ttft_ms_avg.unwrap() - 25.0).abs() < 1e-6);
    let p50 = s2.ttft_ms_p50.unwrap();
    assert!((20.0..=40.0).contains(&p50), "p50 {p50}");
    assert!((s2.e2e_ms_avg.unwrap() - 1225.0).abs() < 1e-6);
    assert!((s2.tpot_ms_avg.unwrap() - 17.9).abs() < 1e-6);
    assert!((s2.queue_ms_avg.unwrap() - 15.0).abs() < 1e-6);
    assert_eq!(s2.kv_cache_total_tokens, Some(27713 * 16));
    assert!((s2.kv_cache_usage_pct.unwrap() - 47.0).abs() < 1e-9);
    assert_eq!(
        s2.kv_cache_bytes,
        Some(s2.kv_cache_used_tokens.unwrap() * 147_456)
    );
    assert_eq!(
        s2.extra.as_ref().unwrap()["num_preemptions_total"],
        json!(1.0)
    );

    let (omni, _) = sample_from(
        &prometheus::parse(&read("vllm_omni/metrics.txt")),
        None,
        None,
        t0,
    );
    assert_eq!(
        omni.extra.unwrap()["vllm_omni:video_jobs_pending"],
        json!(3.0)
    );
}

#[tokio::test]
async fn vllm_scrape_over_http_uses_history() {
    let routes = Routes::default();
    routes
        .on("GET /metrics", 200, read("vllm/metrics_1.txt"))
        .on("GET /metrics", 200, read("vllm/metrics_2.txt"))
        .on("GET /health", 200, "");
    let base = serve(routes).await;
    let a = adapter_for(EngineKind::Vllm);
    assert_eq!(a.health(&base).await, HealthStatus::Healthy);
    let s1 = a.scrape(&base).await.unwrap();
    tokio::time::sleep(Duration::from_millis(100)).await;
    let s2 = a.scrape(&base).await.unwrap();
    assert!(s1.prompt_tps.is_none() && s2.prompt_tps.unwrap() > 0.0);
    assert!(s2.ttft_ms_avg.is_some());
    gpudeck_engines::forget(&base);
}

// ---------------------------------------------------------------- SGLang

#[test]
fn sglang_command_and_metrics() {
    let mut spec = LaunchSpec::new(EngineKind::Sglang, "qwen3-8b", "/cache/qwen3-8b", 10280);
    spec.devices = vec![AssignedDevice {
        vendor: Vendor::Amd,
        index: 2,
        uuid: None,
        slice: Some(Slice {
            vram_bytes: 96 << 30,
            compute_pct: 50,
        }),
    }];
    spec.device_vram_total_bytes = Some(192 << 30);
    spec.env.insert("GPUDECK_DEVICE_CORES".into(), "304".into());
    spec.model_family = Some("qwen3".into());
    spec.pd_split = true;
    spec.pd_role = Some("prefill".into());
    let p = adapter_for(EngineKind::Sglang).command(&spec).unwrap();
    assert_eq!(p.program, "python3");
    assert_eq!(&p.args[..2], ["-m", "sglang.launch_server"]);
    assert_eq!(
        pos(&p.args, "--mem-fraction-static").as_deref(),
        Some("0.5")
    );
    assert_eq!(
        pos(&p.args, "--tool-call-parser").as_deref(),
        Some("qwen25")
    );
    assert_eq!(
        pos(&p.args, "--disaggregation-mode").as_deref(),
        Some("prefill")
    );
    assert!(p.args.contains(&"--enable-metrics".to_string()));
    assert_eq!(p.env["HIP_VISIBLE_DEVICES"], "2");
    assert!(
        p.env["HSA_CU_MASK"].starts_with("0:0x"),
        "AMD compute slice → CU mask"
    );
    assert!(!p.env.contains_key("GPUDECK_DEVICE_CORES"));

    let t0 = Instant::now();
    let hints = Hints {
        max_concurrency: Some(32),
        ..Default::default()
    };
    let (_, p1) = gpudeck_engines::sglang::sample_from(
        &prometheus::parse(&read("sglang/metrics_1.txt")),
        Some(&hints),
        None,
        t0,
    );
    let (s, _) = gpudeck_engines::sglang::sample_from(
        &prometheus::parse(&read("sglang/metrics_2.txt")),
        Some(&hints),
        Some(&p1),
        t0 + Duration::from_secs(2),
    );
    assert_eq!((s.active_sessions, s.waiting_sessions), (Some(8), Some(3)));
    assert_eq!(s.requests_total, Some(60));
    assert_eq!(s.generation_tps, Some(980.0));
    assert_eq!(s.prompt_tps, Some(3000.0));
    assert_eq!(s.kv_cache_used_tokens, Some(98000));
    assert_eq!(s.kv_cache_total_tokens, Some(412000));
    assert!((s.kv_cache_usage_pct.unwrap() - 23.8).abs() < 1e-9);
    assert_eq!(s.prefix_cache_hit_rate, Some(0.45));
    assert!((s.ttft_ms_avg.unwrap() - 250.0).abs() < 1e-6);
    assert_eq!(s.slots_idle, Some(24));

    let mut diff = LaunchSpec::new(EngineKind::SglangDiffusion, "flux", "/cache/flux", 10281);
    diff.model_type = ModelType::Image;
    diff.devices = vec![apple()];
    let a = adapter_for(EngineKind::SglangDiffusion);
    let p = a.command(&diff).unwrap();
    assert_eq!(
        (p.program.as_str(), p.args[0].as_str()),
        ("sglang", "serve")
    );
    assert!(a.derive_from_proxy());
}

// ---------------------------------------------------------------- TensorRT-LLM

#[test]
fn trtllm_command_and_iteration_stats() {
    let dir = tempfile::tempdir().unwrap();
    let mut spec = LaunchSpec::new(EngineKind::TensorrtLlm, "llama3-8b", "/cache/llama3", 10290);
    spec.devices = vec![nvidia(0, None)];
    spec.working_dir = Some(dir.path().to_string_lossy().into_owned());
    spec.model_family = Some("qwen3".into());
    spec.spec_decoding = SpecDecoding {
        method: Some("eagle".into()),
        draft_model_path: Some("/cache/eagle".into()),
        num_draft_tokens: Some(4),
        ..Default::default()
    };
    let p = adapter_for(EngineKind::TensorrtLlm).command(&spec).unwrap();
    assert_eq!(p.program, "trtllm-serve");
    assert_eq!(p.args[0], "/cache/llama3");
    assert_eq!(pos(&p.args, "--backend").as_deref(), Some("pytorch"));
    assert_eq!(pos(&p.args, "--tool_parser").as_deref(), Some("qwen3"));
    let yaml = std::fs::read_to_string(pos(&p.args, "--extra_llm_api_options").unwrap()).unwrap();
    assert!(
        yaml.contains("enable_iter_perf_stats: true")
            && yaml.contains("decoding_type: Eagle")
            && yaml.contains("max_draft_len: 4")
    );
    let mut amd = spec.clone();
    amd.devices = vec![AssignedDevice {
        vendor: Vendor::Amd,
        index: 0,
        uuid: None,
        slice: None,
    }];
    assert!(adapter_for(EngineKind::TensorrtLlm).command(&amd).is_err());

    let t0 = Instant::now();
    let (s1, p1) = gpudeck_engines::tensorrt_llm::sample_from(
        &json("tensorrt_llm/stats_1.json"),
        None,
        None,
        t0,
    );
    assert_eq!(
        (s1.active_sessions, s1.waiting_sessions, s1.slots_total),
        (Some(2), Some(1), Some(64))
    );
    assert_eq!(
        (s1.prompt_tokens_total, s1.generated_tokens_total),
        (Some(1024), Some(2))
    );
    assert_eq!(s1.kv_cache_total_tokens, Some(4096 * 32));
    let (s2, p2) = gpudeck_engines::tensorrt_llm::sample_from(
        &json("tensorrt_llm/stats_2.json"),
        None,
        Some(&p1),
        t0 + Duration::from_secs(2),
    );
    assert_eq!(
        (s2.prompt_tokens_total, s2.generated_tokens_total),
        (Some(1536), Some(7))
    );
    assert_eq!(s2.prompt_tps, Some(256.0));
    assert_eq!(s2.generation_tps, Some(2.5));
    assert_eq!(s2.kv_cache_used_tokens, Some(32 * 32));
    assert_eq!(s2.prefix_cache_hit_rate, Some(0.25));
    assert!((s2.tpot_ms_avg.unwrap() - 15.75).abs() < 1e-9);
    // The same batch again is not double counted.
    let (s3, _) = gpudeck_engines::tensorrt_llm::sample_from(
        &json("tensorrt_llm/stats_2.json"),
        None,
        Some(&p2),
        t0 + Duration::from_secs(4),
    );
    assert_eq!(s3.generated_tokens_total, Some(7));
}

// ---------------------------------------------------------------- whisper.cpp / sd.cpp / mlx-audio

#[test]
fn media_engine_commands() {
    let mut w = LaunchSpec::new(
        EngineKind::WhisperCpp,
        "whisper-large-v3",
        "/cache/ggml-large-v3.bin",
        10300,
    );
    w.model_type = ModelType::Stt;
    w.devices = vec![apple()];
    let a = adapter_for(EngineKind::WhisperCpp);
    let p = a.command(&w).unwrap();
    assert_eq!(p.program, "whisper-server");
    assert_eq!(
        pos(&p.args, "--inference-path").as_deref(),
        Some("/v1/audio/transcriptions")
    );
    assert_eq!(
        pos(&p.args, "--model").as_deref(),
        Some("/cache/ggml-large-v3.bin")
    );
    assert_eq!(a.health_config().path, "/");
    assert_eq!(a.allowed_path_prefixes(), vec!["/v1/audio/transcriptions"]);

    let mut s = LaunchSpec::new(
        EngineKind::StableDiffusionCpp,
        "sdxl",
        "/cache/sdxl.safetensors",
        10301,
    );
    s.model_type = ModelType::Image;
    let p = adapter_for(EngineKind::StableDiffusionCpp)
        .command(&s)
        .unwrap();
    assert_eq!(pos(&p.args, "--listen-port").as_deref(), Some("10301"));
    s.launch_flags
        .insert("diffusion-model".into(), json!("/cache/flux.gguf"));
    let p = adapter_for(EngineKind::StableDiffusionCpp)
        .command(&s)
        .unwrap();
    assert!(!p.args.contains(&"--model".to_string()));

    let mut m = LaunchSpec::new(
        EngineKind::MlxAudio,
        "kokoro",
        "mlx-community/Kokoro-82M-bf16",
        10302,
    );
    m.model_type = ModelType::Tts;
    m.devices = vec![apple()];
    let a = adapter_for(EngineKind::MlxAudio);
    let p = a.command(&m).unwrap();
    assert_eq!(p.program, "mlx_audio.server");
    assert_eq!(a.upstream_model_name(&m), "mlx-community/Kokoro-82M-bf16");
    m.model_type = ModelType::Chat;
    assert!(a.command(&m).is_err());
}

#[tokio::test]
async fn proxy_derived_engines_scrape_health_only() {
    let routes = Routes::default();
    routes.on("GET /health", 200, read("mlx/health.json"));
    let base = serve(routes).await;
    let s = adapter_for(EngineKind::Mlx).scrape(&base).await.unwrap();
    assert_eq!(s.health, Some(InstanceHealth::Healthy));
    assert_eq!(
        s.active_sessions, None,
        "engine provides nothing; proxy fills it"
    );
}

// ---------------------------------------------------------------- custom

#[tokio::test]
async fn custom_prometheus_engine() {
    let cfg: CustomEngineConfig =
        serde_json::from_value(json("custom/engine_config.json")).unwrap();
    let routes = Routes::default();
    routes
        .on("GET /metrics", 200, read("custom/metrics.txt"))
        .on("GET /health", 200, "ok");
    let base = serve(routes).await;
    let port: u16 = base.rsplit(':').next().unwrap().parse().unwrap();
    let mut spec = LaunchSpec::new(EngineKind::Custom, "tgi-model", "/cache/tgi", port);
    spec.custom = Some(cfg.clone());
    spec.context_size = Some(8192);
    spec.launch_flags
        .insert("max-batch-prefill-tokens".into(), json!(4096));
    let a = gpudeck_engines::custom::CustomAdapter::with_config(cfg);
    let p = a.command(&spec).unwrap();
    assert_eq!(p.program, "/opt/tgi/bin/text-generation-launcher");
    assert_eq!(
        p.args,
        [
            "--model-id",
            "/cache/tgi",
            "--hostname",
            "127.0.0.1",
            "--port",
            &port.to_string(),
            "--max-input-tokens",
            "8192",
            "--max-batch-prefill-tokens",
            "4096"
        ]
    );
    assert_eq!(p.env["HF_HUB_ENABLE_HF_TRANSFER"], "0");
    assert_eq!(a.allowed_path_prefixes(), vec!["/v1/", "/generate"]);
    // The generic adapter finds the registered config by base URL.
    let generic = adapter_for(EngineKind::Custom);
    assert_eq!(generic.health(&base).await, HealthStatus::Healthy);
    let s = generic.scrape(&base).await.unwrap();
    assert_eq!((s.active_sessions, s.waiting_sessions), (Some(3), Some(1)));
    assert_eq!(s.prompt_tokens_total, Some(9120));
    assert_eq!(s.requests_success_total, Some(41));
    assert_eq!(s.kv_cache_used_tokens, Some(2048));
}

#[tokio::test]
async fn custom_json_engine_with_hot_swap() {
    let cfg: CustomEngineConfig =
        serde_json::from_value(json("custom/json_engine_config.json")).unwrap();
    let routes = Routes::default();
    routes
        .on("GET /stats", 200, read("custom/stats.json"))
        .on("POST /admin/load", 200, "{}")
        .on("POST /admin/unload", 200, "{}")
        .on("GET /status", 200, "");
    let base = serve(routes.clone()).await;
    let port: u16 = base.rsplit(':').next().unwrap().parse().unwrap();
    let mut spec = LaunchSpec::new(EngineKind::Custom, "m", "/cache/m", port);
    spec.custom = Some(cfg.clone());
    let a = gpudeck_engines::custom::CustomAdapter::with_config(cfg);
    a.command(&spec).unwrap();
    assert!(a.supports_hot_swap());
    let s = a.scrape(&base).await.unwrap();
    assert_eq!(
        (s.active_sessions, s.waiting_sessions, s.prompt_tokens_total),
        (Some(2), Some(5), Some(1200))
    );
    assert_eq!(s.kv_cache_usage_pct, Some(37.5));
    a.load_model(
        &base,
        &ModelRef {
            name: "m2".into(),
            path: Some("/cache/m2".into()),
            extra: Default::default(),
        },
    )
    .await
    .unwrap();
    a.unload_model(&base, "m2").await.unwrap();
    let posts: Vec<_> = routes
        .requests()
        .into_iter()
        .filter(|(k, _)| k.starts_with("POST"))
        .collect();
    assert_eq!(posts[0].1, r#"{"model":"m2","path":"/cache/m2"}"#);
    assert!(gpudeck_engines::custom::CustomAdapter::new()
        .command(&LaunchSpec::new(EngineKind::Custom, "x", "/x", 1))
        .is_err());
}

// ---------------------------------------------------------------- registry-wide checks

#[test]
fn every_kind_has_an_adapter_with_sane_contract() {
    for kind in EngineKind::ALL {
        let a = adapter_for(kind);
        assert_eq!(a.kind(), kind);
        let mc = a.metrics_config();
        assert_eq!(
            mc.derive_from_proxy,
            mc.path.is_none(),
            "{kind}: engines without /metrics must derive from the proxy"
        );
        assert!(!a.allowed_path_prefixes().is_empty(), "{kind}");
        for admin in a.admin_paths() {
            assert!(
                !a.allowed_path_prefixes()
                    .iter()
                    .any(|p| admin.starts_with(p) && *p != "/"),
                "{kind}: admin {admin} is forwardable"
            );
        }
        // Wire names round-trip.
        assert_eq!(kind.as_str().parse::<EngineKind>().unwrap(), kind);
        assert_eq!(serde_json::to_value(kind).unwrap(), json!(kind.as_str()));
    }
    assert_eq!(all_adapters().len(), EngineKind::ALL.len());
}

#[test]
fn every_engine_fixture_is_listed_in_manifest() {
    let manifest = json("../manifest.json");
    let files = manifest["files"].as_object().unwrap();
    fn walk(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
        for e in std::fs::read_dir(dir).unwrap().flatten() {
            if e.path().is_dir() {
                walk(&e.path(), out);
            } else {
                out.push(e.path());
            }
        }
    }
    let mut all = Vec::new();
    walk(&common::fixture_dir(), &mut all);
    for p in all {
        let rel = p
            .strip_prefix(common::fixture_dir().parent().unwrap())
            .unwrap()
            .to_string_lossy()
            .replace('\\', "/");
        assert!(
            files.contains_key(&rel),
            "{rel} missing from fixtures/manifest.json"
        );
    }
}

#[tokio::test]
async fn llama_router_model_whose_child_does_not_answer_is_unhealthy() {
    // The router lists the model as loaded and answers /health itself; only the child's own
    // endpoint tells a hung or dead model process apart.
    let routes = Routes::default();
    routes
        .on(
            "GET /models",
            200,
            read("llama_cpp/router/models_loaded.json"),
        )
        .on("GET /health", 200, read("llama_cpp/health.json"))
        .on("GET /props?model=qwen3-0.6b", 502, "{}".to_string());
    let base = serve(routes.clone()).await;
    let a = adapter_for(EngineKind::LlamaCpp);
    let inst = gpudeck_engines::llama_cpp::instance_url(&base, Some("qwen3-0.6b"));
    assert!(matches!(
        a.health(&inst).await,
        HealthStatus::Unhealthy { .. }
    ));
    assert_eq!(a.health(&base).await, HealthStatus::Healthy); // the router itself is fine
}
