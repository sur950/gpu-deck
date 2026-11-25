//! Agent self-update applier (`protocol.md` §14) on a real file system: fake agent binaries
//! (shell scripts) run under a small supervisor that restarts them like systemd `Restart=always`
//! / launchd `KeepAlive`. Covers the atomic swap that keeps `<binary>.prev`, the health check,
//! the automatic rollback when the new agent hangs or crash-loops, and refusals (tampered staged
//! binary, manifest signed by an unknown key) that leave the installed binary untouched.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::Engine as _;
use gpudeck_agent::update::{
    build_target, run_applier, ApplyOptions, RestartSpec, RunningMarker, StagedInfo, UpdatePaths,
    UpdateRequest,
};
use gpudeck_agent::AgentConfig;
use gpudeck_protocol::{ReleaseArtifact, ReleaseManifest, SignedReleaseManifest};
use ring::signature::KeyPair;
use sha2::Digest;
use uuid::Uuid;

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

/// A fake `gpudeck-agent`: `version` prints its version; `run` records its pid and, when
/// `healthy`, writes the health marker for a pending request like a reconnected agent does.
/// `mode`: good | hang (never healthy) | crash (exits at once).
fn script(state: &Path, version: &str, mode: &str) -> Vec<u8> {
    format!(
        r#"#!/bin/sh
V="{version}"
U="{state}/update"
case "$1" in version) echo "gpudeck-agent $V (protocol v1)"; exit 0 ;; esac
[ "{mode}" = crash ] && exit 3
now() {{ date -u +%Y-%m-%dT%H:%M:%SZ; }}
printf '{{"pid":%s,"version":"%s","started_at":"%s"}}' $$ "$V" "$(now)" > "$U/.r.$$" && mv "$U/.r.$$" "$U/running.json"
if [ "{mode}" = good ] && [ -f "$U/request.json" ]; then
  ID=$(sed -n 's/.*"id": *"\([^"]*\)".*/\1/p' "$U/request.json" | head -1)
  printf '{{"request_id":"%s","version":"%s","pid":%s,"at":"%s","adopted":2}}' "$ID" "$V" $$ "$(now)" > "$U/.h.$$" && mv "$U/.h.$$" "$U/health.json"
fi
trap 'exit 0' TERM
while true; do sleep 1 & wait $!; done
"#,
        state = state.display()
    )
    .into_bytes()
}

fn sha(b: &[u8]) -> String {
    hex::encode(sha2::Sha256::digest(b))
}

fn write_exec(path: &Path, bytes: &[u8]) {
    use std::os::unix::fs::PermissionsExt;
    std::fs::write(path, bytes).unwrap();
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
}

/// Restarts `bin run` whenever it exits (what systemd / launchd do for the agent).
struct Supervisor {
    stop: Arc<AtomicBool>,
    pids: Arc<Mutex<Vec<u32>>>,
    task: tokio::task::JoinHandle<()>,
}

impl Supervisor {
    fn start(bin: PathBuf) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let pids = Arc::new(Mutex::new(vec![]));
        let (s, p) = (stop.clone(), pids.clone());
        let task = tokio::spawn(async move {
            while !s.load(Ordering::SeqCst) {
                let child = tokio::process::Command::new(&bin)
                    .arg("run")
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
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        });
        Self { stop, pids, task }
    }

    async fn stop(self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = tokio::time::timeout(Duration::from_secs(5), self.task).await;
        for pid in self.pids.lock().unwrap().iter() {
            // SAFETY: test-owned child pids.
            unsafe { libc::kill(*pid as libc::pid_t, libc::SIGKILL) };
        }
    }
}

struct Env {
    _tmp: tempfile::TempDir,
    state: PathBuf,
    bin: PathBuf,
    up: UpdatePaths,
    key: Key,
    cfg: AgentConfig,
}

fn env() -> Env {
    let tmp = tempfile::tempdir().unwrap();
    let state = tmp.path().join("state");
    let bin_dir = tmp.path().join("bin");
    std::fs::create_dir_all(&bin_dir).unwrap();
    let paths = gpudeck_agent::state::StatePaths::new(&state);
    paths.ensure().unwrap();
    let up = UpdatePaths::new(&paths);
    up.ensure().unwrap();
    let key = key();
    let cfg = AgentConfig {
        state_dir: state.clone(),
        release_public_keys: vec![key.public.clone()],
        ..Default::default()
    };
    Env {
        bin: bin_dir.join("gpudeck-agent"),
        _tmp: tmp,
        state,
        up,
        key,
        cfg,
    }
}

impl Env {
    /// Stage `bytes` as `version` with a manifest signed by `signer` and request the update.
    fn stage_and_request(&self, version: &str, from: &str, bytes: &[u8], signer: &Key) {
        let manifest = ReleaseManifest {
            schema: 1,
            product: "gpudeck-agent".into(),
            version: version.into(),
            protocol_version: 1,
            created_at: None,
            artifacts: vec![ReleaseArtifact {
                target: build_target(),
                file: None,
                size_bytes: None,
                sha256: None,
                binary_sha256: sha(bytes),
                binary_size_bytes: bytes.len() as u64,
            }],
        };
        let doc = serde_json::to_vec(&manifest).unwrap();
        let b64 = base64::engine::general_purpose::STANDARD;
        let signed = SignedReleaseManifest {
            manifest: b64.encode(&doc),
            signature: b64.encode(signer.pair.sign(&doc).as_ref()),
            algorithm: "ed25519".into(),
        };
        let dir = self.up.staged_dir(version);
        std::fs::create_dir_all(&dir).unwrap();
        write_exec(&dir.join("gpudeck-agent"), bytes);
        std::fs::write(
            dir.join("manifest.json"),
            serde_json::to_vec(&signed).unwrap(),
        )
        .unwrap();
        let staged = StagedInfo {
            version: version.into(),
            target: build_target(),
            sha256: sha(bytes),
            size_bytes: bytes.len() as u64,
            staged_at: gpudeck_protocol::now(),
        };
        std::fs::write(self.up.staged_json(), serde_json::to_vec(&staged).unwrap()).unwrap();
        for f in [self.up.result(), self.up.status(), self.up.health()] {
            let _ = std::fs::remove_file(f);
        }
        let req = UpdateRequest {
            id: Uuid::new_v4(),
            command_id: Some(Uuid::new_v4()),
            run_id: None,
            version: version.into(),
            from_version: from.into(),
            target: build_target(),
            sha256: sha(bytes),
            health_timeout_seconds: 4,
            requested_at: gpudeck_protocol::now(),
            agent_pid: 0,
            mode: "self".into(),
        };
        std::fs::write(self.up.request(), serde_json::to_vec_pretty(&req).unwrap()).unwrap();
    }

    fn opts(&self) -> ApplyOptions {
        ApplyOptions {
            binary: self.bin.canonicalize().unwrap(),
            restart: RestartSpec::Signal,
        }
    }

    fn running(&self) -> Option<RunningMarker> {
        gpudeck_agent::update::read_json_secure(&self.up.running())
            .ok()
            .flatten()
    }

    async fn wait_running(&self, version: &str) {
        for _ in 0..100 {
            if self.running().is_some_and(|r| r.version == version) {
                return;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        panic!("agent {version} never ran");
    }
}

fn phases(r: &gpudeck_agent::update::UpdateResult) -> Vec<String> {
    r.steps.iter().map(|s| s.phase.clone()).collect()
}

#[tokio::test(flavor = "multi_thread")]
async fn swap_health_check_and_rollback() {
    let e = env();
    let v100 = script(&e.state, "1.0.0", "good");
    let v110 = script(&e.state, "1.1.0", "good");
    write_exec(&e.bin, &v100);
    let sup = Supervisor::start(e.bin.clone());
    e.wait_running("1.0.0").await;

    // 1. A healthy new version: swapped, restarted, healthy; the old binary is kept as .prev.
    e.stage_and_request("1.1.0", "1.0.0", &v110, &e.key);
    let r = run_applier(&e.cfg, &e.opts()).await.unwrap();
    assert_eq!(r.outcome, "succeeded", "{r:?}");
    assert_eq!(
        phases(&r),
        [
            "verifying",
            "swapping",
            "restarting",
            "health_check",
            "succeeded"
        ]
    );
    assert_eq!(std::fs::read(&e.bin).unwrap(), v110);
    let prev = e.bin.with_file_name("gpudeck-agent.prev");
    assert_eq!(std::fs::read(&prev).unwrap(), v100);
    assert_eq!(e.running().unwrap().version, "1.1.0");
    // Re-running the applier for a finished request changes nothing.
    let again = run_applier(&e.cfg, &e.opts()).await.unwrap();
    assert_eq!(again.request_id, r.request_id);
    assert_eq!(std::fs::read(&e.bin).unwrap(), v110);

    // 2. The new version never reports healthy: rolled back to the previous binary.
    let v120 = script(&e.state, "1.2.0", "hang");
    e.stage_and_request("1.2.0", "1.1.0", &v120, &e.key);
    let r = run_applier(&e.cfg, &e.opts()).await.unwrap();
    assert_eq!(r.outcome, "rolled_back", "{r:?}");
    assert_eq!(r.error_code.as_deref(), Some("rolled_back"));
    assert!(phases(&r).contains(&"rolling_back".to_string()));
    assert_eq!(
        std::fs::read(&e.bin).unwrap(),
        v110,
        "previous binary restored"
    );
    assert_eq!(
        std::fs::read(e.bin.with_file_name("gpudeck-agent.failed")).unwrap(),
        v120
    );
    e.wait_running("1.1.0").await;

    // 3. The new version crash-loops: rolled back as well.
    let v130 = script(&e.state, "1.3.0", "crash");
    e.stage_and_request("1.3.0", "1.1.0", &v130, &e.key);
    let r = run_applier(&e.cfg, &e.opts()).await.unwrap();
    assert_eq!(r.outcome, "rolled_back", "{r:?}");
    assert_eq!(std::fs::read(&e.bin).unwrap(), v110);
    e.wait_running("1.1.0").await;

    // 4. A staged binary changed after it was verified: refused, nothing swapped or restarted.
    let pid_before = e.running().unwrap().pid;
    e.stage_and_request("1.4.0", "1.1.0", &script(&e.state, "1.4.0", "good"), &e.key);
    let staged = e.up.staged_dir("1.4.0").join("gpudeck-agent");
    write_exec(&staged, &script(&e.state, "6.6.6", "good"));
    let r = run_applier(&e.cfg, &e.opts()).await.unwrap();
    assert_eq!(r.outcome, "failed");
    assert_eq!(r.error_code.as_deref(), Some("artifact_checksum_mismatch"));
    assert_eq!(phases(&r), ["verifying", "failed"]);

    // 5. A manifest signed by a key the node does not pin: refused.
    let rogue = key();
    e.stage_and_request("1.5.0", "1.1.0", &script(&e.state, "1.5.0", "good"), &rogue);
    let r = run_applier(&e.cfg, &e.opts()).await.unwrap();
    assert_eq!(r.outcome, "failed");
    assert_eq!(r.error_code.as_deref(), Some("signature_invalid"));
    assert_eq!(std::fs::read(&e.bin).unwrap(), v110);
    assert_eq!(e.running().unwrap().pid, pid_before, "never restarted");

    // 6. No pinned release key at all: refused.
    let mut cfg = e.cfg.clone();
    cfg.release_public_keys.clear();
    e.stage_and_request("1.6.0", "1.1.0", &script(&e.state, "1.6.0", "good"), &e.key);
    let r = run_applier(&cfg, &e.opts()).await.unwrap();
    assert_eq!(r.error_code.as_deref(), Some("signature_invalid"));
    assert_eq!(std::fs::read(&e.bin).unwrap(), v110);

    sup.stop().await;
}
