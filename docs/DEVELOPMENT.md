# Developing GPU Deck

How the code is organised, the conventions every change follows, and how to run and test each part. [CONTRIBUTING.md](../CONTRIBUTING.md) covers the workflow (forks, commits, pull requests, the contributor checklist); this page covers the engineering rules. To understand the system first, read [architecture.md](architecture.md); to run it in production, [DEPLOYMENT.md](DEPLOYMENT.md).

## Repository layout

| Path | What lives there |
| --- | --- |
| `apps/fastapi` | Admin API, serving API, agent hub, Celery worker, scheduler (Python 3.12) |
| `apps/web` | Admin web UI (Next.js, TypeScript) |
| `apps/node-agent` | Node agent: device plugins, engine adapters, mTLS proxy (Rust workspace) |
| `apps/desktop` | Desktop app, a Tauri 2 shell around a control plane's own web UI |
| `apps/cli` | The `gpudeck` command line (Python, Typer) |
| `apps/terraform-provider` | Terraform provider (Go) |
| `libs/contracts` | The interfaces between components: OpenAPI, agent protocol schemas, rule schemas, metrics catalog, environment reference ([contracts.md](components/contracts.md)) |
| `infra` | Compose files, Helm chart, offline bundle, observability, dev scripts ([infra.md](components/infra.md)) |
| `docs` | All documentation ([docs index](README.md)) |
| `scripts` | Repository checks and release helpers (`check_env_docs.py`, `version.py`, `release.sh`, `ci_local.sh`) |
| `assets/brand` | Brand sources and generated icons ([BRAND.md](BRAND.md)) |

Per-component notes: [fastapi.md](components/fastapi.md), [node-agent.md](components/node-agent.md), [desktop.md](components/desktop.md), [terraform-provider.md](components/terraform-provider.md), and the CLI's own `apps/cli/README.md`.

## The local stack

Docker with the compose plugin (OrbStack, Docker Desktop, Colima or Docker Engine) is the only host requirement for dependencies: PostgreSQL 17 and Redis 7.4 run as containers from `infra/docker/docker-compose.deps.yml`. Do not rely on a host-installed Postgres, Redis or Caddy.

```bash
./infra/scripts/bootstrap.dev.sh                  # deps, .env, venv, migrations, seed, start everything
./infra/scripts/bootstrap.dev.sh status | health | verify | urls | logs [svc]
./infra/scripts/bootstrap.dev.sh stop [--deps]    # leave nothing running; --deps also stops the containers
./infra/scripts/bootstrap.dev.sh deps up|down|status|reset
./infra/scripts/bootstrap.dev.sh caddy            # optional HTTPS proxy (tls internal)
```

| Process | Command (from `apps/fastapi`) | Port |
| --- | --- | --- |
| admin API | `venv/bin/uvicorn app.main:app --port 8180 --reload --timeout-graceful-shutdown 5` | 8180 |
| API layer (serving) | `venv/bin/uvicorn app.api_layer_main:app --port 8181 --reload` | 8181 |
| agent hub | `venv/bin/python -m app.agent_hub_main` (TLS 1.3, mTLS on `/agent/v1/ws`) | 8443 |
| worker | `PYTHONPATH=. venv/bin/celery -A app.workers.celery_app worker --beat --loglevel=info --pool=threads` (macOS; Linux uses the default prefork pool) | none |
| scheduler | `venv/bin/python -m app.workers.scheduler` | none |
| web | `pnpm --filter web dev` | 3180 |
| desktop | `pnpm --filter desktop tauri dev` (Vite on 127.0.0.1:1420) | none |
| PostgreSQL (Docker) | superuser `postgres`, app role `gpu_deck`, database `gpu_deck_dev` | 55432 |
| Redis (Docker) | ACL user `gpu-deck`, keys `gpu-deck:*` only, db 0 | 56379 |
| Caddy (Docker, optional) | `https://gpu-deck.localhost:58443` to 3180, `gpu-deck-api.localhost` to 8181, `gpu-deck-admin.localhost` to 8180 | 58443 |

Ports 8443 (agent hub), 8444 (API-layer tunnel listener agents dial in serving mode `tunnel`) and 9443 (node-agent proxy, direct mode) are mTLS. Generated dev credentials live in the gitignored `infra/docker/.env` and are copied into `apps/fastapi/.env` by `bootstrap`; integration tests that create scratch databases use `TEST_PG_ADMIN_URL` (the container superuser) from the same file. Leave no processes running when you finish: `bootstrap.dev.sh stop`.

You need no model downloads to work on GPU Deck. Tests use fixtures and small local models only.

## Backend architecture and conventions

Layers, top to bottom:

| Layer | Responsibility |
| --- | --- |
| Routes (`app/api/v1/routes`, `app/api/serving`) | HTTP only: parse, call a service, return `success_response(...)` |
| Services (`app/services/<domain>/service.py`) | Business logic and orchestration |
| Repositories (`app/db/postgres/repositories`) | All SQL. They flush, never commit |
| Generated models (`generated_models.py`) | sqlacodegen output built from a scratch schema of the migrations. Committed, checked in CI with `--check`, never hand-edited |
| Workers (`app/workers`) | Celery tasks and APScheduler jobs |

Rules:

- **Routes.** `router = APIRouter()` with no prefix. Decorator order is `@router.<method>`, then `@rate_limit`, then `@handle_exceptions`. Signature: `request: Request, ..., admin=Depends(get_current_admin), service: XService = Depends(XService.as_dependency)`. Return `success_response(data, meta)`; list endpoints use `paginated_meta(total, page, page_size)`.
- **Envelope and errors.** `{success, data, error, meta}`. Raise subclasses of `app/core/exceptions.py` with an `error_code`. Serving routes do not use `@handle_exceptions`; their errors come out in the OpenAI, Anthropic or Ollama shape depending on the path prefix.
- **Sessions and transactions.** `get_db` commits once per request, before the response is sent: declare it as `Depends(get_db, scope="function")` (a unit test enforces it). Call `repo.release_connection()` before slow I/O. Celery and the scheduler use `NullPool`.
- **Redis keys.** Every key, stream and channel is `gpu-deck:<domain>:<event-or-key>` (`settings.REDIS_KEY_PREFIX`). Build names with `settings.redis_key(...)`, `CacheService`, or the pre-wired Celery, APScheduler and slowapi configuration. The development Redis ACL rejects any key outside `gpu-deck:*`.
- **Off the hot path.** Writes for usage, audit and metering go through `enqueue_*` helpers that `apply_async(queue=...)` and never raise. Tasks are synchronous Celery entry points that run an async `_impl` on a fresh loop with `BaseTaskWithRetry`.
- **Modules.** Docstring first, then `logger = get_logger(__name__)`. black, isort and flake8 at 100 columns; mypy with `--ignore-missing-imports`. Never log secrets.
- **Security primitives.** Passwords: Argon2id through `app/utils/password.py`. Sessions: opaque Fernet token, SHA-256 stored, Redis cache, HttpOnly cookie `gpudeck_session` plus CSRF double-submit cookie `gpudeck_csrf`. API keys: `gd-` plus 48 hex (`API_KEY_PREFIX`), prefix and SHA-256 stored, shown once. Vault: `MultiFernet(VAULT_KEYS)`.
- **Middleware** is pure ASGI. No `BaseHTTPMiddleware` and no `@app.middleware("http")`. The serving process logs per request at DEBUG with a sampled INFO access line.
- **Secrets** live only in `.env` (gitignored). `.env.example` keeps `CHANGE-ME` placeholders.

### Extension points

Add a file; do not edit a shared registry.

| Need | Drop a file | Picked up by |
| --- | --- | --- |
| Admin endpoint | `app/api/v1/routes/<name>.py` with `router = APIRouter()` (full paths, optional `TAGS`) | `app/api/router_registry.py`, mounted under `/api/v1` |
| Serving endpoint | `app/api/serving/{openai,anthropic,ollama}/<name>.py` with `router` | the same registry; prefix from the package `PREFIX` (`/v1`, `/anthropic/v1`, `/api`) |
| Setting | `app/core/settings/<domain>.py` with a `DomainSettings` subclass and `UPPER_SNAKE` fields | `app/core/settings/__init__.py` merges it into `settings`; duplicate names raise |
| Middleware | `app/middlewares/<name>.py` with `install(app, process)`, `ORDER`, `PROCESSES` | `app/middlewares/__init__.py` |
| Startup or shutdown work | `app/services/<domain>/hooks.py` with `@on_startup("admin", "serving")` or `@on_shutdown(...)` | `app/core/lifecycle.py` (processes: admin, serving, worker, scheduler) |
| Celery task | `app/workers/tasks/<domain>_tasks.py` with `QUEUE` and optional `BEAT_SCHEDULE` | `app/workers/celery_app.py` |
| Periodic job | `@scheduled_job("<domain>.<verb>", "interval" or "cron", ...)` in a task module | `app/workers/scheduler.py` |
| Migration | `app/db/postgres/alembic/migrations/NNN_slug/` via `create_migration.sh "desc" NNN` | `alembic.ini` `version_locations` |

New domain files are named after the domain: `routes/<domain>.py`, `schemas/<domain>_schema.py`, `repositories/<entity>_repository.py`, `workers/tasks/<domain>_tasks.py`, `core/settings/<domain>.py`, `services/<domain>/hooks.py`. A new admin endpoint also starts in the contract: describe it in `libs/contracts/openapi/admin.yaml`, then regenerate the typed clients ([contracts.md](components/contracts.md)).

### Migrations

Raw SQL is the source of truth; Alembic records which folders ran. See [fastapi-migrations.md](components/fastapi-migrations.md) for the folder layout.

- Run `./app/db/postgres/scripts/create_migration.sh "add widget table" NNN` from `apps/fastapi`. Pick the next free `NNN`; maintainers renumber at merge time if two pull requests collide.
- The number is not the apply order. Alembic follows one linear chain, so the new migration's `down_revision` is the current head. `venv/bin/alembic heads` must print exactly one revision (`tests/unit/test_platform_conventions.py` enforces it).
- Never edit an applied migration. `up.sql` is idempotent where possible; `down.sql` reverses it in opposite order.
- SQL style: UUID primary keys `gen_random_uuid()`, `TIMESTAMPTZ`, `created_at`, `updated_at` and `deleted_at`, enums with `CREATE TYPE`, partial indexes `WHERE deleted_at IS NULL`, `pg_trgm` GIN indexes for search, `COMMENT ON` for tables and non-obvious columns, the `update_updated_at_column()` trigger. Enum and type existence checks must be schema-aware (`typnamespace = current_schema()::regnamespace`) so scratch schemas work. `migration.py` runs the SQL through the raw DBAPI cursor.
- After adding a migration run `./app/db/postgres/scripts/generate_models.sh` and commit the regenerated `generated_models.py`.

### Settings and the environment reference

Every setting field must have a row in [`libs/contracts/env.md`](../libs/contracts/env.md). The two `.env.example` files (repository root and `apps/fastapi`) are generated from the settings classes, so after adding or changing a setting run:

```bash
apps/fastapi/venv/bin/python scripts/check_env_docs.py --write     # regenerate both .env.example files, append env.md rows
apps/fastapi/venv/bin/python scripts/check_env_docs.py             # the CI check: no drift between settings, env.md and the examples
```

Then edit the new `env.md` rows (process column, description).

### Tests

| Kind | Where | Rules |
| --- | --- | --- |
| Unit | `tests/unit` | Mocks only. No services needed |
| Integration | `tests/integration` | Real local Postgres and Redis from `bootstrap.dev.sh deps up`; mark with `@pytest.mark.integration` |
| End to end | `tests/e2e` | Log in for real and drive the APIs; name created resources `zz_e2e_<uuid>` and delete them in teardown ([fastapi-e2e.md](components/fastapi-e2e.md)) |

The **whole** suite must pass in one run: `cd apps/fastapi && venv/bin/pytest tests` (or `./tests/scripts/run_tests.sh unit|integration|e2e`). Integration tests that write data build a scratch schema with `tests/schema_helpers.py::create_scratch_schema` (all migrations, in chain order) and restore every global they swap (engine, `REDIS_KEY_PREFIX`, settings). Use recorded or hand-written fixtures for hardware, cloud providers and engines you do not own, and say so in the test. What has been verified on real hardware versus fixtures is tracked in [TESTING.md](TESTING.md).

## Frontend (`apps/web`)

- `pnpm` only (the version in the root `package.json` `packageManager`). The Python backend is run from `apps/fastapi` with its own scripts, not pnpm.
- File names are kebab-case; imports use the `@/` alias.
- Layers: `components/ui` (shadcn, never edited by hand) then `components/shared` then a route's `app/.../_components`. Hooks live in `hooks/use-*.ts` (API hooks in `hooks/api`), types in `types/<domain>.ts`, API calls in `lib/api/`. The generated client in `lib/api/generated` comes from `libs/contracts/openapi/admin.yaml` (`pnpm gen:api`, `node scripts/gen-api.mjs --check` in CI) and is never edited by hand.
- Navigation comes from one registry, `lib/nav/registry.ts` (sections, breadcrumbs, command palette entries). Add a page by adding an entry, not by editing the layout.
- Theme and branding: the crimson family shared with Container Deck: warm off-white / near-black neutrals, crimson as the only accent (primary actions, links, active nav, focus, chart slot 1), green / amber / vermilion only for state, in light and dark mode. Branding values (`NEXT_PUBLIC_APP_NAME`, `NEXT_PUBLIC_BRAND_COLOR`, ...) are read at build time ([BRAND.md](BRAND.md)). Every screen must look right in both modes.
- Checks: `pnpm lint`, `pnpm type:check`, `pnpm test` (Vitest), `pnpm build`, `pnpm e2e` (Playwright). Screenshots are regenerated against a populated dev stack, see [Screenshots](#screenshots).

## Node agent (`apps/node-agent`)

Rust workspace: `gpudeck-protocol` (types for every schema in `libs/contracts/agent-protocol`), `gpudeck-agent` (the binary), `gpudeck-devices` (vendor plugins), `gpudeck-engines` (engine adapters). `cargo fmt --all`, no `clippy` warnings (`cargo clippy --workspace --all-targets --locked -- -D warnings`), `cargo test --workspace --locked`. Engines bind to `127.0.0.1` only and the agent never opens an inbound control port. A new device plugin or engine adapter comes with fixtures of the real tool output (`nvidia-smi`, `rocm-smi`, engine responses) next to the tests; please also file a hardware report if you could try it on real hardware. Details: [node-agent.md](components/node-agent.md).

## Desktop app (`apps/desktop`)

A Tauri 2 remote shell: it loads the server's own web UI rather than bundling one.

```bash
pnpm install
pnpm --filter desktop tauri dev           # Vite on 127.0.0.1:1420 + the app
pnpm --filter desktop test                # Vitest
pnpm --filter desktop type:check
cd apps/desktop/src-tauri && cargo test --locked && cargo clippy --all-targets --locked -- -D warnings
pnpm --filter desktop tauri build         # bundles for the host platform
```

The web side of the bridge is `apps/web/lib/desktop` (a no-op in browsers, mounted by one line in the dashboard layout). Keep the IPC surface minimal: nothing is granted to a remote origin except `notify` and `set_tray_status` for a connected saved server ([security-model.md](security-model.md) section 3). Build, signing and updater setup: [desktop.md](components/desktop.md).

## CLI and Terraform provider

- CLI (`apps/cli`): same formatters as the backend (black, isort, flake8, mypy). Output is a table on a terminal and JSON when piped. Commands are built at runtime from the server's OpenAPI document (cached), falling back to `libs/contracts/openapi/admin.yaml`, which the wheel build copies in as `gpudeck/_spec/admin.yaml` (never committed); see `apps/cli/README.md`.
- Terraform provider (`apps/terraform-provider`): `gofmt`, `go vet ./...`, `go test ./... -count=1`. The client is generated by `go generate ./...`. Resource docs are generated into the provider's `docs/` folder, which the Terraform Registry expects in place.

## Infrastructure

`helm lint infra/helm/gpu-deck --strict --set secrets.existingSecret=ci`, `helm template ci infra/helm/gpu-deck --set secrets.existingSecret=ci`, and `docker compose -f infra/docker/docker-compose.yml config -q` must pass. Containers run non-root with a read-only root file system and dropped capabilities; keep it that way. Regenerate the Grafana dashboards with `python3 infra/observability/grafana/build_dashboards.py` after changing the metrics catalog.

## Documentation layout

At the repository root only `README.md`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md` and `CONTRIBUTING.md`. Everything else is under [`docs/`](README.md): guides at the top level, per-component notes in `docs/components/`. Exceptions stay next to the thing that reads them:

- `libs/contracts/**/*.md` (`env.md`, `metrics.md`, `serving.md`, `rules.md`, `protocol.md` and the protocol `CHANGELOG.md`) sit beside their schemas and are read by tests, generators and `scripts/check_env_docs.py`.
- `apps/terraform-provider/docs/**` is the Terraform Registry layout, generated from the provider schemas.
- `apps/cli/README.md` is the Python package readme (`pyproject.toml` `readme`).
- `.github/PULL_REQUEST_TEMPLATE.md` is where GitHub looks for it.

Public docs link only to other public files, and use relative links.

## Website and docs site

The public site lives in `website/` and is published to GitHub Pages at `https://sur950.github.io/gpu-deck/` by `.github/workflows/pages.yml`. Every public Markdown file is rendered into `website/docs/` by `scripts/build_docs.py`, which fails on broken links, broken anchors and links to non-public files. `website/docs/` and `website/assets/screenshots/` are generated, so they are not committed.

Preview it locally exactly as GitHub Pages serves it:

```bash
scripts/serve_site.sh          # builds the docs, then serves http://localhost:8000/gpu-deck/
```

Opening `website/index.html` straight from disk does not work: folder links such as `docs/architecture/` need a web server, and `404.html` uses the `/gpu-deck/` base path. Check the build without serving with `python scripts/build_docs.py --check`.

### Writing docs that read well on GitHub and on the site

Everything below renders natively on GitHub and is styled by the docs site:

- **Callouts**: GitHub alerts, `> [!NOTE]`, `> [!TIP]`, `> [!IMPORTANT]`, `> [!WARNING]`, `> [!CAUTION]`, each on its own line at the start of a quote.
- **Summary cards**: an HTML comment `<!-- cards -->` on the line before a bullet list whose items read `**Title**: text`. GitHub hides the comment and shows the list; the site shows cards (a title starting with Verified, Partial or Awaiting gets that status colour).
- **Screenshots**: `![alt text](../screenshots/<name>.svg)` on a line of its own, then an `*italic*` line as the caption. Always write alt text that says what the screen shows.
- **Diagrams**: the brand diagrams in `docs/diagrams/` (`<name>-light.svg` and `<name>-dark.svg`, built by `assets/brand/doc_art.py`, see [BRAND.md](BRAND.md#documentation-diagrams)) go in a `<picture>` with the dark file as `<source media="(prefers-color-scheme: dark)">`, the light one as the `<img>` with alt text and `width="880"` (no `height`: GitHub would distort a scaled image). The site follows its own theme switch and adds the height from the SVG, lazy loading and async decoding. Mermaid blocks still work for detailed sequences (`--render-diagrams`).

## Screenshots

Every product screenshot is captured as a **vector SVG**, so text, colours and shapes can be changed later without re-shooting. The tooling lives in `docs/screenshots/` (its own `package.json` and lockfile, outside the pnpm workspace):

| File | What it is |
|---|---|
| `src/<name>.svg` | The editable source: every box a `<rect>`/`<path>`, every string a `<text>`, charts and icons as their original SVG. Colours that are theme tokens are classes defined once at the top (`.f-brand{fill:#d3263f}`, `.s-border{stroke:#2a2b30}`; `f-` = fill, `s-` = stroke), so recolouring a token is a one-line edit. JetBrains Mono (OFL) is embedded as a base64 subset; Satoshi is referenced by name only (its licence does not allow redistributing the font), so open the source with Satoshi installed to see the real type. |
| `<name>.svg` | The display SVG used by the README and the website (the only committed screenshot; no PNG or WebP is committed, and the website serves these SVGs directly): Satoshi text converted to outlines (each glyph once in `<defs>`, each string a group of `<use>` with an `aria-label` holding the text), no `<foreignObject>`, scripts or external references, so GitHub and `<img>` tags render it. Generated, never edited. |

Re-capture against a running dev stack that has some data in it (clusters, nodes, models, at least one deployment and one rule):

```bash
./infra/scripts/bootstrap.dev.sh bootstrap
(cd docs/screenshots && pnpm install --ignore-workspace)
node docs/screenshots/capture.mjs                # all screens, or: capture.mjs overview nodes
node docs/screenshots/desktop.mjs                # desktop connect screen (dark + light) as SVG
```

`capture.mjs` signs in as the admin from `apps/fastapi/.env`, opens each screen, hides toasts, scrollbars and the dev overlay, masks join tokens and API keys, converts the page in the browser (`lib/dom-to-svg.js`), writes `src/<name>.svg`, renders the display SVG, and compares the render with a normal screenshot of the live page (pixel share and dense 8 x 8 blocks; reference shots and diff images go to `$TMPDIR/gpu-deck-shots`). Detail pages open the first row of their list. An optional, git-ignored config module (`docs/screenshots/capture.config.mjs`, or `SHOT_CONFIG=<path>`) can pin the rows and form choices (deployment and rule ids, cluster, model, cloud region and instance type) and add `setup` / `teardown` / per-screen hooks that get small `psql` and `redis` helpers for the dev containers; `--skip-setup` ignores the hooks. The header of `capture.mjs` lists the keys. Fonts are downloaded once into `~/.cache/gpu-deck-screens/fonts` (Satoshi from Fontshare, JetBrains Mono from Google Fonts), never committed.

To tweak a screenshot, edit `src/<name>.svg` (a text editor, Inkscape, Illustrator or Figma) and re-render:

```bash
node docs/screenshots/render.mjs overview                # display SVG for one screen
node docs/screenshots/render.mjs                         # all of them
website/sync-screenshots.sh                              # refresh the website copies (git-ignored)
```

The desktop screens (`desktop-connect.svg`, `desktop-connect-light.svg`) are the connect screen's web view rendered in Chromium with the Tauri IPC stubbed (`node docs/screenshots/desktop.mjs`), so they have no macOS title bar. They are SVG like every other screenshot.

