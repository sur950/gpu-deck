# GPU Deck Desktop (`apps/desktop`)

Tauri 2 desktop app in **remote-shell mode**. It does not bundle the Next.js UI: a small
native connect screen picks a GPU Deck control plane, then the main window loads that
server's own web UI. Same origin as in a browser, so the session cookie (`gpudeck_session`)
and the CSRF double-submit work unchanged, and the UI always matches the server version.

What the shell adds on top of a browser tab:

- Saved servers (name, URL, last used), checked through the public `GET /api/v1/branding`
  and `GET /api/v1/health` (app name, version, dependency checks) before connecting.
- Native notifications for alerts, rule runs and swap results; click focuses the window
  and opens the item.
- Tray icon (health dot) + menu: fleet health summary, active swaps, open alerts,
  Switch server, Quit.
- Menu: Switch server (⌘⇧S), Reload (⌘R), Open in browser (⌘⇧O), Zoom (⌘= / ⌘- / ⌘0),
  Developer tools (debug builds only). Window size, position and zoom are remembered per server.
- Deep links `gpudeck://open?server=<saved-name-or-url>&path=/deployments/<id>`.
- Single instance; optional signed auto-update.

## Layout

```
apps/desktop/
├── index.html, src/            connect screen (Vite + TypeScript, no framework)
│   ├── app.ts                  UI (plain DOM, text nodes only)
│   └── lib/{url,model,ipc}.ts  URL rules, view model, the 7 connect commands
├── tests/                      vitest (jsdom)
├── public/gpudeck-mark.svg     logo for the lockup
├── scripts/make-icons.py       icon sources from assets/brand
├── scripts/updater-config.mjs  writes the updater config overlay for release builds
└── src-tauri/                  Rust crate `gpudeck-desktop` (own Cargo.lock; not in the node-agent workspace)
    ├── tauri.conf.json         windows, CSP, bundle, deep-link scheme
    ├── capabilities/connect-screen.json   the only static capability (local connect window)
    ├── build.rs                app ACL manifest: every command needs an explicit allow-<cmd>
    └── src/
        ├── servers.rs          URL validation, saved server list (persisted model)
        ├── deeplink.rs         gpudeck:// parsing, in-app path validation
        ├── status.rs           notify / tray payload sanitising, rate limit
        ├── probe.rs            branding + health check (reqwest, no cookies, no redirects)
        ├── windows.rs          connect + main windows, runtime IPC grant, navigation policy
        ├── bridge.js           script injected into the main window (window.__GPUDECK_DESKTOP__)
        ├── commands.rs, tray.rs, menu.rs, updater.rs, state.rs, lib.rs
```

The web side of the bridge lives in `apps/web/lib/desktop/` (`host.ts`, `events.ts`,
`desktop-bridge.tsx`, mounted in `app/(dashboard)/layout.tsx`). It is a no-op in browsers.

## Develop

Prerequisites: Rust stable (1.85+), Node 20+ with pnpm; macOS: Xcode command line tools;
Linux: `libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev patchelf`;
Windows: WebView2 (preinstalled on 10/11) and MSVC build tools.

```bash
pnpm install                               # repo root (single lockfile)
pnpm --filter desktop tauri dev            # Vite on 127.0.0.1:1420 + the app (debug)
pnpm --filter desktop test                 # vitest: URL rules, view model, connect screen
pnpm --filter desktop type:check
cd apps/desktop/src-tauri && cargo test && cargo clippy --all-targets -- -D warnings
```

Against the local dev stack (`./infra/scripts/bootstrap.dev.sh bootstrap`): add
`http://localhost:3180` (http is allowed for local/private addresses, with a warning) and
set **Admin API URL** to `http://localhost:8180`, because in dev the web UI and the admin
API are on different ports. In Helm / production ingress both share one origin
(`/` → web, `/api` → admin API) and the field stays empty.

Persisted state: `servers.json` in the app config dir (macOS
`~/Library/Application Support/io.github.sur950.gpudeck/`, Windows `%APPDATA%\io.github.sur950.gpudeck\`,
Linux `~/.config/io.github.sur950.gpudeck/`). It holds names, URLs, last-used times and window
geometry, never credentials. Cookies live in the OS webview's data store.

Icons: `pnpm --filter desktop icons` (needs Pillow and `rsvg-convert`) regenerates
`src-tauri/icons/**` from `assets/brand` (app icon: rendered from `app-icon-1024.svg`; tray: `glyph-tray.svg`
+ health dot; see [BRAND.md](../BRAND.md)).

## Build

```bash
pnpm --filter desktop tauri build          # host platform bundles
pnpm --filter desktop build:mac            # macOS: .app via tauri, .dmg via scripts/make-dmg.sh
```

On macOS 26+/27 `hdiutil create` (used by Tauri's DMG step) fails with "no mountable file
systems"; `build:mac` bundles the `.app` with Tauri and builds the `.dmg` with
`diskutil image create from` (app + `/Applications` link). Signing/notarization env vars below
apply to the `.app` either way; sign the `.dmg` afterwards with `codesign -s "$APPLE_SIGNING_IDENTITY"`
and notarize it with `xcrun notarytool submit --wait` + `xcrun stapler staple`.

| Platform | Output (`src-tauri/target/release/bundle/`) |
| --- | --- |
| macOS | `macos/GPU Deck.app`, `dmg/GPU Deck_<version>_<arch>.dmg` |
| Windows | `msi/*.msi`, `nsis/*-setup.exe` |
| Linux | `appimage/*.AppImage`, `deb/*.deb` (and `rpm/` when `rpmbuild` exists) |

A release build target dir is ~1-3 GB; `cargo clean` (in `src-tauri`) after copying bundles out.
Universal macOS binary: `rustup target add x86_64-apple-darwin` then
`pnpm --filter desktop tauri build --target universal-apple-darwin`.

### macOS signing and notarization

Without signing, Gatekeeper blocks the downloaded app ("cannot be opened"); local builds run
ad-hoc signed. For distribution:

1. Apple Developer Program membership; create a **Developer ID Application** certificate
   (Xcode → Settings → Accounts → Manage Certificates, or developer.apple.com), export it as
   `.p12` with a password.
2. Notarization credentials: an app-specific password for the Apple ID
   (appleid.apple.com) + the Team ID, **or** an App Store Connect API key (`.p8`, key id, issuer id).
3. Build with:

```bash
export APPLE_CERTIFICATE="$(base64 -i developer-id.p12)"   # CI: import into a temp keychain
export APPLE_CERTIFICATE_PASSWORD=…
export APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export APPLE_ID=… APPLE_PASSWORD=<app-specific password> APPLE_TEAM_ID=TEAMID
#   or: APPLE_API_KEY=<key id> APPLE_API_ISSUER=<issuer id> APPLE_API_KEY_PATH=/path/AuthKey.p8
pnpm --filter desktop tauri build
```

The bundler signs with the hardened runtime (`bundle.macOS.hardenedRuntime`), submits to the
notary service, waits and staples the ticket to the `.app` and `.dmg`. Check with
`spctl -a -vv "GPU Deck.app"` and `xcrun stapler validate *.dmg`. No extra entitlements are
needed (no JIT, no camera/mic; outgoing network is allowed by default outside the sandbox).

### Windows signing

Unsigned installers trigger SmartScreen. Options:

- **OV/EV code-signing certificate** (`.pfx`): import into the runner's cert store and set
  `bundle.windows.certificateThumbprint` (SHA-1 thumbprint) via a `--config` overlay;
  `digestAlgorithm` sha256 and `timestampUrl` are already in `tauri.conf.json`.
- **Cloud HSM / Azure Trusted Signing / KMS**: set `bundle.windows.signCommand`
  (e.g. `trusted-signing-cli -e <endpoint> -a <account> -c <profile> %1`).

EV certificates get SmartScreen reputation immediately; OV builds reputation over time.

### Linux

AppImage and `.deb` are unsigned by default; publish SHA-256 sums (CI does). Optional GPG
signing of the AppImage: `SIGN=1 SIGN_KEY=<key id> APPIMAGETOOL_SIGN_PASSPHRASE=…`.

## Auto-update

`tauri-plugin-updater` is compiled in but **disabled** unless the build carries an update
endpoint and a public key (`plugins.updater` in the config). Plain `tauri build` ships with
it off, the "Check for Updates…" menu item greyed out and no network calls to any update host.

1. Generate the signing key pair once (keep the private key and password in the CI secret store):

   ```bash
   pnpm --filter desktop tauri signer generate -w ~/.tauri/gpudeck-updater.key
   # → ~/.tauri/gpudeck-updater.key (private, password-protected) and .key.pub (public)
   ```

2. Release build with the updater on:

   ```bash
   export GPUDECK_UPDATER_ENDPOINTS="https://downloads.example.com/gpudeck-desktop/{{target}}/{{arch}}/{{current_version}}"
   export GPUDECK_UPDATER_PUBKEY="$(cat ~/.tauri/gpudeck-updater.key.pub)"
   export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/gpudeck-updater.key)"
   export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=…
   pnpm --filter desktop release:config     # writes src-tauri/tauri.updater.conf.json (gitignored)
   pnpm --filter desktop tauri build --config src-tauri/tauri.updater.conf.json
   ```

   This also sets `bundle.createUpdaterArtifacts`, so the bundler emits the update archives
   (macOS `GPU Deck.app.tar.gz`, Windows the `.msi`/`-setup.exe`, Linux the `.AppImage`) each
   with a `.sig` file.

3. Serve the manifest. Either a dynamic endpoint (the URL template above, answering
   `204 No Content` when there is no update, or `200` with one platform entry), or one static
   JSON file listing every platform:

   ```json
   {
     "version": "0.2.0",
     "notes": "Tray shows deployment counts; bug fixes.",
     "pub_date": "2026-12-01T12:00:00Z",
     "platforms": {
       "darwin-aarch64": { "signature": "<contents of GPU Deck.app.tar.gz.sig>", "url": "https://downloads.example.com/0.2.0/GPU.Deck_aarch64.app.tar.gz" },
       "darwin-x86_64":  { "signature": "…", "url": "https://downloads.example.com/0.2.0/GPU.Deck_x64.app.tar.gz" },
       "windows-x86_64": { "signature": "…", "url": "https://downloads.example.com/0.2.0/GPU.Deck_0.2.0_x64-setup.exe" },
       "linux-x86_64":   { "signature": "…", "url": "https://downloads.example.com/0.2.0/gpu-deck_0.2.0_amd64.AppImage" }
     }
   }
   ```

   `signature` is the text of the `.sig` file, not a URL. Keys are `<os>-<arch>` (optionally
   `<os>-<arch>-<installer>`, e.g. `windows-x86_64-msi`). `version` must be SemVer and greater
   than the running version.

When enabled, the app checks 15 s after start and from the menu, downloads, verifies the
minisign signature against the embedded public key, installs, and tells the user to restart.
Endpoints must be https.

## Security model (summary; full text in [security-model.md](../security-model.md), section 3)

- The remote server origin gets IPC for exactly two commands, `notify` and
  `set_tray_status`, in the main window only, granted at connect time for that saved origin
  (`windows.rs::grant_ipc`); both handlers re-check window label + origin + "still saved".
  No plugin permission (fs, shell, opener, store, notification JS API, updater) is granted
  to any remote origin.
- The connect screen (local origin, `connect` window) gets its seven commands + event
  listen/unlisten + window dragging. Strict CSP. (`freezePrototype` is off on purpose: Tauri
  would inject it into the server's pages too, and the web UI's chart library breaks.)
- No credentials in native code: the probe sends no cookies and follows no redirects;
  the store holds names, URLs and geometry only.
- Main-window navigation is pinned to the server origin; other http(s) links open in the
  system browser; `target=_blank` never creates a window.
- Server strings shown natively are sanitised and capped; notification paths are validated
  in-app paths; notifications are rate limited (10/min).
- Deep links connect only to saved servers; anything else opens the connect screen prefilled.

## CI

`.github/workflows/ci.yml` job `desktop`: vitest + type-check + build, and `cargo fmt`,
`cargo test` and clippy on Linux. `.github/workflows/release.yml` job `desktop` builds the macOS
universal app + dmg, Windows msi + nsis and Linux AppImage + deb on GitHub-hosted runners; it signs
and notarizes when the Apple / Windows secrets exist and turns the updater on when
`TAURI_SIGNING_PRIVATE_KEY` and `GPUDECK_UPDATER_PUBKEY` are set (see [RELEASING.md](../RELEASING.md)).
