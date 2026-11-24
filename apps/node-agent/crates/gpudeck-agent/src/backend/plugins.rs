//! Bridge to the `gpudeck-devices` and `gpudeck-engines` crates: `gpudeck_devices` plugins as a
//! [`DeviceSource`] and `gpudeck_engines` adapters as [`EngineDriver`]s. Types are converted through their shared
//! wire shape (`common.json`), so the agent core only sees protocol types.

use std::sync::Arc;

use anyhow::{anyhow, Result};
use async_trait::async_trait;
use gpudeck_devices::DevicePlugin;
use gpudeck_engines::{EngineAdapter, EngineKind, HealthStatus, ModelRef};
use gpudeck_protocol::{
    Device, DeviceVendor, EngineName, EngineSample, GpuSample, HealthConfig, MetricsConfig,
    SessionSample,
};
use serde_json::{json, Value};
use uuid::Uuid;

use super::{
    DeviceReading, DeviceSource, EngineCatalog, EngineDriver, EngineLaunch, Health, ProcSpec,
    ProxySnapshot, RouterModel, Scrape,
};

// ---------------------------------------------------------------------------- devices

pub struct PluginDevices {
    plugins: Vec<Box<dyn DevicePlugin>>,
}

impl PluginDevices {
    pub fn detect() -> Self {
        Self {
            plugins: gpudeck_devices::detect_plugins(),
        }
    }
}

fn vendor(v: gpudeck_devices::Vendor) -> DeviceVendor {
    v.as_str().parse().unwrap_or(DeviceVendor::Apple)
}

#[async_trait]
impl DeviceSource for PluginDevices {
    async fn detect(&self) -> Vec<Device> {
        gpudeck_devices::detect_all(&self.plugins)
            .await
            .into_iter()
            .filter_map(|d| {
                match serde_json::to_value(&d).and_then(serde_json::from_value::<Device>) {
                    Ok(x) => Some(x),
                    Err(e) => {
                        tracing::warn!("device {} {} not representable: {e}", d.vendor, d.index);
                        None
                    }
                }
            })
            .collect()
    }

    async fn sample(&self) -> Vec<DeviceReading> {
        gpudeck_devices::sample_all(&self.plugins)
            .await
            .into_iter()
            .filter_map(|s| {
                let gs: GpuSample =
                    serde_json::from_value(s.to_gpu_sample(&Uuid::nil().to_string())).ok()?;
                Some(DeviceReading {
                    vendor: vendor(s.vendor),
                    index: s.index,
                    uuid: s.uuid.clone(),
                    sample: gs,
                })
            })
            .collect()
    }

    fn backends(&self) -> Vec<String> {
        self.plugins.iter().map(|p| p.backend()).collect()
    }
}

// ---------------------------------------------------------------------------- engines

pub struct PluginCatalog;

impl PluginCatalog {
    pub fn new() -> Self {
        Self
    }
}

impl Default for PluginCatalog {
    fn default() -> Self {
        Self::new()
    }
}

impl EngineCatalog for PluginCatalog {
    fn driver(&self, name: EngineName) -> Arc<dyn EngineDriver> {
        match name.as_str().parse::<EngineKind>() {
            Ok(kind) => Arc::new(PluginDriver {
                name,
                adapter: gpudeck_engines::adapter_for(kind),
                http: super::generic::local_http_client(),
            }),
            Err(_) => Arc::new(super::generic::GenericDriver::new(name)),
        }
    }

    fn observed_sample(
        &self,
        kind: gpudeck_protocol::ObservedEngineKind,
        name: &str,
        metrics_text: &str,
    ) -> Option<EngineSample> {
        let s = gpudeck_engines::observe::sample_from_metrics(kind.as_str(), name, metrics_text)?;
        to_scrape(s).ok().map(|sc| sc.engine)
    }

    fn forget_observed(&self, name: &str) {
        gpudeck_engines::observe::forget(name);
    }
}

pub struct PluginDriver {
    name: EngineName,
    adapter: Box<dyn EngineAdapter>,
    http: reqwest::Client,
}

/// Engine-crate `LaunchSpec` for an agent launch (field names are shared with the protocol).
pub fn plugin_launch_spec(l: &EngineLaunch) -> Result<gpudeck_engines::LaunchSpec> {
    let p = |x: &Option<std::path::PathBuf>| x.as_ref().map(|p| p.display().to_string());
    let spec = &l.launch.spec_decoding;
    let v = json!({
        "engine": l.engine.name.as_str(),
        "served_model_name": l.served_model_name.clone().unwrap_or_default(),
        "model_path": p(&l.model_path).or_else(|| p(&l.model_dir)).unwrap_or_default(),
        "model_paths": l.model_paths.iter().map(|x| x.display().to_string()).collect::<Vec<_>>(),
        "model_type": l.model.as_ref().map(|m| m.model_type.as_str()).unwrap_or("chat"),
        "model_family": l.model.as_ref().and_then(|m| m.config.get("family").or_else(|| m.config.get("model_family")).cloned()),
        "model_config": l.model.as_ref().map(|m| Value::Object(m.config.clone())).unwrap_or(json!({})),
        "host": "127.0.0.1",
        "port": l.port,
        "devices": l.devices.iter().map(|d| json!({"vendor": d.vendor.as_str(), "index": d.index, "slice": d.slice})).collect::<Vec<_>>(),
        "launch_flags": l.launch.flags,
        "tool_parser": l.launch.tool_parser,
        "context_size": l.launch.context_size,
        "control_vectors": l.launch.control_vectors,
        "env": l.launch.env,
        "preset": l.launch.preset,
        "max_concurrency": l.launch.max_concurrency,
        "tp_size": l.launch.tp_size,
        "pp_size": l.launch.pp_size,
        "spec_decoding": {"method": spec.get("method"), "num_draft_tokens": spec.get("num_draft_tokens"), "draft_model_path": p(&l.draft_model_path), "extra": spec},
        "pd_split": l.launch.pd_split,
        "pd_role": l.launch.pd_role,
        "pd_config": l.launch.pd_config,
        "kv_sharing": l.launch.kv_sharing,
        "kv_sharing_config": l.launch.kv_sharing_config,
        "trust_remote_code": l.model.as_ref().is_some_and(|m| m.trust_remote_code),
        "router_mode": l.router.is_some(),
        "models_dir": l.router.as_ref().map(|r| r.models_dir.display().to_string()),
        "models_preset": l.router.as_ref().map(|r| r.preset_path.display().to_string()),
        "executable": p(&l.executable),
        "library_dir": p(&l.library_dir),
        "cache_dir": l.cache_dir.display().to_string(),
        "runtime": l.engine.runtime.as_str(),
        "container": l.engine.image.as_ref().map(|i| json!({"image": i})),
        "engine_version": l.engine.version,
    });
    serde_json::from_value(v)
        .map_err(|e| anyhow!("cannot build {} launch spec: {e}", l.engine.name))
}

fn to_scrape(s: gpudeck_engines::EngineSample) -> Result<Scrape> {
    let nil = Uuid::nil().to_string();
    let engine: EngineSample = serde_json::from_value(s.to_wire(&nil, &nil))?;
    let sessions = s
        .sessions_to_wire(&nil)
        .into_iter()
        .filter_map(|mut v| {
            if let Some(m) = v.as_object_mut() {
                let ok = m
                    .get("request_id")
                    .and_then(|r| r.as_str())
                    .is_some_and(|r| r.parse::<Uuid>().is_ok());
                if !ok {
                    m.insert("request_id".into(), Value::Null);
                }
            }
            serde_json::from_value::<SessionSample>(v).ok()
        })
        .collect();
    Ok(Scrape { engine, sessions })
}

fn to_plugin_sample(s: &Scrape) -> Option<gpudeck_engines::EngineSample> {
    let mut v = serde_json::to_value(&s.engine).ok()?;
    let m = v.as_object_mut()?;
    m.remove("instance_id");
    m.remove("deployment_id");
    let mut plugin_sample: gpudeck_engines::EngineSample = serde_json::from_value(v).ok()?;
    plugin_sample.sessions = s
        .sessions
        .iter()
        .filter_map(|x| {
            let mut v = serde_json::to_value(x).ok()?;
            let m = v.as_object_mut()?;
            m.remove("ts");
            m.remove("instance_id");
            serde_json::from_value(v).ok()
        })
        .collect();
    Some(plugin_sample)
}

fn to_plugin_stats(s: &ProxySnapshot) -> gpudeck_engines::ProxyStats {
    let rec = |r: &crate::proxy::stats::RequestRecord| gpudeck_engines::RequestRecord {
        request_id: r.request_id.clone(),
        e2e_ms: r.e2e_ms,
        ttft_ms: r.ttft_ms,
        queue_ms: r.queue_ms,
        status: r.status,
        transport_error: r.transport_error,
        prompt_tokens: r.prompt_tokens,
        completion_tokens: r.completion_tokens,
        cached_tokens: r.cached_tokens,
        reasoning_tokens: r.reasoning_tokens,
        draft_tokens: r.draft_tokens,
        draft_accepted: r.draft_accepted,
        media_units: r.media_units,
        streamed: r.streamed,
    };
    gpudeck_engines::ProxyStats {
        requests_total: s.requests_total,
        requests_success_total: s.requests_success_total,
        errors_total: s.errors_total,
        prompt_tokens_total: s.prompt_tokens_total,
        generated_tokens_total: s.generated_tokens_total,
        cached_tokens_total: s.cached_tokens_total,
        draft_tokens_total: s.draft_tokens_total,
        draft_accepted_total: s.draft_accepted_total,
        active: s.active,
        waiting: s.waiting,
        max_concurrent: s.max_concurrent,
        window: s.window.iter().map(rec).collect(),
        window_seconds: s.window_seconds,
        inflight: s
            .inflight
            .iter()
            .map(|i| gpudeck_engines::InflightRequest {
                connection_id: i.connection_id.clone(),
                request_id: i.request_id.clone(),
                elapsed_ms: i.elapsed_ms,
                queued: i.queued,
                first_byte_seen: i.first_byte_seen,
                ttft_ms: i.ttft_ms,
                prompt_tokens: i.prompt_tokens,
                chunks_seen: i.chunks_seen,
            })
            .collect(),
        kv_bytes_per_token: s.kv_bytes_per_token,
        context_size: s.context_size,
    }
}

#[async_trait]
impl EngineDriver for PluginDriver {
    fn name(&self) -> EngineName {
        self.name
    }

    fn adapter_command(&self, launch: &EngineLaunch) -> Result<ProcSpec> {
        let spec = plugin_launch_spec(launch)?;
        let p = self.adapter.command(&spec)?;
        Ok(ProcSpec {
            program: p.program,
            args: p.args,
            env: p.env,
            cwd: p.cwd.map(Into::into),
            container: p.container.map(|c| super::ContainerRun {
                runtime: c.runtime,
                name: c.name,
            }),
        })
    }

    async fn health(&self, base_url: &str, health: &HealthConfig, model: Option<&str>) -> Health {
        // Engine versions with a non-default health path keep the generic probe.
        if model.is_none() && health.path != self.adapter.health_config().path {
            return super::generic::probe(&self.http, base_url, health, None).await;
        }
        match self
            .adapter
            .health(&gpudeck_engines::llama_cpp::instance_url(base_url, model))
            .await
        {
            HealthStatus::Healthy => Health::Healthy,
            HealthStatus::Loading => Health::Loading,
            HealthStatus::Unhealthy { reason } => Health::Unhealthy(reason),
        }
    }

    async fn scrape(
        &self,
        base_url: &str,
        _metrics: &MetricsConfig,
        model: Option<&str>,
    ) -> Result<Scrape> {
        let s = self
            .adapter
            .scrape(&gpudeck_engines::llama_cpp::instance_url(base_url, model))
            .await?;
        to_scrape(s)
    }

    fn supports_hot_swap(&self) -> bool {
        self.adapter.supports_hot_swap()
    }

    async fn load_model(&self, base_url: &str, name: &str, path: Option<&str>) -> Result<()> {
        let mut m = ModelRef::named(name);
        m.path = path.map(str::to_owned);
        self.adapter.load_model(base_url, &m).await
    }

    async fn unload_model(&self, base_url: &str, name: &str) -> Result<()> {
        self.adapter.unload_model(base_url, name).await
    }

    async fn router_models(&self, base_url: &str) -> Result<Vec<RouterModel>> {
        super::generic::router_models(&self.http, base_url).await
    }

    fn merge_proxy(&self, scrape: &mut Scrape, snap: &ProxySnapshot, derive_from_proxy: bool) {
        let Some(mut plugin_sample) = to_plugin_sample(scrape) else {
            super::generic::merge_proxy(scrape, snap, derive_from_proxy);
            return;
        };
        gpudeck_engines::proxy::merge_proxy_stats(
            &mut plugin_sample,
            &to_plugin_stats(snap),
            derive_from_proxy || self.adapter.derive_from_proxy(),
        );
        match to_scrape(plugin_sample) {
            Ok(mut s) => {
                s.engine.ts = scrape.engine.ts;
                *scrape = s;
            }
            Err(_) => super::generic::merge_proxy(scrape, snap, derive_from_proxy),
        }
    }

    fn admin_paths(&self) -> Vec<String> {
        // `/health` stays forwardable when routing allows it (contract example); the agent's own
        // `/instances/{id}/_health` is the canonical probe.
        self.adapter
            .admin_paths()
            .into_iter()
            .filter(|p| *p != "/health")
            .map(str::to_owned)
            .collect()
    }

    fn upstream_model_name(&self, launch: &EngineLaunch) -> Option<String> {
        plugin_launch_spec(launch)
            .ok()
            .map(|s| self.adapter.upstream_model_name(&s))
            .filter(|n| !n.is_empty())
    }

    fn register(&self, base_url: &str, launch: &EngineLaunch) {
        let hints = gpudeck_engines::state::Hints {
            served_model_name: launch.served_model_name.clone(),
            context_size: launch.launch.context_size,
            max_concurrency: launch.launch.max_concurrency.map(u64::from),
            kv_unified: launch.engine.name == EngineName::LlamaCpp
                && (launch
                    .launch
                    .flags
                    .get("kv-unified")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false)
                    || (launch.launch.max_concurrency.is_none()
                        && !launch.launch.flags.contains_key("parallel")
                        && !launch.launch.flags.contains_key("np"))),
            kv_bytes_per_token: launch
                .model
                .as_ref()
                .and_then(|m| crate::metrics::kv_bytes_per_token(&m.config)),
            memory_fraction: None,
            router_mode: launch.router.is_some(),
            models_dir: launch
                .router
                .as_ref()
                .map(|r| r.models_dir.display().to_string()),
        };
        gpudeck_engines::state::register(base_url, hints);
    }

    fn forget(&self, base_url: &str) {
        gpudeck_engines::forget(base_url);
    }
}
