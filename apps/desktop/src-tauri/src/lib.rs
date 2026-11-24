//! GPU Deck desktop shell (Tauri 2, "remote shell" mode).
//!
//! A small bundled connect screen picks a GPU Deck control plane; the main
//! window then loads that server's own web UI (same origin, so the session
//! cookie and CSRF double-submit work unchanged and the UI always matches the
//! server version). Native integration is limited to two IPC commands the web
//! app may call from a saved server origin: `notify` and `set_tray_status`.
//! The shell never stores or sees credentials.

pub mod commands;
pub mod deeplink;
pub mod menu;
pub mod probe;
pub mod servers;
pub mod state;
pub mod status;
pub mod tray;
pub mod updater;
pub mod windows;

use tauri::{Manager, RunEvent, WindowEvent};
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_notification::NotificationExt;

use crate::deeplink::{parse_deep_link, resolve, DeepLinkAction};
use crate::state::Shell;

/// rustls needs one process-wide crypto provider (reqwest + updater use ring).
pub fn install_crypto_provider() {
    let _ = rustls::crypto::ring::default_provider().install_default();
}

fn handle_deep_link<R: tauri::Runtime>(app: &tauri::AppHandle<R>, raw: &str) {
    let link = match parse_deep_link(raw) {
        Ok(l) => l,
        Err(e) => {
            log::warn!("ignored deep link: {e}");
            return;
        }
    };
    let action = resolve(&app.state::<Shell>().servers.lock().unwrap(), &link);
    match action {
        DeepLinkAction::Connect { server_id, path } => {
            let server = app.state::<Shell>().server(&server_id);
            if let Some(server) = server {
                if let Err(e) = windows::open_main(app, &server, path.as_deref()) {
                    log::error!("deep link connect failed: {e}");
                    windows::show_connect(app, None);
                }
            }
        }
        DeepLinkAction::Prefill(prefill) => windows::show_connect(app, Some(prefill)),
    }
}

fn focus_any<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if !windows::show_main(app) {
        windows::show_connect(app, None);
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    install_crypto_provider();
    let context = tauri::generate_context!();
    let updater_enabled = updater::configured(context.config());

    let mut builder = tauri::Builder::default()
        // Must be first: a second launch (or a deep link on Windows/Linux) is
        // forwarded here, and to the deep-link plugin, instead of starting twice.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            focus_any(app)
        }))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_notification::init());
    if updater_enabled {
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
    }

    let app = builder
        .menu(menu::build)
        .on_menu_event(menu::handle)
        .invoke_handler(tauri::generate_handler![
            commands::servers_list,
            commands::servers_save,
            commands::servers_remove,
            commands::servers_probe,
            commands::servers_connect,
            commands::connect_prefill,
            commands::connect_back,
            commands::notify,
            commands::set_tray_status,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            app.manage(Shell::new(state::load(&handle)));
            tray::create(&handle)?;

            let on_action = handle.clone();
            app.notification().on_action(move |performed| {
                let extra = performed
                    .notification()
                    .map(|n| n.extra().clone())
                    .unwrap_or_default();
                commands::on_notification_action(&on_action, performed.action_id(), &extra);
            })?;

            #[cfg(any(windows, target_os = "linux"))]
            {
                // Installed bundles register the scheme; this covers dev runs.
                let _ = app.deep_link().register_all();
            }
            let links = handle.clone();
            app.deep_link().on_open_url(move |event| {
                for url in event.urls() {
                    handle_deep_link(&links, url.as_str());
                }
            });

            // The connect screen closes the app when nothing else is open.
            if let Some(connect) = app.get_webview_window(windows::CONNECT) {
                let h = handle.clone();
                connect.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        if h.get_webview_window(windows::MAIN).is_some() {
                            api.prevent_close();
                            windows::hide_connect(&h);
                        } else {
                            h.exit(0);
                        }
                    }
                });
            }

            if let Ok(Some(urls)) = app.deep_link().get_current() {
                for url in urls {
                    handle_deep_link(&handle, url.as_str());
                }
            }
            updater::schedule_startup_check(&handle);
            Ok(())
        })
        .build(context)
        .expect("error while building the GPU Deck desktop app");

    app.run(|app, event| match event {
        RunEvent::ExitRequested {
            code: None, api, ..
        } => {
            // Last window hidden/closed by the OS: keep the tray (and the hidden
            // main page that feeds notifications) alive. Quit is explicit.
            if app.get_webview_window(windows::MAIN).is_some() {
                api.prevent_exit();
            }
        }
        RunEvent::Exit => windows::persist_geometry(app),
        #[cfg(target_os = "macos")]
        RunEvent::Reopen {
            has_visible_windows: false,
            ..
        } => focus_any(app),
        _ => {}
    });
}
