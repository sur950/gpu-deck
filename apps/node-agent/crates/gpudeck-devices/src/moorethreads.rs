//! Moore Threads plugin (MUSA). Parses `mthreads-gmi -q`, an `nvidia-smi -q` style report: one
//! `GPU <bus id>` block per device with indented sections (`Memory Usage`, `Utilization`,
//! `Temperature`, `Power Readings`, `Clocks`).
//!
//! Status: fixture-only, format from MUSA SDK documentation / public logs (unverified).

use std::collections::BTreeMap;
use std::path::PathBuf;

use async_trait::async_trait;

use crate::health::HealthBook;
use crate::types::{Device, DeviceHealth, DevicePlugin, DeviceSample, Vendor};
use crate::util::{parse_bytes, parse_num, run, which, SMI_TIMEOUT};

pub struct MooreThreadsPlugin {
    tool: PathBuf,
    health: HealthBook,
}

impl MooreThreadsPlugin {
    pub fn detect_backend() -> Option<Self> {
        which("mthreads-gmi").map(Self::with_tool)
    }

    pub fn with_tool(tool: PathBuf) -> Self {
        MooreThreadsPlugin {
            tool,
            health: HealthBook::default(),
        }
    }
}

#[async_trait]
impl DevicePlugin for MooreThreadsPlugin {
    fn vendor(&self) -> Vendor {
        Vendor::Moorethreads
    }

    fn backend(&self) -> String {
        "mthreads-gmi".into()
    }

    async fn detect(&self) -> anyhow::Result<Vec<Device>> {
        let out = run(&self.tool, &["-q"], SMI_TIMEOUT).await?;
        let (mut d, _) = parse_query(&out);
        self.health.apply(&mut d);
        Ok(d)
    }

    async fn sample(&self) -> anyhow::Result<Vec<DeviceSample>> {
        let out = run(&self.tool, &["-q"], SMI_TIMEOUT).await?;
        let (_, s) = parse_query(&out);
        self.health.observe(&s);
        Ok(s)
    }
}

/// One `GPU ...` block flattened to `"Section/Key" → value` (top-level keys have no section).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct QBlock {
    pub header: String,
    pub fields: BTreeMap<String, String>,
}

impl QBlock {
    pub fn get(&self, path: &str) -> Option<&str> {
        self.fields
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(path))
            .map(|(_, v)| v.as_str())
            .filter(|v| !v.is_empty() && !v.eq_ignore_ascii_case("n/a"))
    }
}

/// Split an `-q` report into the global key/values and per-GPU blocks.
pub fn parse_q_blocks(text: &str) -> (BTreeMap<String, String>, Vec<QBlock>) {
    let mut global = BTreeMap::new();
    let mut blocks: Vec<QBlock> = Vec::new();
    // (indent, name) of open sections inside the current GPU block.
    let mut stack: Vec<(usize, String)> = Vec::new();
    for line in text.lines() {
        if line.trim().is_empty() || line.trim_start().starts_with('=') {
            continue;
        }
        let indent = line.len() - line.trim_start().len();
        let t = line.trim();
        // `GPU 00000000:3B:00.0` opens a device block (`GPU Count : 2` is a plain key).
        if indent == 0 && t.starts_with("GPU ") && !t.contains(" : ") {
            blocks.push(QBlock {
                header: t[4..].trim().to_string(),
                fields: BTreeMap::new(),
            });
            stack.clear();
            continue;
        }
        let (key, value) = match t.split_once(" : ").or_else(|| t.split_once(':')) {
            Some((k, v)) => (k.trim().to_string(), Some(v.trim().to_string())),
            None => (t.to_string(), None),
        };
        while stack.last().is_some_and(|(i, _)| *i >= indent) {
            stack.pop();
        }
        match (value, blocks.last_mut()) {
            (Some(v), _) if v.is_empty() => stack.push((indent, key)),
            (None, _) => stack.push((indent, key)),
            (Some(v), Some(b)) => {
                let path = stack
                    .iter()
                    .map(|(_, n)| n.as_str())
                    .chain(std::iter::once(key.as_str()))
                    .collect::<Vec<_>>()
                    .join("/");
                b.fields.insert(path, v);
            }
            (Some(v), None) => {
                global.insert(key, v);
            }
        }
    }
    (global, blocks)
}

/// Parse `mthreads-gmi -q` into devices and samples.
pub fn parse_query(text: &str) -> (Vec<Device>, Vec<DeviceSample>) {
    let (global, blocks) = parse_q_blocks(text);
    let driver = global.get("Driver Version").cloned();
    let musa = global
        .get("MUSA Version")
        .or_else(|| global.get("MUSA Runtime Version"))
        .cloned();
    let mut devices = Vec::new();
    let mut samples = Vec::new();
    for (pos, b) in blocks.iter().enumerate() {
        let idx = b
            .get("Index")
            .or_else(|| b.get("Minor Number"))
            .and_then(parse_num)
            .map(|v| v as u32)
            .unwrap_or(pos as u32);
        let total = b
            .get("Memory Usage/Total")
            .or_else(|| b.get("FB Memory Usage/Total"))
            .and_then(|v| parse_bytes(v, 1 << 20));
        let used = b
            .get("Memory Usage/Used")
            .or_else(|| b.get("FB Memory Usage/Used"))
            .and_then(|v| parse_bytes(v, 1 << 20));
        let uuid = b
            .get("GPU UUID")
            .or_else(|| b.get("UUID"))
            .map(str::to_string);
        let mut d = Device::new(
            Vendor::Moorethreads,
            idx,
            b.get("Product Name").unwrap_or("Moore Threads GPU"),
            total.unwrap_or(0),
        );
        d.uuid = uuid.clone();
        d.driver_version = driver.clone();
        d.runtime_version = musa.clone();
        d.pci_bus_id = b
            .get("PCI/Bus Id")
            .map(str::to_string)
            .or_else(|| {
                if b.header.contains(':') {
                    Some(b.header.clone())
                } else {
                    None
                }
            })
            .map(|s| crate::nvidia::smi::normalize_bus_id(&s));
        d.max_power_watts = b
            .get("Power Readings/Max Power Limit")
            .or_else(|| b.get("Power Readings/Power Limit"))
            .and_then(parse_num);
        d.core_count = b
            .get("MUSA Cores")
            .or_else(|| b.get("Core Count"))
            .and_then(parse_num)
            .map(|v| v as u32);
        d.health = DeviceHealth::Healthy;
        d.labels.insert("backend".into(), "mthreads-gmi".into());
        devices.push(d);

        let mut s = DeviceSample::new(Vendor::Moorethreads, idx, uuid);
        s.utilization_pct = b
            .get("Utilization/Gpu")
            .or_else(|| b.get("Utilization/GPU"))
            .and_then(parse_num);
        s.memory_util_pct = b.get("Utilization/Memory").and_then(parse_num);
        s.memory_used_bytes = used;
        s.memory_total_bytes = total;
        s.temperature_c = b
            .get("Temperature/Gpu")
            .or_else(|| b.get("Temperature/GPU Current Temp"))
            .and_then(parse_num);
        s.power_watts = b.get("Power Readings/Power Draw").and_then(parse_num);
        s.power_limit_watts = b.get("Power Readings/Power Limit").and_then(parse_num);
        s.sm_clock_mhz = b
            .get("Clocks/Graphics")
            .and_then(parse_num)
            .map(|v| v as u32);
        s.memory_clock_mhz = b.get("Clocks/Memory").and_then(parse_num).map(|v| v as u32);
        s.fan_pct = b.get("Fan Speed").and_then(parse_num);
        s.ecc_errors_total = b
            .get("ECC Errors/Aggregate/Double Bit/Total")
            .or_else(|| b.get("ECC Errors/Uncorrectable"))
            .and_then(parse_num)
            .map(|v| v as u64);
        samples.push(s);
    }
    (devices, samples)
}
