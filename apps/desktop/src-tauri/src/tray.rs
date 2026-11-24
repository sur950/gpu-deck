//! Tray icon: health state icon + menu with the fleet summary, active swaps,
//! open alerts, "Switch server…" and "Quit". Fed by `set_tray_status`.

use tauri::image::Image;
use tauri::menu::{IsMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Runtime};

use crate::state::Shell;
use crate::status::{Health, TrayItem, TrayStatus};

pub const TRAY_ID: &str = "gpudeck";
pub const OPEN: &str = "tray:open";
pub const SWITCH: &str = "tray:switch";
pub const QUIT: &str = "tray:quit";
pub const ITEM_PREFIX: &str = "tray:item:";

/// macOS: template images (black + alpha) that the menu bar tints for light and dark bars and
/// wallpapers; the state is in the badge's shape (solid dot / ring / dot with a bar), because
/// template images carry no colour. Windows / Linux: the app tile with a status-coloured dot.
/// Built by scripts/make-icons.py (docs/BRAND.md).
const TEMPLATE_ICONS: bool = cfg!(target_os = "macos");

fn icon(health: Option<Health>) -> Image<'static> {
    let bytes: &'static [u8] = match (TEMPLATE_ICONS, health) {
        (true, Some(Health::Ok)) => include_bytes!("../icons/tray/tray-ok.png"),
        (true, Some(Health::Degraded)) => include_bytes!("../icons/tray/tray-degraded.png"),
        (true, Some(Health::Down)) => include_bytes!("../icons/tray/tray-down.png"),
        (true, _) => include_bytes!("../icons/tray/tray-idle.png"),
        (false, Some(Health::Ok)) => include_bytes!("../icons/tray/tray-ok-color.png"),
        (false, Some(Health::Degraded)) => include_bytes!("../icons/tray/tray-degraded-color.png"),
        (false, Some(Health::Down)) => include_bytes!("../icons/tray/tray-down-color.png"),
        (false, _) => include_bytes!("../icons/tray/tray-idle-color.png"),
    };
    Image::from_bytes(bytes).expect("bundled tray icon is a valid PNG")
}

fn build_menu<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let shell = app.state::<Shell>();
    let current = shell.current();
    let status = shell.tray_status.lock().unwrap().clone();
    let mut paths = shell.tray_paths.lock().unwrap();
    paths.clear();

    let mut owned: Vec<Box<dyn IsMenuItem<R>>> = Vec::new();
    match &current {
        Some(c) => {
            owned.push(Box::new(MenuItem::with_id(
                app,
                "tray:server",
                format!("Connected to {}", c.name),
                false,
                None::<&str>,
            )?));
            match &status {
                Some(s) => {
                    for (i, line) in s.summary_lines().into_iter().enumerate() {
                        owned.push(Box::new(MenuItem::with_id(
                            app,
                            format!("tray:summary:{i}"),
                            line,
                            false,
                            None::<&str>,
                        )?));
                    }
                    owned.push(Box::new(PredefinedMenuItem::separator(app)?));
                    owned.push(Box::new(section(
                        app,
                        &mut paths,
                        "swaps",
                        &s.swaps_title(),
                        &s.active_swaps,
                        "No swaps running",
                        "/swaps",
                    )?));
                    owned.push(Box::new(section(
                        app,
                        &mut paths,
                        "alerts",
                        &s.alerts_title(),
                        &s.open_alerts,
                        "No open alerts",
                        "/alerts",
                    )?));
                }
                None => owned.push(Box::new(MenuItem::with_id(
                    app,
                    "tray:waiting",
                    "Waiting for fleet status…",
                    false,
                    None::<&str>,
                )?)),
            }
            owned.push(Box::new(PredefinedMenuItem::separator(app)?));
            owned.push(Box::new(MenuItem::with_id(
                app,
                OPEN,
                "Open GPU Deck",
                true,
                None::<&str>,
            )?));
        }
        None => {
            owned.push(Box::new(MenuItem::with_id(
                app,
                "tray:none",
                "Not connected",
                false,
                None::<&str>,
            )?));
            owned.push(Box::new(PredefinedMenuItem::separator(app)?));
        }
    }
    owned.push(Box::new(MenuItem::with_id(
        app,
        SWITCH,
        "Switch server…",
        true,
        None::<&str>,
    )?));
    owned.push(Box::new(PredefinedMenuItem::separator(app)?));
    owned.push(Box::new(MenuItem::with_id(
        app,
        QUIT,
        "Quit GPU Deck",
        true,
        None::<&str>,
    )?));
    let refs: Vec<&dyn IsMenuItem<R>> = owned.iter().map(|b| b.as_ref()).collect();
    Menu::with_items(app, &refs)
}

fn section<R: Runtime>(
    app: &AppHandle<R>,
    paths: &mut std::collections::HashMap<String, String>,
    key: &str,
    title: &str,
    items: &[TrayItem],
    empty: &str,
    list_path: &str,
) -> tauri::Result<Submenu<R>> {
    let mut owned: Vec<Box<dyn IsMenuItem<R>>> = Vec::new();
    if items.is_empty() {
        owned.push(Box::new(MenuItem::with_id(
            app,
            format!("tray:{key}:empty"),
            empty,
            false,
            None::<&str>,
        )?));
    }
    for (i, item) in items.iter().enumerate() {
        let id = format!("{ITEM_PREFIX}{key}:{i}");
        let enabled = item.path.is_some();
        if let Some(p) = &item.path {
            paths.insert(id.clone(), p.clone());
        }
        owned.push(Box::new(MenuItem::with_id(
            app,
            id,
            &item.label,
            enabled,
            None::<&str>,
        )?));
    }
    owned.push(Box::new(PredefinedMenuItem::separator(app)?));
    let all_id = format!("{ITEM_PREFIX}{key}:all");
    paths.insert(all_id.clone(), list_path.to_string());
    owned.push(Box::new(MenuItem::with_id(
        app,
        all_id,
        "Show all…",
        true,
        None::<&str>,
    )?));
    let refs: Vec<&dyn IsMenuItem<R>> = owned.iter().map(|b| b.as_ref()).collect();
    Submenu::with_id_and_items(app, format!("tray:{key}"), title, true, &refs)
}

pub fn create<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let menu = build_menu(app)?;
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(icon(None))
        .icon_as_template(TEMPLATE_ICONS)
        .tooltip("GPU Deck · not connected")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .build(app)?;
    Ok(())
}

/// Rebuild the tray icon, tooltip and menu from the shell state.
pub fn refresh<R: Runtime>(app: &AppHandle<R>) {
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    let shell = app.state::<Shell>();
    let current = shell.current();
    let status: Option<TrayStatus> = shell.tray_status.lock().unwrap().clone();
    let health = current.as_ref().and(status.as_ref()).map(|s| s.health);
    let tooltip = match (&current, &status) {
        (Some(c), Some(s)) => s.tooltip(&c.name),
        (Some(c), None) => format!("GPU Deck · {}", c.name),
        _ => "GPU Deck · not connected".into(),
    };
    let _ = tray.set_icon(Some(icon(health)));
    let _ = tray.set_icon_as_template(TEMPLATE_ICONS);
    let _ = tray.set_tooltip(Some(tooltip));
    match build_menu(app) {
        Ok(menu) => {
            let _ = tray.set_menu(Some(menu));
        }
        Err(e) => log::warn!("tray menu: {e}"),
    }
}
