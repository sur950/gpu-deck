//! Application menu: Server (Switch server, Reload, Open in browser), View
//! (zoom, full screen, developer tools in debug builds), plus the standard
//! app / Edit / Window menus (Edit is required on macOS for copy and paste).

use tauri::menu::{AboutMetadata, Menu, MenuEvent, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Manager, Runtime};

use crate::state::Shell;
use crate::windows::{self, MAIN};

pub const SWITCH: &str = "menu:switch";
pub const RELOAD: &str = "menu:reload";
pub const BROWSER: &str = "menu:browser";
pub const ZOOM_IN: &str = "menu:zoom-in";
pub const ZOOM_OUT: &str = "menu:zoom-out";
pub const ZOOM_RESET: &str = "menu:zoom-reset";
pub const DEVTOOLS: &str = "menu:devtools";
pub const UPDATES: &str = "menu:updates";

pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let about = AboutMetadata {
        name: Some("GPU Deck".into()),
        version: Some(app.package_info().version.to_string()),
        copyright: Some("Copyright 2026 Suresh Konakanchi. Apache-2.0.".into()),
        // The project credit (docs/BRAND.md): in About, never under the product name.
        credits: Some("Made by Suresh Konakanchi\nhttps://suresh-konakanchi.in".into()),
        authors: Some(vec![
            "Suresh Konakanchi <https://suresh-konakanchi.in>".into()
        ]),
        website: Some("https://sur950.github.io/gpu-deck/".into()),
        website_label: Some("sur950.github.io/gpu-deck".into()),
        ..Default::default()
    };
    let updates = MenuItem::with_id(
        app,
        UPDATES,
        "Check for Updates…",
        crate::updater::enabled(app),
        None::<&str>,
    )?;
    let app_menu = Submenu::with_items(
        app,
        "GPU Deck",
        true,
        &[
            &PredefinedMenuItem::about(app, Some("About GPU Deck"), Some(about))?,
            &updates,
            &PredefinedMenuItem::separator(app)?,
            #[cfg(target_os = "macos")]
            &PredefinedMenuItem::services(app, None)?,
            #[cfg(target_os = "macos")]
            &PredefinedMenuItem::separator(app)?,
            #[cfg(target_os = "macos")]
            &PredefinedMenuItem::hide(app, None)?,
            #[cfg(target_os = "macos")]
            &PredefinedMenuItem::hide_others(app, None)?,
            #[cfg(target_os = "macos")]
            &PredefinedMenuItem::show_all(app, None)?,
            #[cfg(target_os = "macos")]
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::quit(app, Some("Quit GPU Deck"))?,
        ],
    )?;
    let edit = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;
    let server = Submenu::with_items(
        app,
        "Server",
        true,
        &[
            &MenuItem::with_id(
                app,
                SWITCH,
                "Switch Server…",
                true,
                Some("CmdOrCtrl+Shift+S"),
            )?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, RELOAD, "Reload", true, Some("CmdOrCtrl+R"))?,
            &MenuItem::with_id(
                app,
                BROWSER,
                "Open in Browser",
                true,
                Some("CmdOrCtrl+Shift+O"),
            )?,
        ],
    )?;
    let devtools = MenuItem::with_id(
        app,
        DEVTOOLS,
        "Developer Tools",
        true,
        Some("Alt+CmdOrCtrl+I"),
    )?;
    let mut view_items: Vec<&dyn tauri::menu::IsMenuItem<R>> = Vec::new();
    let zoom_in = MenuItem::with_id(app, ZOOM_IN, "Zoom In", true, Some("CmdOrCtrl+="))?;
    let zoom_out = MenuItem::with_id(app, ZOOM_OUT, "Zoom Out", true, Some("CmdOrCtrl+-"))?;
    let zoom_reset = MenuItem::with_id(app, ZOOM_RESET, "Actual Size", true, Some("CmdOrCtrl+0"))?;
    let sep = PredefinedMenuItem::separator(app)?;
    let fullscreen = PredefinedMenuItem::fullscreen(app, None)?;
    view_items.extend([
        &zoom_in as &dyn tauri::menu::IsMenuItem<R>,
        &zoom_out,
        &zoom_reset,
        &sep,
        &fullscreen,
    ]);
    let sep2 = PredefinedMenuItem::separator(app)?;
    if cfg!(debug_assertions) {
        view_items.push(&sep2);
        view_items.push(&devtools);
    }
    let view = Submenu::with_items(app, "View", true, &view_items)?;
    let window = Submenu::with_items(
        app,
        "Window",
        true,
        &[
            &PredefinedMenuItem::minimize(app, None)?,
            &PredefinedMenuItem::maximize(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::close_window(app, None)?,
        ],
    )?;
    Menu::with_items(app, &[&app_menu, &edit, &server, &view, &window])
}

/// Handles app-menu and tray-menu clicks (both arrive here).
pub fn handle<R: Runtime>(app: &AppHandle<R>, event: MenuEvent) {
    let id = event.id().as_ref();
    match id {
        SWITCH | crate::tray::SWITCH => windows::show_connect(app, None),
        RELOAD => {
            if let Some(win) = app.get_webview_window(MAIN) {
                let _ = win.reload();
            }
        }
        BROWSER => {
            let url = app
                .get_webview_window(MAIN)
                .and_then(|w| w.url().ok())
                .filter(|u| matches!(u.scheme(), "http" | "https"));
            if let Some(url) = url {
                let _ = tauri_plugin_opener::open_url(url.as_str(), None::<&str>);
            }
        }
        ZOOM_IN => windows::step_zoom(app, 1),
        ZOOM_OUT => windows::step_zoom(app, -1),
        ZOOM_RESET => windows::step_zoom(app, 0),
        DEVTOOLS => {
            #[cfg(debug_assertions)]
            {
                let focused = app
                    .webview_windows()
                    .into_values()
                    .find(|w| w.is_focused().unwrap_or(false));
                if let Some(win) = focused.or_else(|| app.get_webview_window(MAIN)) {
                    win.open_devtools();
                }
            }
        }
        UPDATES => crate::updater::check(app.clone(), true),
        crate::tray::OPEN => {
            if !windows::show_main(app) {
                windows::show_connect(app, None);
            }
        }
        crate::tray::QUIT => {
            windows::persist_geometry(app);
            app.exit(0);
        }
        other if other.starts_with(crate::tray::ITEM_PREFIX) => {
            let path = app
                .state::<Shell>()
                .tray_paths
                .lock()
                .unwrap()
                .get(other)
                .cloned();
            if let Some(path) = path {
                windows::navigate_main(app, &path);
            }
        }
        _ => {}
    }
}
