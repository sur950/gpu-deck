//! `nvidia-smi --query-gpu=... --format=csv,noheader,nounits` backend (fallback when NVML cannot
//! be loaded, e.g. inside minimal containers). The same CSV dialect is spoken by T-Head's
//! `ppu-smi`, which reuses this parser.

use std::collections::BTreeMap;
use std::path::Path;

use anyhow::{anyhow, Context};

use crate::types::{Device, DeviceHealth, DeviceSample, Vendor};
use crate::util::{parse_bytes, parse_num, run, SMI_TIMEOUT};

/// Full field list (driver >= 510). Units with `nounits`: MiB, W, MHz, C, %.
pub const QUERY_FIELDS: &[&str] = &[
    "index",
    "uuid",
    "name",
    "pci.bus_id",
    "driver_version",
    "compute_cap",
    "memory.total",
    "memory.used",
    "utilization.gpu",
    "utilization.memory",
    "power.draw",
    "power.limit",
    "power.max_limit",
    "temperature.gpu",
    "temperature.memory",
    "clocks.sm",
    "clocks.mem",
    "fan.speed",
    "ecc.errors.uncorrected.aggregate.total",
    "clocks_throttle_reasons.active",
];

/// Conservative field list understood by old drivers; used when the full query is rejected
/// (`Field "compute_cap" is not a valid field to query.`).
pub const MINIMAL_FIELDS: &[&str] = &[
    "index",
    "uuid",
    "name",
    "pci.bus_id",
    "driver_version",
    "memory.total",
    "memory.used",
    "utilization.gpu",
    "utilization.memory",
    "power.draw",
    "power.limit",
    "temperature.gpu",
    "clocks.sm",
    "clocks.mem",
    "fan.speed",
];

/// One CSV row keyed by query field name.
pub type Row = BTreeMap<String, String>;

/// Parse `csv,noheader,nounits` output for `fields`. Rows with the wrong column count are skipped.
pub fn parse_csv(text: &str, fields: &[&str]) -> Vec<Row> {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .filter_map(|line| {
            let cols: Vec<&str> = line.split(',').map(str::trim).collect();
            if cols.len() != fields.len() {
                return None;
            }
            Some(
                fields
                    .iter()
                    .zip(cols)
                    .map(|(f, v)| (f.to_string(), v.to_string()))
                    .collect(),
            )
        })
        .collect()
}

/// NVML clock-event / throttle bitmask → names used in `throttle_reasons` (`metrics.md` §1).
/// `gpu_idle` is not a throttle and is dropped.
pub fn throttle_names(mask: u64) -> Vec<String> {
    const BITS: &[(u64, &str)] = &[
        (0x2, "applications_clocks"),
        (0x4, "power_cap"),
        (0x8, "hw_slowdown"),
        (0x10, "sync_boost"),
        (0x20, "sw_thermal"),
        (0x40, "hw_thermal"),
        (0x80, "hw_power_brake"),
        (0x100, "display_clocks"),
    ];
    BITS.iter()
        .filter(|(b, _)| mask & b != 0)
        .map(|(_, n)| n.to_string())
        .collect()
}

fn parse_hex_mask(s: &str) -> Option<u64> {
    let t = s.trim();
    let t = t.strip_prefix("0x").or_else(|| t.strip_prefix("0X"))?;
    u64::from_str_radix(t, 16).ok()
}

fn get<'a>(row: &'a Row, key: &str) -> Option<&'a str> {
    row.get(key)
        .map(|s| s.as_str())
        .filter(|s| parse_text(s).is_some())
}

/// Text value with SMI placeholders (`[N/A]`, `[Not Supported]`) mapped to `None`.
pub fn parse_text(s: &str) -> Option<String> {
    let t = s.trim();
    let l = t.to_ascii_lowercase();
    if t.is_empty() || l.contains("n/a") || l.contains("not supported") || l.contains("unknown") {
        None
    } else {
        Some(t.to_string())
    }
}

fn num(row: &Row, key: &str) -> Option<f64> {
    row.get(key).and_then(|v| parse_num(v))
}

/// Inventory device from one row.
pub fn row_to_device(vendor: Vendor, row: &Row, runtime_version: Option<&str>) -> Option<Device> {
    let index = num(row, "index")? as u32;
    let name = get(row, "name").unwrap_or("unknown").to_string();
    let total = row
        .get("memory.total")
        .and_then(|v| parse_bytes(v, 1 << 20))
        .unwrap_or(0);
    let mut d = Device::new(vendor, index, name, total);
    d.uuid = get(row, "uuid").map(str::to_string);
    d.driver_version = get(row, "driver_version").map(str::to_string);
    d.runtime_version = runtime_version.map(str::to_string);
    d.compute_capability = get(row, "compute_cap").map(str::to_string);
    d.pci_bus_id = get(row, "pci.bus_id").map(normalize_bus_id);
    d.max_power_watts = num(row, "power.max_limit").or_else(|| num(row, "power.limit"));
    d.health = DeviceHealth::Healthy;
    Some(d)
}

/// `00000000:3B:00.0` → `0000:3b:00.0` (domain trimmed to 4 digits, lower case, like sysfs).
pub fn normalize_bus_id(s: &str) -> String {
    let lower = s.trim().to_ascii_lowercase();
    match lower.split_once(':') {
        Some((dom, rest)) if dom.len() > 4 => format!("{}:{rest}", &dom[dom.len() - 4..]),
        _ => lower,
    }
}

/// Metric sample from one row.
pub fn row_to_sample(vendor: Vendor, row: &Row) -> Option<DeviceSample> {
    let index = num(row, "index")? as u32;
    let mut s = DeviceSample::new(vendor, index, get(row, "uuid").map(str::to_string));
    s.utilization_pct = num(row, "utilization.gpu");
    s.memory_util_pct = num(row, "utilization.memory");
    s.memory_used_bytes = row.get("memory.used").and_then(|v| parse_bytes(v, 1 << 20));
    s.memory_total_bytes = row
        .get("memory.total")
        .and_then(|v| parse_bytes(v, 1 << 20));
    s.power_watts = num(row, "power.draw");
    s.power_limit_watts = num(row, "power.limit");
    s.temperature_c = num(row, "temperature.gpu");
    s.memory_temperature_c = num(row, "temperature.memory");
    s.sm_clock_mhz = num(row, "clocks.sm").map(|v| v as u32);
    s.memory_clock_mhz = num(row, "clocks.mem").map(|v| v as u32);
    s.fan_pct = num(row, "fan.speed");
    s.ecc_errors_total = num(row, "ecc.errors.uncorrected.aggregate.total").map(|v| v as u64);
    s.throttle_reasons = row
        .get("clocks_throttle_reasons.active")
        .and_then(|v| parse_hex_mask(v))
        .map(throttle_names);
    Some(s)
}

/// `CUDA Version: 12.4` from the plain `nvidia-smi` banner.
pub fn parse_cuda_version(banner: &str) -> Option<String> {
    let idx = banner.find("CUDA Version")?;
    let rest = &banner[idx + "CUDA Version".len()..];
    let rest = rest.trim_start_matches([':', ' ']);
    let v: String = rest
        .chars()
        .take_while(|c| c.is_ascii_digit() || *c == '.')
        .collect();
    if v.is_empty() {
        None
    } else {
        Some(v)
    }
}

/// Run the query with the full field list, falling back to the minimal list on old drivers.
pub async fn query(tool: &Path) -> anyhow::Result<(Vec<Row>, &'static [&'static str])> {
    for fields in [QUERY_FIELDS, MINIMAL_FIELDS] {
        let arg = format!("--query-gpu={}", fields.join(","));
        match run(tool, &[&arg, "--format=csv,noheader,nounits"], SMI_TIMEOUT).await {
            Ok(out) => return Ok((parse_csv(&out, fields), fields)),
            Err(e) if e.to_string().contains("not a valid field") => continue,
            Err(e) => return Err(e).with_context(|| format!("{} query failed", tool.display())),
        }
    }
    Err(anyhow!("{} rejected every field list", tool.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn throttle_mask() {
        assert_eq!(throttle_names(0x1), Vec::<String>::new());
        assert_eq!(throttle_names(0x24), vec!["power_cap", "sw_thermal"]);
        assert_eq!(parse_hex_mask("0x0000000000000004"), Some(4));
    }

    #[test]
    fn bus_id() {
        assert_eq!(normalize_bus_id("00000000:3B:00.0"), "0000:3b:00.0");
        assert_eq!(normalize_bus_id("0000:3b:00.0"), "0000:3b:00.0");
    }

    #[test]
    fn cuda_banner() {
        let b = "| NVIDIA-SMI 550.54.15   Driver Version: 550.54.15   CUDA Version: 12.4     |";
        assert_eq!(parse_cuda_version(b).as_deref(), Some("12.4"));
        assert_eq!(parse_cuda_version("nothing"), None);
    }
}
