//! Host facts for `hello`, `inventory.system`, `heartbeat.load` and disks (sysinfo).

use std::path::Path;
use std::sync::Mutex;

use gpudeck_protocol::{Disk, NodeArch, NodeLoad, NodeOs, SystemInfo};
use sysinfo::{Disks, Networks, Pid, ProcessesToUpdate, System};

pub struct HostInfo {
    pub hostname: String,
    pub os: NodeOs,
    pub os_version: Option<String>,
    pub arch: NodeArch,
    pub kernel_version: Option<String>,
    pub ip_addresses: Vec<String>,
    pub cpu_cores: Option<u64>,
    pub memory_total_bytes: Option<u64>,
    pub boot_id: Option<String>,
}

pub fn host_info() -> HostInfo {
    let os = if cfg!(target_os = "macos") {
        NodeOs::Macos
    } else {
        NodeOs::Linux
    };
    let arch = if cfg!(target_arch = "aarch64") {
        NodeArch::Aarch64
    } else {
        NodeArch::X86_64
    };
    // sysinfo reports an unreadable source as an error; a host fact it cannot read is omitted.
    let sys = System::new_all().ok();
    HostInfo {
        hostname: System::host_name().unwrap_or_else(|_| "unknown".into()),
        os,
        os_version: System::os_version().ok(),
        arch,
        kernel_version: System::kernel_version().ok(),
        ip_addresses: ip_addresses(),
        cpu_cores: sys
            .as_ref()
            .map(|s| s.cpus().len() as u64)
            .filter(|n| *n > 0),
        memory_total_bytes: sys.as_ref().map(System::total_memory).filter(|m| *m > 0),
        boot_id: boot_id(),
    }
}

/// Non-loopback unicast addresses.
pub fn ip_addresses() -> Vec<String> {
    let Ok(networks) = Networks::new_with_refreshed_list() else {
        return vec![];
    };
    let mut out: Vec<String> = vec![];
    for data in networks.values() {
        for net in data.ip_networks() {
            let addr = net.addr;
            let skip = match addr {
                std::net::IpAddr::V4(v4) => {
                    v4.is_loopback() || v4.is_link_local() || v4.is_unspecified()
                }
                std::net::IpAddr::V6(v6) => {
                    v6.is_loopback() || v6.is_unspecified() || (v6.segments()[0] & 0xffc0) == 0xfe80
                }
            };
            if !skip {
                out.push(addr.to_string());
            }
        }
    }
    out.sort();
    out.dedup();
    out
}

fn boot_id() -> Option<String> {
    if let Ok(s) = std::fs::read_to_string("/proc/sys/kernel/random/boot_id") {
        return Some(s.trim().to_string());
    }
    let t = System::boot_time().ok()?;
    (t > 0).then(|| format!("boot-{t}"))
}

static SELF_SYSTEM: Mutex<Option<System>> = Mutex::new(None);

/// CPU % and RSS of this process (sysinfo needs two refreshes for a CPU reading).
pub fn self_usage() -> (Option<f64>, Option<u64>) {
    let pid = Pid::from_u32(std::process::id());
    let mut guard = SELF_SYSTEM.lock().unwrap_or_else(|e| e.into_inner());
    if guard.is_none() {
        *guard = System::new().ok();
    }
    let Some(sys) = guard.as_mut() else {
        return (None, None);
    };
    sys.refresh_processes(ProcessesToUpdate::Some(&[pid]), true);
    match sys.process(pid) {
        Some(p) => (Some(p.cpu_usage() as f64), Some(p.memory())),
        None => (None, None),
    }
}

/// RSS of an arbitrary pid, bytes.
pub fn process_rss(pid: u32) -> Option<u64> {
    let mut sys = System::new().ok()?;
    let p = Pid::from_u32(pid);
    sys.refresh_processes(ProcessesToUpdate::Some(&[p]), true);
    sys.process(p).map(|p| p.memory())
}

pub fn system_info() -> SystemInfo {
    let mut sys = System::new().ok();
    if let Some(sys) = sys.as_mut() {
        sys.refresh_memory();
        sys.refresh_cpu_all();
    }
    SystemInfo {
        cpu_cores: sys
            .as_ref()
            .map(|s| s.cpus().len() as u64)
            .filter(|n| *n > 0),
        memory_total_bytes: sys.as_ref().map(System::total_memory).filter(|m| *m > 0),
        memory_used_bytes: sys.as_ref().map(System::used_memory),
        load_avg_1m: System::load_average().ok().map(|l| l.one),
        uptime_seconds: System::uptime().ok(),
    }
}

pub fn node_load(cache_used_bytes: u64, cache_dir: &Path) -> NodeLoad {
    let mut sys = System::new().ok();
    if let Some(sys) = sys.as_mut() {
        sys.refresh_memory();
        // `System::new()` has an empty CPU list until it is refreshed (the load average was
        // divided by 1 instead of the core count).
        sys.refresh_cpu_list(sysinfo::CpuRefreshKind::nothing());
    }
    let cores = sys.as_ref().map_or(0, |s| s.cpus().len()).max(1) as f64;
    NodeLoad {
        cpu_pct: System::load_average()
            .ok()
            .map(|l| l.one * 100.0 / cores)
            .filter(|v| v.is_finite()),
        memory_used_bytes: sys.as_ref().map(System::used_memory),
        cache_used_bytes: Some(cache_used_bytes),
        disk_free_bytes: disk_free(cache_dir),
    }
}

/// Mounted filesystems; the one holding `cache_dir` is flagged.
pub fn disks(cache_dir: &Path) -> Vec<Disk> {
    let Ok(disks) = Disks::new_with_refreshed_list() else {
        return vec![];
    };
    let cache_mount = mount_for(&disks, cache_dir);
    let mut out: Vec<Disk> = disks
        .iter()
        .filter(|d| d.total_space() > 0)
        .map(|d| Disk {
            mount: d.mount_point().to_string_lossy().to_string(),
            device: Some(d.name().to_string_lossy().to_string()).filter(|s| !s.is_empty()),
            fs: Some(d.file_system().to_string_lossy().to_string()).filter(|s| !s.is_empty()),
            total_bytes: d.total_space(),
            free_bytes: d.available_space(),
            is_cache_dir: cache_mount.as_deref() == Some(d.mount_point()),
        })
        .collect();
    out.sort_by(|a, b| a.mount.cmp(&b.mount));
    out.dedup_by(|a, b| a.mount == b.mount);
    dedupe_devices(out)
}

/// One entry per block device: bind mounts of the same filesystem (systemd sandboxing shows `/`
/// again as `/home`, `/root`, `/var/lib/gpudeck`, ...) collapse into the shortest mount, which
/// is flagged as the cache disk when any of its mounts holds the cache dir.
fn dedupe_devices(disks: Vec<Disk>) -> Vec<Disk> {
    let mut out: Vec<Disk> = Vec::with_capacity(disks.len());
    for d in disks {
        let same = d.device.as_ref().and_then(|dev| {
            out.iter_mut().find(|o| {
                o.device.as_deref() == Some(dev.as_str())
                    && o.total_bytes == d.total_bytes
                    && dev.starts_with("/dev/")
            })
        });
        match same {
            Some(o) => o.is_cache_dir |= d.is_cache_dir,
            None => out.push(d),
        }
    }
    out
}

fn mount_for(disks: &Disks, path: &Path) -> Option<std::path::PathBuf> {
    let path = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    disks
        .iter()
        .filter(|d| path.starts_with(d.mount_point()))
        .max_by_key(|d| d.mount_point().as_os_str().len())
        .map(|d| d.mount_point().to_path_buf())
}

/// Free bytes on the filesystem holding `path` (walks up to the nearest existing ancestor).
pub fn disk_free(path: &Path) -> Option<u64> {
    let disks = Disks::new_with_refreshed_list().ok()?;
    let mut p = path.to_path_buf();
    while !p.exists() {
        p = p.parent()?.to_path_buf();
    }
    let mount = mount_for(&disks, &p)?;
    disks
        .iter()
        .find(|d| d.mount_point() == mount)
        .map(|d| d.available_space())
}

pub fn disk_total(path: &Path) -> Option<u64> {
    let disks = Disks::new_with_refreshed_list().ok()?;
    let mut p = path.to_path_buf();
    while !p.exists() {
        p = p.parent()?.to_path_buf();
    }
    let mount = mount_for(&disks, &p)?;
    disks
        .iter()
        .find(|d| d.mount_point() == mount)
        .map(|d| d.total_space())
}

/// Detect the container runtime available on PATH.
/// Container CLI to use (`docker` / `podman` / `nerdctl`): the forced one when it exists, else the
/// first found on `PATH`. The CLI name is what the agent executes; `hello` reports `nerdctl` as
/// `containerd` (see [`container_runtime_label`]).
pub fn container_runtime(forced: Option<&str>) -> Option<String> {
    if let Some(f) = forced.map(str::trim).filter(|f| !f.is_empty()) {
        let cli = if f == "containerd" { "nerdctl" } else { f };
        return which(cli).map(|_| cli.to_string());
    }
    ["docker", "podman", "nerdctl"]
        .into_iter()
        .find(|n| which(n).is_some())
        .map(str::to_string)
}

/// `hello.capabilities.container_runtime` spelling of a container CLI.
pub fn container_runtime_label(cli: &str) -> String {
    match cli {
        "nerdctl" => "containerd".into(),
        other => other.into(),
    }
}

/// Minimal `which`.
pub fn which(bin: &str) -> Option<std::path::PathBuf> {
    let p = Path::new(bin);
    if p.is_absolute() {
        return p.is_file().then(|| p.to_path_buf());
    }
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|d| d.join(bin))
        .find(|c| c.is_file())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn disk(mount: &str, device: &str, cache: bool) -> Disk {
        Disk {
            mount: mount.into(),
            device: Some(device.into()),
            fs: Some("ext4".into()),
            total_bytes: 100,
            free_bytes: 50,
            is_cache_dir: cache,
        }
    }

    #[test]
    fn bind_mounts_of_one_device_collapse() {
        let out = dedupe_devices(vec![
            disk("/", "/dev/vda1", false),
            disk("/boot", "/dev/vda13", false),
            disk("/home", "/dev/vda1", false),
            disk("/var/lib/gpudeck", "/dev/vda1", true),
            disk("/run", "tmpfs", false),
            disk("/tmp", "tmpfs", false),
        ]);
        let mounts: Vec<&str> = out.iter().map(|d| d.mount.as_str()).collect();
        assert_eq!(mounts, ["/", "/boot", "/run", "/tmp"]);
        assert!(out[0].is_cache_dir);
    }

    #[test]
    fn node_cpu_pct_is_per_core() {
        let cores = host_info().cpu_cores.unwrap_or(1) as f64;
        let pct = node_load(0, Path::new("/")).cpu_pct.unwrap();
        let expected = System::load_average().expect("load average").one * 100.0 / cores;
        assert!(
            (pct - expected).abs() < 100.0 / cores + 1.0,
            "{pct} vs {expected}"
        );
    }

    #[test]
    fn host_facts_are_sane() {
        let h = host_info();
        assert!(!h.hostname.is_empty());
        assert!(h.cpu_cores.unwrap_or(0) > 0);
        assert!(h.memory_total_bytes.unwrap_or(0) > 0);
        let d = disks(Path::new("/"));
        assert!(
            d.iter().any(|d| d.is_cache_dir),
            "root mount flagged: {d:?}"
        );
        assert!(disk_free(Path::new("/definitely/missing/dir")).is_some());
        let (_, rss) = self_usage();
        assert!(rss.unwrap_or(0) > 0);
    }
}
