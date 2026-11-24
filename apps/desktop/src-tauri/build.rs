//! Generates the ACL for the app's own commands: every command needs an
//! explicit `allow-<command>` permission in a capability (none is implicit).

fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "servers_list",
            "servers_save",
            "servers_remove",
            "servers_probe",
            "servers_connect",
            "connect_prefill",
            "connect_back",
            "notify",
            "set_tray_status",
        ]),
    ))
    .expect("failed to run tauri-build");
}
