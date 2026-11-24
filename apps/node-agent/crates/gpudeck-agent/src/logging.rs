//! tracing setup with a runtime-adjustable level (`config_push.settings.log_level`).

use std::sync::OnceLock;

use tracing_subscriber::{fmt, prelude::*, reload, EnvFilter, Registry};

static HANDLE: OnceLock<reload::Handle<EnvFilter, Registry>> = OnceLock::new();

/// Initialise logging once (`RUST_LOG` wins over `level`). `json` = one JSON object per line.
pub fn init(level: &str, json: bool) {
    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(filter_for(level)));
    let (layer, handle) = reload::Layer::new(filter);
    let registry = tracing_subscriber::registry().with(layer);
    let ok = if json {
        registry
            .with(fmt::layer().json().with_target(false))
            .try_init()
            .is_ok()
    } else {
        registry
            .with(fmt::layer().with_target(false))
            .try_init()
            .is_ok()
    };
    if ok {
        let _ = HANDLE.set(handle);
    }
}

fn filter_for(level: &str) -> String {
    format!("{level},hyper=warn,rustls=warn,tungstenite=warn,tokio_tungstenite=warn,reqwest=warn")
}

pub fn set_level(level: &str) {
    if std::env::var("RUST_LOG").is_ok() {
        return;
    }
    if let Some(h) = HANDLE.get() {
        let _ = h.reload(EnvFilter::new(filter_for(level)));
    }
}
