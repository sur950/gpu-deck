//! Every message the agent emits (and every hub message it parses) is serialised and checked
//! against the JSON Schemas in `libs/contracts/agent-protocol` with a local `$ref` retriever.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;

use chrono::Utc;
use gpudeck_protocol::*;
use jsonschema::{Retrieve, Uri, Validator};
use serde_json::{json, Value};
use uuid::Uuid;

fn schema_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../../libs/contracts/agent-protocol")
}

struct LocalRetriever;

impl Retrieve for LocalRetriever {
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
        let path = schema_dir().join(&name);
        let text =
            std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        Ok(serde_json::from_str(&text)?)
    }
}

fn validator(name: &str) -> Validator {
    let text = std::fs::read_to_string(schema_dir().join(name)).expect("schema file");
    let schema: Value = serde_json::from_str(&text).unwrap();
    jsonschema::options()
        .with_retriever(LocalRetriever)
        .build(&schema)
        .expect("compile schema")
}

fn assert_valid(name: &str, value: &Value) {
    let v = validator(name);
    let errors: Vec<String> = v
        .iter_errors(value)
        .map(|e| format!("{} at {}", e, e.instance_path()))
        .collect();
    assert!(
        errors.is_empty(),
        "{name} violations for {}:\n{}",
        serde_json::to_string_pretty(value).unwrap(),
        errors.join("\n")
    );
}

fn uuid() -> Uuid {
    Uuid::new_v4()
}

fn envelope(message: Message) -> Value {
    serde_json::to_value(Envelope::new(Some(uuid()), message)).unwrap()
}

fn sample_device() -> Device {
    Device {
        device_id: None,
        vendor: DeviceVendor::Apple,
        index: 0,
        uuid: Some("apple-m5-ABC".into()),
        name: "Apple M5".into(),
        vram_total_bytes: 24_000_000_000,
        is_unified_memory: true,
        driver_version: Some("27.2".into()),
        runtime_version: Some("Metal 4".into()),
        compute_capability: Some("metal3".into()),
        pci_bus_id: None,
        numa_node: None,
        max_power_watts: None,
        core_count: Some(10),
        health: DeviceHealth::Healthy,
        health_reason: None,
        labels: BTreeMap::new(),
        extra: JsonObject::new(),
    }
}

fn sample_artifact() -> Artifact {
    let build = |name: &str, glibc: &str| Artifact {
        url: format!("https://github.com/ggml-org/llama.cpp/releases/download/{name}.tar.gz"),
        sha256: "ab".repeat(32),
        kind: ArtifactKind::Tarball,
        entrypoint: Some("llama-b12500/llama-server".into()),
        size_bytes: Some(1),
        strip_components: None,
        parts: vec![],
        pip_extras: vec![],
        python: None,
        build: None,
        min_glibc: Some(glibc.into()),
        fallbacks: vec![],
        container: None,
    };
    let mut art = build("b12500/llama-b12500-bin-ubuntu-rocm-10.0-x64", "2.38");
    art.fallbacks = vec![build(
        "b10375/llama-b10375-bin-ubuntu-rocm-7.14-x64",
        "2.35",
    )];
    art.container = Some(ArtifactContainer {
        image: format!(
            "ghcr.io/ggml-org/llama.cpp:server-rocm-b12464@sha256:{}",
            "cd".repeat(32)
        ),
        entrypoint: Some("/app/llama-server".into()),
        version: Some("b12464".into()),
    });
    art
}

fn sample_engine_spec() -> EngineSpec {
    EngineSpec {
        engine_version_id: uuid(),
        name: EngineName::LlamaCpp,
        version: "b8798".into(),
        runtime: EngineRuntime::Native,
        image: None,
        artifact: Some(sample_artifact()),
        launch_template: LaunchTemplate {
            command: vec![
                "llama-server".into(),
                "--port".into(),
                "{{port}}".into(),
                "-m".into(),
                "{{model_path}}".into(),
            ],
            env: BTreeMap::new(),
            working_dir: None,
            router_mode: false,
        },
        health: HealthConfig::default(),
        metrics: MetricsConfig {
            slots_path: Some("/slots".into()),
            metric_prefix: Some("llamacpp:".into()),
            ..Default::default()
        },
        port_range: [10240, 10340],
        capabilities: JsonObject::new(),
    }
}

fn sample_model_spec() -> ModelSpec {
    ModelSpec {
        model_id: uuid(),
        model_version_id: uuid(),
        name: "qwen3-0.6b".into(),
        served_model_name: "qwen3-0.6b".into(),
        model_type: ModelType::Chat,
        format: ModelFileFormat::Gguf,
        files: vec![ModelFileRef {
            model_file_id: uuid(),
            path: "qwen3-0.6b.gguf".into(),
            format: Some(ModelFileFormat::Gguf),
            size_bytes: 522_640_096,
            sha256: Some("7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa".into()),
            is_required: true,
        }],
        cache_dir: "/var/lib/gpudeck/cache/x".into(),
        source: Some(ModelSource {
            kind: ModelSourceKind::Huggingface,
            repo_id: "Qwen/Qwen3-0.6B-GGUF".into(),
            revision: None,
            endpoint: None,
            token: Some("hf_secret".into()),
        }),
        config: JsonObject::new(),
        trust_remote_code: false,
        allow_pickle: false,
    }
}

#[test]
fn hello_is_valid() {
    let hello = Hello {
        agent_version: "1.0.0".into(),
        protocol_version: 1,
        os: NodeOs::Macos,
        os_version: Some("27.2".into()),
        arch: NodeArch::Aarch64,
        kernel_version: Some("Darwin 27.2.0".into()),
        hostname: "mac-1".into(),
        ip_addresses: vec!["10.0.0.5".into()],
        cpu_cores: Some(10),
        memory_total_bytes: Some(34_359_738_368),
        boot_id: Some("abc".into()),
        started_at: now(),
        capabilities: Capabilities {
            engines: vec![EngineCapability {
                name: EngineName::LlamaCpp,
                versions: vec!["b8798".into()],
                runtime: Some(EngineRuntime::Native),
                path: Some("/x/llama-server".into()),
            }],
            runtimes: vec![EngineRuntime::Native],
            container_runtime: Some("docker".into()),
            glibc_version: Some("2.35".into()),
            cuda_version: None,
            rocm_version: None,
            musa_version: None,
            metal: true,
            egress_lock: EgressLockKind::Pf,
            kubernetes: None,
            cloud: None,
            commands: CommandName::ALL.to_vec(),
            ssh_instances: true,
        },
        cache: CacheSummary {
            dir: "/x/cache".into(),
            limit_bytes: 10,
            used_bytes: 0,
            free_bytes: Some(5),
        },
        proxy: ProxyInfo {
            port: 9443,
            address: None,
            cert_fingerprint_sha256: Some("a".repeat(64)),
        },
        config_version: 0,
        labels: BTreeMap::from([("zone".to_string(), "office".to_string())]),
        observed_engines: vec![observed_engine()],
    };
    assert_valid("hello.json", &serde_json::to_value(&hello).unwrap());
    assert_valid("envelope.json", &envelope(Message::Hello(hello)));
}

#[test]
fn hello_ack_parses_and_validates() {
    let raw = json!({
        "accepted": true, "reason": null, "server_time": "2026-11-16T10:20:30.123Z",
        "node_id": uuid(), "node_name": "mac-1", "cluster_id": null,
        "intervals": {"heartbeat_seconds": 10, "metrics_seconds": 2, "inventory_seconds": 60},
        "config_version": 3, "min_agent_version": null, "drain": false
    });
    assert_valid("hello_ack.json", &raw);
    let ack: HelloAck = serde_json::from_value(raw).unwrap();
    assert_eq!(ack.intervals.command_ack_timeout_seconds, 10);
    let env = Envelope::new(Some(uuid()), Message::HelloAck(ack)).with_correlation(uuid());
    assert_valid("envelope.json", &serde_json::to_value(&env).unwrap());
}

#[test]
fn heartbeat_is_valid() {
    let hb = Heartbeat {
        uptime_seconds: 12,
        config_version: 3,
        instances: vec![HeartbeatInstance {
            instance_id: uuid(),
            state: InstanceState::Ready,
            health: InstanceHealth::Healthy,
            active_sessions: Some(1),
            waiting_sessions: None,
        }],
        running_commands: vec![uuid()],
        load: Some(NodeLoad {
            cpu_pct: Some(3.5),
            memory_used_bytes: Some(1),
            cache_used_bytes: Some(0),
            disk_free_bytes: None,
        }),
    };
    assert_valid("heartbeat.json", &serde_json::to_value(&hb).unwrap());
    assert_valid("envelope.json", &envelope(Message::Heartbeat(hb)));
}

#[test]
fn inventory_is_valid() {
    let inv = Inventory {
        devices: vec![sample_device()],
        disks: vec![Disk {
            mount: "/".into(),
            device: Some("disk3s1".into()),
            fs: Some("apfs".into()),
            total_bytes: 10,
            free_bytes: 5,
            is_cache_dir: true,
        }],
        cache: CacheInventory {
            dir: "/x".into(),
            limit_bytes: 10,
            used_bytes: 1,
            items: vec![CacheItem {
                model_file_id: uuid(),
                model_version_id: uuid(),
                local_path: "/x/a.gguf".into(),
                size_bytes: 1,
                state: CacheState::Ready,
                progress_pct: 100.0,
                sha256_verified: true,
                pinned: false,
                last_used_at: Some(now()),
                error_message: None,
            }],
        },
        instances: vec![InstanceStatus {
            instance_id: uuid(),
            deployment_id: Some(uuid()),
            engine_process_id: None,
            state: InstanceState::Ready,
            health: InstanceHealth::Healthy,
            port: Some(10240),
            served_model_name: Some("m".into()),
            pid: Some(1),
            container_id: None,
            device_ids: vec![uuid()],
            active_sessions: Some(0),
            waiting_sessions: Some(0),
            message: None,
            distributed_rank: None,
        }],
        engine_processes: vec![EngineProcessInfo {
            engine_process_id: uuid(),
            engine: EngineName::LlamaCpp,
            engine_version_id: Some(uuid()),
            port: 10240,
            pid: Some(1),
            device_ids: vec![],
            instance_ids: vec![],
        }],
        ssh_instances: vec![],
        observed_engines: vec![observed_engine()],
        system: Some(SystemInfo {
            cpu_cores: Some(10),
            memory_total_bytes: Some(1),
            memory_used_bytes: Some(1),
            load_avg_1m: Some(0.5),
            uptime_seconds: Some(1),
        }),
        config_version: Some(3),
    };
    assert_valid("inventory.json", &serde_json::to_value(&inv).unwrap());
    assert_valid("envelope.json", &envelope(Message::Inventory(inv)));
}

fn observed_engine() -> ObservedEngine {
    ObservedEngine {
        observe_id: uuid(),
        deployment_id: uuid(),
        name: "main".into(),
        engine: ObservedEngineKind::Vllm,
        endpoint: Some("127.0.0.1:8000".into()),
        sources: vec![
            "metrics".into(),
            "health".into(),
            "models".into(),
            "log".into(),
        ],
        models: vec!["Qwen/Qwen3-8B".into()],
        version: Some("0.34.0".into()),
        health: InstanceHealth::Healthy,
        message: None,
        last_ok_at: Some(now()),
        pid: Some(4242),
        device_ids: vec![uuid()],
    }
}

/// Additive 2026-11-23 fields: observed engines, GPU processes, hub features. Absent = the old
/// wire shape (an older hub never sees them).
#[test]
fn observed_engines_and_gpu_processes_validate() {
    let mut gs = GpuSample {
        device_id: uuid(),
        process_count: Some(2),
        ..Default::default()
    };
    let plain = serde_json::to_value(&gs).unwrap();
    assert!(plain.get("processes").is_none(), "omitted unless known");
    gs.processes = Some(vec![
        GpuProcess {
            pid: 31337,
            used_memory_bytes: Some(41_204_842_496),
            name: Some("python3".into()),
            instance_id: Some(uuid()),
        },
        GpuProcess {
            pid: 7,
            used_memory_bytes: None,
            name: None,
            instance_id: None,
        },
    ]);
    let es = EngineSample {
        instance_id: uuid(),
        deployment_id: uuid(),
        observed: true,
        health: Some(InstanceHealth::Healthy),
        ..Default::default()
    };
    let managed = serde_json::to_value(EngineSample {
        observed: false,
        ..es.clone()
    })
    .unwrap();
    assert!(
        managed.get("observed").is_none(),
        "omitted for managed engines"
    );
    let batch = MetricsBatch {
        interval_seconds: 2.0,
        sequence: None,
        gpu: vec![gs],
        engine: vec![es],
        sessions: vec![],
        agent: None,
    };
    assert_valid("metrics_batch.json", &serde_json::to_value(&batch).unwrap());
    assert_valid("envelope.json", &envelope(Message::MetricsBatch(batch)));
    let inv: Inventory = serde_json::from_value(json!({
        "devices": [], "disks": [], "cache": {"dir": "/x", "limit_bytes": 0, "used_bytes": 0, "items": []},
        "instances": [], "observed_engines": [serde_json::to_value(observed_engine()).unwrap()]
    }))
    .unwrap();
    assert_eq!(inv.observed_engines[0].engine, ObservedEngineKind::Vllm);
    assert_valid("inventory.json", &serde_json::to_value(&inv).unwrap());
    let empty = Inventory {
        observed_engines: vec![],
        ..inv
    };
    assert!(serde_json::to_value(&empty)
        .unwrap()
        .get("observed_engines")
        .is_none());
    // Bad names and kinds are schema errors.
    let mut bad = serde_json::to_value(observed_engine()).unwrap();
    bad["name"] = json!("has space");
    assert!(!def_validator("common.json", "ObservedEngine").is_valid(&bad));
    bad["name"] = json!("ok");
    bad["engine"] = json!("ollama");
    assert!(!def_validator("common.json", "ObservedEngine").is_valid(&bad));
    // hello_ack.features: optional; older agents ignore it.
    let raw = json!({
        "accepted": true, "server_time": "2026-11-23T10:20:30.123Z", "node_id": uuid(),
        "intervals": {"heartbeat_seconds": 10, "metrics_seconds": 2, "inventory_seconds": 60},
        "config_version": 0, "features": [hub_feature::OBSERVED_ENGINES, hub_feature::GPU_PROCESSES]
    });
    assert_valid("hello_ack.json", &raw);
    let ack: HelloAck = serde_json::from_value(raw).unwrap();
    assert_eq!(ack.features.len(), 2);
}

#[test]
fn metrics_batch_is_valid() {
    let iid = uuid();
    let batch = MetricsBatch {
        interval_seconds: 2.0,
        sequence: Some(7),
        gpu: vec![GpuSample {
            device_id: uuid(),
            utilization_pct: Some(12.5),
            memory_used_bytes: Some(1),
            ..Default::default()
        }],
        engine: vec![EngineSample {
            instance_id: iid,
            deployment_id: uuid(),
            active_sessions: Some(1),
            health: Some(InstanceHealth::Healthy),
            ..Default::default()
        }],
        sessions: vec![SessionSample {
            slot_id: Some(0),
            prompt_tokens: Some(10),
            ..SessionSample::new(iid, "slot-0", SessionState::Generating)
        }],
        agent: Some(AgentMetrics {
            cpu_pct: Some(1.0),
            rss_bytes: Some(1),
            proxy_active_connections: Some(0),
            proxy_requests_total: Some(0),
            proxy_errors_total: Some(0),
            scrape_errors_total: Some(0),
        }),
    };
    assert_valid("metrics_batch.json", &serde_json::to_value(&batch).unwrap());
    assert_valid("envelope.json", &envelope(Message::MetricsBatch(batch)));
}

#[test]
fn events_are_valid() {
    let mut e1 = Event::info(EventKind::InstanceStateChanged, "ready").instance(uuid());
    e1.state = Some(StateChange {
        from: Some("loading".into()),
        to: "ready".into(),
        reason: None,
    });
    let mut e2 = Event::critical(EventKind::EngineCrashed, "crash").instance(uuid());
    e2.exit = Some(ExitInfo {
        code: Some(137),
        signal: Some("SIGKILL".into()),
        oom: true,
        last_lines: vec!["x".into()],
    });
    let mut e3 = Event::info(EventKind::Log, "log");
    e3.log = Some(LogBatch {
        source: LogSource::Engine,
        stream: Some(LogStream::Stderr),
        lines: vec![LogLine {
            ts: now(),
            level: Some(LogLevel::Info),
            stream: Some(LogStream::Stderr),
            text: "hello".into(),
        }],
    });
    let mut e4 = Event::info(EventKind::DrainProgress, "drain").instance(uuid());
    e4.drain = Some(DrainInfo {
        active_sessions: 1,
        waiting_sessions: 0,
        elapsed_seconds: 5.0,
        timed_out: false,
    });
    let e5 = Event::info(EventKind::ConfigApplied, "applied").details(serde_json::Map::from_iter(
        [("config_version".to_string(), json!(3))],
    ));
    for e in [e1, e2, e3, e4, e5] {
        assert_valid("event.json", &serde_json::to_value(&e).unwrap());
        assert_valid("envelope.json", &envelope(Message::Event(e)));
    }
}

#[test]
fn commands_parse_and_validate() {
    let load = Command::new(CommandKind::LoadModel(LoadModelArgs {
        instance_id: uuid(),
        deployment_id: uuid(),
        engine: sample_engine_spec(),
        model: sample_model_spec(),
        devices: vec![DeviceAssignment {
            device_id: uuid(),
            index: 0,
            vendor: DeviceVendor::Apple,
            slice: None,
        }],
        port: 0,
        engine_process_id: None,
        launch: LaunchSpec {
            context_size: Some(4096),
            ..Default::default()
        },
        draft_model: None,
        health_timeout_seconds: 600,
    }));
    let cmds = vec![
        load.clone(),
        Command::new(CommandKind::UnloadModel(UnloadModelArgs {
            instance_id: uuid(),
            force: false,
            drain_timeout_seconds: 120,
            kill_grace_seconds: 15,
            stop_engine_if_empty: false,
        })),
        Command::new(CommandKind::DrainInstance(DrainInstanceArgs {
            instance_id: uuid(),
            timeout_seconds: 10,
            wait_for_zero: true,
            resume: false,
        })),
        Command::new(CommandKind::StartEngine(StartEngineArgs {
            engine_process_id: uuid(),
            engine: sample_engine_spec(),
            devices: vec![DeviceAssignment {
                device_id: uuid(),
                index: 0,
                vendor: DeviceVendor::Apple,
                slice: None,
            }],
            port: 0,
            launch: LaunchSpec::default(),
        })),
        Command::new(CommandKind::StopEngine(StopEngineArgs {
            engine_process_id: uuid(),
            force: true,
            drain_timeout_seconds: 0,
            kill_grace_seconds: 5,
        })),
        Command::new(CommandKind::RestartInstance(RestartInstanceArgs {
            instance_id: uuid(),
            drain_timeout_seconds: 5,
            force: false,
        })),
        Command::new(CommandKind::PrefetchModel(PrefetchModelArgs {
            model_version_id: uuid(),
            files: sample_model_spec().files,
            cache_dir: "/x".into(),
            source: None,
            pin: true,
            pin_reason: Some("r".into()),
            allow_pickle: false,
            max_bandwidth_bytes_per_s: None,
        })),
        Command::new(CommandKind::EvictCache(EvictCacheArgs {
            target_free_bytes: Some(10),
            ..Default::default()
        })),
        Command::new(CommandKind::RunBenchmark(RunBenchmarkArgs {
            benchmark_run_id: uuid(),
            instance_id: uuid(),
            port: 10240,
            served_model_name: "m".into(),
            model_type: ModelType::Chat,
            endpoint: "/v1/chat/completions".into(),
            concurrency_levels: vec![1, 2],
            workload: BenchmarkWorkload::default(),
            duration_seconds: 5,
            target_slo: TargetSlo::default(),
            auto_tune: None,
        })),
        Command::new(CommandKind::UpdateConfig(UpdateConfigArgs {
            settings: Some(Settings {
                log_level: Some(LogLevel::Debug),
                ..Default::default()
            }),
        })),
        Command::new(CommandKind::RotateCert(RotateCertArgs::default())),
        Command::new(CommandKind::InstallCert(InstallCertArgs {
            certificate_pem: "x".into(),
            ca_bundle_pem: "y".into(),
            serial: "1".into(),
            not_after: now(),
        })),
        Command::new(CommandKind::Cancel(CancelArgs {
            target_command_id: uuid(),
        })),
    ];
    for c in cmds {
        let v = serde_json::to_value(&c).unwrap();
        assert_valid("command.json", &v);
        let back: Command = serde_json::from_value(v).unwrap();
        assert_eq!(back, c);
        assert_valid("envelope.json", &envelope(Message::Command(c)));
    }
    // Redaction strips tokens.
    let red = serde_json::to_value(load.kind.redacted()).unwrap();
    assert!(red["args"]["model"]["source"]["token"].is_null());
    assert!(!serde_json::to_string(&load.kind.redacted())
        .unwrap()
        .contains("hf_secret"));
}

#[test]
fn command_results_are_valid() {
    let id = uuid();
    let results = vec![
        CommandResult::accepted(CommandName::LoadModel).with_instance(id),
        CommandResult::running(CommandName::LoadModel, Some(42.0), "downloading"),
        CommandResult::succeeded(
            CommandName::LoadModel,
            json!({"instance_id": id, "port": 10240}),
        ),
        CommandResult::failed(
            CommandName::LoadModel,
            ErrorInfo::new(error_code::HEALTH_TIMEOUT, "no health").retryable(),
        ),
        CommandResult::rejected(
            CommandName::LoadModel,
            ErrorInfo::new(error_code::INVALID_ARGS, "bad"),
        ),
        CommandResult::cancelled(CommandName::PrefetchModel),
    ];
    for r in results {
        assert_valid("command_result.json", &serde_json::to_value(&r).unwrap());
        let env = Envelope::new(Some(uuid()), Message::CommandResult(r)).with_correlation(id);
        assert_valid("envelope.json", &serde_json::to_value(&env).unwrap());
    }
}

#[test]
fn config_push_parses_and_validates() {
    let raw = json!({
        "config_version": 4, "full": true,
        "routing": [{"instance_id": uuid(), "deployment_id": uuid(), "model_name": "qwen", "model_type": "chat", "engine": "llama_cpp", "port": 10240, "served_model_name": "qwen3-0.6b", "rewrite_model": true, "allowed_path_prefixes": ["/v1/chat/completions", "/health"], "accepting": true, "max_concurrent": 4}],
        "egress_allowlist": ["huggingface.co:443", "10.0.0.0/8"],
        "engine_catalog": [serde_json::to_value(sample_engine_spec()).unwrap()],
        "crl": {"generated_at": Utc::now().to_rfc3339(), "serials": ["01"], "fingerprints_sha256": []},
        "ca_bundle_pem": null,
        "settings": {"heartbeat_interval_seconds": 10, "proxy": {"allowed_client_subjects": ["O=gpudeck-services"], "request_timeout_seconds": 600}},
        "model_sources": {"huggingface_endpoint": "https://huggingface.co", "modelscope_endpoint": null}
    });
    assert_valid("config_push.json", &raw);
    let cp: ConfigPush = serde_json::from_value(raw).unwrap();
    assert_eq!(cp.routing.as_ref().unwrap()[0].port, 10240);
    let v = serde_json::to_value(&cp).unwrap();
    assert_valid("config_push.json", &v);
    assert_valid("envelope.json", &envelope(Message::ConfigPush(cp)));
}

#[test]
fn settings_merge_keeps_absent_keys() {
    let mut base = Settings {
        heartbeat_interval_seconds: Some(10),
        log_level: Some(LogLevel::Info),
        ..Default::default()
    };
    base.merge(&Settings {
        log_level: Some(LogLevel::Debug),
        proxy: Some(ProxySettings {
            max_body_bytes: Some(1024),
            ..Default::default()
        }),
        ..Default::default()
    });
    assert_eq!(base.heartbeat_interval_seconds, Some(10));
    assert_eq!(base.log_level, Some(LogLevel::Debug));
    assert_eq!(base.proxy.unwrap().max_body_bytes, Some(1024));
}

#[test]
fn unknown_top_level_envelope_field_is_rejected_by_schema() {
    let mut v = envelope(Message::Heartbeat(Heartbeat {
        uptime_seconds: 1,
        config_version: 0,
        instances: vec![],
        running_commands: vec![],
        load: None,
    }));
    v["extra_field"] = json!(1);
    let val = validator("envelope.json");
    assert!(!val.is_valid(&v));
    let _ = Arc::new(());
}

// ---------------------------------------------------------------------------
// Protocol 1.1 (2026-11-17): scan_path, hash_files, SSH instances, distributed launch.
// ---------------------------------------------------------------------------

fn def_validator(file: &str, def: &str) -> Validator {
    let text = std::fs::read_to_string(schema_dir().join(file)).expect("schema file");
    let schema: Value = serde_json::from_str(&text).unwrap();
    let id = schema["$id"].as_str().unwrap().to_string();
    let wrapper = json!({"$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": format!("{id}#/$defs/{def}")});
    jsonschema::options()
        .with_retriever(LocalRetriever)
        .build(&wrapper)
        .expect("compile def schema")
}

fn assert_def_valid(file: &str, def: &str, value: &Value) {
    let v = def_validator(file, def);
    let errors: Vec<String> = v
        .iter_errors(value)
        .map(|e| format!("{} at {}", e, e.instance_path()))
        .collect();
    assert!(
        errors.is_empty(),
        "{file}#{def} violations for {}:\n{}",
        serde_json::to_string_pretty(value).unwrap(),
        errors.join("\n")
    );
}

fn distributed(rank: u32) -> DistributedSpec {
    DistributedSpec {
        group_id: Some(uuid()),
        rank,
        world_size: 2,
        master_addr: "10.0.0.5".into(),
        master_port: 29500,
        bind_address: Some("10.0.0.6".into()),
        rpc_port: Some(50052),
        nccl_socket_ifname: Some("eth0".into()),
        peers: vec![
            DistributedPeer {
                rank: 0,
                node_id: uuid(),
                address: "10.0.0.5".into(),
                rpc_port: None,
                device_ids: vec![uuid()],
            },
            DistributedPeer {
                rank: 1,
                node_id: uuid(),
                address: "10.0.0.6".into(),
                rpc_port: Some(50052),
                device_ids: vec![],
            },
        ],
    }
}

#[test]
fn v1_1_commands_parse_and_validate() {
    let mut ssh = StartSshInstanceArgs::new(
        uuid(),
        "lscr.io/linuxserver/openssh-server:latest",
        vec!["ssh-ed25519 AAAA x@y".into()],
    );
    ssh.devices = vec![DeviceAssignment {
        device_id: uuid(),
        index: 0,
        vendor: DeviceVendor::Nvidia,
        slice: Some(Slice {
            vram_bytes: 1 << 30,
            compute_pct: 50,
        }),
    }];
    ssh.limits = SshInstanceLimits {
        cpu_cores: Some(2.5),
        memory_bytes: Some(1 << 30),
        shm_bytes: Some(1 << 28),
        pids: Some(512),
        ..Default::default()
    };
    ssh.volumes = vec![
        SshVolume {
            kind: SshVolumeType::Volume,
            source: Some("home".into()),
            target: "/home/gpudeck".into(),
            read_only: false,
            keep: true,
            size_bytes: None,
        },
        SshVolume {
            kind: SshVolumeType::Tmpfs,
            source: None,
            target: "/scratch".into(),
            read_only: false,
            keep: false,
            size_bytes: Some(1 << 30),
        },
    ];
    ssh.ttl_seconds = Some(3600);
    ssh.container_port = 2222;
    let mut load = match commands_load() {
        CommandKind::LoadModel(a) => a,
        _ => unreachable!(),
    };
    load.launch.distributed = Some(distributed(1));
    load.launch.pp_size = 2;
    let cmds = vec![
        Command::new(CommandKind::ScanPath(ScanPathArgs::new("~/.ollama/models"))),
        Command::new(CommandKind::HashFiles(HashFilesArgs::new(
            Some("/m".into()),
            vec!["a.gguf".into(), "sub/b.safetensors".into()],
        ))),
        Command::new(CommandKind::StartSshInstance(ssh)),
        Command::new(CommandKind::StopSshInstance(StopSshInstanceArgs::new(
            uuid(),
        ))),
        Command::new(CommandKind::LoadModel(load)),
        Command::new(CommandKind::StartEngine(StartEngineArgs {
            engine_process_id: uuid(),
            engine: sample_engine_spec(),
            devices: vec![DeviceAssignment {
                device_id: uuid(),
                index: 0,
                vendor: DeviceVendor::Nvidia,
                slice: None,
            }],
            port: 0,
            launch: LaunchSpec {
                distributed: Some(distributed(0)),
                ..Default::default()
            },
        })),
    ];
    for c in cmds {
        let v = serde_json::to_value(&c).unwrap();
        assert_valid("command.json", &v);
        let back: Command = serde_json::from_value(v).unwrap();
        assert_eq!(back, c);
        assert_valid("envelope.json", &envelope(Message::Command(c)));
    }
    // Schema rejects a world of one and unknown args.
    let mut bad =
        serde_json::to_value(Command::new(CommandKind::ScanPath(ScanPathArgs::new("/x")))).unwrap();
    bad["args"]["bogus"] = json!(1);
    assert!(!validator("command.json").is_valid(&bad));
    let mut d = serde_json::to_value(LaunchSpec {
        distributed: Some(distributed(0)),
        ..Default::default()
    })
    .unwrap();
    d["distributed"]["world_size"] = json!(1);
    assert!(!def_validator("common.json", "LaunchSpec").is_valid(&d));
    // Every command name is in the schema enum (hello.capabilities.commands).
    let names: Vec<Value> = CommandName::ALL
        .iter()
        .map(|n| serde_json::to_value(n).unwrap())
        .collect();
    let schema: Value =
        serde_json::from_str(&std::fs::read_to_string(schema_dir().join("command.json")).unwrap())
            .unwrap();
    assert_eq!(
        schema["properties"]["command"]["enum"].as_array().unwrap(),
        &names
    );
}

fn commands_load() -> CommandKind {
    CommandKind::LoadModel(LoadModelArgs {
        instance_id: uuid(),
        deployment_id: uuid(),
        engine: sample_engine_spec(),
        model: sample_model_spec(),
        devices: vec![DeviceAssignment {
            device_id: uuid(),
            index: 0,
            vendor: DeviceVendor::Nvidia,
            slice: None,
        }],
        port: 0,
        engine_process_id: None,
        launch: LaunchSpec::default(),
        draft_model: None,
        health_timeout_seconds: 600,
    })
}

#[test]
fn v1_1_results_and_state_validate() {
    let scan = ScanPathResult {
        path: "/x".into(),
        truncated: false,
        visited: 3,
        seconds: 0.1,
        candidates: vec![ScanCandidate {
            path: "/x/m.gguf".into(),
            kind: ScanKind::Plain,
            format: ModelFileFormat::Gguf,
            files: vec![ScanFile {
                path: "m.gguf".into(),
                absolute_path: "/x/m.gguf".into(),
                size_bytes: 10,
                format: ModelFileFormat::Gguf,
                sha256: None,
                is_required: true,
            }],
            total_size_bytes: 10,
            suggested: JsonObject::new(),
            metadata: JsonObject::new(),
        }],
    };
    assert_def_valid(
        "command.json",
        "ScanPathResult",
        &serde_json::to_value(&scan).unwrap(),
    );
    let hash = HashFilesResult {
        root: None,
        files: vec![
            HashedFile {
                path: "/x/m.gguf".into(),
                sha256: Some("a".repeat(64)),
                size_bytes: Some(10),
                error: None,
            },
            HashedFile {
                path: "/x/nope".into(),
                sha256: None,
                size_bytes: None,
                error: Some(ErrorInfo::new(error_code::PATH_NOT_FOUND, "missing")),
            },
        ],
        hashes: BTreeMap::from([("/x/m.gguf".to_string(), "a".repeat(64))]),
        bytes_hashed: 10,
        seconds: 0.01,
    };
    assert_def_valid(
        "command.json",
        "HashFilesResult",
        &serde_json::to_value(&hash).unwrap(),
    );
    let fp = "SHA256:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU".to_string();
    let res = SshInstanceResult {
        ssh_instance_id: uuid(),
        container_id: Some("abc".into()),
        host_address: "10.0.0.5".into(),
        host_port: 2200,
        ssh_user: "gpudeck".into(),
        host_key_fingerprint: fp.clone(),
        host_keys: vec![SshHostKey {
            key_type: "ssh-ed25519".into(),
            fingerprint: fp.clone(),
            public_key: Some("ssh-ed25519 AAAA".into()),
        }],
        ssh_command: Some("ssh -p 2200 gpudeck@10.0.0.5".into()),
        device_ids: vec![uuid()],
        gpu_attached: false,
        started_at: Some(now()),
        expires_at: None,
        warnings: vec!["no GPU passthrough".into()],
    };
    let v = serde_json::to_value(&res).unwrap();
    assert_def_valid("command.json", "SshInstanceResult", &v);
    assert_valid(
        "command_result.json",
        &serde_json::to_value(CommandResult::succeeded(CommandName::StartSshInstance, v)).unwrap(),
    );

    let mut dev = sample_device();
    dev.device_id = Some(uuid());
    let st = SshInstanceStatus {
        ssh_instance_id: uuid(),
        state: SshInstanceState::Running,
        health: InstanceHealth::Healthy,
        container_id: Some("abc".into()),
        image: Some("img".into()),
        host_address: Some("10.0.0.5".into()),
        host_port: Some(2200),
        ssh_user: Some("gpudeck".into()),
        host_key_fingerprint: Some(fp),
        device_ids: vec![],
        gpu_attached: false,
        active_connections: 1,
        connections_total: 2,
        last_connection_at: Some(now()),
        started_at: Some(now()),
        expires_at: None,
        message: None,
    };
    let inv = Inventory {
        devices: vec![dev],
        disks: vec![],
        cache: CacheInventory {
            dir: "/x".into(),
            limit_bytes: 0,
            used_bytes: 0,
            items: vec![],
        },
        instances: vec![InstanceStatus {
            instance_id: uuid(),
            deployment_id: None,
            engine_process_id: None,
            state: InstanceState::Ready,
            health: InstanceHealth::Healthy,
            port: None,
            served_model_name: None,
            pid: None,
            container_id: None,
            device_ids: vec![],
            active_sessions: None,
            waiting_sessions: None,
            message: None,
            distributed_rank: Some(1),
        }],
        engine_processes: vec![],
        ssh_instances: vec![st.clone()],
        observed_engines: vec![],
        system: None,
        config_version: None,
    };
    assert_valid("inventory.json", &serde_json::to_value(&inv).unwrap());
    let mut ev = Event::info(EventKind::SshInstanceStateChanged, "running");
    ev.ssh_instance_id = Some(st.ssh_instance_id);
    ev.state = Some(StateChange {
        from: Some("starting".into()),
        to: "running".into(),
        reason: None,
    });
    ev.details = serde_json::to_value(&st).unwrap().as_object().cloned();
    assert_valid("event.json", &serde_json::to_value(&ev).unwrap());
    assert_valid("envelope.json", &envelope(Message::Event(ev)));
}

/// Every sample document in `agent-protocol/examples/` validates against its schema and parses
/// into the Rust types (`<schema>.<what>.json`, `command.<Def>.json` → `$defs/<Def>`).
#[test]
fn protocol_examples_validate_and_parse() {
    let dir = schema_dir().join("examples");
    let mut n = 0;
    for entry in std::fs::read_dir(&dir).expect("examples dir") {
        let path = entry.unwrap().path();
        if path.extension().is_none_or(|e| e != "json") {
            continue;
        }
        let name = path.file_name().unwrap().to_string_lossy().to_string();
        let doc: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        let parts: Vec<&str> = name.split('.').collect();
        let file = format!("{}.json", parts[0]);
        if parts[1]
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_uppercase())
        {
            assert_def_valid(&file, parts[1], &doc);
            match parts[1] {
                "ScanPathResult" => drop(serde_json::from_value::<ScanPathResult>(doc).unwrap()),
                "HashFilesResult" => drop(serde_json::from_value::<HashFilesResult>(doc).unwrap()),
                "SshInstanceResult" => {
                    drop(serde_json::from_value::<SshInstanceResult>(doc).unwrap())
                }
                other => panic!("no Rust type mapped for {other}"),
            }
        } else {
            assert_valid(&file, &doc);
            match parts[0] {
                "envelope" => {
                    let env: Envelope = serde_json::from_value(doc.clone())
                        .unwrap_or_else(|e| panic!("{name}: {e}"));
                    assert_valid("envelope.json", &serde_json::to_value(&env).unwrap());
                }
                "inventory" => drop(serde_json::from_value::<Inventory>(doc).unwrap()),
                other => panic!("no Rust type mapped for {other}"),
            }
        }
        n += 1;
    }
    assert!(n >= 12, "expected the 1.1 sample documents, found {n}");
}
