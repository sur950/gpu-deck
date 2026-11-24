//! Read-only `/proc` lookups that tie an observed server to its processes (Linux only):
//!
//! * `listen_port` → the socket inode listening on that TCP port (`/proc/net/tcp{,6}`) → the pid
//!   holding it (`/proc/<pid>/fd/*` → `socket:[inode]`; needs permission to read the target's
//!   `fd` directory: same user, root or `CAP_SYS_PTRACE`);
//! * `pid_cmd_contains` → pids whose `/proc/<pid>/cmdline` contains the text (matched locally,
//!   never reported);
//! * the matched pids plus all their descendants (`/proc/<pid>/stat` ppid): vLLM's API server
//!   listens while its `EngineCore` child holds the GPU memory.
//!
//! Nothing here signals, ptraces or writes anything.

use std::collections::{BTreeSet, HashMap};
use std::path::Path;

/// Socket inodes in state LISTEN (`0A`) on `port` from a `/proc/net/tcp`-format table.
pub fn listen_inodes(table: &str, port: u16) -> Vec<u64> {
    let mut out = vec![];
    for line in table.lines().skip(1) {
        let cols: Vec<&str> = line.split_whitespace().collect();
        if cols.len() < 10 || cols[3] != "0A" {
            continue;
        }
        let Some((_, port_hex)) = cols[1].rsplit_once(':') else {
            continue;
        };
        if u16::from_str_radix(port_hex, 16).ok() == Some(port) {
            if let Ok(inode) = cols[9].parse::<u64>() {
                if inode != 0 {
                    out.push(inode);
                }
            }
        }
    }
    out
}

fn pids(root: &Path) -> Vec<u32> {
    std::fs::read_dir(root)
        .map(|d| {
            d.filter_map(|e| e.ok()?.file_name().to_str()?.parse::<u32>().ok())
                .collect()
        })
        .unwrap_or_default()
}

/// Pid holding one of `inodes` (first match). `Err(true)` = some `fd` directories could not be
/// read (permission), so the listener may be invisible to this agent.
pub fn pid_for_inodes(root: &Path, inodes: &[u64]) -> Result<u32, bool> {
    if inodes.is_empty() {
        return Err(false);
    }
    let wanted: Vec<String> = inodes.iter().map(|i| format!("socket:[{i}]")).collect();
    let mut denied = false;
    for pid in pids(root) {
        let fd_dir = root.join(pid.to_string()).join("fd");
        let entries = match std::fs::read_dir(&fd_dir) {
            Ok(e) => e,
            Err(_) => {
                denied = true;
                continue;
            }
        };
        for e in entries.flatten() {
            if let Ok(target) = std::fs::read_link(e.path()) {
                if wanted.iter().any(|w| target.as_os_str() == w.as_str()) {
                    return Ok(pid);
                }
            }
        }
    }
    Err(denied)
}

/// Pids whose command line contains `needle` (never `self_pid`).
pub fn pids_with_cmd(root: &Path, needle: &str, self_pid: u32) -> Vec<u32> {
    if needle.is_empty() {
        return vec![];
    }
    pids(root)
        .into_iter()
        .filter(|p| *p != self_pid)
        .filter(|p| {
            std::fs::read(root.join(p.to_string()).join("cmdline"))
                .map(|raw| {
                    let text: Vec<u8> = raw
                        .iter()
                        .map(|b| if *b == 0 { b' ' } else { *b })
                        .collect();
                    String::from_utf8_lossy(&text).contains(needle)
                })
                .unwrap_or(false)
        })
        .collect()
}

/// Parent pid from `/proc/<pid>/stat` (the field after the parenthesised comm).
pub fn ppid_of(stat: &str) -> Option<u32> {
    let rest = &stat[stat.rfind(')')? + 1..];
    rest.split_whitespace().nth(1)?.parse().ok()
}

fn children_map(root: &Path) -> HashMap<u32, Vec<u32>> {
    let mut children: HashMap<u32, Vec<u32>> = HashMap::new();
    for pid in pids(root) {
        if let Some(pp) = std::fs::read_to_string(root.join(pid.to_string()).join("stat"))
            .ok()
            .as_deref()
            .and_then(ppid_of)
        {
            children.entry(pp).or_default().push(pid);
        }
    }
    children
}

/// pid → owner for every root and its descendants (first root wins). Used to tag NVML GPU
/// processes with the managed instance whose engine spawned them.
pub fn owner_map<T: Copy>(root: &Path, roots: &[(u32, T)]) -> HashMap<u32, T> {
    let children = children_map(root);
    let mut out: HashMap<u32, T> = HashMap::new();
    for (pid, owner) in roots {
        let mut stack = vec![*pid];
        while let Some(p) = stack.pop() {
            if out.contains_key(&p) {
                continue;
            }
            out.insert(p, *owner);
            if let Some(c) = children.get(&p) {
                stack.extend(c.iter().copied());
            }
        }
    }
    out
}

/// `roots` plus every descendant.
pub fn with_descendants(root: &Path, roots: &[u32]) -> BTreeSet<u32> {
    let children = children_map(root);
    let mut out: BTreeSet<u32> = BTreeSet::new();
    let mut stack: Vec<u32> = roots.to_vec();
    while let Some(p) = stack.pop() {
        if out.insert(p) {
            if let Some(c) = children.get(&p) {
                stack.extend(c.iter().copied());
            }
        }
    }
    out
}

/// What one lookup found.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Match {
    /// The listener (or first command-line match).
    pub pid: Option<u32>,
    /// `pid`, the other command-line matches and all their descendants.
    pub tree: BTreeSet<u32>,
    /// Set when the listener exists but its owner could not be read (permissions).
    pub note: Option<String>,
}

/// Resolve the processes of one observed server against a procfs root (`/proc` in production).
pub fn resolve(
    root: &Path,
    listen_port: Option<u16>,
    cmd_contains: Option<&str>,
    self_pid: u32,
) -> Match {
    let mut m = Match::default();
    let mut roots: Vec<u32> = vec![];
    if let Some(port) = listen_port {
        let mut inodes = vec![];
        for t in ["net/tcp", "net/tcp6"] {
            if let Ok(table) = std::fs::read_to_string(root.join(t)) {
                inodes.extend(listen_inodes(&table, port));
            }
        }
        match pid_for_inodes(root, &inodes) {
            Ok(pid) => roots.push(pid),
            Err(true) => {
                m.note = Some(format!(
                    "port {port}: listener not visible to the agent user (needs read access to \
                     /proc/<pid>/fd; use pid_cmd_contains or run the agent as the server's user)"
                ))
            }
            Err(false) if inodes.is_empty() => {
                m.note = Some(format!("nothing listens on port {port}"))
            }
            Err(false) => {}
        }
    }
    if let Some(needle) = cmd_contains {
        for p in pids_with_cmd(root, needle, self_pid) {
            if !roots.contains(&p) {
                roots.push(p);
            }
        }
        if !roots.is_empty() {
            m.note = None;
        }
    }
    m.pid = roots.first().copied();
    m.tree = with_descendants(root, &roots);
    m
}

#[cfg(test)]
mod tests {
    use super::*;

    const TCP: &str = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F40 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 4242 1 0000000000000000 100 0 0 10 0
   1: 0100007F:1FA4 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 4343 1 0000000000000000 100 0 0 10 0
   2: 0100007F:1F40 0100007F:D4C2 01 00000000:00000000 00:00000000 00000000     0        0 5555 1 0000000000000000 100 0 0 10 0
";

    #[test]
    fn listen_table_parsing() {
        assert_eq!(listen_inodes(TCP, 8000), vec![4242]);
        assert_eq!(listen_inodes(TCP, 8100), vec![4343]);
        assert!(listen_inodes(TCP, 8200).is_empty());
        assert_eq!(
            ppid_of("1234 (VLLM::EngineCor e) S 1200 1234 1 0 -1 4194560"),
            Some(1200)
        );
    }

    #[cfg(unix)]
    #[test]
    fn resolves_listener_and_its_children() {
        let root = tempfile::tempdir().unwrap();
        let r = root.path();
        std::fs::create_dir_all(r.join("net")).unwrap();
        std::fs::write(r.join("net/tcp"), TCP).unwrap();
        let proc_dir = |pid: u32, ppid: u32, cmd: &str| {
            let d = r.join(pid.to_string());
            std::fs::create_dir_all(d.join("fd")).unwrap();
            std::fs::write(d.join("stat"), format!("{pid} (x) S {ppid} 1 1")).unwrap();
            std::fs::write(d.join("cmdline"), cmd.replace(' ', "\0")).unwrap();
            d
        };
        let api = proc_dir(100, 1, "python3 -m vllm.entrypoints.openai.api_server");
        std::os::unix::fs::symlink("socket:[4242]", api.join("fd/3")).unwrap();
        proc_dir(101, 100, "VLLM::EngineCore");
        proc_dir(102, 101, "worker");
        let img = proc_dir(200, 1, "python image_engine.py --port 8100");
        proc_dir(300, 1, "python other_job.py");
        let m = resolve(r, Some(8000), None, 999);
        assert_eq!(m.pid, Some(100));
        assert_eq!(
            m.tree.iter().copied().collect::<Vec<_>>(),
            vec![100, 101, 102]
        );
        // No fd link for 8100 but the command line matches.
        let m = resolve(r, Some(8100), Some("image_engine"), 999);
        assert_eq!(m.pid, Some(200));
        assert_eq!(m.note, None);
        let _ = img;
        let m = resolve(r, Some(8200), None, 999);
        assert_eq!(m.pid, None);
        assert!(m.note.unwrap().contains("nothing listens"));
        // The agent never matches itself.
        assert!(resolve(r, None, Some("other_job"), 300).tree.is_empty());
        // Ownership of GPU processes: an engine's whole tree, first root wins.
        let owners = owner_map(r, &[(100, 'a'), (101, 'b'), (300, 'c')]);
        assert_eq!(owners.get(&102), Some(&'a'));
        assert_eq!(owners.get(&101), Some(&'a'));
        assert_eq!(owners.get(&300), Some(&'c'));
        assert_eq!(owners.get(&200), None);
    }
}
