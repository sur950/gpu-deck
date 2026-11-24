//! Common device types shared by every vendor plugin.
//!
//! `Device` mirrors `common.json#/$defs/Device` field for field (inventory → `devices` table) and
//! `DeviceSample` mirrors `common.json#/$defs/GpuSample` minus `device_id` (the control-plane UUID
//! is assigned by the hub; the agent matches on `(vendor, index)` / `uuid`). Anything a plugin
//! cannot provide is `None` → JSON `null`, never `0` (metrics.md conventions).

use std::collections::BTreeMap;

use async_trait::async_trait;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Accelerator vendor — values match `common.json#/$defs/device_vendor`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Vendor {
    Nvidia,
    Amd,
    Hygon,
    Metax,
    Moorethreads,
    Thead,
    Apple,
}

impl Vendor {
    pub const ALL: [Vendor; 7] = [
        Vendor::Nvidia,
        Vendor::Amd,
        Vendor::Hygon,
        Vendor::Metax,
        Vendor::Moorethreads,
        Vendor::Thead,
        Vendor::Apple,
    ];

    /// Wire name (`nvidia`, `amd`, ...).
    pub fn as_str(&self) -> &'static str {
        match self {
            Vendor::Nvidia => "nvidia",
            Vendor::Amd => "amd",
            Vendor::Hygon => "hygon",
            Vendor::Metax => "metax",
            Vendor::Moorethreads => "moorethreads",
            Vendor::Thead => "thead",
            Vendor::Apple => "apple",
        }
    }

    /// Human readable vendor stack name (CUDA, ROCm, Metal, ...).
    pub fn stack(&self) -> &'static str {
        match self {
            Vendor::Nvidia => "CUDA",
            Vendor::Amd => "ROCm",
            Vendor::Hygon => "DTK",
            Vendor::Metax => "MACA",
            Vendor::Moorethreads => "MUSA",
            Vendor::Thead => "PPU SDK",
            Vendor::Apple => "Metal",
        }
    }
}

impl std::fmt::Display for Vendor {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

impl std::str::FromStr for Vendor {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "nvidia" => Ok(Vendor::Nvidia),
            "amd" => Ok(Vendor::Amd),
            "hygon" => Ok(Vendor::Hygon),
            "metax" => Ok(Vendor::Metax),
            "moorethreads" | "moore_threads" | "mthreads" => Ok(Vendor::Moorethreads),
            "thead" | "t-head" | "t_head" => Ok(Vendor::Thead),
            "apple" => Ok(Vendor::Apple),
            other => Err(format!("unknown vendor {other:?}")),
        }
    }
}

/// `common.json#/$defs/device_health`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum DeviceHealth {
    Healthy,
    Degraded,
    Unhealthy,
    #[default]
    Unknown,
}

/// GPU slice for one instance on one device (`common.json#/$defs/Slice`). `None` = whole device.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Slice {
    pub vram_bytes: u64,
    pub compute_pct: u8,
}

/// Common Device format produced by every vendor plugin — maps 1:1 to the `devices` table and to
/// `common.json#/$defs/Device` (same field names, same optionality).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Device {
    pub vendor: Vendor,
    /// Vendor ordinal (CUDA_VISIBLE_DEVICES index, amd-smi gpu id, 0 on Apple).
    pub index: u32,
    /// Vendor UUID; stable across reboots when available.
    #[serde(default)]
    pub uuid: Option<String>,
    pub name: String,
    /// Apple: Metal `recommendedMaxWorkingSetSize` (wired limit).
    pub vram_total_bytes: u64,
    #[serde(default)]
    pub is_unified_memory: bool,
    #[serde(default)]
    pub driver_version: Option<String>,
    /// CUDA / ROCm / MUSA / Metal version.
    #[serde(default)]
    pub runtime_version: Option<String>,
    /// NVIDIA "8.9", AMD "gfx942", Apple "metal3"/"metal4".
    #[serde(default)]
    pub compute_capability: Option<String>,
    #[serde(default)]
    pub pci_bus_id: Option<String>,
    #[serde(default)]
    pub numa_node: Option<i32>,
    #[serde(default)]
    pub max_power_watts: Option<f64>,
    /// SMs / CUs / Apple GPU cores.
    #[serde(default)]
    pub core_count: Option<u32>,
    pub health: DeviceHealth,
    #[serde(default)]
    pub health_reason: Option<String>,
    #[serde(default)]
    pub labels: BTreeMap<String, String>,
    /// Vendor-specific raw fields (never required by the control plane).
    #[serde(default)]
    pub extra: serde_json::Map<String, serde_json::Value>,
}

impl Device {
    /// Minimal device with every optional field empty; plugins fill what they know.
    pub fn new(vendor: Vendor, index: u32, name: impl Into<String>, vram_total_bytes: u64) -> Self {
        Device {
            vendor,
            index,
            uuid: None,
            name: name.into(),
            vram_total_bytes,
            is_unified_memory: false,
            driver_version: None,
            runtime_version: None,
            compute_capability: None,
            pci_bus_id: None,
            numa_node: None,
            max_power_watts: None,
            core_count: None,
            health: DeviceHealth::Unknown,
            health_reason: None,
            labels: BTreeMap::new(),
            extra: serde_json::Map::new(),
        }
    }

    /// Stable key used to match samples to devices: the UUID when known, else `vendor:index`.
    pub fn key(&self) -> String {
        match &self.uuid {
            Some(u) if !u.is_empty() => u.clone(),
            _ => format!("{}:{}", self.vendor, self.index),
        }
    }
}

/// One GPU metric sample — `common.json#/$defs/GpuSample` minus `device_id`, plus the
/// `(vendor, index, uuid)` identity the agent uses to attach the hub's `device_id`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DeviceSample {
    #[serde(default = "default_vendor")]
    pub vendor: Vendor,
    pub index: u32,
    #[serde(default)]
    pub uuid: Option<String>,
    /// Sample time (RFC 3339, millisecond precision when serialized by the agent).
    #[serde(default = "Utc::now")]
    pub ts: DateTime<Utc>,
    pub utilization_pct: Option<f64>,
    pub memory_util_pct: Option<f64>,
    pub memory_used_bytes: Option<u64>,
    pub memory_total_bytes: Option<u64>,
    pub power_watts: Option<f64>,
    pub power_limit_watts: Option<f64>,
    pub temperature_c: Option<f64>,
    pub memory_temperature_c: Option<f64>,
    pub sm_clock_mhz: Option<u32>,
    pub memory_clock_mhz: Option<u32>,
    pub fan_pct: Option<f64>,
    pub encoder_pct: Option<f64>,
    pub decoder_pct: Option<f64>,
    pub pcie_tx_bytes_per_s: Option<u64>,
    pub pcie_rx_bytes_per_s: Option<u64>,
    pub ecc_errors_total: Option<u64>,
    /// Errors (Xid / RAS) seen since the previous sample.
    pub error_count: Option<u64>,
    pub throttle_reasons: Option<Vec<String>>,
    pub process_count: Option<u32>,
    /// Processes holding memory on the device (NVIDIA/NVML); `None` when the backend cannot list
    /// them.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub processes: Option<Vec<GpuProcess>>,
    #[serde(default)]
    pub extra: Option<serde_json::Map<String, serde_json::Value>>,
}

/// One process using a GPU (`common.json#/$defs/GpuProcess`): pid, device memory and the
/// executable basename. Command line arguments are never read.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GpuProcess {
    pub pid: u32,
    pub used_memory_bytes: Option<u64>,
    pub name: Option<String>,
}

/// Most processes reported per device (largest memory first).
pub const MAX_GPU_PROCESSES: usize = 64;

/// Executable name of `pid` from `/proc` (Linux): `/proc/<pid>/comm`, else the basename of
/// `argv[0]` from `/proc/<pid>/cmdline` (only the first NUL-separated field is read, so no
/// arguments ever leave the host). `None` elsewhere or when the process is gone / not visible.
pub fn process_name(pid: u32) -> Option<String> {
    process_name_in(std::path::Path::new("/proc"), pid)
}

/// [`process_name`] against an arbitrary procfs root (tests).
pub fn process_name_in(proc_root: &std::path::Path, pid: u32) -> Option<String> {
    if !cfg!(target_os = "linux") && proc_root == std::path::Path::new("/proc") {
        return None;
    }
    let dir = proc_root.join(pid.to_string());
    if let Ok(comm) = std::fs::read_to_string(dir.join("comm")) {
        let c = comm.trim();
        if !c.is_empty() {
            return Some(c.chars().take(255).collect());
        }
    }
    let raw = std::fs::read(dir.join("cmdline")).ok()?;
    let argv0 = raw.split(|b| *b == 0).next()?;
    let argv0 = String::from_utf8_lossy(argv0);
    let base = argv0.rsplit('/').next()?.trim();
    (!base.is_empty()).then(|| base.chars().take(255).collect())
}

/// Sort by memory (largest first, unknown last), then pid, and cap at [`MAX_GPU_PROCESSES`].
pub fn tidy_processes(mut v: Vec<GpuProcess>) -> Vec<GpuProcess> {
    v.sort_by(|a, b| {
        b.used_memory_bytes
            .unwrap_or(0)
            .cmp(&a.used_memory_bytes.unwrap_or(0))
            .then(a.pid.cmp(&b.pid))
    });
    v.dedup_by_key(|p| p.pid);
    v.truncate(MAX_GPU_PROCESSES);
    v
}

fn default_vendor() -> Vendor {
    Vendor::Apple
}

impl Default for DeviceSample {
    fn default() -> Self {
        DeviceSample {
            vendor: default_vendor(),
            index: 0,
            uuid: None,
            ts: Utc::now(),
            utilization_pct: None,
            memory_util_pct: None,
            memory_used_bytes: None,
            memory_total_bytes: None,
            power_watts: None,
            power_limit_watts: None,
            temperature_c: None,
            memory_temperature_c: None,
            sm_clock_mhz: None,
            memory_clock_mhz: None,
            fan_pct: None,
            encoder_pct: None,
            decoder_pct: None,
            pcie_tx_bytes_per_s: None,
            pcie_rx_bytes_per_s: None,
            ecc_errors_total: None,
            error_count: None,
            throttle_reasons: None,
            process_count: None,
            processes: None,
            extra: None,
        }
    }
}

impl DeviceSample {
    pub fn new(vendor: Vendor, index: u32, uuid: Option<String>) -> Self {
        DeviceSample {
            vendor,
            index,
            uuid,
            ts: Utc::now(),
            ..Default::default()
        }
    }

    /// Stable key matching `Device::key()`.
    pub fn key(&self) -> String {
        match &self.uuid {
            Some(u) if !u.is_empty() => u.clone(),
            _ => format!("{}:{}", self.vendor, self.index),
        }
    }

    /// The `GpuSample` wire object for `metrics_batch.gpu[]`: exactly the schema's properties
    /// (`ts`, `device_id` + metric columns), with `null` for everything unknown.
    pub fn to_gpu_sample(&self, device_id: &str) -> serde_json::Value {
        let mut v = serde_json::to_value(self).unwrap_or(serde_json::Value::Null);
        if let serde_json::Value::Object(ref mut m) = v {
            m.remove("vendor");
            m.remove("index");
            m.remove("uuid");
            m.insert(
                "device_id".into(),
                serde_json::Value::String(device_id.to_string()),
            );
            m.insert(
                "ts".into(),
                serde_json::Value::String(
                    self.ts.to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
                ),
            );
        }
        v
    }
}

/// A vendor device plugin. `detect` is called at startup and on inventory refresh; `sample`
/// every `METRICS_INTERVAL_SECONDS` (2 s). Both must be cheap, never panic, and return partial
/// data (with `None`s) rather than fail when a single counter is unavailable.
#[async_trait]
pub trait DevicePlugin: Send + Sync {
    fn vendor(&self) -> Vendor;
    async fn detect(&self) -> anyhow::Result<Vec<Device>>;
    async fn sample(&self) -> anyhow::Result<Vec<DeviceSample>>;

    /// Short description of the backend in use (`nvml`, `nvidia-smi`, `amd-smi`, `iokit`...), for
    /// logs and the node inventory `labels`.
    fn backend(&self) -> String {
        self.vendor().as_str().to_string()
    }

    /// How VRAM / compute slices map onto this vendor's hardware (MIG/MPS, CU masks, memory caps).
    fn slicing(&self) -> crate::slicing::SliceSupport {
        crate::slicing::slice_support(self.vendor())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn process_names_come_from_comm_or_argv0_only() {
        let root = tempfile::tempdir().unwrap();
        let p1 = root.path().join("100");
        std::fs::create_dir_all(&p1).unwrap();
        std::fs::write(p1.join("comm"), "VLLM::EngineCor\n").unwrap();
        std::fs::write(
            p1.join("cmdline"),
            b"python3\0-m\0vllm\0--api-key\0secret\0",
        )
        .unwrap();
        assert_eq!(
            process_name_in(root.path(), 100).as_deref(),
            Some("VLLM::EngineCor")
        );
        // No comm: basename of argv[0], never the arguments.
        let p2 = root.path().join("200");
        std::fs::create_dir_all(&p2).unwrap();
        std::fs::write(
            p2.join("cmdline"),
            b"/opt/venv/bin/python3\0serve.py\0--token\0secret\0",
        )
        .unwrap();
        let name = process_name_in(root.path(), 200).unwrap();
        assert_eq!(name, "python3");
        assert!(!name.contains("secret"));
        assert_eq!(process_name_in(root.path(), 300), None);
    }

    #[test]
    fn processes_sorted_deduped_and_capped() {
        let mk = |pid, mem| GpuProcess {
            pid,
            used_memory_bytes: mem,
            name: None,
        };
        let v = tidy_processes(vec![
            mk(3, None),
            mk(1, Some(10)),
            mk(2, Some(30)),
            mk(2, Some(30)),
        ]);
        assert_eq!(v.iter().map(|p| p.pid).collect::<Vec<_>>(), vec![2, 1, 3]);
        let many = (0..200).map(|i| mk(i, Some(i as u64))).collect();
        assert_eq!(tidy_processes(many).len(), MAX_GPU_PROCESSES);
    }

    #[test]
    fn processes_reach_the_wire_sample() {
        let mut s = DeviceSample::new(Vendor::Nvidia, 0, Some("GPU-1".into()));
        assert!(s.to_gpu_sample("x").get("processes").is_none());
        s.processes = Some(vec![GpuProcess {
            pid: 7,
            used_memory_bytes: Some(1 << 30),
            name: Some("python3".into()),
        }]);
        let v = s.to_gpu_sample("x");
        assert_eq!(v["processes"][0]["pid"], 7);
        assert_eq!(v["processes"][0]["used_memory_bytes"], 1u64 << 30);
    }
}
