//! IPC commands.
//!
//! Connect screen (local origin, `connect` window only — capabilities/connect.json):
//! `servers_list`, `servers_save`, `servers_remove`, `servers_probe`,
//! `servers_connect`, `connect_prefill`, `connect_back`.
//!
//! Web bridge (remote origin, `main` window only, runtime capability granted per
//! saved server origin): `notify`, `set_tray_status`. Both re-check that the
//! caller is the main window on the connected, saved server origin.

use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime, State, Webview};
use tauri_plugin_notification::NotificationExt;

use crate::deeplink::Prefill;
use crate::probe::{self, ProbeResult};
use crate::servers::{origin_of, Server, ServerInput};
use crate::state::{self, now_ms, Shell};
use crate::status::{NotifyPayload, TrayStatusPayload};
use crate::windows::{self, CONNECT, MAIN};

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerView {
    #[serde(flatten)]
    pub server: Server,
    pub insecure: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServersView {
    pub servers: Vec<ServerView>,
    pub last_server_id: Option<String>,
    pub current_server_id: Option<String>,
    pub app_version: String,
    pub platform: &'static str,
}

fn require_window(webview: &Webview<impl Runtime>, label: &str) -> Result<(), String> {
    if webview.label() == label {
        Ok(())
    } else {
        Err("not allowed from this window".into())
    }
}

#[tauri::command]
pub fn servers_list<R: Runtime>(
    app: AppHandle<R>,
    webview: Webview<R>,
    shell: State<'_, Shell>,
) -> Result<ServersView, String> {
    require_window(&webview, CONNECT)?;
    let list = shell.servers.lock().unwrap();
    Ok(ServersView {
        servers: list
            .sorted()
            .into_iter()
            .map(|s| ServerView {
                insecure: s.insecure(),
                server: s,
            })
            .collect(),
        last_server_id: list.last_server_id.clone(),
        current_server_id: shell.current().map(|c| c.server_id),
        app_version: app.package_info().version.to_string(),
        platform: std::env::consts::OS,
    })
}

#[tauri::command]
pub fn servers_save<R: Runtime>(
    app: AppHandle<R>,
    webview: Webview<R>,
    shell: State<'_, Shell>,
    input: ServerInput,
) -> Result<Server, String> {
    require_window(&webview, CONNECT)?;
    let editing_current = input.id.is_some() && input.id == shell.current().map(|c| c.server_id);
    let saved = shell
        .servers
        .lock()
        .unwrap()
        .upsert(input, || uuid::Uuid::new_v4().to_string(), now_ms())
        .map_err(|e| e.to_string())?;
    state::save(&app);
    if editing_current {
        let origin_changed = shell
            .current()
            .map(|c| c.origin != saved.url)
            .unwrap_or(false);
        if origin_changed {
            // The open page belongs to the old origin: close it.
            windows::close_main(&app);
        } else if let Some(c) = shell.current.lock().unwrap().as_mut() {
            c.name = saved.name.clone();
        }
        crate::tray::refresh(&app);
    }
    Ok(saved)
}

#[tauri::command]
pub fn servers_remove<R: Runtime>(
    app: AppHandle<R>,
    webview: Webview<R>,
    shell: State<'_, Shell>,
    id: String,
) -> Result<(), String> {
    require_window(&webview, CONNECT)?;
    if shell.current().map(|c| c.server_id == id).unwrap_or(false) {
        windows::close_main(&app);
    }
    shell
        .servers
        .lock()
        .unwrap()
        .remove(&id)
        .map_err(|e| e.to_string())?;
    state::save(&app);
    Ok(())
}

#[tauri::command]
pub async fn servers_probe<R: Runtime>(
    webview: Webview<R>,
    url: String,
    api_url: Option<String>,
) -> Result<ProbeResult, String> {
    require_window(&webview, CONNECT)?;
    probe::probe(&url, api_url.as_deref()).await
}

#[tauri::command]
pub fn servers_connect<R: Runtime>(
    app: AppHandle<R>,
    webview: Webview<R>,
    shell: State<'_, Shell>,
    id: String,
    path: Option<String>,
) -> Result<(), String> {
    require_window(&webview, CONNECT)?;
    let server = shell.server(&id).ok_or("Server not found.")?;
    windows::open_main(&app, &server, path.as_deref())
}

#[tauri::command]
pub fn connect_prefill<R: Runtime>(
    webview: Webview<R>,
    shell: State<'_, Shell>,
) -> Result<Option<Prefill>, String> {
    require_window(&webview, CONNECT)?;
    Ok(shell.pending_prefill.lock().unwrap().take())
}

/// "Back to <server>" on the connect screen.
#[tauri::command]
pub fn connect_back<R: Runtime>(app: AppHandle<R>, webview: Webview<R>) -> Result<bool, String> {
    require_window(&webview, CONNECT)?;
    let shown = windows::show_main(&app);
    if shown {
        windows::hide_connect(&app);
    }
    Ok(shown)
}

/// The caller must be the main window, on the connected server's origin, and
/// that server must still be saved. Returns the origin.
fn require_server_page<R: Runtime>(webview: &Webview<R>, shell: &Shell) -> Result<String, String> {
    require_window(webview, MAIN)?;
    let url = webview.url().map_err(|e| e.to_string())?;
    let origin = origin_of(&url).ok_or("opaque origin")?;
    let current = shell.current().ok_or("not connected")?;
    if origin != current.origin || !shell.servers.lock().unwrap().is_saved_origin(&origin) {
        return Err("origin not allowed".into());
    }
    Ok(origin)
}

#[tauri::command]
pub fn notify<R: Runtime>(
    app: AppHandle<R>,
    webview: Webview<R>,
    shell: State<'_, Shell>,
    payload: NotifyPayload,
) -> Result<(), String> {
    let origin = require_server_page(&webview, &shell)?;
    let n = payload.sanitize().map_err(str::to_string)?;
    if !shell
        .notify_limiter
        .lock()
        .unwrap()
        .allow(std::time::Instant::now())
    {
        return Err("rate limited".into());
    }
    let mut builder = app
        .notification()
        .builder()
        .title(n.title)
        .body(n.body)
        .extra("origin", origin);
    if let Some(path) = n.path {
        builder = builder.extra("path", path);
    }
    builder.show().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn set_tray_status<R: Runtime>(
    app: AppHandle<R>,
    webview: Webview<R>,
    shell: State<'_, Shell>,
    payload: TrayStatusPayload,
) -> Result<(), String> {
    require_server_page(&webview, &shell)?;
    let status = payload.sanitize();
    {
        let mut slot = shell.tray_status.lock().unwrap();
        if slot.as_ref() == Some(&status) {
            return Ok(());
        }
        *slot = Some(status);
    }
    crate::tray::refresh(&app);
    Ok(())
}

/// A click on a notification: focus the main window and open its item, but only
/// when the notification came from the server that is still connected.
pub fn on_notification_action<R: Runtime>(
    app: &AppHandle<R>,
    action_id: &str,
    extra: &std::collections::HashMap<String, serde_json::Value>,
) {
    if action_id != "tap" {
        return;
    }
    let origin = extra
        .get("origin")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let path = extra
        .get("path")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let current = app.state::<Shell>().current();
    let app = app.clone();
    let _ = app
        .clone()
        .run_on_main_thread(move || match (current, origin, path) {
            (Some(c), Some(o), Some(p)) if c.origin == o => windows::navigate_main(&app, &p),
            _ => {
                if !windows::show_main(&app) {
                    windows::show_connect(&app, None);
                }
            }
        });
}
