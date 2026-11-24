//! T-Head PPU plugin. The PPU SDK ships an `nvidia-smi`-compatible management CLI; GPU Deck queries
//! it with the same `--query-gpu=... --format=csv,noheader,nounits` dialect as NVIDIA and reuses
//! that parser. The tool name defaults to `ppu-smi`; override the path with `GPUDECK_PPU_SMI_PATH`.
//!
//! Status: fixture-only; tool name and field support are unverified until hardware is available.

use std::path::PathBuf;

use async_trait::async_trait;

use crate::health::HealthBook;
use crate::nvidia::smi;
use crate::types::{Device, DevicePlugin, DeviceSample, Vendor};
use crate::util::{run, which, SMI_TIMEOUT};

pub struct TheadPlugin {
    tool: PathBuf,
    health: HealthBook,
}

impl TheadPlugin {
    pub fn detect_backend() -> Option<Self> {
        which("ppu-smi").map(Self::with_tool)
    }

    pub fn with_tool(tool: PathBuf) -> Self {
        TheadPlugin {
            tool,
            health: HealthBook::default(),
        }
    }
}

/// SDK version from the banner (`PPU SDK Version: 1.4.0` / `Driver Version: ...`).
pub fn parse_sdk_version(banner: &str) -> Option<String> {
    for key in ["PPU SDK Version", "SDK Version", "PPU Version"] {
        if let Some(i) = banner.find(key) {
            let rest = banner[i + key.len()..].trim_start_matches([':', ' ']);
            let v: String = rest
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == '.' || *c == '-')
                .collect();
            if !v.is_empty() {
                return Some(v);
            }
        }
    }
    None
}

#[async_trait]
impl DevicePlugin for TheadPlugin {
    fn vendor(&self) -> Vendor {
        Vendor::Thead
    }

    fn backend(&self) -> String {
        "ppu-smi".into()
    }

    async fn detect(&self) -> anyhow::Result<Vec<Device>> {
        let banner = run(&self.tool, &[], SMI_TIMEOUT).await.unwrap_or_default();
        let sdk = parse_sdk_version(&banner);
        let (rows, _) = smi::query(&self.tool).await?;
        let mut devices: Vec<Device> = rows
            .iter()
            .filter_map(|r| smi::row_to_device(Vendor::Thead, r, sdk.as_deref()))
            .map(|mut d| {
                d.labels.insert("backend".into(), "ppu-smi".into());
                d
            })
            .collect();
        self.health.apply(&mut devices);
        Ok(devices)
    }

    async fn sample(&self) -> anyhow::Result<Vec<DeviceSample>> {
        let (rows, _) = smi::query(&self.tool).await?;
        let samples: Vec<DeviceSample> = rows
            .iter()
            .filter_map(|r| smi::row_to_sample(Vendor::Thead, r))
            .collect();
        self.health.observe(&samples);
        Ok(samples)
    }
}
