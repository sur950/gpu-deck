//! Auto-update (tauri-plugin-updater), off unless the build supplied an
//! update endpoint and a minisign public key (`plugins.updater` in a config
//! overlay passed to `tauri build --config`; see docs/components/desktop.md).
//!
//! When enabled: one silent check shortly after start, and "Check for
//! Updates…" in the app menu. An update is downloaded, its signature verified
//! by the plugin against the embedded public key, installed, and the user is
//! told to restart.

use std::time::Duration;

use tauri::{AppHandle, Config, Manager, Runtime};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_updater::UpdaterExt;

/// True when the bundled config carries a usable updater section.
pub fn configured(config: &Config) -> bool {
    let Some(updater) = config.plugins.0.get("updater") else {
        return false;
    };
    let pubkey = updater
        .get("pubkey")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    let endpoints = updater
        .get("endpoints")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|e| e.as_str())
                .filter(|e| e.starts_with("https://"))
                .count()
        })
        .unwrap_or(0);
    !pubkey.is_empty() && endpoints > 0
}

pub fn enabled<R: Runtime>(app: &AppHandle<R>) -> bool {
    configured(app.config())
}

fn tell<R: Runtime>(app: &AppHandle<R>, title: &str, body: &str) {
    let _ = app.notification().builder().title(title).body(body).show();
}

/// Check for an update; `interactive` also reports "up to date" and errors.
pub fn check<R: Runtime>(app: AppHandle<R>, interactive: bool) {
    if !enabled(&app) {
        return;
    }
    tauri::async_runtime::spawn(async move {
        let updater = match app.updater() {
            Ok(u) => u,
            Err(e) => {
                log::warn!("updater unavailable: {e}");
                return;
            }
        };
        match updater.check().await {
            Ok(Some(update)) => {
                let version = update.version.clone();
                match update.download_and_install(|_, _| {}, || {}).await {
                    Ok(()) => tell(
                        &app,
                        "GPU Deck update installed",
                        &format!("Version {version} is ready. Restart GPU Deck to use it."),
                    ),
                    Err(e) => {
                        log::error!("update {version} failed: {e}");
                        if interactive {
                            tell(
                                &app,
                                "GPU Deck update failed",
                                "The update could not be installed.",
                            );
                        }
                    }
                }
            }
            Ok(None) => {
                if interactive {
                    let v = app.package_info().version.to_string();
                    tell(
                        &app,
                        "GPU Deck is up to date",
                        &format!("You are running version {v}."),
                    );
                }
            }
            Err(e) => {
                log::warn!("update check failed: {e}");
                if interactive {
                    tell(
                        &app,
                        "Update check failed",
                        "Could not reach the update server.",
                    );
                }
            }
        }
    });
}

pub fn schedule_startup_check<R: Runtime>(app: &AppHandle<R>) {
    if !enabled(app) {
        return;
    }
    let handle = app.app_handle().clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(15)).await;
        check(handle, false);
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config(updater: Option<serde_json::Value>) -> Config {
        let mut c = Config::default();
        if let Some(u) = updater {
            c.plugins.0.insert("updater".into(), u);
        }
        c
    }

    #[test]
    fn disabled_unless_pubkey_and_https_endpoint() {
        assert!(!configured(&config(None)));
        assert!(!configured(&config(Some(
            serde_json::json!({"pubkey": "", "endpoints": ["https://u.example.com"]})
        ))));
        assert!(!configured(&config(Some(
            serde_json::json!({"pubkey": "KEY", "endpoints": []})
        ))));
        assert!(!configured(&config(Some(
            serde_json::json!({"pubkey": "KEY", "endpoints": ["http://u.example.com"]})
        ))));
        assert!(configured(&config(Some(serde_json::json!({
            "pubkey": "KEY", "endpoints": ["https://u.example.com/{{target}}/{{arch}}/{{current_version}}"]
        })))));
    }
}
