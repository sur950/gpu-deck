//! NVIDIA plugin: NVML first (dlopen of `libnvidia-ml.so.1`, no link-time dependency), falling back
//! to `nvidia-smi --query-gpu` CSV. Force the CLI backend with `GPUDECK_NVIDIA_BACKEND=smi`.

pub mod nvml;
pub mod smi;

use std::path::PathBuf;

use async_trait::async_trait;

use crate::health::HealthBook;
use crate::types::{Device, DevicePlugin, DeviceSample, Vendor};
use crate::util::{run, which, SMI_TIMEOUT};

enum Backend {
    Nvml(nvml::NvmlBackend),
    Smi(PathBuf),
}

pub struct NvidiaPlugin {
    backend: Backend,
    health: HealthBook,
}

impl NvidiaPlugin {
    /// NVML when loadable with at least one device, else `nvidia-smi` when on `PATH`.
    pub fn detect_backend() -> Option<Self> {
        let force_smi = std::env::var("GPUDECK_NVIDIA_BACKEND")
            .map(|v| v == "smi")
            .unwrap_or(false);
        if !force_smi {
            if let Some(b) = nvml::NvmlBackend::init() {
                return Some(NvidiaPlugin {
                    backend: Backend::Nvml(b),
                    health: HealthBook::default(),
                });
            }
        }
        which("nvidia-smi").map(Self::with_smi)
    }

    /// CLI backend with an explicit tool path (tests point this at a fixture script).
    pub fn with_smi(tool: PathBuf) -> Self {
        NvidiaPlugin {
            backend: Backend::Smi(tool),
            health: HealthBook::default(),
        }
    }
}

#[async_trait]
impl DevicePlugin for NvidiaPlugin {
    fn vendor(&self) -> Vendor {
        Vendor::Nvidia
    }

    fn backend(&self) -> String {
        match self.backend {
            Backend::Nvml(_) => "nvml".into(),
            Backend::Smi(_) => "nvidia-smi".into(),
        }
    }

    async fn detect(&self) -> anyhow::Result<Vec<Device>> {
        let mut devices = match &self.backend {
            Backend::Nvml(b) => {
                let (devs, fatal) = b.detect()?;
                for (key, reason) in fatal {
                    self.health.mark_fatal(&key, Vendor::Nvidia, reason);
                }
                devs
            }
            Backend::Smi(tool) => {
                let banner = run(tool, &[], SMI_TIMEOUT).await.unwrap_or_default();
                let cuda = smi::parse_cuda_version(&banner);
                let (rows, _) = smi::query(tool).await?;
                rows.iter()
                    .filter_map(|r| smi::row_to_device(Vendor::Nvidia, r, cuda.as_deref()))
                    .map(|mut d| {
                        d.labels.insert("backend".into(), "nvidia-smi".into());
                        d
                    })
                    .collect()
            }
        };
        self.health.apply(&mut devices);
        Ok(devices)
    }

    async fn sample(&self) -> anyhow::Result<Vec<DeviceSample>> {
        let samples = match &self.backend {
            Backend::Nvml(b) => {
                let (samples, fatal) = b.sample()?;
                for (key, reason) in fatal {
                    self.health.mark_fatal(&key, Vendor::Nvidia, reason);
                }
                samples
            }
            Backend::Smi(tool) => {
                let (rows, _) = smi::query(tool).await?;
                rows.iter()
                    .filter_map(|r| smi::row_to_sample(Vendor::Nvidia, r))
                    .collect()
            }
        };
        self.health.observe(&samples);
        Ok(samples)
    }
}
