//! PID 1 duties for containers.
//!
//! In a container the agent is often PID 1 (`docker run` without `--init`, a DaemonSet whose
//! `command:` bypasses the image's `tini`). Processes orphaned inside the container (llama.cpp
//! router children when the router is restarted or killed, grandchildren of a stopped process
//! group) are re-parented to PID 1, and the kernel keeps them as `<defunct>` zombies until PID 1
//! waits for them. The agent's supervisor only waits for the children it spawned.
//!
//! So when the agent starts as PID 1 it splits in two, like `tini`: the PID 1 process stays a
//! minimal init that forwards signals to the agent, reaps **every** child that exits (the agent
//! and any orphan) and exits with the agent's status; the agent itself runs as its child.
//! `GPUDECK_AGENT_INIT=1` forces this mode when the agent is not PID 1 (on Linux it then
//! registers as a child subreaper so orphans come to it); `GPUDECK_AGENT_INIT=0` turns it off.

use std::ffi::OsString;
use std::path::Path;

/// Set in the agent child so it does not split again.
pub const INIT_CHILD_ENV: &str = "GPUDECK_AGENT_INIT_CHILD";
/// `1` forces the init split, `0` disables it.
pub const INIT_ENV: &str = "GPUDECK_AGENT_INIT";

/// How the supervised process ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InitOutcome {
    /// Exit code to leave with (`128 + signal` when the child was killed by a signal).
    pub code: i32,
    /// Other children (orphans) reaped while it ran.
    pub orphans_reaped: usize,
}

/// Whether this process should become the init (PID 1, or forced, and not already the child).
pub fn wanted() -> bool {
    if !cfg!(unix) || std::env::var_os(INIT_CHILD_ENV).is_some() {
        return false;
    }
    match std::env::var(INIT_ENV).ok().as_deref().map(str::trim) {
        Some("0") | Some("false") => false,
        Some("1") | Some("true") => true,
        _ => std::process::id() == 1,
    }
}

/// Make this process the reaper of its orphaned descendants (Linux `PR_SET_CHILD_SUBREAPER`).
/// No-op elsewhere; PID 1 does not need it.
pub fn become_subreaper() -> bool {
    #[cfg(target_os = "linux")]
    {
        // SAFETY: prctl with integer arguments only.
        unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0 }
    }
    #[cfg(not(target_os = "linux"))]
    {
        false
    }
}

/// `main()` entry: when [`wanted`], run the agent (this executable, same arguments) as a child
/// under [`run_as_init`] and return the exit code to leave with.
pub fn maybe_run_as_init() -> Option<i32> {
    if !wanted() {
        return None;
    }
    let exe = std::env::current_exe().ok()?;
    if std::process::id() != 1 {
        become_subreaper();
    }
    let args: Vec<OsString> = std::env::args_os().skip(1).collect();
    match run_as_init(&exe, &args) {
        Ok(outcome) => Some(outcome.code),
        Err(e) => {
            eprintln!("gpudeck-agent: init could not start the agent ({e}); running without it");
            None
        }
    }
}

#[cfg(unix)]
mod imp {
    use std::sync::atomic::{AtomicI32, Ordering};

    pub static CHILD: AtomicI32 = AtomicI32::new(0);

    pub extern "C" fn forward(sig: libc::c_int) {
        let pid = CHILD.load(Ordering::SeqCst);
        if pid > 0 {
            // SAFETY: kill(2) is async-signal-safe.
            unsafe {
                libc::kill(pid, sig);
            }
        }
    }

    pub const FORWARDED: [libc::c_int; 7] = [
        libc::SIGTERM,
        libc::SIGINT,
        libc::SIGHUP,
        libc::SIGQUIT,
        libc::SIGUSR1,
        libc::SIGUSR2,
        libc::SIGWINCH,
    ];

    pub fn install_forwarders() {
        for sig in FORWARDED {
            // SAFETY: a plain sigaction with an async-signal-safe handler.
            unsafe {
                let mut sa: libc::sigaction = std::mem::zeroed();
                sa.sa_sigaction = forward as extern "C" fn(libc::c_int) as libc::sighandler_t;
                sa.sa_flags = libc::SA_RESTART;
                libc::sigemptyset(&mut sa.sa_mask);
                libc::sigaction(sig, &sa, std::ptr::null_mut());
            }
        }
    }

    pub fn exit_code(status: libc::c_int) -> i32 {
        if libc::WIFEXITED(status) {
            libc::WEXITSTATUS(status)
        } else if libc::WIFSIGNALED(status) {
            128 + libc::WTERMSIG(status)
        } else {
            1
        }
    }
}

/// Spawn `program args` and act as its init until it exits: forward termination signals to it,
/// wait for every child of this process (it and any orphan re-parented here), return its
/// status. The child runs with [`INIT_CHILD_ENV`] set.
#[cfg(unix)]
pub fn run_as_init(program: &Path, args: &[OsString]) -> std::io::Result<InitOutcome> {
    use std::sync::atomic::Ordering;
    imp::install_forwarders();
    let child = std::process::Command::new(program)
        .args(args)
        .env(INIT_CHILD_ENV, "1")
        .spawn()?;
    let main = child.id() as libc::pid_t;
    imp::CHILD.store(main, Ordering::SeqCst);
    drop(child); // reaped below with waitpid(-1)
    let mut orphans_reaped = 0;
    loop {
        let mut status: libc::c_int = 0;
        // SAFETY: waitpid on any child of this process; status is a valid out pointer.
        let pid = unsafe { libc::waitpid(-1, &mut status, 0) };
        if pid == main {
            imp::CHILD.store(0, Ordering::SeqCst);
            return Ok(InitOutcome {
                code: imp::exit_code(status),
                orphans_reaped,
            });
        }
        if pid > 0 {
            orphans_reaped += 1;
            continue;
        }
        let err = std::io::Error::last_os_error();
        if err.raw_os_error() == Some(libc::EINTR) {
            continue;
        }
        // ECHILD: the main child is gone without us seeing it (should not happen).
        return Err(err);
    }
}

#[cfg(not(unix))]
pub fn run_as_init(_program: &Path, _args: &[OsString]) -> std::io::Result<InitOutcome> {
    Err(std::io::Error::other("init mode needs a Unix system"))
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn exit_codes() {
        // Encoded wait statuses: exit 7 is 7 << 8; killed by SIGTERM is the signal number.
        assert_eq!(imp::exit_code(7 << 8), 7);
        assert_eq!(imp::exit_code(libc::SIGTERM), 128 + libc::SIGTERM);
    }

    #[test]
    fn the_child_does_not_split_again() {
        // The test binary is not PID 1 and INIT_ENV is unset here.
        if std::env::var_os(INIT_ENV).is_none() && std::process::id() != 1 {
            assert!(!wanted());
        }
    }
}
