//! Hygon DCU plugin (DTK). `hy-smi` is a fork of `rocm-smi`: the JSON form is parsed with the
//! shared rocm-smi parser (`DCU use (%)` instead of `GPU use (%)`); when the installed `hy-smi`
//! rejects `--json`, the default summary table plus `--showmeminfo vram` text are parsed.
//!
//! Status: fixture-only, formats taken from DTK documentation and public logs (unverified).

use std::collections::BTreeMap;
use std::path::PathBuf;

use async_trait::async_trait;

use crate::amd::{parse_rocm_smi, run_json, ROCM_SMI_ARGS};
use crate::health::HealthBook;
use crate::types::{Device, DeviceHealth, DevicePlugin, DeviceSample, Vendor};
use crate::util::{parse_num, run, which, SMI_TIMEOUT};

pub struct HygonPlugin {
    tool: PathBuf,
    health: HealthBook,
}

impl HygonPlugin {
    pub fn detect_backend() -> Option<Self> {
        which("hy-smi").map(Self::with_tool)
    }

    pub fn with_tool(tool: PathBuf) -> Self {
        HygonPlugin {
            tool,
            health: HealthBook::default(),
        }
    }

    async fn query(&self) -> anyhow::Result<(Vec<Device>, Vec<DeviceSample>)> {
        if let Ok(v) = run_json(&self.tool, ROCM_SMI_ARGS).await {
            let parsed = parse_rocm_smi(&v, Vendor::Hygon);
            if !parsed.0.is_empty() {
                return Ok(parsed);
            }
        }
        let table = run(&self.tool, &[], SMI_TIMEOUT).await?;
        let mem = run(&self.tool, &["--showmeminfo", "vram"], SMI_TIMEOUT)
            .await
            .unwrap_or_default();
        Ok(parse_text(&table, &mem))
    }
}

#[async_trait]
impl DevicePlugin for HygonPlugin {
    fn vendor(&self) -> Vendor {
        Vendor::Hygon
    }

    fn backend(&self) -> String {
        "hy-smi".into()
    }

    async fn detect(&self) -> anyhow::Result<Vec<Device>> {
        let (mut d, _) = self.query().await?;
        self.health.apply(&mut d);
        Ok(d)
    }

    async fn sample(&self) -> anyhow::Result<Vec<DeviceSample>> {
        let (_, s) = self.query().await?;
        self.health.observe(&s);
        Ok(s)
    }
}

/// Parse `DCU[0] : vram Total Memory (B): 68702699520` lines into index → (key, value).
pub fn parse_indexed_lines(text: &str) -> BTreeMap<u32, BTreeMap<String, String>> {
    let mut out: BTreeMap<u32, BTreeMap<String, String>> = BTreeMap::new();
    for line in text.lines() {
        let t = line.trim();
        let Some(open) = t.find('[') else { continue };
        let Some(close) = t[open..].find(']').map(|c| c + open) else {
            continue;
        };
        let prefix = &t[..open];
        if !matches!(prefix, "DCU" | "HCU" | "GPU") {
            continue;
        }
        let Ok(idx) = t[open + 1..close].parse::<u32>() else {
            continue;
        };
        let rest = t[close + 1..].trim_start().trim_start_matches(':').trim();
        if let Some((k, v)) = rest.rsplit_once(':') {
            out.entry(idx)
                .or_default()
                .insert(k.trim().to_string(), v.trim().to_string());
        }
    }
    out
}

/// Parse the default `hy-smi` summary table (header `DCU Temp AvgPwr Perf PwrCap VRAM% DCU% Mode`)
/// plus `--showmeminfo vram` output.
pub fn parse_text(table: &str, meminfo: &str) -> (Vec<Device>, Vec<DeviceSample>) {
    let mem = parse_indexed_lines(meminfo);
    let mut header: Option<Vec<String>> = None;
    let mut devices = Vec::new();
    let mut samples = Vec::new();
    for line in table.lines() {
        let cols: Vec<&str> = line.split_whitespace().collect();
        if cols.is_empty() {
            continue;
        }
        if matches!(cols[0], "DCU" | "HCU" | "GPU") && cols.len() > 2 {
            header = Some(cols.iter().map(|c| c.to_string()).collect());
            continue;
        }
        let Some(h) = &header else { continue };
        let Ok(idx) = cols[0].parse::<u32>() else {
            continue;
        };
        let col = |name: &str| -> Option<&str> {
            h.iter()
                .position(|c| c.eq_ignore_ascii_case(name))
                .and_then(|i| cols.get(i).copied())
        };
        let m = mem.get(&idx);
        let total = m
            .and_then(|m| {
                m.iter()
                    .find(|(k, _)| k.to_ascii_lowercase().contains("total memory"))
                    .map(|(_, v)| v)
            })
            .and_then(|v| parse_num(v))
            .map(|v| v as u64);
        let used = m
            .and_then(|m| {
                m.iter()
                    .find(|(k, _)| k.to_ascii_lowercase().contains("used memory"))
                    .map(|(_, v)| v)
            })
            .and_then(|v| parse_num(v))
            .map(|v| v as u64);
        let mut d = Device::new(Vendor::Hygon, idx, "Hygon DCU", total.unwrap_or(0));
        d.max_power_watts = col("PwrCap").and_then(parse_num);
        d.health = DeviceHealth::Healthy;
        d.labels.insert("backend".into(), "hy-smi".into());
        devices.push(d);

        let mut s = DeviceSample::new(Vendor::Hygon, idx, None);
        s.temperature_c = col("Temp").and_then(parse_num);
        s.power_watts = col("AvgPwr").and_then(parse_num);
        s.power_limit_watts = col("PwrCap").and_then(parse_num);
        s.memory_util_pct = col("VRAM%").and_then(parse_num);
        s.utilization_pct = col("DCU%")
            .or_else(|| col("HCU%"))
            .or_else(|| col("GPU%"))
            .and_then(parse_num);
        s.memory_total_bytes = total;
        s.memory_used_bytes = used;
        samples.push(s);
    }
    (devices, samples)
}
