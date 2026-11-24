//! The two windows: `connect` (local connect screen, bundled assets) and `main`
//! (the selected server's own web UI, a remote origin).
//!
//! The main window is recreated on every server switch so its initialization
//! script, navigation policy and IPC capability are bound to exactly one origin.

use tauri::ipc::CapabilityBuilder;
use tauri::webview::{DownloadEvent, NewWindowResponse};
use tauri::{
    AppHandle, Emitter, Manager, Runtime, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    WindowEvent,
};
use url::Url;

use crate::deeplink::{app_url, validate_app_path, Prefill};
use crate::servers::{origin_of, Server, WindowState};
use crate::state::{self, now_ms, Current, Shell};

pub const MAIN: &str = "main";
pub const CONNECT: &str = "connect";
const DEFAULT_SIZE: (f64, f64) = (1360.0, 860.0);
const MIN_SIZE: (f64, f64) = (900.0, 600.0);
pub const ZOOM_STEPS: [f64; 9] = [0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75];

/// Script injected into the main window's top frame. It exposes the two bridge
/// calls only when the page is on the connected server's origin; the IPC ACL and
/// the command handlers check the origin again.
pub fn bridge_script(origin: &str) -> String {
    let origin = serde_json::to_string(origin).unwrap_or_else(|_| "\"\"".into());
    let platform = serde_json::to_string(std::env::consts::OS).unwrap_or_else(|_| "\"\"".into());
    include_str!("bridge.js")
        .replace("__ORIGIN__", &origin)
        .replace("__PLATFORM__", &platform)
        .replace(
            "__VERSION__",
            &serde_json::to_string(env!("CARGO_PKG_VERSION")).unwrap(),
        )
}

/// Grant the remote-origin capability (only `notify` + `set_tray_status`, only
/// in the main window) for a saved server origin. Idempotent.
fn grant_ipc<R: Runtime>(app: &AppHandle<R>, origin: &str) -> Result<(), String> {
    let shell = app.state::<Shell>();
    let mut granted = shell.granted_origins.lock().unwrap();
    if granted.contains(origin) {
        return Ok(());
    }
    let id = format!("server-bridge-{}", granted.len() + 1);
    app.add_capability(
        CapabilityBuilder::new(id)
            .remote(format!("{origin}/*"))
            .local(false)
            .window(MAIN)
            .webview(MAIN)
            .permission("allow-notify")
            .permission("allow-set-tray-status"),
    )
    .map_err(|e| e.to_string())?;
    granted.insert(origin.to_string());
    Ok(())
}

fn open_external(url: &Url) {
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        if let Err(e) = tauri_plugin_opener::open_url(url.as_str(), None::<&str>) {
            log::warn!("opening {url} in the browser failed: {e}");
        }
    }
}

fn on_screen<R: Runtime>(app: &AppHandle<R>, ws: &WindowState) -> bool {
    let Ok(monitors) = app.available_monitors() else {
        return false;
    };
    monitors.iter().any(|m| {
        let s = m.scale_factor();
        let p = m.position().to_logical::<f64>(s);
        let size = m.size().to_logical::<f64>(s);
        let (x, y) = (f64::from(ws.x) + 40.0, f64::from(ws.y) + 10.0);
        x >= p.x && x < p.x + size.width && y >= p.y && y < p.y + size.height
    })
}

/// Current geometry of the main window in logical pixels (maximised windows
/// keep their last normal bounds).
pub fn capture_geometry<R: Runtime>(win: &WebviewWindow<R>) {
    let shell = win.state::<Shell>();
    let zoom = *shell.zoom.lock().unwrap();
    let mut geometry = shell.main_geometry.lock().unwrap();
    if win.is_minimized().unwrap_or(false) || win.is_fullscreen().unwrap_or(false) {
        return;
    }
    let maximized = win.is_maximized().unwrap_or(false);
    if maximized {
        if let Some(g) = geometry.as_mut() {
            g.maximized = true;
            g.zoom = zoom;
            return;
        }
    }
    let Ok(scale) = win.scale_factor() else {
        return;
    };
    let (Ok(pos), Ok(size)) = (win.outer_position(), win.inner_size()) else {
        return;
    };
    let pos = pos.to_logical::<f64>(scale);
    let size = size.to_logical::<f64>(scale);
    *geometry = Some(WindowState {
        x: pos.x.round() as i32,
        y: pos.y.round() as i32,
        width: size.width.round().max(MIN_SIZE.0) as u32,
        height: size.height.round().max(MIN_SIZE.1) as u32,
        maximized,
        zoom,
    });
}

/// Persist the main window geometry for the current server.
pub fn persist_geometry<R: Runtime>(app: &AppHandle<R>) {
    let shell = app.state::<Shell>();
    let Some(current) = shell.current() else {
        return;
    };
    let Some(geometry) = *shell.main_geometry.lock().unwrap() else {
        return;
    };
    {
        let mut list = shell.servers.lock().unwrap();
        if list.get(&current.server_id).is_none() {
            return;
        }
        list.window_states
            .insert(current.server_id.clone(), geometry);
    }
    state::save(app);
}

/// Navigate the main window to an in-app path. Client-side through the web
/// bridge when present (cancelable `gpudeck:navigate` event), else a full load.
pub fn navigate_main<R: Runtime>(app: &AppHandle<R>, path: &str) {
    let Some(win) = app.get_webview_window(MAIN) else {
        return;
    };
    let Ok(path) = validate_app_path(path) else {
        return;
    };
    let js = format!(
        "(function(p){{var e=new CustomEvent('gpudeck:navigate',{{detail:{{path:p}},cancelable:true}});\
         if(window.dispatchEvent(e)){{window.location.assign(p);}}}})({});",
        serde_json::to_string(&path).unwrap_or_else(|_| "\"/\"".into())
    );
    if let Err(e) = win.eval(js) {
        log::warn!("navigate failed: {e}");
    }
    show_main(app);
}

pub fn show_main<R: Runtime>(app: &AppHandle<R>) -> bool {
    match app.get_webview_window(MAIN) {
        Some(win) => {
            let _ = win.unminimize();
            let _ = win.show();
            let _ = win.set_focus();
            true
        }
        None => false,
    }
}

/// Show the connect screen, optionally prefilled (deep link to an unknown server).
pub fn show_connect<R: Runtime>(app: &AppHandle<R>, prefill: Option<Prefill>) {
    if let Some(p) = prefill {
        *app.state::<Shell>().pending_prefill.lock().unwrap() = Some(p.clone());
        let _ = app.emit_to(CONNECT, "gpudeck://prefill", p);
    }
    if let Some(win) = app.get_webview_window(CONNECT) {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
    let _ = app.emit_to(CONNECT, "gpudeck://servers-changed", ());
}

pub fn hide_connect<R: Runtime>(app: &AppHandle<R>) {
    if let Some(win) = app.get_webview_window(CONNECT) {
        let _ = win.hide();
    }
}

/// Close the main window (server removed / switching) after saving its geometry.
pub fn close_main<R: Runtime>(app: &AppHandle<R>) {
    if let Some(win) = app.get_webview_window(MAIN) {
        capture_geometry(&win);
        persist_geometry(app);
        let _ = win.destroy();
    }
    let shell = app.state::<Shell>();
    *shell.current.lock().unwrap() = None;
    *shell.main_geometry.lock().unwrap() = None;
    *shell.tray_status.lock().unwrap() = None;
    crate::tray::refresh(app);
}

pub fn set_zoom<R: Runtime>(app: &AppHandle<R>, zoom: f64) {
    let zoom = zoom.clamp(ZOOM_STEPS[0], ZOOM_STEPS[ZOOM_STEPS.len() - 1]);
    let shell = app.state::<Shell>();
    *shell.zoom.lock().unwrap() = zoom;
    if let Some(win) = app.get_webview_window(MAIN) {
        let _ = win.set_zoom(zoom);
        capture_geometry(&win);
    }
}

pub fn step_zoom<R: Runtime>(app: &AppHandle<R>, direction: i32) {
    let current = *app.state::<Shell>().zoom.lock().unwrap();
    let next = if direction == 0 {
        1.0
    } else if direction > 0 {
        ZOOM_STEPS
            .iter()
            .copied()
            .find(|z| *z > current + 0.001)
            .unwrap_or(current)
    } else {
        ZOOM_STEPS
            .iter()
            .rev()
            .copied()
            .find(|z| *z < current - 0.001)
            .unwrap_or(current)
    };
    set_zoom(app, next);
}

/// Open (or focus) the main window on `server`, then `path`.
pub fn open_main<R: Runtime>(
    app: &AppHandle<R>,
    server: &Server,
    path: Option<&str>,
) -> Result<(), String> {
    let shell = app.state::<Shell>();
    if let (Some(current), Some(_)) = (shell.current(), app.get_webview_window(MAIN)) {
        if current.server_id == server.id && current.origin == server.url {
            if let Some(p) = path {
                navigate_main(app, p);
            }
            show_main(app);
            hide_connect(app);
            return Ok(());
        }
    }
    close_main(app);
    grant_ipc(app, &server.url)?;

    let target = app_url(&server.url, path.unwrap_or("/")).ok_or("invalid path")?;
    let saved = {
        let mut list = shell.servers.lock().unwrap();
        list.touch(&server.id, now_ms())
            .map_err(|e| e.to_string())?;
        list.window_states.get(&server.id).copied()
    };
    state::save(app);
    *shell.current.lock().unwrap() = Some(Current {
        server_id: server.id.clone(),
        name: server.name.clone(),
        origin: server.url.clone(),
    });
    *shell.main_geometry.lock().unwrap() = saved;
    let zoom = saved
        .map(|s| s.zoom)
        .filter(|z| z.is_finite())
        .unwrap_or(1.0);
    *shell.zoom.lock().unwrap() = zoom;

    let origin = server.url.clone();
    let nav_origin = origin.clone();
    let win_origin = origin.clone();
    let nav_app = app.clone();
    let mut builder = WebviewWindowBuilder::new(app, MAIN, WebviewUrl::External(target))
        .title(format!("{} · GPU Deck", server.name))
        .min_inner_size(MIN_SIZE.0, MIN_SIZE.1)
        .initialization_script(bridge_script(&origin))
        .zoom_hotkeys_enabled(false)
        .devtools(cfg!(debug_assertions))
        .on_navigation(move |url| {
            if origin_of(url).as_deref() == Some(nav_origin.as_str())
                || url.as_str() == "about:blank"
            {
                return true;
            }
            if matches!(url.scheme(), "blob" | "data") {
                // Downloads (CSV / JSON exports) are handled by on_download.
                return true;
            }
            open_external(url);
            false
        })
        .on_new_window(move |url, _features| {
            if origin_of(&url).as_deref() == Some(win_origin.as_str()) {
                if let Some(win) = nav_app.get_webview_window(MAIN) {
                    let _ = win.navigate(url);
                }
            } else {
                open_external(&url);
            }
            NewWindowResponse::Deny
        })
        .on_download(|webview, event| match event {
            DownloadEvent::Requested { url, destination } => {
                let name = destination
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .filter(|n| !n.is_empty())
                    .or_else(|| {
                        url.path_segments()
                            .and_then(|mut s| s.next_back())
                            .map(str::to_string)
                    })
                    .filter(|n| !n.is_empty() && !n.contains(['/', '\\']))
                    .unwrap_or_else(|| "gpu-deck-download".into());
                match webview.path().download_dir() {
                    Ok(dir) => {
                        *destination = dir.join(name);
                        true
                    }
                    Err(_) => false,
                }
            }
            DownloadEvent::Finished { .. } => true,
            _ => true,
        });
    builder = match saved {
        Some(ws) if on_screen(app, &ws) => builder
            .inner_size(f64::from(ws.width), f64::from(ws.height))
            .position(f64::from(ws.x), f64::from(ws.y)),
        Some(ws) => builder
            .inner_size(f64::from(ws.width), f64::from(ws.height))
            .center(),
        None => builder.inner_size(DEFAULT_SIZE.0, DEFAULT_SIZE.1).center(),
    };
    let win = builder.build().map_err(|e| e.to_string())?;
    if saved.map(|s| s.maximized).unwrap_or(false) {
        let _ = win.maximize();
    }
    if (zoom - 1.0).abs() > f64::EPSILON {
        let _ = win.set_zoom(zoom);
    }
    let handle = win.clone();
    win.on_window_event(move |event| match event {
        WindowEvent::Moved(_) | WindowEvent::Resized(_) => capture_geometry(&handle),
        WindowEvent::CloseRequested { api, .. } => {
            // Keep the page (and its live stream) alive for notifications; the
            // tray / dock brings it back. Quit from the menu or the tray.
            api.prevent_close();
            capture_geometry(&handle);
            persist_geometry(handle.app_handle());
            let _ = handle.hide();
        }
        _ => {}
    });
    hide_connect(app);
    crate::tray::refresh(app);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bridge_script_embeds_a_json_origin() {
        let s = bridge_script("http://localhost:3180");
        assert!(s.contains("\"http://localhost:3180\""));
        assert!(!s.contains("__ORIGIN__"));
        assert!(!s.contains("__PLATFORM__"));
        assert!(s.contains("\"notify\""));
        assert!(s.contains("\"set_tray_status\""));
        // A hostile origin string cannot break out of the JSON literal.
        let s = bridge_script("x\";alert(1);\"");
        assert!(s.contains(r#""x\";alert(1);\"""#));
    }

    #[test]
    fn zoom_steps_are_sorted() {
        assert!(ZOOM_STEPS.windows(2).all(|w| w[0] < w[1]));
        assert!(ZOOM_STEPS.contains(&1.0));
    }
}
