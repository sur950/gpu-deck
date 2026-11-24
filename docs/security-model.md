# GPU Deck security

Base rule: **deny everything, then open only the paths below.** Nothing on a GPU machine is
reachable from the internet. Ports are defaults (see `libs/contracts/env.md`). This document maps
every network rule and control to where it is enforced. The vulnerability reporting policy is
[SECURITY.md](SECURITY.md).

## 1. Network rules

| From | To | Port | Rule | Enforced by |
| --- | --- | --- | --- | --- |
| AI gateway | API layer | 443 → 8181, TLS | Only the gateway's IPs, every call needs an API key | Ingress `sourceRanges` / Helm NetworkPolicy `api-layer` ingress CIDRs; in-app IP rules scope `api_layer` + `GATEWAY_IP_ALLOWLIST` (403 `ip_blocked`, checked before key lookup); API-key auth on every serving route |
| Admin | Control plane UI / admin API | 443 → 3180 / 8180, TLS | Private network or VPN only, never public | Internal ingress / Caddy; NetworkPolicies `web` + `admin-api` accept only the ingress controller; compose binds UI/admin to 127.0.0.1 unless `GPUDECK_BIND`; IP rules scope `admin` (block wins, any allow rule ⇒ allow-list mode) |
| Node agent | Agent hub | 8443, mutual TLS | Agent dials out; GPU machines need no inbound control port | Hub listener TLS 1.3, client cert required on `/agent/v1/ws` (CN = node id, chained to the internal CA, not in the CRL); NetworkPolicy `agent-hub` from node CIDRs only |
| API layer | Node agent proxy | 9443, mutual TLS | Only the API layer's certificate is accepted | Agent proxy requires a client cert of kind `api_layer` from the internal CA and checks the CRL; the API layer verifies the node cert chain against the routing table's CA bundle and CN == node id, and rejects CRL fingerprints |
| Node agent | Engines | localhost only | Engines never listen on a network interface | The agent launches engines bound to 127.0.0.1 on ports from `ENGINE_PORT_RANGE`; engine admin endpoints (llama.cpp load/unload/slots) are reachable only by the agent, never forwarded by the proxy |
| Control plane and jobs | PostgreSQL | 5432, TLS | Private network only | NetworkPolicy `data-egress` (database CIDRs); `POSTGRES_SSLMODE=verify-full` + `POSTGRES_SSLROOTCERT` |
| Control plane | Vault (secrets) | private, TLS | Private network only | The vault is the `credentials` table in the same private Postgres (Fernet / MultiFernet at rest, keys only in `VAULT_KEYS`) |
| GPU machines | Hugging Face, ModelScope | 443, TLS | Outbound only, or a mirror for offline sites | Agent egress lock (`egress_lock`: pf on macOS, nftables on Linux) with `EGRESS_ALLOWLIST_DEFAULT` + per-cluster `egress_allowlist` + the hub; `HF_ENDPOINT` / `MODELSCOPE_ENDPOINT` for mirrors; blocked attempts raise `egress_blocked` events |
| Admins | GPU machines | 22, SSH | Through a bastion or VPN, keys only | Site firewall / bastion (outside GPU Deck). SSH GPU instances expose sshd only through the agent on the node's private interface, keys only (`authorized_keys`, no passwords), optional `allowed_source_cidrs` |

Kubernetes: `infra/helm/gpu-deck/templates/networkpolicies.yaml` implements the default deny and
each row above for the control-plane pods (node agents use hostNetwork and are governed by the
node firewall).

## 2. Controls

| Control | Implementation |
| --- | --- |
| Machine join | Join tokens (`gdj_` + 40 hex, SHA-256 stored, TTL `JOIN_TOKEN_TTL_MINUTES` 15; one-time by default, or multi-use with `max_uses` and a TTL of up to a year for node pools; revocable; every join audited with the token used; rate limited `JOIN_RATE_LIMIT`). The install command pins the sha256 of `GET /agent/v1/ca.pem` before it fetches the installer from the hub. The agent sends a CSR; the hub signs it with the internal CA (key in the vault as `internal-ca`). Optional CA pinning with `--ca-fingerprint` against the unauthenticated `GET /agent/v1/ca` |
| Machine revoke | Revoking a cert closes the hub socket (4003), pushes the CRL to every agent proxy and drops the node from the routing table; certs rotate automatically 30 days before expiry |
| API keys | `gd-` + 48 hex, shown once, stored as SHA-256 only (`key_prefix` = first 12 chars for display). Revoke / suspend / rotate invalidates every API layer process at once (pub/sub `gpu-deck:apikeys:invalidate` + short local cache `API_KEY_LOCAL_CACHE_SECONDS`) |
| Admin password | Argon2id (`argon2-cffi`, `ARGON2_*`), min length `AUTH_PASSWORD_MIN_LENGTH`; seed password re-applied only when the `.env` value changes (stored as HMAC with `SECRET_KEY`) |
| Login limits | Lockout after `AUTH_LOCKOUT_ATTEMPTS` (5) for `AUTH_LOCKOUT_MINUTES` (15) per username; slowapi `RATE_LIMIT_LOGIN_PER_MINUTE` per client IP (forwarded headers trusted only from `TRUSTED_PROXIES`) |
| Sessions | Opaque Fernet tokens (SHA-256 stored), HttpOnly cookie `gpudeck_session`, `Secure` in production, `SameSite=Strict` in production / `Lax` in dev; hard expiry `SESSION_EXPIRE_HOURS` (12) + idle timeout `SESSION_IDLE_TIMEOUT_MINUTES`; password change revokes other sessions |
| CSRF | Pure-ASGI double-submit middleware: cookie `gpudeck_csrf` + `X-CSRF-Token` header, HMAC-bound to the session; bearer-token clients (CLI, Terraform) are exempt because they carry no ambient cookie |
| IP rules | `ip_rules` (scopes `admin`, `api_layer`), block wins, allow rules switch the scope to allow-list mode; cached in Redis + process memory; the API refuses a change that would lock out the caller; client IP from `X-Forwarded-For` only via `TRUSTED_PROXIES` |
| Secrets | Vault (`credentials`): Fernet/MultiFernet with rotation (`VAULT_KEYS`, hourly re-encrypt job); values never logged (`mask` helpers). Typed kinds (architecture.md §2): fields validated against a JSON Schema per kind; secret fields are write-only (list / get return a masked preview and the public fields only; edit replaces a secret only when it is typed again); the plaintext leaves the vault only to the consuming domain (each use writes `credential_usage_history` with its purpose) or through the reveal endpoint, which needs a confirmation in the UI and writes `credential.revealed` + a `reveal` usage row; `test` checks decryption, status and schema without returning anything secret. System credentials (internal CA key, hub / API-layer TLS keys) are hidden from listings and refused by update / delete / reveal (403 `CREDENTIAL_SYSTEM`). Deleting a credential that a cluster, alert channel, model or live cloud VM references is refused (409 `CREDENTIAL_IN_USE`). SSH keys are stored as public keys only |
| Audit | Every admin, rule, swap, key and vault action writes `audit_logs` (actor, IP, user agent, request id, before/after); export CSV; retention `AUDIT_RETENTION_DAYS`; filter by exact, category (`rule`), prefix or glob `event_type` |
| Request limits | Body caps (`MAX_REQUEST_BODY_BYTES`, `MAX_UPLOAD_BODY_BYTES`, `ADMIN_MAX_REQUEST_BODY_BYTES`) enforced before routing; serving timeouts per call site; no quotas on the serving path (the job of the AI gateway in front) |
| Response headers | Security headers on every response (nosniff, frame deny, HSTS, CSP, referrer and permissions policy); branding assets get a sandboxed CSP |
| Request ids | Inbound `X-Request-ID` echoed only when it is a UUID, otherwise a new one; ties audit, usage and traces |
| Containers | Compose / Helm run non-root with read-only root FS, tmpfs, dropped capabilities |
| Supply chain | Pinned Python deps (`requirements.txt`), lockfiles for web/agent/CLI; CI produces SBOMs and vulnerability scans for every image |

## 3. Desktop app (apps/desktop, Tauri 2 remote shell)

The desktop app loads a control plane's own web UI (a remote origin) in its main window, so
the webview IPC is locked down with Tauri 2 capabilities. Auth is unchanged: the session cookie
and CSRF double-submit live in the webview; native code never stores, reads or sends credentials.

| Surface | Allowed | Enforced by |
| --- | --- | --- |
| Remote server page (main window) → native | Only `notify` and `set_tray_status`, only from the origin of a **saved** server that is currently connected | Runtime capability per origin (`remote.urls = ["<origin>/*"]`, `local: false`, window/webview `main`, permissions `allow-notify` + `allow-set-tray-status`) added at connect time (`src-tauri/src/windows.rs::grant_ipc`); the app ACL (`build.rs` app manifest) makes every app command require an explicit `allow-<cmd>`; both handlers re-check window label, caller origin == connected origin, origin still saved |
| Remote page → plugins (fs, shell, opener, store, notification JS, updater, deep-link, event emit/listen) | Nothing | No plugin permission is granted to any remote origin; the opener plugin is not registered (Rust `open_url` only) |
| Connect screen (bundled, `connect` window) → native | 7 server-list commands, event listen/unlisten, window dragging | `capabilities/connect-screen.json` (`local: true`, `windows: ["connect"]`), handlers check the window label; CSP `default-src 'self'` (+ Fontshare CSS/fonts). `freezePrototype` stays off: Tauri injects it into every page, including the server's web UI, where it breaks libraries (recharts) |
| Payloads from the server page | Display text and in-app paths only | `status.rs`: control characters stripped, lengths capped (title 120, body 400, 5 tray items × 80), paths must resolve inside the server origin; notifications rate limited 10/min |
| Main window navigation | Same origin as the server; `blob:`/`data:` downloads go to the Downloads folder | `on_navigation` / `on_new_window`: other http(s) links open in the system browser, `target=_blank` never creates a window |
| Server URLs | `https://` by default; `http://` only for loopback, RFC 1918, CGNAT, link-local, IPv6 ULA, `localhost`, `*.local` (shown with a warning); no userinfo, path, query or fragment | `servers.rs::normalize_server_url` (re-applied when the store is loaded, so a hand-edited store cannot widen the allowlist); mirrored in the connect screen |
| Server check | Unauthenticated `GET /api/v1/branding` + `/api/v1/health`, no cookies, no redirects, 256 KB cap, 6 s timeout, OS trust store | `probe.rs` (reqwest + rustls platform verifier) |
| Deep links `gpudeck://open?server=…&path=…` | Connect only to saved servers; unknown servers open the connect screen prefilled (never auto-connect); path validated as above | `deeplink.rs` |
| Auto-update | Off unless built with an https endpoint and a minisign public key; artifacts verified against the embedded key | `updater.rs`, [desktop.md](components/desktop.md) |
| Persisted data | `servers.json` in the app config dir: names, URLs, last used, window geometry | `state.rs`; cookies stay in the OS webview store |

## 4. Reporting

Security issues: report them privately through GitHub security advisories (see [SECURITY.md](SECURITY.md)); do not open public issues.
