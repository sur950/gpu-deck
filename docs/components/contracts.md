# Contracts (`libs/contracts`)

The single source of truth for the interfaces between GPU Deck's components. Nothing in this folder is runtime code: each artifact is either read as a spec or feeds a code generator. Change the contract first, then regenerate the clients.

## Artifacts

| Path | What it is | Consumed by |
| --- | --- | --- |
| [`apps/fastapi/.../migrations/001_init/{up,down}.sql`](../../apps/fastapi/app/db/postgres/alembic/migrations/001_init/up.sql) | The baseline PostgreSQL schema (partitioned time series, retention notes at the top of `up.sql`, built-in engine rows) | Backend models (`generated_models.py`), Grafana dashboards |
| [`openapi/admin.yaml`](../../libs/contracts/openapi/admin.yaml) | OpenAPI 3.1 for the admin REST API (`/api/v1`): the response envelope, SSE and WebSocket live endpoints, CSV exports | Web client types, the CLI (read at runtime), Terraform provider client, backend route tests |
| [`openapi/serving.md`](../../libs/contracts/openapi/serving.md) | Serving API (OpenAI, Anthropic, Ollama): endpoints, prefixes, auth, error shapes, the routing table handed to the API layer, usage extraction, limits | API layer, node agent proxy, web API keys screen |
| [`agent-protocol/`](../../libs/contracts/agent-protocol/protocol.md) | JSON Schemas (draft 2020-12) for the node agent and hub WebSocket protocol, the join flow, reconnect and idempotency rules, the data path (outbound tunnel or the proxy on port 9443) and the CA-signed node security policy | Node agent (serde types), agent hub (pydantic models), API layer |
| [`rules/`](../../libs/contracts/rules/rules.md) | Params schema per rule type, target selector, layout and rule objects; evaluation semantics and the swap safety sequence | Rules evaluator, web rule builder (forms are rendered from the schemas), Terraform `gpudeck_rule` |
| [`metrics.md`](../../libs/contracts/metrics.md) | Metric catalog: canonical names, units, per-engine and per-vendor sources, database columns, Prometheus names, dashboard mapping | Node agent scrapers, metrics ingest, rules, Grafana dashboard generator |
| [`env.md`](../../libs/contracts/env.md) | Every environment variable and `agent.toml` key, with defaults, grouped and tagged by process | Backend settings, `.env.example` files, compose, Helm, node agent |

These Markdown specs stay next to their schemas because code, tests and generators read them in place (for example `scripts/check_env_docs.py` parses `env.md`, and the dashboard generator reads `metrics.md`).

## Versioning

- **Database:** migrations are append-only; `001_init` is the baseline and later changes are new migrations in one linear chain (see [migrations](fastapi-migrations.md)).
- **Admin API:** `info.version` is semver and equals the release version. Additive changes (optional fields, endpoints, enum values) are minor. Removing or renaming fields, or changing the envelope, is a major. Clients must ignore unknown response fields.
- **Agent protocol:** `envelope.v` (currently `1`) is the wire major and the schema `$id`s carry `/v1/`. Hub and agent each state their `protocol_version` in `hello` / `hello_ack`. Changes are listed in [`agent-protocol/CHANGELOG.md`](../../libs/contracts/agent-protocol/CHANGELOG.md); see also [RELEASING.md](../RELEASING.md).
- **Rule and layout schemas:** `$id` carries `/v1/`. New params are optional with defaults so stored `rules.params` stay valid; removing a param needs a data migration.
- **Metrics:** canonical names are stable identifiers (they appear in URLs and dashboards). Add, never rename.
- **Environment:** adding a variable with a default is free; renaming needs an alias in the settings class for one release.

## Generated clients

Run from the repository root. Generated files are committed and never edited by hand; CI checks that they are current.

| Target | Command |
| --- | --- |
| Web TypeScript types and client (`apps/web/lib/api/generated`) | `pnpm --filter web gen:api` (`--check` in CI) |
| Terraform provider Go client (`apps/terraform-provider/internal/client`) | `go generate ./...` in `apps/terraform-provider` |
| Backend models (`generated_models.py`) | `apps/fastapi/app/db/postgres/scripts/generate_models.sh` (`--check` in CI) |
| `.env.example` files | `python scripts/check_env_docs.py --write` |

The node agent's protocol types in `apps/node-agent/crates/gpudeck-protocol` are hand-written serde structs that are tested against every schema and example in `agent-protocol/`. Unknown fields are rejected (`additionalProperties: false` becomes `deny_unknown_fields`).

## Design decisions worth knowing

- **Partitioned time series use `BIGSERIAL` plus `PRIMARY KEY (id, ts)`** (not UUID) for btree locality and index size. Partitions are `<table>_pYYYYMMDD` (UTC days) and there is no default partition on purpose: ingest creates partitions ahead of time and retries once on SQLSTATE `23514`. Partition tables are excluded from the generated models.
- **`admin_users` is a true singleton** (`UNIQUE (singleton)`); `env_secret_hash` implements the "change `ADMIN_PASSWORD` in `.env` to rotate it" rule.
- **`node_certificates` also stores service certificates** (`kind` column), so one table feeds the revocation list. The internal CA key lives in the vault (`credentials.name = internal-ca`).
- **`price_plans` is a standalone table** referenced by clusters and nodes (the node overrides the cluster); usage events snapshot the resolved `price_plan_id` and `cost_usd` when they are written. Billing on top of it is planned for v2; v1 has no price plan endpoints.
- **Agent commands are persisted (`agent_commands`)** and replayed by id on reconnect; the agent is idempotent on the envelope id.
- **The routing table lives in Redis** (`gpu-deck:routing:v1`), so the API layer never touches PostgreSQL on the request path.
- **Redis naming:** every key, stream and channel is `gpu-deck:<domain>:<event-or-key>` (for example `gpu-deck:routing:updated`, `gpu-deck:apikeys:invalidate`, `gpu-deck:hub:dispatch`). The prefix is `REDIS_KEY_PREFIX`; code builds names with `settings.redis_key(...)`, and the development Redis ACL user only accepts `gpu-deck:*`.
- **Billable GPU time comes from instance uptime**, not request wall time: `usage_events.gpu_seconds` is the request's informational busy share, and billing allocates `gpu_count x slice` GPU-minutes of each loaded instance-minute to API keys pro rata by tokens, with an unallocated bucket for idle minutes (see `serving.md`, section 6; the billing pages are planned for v2).
- **llama.cpp router-mode admin endpoints and per-model metrics paths depend on the exact build**; `engine_versions.metrics_config` carries the paths, pinned against llama.cpp b8798.
