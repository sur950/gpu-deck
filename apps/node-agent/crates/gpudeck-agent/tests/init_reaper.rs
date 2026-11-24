//! The agent's PID 1 mode (`gpudeck_agent::init`): exit status and signal forwarding, and on
//! Linux the reaping of orphans re-parented to it (what left `<defunct>` llama-server children
//! when the agent ran as PID 1 in a container). One test function: `run_as_init` waits for any
//! child of the test process, so nothing else may spawn children here concurrently.
#![cfg(unix)]

use std::ffi::OsString;
use std::path::Path;
use std::time::{Duration, Instant};

use gpudeck_agent::init::{become_subreaper, run_as_init};

fn sh(script: &str) -> Vec<OsString> {
    vec!["-c".into(), script.into()]
}

#[test]
fn init_mode_forwards_status_and_reaps_orphans() {
    // Exit status of the supervised process is passed through.
    let out = run_as_init(Path::new("/bin/sh"), &sh("exit 7")).unwrap();
    assert_eq!(out.code, 7);

    // A signal to the init reaches the child; its death by signal maps to 128 + n.
    let me = std::process::id();
    let killer = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(300));
        // SAFETY: plain kill(2) to this test process.
        unsafe { libc::kill(me as i32, libc::SIGTERM) };
    });
    let started = Instant::now();
    let out = run_as_init(Path::new("/bin/sh"), &sh("exec sleep 30")).unwrap();
    killer.join().unwrap();
    assert_eq!(
        out.code,
        128 + libc::SIGTERM,
        "SIGTERM forwarded to the agent"
    );
    assert!(started.elapsed() < Duration::from_secs(10));

    // Linux: an orphan re-parented to the init is reaped (no zombie left behind).
    if cfg!(target_os = "linux") {
        assert!(become_subreaper(), "PR_SET_CHILD_SUBREAPER");
        let dir = tempfile::tempdir().unwrap();
        let pidfile = dir.path().join("orphan.pid");
        let script = format!(
            "sh -c 'sleep 0.2 & echo $! > {}'; sleep 1.5; exit 3",
            pidfile.display()
        );
        let out = run_as_init(Path::new("/bin/sh"), &sh(&script)).unwrap();
        assert_eq!(out.code, 3);
        assert!(out.orphans_reaped >= 1, "{out:?}");
        let pid = std::fs::read_to_string(&pidfile).unwrap();
        let stat = format!("/proc/{}/stat", pid.trim());
        assert!(
            !Path::new(&stat).exists(),
            "orphan {} is still in the process table: {:?}",
            pid.trim(),
            std::fs::read_to_string(&stat)
        );
    }
}
