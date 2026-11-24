//! MetaX plugin (MACA). Parses the default `mx-smi` summary table, which mirrors `nvidia-smi`:
//! two table rows per GPU (`index name | bus-id | util` then `temp power/cap | used/total MiB`),
//! with the KMD and MACA versions in the banner.
//!
//! Status: fixture-only, format from MetaX documentation / public logs (unverified).

use std::path::PathBuf;

use async_trait::async_trait;

use crate::health::HealthBook;
use crate::types::{Device, DeviceHealth, DevicePlugin, DeviceSample, Vendor};
use crate::util::{parse_bytes, parse_num, run, which, SMI_TIMEOUT};

pub struct MetaxPlugin {
    tool: PathBuf,
    health: HealthBook,
}

impl MetaxPlugin {
    pub fn detect_backend() -> Option<Self> {
        which("mx-smi").map(Self::with_tool)
    }

    pub fn with_tool(tool: PathBuf) -> Self {
        MetaxPlugin {
            tool,
            health: HealthBook::default(),
        }
    }
}

#[async_trait]
impl DevicePlugin for MetaxPlugin {
    fn vendor(&self) -> Vendor {
        Vendor::Metax
    }

    fn backend(&self) -> String {
        "mx-smi".into()
    }

    async fn detect(&self) -> anyhow::Result<Vec<Device>> {
        let out = run(&self.tool, &[], SMI_TIMEOUT).await?;
        let (mut d, _) = parse(&out);
        self.health.apply(&mut d);
        Ok(d)
    }

    async fn sample(&self) -> anyhow::Result<Vec<DeviceSample>> {
        let out = run(&self.tool, &[], SMI_TIMEOUT).await?;
        let (_, s) = parse(&out);
        self.health.observe(&s);
        Ok(s)
    }
}

fn banner_value(text: &str, key: &str) -> Option<String> {
    let i = text.find(key)?;
    let rest = text[i + key.len()..].trim_start_matches([':', ' ']);
    let v: String = rest
        .chars()
        .take_while(|c| !c.is_whitespace() && *c != '|')
        .collect();
    if v.is_empty() {
        None
    } else {
        Some(v)
    }
}

/// Parse `mx-smi` output into devices and samples.
pub fn parse(text: &str) -> (Vec<Device>, Vec<DeviceSample>) {
    let driver = banner_value(text, "Kernel Mode Driver Version");
    let maca = banner_value(text, "MACA Version");
    let rows: Vec<Vec<String>> = text
        .lines()
        .map(str::trim)
        .filter(|l| l.starts_with('|') && !l.starts_with("|=") && !l.starts_with("|-"))
        .map(|l| {
            l.trim_matches('|')
                .split('|')
                .map(|c| c.trim().to_string())
                .collect()
        })
        .collect();
    let mut devices = Vec::new();
    let mut samples = Vec::new();
    let mut i = 0;
    while i < rows.len() {
        let first = &rows[i];
        let mut parts = first
            .first()
            .map(|c| c.split_whitespace())
            .into_iter()
            .flatten();
        let idx = parts.next().and_then(|t| t.parse::<u32>().ok());
        let (Some(idx), true) = (idx, first.len() >= 3) else {
            i += 1;
            continue;
        };
        let name: String = first[0]
            .split_whitespace()
            .skip(1)
            .collect::<Vec<_>>()
            .join(" ");
        let bus = first.get(1).cloned().filter(|b| b.contains(':'));
        let util = first.get(2).and_then(|u| parse_num(u));
        let second = rows.get(i + 1).cloned().unwrap_or_default();
        let (temp, power, cap) = second
            .first()
            .map(|c| {
                let toks: Vec<&str> = c.split_whitespace().collect();
                let temp = toks.first().and_then(|t| parse_num(t));
                let power = toks.get(1).and_then(|t| parse_num(t));
                let cap = c.split_once('/').and_then(|(_, r)| parse_num(r.trim()));
                (temp, power, cap)
            })
            .unwrap_or((None, None, None));
        let (used, total) = second
            .get(1)
            .and_then(|m| {
                let (u, t) = m.split_once('/')?;
                let unit_bytes = if t.to_ascii_lowercase().contains("gib") {
                    1u64 << 30
                } else {
                    1 << 20
                };
                Some((
                    parse_bytes(u.trim(), unit_bytes),
                    parse_bytes(t.trim(), unit_bytes),
                ))
            })
            .unwrap_or((None, None));

        let mut d = Device::new(
            Vendor::Metax,
            idx,
            if name.is_empty() {
                "MetaX GPU".into()
            } else {
                name
            },
            total.unwrap_or(0),
        );
        d.driver_version = driver.clone();
        d.runtime_version = maca.clone();
        d.pci_bus_id = bus.map(|b| b.to_ascii_lowercase());
        d.max_power_watts = cap;
        d.health = DeviceHealth::Healthy;
        d.labels.insert("backend".into(), "mx-smi".into());
        devices.push(d);

        let mut s = DeviceSample::new(Vendor::Metax, idx, None);
        s.utilization_pct = util;
        s.temperature_c = temp;
        s.power_watts = power;
        s.power_limit_watts = cap;
        s.memory_used_bytes = used;
        s.memory_total_bytes = total;
        if let (Some(u), Some(t)) = (used, total) {
            if t > 0 {
                s.extra = Some(serde_json::Map::from_iter([(
                    "memory_used_pct".to_string(),
                    serde_json::json!(u as f64 * 100.0 / t as f64),
                )]));
            }
        }
        samples.push(s);
        i += 2;
    }
    (devices, samples)
}
