//! Apple Silicon plugin (real on this Mac).
//!
//! | Field | Source (no root needed) |
//! | --- | --- |
//! | name, VRAM | Metal `MTLCreateSystemDefaultDevice()`: `name`, `recommendedMaxWorkingSetSize` (the wired limit the GPU may use; reported as `vram_total_bytes`, `is_unified_memory = true`) |
//! | usable fraction | `recommendedMaxWorkingSetSize / hw.memsize` → `extra.usable_memory_fraction` |
//! | GPU cores, chip | IOKit `IOAccelerator` (`AGXAccelerator*`) properties `gpu-core-count`, `model` |
//! | runtime / compute capability | Metal family support → `Metal 4` / `metal4` (else 3) |
//! | driver version | macOS product version + build (`kern.osproductversion`, `kern.osversion`) |
//! | uuid | `apple-<chip>-<IOPlatformSerialNumber>` |
//! | utilization, memory used | `IOAccelerator` → `PerformanceStatistics["Device Utilization %"]`, `["In use system memory"]` |
//! | power | IOReport `Energy Model` / `GPU Energy` delta between samples (dlopen of `libIOReport.dylib`) |
//! | temperature | SMC `Tg*` float keys via the `AppleSMC` user client (mean of valid sensors), best effort |
//!
//! When the IOKit FFI is unavailable the plugin falls back to `ioreg` / `system_profiler` text,
//! parsed by the pure functions in this module (also used by the fixture tests).

#[cfg(target_os = "macos")]
mod cf;
#[cfg(target_os = "macos")]
mod iokit;
#[cfg(target_os = "macos")]
mod ioreport;
#[cfg(target_os = "macos")]
mod metal;
#[cfg(target_os = "macos")]
mod smc;

use async_trait::async_trait;
use serde_json::Value;

use crate::health::HealthBook;
use crate::types::{Device, DeviceHealth, DevicePlugin, DeviceSample, Vendor};

/// Static facts about the Mac's GPU, gathered once per `detect()`.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct AppleGpuInfo {
    /// Metal device name (`Apple M5`).
    pub name: Option<String>,
    /// IOKit `model` / sysctl `machdep.cpu.brand_string`.
    pub chip: Option<String>,
    pub gpu_cores: Option<u32>,
    pub recommended_max_working_set: Option<u64>,
    pub memsize: Option<u64>,
    pub metal_family: Option<u8>,
    pub os_version: Option<String>,
    pub os_build: Option<String>,
    pub hw_model: Option<String>,
    pub serial: Option<String>,
    pub wired_limit_mb: Option<u64>,
    pub metal_ok: bool,
}

/// `PerformanceStatistics` values used for samples.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct AccelStats {
    pub device_utilization_pct: Option<f64>,
    pub renderer_utilization_pct: Option<f64>,
    pub tiler_utilization_pct: Option<f64>,
    pub in_use_system_memory: Option<u64>,
    pub alloc_system_memory: Option<u64>,
}

impl AccelStats {
    /// From the `PerformanceStatistics` dictionary (as JSON).
    pub fn from_json(v: &Value) -> Self {
        let n = |k: &str| v.get(k).and_then(|x| x.as_f64());
        AccelStats {
            device_utilization_pct: n("Device Utilization %").or_else(|| n("GPU Activity(%)")),
            renderer_utilization_pct: n("Renderer Utilization %"),
            tiler_utilization_pct: n("Tiler Utilization %"),
            in_use_system_memory: n("In use system memory").map(|x| x as u64),
            alloc_system_memory: n("Alloc system memory").map(|x| x as u64),
        }
    }
}

/// Lower-case chip slug for the UUID: `Apple M3 Max` → `m3-max`.
pub fn chip_slug(chip: &str) -> String {
    let lower = chip.trim().to_ascii_lowercase();
    let t = lower.strip_prefix("apple").unwrap_or(&lower).trim();
    t.split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("-")
}

/// Build the inventory device from gathered facts.
pub fn build_device(info: &AppleGpuInfo) -> Device {
    let name = info
        .name
        .clone()
        .or_else(|| info.chip.clone())
        .unwrap_or_else(|| "Apple GPU".into());
    let vram = info
        .recommended_max_working_set
        .or(info.memsize)
        .unwrap_or(0);
    let mut d = Device::new(Vendor::Apple, 0, name.clone(), vram);
    d.is_unified_memory = true;
    let chip = info.chip.clone().unwrap_or(name);
    d.uuid = info
        .serial
        .as_ref()
        .map(|s| format!("apple-{}-{}", chip_slug(&chip), s.to_ascii_lowercase()));
    d.driver_version = match (&info.os_version, &info.os_build) {
        (Some(v), Some(b)) => Some(format!("macOS {v} ({b})")),
        (Some(v), None) => Some(format!("macOS {v}")),
        _ => None,
    };
    let fam = info.metal_family.unwrap_or(3);
    d.runtime_version = Some(format!("Metal {fam}"));
    d.compute_capability = Some(format!("metal{fam}"));
    d.core_count = info.gpu_cores;
    d.health = if info.metal_ok {
        DeviceHealth::Healthy
    } else {
        DeviceHealth::Unhealthy
    };
    if !info.metal_ok {
        d.health_reason = Some("MTLCreateSystemDefaultDevice returned nil".into());
    }
    d.labels.insert("backend".into(), "iokit".into());
    d.labels.insert("chip".into(), chip);
    if let Some(m) = &info.hw_model {
        d.labels.insert("hw_model".into(), m.clone());
    }
    if let Some(mem) = info.memsize {
        d.extra
            .insert("unified_memory_bytes".into(), serde_json::json!(mem));
        if let Some(rec) = info.recommended_max_working_set {
            if mem > 0 {
                let frac = rec as f64 / mem as f64;
                d.extra.insert(
                    "usable_memory_fraction".into(),
                    serde_json::json!((frac * 1000.0).round() / 1000.0),
                );
            }
        }
    }
    if let Some(w) = info.wired_limit_mb {
        d.extra
            .insert("iogpu_wired_limit_mb".into(), serde_json::json!(w));
    }
    d
}

/// Build a sample from accelerator stats, IOReport power and SMC temperature.
pub fn build_sample(
    uuid: Option<String>,
    vram_total: u64,
    stats: &AccelStats,
    power_w: Option<f64>,
    temp_c: Option<f64>,
) -> DeviceSample {
    let mut s = DeviceSample::new(Vendor::Apple, 0, uuid);
    s.utilization_pct = stats.device_utilization_pct;
    s.memory_used_bytes = stats.in_use_system_memory.or(stats.alloc_system_memory);
    s.memory_total_bytes = if vram_total > 0 {
        Some(vram_total)
    } else {
        None
    };
    s.power_watts = power_w;
    s.temperature_c = temp_c;
    let mut extra = serde_json::Map::new();
    if let Some(v) = stats.renderer_utilization_pct {
        extra.insert("renderer_utilization_pct".into(), serde_json::json!(v));
    }
    if let Some(v) = stats.tiler_utilization_pct {
        extra.insert("tiler_utilization_pct".into(), serde_json::json!(v));
    }
    if let Some(v) = stats.alloc_system_memory {
        extra.insert("alloc_system_memory_bytes".into(), serde_json::json!(v));
    }
    if !extra.is_empty() {
        s.extra = Some(extra);
    }
    s
}

/// Parse `ioreg -r -c IOAccelerator -l -w0` text: `PerformanceStatistics`, `gpu-core-count`, `model`.
pub fn parse_ioreg_accelerator(text: &str) -> (AccelStats, Option<u32>, Option<String>) {
    let mut stats = AccelStats::default();
    let mut cores = None;
    let mut model = None;
    for line in text.lines() {
        let t = line.trim_start_matches([' ', '|']).trim();
        if let Some(rest) = t.strip_prefix("\"PerformanceStatistics\" = {") {
            let body = rest.trim_end_matches('}');
            let mut map = serde_json::Map::new();
            for kv in body.split(',') {
                if let Some((k, v)) = kv.split_once('=') {
                    let k = k.trim().trim_matches('"');
                    if let Ok(n) = v.trim().parse::<f64>() {
                        map.insert(k.to_string(), serde_json::json!(n));
                    }
                }
            }
            stats = AccelStats::from_json(&Value::Object(map));
        } else if let Some(rest) = t.strip_prefix("\"gpu-core-count\" = ") {
            cores = rest.trim().parse().ok();
        } else if let Some(rest) = t.strip_prefix("\"model\" = ") {
            let m = rest
                .trim()
                .trim_matches(|c| c == '"' || c == '<' || c == '>');
            if !m.is_empty() {
                model = Some(m.to_string());
            }
        }
    }
    (stats, cores, model)
}

/// Parse `system_profiler SPDisplaysDataType -json`: (model, cores, metal family).
pub fn parse_system_profiler(v: &Value) -> Option<(String, Option<u32>, Option<u8>)> {
    let gpus = v.get("SPDisplaysDataType")?.as_array()?;
    let g = gpus
        .iter()
        .find(|g| g.get("sppci_device_type").and_then(|t| t.as_str()) == Some("spdisplays_gpu"))
        .or(gpus.first())?;
    let model = g
        .get("sppci_model")
        .or_else(|| g.get("_name"))
        .and_then(|m| m.as_str())?
        .to_string();
    let cores = g.get("sppci_cores").and_then(|c| {
        c.as_str()
            .and_then(|s| s.parse().ok())
            .or_else(|| c.as_u64().map(|n| n as u32))
    });
    let fam = g
        .get("spdisplays_mtlgpufamilysupport")
        .and_then(|f| f.as_str())
        .and_then(|f| f.trim_start_matches("spdisplays_metal").parse().ok());
    Some((model, cores, fam))
}

/// Apple Silicon plugin.
pub struct ApplePlugin {
    health: HealthBook,
    #[cfg(target_os = "macos")]
    state: std::sync::Mutex<MacState>,
}

#[cfg(target_os = "macos")]
struct MacState {
    ioreport: Option<ioreport::IoReport>,
    smc: Option<smc::Smc>,
    uuid: Option<String>,
    vram_total: u64,
}

impl ApplePlugin {
    /// Available on Apple Silicon Macs (an `IOAccelerator` with an AGX driver or a Metal device).
    pub fn detect_backend() -> Option<Self> {
        #[cfg(target_os = "macos")]
        {
            if std::env::consts::ARCH != "aarch64" {
                return None;
            }
            let info = gather_info();
            if !info.metal_ok && info.gpu_cores.is_none() {
                return None;
            }
            let dev = build_device(&info);
            Some(ApplePlugin {
                health: HealthBook::default(),
                state: std::sync::Mutex::new(MacState {
                    ioreport: ioreport::IoReport::new(),
                    smc: smc::Smc::open(),
                    uuid: dev.uuid.clone(),
                    vram_total: dev.vram_total_bytes,
                }),
            })
        }
        #[cfg(not(target_os = "macos"))]
        {
            None
        }
    }
}

/// Gather static facts from Metal, IOKit and sysctl.
#[cfg(target_os = "macos")]
pub fn gather_info() -> AppleGpuInfo {
    let mut info = AppleGpuInfo::default();
    if let Some(m) = metal::default_device_info() {
        info.name = Some(m.name);
        info.recommended_max_working_set = Some(m.recommended_max_working_set);
        info.metal_family = Some(m.metal_family);
        info.metal_ok = true;
    }
    if let Some(acc) = iokit::accelerator() {
        info.gpu_cores = acc.gpu_cores;
        info.chip = acc.model;
    }
    if info.chip.is_none() {
        info.chip = iokit::sysctl_string("machdep.cpu.brand_string");
    }
    info.memsize = iokit::sysctl_u64("hw.memsize");
    info.os_version = iokit::sysctl_string("kern.osproductversion");
    info.os_build = iokit::sysctl_string("kern.osversion");
    info.hw_model = iokit::sysctl_string("hw.model");
    info.wired_limit_mb = iokit::sysctl_u64("iogpu.wired_limit_mb").filter(|v| *v > 0);
    info.serial = iokit::platform_serial();
    if info.gpu_cores.is_none() {
        // Fallback: system_profiler (slow, ~0.3 s; detect-time only).
        if let Ok(out) = std::process::Command::new("system_profiler")
            .args(["SPDisplaysDataType", "-json"])
            .output()
        {
            if let Ok(v) = serde_json::from_slice::<Value>(&out.stdout) {
                if let Some((model, cores, fam)) = parse_system_profiler(&v) {
                    info.gpu_cores = cores;
                    info.chip.get_or_insert(model);
                    if info.metal_family.is_none() {
                        info.metal_family = fam;
                    }
                }
            }
        }
    }
    info
}

#[async_trait]
impl DevicePlugin for ApplePlugin {
    fn vendor(&self) -> Vendor {
        Vendor::Apple
    }

    fn backend(&self) -> String {
        "iokit".into()
    }

    async fn detect(&self) -> anyhow::Result<Vec<Device>> {
        #[cfg(target_os = "macos")]
        {
            let info = gather_info();
            let mut d = build_device(&info);
            if let Ok(mut st) = self.state.lock() {
                st.uuid = d.uuid.clone();
                st.vram_total = d.vram_total_bytes;
            }
            let mut v = vec![d.clone()];
            self.health.apply(&mut v);
            // Metal failure is authoritative regardless of sample history.
            if !info.metal_ok {
                d.health = DeviceHealth::Unhealthy;
                v[0] = d;
            }
            Ok(v)
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = &self.health;
            Ok(Vec::new())
        }
    }

    async fn sample(&self) -> anyhow::Result<Vec<DeviceSample>> {
        #[cfg(target_os = "macos")]
        {
            let stats = iokit::accelerator().map(|a| a.stats).unwrap_or_default();
            let mut st = self
                .state
                .lock()
                .map_err(|_| anyhow::anyhow!("apple plugin state poisoned"))?;
            let power = st.ioreport.as_mut().and_then(|r| r.gpu_power_watts());
            let temp = st.smc.as_mut().and_then(|s| s.gpu_temperature_c());
            let s = build_sample(st.uuid.clone(), st.vram_total, &stats, power, temp);
            drop(st);
            let out = vec![s];
            self.health.observe(&out);
            Ok(out)
        }
        #[cfg(not(target_os = "macos"))]
        {
            Ok(Vec::new())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slug() {
        assert_eq!(chip_slug("Apple M5"), "m5");
        assert_eq!(chip_slug("Apple M3 Max"), "m3-max");
    }

    #[test]
    fn device_from_info() {
        let info = AppleGpuInfo {
            name: Some("Apple M5".into()),
            chip: Some("Apple M5".into()),
            gpu_cores: Some(10),
            recommended_max_working_set: Some(28_102_000_000),
            memsize: Some(34_359_738_368),
            metal_family: Some(4),
            os_version: Some("27.2".into()),
            os_build: Some("26B123".into()),
            hw_model: Some("Mac17,2".into()),
            serial: Some("ABC123".into()),
            wired_limit_mb: None,
            metal_ok: true,
        };
        let d = build_device(&info);
        assert_eq!(d.uuid.as_deref(), Some("apple-m5-abc123"));
        assert!(d.is_unified_memory);
        assert_eq!(d.vram_total_bytes, 28_102_000_000);
        assert_eq!(d.compute_capability.as_deref(), Some("metal4"));
        assert_eq!(d.runtime_version.as_deref(), Some("Metal 4"));
        assert_eq!(d.driver_version.as_deref(), Some("macOS 27.2 (26B123)"));
        assert_eq!(d.core_count, Some(10));
        assert_eq!(d.health, DeviceHealth::Healthy);
        assert_eq!(d.extra["usable_memory_fraction"], serde_json::json!(0.818));
    }
}
