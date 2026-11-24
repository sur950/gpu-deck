# Environment variables (v1)

Every setting GPU Deck reads. The skeleton agent turns this into `.env.example` (grouped with these headers) and `app/core/settings.py` (pydantic-settings, `UPPER_SNAKE` field = env name, `Field(..., description=...)`, no default for the ones marked **required**). One `.env` feeds all three Python processes (`admin-api`, `api-layer`, `jobs`) and, via `NEXT_PUBLIC_*`, the web app. The Rust agent reads `agent.toml` plus the `GPUDECK_AGENT_*` variables listed in §13.

Legend: **R** required (no default), process column = which processes read it (`A` admin-api, `L` api-layer, `J` jobs/worker, `H` agent hub, `W` web, `N` node agent, `I` deployment tooling only — compose, Helm, scripts; not a backend setting).

**Drift check.** Every backend setting (`apps/fastapi/app/core/settings/*.py`) must have a row here and defaults must match; both `.env.example` files are generated from the settings classes. `python scripts/check_env_docs.py` (CI, `test:fastapi`) fails on drift; `--write` regenerates the examples and appends rows for new settings (then edit the Proc/description).

## 1. App

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `APP_NAME` | `GPU Deck` | A L J W | Product name (also branding) |
| `APP_VERSION` | `1.0.0` | A L J | Reported by `/system/info`, `/health`, OTel resource |
| `APP_ENV` | `development` | A L J W | `development` / `staging` / `production`; production turns on Secure cookies, TrustedHost, strict CSP |
| `DEBUG` | `false` | A L J | Enables `/docs`, error details in envelopes |
| `API_V1_PREFIX` | `/api/v1` | A W | Admin API prefix |
| `LOG_LEVEL` | `INFO` | A L J | loguru level |
| `LOG_FORMAT` | `text` | A L J | `json` or `text` |
| `LOG_FILE_PATH` | `logs/app.log` | A L J | Rotating file sink (best effort) |
| `LOG_ROTATION` / `LOG_RETENTION` | `500 MB` / `10 days` | A L J | loguru rotation/retention |
| `TZ` | `UTC` | all | Process timezone; DB and API are always UTC |

## 2. Ports and URLs

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `HOST` / `PORT` | `0.0.0.0` / `8180` | A L | Bind host of every HTTP listener / admin API port (aliases `ADMIN_HOST` / `ADMIN_PORT`) |
| `API_LAYER_PORT` | `8181` | L | Serving API port (binds `HOST`) |
| `WEB_PORT` | `3180` | W | Next.js dev/start port |
| `AGENT_HUB_HOST` / `AGENT_HUB_PORT` | `0.0.0.0` / `8443` | H | mTLS WebSocket + join listener; its own process (`python -m app.agent_hub_main`), other processes reach agents through Redis (`gpu-deck:hub:dispatch` / `gpu-deck:hub:results`) |
| `AGENT_HUB_PUBLIC_URL` | `https://localhost:8443` | A | The hub URL exactly as GPU machines reach it: embedded in join and install commands and hello_ack, and the default origin of the installer and agent downloads (`<url>/agent/v1/...`), so GPU machines need only outbound 8443 |
| `AGENT_PROXY_PORT` | `9443` | A L N | Default node proxy port (per node override in DB) |
| `PUBLIC_API_URL` | `http://localhost:8180` | A W | Public admin API URL: alert links, agent downloads, TrustedHost (alias `ADMIN_PUBLIC_URL`) |
| `PUBLIC_SERVING_URL` | `http://localhost:8181` | A J W | Public serving URL: API keys screen, benchmarks (alias `API_LAYER_PUBLIC_URL`) |
| `FRONTEND_URL` | `http://localhost:3180` | A | Cookie/CORS origin |
| `CORS_ORIGINS` | `http://localhost:3180` | A | Comma list |
| `TRUSTED_HOSTS` | `localhost,127.0.0.1` | A L | Production Host-header allow-list extras (hostnames of CORS_ORIGINS / PUBLIC_API_URL / PUBLIC_SERVING_URL always allowed); `/health*`, `/api/v1/health/*`, `/metrics`, `/api/v1/metrics/prometheus` are never host-checked |
| `GPU_DECK_CONTRACTS_DIR` | *(empty)* | I | Directory holding `agent-protocol/` and `rules/` schemas when the backend does not run from the repo layout (images); `GPU_DECK_RULES_SCHEMA_DIR` overrides `rules/` alone |
| `CORS_CREDENTIALS` / `CORS_METHODS` / `CORS_HEADERS` | `true` / `*` / `*` | A | |
| `WORKERS` | `4` | A L | gunicorn workers (production) |
| `RELOAD` | `false` | A L | uvicorn reload (dev) |
| `PROMETHEUS_PORT` | `0` | I | Separate `/metrics` port; `0` = serve on the main port |
| `PROMETHEUS_SCRAPE_TOKEN` | *(empty)* | A L J | Bearer token required on `/metrics` (admin API, API layer, agent hub) and `/api/v1/metrics/prometheus` when set; the worker / scheduler listeners (`WORKER_METRICS_PORT`, `SCHEDULER_METRICS_PORT`) are not token-protected — keep them on the private network |
| `SCHEDULER_EMBEDDED` | `false` | A L J | Run the APScheduler loop inside the admin API process instead of the dedicated jobs process (python -m app.workers.scheduler). Single-container only. |

## 3. Branding (white label; `NEXT_PUBLIC_*` mirrors are read by the web app at build time)

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `APP_BYLINE` | `GPU control plane` | A W | Descriptor line under the logo |
| `BRAND_COLOR` | `#B3122E` | A W | Primary accent color (buttons, links, active nav, focus, chart slot 1). The default is the GPU Deck crimson, with its dark-mode step `#D3263F`; any other hex is used as-is in both modes (docs/BRAND.md) |
| `LOGO_URL` | `/branding/logo.svg` | A W | Absolute or app-relative; the admin API serves `static/logo.svg` at `/branding/logo.svg` |
| `FAVICON_URL` | `/branding/favicon.svg` | A W | |
| `DOCS_URL` / `SUPPORT_URL` | *(empty)* | A W | Optional links in the shell |
| `NEXT_PUBLIC_APP_NAME` | `GPU Deck` | W | Mirror of `APP_NAME` |
| `NEXT_PUBLIC_APP_BYLINE` | `GPU control plane` | W | Mirror |
| `NEXT_PUBLIC_BRAND_COLOR` | `#B3122E` | W | Mirror |
| `NEXT_PUBLIC_LOGO_URL` / `NEXT_PUBLIC_FAVICON_URL` | as above | W | Mirror |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8180/api/v1` | W | Admin API base for the browser |
| `NEXT_PUBLIC_API_LAYER_URL` | `http://localhost:8181` | W | Shown in the API keys screen |
| `NEXT_PUBLIC_LIVE_TRANSPORT` | `sse` | W | `sse` or `ws` for live dashboards |
| `NEXT_PUBLIC_DOCS_URL` / `NEXT_PUBLIC_SUPPORT_URL` | *(empty)* | W | Mirror |
| `BRAND_NAME` | *(empty)* | A L J | Product name shown in the UI (defaults to APP_NAME) |
| `BRAND_COMPANY` | *(empty)* | A L J | Company name for footers (optional) |
| `BRAND_LOGO_DARK_URL` | *(empty)* | A L J | Logo URL for the dark theme (empty = same as BRAND_LOGO_URL) |

`GET /api/v1/branding` returns the server-side values so the login page works before any build-time config.

## 4. Admin seed and auth

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `ADMIN_USERNAME` | **R** | A | Seeded admin username (synced on every boot) |
| `ADMIN_PASSWORD` | **R** | A | Seed password; re-applied only when its SHA-256 differs from `admin_users.env_secret_hash` |
| `ADMIN_DISPLAY_NAME` | `Administrator` | A | |
| `SECRET_KEY` | **R** (≥ 32 chars) | A L J | Session Fernet key derivation, CSRF HMAC, join-command signing |
| `SESSION_COOKIE_NAME` | `gpudeck_session` | A W | |
| `SESSION_COOKIE_DOMAIN` | *(empty)* | A | Optional parent domain |
| `SESSION_EXPIRE_HOURS` | `12` | A | Short sessions by default |
| `SESSION_IDLE_TIMEOUT_MINUTES` | `60` | A | Sliding idle expiry (0 = off) |
| `CSRF_COOKIE_NAME` | `gpudeck_csrf` | A W | Readable double-submit cookie |
| `CSRF_HEADER_NAME` | `X-CSRF-Token` | A W | |
| `AUTH_LOCKOUT_ATTEMPTS` | `5` | A | Failed logins before lockout |
| `AUTH_LOCKOUT_MINUTES` | `15` | A | Lockout duration |
| `AUTH_PASSWORD_MIN_LENGTH` | `12` | A | For change-password |
| `AUTH_REQUIRE_PASSWORD_CHANGE` | `false` | A | Force a change after first login with the seed |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_KIB` / `ARGON2_PARALLELISM` | `3` / `65536` / `2` | A | argon2-cffi parameters |
| `RATE_LIMIT_ENABLED` | `true` | A | slowapi on admin routes |
| `RATE_LIMIT_PER_MINUTE` | `120` | A | Default per session/IP |
| `RATE_LIMIT_LOGIN_PER_MINUTE` | `10` | A | Login endpoint |
| `SESSION_COOKIE_SECURE` | *(empty)* | A L J | Force the Secure cookie flag (default: only in production) |
| `CSRF_ENABLED` | `true` | A L J | Enforce CSRF on state-changing requests |
| `API_KEY_PREFIX` | `gd-` | A L J | Prefix of generated API keys (serving.md §3: gd- + 48 hex). Keys issued under an older prefix keep working: lookup is by SHA-256 of the full key |
| `API_KEY_LOCAL_CACHE_SECONDS` | `1.0` | A L J | Process-local cache of verified API keys (bounded by the invalidation pub/sub); keeps the hot path memory-only |
| `IP_ALLOWLIST` | *(empty)* | A L J | Admin API implicit allow rules |
| `IP_BLOCKLIST` | *(empty)* | A L J | Admin API implicit block rules |
| `TRUSTED_PROXIES` | `127.0.0.1/32,::1/128` | A L J | Comma-separated IPs/CIDRs of reverse proxies (Caddy, load balancer) whose X-Forwarded-For / X-Real-IP headers are believed when evaluating IP rules. Headers from any other peer are ignored so clients cannot spoof their address. |
| `IP_RULES_LOCAL_CACHE_SECONDS` | `2.0` | A L J | Process-local snapshot age before re-reading Redis |
| `AGENT_HUB_CLIENT_CA_FILE` | *(empty)* | A L J | Internal CA that signs node-agent client certificates (mTLS) |
| `PKI_CA_DIR` | `certs` | A L J | Directory holding the internal CA material (dev) |

## 5. Database (Postgres; local dev: the `postgres` container in `infra/docker/docker-compose.deps.yml`, 127.0.0.1:55432)

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `DATABASE_URL` | **R** (or `POSTGRES_*`) | A L J | `postgresql://user:pw@host:5432/db`; app rewrites to `+asyncpg`, alembic to `+psycopg` |
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_SCHEMA` | `127.0.0.1` / `5432` / — / — / `gpu_deck_dev` / `public` | A L J | Alternative to `DATABASE_URL` |
| `POSTGRES_SSLMODE` / `POSTGRES_SSLROOTCERT` | *(empty)* / *(empty)* | A L J | TLS to Postgres (`verify-full` in production; empty = driver default `prefer`). Added to the URL as `ssl=` (asyncpg) / `sslmode=` + `sslrootcert=` (psycopg) unless `DATABASE_URL` already has one; the root CA is also exported as `PGSSLROOTCERT` |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` / `DB_POOL_RECYCLE` / `DB_POOL_PRE_PING` | `5` / `10` / `60` / `3600` / `true` | A L J | Per process; jobs/Celery use NullPool |
| `DB_ECHO` | `false` | A L J H | Log every SQL statement (debugging only; independent of `DEBUG`) |
| `DB_STATEMENT_TIMEOUT_MS` / `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` / `DB_IDLE_SESSION_TIMEOUT_MS` | `60000` / `30000` / `300000` | A L J | Server settings per connection |
| `DB_METRICS_INGEST_BATCH_SIZE` | `2000` | A | Rows per COPY/insert batch in the hub |

## 6. Redis (local dev: the `redis` container in `infra/docker/docker-compose.deps.yml`, 127.0.0.1:56379)

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `REDIS_URL` | **R** (or `REDIS_*`) | A L J | `redis://user:pw@127.0.0.1:6379/0` |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` / `REDIS_USERNAME` / `REDIS_PASSWORD` | `127.0.0.1` / `6379` / `0` / — / — | A L J | Alternative |
| `REDIS_KEY_PREFIX` | `gpu-deck:` | A L J | All keys, streams and pub/sub channels: `gpu-deck:<domain>:<event-or-key>` (the dev Redis ACL user may only touch keys under `gpu-deck:`) |
| `CACHE_TTL_CREDENTIALS_SECONDS` | `86400` | A J | Vault ciphertext cache |
| `API_KEY_CACHE_TTL_SECONDS` | `60` | L | |
| `IP_RULES_CACHE_TTL_SECONDS` | `30` | A L | |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | = Redis URL | A L J | |
| `CELERY_TASK_TIME_LIMIT` / `CELERY_MAX_RETRIES` | `300` / `3` | J | |
| `CELERY_QUEUES` | *(all discovered queues)* | I | Worker `-Q` list in compose/Helm/bootstrap = `app.workers.celery_app.queue_names()` (every task module's `QUEUE`, e.g. `metering`, `billing`, `alerts`, `catalog`, `cloud`) |
| `FLOWER_PORT` / `FLOWER_BASIC_AUTH` | `5555` / *(empty)* | I | Optional Flower container |
| `BUILD_SHA` | *(empty)* | A L J | Git SHA of the running build (CI sets it) |
| `AGENT_PROTOCOL_VERSION` | `1` | A L J | Agent protocol major (libs/contracts/agent-protocol envelope.v) |
| `CELERY_TASK_TRACK_STARTED` | `true` | A L J | Track task start |
| `CELERY_TASK_RESULT_EXPIRES` | `86400` | A L J | Result TTL (s) |
| `CELERY_SEND_EVENTS` | `false` | A L J | Emit worker/task events (needed only for Flower) |

## 7. Vault and PKI

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `VAULT_KEYS` | **R** | A L J | Comma-separated Fernet keys, newest first (`MultiFernet`); generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Rotation: prepend a new key and restart; the hourly `vault.rotate_keys` job re-encrypts every credential still on an old key (audit `vault.keys_rotated`), then drop the old key |
| `PKI_CA_CERT_PATH` / `PKI_CA_KEY_PATH` | *(empty)* | A | Import an existing CA on first boot (read once, stored in vault as `internal-ca`); otherwise a CA is generated (`CN=GPU Deck Internal CA`, 10 years) |
| `PKI_CA_KEY_TYPE` | `ecdsa_p384` | A | For a generated CA |
| `NODE_CERT_VALIDITY_DAYS` | `365` | A | |
| `NODE_CERT_ROTATE_BEFORE_DAYS` | `30` | J | Auto rotate_cert |
| `SERVICE_CERT_VALIDITY_DAYS` | `365` | A | api_layer / control_plane certs |
| `JOIN_TOKEN_TTL_MINUTES` | `15` | A | Default for `POST /nodes/join-tokens` (one-time tokens live at most 1440 minutes; multi-use enrolment tokens, `max_uses` > 1, up to a year) |
| `AGENT_HUB_TLS_CERT_FILE` / `AGENT_HUB_TLS_KEY_FILE` | *(empty)* | H | (aliases `AGENT_HUB_TLS_CERT_PATH` / `_KEY_PATH`) Hub server cert; empty = issued by the internal CA at boot (SANs from `AGENT_HUB_PUBLIC_URL` + `AGENT_HUB_TLS_SANS`) and stored in vault `agent-hub-tls` |
| `AGENT_HUB_TLS_SANS` | `localhost,127.0.0.1` | A | Extra SANs |
| `API_LAYER_CLIENT_CERT_PATH` / `API_LAYER_CLIENT_KEY_PATH` | *(empty)* | L J | Client cert for the node proxies (:9443). Empty (or missing files) = issued by the internal CA on first start, kept in the vault (`api-layer-tls`) and written to `PKI_CA_DIR` as `api-layer.crt` / `.key`; set them only to bring your own |
| `API_LAYER_CA_BUNDLE_PATH` | *(empty)* | L | Override the CA bundle from the routing table (air-gapped bootstrap) |
| `CONTROL_PLANE_CLIENT_CERT_PATH` / `CONTROL_PLANE_CLIENT_KEY_PATH` | *(empty)* | A J | For control-plane → proxy health probes/benchmarks; empty = issued at boot and stored in vault |
| `TLS_CERT_FILE` / `TLS_KEY_FILE` / `TLS_CA_FILE` | *(empty)* / *(empty)* / *(empty)* | A L | Serve HTTPS directly from the process that is given them (set per process; empty = plain HTTP behind Caddy/ingress). Aliases `ADMIN_TLS_CERT_PATH` / `ADMIN_TLS_KEY_PATH` |

## 8. Agent hub and fleet

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `NODE_OFFLINE_AFTER_SECONDS` | `30` | A | No heartbeat → offline |
| `HEARTBEAT_INTERVAL_SECONDS` | `10` | A | Sent to agents in hello_ack |
| `METRICS_INTERVAL_SECONDS` | `2` | A | Scrape/batch interval sent to agents |
| `INVENTORY_INTERVAL_SECONDS` | `60` | A | |
| `COMMAND_ACK_TIMEOUT_SECONDS` | `10` | A | |
| `COMMAND_DEFAULT_TIMEOUT_SECONDS` | `900` | A J | |
| `HUB_MAX_FRAME_BYTES` | `4194304` | A | 4 MiB |
| `EGRESS_ALLOWLIST_DEFAULT` | `huggingface.co:443,cdn-lfs.huggingface.co:443,cdn-lfs-us-1.huggingface.co:443,cas-bridge.xethub.hf.co:443,modelscope.cn:443,www.modelscope.cn:443` | A | Base egress allowlist pushed to every node (plus the hub itself and `clusters.egress_allowlist`) |
| `HF_ENDPOINT` / `MODELSCOPE_ENDPOINT` | `https://huggingface.co` / `https://www.modelscope.cn` | A J | Mirrors for offline sites; pushed in `config_push.model_sources` |
| `HF_TOKEN_CREDENTIAL` / `MODELSCOPE_TOKEN_CREDENTIAL` | `huggingface-token` / `modelscope-token` | A J | Vault credential names used for catalog search and downloads (optional) |
| `CATALOG_SEARCH_CACHE_SECONDS` | `300` | A | |
| `LOCAL_MODEL_SCAN_ROOTS` | `~/.ollama/models,~/.lmstudio/models,~/.cache/huggingface/hub,/models` | A | Default scan roots offered in the UI |
| `ENGINE_PORT_RANGE` | `10240-10340` | A | Default per engine version |
| `SCHEDULER_VRAM_HEADROOM_PCT` | `10` | A J | Free VRAM kept per device when placing |
| `SCHEDULER_KV_BYTES_PER_TOKEN_FALLBACK` | `131072` | A J | When model config lacks shape info |
| `HUB_VALIDATE_SCHEMAS` | `true` | A H J | Validate every inbound frame against libs/contracts/agent-protocol JSON Schemas (close 4002 on violation) |
| `HUB_PING_INTERVAL_SECONDS` | `15` | A H J | WebSocket ping cadence |
| `PKI_CA_VALIDITY_DAYS` | `3650` | A H J | Generated CA validity |
| `NODE_CERT_OLD_REVOKE_AFTER_SECONDS` | `600` | A H J | Revoke the previous cert this long after the new one connects |
| `JOIN_RATE_LIMIT` | `10/minute` | A H J | Rate limit on /agent/v1/join |
| `COMMAND_MIN_AGENT_VERSIONS` | *(empty)* | A H J | Comma list command=semver of the minimum agent version per command (e.g. run_benchmark=1.1.0); unsupported commands are rejected before sending |

## 9. Rules, swaps, HA

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `RULES_TICK_SECONDS` | `10` | J | Evaluator tick |
| `RULES_DEFAULT_TIMEZONE` | `UTC` | A J W | Default for time_schedule / windows |
| `RULES_MAX_CONCURRENT_SWAPS` | `2` | J | |
| `RULE_RUN_NO_MATCH_LOG_INTERVAL_SECONDS` | `3600` | J | |
| `RULE_RUNS_RETENTION_DAYS` | `30` | J | |
| `SWAP_DEFAULT_DRAIN_TIMEOUT_SECONDS` | `120` | A J | |
| `SWAP_MAX_DURATION_SECONDS` | `1800` | J | |
| `SWAP_FORCE_AFTER_DRAIN_TIMEOUT` | `false` | J | Default `force_after_timeout` for rule-triggered swaps |
| `HA_LEASE_TTL_SECONDS` / `HA_LEASE_RENEW_SECONDS` | `30` / `10` | A J | Leader election |
| `HA_INSTANCE_ID` | hostname+pid | A J | Lease holder id |
| `RULES_SCHEDULE_LOOKBACK_HOURS` | `48` | J | How far back a time_schedule rule looks for a missed run on start-up (catch_up); longer gaps are ignored |
| `SWAP_COMMAND_POLL_SECONDS` | `1.0` | A J | How often the swap manager polls agent_commands for a result |
| `SWAP_DEVICE_LOCK_TTL_SECONDS` | `2400` | A J | TTL of the per-device Redis lock held while a swap runs |
| `SWAP_DRIVE_INTERVAL_SECONDS` | `5` | A J | How often the jobs leader looks for pending/resumable swaps |
| `SWAP_HEALTH_PROBE_TIMEOUT_SECONDS` | `5.0` | A J | Timeout of the control-plane /_health probe through the proxy |

## 10. Metrics, logs, traces, retention

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `METRICS_RAW_RETENTION_HOURS` | `48` | J | `metric_samples_*` partitions |
| `METRICS_ROLLUP_RETENTION_DAYS` | `90` | J | `metric_rollups_*_1m` |
| `LOGS_RETENTION_DAYS` | `14` | J | `log_chunks` |
| `TRACES_RETENTION_DAYS` | `7` | J | `traces` |
| `USAGE_EVENTS_RETENTION_DAYS` | `400` | J | `usage_events` |
| `AUDIT_RETENTION_DAYS` | `365` | J | `audit_logs` row deletes (0 = keep forever) |
| `ALERT_EVENTS_RETENTION_DAYS` | `90` | J | |
| `PARTITIONS_DAYS_AHEAD` | `7` | J | `create_daily_partitions()` horizon (hourly job) |
| `METRICS_LIVE_STALE_SECONDS` | `10` | A | After this the `live` fields read null |
| `LOG_CHUNK_MAX_LINES` / `LOG_FLUSH_INTERVAL_MS` | `100` / `1000` | A N | Agent log batching (pushed in settings) |
| `LOG_FORWARD_ENGINE_OUTPUT` | `true` | A N | |
| `ALERT_DEDUPE_WINDOW_SECONDS` | `600` | J | |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | *(empty)* | A L J | Empty = tracing to DB index only |
| `OTEL_SERVICE_NAME` | per process (`gpudeck-admin-api` …) | A L J | |
| `OTEL_TRACES_SAMPLER` / `OTEL_TRACES_SAMPLER_ARG` | `always_on` / `0.1` | A L J | |
| `OTEL_UI_URL` | *(empty)* | A W | Deep link template `{trace_id}` |
| `GRAFANA_URL` | *(empty)* | A W | Link in the UI |
| `AUDIT_EXPORT_MAX_ROWS` | `50000` | A J | Upper bound of rows in one CSV export |
| `AUDIT_QUEUE` | `audit` | A J | Celery queue for audit writes |
| `METRICS_QUERY_MAX_POINTS` | `5000` | A H J | Max buckets per series in /metrics/* (from/to/step guard) |
| `METRICS_ROLLUP_CATCHUP_MINUTES` | `120` | A H J | How far back the minute rollup job backfills on a gap |
| `LOGS_QUERY_MAX_LIMIT` | `1000` | A H J | Max rows per /logs page |
| `LIVE_EVENTS_REPLAY_SECONDS` | `60` | A H J | Replay window honoured for Last-Event-ID on /live/events |
| `LIVE_HEARTBEAT_SECONDS` | `15` | A H J | SSE heartbeat cadence |
| `TRACE_INDEX_ENABLED` | `true` | A H J | Write a span index into the traces table |
| `SERVING_ACCESS_LOG_SAMPLE_RATE` | `0.01` | A L J | Serving API: fraction of requests that get an INFO access-log line (the rest log at DEBUG; 5xx always log at WARNING) |
| `METRICS_ENABLED` | `true` | A L J | Expose Prometheus /metrics |
| `WORKER_METRICS_PORT` | `9464` | J | Prometheus listener of the Celery worker (`celery_tasks_total{task,state}`); 0 = off. Prefork pools need `PROMETHEUS_MULTIPROC_DIR` |
| `SCHEDULER_METRICS_PORT` | `9465` | J | Prometheus listener of the jobs/scheduler process (`gpudeck_rules_*`, `gpudeck_swaps_*`, `gpudeck_jobs_leader`); 0 = off |
| `METRICS_BIND_HOST` | `0.0.0.0` | J | Bind address of the worker/scheduler listeners (dev: `127.0.0.1`) |
| `OTEL_CONSOLE_EXPORT` | `false` | A L J | Print spans to stdout when no OTLP endpoint is set |
| `HEALTH_CHECK_ENABLED` | `true` | A L J | Enable health checks |
| `HEALTH_CHECK_INTERVAL_SECONDS` | `30` | A L J | Health check interval |

## 11. Serving API layer

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `GATEWAY_IP_ALLOWLIST` | *(empty)* | L | Comma CIDRs allowed to call the API layer (empty = any, rely on `ip_rules`); production should set it to the IPs of the AI gateway in front of GPU Deck |
| `SERVING_OPENAI_PREFIX` / `SERVING_ANTHROPIC_PREFIX` / `SERVING_OLLAMA_PREFIX` | `/openai` / `/anthropic` / `/ollama` | L | |
| `SERVING_ENABLE_BARE_PATHS` | `true` | L | Serve `/v1/*` and `/api/*` without prefix |
| `SERVING_LIST_UNLOADED_MODELS` | `false` | L | |
| `SERVING_ON_DEMAND_LOAD` | `false` | L | |
| `SERVING_NO_INSTANCE_WAIT_SECONDS` | `30` | L | |
| `SERVING_EJECT_SECONDS` | `10` | L | |
| `SERVING_REWRITE_RESPONSE_MODEL` | `true` | L | |
| `SERVING_EXPOSE_ROUTING_HEADERS` | `true` | L | |
| `SERVING_MAX_JSON_BODY_BYTES` | `16777216` | L | 16 MiB |
| `SERVING_MAX_UPLOAD_BYTES` | `268435456` | L | 256 MiB |
| `SERVING_MAX_INPUT_ITEMS` | `2048` | L | |
| `SERVING_REQUEST_TIMEOUT_SECONDS` | `600` | L | |
| `SERVING_STREAM_IDLE_TIMEOUT_SECONDS` | `120` | L | |
| `SERVING_CONNECT_TIMEOUT_SECONDS` | `5` | L | |
| `SERVING_VIDEO_JOB_TIMEOUT_SECONDS` | `3600` | L J | |
| `SERVING_MAX_CONCURRENT_REQUESTS` | `1024` | L | |
| `SERVING_BATCH_MAX_ITEMS` / `SERVING_BATCH_MAX_BYTES` / `SERVING_BATCH_EXPIRES_HOURS` / `SERVING_BATCH_RESULTS_RETENTION_DAYS` / `SERVING_BATCH_CONCURRENCY` | `10000` / `268435456` / `24` / `29` / `4` | L J | |
| `SERVING_ANTHROPIC_DEFAULT_VERSION` | `2023-06-01` | L | |
| `SERVING_COMPACTION_MODEL_OVERRIDE` | *(empty)* | L | Use this model for compaction instead of the request's model |
| `ROUTING_TABLE_REFRESH_SECONDS` | `2` | A | Push cadence incl. live sessions |
| `ROUTING_TABLE_POLL_SECONDS` | `5` | L | Fallback poll |
| `METERING_ROLLUP_INTERVAL_MINUTES` | `5` | J | |
| `MAX_REQUEST_BODY_BYTES` | `33554432` | A L | Maximum request body (bytes) on the serving API |
| `MAX_UPLOAD_BODY_BYTES` | `268435456` | A L | Maximum multipart body (bytes) for audio/image upload endpoints |
| `REQUEST_TIMEOUT_SECONDS` | `600` | A L | Upper bound for one non-streaming serving request |
| `STREAM_IDLE_TIMEOUT_SECONDS` | `120` | A L | Abort a stream when the engine sends nothing for this long |
| `ENGINE_CONNECT_TIMEOUT_SECONDS` | `5.0` | A L | TCP/TLS connect timeout to the node-agent proxy |
| `ADMIN_MAX_REQUEST_BODY_BYTES` | `4194304` | A L | Maximum request body (bytes) on the admin API and the agent hub listener (join POST) |
| `SERVING_COMPACTION_MAX_TOKENS` | `2048` | L | max_tokens of the summarisation call the API layer runs |
| `SERVING_INFLIGHT_FLUSH_MS` | `250` | L | How often per-instance in-flight counts are flushed to Redis |
| `SERVING_HEALTH_REQUIRE_NODES` | `false` | L | /health answers 503 unless the routing table lists >= 1 online node (serving.md); off by default so an empty dev stack is healthy |
| `SERVING_UPSTREAM_VERIFY_TLS` | `true` | L | Verify node-agent proxy certificates (dev only: false) |
| `SERVING_UPSTREAM_KEEPALIVE_SECONDS` | `300.0` | L | Idle time a pooled connection to a node-agent proxy is kept for reuse (a new one costs TCP + TLS round trips) |
| `SERVING_UPSTREAM_HTTP2` | `true` | L | Offer HTTP/2 (ALPN h2) to node-agent proxies; one connection multiplexes concurrent requests. HTTP/1.1 is used when a proxy does not negotiate h2 |
| `SERVING_UPSTREAM_MAX_CONNECTIONS_PER_NODE` | `512` | L | Connection cap of the pool towards one node-agent proxy |
| `SERVING_UPSTREAM_MAX_KEEPALIVE_PER_NODE` | `128` | L | Idle connections kept per node-agent proxy |
| `SERVING_UPSTREAM_PREWARM` | `true` | L | Open connections to a node proxy as soon as it serves an instance and keep idle pools warm |
| `SERVING_UPSTREAM_WARM_CONNECTIONS` | `2` | L | Connections opened per node proxy when it is first routed |
| `SERVING_UPSTREAM_KEEPWARM_SECONDS` | `20` | L | Interval of the GET /_health ping that keeps each routed node's pool warm (0 = off; keep it below the agent's 30 s HTTP/2 keep-alive ping) |
| `SERVING_DEV_ENGINE_URL` | *(empty)* | L | Direct engine base URL for local testing (e.g. http://127.0.0.1:11434); requests go to {url}/{engine_path} without a node-agent proxy |
| `SERVING_DEV_ENGINE_MODELS` | `*` | L | Comma-separated serving names the dev engine answers to ('*' = any) |
| `SERVING_DEV_ENGINE_FORMATS` | `openai` | L | Formats the dev engine speaks natively (openai,ollama) |
| `SERVING_DEV_ENGINE_FAMILY` | *(empty)* | L | Model family hint for the dev engine (thinking tag handling) |
| `SERVING_DEV_ENGINE_TYPE` | `chat` | L | Model type of the dev engine model(s) |

## 12. Alerts and billing

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `ALERT_DEFAULT_MIN_SEVERITY` | `warning` | A J | |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD_CREDENTIAL` / `SMTP_FROM` / `SMTP_USE_TLS` | *(empty)* / `587` / *(empty)* / `smtp-password` / `gpudeck@localhost` / `true` | J | Defaults for email channels (channel config overrides); password lives in the vault under the named credential |
| `SLACK_DEFAULT_WEBHOOK_CREDENTIAL` | *(empty)* | J | Vault credential name used when a Slack channel has no credential_id |
| `WEBHOOK_SIGNING_SECRET_CREDENTIAL` | *(empty)* | J | HMAC-SHA256 `X-GPUDeck-Signature` for webhook channels |
| `ALERT_SEND_TIMEOUT_SECONDS` / `ALERT_MAX_ATTEMPTS` | `10` / `3` | J | |
| `BILLING_CURRENCY` | `USD` | A J | Display only; prices are USD |
| `BILLING_MONTHLY_REPORT_DAY` | `1` | J | Auto-generate last month's report on this day (0 = off) |
| `ALERT_RETRY_BACKOFF_SECONDS` | `30` | A J | Base backoff between delivery attempts (doubles per attempt) |

## 13. Node agent (`agent.toml` keys, overridable by env `GPUDECK_AGENT_<KEY>`)

| Key / env | Default | Description |
| --- | --- | --- |
| `hub_url` / `GPUDECK_AGENT_HUB_URL` | — (set by `join`) | `https://host:8443` |
| `node_id` | set by `join` | |
| `state_dir` | `/var/lib/gpudeck` (`/usr/local/var/gpudeck` on macOS) | |
| `cache_dir` | `<state_dir>/cache` | |
| `cache_limit_bytes` | 70 % of the cache volume | |
| `proxy_port` / `proxy_address` | `9443` / auto | |
| `proxy_advertise_port` | unset (= `proxy_port`) | Port the control plane dials when the proxy sits behind a port forward |
| `proxy_bind` / `GPUDECK_AGENT_PROXY_BIND` | `auto` | Proxy listen address. `auto`: `proxy_address` when it is an IP of this host or loopback (e.g. `127.0.0.1` behind an SSH tunnel), else `0.0.0.0` (a warning names any public address it listens on; firewall the port to the API layer) |
| `labels` | `{}` | |
| `log_level` | `info` | |
| `engine_paths.<engine>` | auto-detect (`llama-server` on PATH, `~/.venv/bin/mlx_lm.server`, …) | Preinstalled engine binaries |
| `egress_lock` | `auto` (`pf` on macOS, `nftables` on Linux, `none` in containers without CAP_NET_ADMIN) | |
| `prom_port` / `GPUDECK_AGENT_PROM_PORT` | `9100` | Loopback Prometheus endpoint |
| `max_concurrent_commands` / `max_concurrent_downloads` | `4` / `2` | |
| `event_buffer_max` | `10000` | |
| `ca_fingerprint` | *(empty)* | Pin the hub CA at join |
| `GPUDECK_AGENT_JOIN_TOKEN` | — | Non-interactive join (DaemonSet) |
| `router_idle_stop_seconds` / `GPUDECK_AGENT_ROUTER_IDLE_STOP_SECONDS` | `120` | Stop a llama.cpp router that has had no instance for this long (`0` keeps empty routers running) |
| `router_overflow` / `GPUDECK_AGENT_ROUTER_OVERFLOW` | `true` | A model a running llama.cpp router does not know is started as its own `llama-server` while the router hosts other models (a router restart would reload all of them); `false` restarts the router |
| `keep_engines_on_exit` / `GPUDECK_AGENT_KEEP_ENGINES_ON_EXIT` | `true` | Engines keep running when the agent stops, restarts or is upgraded; the next start re-adopts them (pid + start time + health) and relaunches only the ones that are gone, on their previous ports. `false`: a clean agent shutdown stops every engine |
| `engine_scope` / `GPUDECK_AGENT_ENGINE_SCOPE` | `auto` | Linux: run each engine in its own transient systemd scope (`systemd-run --scope`, out of the agent's cgroup) when the agent runs under systemd and may create scopes (root / polkit); `systemd` = always, `none` = never. Without a scope the unit's `KillMode=process` keeps engines alive |
| `engine_warmup` / `GPUDECK_AGENT_ENGINE_WARMUP` | `auto` | Warm-up requests (short, ~1 and ~3 ubatches; bounded by the context) before an instance is reported ready, so first user requests skip one-time kernel JIT: `auto` = llama.cpp on NVIDIA GPUs, `on` = every chat / embedding model, `off` |
| `verify_local_files` / `GPUDECK_AGENT_VERIFY_LOCAL_FILES` | `background` | sha256 check of in-place `local_path` model files on load: `background` (start the engine, verify afterwards, report a mismatch on the cache item), `blocking`, `off`. Files hashed before (`hash_files`, an earlier load) with unchanged size + mtime are never re-read (`<cache_dir>/.gpudeck-hashes.json`) |
| `GPUDECK_AGENT_INIT` | auto | `1` / `0` forces / disables the PID 1 init mode (signal forwarding, reaping orphaned engine processes); auto = on when the agent is PID 1 |
| `[[observe]]` (array of tables, file only) | none | Model servers GPU Deck did not start and only observes, read-only (agent protocol §12). Per entry: `name` (unique, `[A-Za-z0-9][A-Za-z0-9._-]*`), `engine` (`vllm` / `sglang` / `llamacpp` / `tgi` / `openai` / `generic`), `url` (e.g. `http://127.0.0.1:8000`; GET `/metrics`, `/health`, `/v1/models` only) and/or `log_file` (followed read-only, lines shipped redacted), optional `metrics_path` (`/metrics`), `model` (display name), `listen_port` / `pid_cmd_contains` (find its processes for per-process GPU memory), `interval_seconds` (10, 2-300) |

## 14. Local dev (this Mac)

`./infra/scripts/bootstrap.dev.sh deps up` starts Postgres 17 and Redis 7.4 in Docker (compose project `gpu-deck-deps`, 127.0.0.1:55432 and 127.0.0.1:56379; ports, the `postgres` superuser password, the app role `gpu_deck` / database `gpu_deck_dev` and the Redis ACL user `gpu-deck` live in the gitignored `infra/docker/.env`, generated on first run). `bootstrap` copies the connection values into `apps/fastapi/.env` (`POSTGRES_*`, `DATABASE_URL`, `REDIS_*`, `REDIS_URL`) without touching other settings, plus `TEST_PG_ADMIN_URL` (superuser URL, read only by integration tests that create scratch databases; not an app setting). Optional HTTPS proxy: `bootstrap.dev.sh caddy` (compose profile `proxy`, `tls internal`) serves `https://gpu-deck.localhost:58443` → 3180, `https://gpu-deck-api.localhost:58443` → 8181, `https://gpu-deck-admin.localhost:58443` → 8180; the default `.env` uses plain `http://localhost` URLs (`PUBLIC_API_URL=http://localhost:8180`, `CORS_ORIGINS=http://localhost:3180`; add the `https://gpu-deck.localhost:58443` origin when using the proxy). Engines on this Mac: `GPUDECK_AGENT_ENGINE_PATHS_LLAMA_CPP=~/.unsloth/llama.cpp/build/bin/llama-server` (needs `DYLD_LIBRARY_PATH` of the same dir; the agent sets it), `GPUDECK_AGENT_ENGINE_PATHS_MLX=~/.venv/bin/mlx_lm.server`.

## 15. Models, deployments, benchmarks

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `BENCHMARK_TARGET_URL` | *(empty)* | J | Base URL of the serving API the runner calls; empty = PUBLIC_SERVING_URL |
| `BENCHMARK_API_KEY_CREDENTIAL` | `benchmark-api-key` | J | Vault credential name holding the API key the runner sends |
| `BENCHMARK_API_KEY` | *(empty)* | J | Plain API key for the runner (dev only; the vault credential wins) |
| `BENCHMARK_REQUEST_TIMEOUT_SECONDS` | `300.0` | J | Per-request timeout during a load test |
| `BENCHMARK_MAX_CONCURRENCY` | `512` | J | Upper bound for any concurrency level |
| `BENCHMARK_VERIFY_TLS` | `true` | J | Verify the serving API certificate (off for self-signed dev certs) |
| `BENCHMARK_STALE_RUN_MINUTES` | `180` | J | Running benchmark runs without progress this long are marked failed |
| `BENCHMARK_READY_WAIT_SECONDS` | `900` | J | Auto-tune waits this long for a restarted instance to be ready |
| `CATALOG_HTTP_TIMEOUT_SECONDS` | `20.0` | A J | HTTP timeout for HF / ModelScope API calls |
| `LOCAL_SCAN_MAX_FILES` | `20000` | A J | Safety cap on files visited by one local scan |
| `SCHEDULER_MAX_DEVICE_COMBOS` | `64` | A J | Max device combinations evaluated per node per replica |
| `DEPLOYMENT_RECONCILE_INTERVAL_SECONDS` | `15` | A J | Jobs-process tick that turns desired replicas into instances |
| `DEPLOYMENT_LOAD_TIMEOUT_SECONDS` | `900` | A J | pending/loading instances older than this are marked error |
| `DEPLOYMENT_UNLOAD_DRAIN_TIMEOUT_SECONDS` | `120` | A J | Default drain timeout for unloads issued by deployments |
| `DEPLOYMENT_ERROR_GRACE_SECONDS` | `60` | A J | Errored instances still count toward replicas this long (auto-heal window) |
| `DEPLOYMENT_CRASHLOOP_ERRORS` | `3` | A J | Instance failures inside the window that mark a deployment failed |
| `DEPLOYMENT_CRASHLOOP_WINDOW_SECONDS` | `600` | A J | Crash-loop detection window |
| `DEPLOYMENT_STOPPED_INSTANCE_RETENTION_DAYS` | `7` | A J | Stopped instance rows older than this are pruned |
| `MODEL_CACHE_LRU_SWEEP_INTERVAL_MINUTES` | `60` | A J | How often the LRU cache sweep runs per node |
| `MODEL_CACHE_HIGH_WATER_PCT` | `90` | A J | Sweep a node cache when used bytes exceed this % of its limit |
| `MODEL_CACHE_LOW_WATER_PCT` | `75` | A J | Sweep until used bytes drop under this % of the limit |
| `AGENT_DOWNLOAD_BASE_URL` | *(empty)* | A H | URL prefix GPU machines fetch `install.sh` and `downloads/` from. Empty = `AGENT_HUB_PUBLIC_URL` + `/agent/v1` (the hub); a value ending in `/api/v1` means the admin API's copies |
| `GPU_INSTANCE_DEFAULT_IMAGE` | `ghcr.io/sur950/gpu-deck-ssh:1` | A J H | Image used when a request names none (must run sshd, protocol.md §8). Default: Ubuntu + OpenSSH (`apps/node-agent/packaging/ssh-image`, published by the release workflow with the first release), glibc so the NVIDIA runtime's `nvidia-smi` / libcuda run inside (Alpine images such as `lscr.io/linuxserver/openssh-server` cannot) |
| `GPU_INSTANCE_DEFAULT_CONTAINER_PORT` | `2222` | A J H | sshd port inside the default image (gpu-deck-ssh and linuxserver: 2222) |
| `GPU_INSTANCE_ALLOWED_IMAGES` | *(empty)* | A J H | Comma list of allowed image prefixes (empty = any image) |
| `GPU_INSTANCE_DEFAULT_TTL_SECONDS` | `28800` | A J H | TTL applied when a request gives none (0 = no TTL) |
| `GPU_INSTANCE_MAX_TTL_SECONDS` | `604800` | A J H | Upper bound for ttl_seconds / extensions |
| `GPU_INSTANCE_MAX_PER_NODE` | `16` | A J H | Running SSH GPU instances allowed per node |
| `GPU_INSTANCE_START_TIMEOUT_SECONDS` | `900` | A J H | start_ssh_instance command timeout (image pull + boot) |
| `GPU_INSTANCE_STOP_TIMEOUT_SECONDS` | `120` | A J H | stop_ssh_instance command timeout |
| `GPU_INSTANCE_TICK_SECONDS` | `15` | A J H | Lifecycle tick (placement retries, TTL, idle, metering) |
| `GPU_INSTANCE_METER_INTERVAL_MINUTES` | `60` | A J H | Write a GPU-minute usage event at least this often |
| `GPU_INSTANCE_NOT_REPORTED_GRACE_SECONDS` | `120` | A J H | Running instance missing from the agent inventory this long -> error |
| `AGENT_DIST_DIR` | *(empty)* | A H | Local agent builds, checked first (offline / air-gapped override): `<version>/<target>/` folders or flat release files `gpudeck-agent-<version>-<target>.tar.gz` + `SHA256SUMS` (empty = apps/node-agent/dist/agent in the repo) |
| `AGENT_RELEASE_URL` | `https://github.com/sur950/gpu-deck/releases/download` | A H | Builds not found locally are fetched from `<url>/v<version>/gpudeck-agent-<version>-<target>.tar.gz`, verified against the release `SHA256SUMS.txt` and cached in `AGENT_CACHE_DIR`; `latest` = this control plane's `APP_VERSION`. Empty = never fetch (air-gapped) |
| `AGENT_CACHE_DIR` | *(empty)* | A H | Writable directory for builds fetched from `AGENT_RELEASE_URL` (empty = `<tmp>/gpu-deck-agent-cache`; Compose and Helm mount a volume) |


## 16. Cloud GPU VMs

| Name | Default | Proc | Description |
| --- | --- | --- | --- |
| `CLOUD_PROVIDERS_ENABLED` | `aws,azure,gcp,digitalocean` | A J | Comma list of cloud providers offered in the UI/API |
| `CLOUD_AGENT_VERSION` | `latest` | A J | Node agent version cloud-init downloads from the hub (/agent/v1/downloads/{version}/{target}; latest = newest build in dist/agent) |
| `CLOUD_AGENT_DOWNLOAD_BASE_URL` | *(empty)* | A J | Base URL of the agent download endpoint VMs use; empty = AGENT_HUB_PUBLIC_URL |
| `CLOUD_JOIN_TOKEN_TTL_MINUTES` | `60` | A J | TTL of the one-time join token baked into a VM's cloud-init |
| `CLOUD_BOOT_TIMEOUT_MINUTES` | `30` | A J | A VM whose agent has not joined after this long goes to status error |
| `CLOUD_DRAIN_TIMEOUT_SECONDS` | `600` | A J | Node drain time limit before stop/terminate proceeds anyway |
| `CLOUD_MAX_NODES_PER_CLUSTER` | `20` | A J | Default cap of nodes per cloud cluster (clusters.config.cloud.max_nodes wins) |
| `CLOUD_DEFAULT_DISK_GB` | `200` | A J | Default VM disk size |
| `CLOUD_RECONCILE_SECONDS` | `30` | A J | Cadence of the cloud.reconcile lifecycle job |
| `CLOUD_STATUS_POLL_SECONDS` | `300` | A J | How often a running/stopped VM is re-read from the provider |
| `CLOUD_CATALOG_CACHE_SECONDS` | `3600` | A J | Redis cache of region / instance-type / price listings (0 = off) |
| `CLOUD_PROVIDER_TIMEOUT_SECONDS` | `60` | A J | Per-call timeout for provider APIs |
| `CLOUD_DO_API_URL` | `https://api.digitalocean.com` | A J | DigitalOcean REST API base URL |
| `CLOUD_AZURE_PRICES_URL` | `https://prices.azure.com/api/retail/prices` | A J | Azure Retail Prices API (public, unauthenticated) |

