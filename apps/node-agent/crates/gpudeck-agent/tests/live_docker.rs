//! Live container checks against a real Docker engine (OrbStack / Docker Desktop / Linux):
//! `start_ssh_instance` with an sshd image and an actual `ssh` login with a throwaway key
//! (host key verified against the fingerprint the agent reported), limits applied, then
//! `stop_ssh_instance` cleanup; and the container engine path of `load_model` with a stub HTTP
//! server image. Ignored by default:
//!
//! ```text
//! GPUDECK_LIVE_SSH_IMAGE=lscr.io/linuxserver/openssh-server:latest \
//! GPUDECK_LIVE_HTTP_IMAGE=python:3.12.12-slim-bookworm \
//! cargo test -p gpudeck-agent --test live_docker -- --ignored --nocapture
//! ```

use std::process::Command as Proc;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use gpudeck_agent::backend::{DeviceReading, DeviceSource, GenericCatalog};
use gpudeck_agent::fakehub::FakeHub;
use gpudeck_agent::join::{join, JoinOptions};
use gpudeck_agent::{Agent, AgentConfig, RunOptions};
use gpudeck_protocol::*;
use serde_json::{json, Value};
use uuid::Uuid;

struct NoGpu;

#[async_trait]
impl DeviceSource for NoGpu {
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

fn docker(args: &[&str]) -> String {
    let o = Proc::new("docker").args(args).output().expect("docker");
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}

fn ok(r: &CommandResult) -> JsonObject {
    assert_eq!(
        r.status,
        ResultStatus::Succeeded,
        "command failed: {:?}",
        r.error
    );
    r.result.clone().unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "needs a Docker engine and the sshd / http images"]
async fn ssh_instance_and_container_engine() {
    let ssh_image = std::env::var("GPUDECK_LIVE_SSH_IMAGE")
        .unwrap_or_else(|_| "lscr.io/linuxserver/openssh-server:latest".into());
    let http_image = std::env::var("GPUDECK_LIVE_HTTP_IMAGE")
        .unwrap_or_else(|_| "python:3.12.12-slim-bookworm".into());
    gpudeck_agent::tls::init_crypto();
    let hub = FakeHub::start("127.0.0.1:0".parse().unwrap(), "gdj_livedocker0123456789")
        .await
        .unwrap();
    let dir = tempfile::tempdir().unwrap();
    let cfg_path = dir.path().join("agent.toml");
    let mut cfg = AgentConfig {
        // Tests never program the host firewall.
        firewall: "off".into(),
        state_dir: dir.path().join("state"),
        ssh_bind: Some("127.0.0.1".into()),
        ssh_port_range: [22200, 22299],
        prom_port: 0,
        ..Default::default()
    };
    let joined = join(
        &mut cfg,
        &cfg_path,
        JoinOptions {
            hub_url: hub.url(),
            token: "gdj_livedocker0123456789".into(),
            name: Some("live".into()),
            ca_fingerprint: Some(hub.ca_fingerprint()),
            proxy_port: 9443,
            labels: Default::default(),
        },
    )
    .await
    .unwrap();
    let node = joined.node_id;
    let agent = Agent::new(
        cfg.clone(),
        cfg_path,
        Arc::new(NoGpu),
        Arc::new(GenericCatalog),
        RunOptions {
            proxy_listen: Some("127.0.0.1:0".parse().unwrap()),
            no_prom: true,
            ..Default::default()
        },
    )
    .unwrap();
    assert!(agent.container_runtime.is_some(), "docker not found");
    let a2 = agent.clone();
    let run = tokio::spawn(async move { a2.run().await });

    // ---------------------------------------------------------------- SSH instance
    let keydir = tempfile::tempdir().unwrap();
    let key = keydir.path().join("id");
    assert!(Proc::new("ssh-keygen")
        .args(["-q", "-t", "ed25519", "-N", "", "-f"])
        .arg(&key)
        .status()
        .unwrap()
        .success());
    let pubkey = std::fs::read_to_string(key.with_extension("pub")).unwrap();
    let sid = Uuid::new_v4();
    let start = json!({"command": "start_ssh_instance", "timeout_seconds": 300, "args": {
        "ssh_instance_id": sid, "name": "live test", "image": ssh_image, "authorized_keys": [pubkey.trim()],
        "ssh_user": "tester", "container_port": 2222, "allowed_source_cidrs": ["127.0.0.0/8"],
        "limits": {"cpu_cores": 1, "memory_bytes": 536870912, "pids": 256, "shm_bytes": 67108864},
        "volumes": [{"type": "volume", "source": "home", "target": "/data"}, {"type": "tmpfs", "target": "/scratch", "size_bytes": 16777216}],
        "env": {"TZ": "UTC"}, "ttl_seconds": 3600, "health_timeout_seconds": 240
    }});
    let id = hub.command(node, serde_json::from_value(start).unwrap());
    let r = hub
        .wait_result(id, Duration::from_secs(300))
        .await
        .expect("start result");
    let res = ok(&r);
    println!(
        "start_ssh_instance result: {}",
        serde_json::to_string_pretty(&res).unwrap()
    );
    let port = res["host_port"].as_u64().unwrap() as u16;
    assert!((22200..=22299).contains(&port));
    assert_eq!(res["host_address"], "127.0.0.1");
    let fp = res["host_key_fingerprint"].as_str().unwrap().to_string();
    assert!(fp.starts_with("SHA256:"));
    let name = format!("gpudeck-ssh-{sid}");
    // Published on loopback only; limits applied.
    let ports = docker(&["port", &name]);
    assert!(ports.lines().all(|l| l.contains("127.0.0.1:")), "{ports}");
    assert_eq!(docker(&["inspect", "-f", "{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.PidsLimit}} {{.HostConfig.Privileged}}", &name]), "1000000000 536870912 256 false");

    // Known-hosts from the reported host keys: ssh refuses to connect if the real key differs.
    let kh = keydir.path().join("known_hosts");
    let lines: Vec<String> = res["host_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|k| format!("[127.0.0.1]:{port} {}", k["public_key"].as_str().unwrap()))
        .collect();
    std::fs::write(&kh, lines.join("\n") + "\n").unwrap();
    let out = Proc::new("ssh")
        .arg("-i")
        .arg(&key)
        .args([
            "-p",
            &port.to_string(),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ConnectTimeout=10",
        ])
        .arg("-o")
        .arg(format!("UserKnownHostsFile={}", kh.display()))
        .args([
            "tester@127.0.0.1",
            "echo gpudeck-ssh-ok; whoami; df -h /scratch | tail -1",
        ])
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&out.stdout);
    println!(
        "ssh stdout: {stdout}\nssh stderr: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.status.success() && stdout.contains("gpudeck-ssh-ok") && stdout.contains("tester"));
    // Inventory reports it with connection accounting.
    agent.inventory_dirty.notify_one();
    let inv = hub.wait_for(0, Duration::from_secs(30), |e| matches!(&e.message, Message::Inventory(i) if i.ssh_instances.iter().any(|s| s.ssh_instance_id == sid && s.connections_total >= 1))).await;
    assert!(inv.is_some(), "inventory lists the ssh instance");
    // Password login is impossible / a wrong key is refused.
    let other = keydir.path().join("other");
    Proc::new("ssh-keygen")
        .args(["-q", "-t", "ed25519", "-N", "", "-f"])
        .arg(&other)
        .status()
        .unwrap();
    let denied = Proc::new("ssh")
        .arg("-i")
        .arg(&other)
        .args([
            "-p",
            &port.to_string(),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "IdentitiesOnly=yes",
            "tester@127.0.0.1",
            "true",
        ])
        .output()
        .unwrap();
    assert!(!denied.status.success());

    let id = hub.command(
        node,
        serde_json::from_value(
            json!({"command": "stop_ssh_instance", "args": {"ssh_instance_id": sid}}),
        )
        .unwrap(),
    );
    let stopped = ok(&hub
        .wait_result(id, Duration::from_secs(120))
        .await
        .expect("stop result"));
    assert_eq!(stopped["container_removed"], true);
    assert_eq!(
        docker(&["ps", "-a", "-q", "--filter", &format!("name={name}")]),
        ""
    );
    assert_eq!(
        docker(&["volume", "ls", "-q", "--filter", &format!("name={name}")]),
        ""
    );
    assert!(
        std::net::TcpStream::connect(("127.0.0.1", port)).is_err(),
        "forwarder closed"
    );

    // ---------------------------------------------------------------- container engine (custom)
    let mdir = tempfile::tempdir().unwrap();
    let mfile = mdir.path().join("stub.gguf");
    std::fs::write(&mfile, b"GGUFstub").unwrap();
    let inst = Uuid::new_v4();
    let load = json!({"command": "load_model", "timeout_seconds": 300, "args": {
        "instance_id": inst, "deployment_id": Uuid::new_v4(),
        "engine": {"engine_version_id": Uuid::new_v4(), "name": "custom", "version": "stub", "runtime": "container", "image": http_image,
            "launch_template": {"command": ["python", "-m", "http.server", "{{port}}", "--bind", "127.0.0.1", "--directory", "{{model_dir}}"]},
            "health": {"path": "/", "interval_seconds": 1, "startup_timeout_seconds": 120}, "metrics": {"path": null, "format": "none", "derive_from_proxy": true}, "port_range": [18240, 18340]},
        "model": {"model_id": Uuid::new_v4(), "model_version_id": Uuid::new_v4(), "name": "stub", "served_model_name": "stub", "type": "chat", "format": "gguf",
            "files": [{"model_file_id": Uuid::new_v4(), "path": "stub.gguf", "format": "gguf", "size_bytes": 8}], "cache_dir": "",
            "source": {"kind": "local_path", "repo_id": mdir.path().display().to_string()}},
        "devices": [{"device_id": Uuid::new_v4(), "index": 0, "vendor": "apple"}], "port": 0, "launch": {}, "health_timeout_seconds": 240
    }});
    let id = hub.command(node, serde_json::from_value(load).unwrap());
    let r = hub
        .wait_result(id, Duration::from_secs(300))
        .await
        .expect("load result");
    let res = ok(&r);
    let eport = res["port"].as_u64().unwrap() as u16;
    let body = reqwest::get(format!("http://127.0.0.1:{eport}/"))
        .await
        .unwrap()
        .text()
        .await
        .unwrap();
    assert!(body.contains("stub.gguf"), "{body}");
    let cname = format!("gpudeck-{inst}");
    assert!(docker(&["port", &cname])
        .lines()
        .all(|l| l.contains("127.0.0.1:")));
    let id = hub.command(
        node,
        serde_json::from_value(
            json!({"command": "unload_model", "args": {"instance_id": inst, "force": true}}),
        )
        .unwrap(),
    );
    ok(&hub
        .wait_result(id, Duration::from_secs(120))
        .await
        .expect("unload"));
    tokio::time::sleep(Duration::from_secs(2)).await;
    assert_eq!(
        docker(&["ps", "-a", "-q", "--filter", &format!("name={cname}")]),
        ""
    );

    // Every frame still matches the contract.
    let raw = hub.received_raw();
    for text in raw {
        let v: Value = serde_json::from_str(&text).unwrap();
        let _: Envelope = serde_json::from_value(v).unwrap();
    }
    agent.shutdown.cancel();
    let _ = tokio::time::timeout(Duration::from_secs(20), run).await;
}
