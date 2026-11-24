//! AMD plugin: `amd-smi` JSON (ROCm >= 6.0), falling back to `rocm-smi --json` on older stacks.
//!
//! `amd-smi` output shapes handled: a top-level array of per-GPU objects (ROCm 6.0-6.3) and
//! `{"gpu_data": [...]}` (ROCm 6.4+). Values are either plain numbers/strings or
//! `{"value": n, "unit": "MB"}` objects; `"N/A"` is `None`.
//!
//! The `rocm-smi` card-JSON parser here is shared with the Hygon plugin (`hy-smi` is a fork).

use std::collections::HashMap;
use std::path::PathBuf;

use async_trait::async_trait;
use serde_json::Value;

use crate::health::HealthBook;
use crate::types::{Device, DeviceHealth, DevicePlugin, DeviceSample, Vendor};
use crate::util::{
    json_num, json_num_unit, json_path, parse_bytes, parse_num, run, to_bytes, which, SMI_TIMEOUT,
};

enum Backend {
    AmdSmi(PathBuf),
    RocmSmi(PathBuf),
}

pub struct AmdPlugin {
    backend: Backend,
    health: HealthBook,
}

impl AmdPlugin {
    pub fn detect_backend() -> Option<Self> {
        if let Some(p) = which("amd-smi") {
            return Some(Self::with_amd_smi(p));
        }
        which("rocm-smi").map(Self::with_rocm_smi)
    }

    pub fn with_amd_smi(tool: PathBuf) -> Self {
        AmdPlugin {
            backend: Backend::AmdSmi(tool),
            health: HealthBook::default(),
        }
    }

    pub fn with_rocm_smi(tool: PathBuf) -> Self {
        AmdPlugin {
            backend: Backend::RocmSmi(tool),
            health: HealthBook::default(),
        }
    }
}

#[async_trait]
impl DevicePlugin for AmdPlugin {
    fn vendor(&self) -> Vendor {
        Vendor::Amd
    }

    fn backend(&self) -> String {
        match self.backend {
            Backend::AmdSmi(_) => "amd-smi".into(),
            Backend::RocmSmi(_) => "rocm-smi".into(),
        }
    }

    async fn detect(&self) -> anyhow::Result<Vec<Device>> {
        let mut devices = match &self.backend {
            Backend::AmdSmi(tool) => {
                let st = run_json(tool, &["static", "--json"]).await?;
                let list = run_json(tool, &["list", "--json"])
                    .await
                    .unwrap_or(Value::Null);
                let ver = run_json(tool, &["version", "--json"])
                    .await
                    .unwrap_or(Value::Null);
                parse_amd_smi_static(&st, &list, &ver)
            }
            Backend::RocmSmi(tool) => {
                let v = run_json(tool, ROCM_SMI_ARGS).await?;
                parse_rocm_smi(&v, Vendor::Amd).0
            }
        };
        self.health.apply(&mut devices);
        Ok(devices)
    }

    async fn sample(&self) -> anyhow::Result<Vec<DeviceSample>> {
        let samples = match &self.backend {
            Backend::AmdSmi(tool) => {
                let m = run_json(tool, &["metric", "--json"]).await?;
                let list = run_json(tool, &["list", "--json"])
                    .await
                    .unwrap_or(Value::Null);
                let procs = run_json(tool, &["process", "--json"])
                    .await
                    .unwrap_or(Value::Null);
                let mut s = parse_amd_smi_metric(&m, &list);
                apply_process_counts(&mut s, &procs);
                s
            }
            Backend::RocmSmi(tool) => {
                let v = run_json(tool, ROCM_SMI_ARGS).await?;
                parse_rocm_smi(&v, Vendor::Amd).1
            }
        };
        // RAS uncorrectable growth (`ecc.total_uncorrectable_count`) is caught by the tracker.
        self.health.observe(&samples);
        Ok(samples)
    }
}

/// Arguments for the `rocm-smi` / `hy-smi` JSON query.
pub const ROCM_SMI_ARGS: &[&str] = &[
    "--showproductname",
    "--showuniqueid",
    "--showbus",
    "--showmeminfo",
    "vram",
    "--showuse",
    "--showmemuse",
    "--showpower",
    "--showmaxpower",
    "--showtemp",
    "--showclocks",
    "--showfan",
    "--showdriverversion",
    "--json",
];

pub async fn run_json(tool: &std::path::Path, args: &[&str]) -> anyhow::Result<Value> {
    let out = run(tool, args, SMI_TIMEOUT).await?;
    // Some versions print warnings before the JSON document.
    let start = out.find(['[', '{']).unwrap_or(0);
    Ok(serde_json::from_str(&out[start..])?)
}

/// Per-GPU objects from either amd-smi shape.
pub fn gpu_entries(v: &Value) -> Vec<&Value> {
    match v {
        Value::Array(a) => a.iter().collect(),
        Value::Object(o) => match o.get("gpu_data") {
            Some(Value::Array(a)) => a.iter().collect(),
            _ => vec![v],
        },
        _ => Vec::new(),
    }
}

fn gpu_index(v: &Value) -> Option<u32> {
    v.get("gpu")
        .and_then(|g| {
            g.as_u64()
                .or_else(|| g.as_str().and_then(|s| s.parse().ok()))
        })
        .map(|g| g as u32)
}

fn text(v: Option<&Value>) -> Option<String> {
    let s = match v? {
        Value::String(s) => s.trim().to_string(),
        Value::Number(n) => n.to_string(),
        _ => return None,
    };
    if s.is_empty() || s.eq_ignore_ascii_case("n/a") {
        None
    } else {
        Some(s)
    }
}

fn num_at(v: &Value, path: &[&str]) -> Option<f64> {
    json_path(v, path).and_then(json_num)
}

fn bytes_at(v: &Value, path: &[&str], default_unit: u64) -> Option<u64> {
    let (n, unit) = json_path(v, path).and_then(json_num_unit)?;
    Some(to_bytes(n, unit.as_deref(), default_unit))
}

/// `amd-smi list --json` → gpu index → uuid.
pub fn parse_amd_smi_list(list: &Value) -> HashMap<u32, String> {
    gpu_entries(list)
        .into_iter()
        .filter_map(|e| Some((gpu_index(e)?, text(e.get("uuid"))?)))
        .collect()
}

/// ROCm version from `amd-smi version --json` (array or object form).
pub fn parse_rocm_version(ver: &Value) -> Option<String> {
    let e = match ver {
        Value::Array(a) => a.first()?,
        other => other,
    };
    text(e.get("rocm_version"))
}

pub fn parse_amd_smi_static(st: &Value, list: &Value, ver: &Value) -> Vec<Device> {
    let uuids = parse_amd_smi_list(list);
    let rocm = parse_rocm_version(ver);
    gpu_entries(st)
        .into_iter()
        .filter_map(|e| {
            let idx = gpu_index(e)?;
            let name =
                text(json_path(e, &["asic", "market_name"])).unwrap_or_else(|| "AMD GPU".into());
            let total = bytes_at(e, &["vram", "size"], 1 << 20).unwrap_or(0);
            let mut d = Device::new(Vendor::Amd, idx, name, total);
            d.uuid = uuids
                .get(&idx)
                .cloned()
                .or_else(|| text(json_path(e, &["asic", "unique_id"])))
                .or_else(|| text(json_path(e, &["asic", "asic_serial"])));
            d.driver_version = text(json_path(e, &["driver", "version"]));
            d.runtime_version = rocm.clone();
            d.compute_capability = text(json_path(e, &["asic", "target_graphics_version"]));
            d.pci_bus_id = text(json_path(e, &["bus", "bdf"])).map(|s| s.to_ascii_lowercase());
            d.numa_node = num_at(e, &["numa", "node"]).map(|n| n as i32);
            d.max_power_watts = num_at(e, &["limit", "max_power"])
                .or_else(|| num_at(e, &["limit", "socket_power"]))
                .or_else(|| num_at(e, &["power", "power_cap"]));
            d.core_count = num_at(e, &["asic", "num_compute_units"]).map(|n| n as u32);
            d.health = DeviceHealth::Healthy;
            if let Some(p) = json_path(e, &["partition"]) {
                for k in ["compute_partition", "memory_partition"] {
                    if let Some(v) = text(p.get(k)) {
                        d.extra.insert(k.into(), Value::String(v));
                    }
                }
            }
            if let Some(t) = text(json_path(e, &["vram", "type"])) {
                d.extra.insert("vram_type".into(), Value::String(t));
            }
            d.labels.insert("backend".into(), "amd-smi".into());
            Some(d)
        })
        .collect()
}

pub fn parse_amd_smi_metric(m: &Value, list: &Value) -> Vec<DeviceSample> {
    let uuids = parse_amd_smi_list(list);
    gpu_entries(m)
        .into_iter()
        .filter_map(|e| {
            let idx = gpu_index(e)?;
            let mut s = DeviceSample::new(Vendor::Amd, idx, uuids.get(&idx).cloned());
            s.utilization_pct = num_at(e, &["usage", "gfx_activity"])
                .or_else(|| num_at(e, &["gfx", "gfx_activity"]));
            s.memory_util_pct = num_at(e, &["usage", "umc_activity"])
                .or_else(|| num_at(e, &["umc", "umc_activity"]));
            let mm =
                num_at(e, &["usage", "mm_activity"]).or_else(|| num_at(e, &["mm", "mm_activity"]));
            s.encoder_pct = mm;
            s.decoder_pct = mm;
            s.memory_used_bytes = bytes_at(e, &["mem_usage", "used_vram"], 1 << 20);
            s.memory_total_bytes = bytes_at(e, &["mem_usage", "total_vram"], 1 << 20);
            s.power_watts = num_at(e, &["power", "socket_power"])
                .or_else(|| num_at(e, &["power", "average_socket_power"]));
            s.power_limit_watts = num_at(e, &["power", "power_cap"]);
            let edge = num_at(e, &["temperature", "edge"]);
            let hotspot = num_at(e, &["temperature", "hotspot"]);
            s.temperature_c = edge.or(hotspot);
            s.memory_temperature_c = num_at(e, &["temperature", "mem"]);
            s.sm_clock_mhz = num_at(e, &["clock", "gfx_0", "clk"]).map(|v| v as u32);
            s.memory_clock_mhz = num_at(e, &["clock", "mem_0", "clk"]).map(|v| v as u32);
            s.fan_pct = match (num_at(e, &["fan", "speed"]), num_at(e, &["fan", "max"])) {
                (Some(sp), Some(max)) if max > 0.0 => Some(sp * 100.0 / max),
                _ => num_at(e, &["fan", "usage"]),
            };
            // `pcie.bandwidth` is total MB/s on recent amd-smi; split is not exposed.
            if let Some(bw) = num_at(e, &["pcie", "bandwidth"]) {
                let mut x = s.extra.take().unwrap_or_default();
                x.insert("pcie_bandwidth_mb_s".into(), serde_json::json!(bw));
                s.extra = Some(x);
            }
            s.ecc_errors_total = num_at(e, &["ecc", "total_uncorrectable_count"]).map(|v| v as u64);
            s.throttle_reasons = text(json_path(e, &["power", "throttle_status"])).map(|t| {
                if t.eq_ignore_ascii_case("UNTHROTTLED") {
                    Vec::new()
                } else {
                    t.split([',', '|'])
                        .map(|x| x.trim().to_ascii_lowercase())
                        .filter(|x| !x.is_empty())
                        .collect()
                }
            });
            if let Some(h) = hotspot {
                let mut x = s.extra.take().unwrap_or_default();
                x.insert("hotspot_temperature_c".into(), serde_json::json!(h));
                s.extra = Some(x);
            }
            Some(s)
        })
        .collect()
}

/// `amd-smi process --json` → number of processes per GPU.
pub fn apply_process_counts(samples: &mut [DeviceSample], procs: &Value) {
    let mut counts: HashMap<u32, u32> = HashMap::new();
    for e in gpu_entries(procs) {
        let Some(idx) = gpu_index(e) else { continue };
        let n = e
            .get("process_list")
            .and_then(|l| l.as_array())
            .map(|l| {
                l.iter()
                    .filter(|p| {
                        p.get("process_info")
                            .map(|pi| pi.is_object())
                            .unwrap_or(false)
                    })
                    .count() as u32
            })
            .unwrap_or(0);
        counts.insert(idx, n);
    }
    if counts.is_empty() {
        return;
    }
    for s in samples {
        s.process_count = Some(counts.get(&s.index).copied().unwrap_or(0));
    }
}

/// First key present (case-insensitive) among candidates in a rocm-smi card object.
fn card_get<'a>(card: &'a serde_json::Map<String, Value>, keys: &[&str]) -> Option<&'a str> {
    for k in keys {
        for (ck, v) in card {
            if ck.eq_ignore_ascii_case(k) {
                if let Some(s) = v.as_str() {
                    let t = s.trim();
                    if !t.is_empty() && !t.eq_ignore_ascii_case("n/a") {
                        return Some(t);
                    }
                }
            }
        }
    }
    None
}

fn card_num(card: &serde_json::Map<String, Value>, keys: &[&str]) -> Option<f64> {
    card_get(card, keys).and_then(parse_num)
}

/// Parse `rocm-smi --json` / `hy-smi --json` (`{"card0": {...}, "system": {...}}`) into devices and
/// samples. Hygon prints `DCU`/`HCU` where AMD prints `GPU`.
pub fn parse_rocm_smi(v: &Value, vendor: Vendor) -> (Vec<Device>, Vec<DeviceSample>) {
    let Some(obj) = v.as_object() else {
        return (Vec::new(), Vec::new());
    };
    let driver = obj
        .get("system")
        .and_then(|s| s.as_object())
        .and_then(|s| card_get(s, &["Driver version", "Driver Version"]).map(str::to_string));
    let mut cards: Vec<(u32, &serde_json::Map<String, Value>)> = obj
        .iter()
        .filter_map(|(k, v)| {
            let idx: u32 = k.strip_prefix("card")?.parse().ok()?;
            Some((idx, v.as_object()?))
        })
        .collect();
    cards.sort_by_key(|(i, _)| *i);
    let backend = if vendor == Vendor::Hygon {
        "hy-smi"
    } else {
        "rocm-smi"
    };
    let mut devices = Vec::new();
    let mut samples = Vec::new();
    for (idx, card) in cards {
        let name = card_get(
            card,
            &[
                "Card Series",
                "Card series",
                "Card Model",
                "Card model",
                "Device Name",
            ],
        )
        .unwrap_or(if vendor == Vendor::Hygon {
            "Hygon DCU"
        } else {
            "AMD GPU"
        })
        .to_string();
        let total = card_get(card, &["VRAM Total Memory (B)", "vram Total Memory (B)"])
            .and_then(|s| parse_bytes(s, 1))
            .unwrap_or(0);
        let used = card_get(
            card,
            &["VRAM Total Used Memory (B)", "vram Total Used Memory (B)"],
        )
        .and_then(|s| parse_bytes(s, 1));
        let uuid = card_get(card, &["Unique ID", "Unique Id", "UUID"]).map(str::to_string);
        let mut d = Device::new(vendor, idx, name, total);
        d.uuid = uuid.clone();
        d.driver_version = driver.clone();
        d.compute_capability = card_get(card, &["GFX Version", "gfx_version"]).map(str::to_string);
        d.pci_bus_id = card_get(card, &["PCI Bus", "PCI Bus ID"]).map(|s| s.to_ascii_lowercase());
        d.max_power_watts = card_num(
            card,
            &[
                "Max Graphics Package Power (W)",
                "Max Graphics Package Power(W)",
                "Power Cap (W)",
            ],
        );
        d.health = DeviceHealth::Healthy;
        d.labels.insert("backend".into(), backend.into());
        devices.push(d);

        let mut s = DeviceSample::new(vendor, idx, uuid);
        s.utilization_pct = card_num(card, &["GPU use (%)", "DCU use (%)", "HCU use (%)"]);
        s.memory_util_pct = card_num(
            card,
            &[
                "GPU Memory Allocated (VRAM%)",
                "GPU memory use (%)",
                "DCU memory use (%)",
                "HCU memory use (%)",
                "GPU Memory Read/Write Activity (%)",
            ],
        );
        s.memory_used_bytes = used;
        s.memory_total_bytes = if total > 0 { Some(total) } else { None };
        s.power_watts = card_num(
            card,
            &[
                "Current Socket Graphics Package Power (W)",
                "Average Graphics Package Power (W)",
                "Socket Graphics Package Power (W)",
            ],
        );
        s.power_limit_watts = card_num(card, &["Max Graphics Package Power (W)", "Power Cap (W)"]);
        s.temperature_c = card_num(
            card,
            &[
                "Temperature (Sensor edge) (C)",
                "Temperature (Sensor junction) (C)",
            ],
        );
        s.memory_temperature_c = card_num(
            card,
            &[
                "Temperature (Sensor memory) (C)",
                "Temperature (Sensor HBM 0) (C)",
            ],
        );
        s.sm_clock_mhz =
            card_num(card, &["sclk clock speed:", "sclk clock speed"]).map(|v| v as u32);
        s.memory_clock_mhz =
            card_num(card, &["mclk clock speed:", "mclk clock speed"]).map(|v| v as u32);
        s.fan_pct = card_num(card, &["Fan speed (%)"]);
        samples.push(s);
    }
    (devices, samples)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn entries_both_shapes() {
        let a = serde_json::json!([{"gpu": 0}, {"gpu": 1}]);
        let b = serde_json::json!({"gpu_data": [{"gpu": 0}]});
        assert_eq!(gpu_entries(&a).len(), 2);
        assert_eq!(gpu_entries(&b).len(), 1);
    }

    #[test]
    fn process_counts() {
        let procs = serde_json::json!([
            {"gpu": 0, "process_list": [{"process_info": {"name": "python3", "pid": 1}}, {"process_info": {"name": "x", "pid": 2}}]},
            {"gpu": 1, "process_list": [{"process_info": "No running processes detected"}]}
        ]);
        let mut s = vec![
            DeviceSample::new(Vendor::Amd, 0, None),
            DeviceSample::new(Vendor::Amd, 1, None),
        ];
        apply_process_counts(&mut s, &procs);
        assert_eq!(s[0].process_count, Some(2));
        assert_eq!(s[1].process_count, Some(0));
    }
}
