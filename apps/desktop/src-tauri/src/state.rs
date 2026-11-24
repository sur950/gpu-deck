//! Process-wide shell state (managed by Tauri) and persistence of the server
//! list through tauri-plugin-store (`servers.json` in the app config dir).

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::{AppHandle, Manager, Runtime};
use tauri_plugin_store::StoreExt;

use crate::deeplink::Prefill;
use crate::servers::{Server, ServerList, WindowState};
use crate::status::{RateLimiter, TrayStatus};

pub const STORE_FILE: &str = "servers.json";
pub const STORE_KEY: &str = "state";

/// The server loaded in the main window.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Current {
    pub server_id: String,
    pub name: String,
    pub origin: String,
}

pub struct Shell {
    pub servers: Mutex<ServerList>,
    pub current: Mutex<Option<Current>>,
    pub tray_status: Mutex<Option<TrayStatus>>,
    /// Menu id → in-app path for the alert / swap entries of the tray menu.
    pub tray_paths: Mutex<HashMap<String, String>>,
    pub notify_limiter: Mutex<RateLimiter>,
    pub pending_prefill: Mutex<Option<Prefill>>,
    /// Origins that already have a runtime IPC capability.
    pub granted_origins: Mutex<HashSet<String>>,
    /// Latest geometry of the main window (saved on switch / close / exit).
    pub main_geometry: Mutex<Option<WindowState>>,
    pub zoom: Mutex<f64>,
}

impl Shell {
    pub fn new(servers: ServerList) -> Self {
        Self {
            servers: Mutex::new(servers),
            current: Mutex::new(None),
            tray_status: Mutex::new(None),
            tray_paths: Mutex::new(HashMap::new()),
            notify_limiter: Mutex::new(RateLimiter::new(10, Duration::from_secs(60))),
            pending_prefill: Mutex::new(None),
            granted_origins: Mutex::new(HashSet::new()),
            main_geometry: Mutex::new(None),
            zoom: Mutex::new(1.0),
        }
    }

    pub fn current(&self) -> Option<Current> {
        self.current.lock().unwrap().clone()
    }

    pub fn server(&self, id: &str) -> Option<Server> {
        self.servers.lock().unwrap().get(id).cloned()
    }
}

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn store_path<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<PathBuf> {
    Ok(app.path().app_config_dir()?.join(STORE_FILE))
}

pub fn load<R: Runtime>(app: &AppHandle<R>) -> ServerList {
    let value = store_path(app)
        .ok()
        .and_then(|p| app.store(p).ok())
        .and_then(|s| s.get(STORE_KEY));
    ServerList::from_value(value)
}

pub fn save<R: Runtime>(app: &AppHandle<R>) {
    let value = app.state::<Shell>().servers.lock().unwrap().to_value();
    let result = store_path(app)
        .map_err(|e| e.to_string())
        .and_then(|p| app.store(p).map_err(|e| e.to_string()))
        .and_then(|s| {
            s.set(STORE_KEY, value);
            s.save().map_err(|e| e.to_string())
        });
    if let Err(e) = result {
        log::error!("saving the server list failed: {e}");
    }
}
