# GPU Deck operations

Install, upgrade, back up, rotate, scale and debug a GPU Deck installation. Environment
variables are listed in `libs/contracts/env.md`; deployment files in `infra/` ([components/infra.md](components/infra.md)). First install: [DEPLOYMENT.md](DEPLOYMENT.md).

## 1. Install

### Docker / OrbStack (one CPU machine)

Step by step, including the files the agents and the API layer need: [DEPLOYMENT.md](DEPLOYMENT.md). In short:

```bash
cp apps/fastapi/.env.example apps/fastapi/.env      # fill every CHANGE-ME (see below)
export GPUDECK_VERSION=1.0.0 POSTGRES_PASSWORD=... REDIS_PASSWORD=...   # GRAFANA_ADMIN_PASSWORD with the observability profile
COMPOSE="docker compose -f infra/docker/docker-compose.yml -f infra/docker/docker-compose.full.yml"
$COMPOSE pull                                       # images from ghcr.io/sur950 (or `up -d --build` to build locally)
$COMPOSE --profile tools run --rm migrate
$COMPOSE up -d                                      # add --profile observability for Prometheus/Grafana/OTel
```

The overlay `docker-compose.full.yml` adds Postgres 17 and Redis. Without it the base file expects your own
database and Redis (`GPUDECK_DATABASE_URL` / `GPUDECK_REDIS_URL`) and the external Docker network `gpu-deck-deps`.

Services: `admin-api` 8180, `api-layer` 8181, `agent-hub` 8443, `worker`, `beat`, `scheduler`,
`web` 3180. Required values:

| Variable | How to generate |
| --- | --- |
| `SECRET_KEY` | `openssl rand -hex 32` |
| `VAULT_KEYS` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Your choice (≥ 12 chars); re-applied at boot only when the `.env` value changes |
| `DATABASE_URL` / `REDIS_URL` | Private-network endpoints; `POSTGRES_SSLMODE=verify-full` + `POSTGRES_SSLROOTCERT` in production |
| `AGENT_HUB_PUBLIC_URL` | `https://<hub-host>:8443` as reachable from GPU machines |
| `PUBLIC_API_URL` / `PUBLIC_SERVING_URL` / `FRONTEND_URL` / `CORS_ORIGINS` | Public URLs of the admin API, serving API and UI |
| `GATEWAY_IP_ALLOWLIST` | AI gateway source CIDRs (serving API) |

The API layer's client certificate for the node proxies is issued from the internal CA on first
start (kept in the vault as `api-layer-tls`, written to `PKI_CA_DIR`). To bring your own instead,
issue one (`gpudeck pki issue --kind api_layer`, or `POST /api/v1/pki/service-certificates` with
kind `api_layer`) and set `API_LAYER_CLIENT_CERT_PATH` / `API_LAYER_CLIENT_KEY_PATH`.

### Kubernetes (Helm)

```bash
kubectl -n gpu-deck create secret generic gpu-deck-env --from-env-file=prod.env
helm upgrade --install gpu-deck infra/helm/gpu-deck -n gpu-deck --create-namespace \
  --set secrets.existingSecret=gpu-deck-env -f my-values.yaml
```

The chart runs migrations as a pre-install/pre-upgrade hook, deploys every component with PDBs,
HPA on the API layer, NetworkPolicies (default deny + the network rules in [security-model.md](security-model.md)),
ServiceMonitors, dashboards and alert rules. Set `networkPolicy.*CIDRs`,
`ingress.serving.sourceRanges` (gateway IPs) and the hosts.

### GPU machines

Create a join token (Nodes → Add node, or `POST /api/v1/nodes/join-tokens`, TTL 15 min, one use)
and run the shown snippet on the machine:

```bash
# Linux (systemd) / macOS (launchd): the UI shows the exact snippet; the script comes from the
# admin API (GET /api/v1/agent/install.sh, authorised by the join token) and downloads the agent
# binary + sha256 from /agent/v1/downloads/{version}/{target}.
gpudeck-agent join --hub https://<hub>:8443 --token gdj_… --ca-fingerprint <sha256>  # manual
```

Kubernetes GPU nodes: `nodeAgent.enabled=true`, `nodeAgent.joinTokenSecret`, label nodes
`gpudeck.sur950.github.io/gpu-node=true`. Cloud GPU VMs are provisioned from the Clusters screen
(cloud-init installs and joins the agent).

### Local development (this Mac)

`./infra/scripts/bootstrap.dev.sh` (Postgres 17 + Redis 7.4 containers from
`infra/docker/docker-compose.deps.yml`, `.env` connection values, venv, migrations, seed, all
processes); `deps up|down|status|reset`, `verify`, `stop [--deps]` and `caddy` (optional HTTPS
proxy on `https://gpu-deck*.localhost:58443`) subcommands. Only Docker with `docker compose` is
required. See [DEVELOPMENT.md](DEVELOPMENT.md).

## 2. Upgrade

1. Read the release notes for new env vars: `python scripts/check_env_docs.py` (in the new
   release) lists every setting; diff your `.env` against the regenerated `.env.example`.
2. Back up (§3).
3. Compose: `docker compose … pull` (or `--build`), `--profile tools run --rm migrate`, `up -d`.
   Helm: `helm upgrade` (the migration hook runs `alembic upgrade head` before pods roll).
4. Migrations are additive and run in one linear alembic chain; API layer replicas keep serving
   from the routing table while the control plane restarts. Agents reconnect automatically and
   replay pending commands; agent binaries are upgraded per node from the agent download endpoint
   (`/agent/v1/downloads/{version}/{target}`) or the offline bundle.
5. Verify: `/api/v1/health/ready`, serving `/health`, Fleet screen shows every node online.

Downgrades are not supported across a migration; restore the backup instead.

## 3. Backup and restore

What to back up:

| Item | How | Notes |
| --- | --- | --- |
| PostgreSQL | `pg_dump -Fc "$DATABASE_URL" > gpudeck-$(date +%F).dump` (or managed snapshots / PITR) | Contains the vault (encrypted), CA key (encrypted, vault `internal-ca`), all config and history |
| `VAULT_KEYS` + `SECRET_KEY` | Your secret manager | **Without the vault keys the DB backup's credentials and CA key are unreadable.** Store separately from the dump |
| `.env` / Helm values | Your secret manager / git (no secrets) | |
| `PKI_CA_DIR` | Optional | Cached PEMs; regenerated from the vault on boot |

Redis holds only caches, queues and the routing table (rebuilt within seconds); no backup needed.
Time-series partitions older than the retention settings are dropped automatically; to keep less
data in backups lower `METRICS_*_RETENTION_*` / `USAGE_EVENTS_RETENTION_DAYS`.

Restore: stop the control plane, `pg_restore --clean --if-exists -d "$DATABASE_URL" gpudeck.dump`,
restore the same `VAULT_KEYS`/`SECRET_KEY`, run migrations (`alembic upgrade head`), start. Agents
reconnect with their existing certificates (same CA). Sessions are invalidated by flushing
`gpu-deck:session:*` from Redis if needed.

## 4. Certificate rotation

| Certificate | Rotation |
| --- | --- |
| Node certificates | Automatic: the hub sends `rotate_cert` `NODE_CERT_ROTATE_BEFORE_DAYS` (30) before expiry; the old cert is revoked 10 minutes after the new one connects. Force: `POST /api/v1/nodes/{id}/certificates/rotate` |
| API layer client cert | Issue a new one (`POST /pki/service-certificates`, kind `api_layer`), update `API_LAYER_CLIENT_CERT_PATH/KEY_PATH`, roll the API layer, then revoke the old serial (`POST /pki/certificates/{id}/revoke`) |
| Agent hub server cert | Issued from the internal CA at boot (vault `agent-hub-tls`, SANs from `AGENT_HUB_PUBLIC_URL` + `AGENT_HUB_TLS_SANS`); delete the credential and restart the hub to reissue, or set `AGENT_HUB_TLS_CERT_FILE/KEY_FILE` to your own |
| Admin / serving HTTPS | Usually terminated by Caddy / the ingress (cert-manager). Direct TLS: replace `TLS_CERT_FILE/KEY_FILE` and restart |
| Internal CA | 10-year validity (`PKI_CA_VALIDITY_DAYS`). Replacing it requires re-joining every node (new join tokens); plan it like a migration |
| Compromised machine | Revoke its certificate (Nodes → node → Certificates → Revoke): the hub closes the socket, the CRL is pushed to every agent, the API layer drops it from routing |

## 5. Key and secret rotation

| Secret | Procedure |
| --- | --- |
| `VAULT_KEYS` | Prepend a new Fernet key (`NEW,OLD`), restart all processes. The hourly `vault.rotate_keys` job re-encrypts every credential still on an old key (audit `vault.keys_rotated`); once it reports 0 remaining, drop the old key and restart |
| `SECRET_KEY` | Changing it invalidates every session and the admin seed hash; rotate during a maintenance window (admins log in again) |
| `ADMIN_PASSWORD` | Change in the UI (Account) or change `.env`: the seed re-applies only when the `.env` value changed (`HMAC(SECRET_KEY, password)` comparison) |
| API keys | Rotate per key (`POST /api-keys/{id}/rotate` shows the new `gd-…` key once); revocation is immediate on every API layer process (pub/sub `gpu-deck:apikeys:invalidate`) |
| Cloud / HF / SMTP / webhook credentials | Update the vault credential; caches are dropped on write |
| Join tokens | One use, 15-minute TTL; revoke unused ones from the Nodes screen |

## 6. High availability

- Admin API, API layer, agent hub: run ≥ 2 replicas behind the ingress / TCP LB (Helm does,
  with PDBs). The API layer is the only hot path; scale it on CPU (HPA) — each replica holds the
  routing table in memory and needs no DB.
- Scheduler: ≥ 2 replicas. Postgres leases (`jobs-leader`, `rules-evaluator`) with
  `HA_LEASE_TTL_SECONDS` (30) / `HA_LEASE_RENEW_SECONDS` (10) and fenced writes; failover ≤ TTL +
  one renew. Swaps resume from their persisted step on the new leader. `gpudeck_jobs_leader`
  must sum to 1 (alert `GpuDeckNoJobsLeader`).
- Celery workers: any number; beat must be a single instance (Helm runs one).
- PostgreSQL: managed HA or an operator (Patroni / CloudNativePG); Redis: managed or Sentinel.
- Node agents keep engines running when the control plane is down; serving continues as long as
  the API layer and Redis are up (routing table cached in memory too).

## 7. Offline (air-gapped) install

On a connected machine: `infra/offline/build-bundle.sh --version X [--with-data-services]
[--with-observability]`. Copy the bundle; on the site:

```bash
./install.sh verify
./install.sh load-images --registry registry.local:5000/gpu-deck
./install.sh compose --env /etc/gpu-deck/.env [--full] [--observability]   # or: helm --values …
./install.sh cli && ./install.sh terraform-mirror
sudo ./install-agent.sh --hub https://hub:8443 --token gdj_…               # each GPU machine
```

Point `HF_ENDPOINT` / `MODELSCOPE_ENDPOINT` at the site mirror (pushed to every agent) or use
local model files (Catalog → Add a model → scan a local path).

## 8. Troubleshooting

| Symptom | Check |
| --- | --- |
| Node stays `pending` / `offline` | Agent logs (`journalctl -u gpudeck-agent` / `log show --predicate 'process == "gpudeck-agent"'`); can the machine reach `AGENT_HUB_PUBLIC_URL:8443`? Clock skew breaks TLS. Hub logs show close codes: 4001 identity mismatch, 4002 schema error, 4003 rejected/revoked |
| Join fails `JOIN_TOKEN_INVALID/EXPIRED` | The token is used up (one-time tokens join one machine, multi-use ones `max_uses`), revoked or expired (15 min by default); create a new one |
| Serving 503 `model_not_ready` | Deployment has no ready instance; Deployments → instance state/logs; `SERVING_NO_INSTANCE_WAIT_SECONDS` |
| Serving 502 `upstream_error` | API layer cannot reach the node proxy :9443 (firewall, `API_LAYER_CLIENT_CERT_PATH`, revoked cert); instance ejected for `SERVING_EJECT_SECONDS` |
| Serving 401 `invalid_api_key` | Key revoked / mistyped; keys are `gd-` + 48 hex; check Audit for `api_key.auth_failed` |
| Serving 403 `ip_blocked` | IP rules scope `api_layer` / `GATEWAY_IP_ALLOWLIST`; behind a proxy set `TRUSTED_PROXIES` so `X-Forwarded-For` is believed |
| Admin locked out by an IP rule | Remove the rule from the DB (`UPDATE ip_rules SET enabled=false WHERE …`) and flush `gpu-deck:ip_rules:*`; the API refuses rules that would block the caller |
| Login keeps failing | Lockout after `AUTH_LOCKOUT_ATTEMPTS` for `AUTH_LOCKOUT_MINUTES`; the message shows the remaining time |
| Usage / billing empty | Celery worker running and consuming `metering` + `billing`? `metering.rollup` runs hourly (current hour every 5 min) |
| Rules never fire | Scheduler running and a replica holding the leases (`gpudeck_jobs_leader` = 1, `ha_leases` table); rule enabled and not in `dry_run`; see Rule → runs |
| `generated_models.py` stale (CI) | `apps/fastapi/app/db/postgres/scripts/generate_models.sh` and commit |
| Env drift (CI) | `python scripts/check_env_docs.py --write`, then document new rows in env.md |
| Slow serving | `gpudeck_serving_ttfb_seconds` vs `gpudeck_engine_ttft_seconds` (Inference dashboard): the difference is API layer + network |

Logs: every process logs to stdout (loguru; `LOG_FORMAT=json` for aggregation). Request ids
(`X-Request-ID`) tie admin audit entries, serving usage rows and traces together.
