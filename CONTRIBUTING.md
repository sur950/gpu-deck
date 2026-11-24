# Contributing to GPU Deck

Thank you for your interest in GPU Deck. This guide explains how to set up each part of the project, the conventions the code follows, and how to get a change merged.

## Ground rules

- Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- Security issues are reported privately, never in a public issue. See [docs/SECURITY.md](docs/SECURITY.md).
- Larger changes start with an issue or a [discussion](https://github.com/sur950/gpu-deck/discussions), so we can agree on the approach before you write the code.
- Questions are welcome in Discussions. See [docs/SUPPORT.md](docs/SUPPORT.md).

## Project layout

GPU Deck is a monorepo with several languages.

| Path | What lives there | Language |
|---|---|---|
| `apps/fastapi` | Admin API, serving API (OpenAI, Anthropic and Ollama formats), agent hub, Celery worker and scheduler | Python 3.12 |
| `apps/web` | Admin web UI | Next.js, TypeScript |
| `apps/node-agent` | Agent that runs on every GPU machine: device plugins, engine lifecycle, mTLS proxy | Rust |
| `apps/desktop` | Desktop app, a Tauri 2 shell around a control plane's web UI | Rust, TypeScript |
| `apps/cli` | The `gpudeck` command line | Python 3.12 |
| `apps/terraform-provider` | Terraform provider | Go |
| `infra/` | Docker Compose files, Helm chart, offline bundle, observability, dev scripts | YAML, shell |
| `libs/contracts` | API, agent protocol, metrics, rule schemas and the environment variable reference | OpenAPI, JSON Schema, Markdown |
| `docs/` | Deployment, architecture, operations, security and development guides, and per-component notes | Markdown |

Read [docs/architecture.md](docs/architecture.md) before a larger change, and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for what runs where. The backend and agent conventions are summarised below and described in full in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md), the reference for layering, naming, Redis keys, migrations, extension points and tests.

## Setup

You need Docker with the compose plugin (OrbStack, Docker Desktop, Colima or Docker Engine). Postgres and Redis for development run as containers, so nothing else is installed on your host. Per component you also need:

| Component | Tools |
|---|---|
| Backend and CLI | Python 3.12 and [uv](https://docs.astral.sh/uv/) |
| Web, desktop | Node 22 or newer (CI uses 26) and `pnpm` (the version in `package.json` `packageManager`) |
| Node agent, desktop shell | Rust 1.95 or newer, with `rustfmt` and `clippy` |
| Terraform provider | Go 1.26 or newer (`go.mod` names the toolchain CI uses), and Terraform for the acceptance tests |
| Helm chart | Helm 4 |

No model downloads are needed to work on GPU Deck. Tests use fixtures and small local models only.

### 1. Fork and clone

Work happens in your own fork, and changes come back through a pull request.

```bash
gh repo fork sur950/gpu-deck --clone     # or fork in the browser and clone your fork
cd gpu-deck
git remote add upstream https://github.com/sur950/gpu-deck.git   # skip if gh added it
```

Before you start something new, bring your fork's `main` up to date:

```bash
git switch main
git fetch upstream
git merge --ff-only upstream/main
git push origin main
```

### 2. Backend and everything together

From the repository root:

```bash
./infra/scripts/bootstrap.dev.sh              # Postgres + Redis containers, .env, venv, migrations, seed, start all
./infra/scripts/bootstrap.dev.sh verify       # smoke-test the endpoints
./infra/scripts/bootstrap.dev.sh stop         # stop app processes (add --deps to stop the containers too)
./infra/scripts/bootstrap.dev.sh deps up      # only the Postgres and Redis containers
```

`bootstrap` creates the Python virtual environment in `apps/fastapi/venv`, writes `apps/fastapi/.env` with generated credentials and starts the admin API (8180), API layer (8181), agent hub (8443), worker, scheduler and web UI (3180). Run `./infra/scripts/bootstrap.dev.sh urls` for the addresses. [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) has the full development loop.

To work on the backend by hand:

```bash
cd apps/fastapi
venv/bin/uvicorn app.main:app --port 8180 --reload          # admin API
venv/bin/uvicorn app.api_layer_main:app --port 8181 --reload  # serving API
```

### 3. Web UI

```bash
pnpm install
pnpm --filter web dev          # http://localhost:3180, talks to the admin API on 8180
```

### 4. Node agent

```bash
cd apps/node-agent
cargo build --release -p gpudeck-agent
```

### 5. Desktop app

```bash
pnpm install
pnpm --filter desktop tauri dev       # Vite on 127.0.0.1:1420
```

### 6. CLI

```bash
cd apps/cli
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/gpudeck --help
```

### 7. Terraform provider

```bash
cd apps/terraform-provider
go build ./...
```

## Code rules

Backend (`apps/fastapi`):

- Layers: routes (HTTP only) call services (`app/services/<domain>/service.py`), services call repositories (all SQL, flush never commit), repositories use the generated models. Workers run off the hot path.
- Responses use the `{success, data, error, meta}` envelope. Errors are subclasses of the exceptions in `app/core/exceptions.py`. The serving API returns errors in the OpenAI, Anthropic or Ollama shape instead.
- Every Redis key, stream and channel is `gpu-deck:<domain>:<event-or-key>`. Use `settings.redis_key(...)`.
- Middleware is pure ASGI. No `BaseHTTPMiddleware`.
- Secrets are never logged. Passwords use Argon2id, API keys are stored as SHA-256 hashes, vault values use Fernet.
- Every module starts with a docstring, then `logger = get_logger(__name__)`. black, isort and flake8 at 100 columns, mypy with `--ignore-missing-imports`.

Web (`apps/web`):

- `pnpm` only. Kebab-case file names and `@/` import aliases.
- `components/ui` is shadcn and is never edited by hand. Shared pieces go in `components/shared`, screen-specific ones in the route's `_components`.
- Hooks live in `hooks/use-*.ts`, types in `types/<domain>.ts`, API calls in `lib/api/`. Generated API types are never edited by hand.
- Every screen must look right in light and dark mode.

Node agent and desktop shell (Rust): `cargo fmt`, no `clippy` warnings, engines bind to `127.0.0.1` only, and nothing in the agent opens an inbound control port.

CLI (Python): same formatters as the backend. There is no generated client: commands are built at runtime from the OpenAPI document (`apps/cli/README.md`).

Terraform provider (Go): `gofmt` and `go vet`. The generated client is never hand-edited.

## Tests and linting

Run the checks for the parts you changed. CI runs all of them on every pull request.

| Component | Lint | Test |
|---|---|---|
| Backend | `cd apps/fastapi && black --check -l 100 app tests && isort --check-only --profile black -l 100 app tests && flake8 --max-line-length 100 --extend-ignore E203,W503 app tests` | `./tests/scripts/run_tests.sh unit`, then `./tests/scripts/run_tests.sh integration` (needs `bootstrap.dev.sh deps up`). The whole suite: `venv/bin/pytest tests` |
| Backend checks | `./app/db/postgres/scripts/generate_models.sh --check` and, from the repo root, `apps/fastapi/venv/bin/python scripts/check_env_docs.py` | |
| Web | `cd apps/web && pnpm lint && pnpm type:check && node scripts/gen-api.mjs --check` | `pnpm test`, `pnpm build`, and `pnpm e2e` for browser tests |
| Node agent | `cd apps/node-agent && cargo fmt --all --check && cargo clippy --workspace --all-targets --locked -- -D warnings` | `cargo test --workspace --locked` |
| Desktop | `cd apps/desktop && pnpm type:check`, and in `src-tauri`: `cargo clippy --all-targets --locked -- -D warnings` | `pnpm test`, and in `src-tauri`: `cargo test --locked` |
| CLI | `cd apps/cli && .venv/bin/black --check gpudeck tests && .venv/bin/isort --check-only gpudeck tests && .venv/bin/flake8 gpudeck tests && .venv/bin/mypy gpudeck` | `.venv/bin/python -m pytest` |
| Terraform provider | `cd apps/terraform-provider && test -z "$(gofmt -l .)" && go vet ./...` | `go test ./... -count=1` |
| Helm and Docker | `helm lint infra/helm/gpu-deck --strict --set secrets.existingSecret=ci` and `docker compose -f infra/docker/docker-compose.yml config -q` | `helm template ci infra/helm/gpu-deck --set secrets.existingSecret=ci` renders without errors |
| Docs and config | `pnpm format:check` (Prettier) | |

Backend unit tests use mocks only. Integration tests use the real Postgres and Redis containers, are marked `@pytest.mark.integration`, and build a scratch schema with `tests/schema_helpers.py::create_scratch_schema`. They must restore every global they change. New behavior needs tests. Use recorded or hand-written fixtures for hardware you do not own (vendor tool output, engine responses, cloud provider replies).

## Common changes

### Add a database migration

Migrations are plain SQL.

```bash
cd apps/fastapi
./app/db/postgres/scripts/create_migration.sh "add widget table" NNN
```

This creates `app/db/postgres/alembic/migrations/NNN_slug/{up.sql,down.sql,migration.py}` and registers it in `alembic.ini`. Pick `NNN` as the next free number; maintainers will renumber at merge time if two pull requests collide. The new migration's `down_revision` must be the current head: `venv/bin/alembic heads` must print exactly one revision. Follow the SQL style in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#migrations) (UUID primary keys, `TIMESTAMPTZ`, soft delete, partial indexes, `COMMENT ON`, the `update_updated_at_column()` trigger, schema-aware type checks). Then regenerate and commit the models:

```bash
./app/db/postgres/scripts/generate_models.sh
```

`generated_models.py` is committed and never edited by hand. CI runs it with `--check`.

### Add a setting

Create or edit `app/core/settings/<domain>.py` with an `UPPER_SNAKE` field on a `DomainSettings` subclass. Then document it in `libs/contracts/env.md` and regenerate the example files:

```bash
python scripts/check_env_docs.py --write
```

Both `.env.example` files are generated. CI fails if a setting, `env.md` and the examples disagree.

### Add an admin endpoint

1. Describe it in `libs/contracts/openapi/admin.yaml`.
2. Add a route file `app/api/v1/routes/<name>.py` with `router = APIRouter()` and full paths. The router registry mounts it under `/api/v1`. Routes are HTTP only: decorator order `@router.<method>`, `@rate_limit`, `@handle_exceptions`, and return `success_response(...)`.
3. Put the logic in `app/services/<domain>/service.py` and all SQL in a repository.
4. Add unit and integration tests.
5. Regenerate the typed clients that consume the contract: `cd apps/web && pnpm gen:api` and `go generate ./...` in `apps/terraform-provider`. The CLI reads the spec at runtime; a new operation only needs a `COMMAND_MAP` line in `apps/cli/gpudeck/registry.py` (its coverage test says which).

Serving endpoints go in `app/api/serving/{openai,anthropic,ollama}/<name>.py` and do not use `@handle_exceptions`.

Other extension points (middleware, startup hooks, Celery tasks, scheduled jobs) are listed in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#extension-points). Add a file for them; do not edit a shared registry.

### Add a device plugin or engine

Device plugins and engine adapters live in `apps/node-agent/crates/gpudeck-devices` and `gpudeck-engines`. Add fixtures of the real tool output (for example `nvidia-smi`, `rocm-smi`) next to the tests. If you can test on real hardware, please also file a [hardware report](https://github.com/sur950/gpu-deck/issues/new?template=hardware_report.yml).

## Reporting bugs

Search the existing issues first, then open a [bug report](https://github.com/sur950/gpu-deck/issues/new?template=bug_report.yml). Say which component is affected, the version, how you deploy GPU Deck, your GPU vendor and engine, the exact steps, and the relevant logs. Redact API keys (`gd-...`), join tokens (`gdj_...`), vault keys, certificates and hostnames you do not want public. Security problems go to a private advisory, not an issue.

## Pull requests

1. Create a branch from an up to date `main` in your fork:
   ```bash
   git switch -c fix/short-description
   ```
2. Keep the change focused. One topic per pull request.
3. Commit with [Conventional Commits](https://www.conventionalcommits.org/) messages, for example `fix(agent): retry join after hub restart` or `feat(web): add GPU temperature column`. Common types are `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci` and `chore`. Common scopes are `api`, `serving`, `hub`, `web`, `agent`, `desktop`, `cli`, `terraform`, `helm`, `docker` and `docs`. Mark breaking changes with `!` and a `BREAKING CHANGE:` footer. A sign-off (DCO) is not required.
4. Run the checks for the parts you changed (see Tests and linting).
5. Push the branch to your fork and open a pull request to `sur950/gpu-deck:main` (GitHub offers a button after the push, or run `gh pr create --repo sur950/gpu-deck`). Fill in the template.
6. If `main` moves on while your pull request is open, rebase on it and push again:
   ```bash
   git fetch upstream
   git rebase upstream/main
   git push --force-with-lease
   ```

CI lints and tests every component touched by the pull request, builds the images and checks the Helm chart.

### Contributor checklist

Before you request a review, confirm that:

- [ ] Tests are added or updated and pass for every component you touched
- [ ] Lint and type checks pass (black, isort, flake8, mypy, eslint, `tsc`, `cargo fmt`, `clippy`, `gofmt`, `go vet`, `helm lint`)
- [ ] Docs are updated, and `CHANGELOG.md` has an entry under `[Unreleased]`
- [ ] New or changed settings are in `libs/contracts/env.md` and `python scripts/check_env_docs.py --write` has been run
- [ ] A new migration is added with `generate_models.sh` re-run, and `alembic heads` prints one revision
- [ ] API contract changes are in `libs/contracts/openapi/admin.yaml`, the generated web and Terraform clients are regenerated, and new operations have a CLI `COMMAND_MAP` entry
- [ ] UI changes include screenshots in light and dark mode
- [ ] No secrets, tokens, keys, certificates or personal data in code, tests, fixtures, logs or screenshots
- [ ] No model files or other large binaries are committed

## Releases

Maintainers cut releases. Versions follow [Semantic Versioning](https://semver.org/), all components share one version number, and release notes come from `CHANGELOG.md`. The release steps (version bump, tag, images, desktop and agent binaries, Helm chart and website) are in [docs/RELEASING.md](docs/RELEASING.md).

### Keeping dependencies current

There are no Dependabot pull requests. Maintainers run `scripts/update_deps.sh` on `main` (npm, Python, Rust and Go to their newest releases), fix what new majors break and run `scripts/ci_local.sh` before pushing; see [Keeping dependencies current](docs/RELEASING.md#keeping-dependencies-current) for what it covers, the packages held back and why, and what is bumped by hand. In a pull request, do not bump unrelated dependencies.

## License

GPU Deck is licensed under the [Apache License 2.0](LICENSE). By contributing you agree that your contribution is licensed under the same license. Code ported from GPUStack keeps its attribution as described in [NOTICE](NOTICE).
