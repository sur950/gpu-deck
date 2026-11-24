# GPU Deck backend (`apps/fastapi`)

> FastAPI control plane for GPU Deck: admin API, serving API (OpenAI / Anthropic / Ollama) and background jobs, built from one codebase.

## Processes

| Process | Entry point | Local port | Runs |
| --- | --- | --- | --- |
| `admin-api` | `uvicorn app.main:app` | 8180 | REST API for the web UI, CLI and Terraform under `/api/v1` |
| `api-layer` | `uvicorn app.api_layer_main:app` | 8181 | `/v1` (OpenAI), `/anthropic/v1` (Anthropic), `/api` (Ollama), API-key auth, `/health`, `/metrics` |
| `agent-hub` | `python -m app.agent_hub_main` | 8443 | Node join (`/agent/v1/join`), agent WebSocket (`/agent/v1/ws`, mTLS), CA and agent downloads |
| `worker` | `celery -A app.workers.celery_app worker --beat` | — | Celery tasks (usage writes, audit, rollups) + beat |
| `scheduler` | `python -m app.workers.scheduler` | — | APScheduler jobs (rules, schedules, billing) with a Redis job store |

Dependencies for development (Docker, `infra/docker/docker-compose.deps.yml`): Postgres 17 on `127.0.0.1:55432`, Redis 7.4 on `127.0.0.1:56379`. Optional HTTPS proxy (`bootstrap.dev.sh caddy`): `https://gpu-deck.localhost:58443` → web 3180, `https://gpu-deck-api.localhost:58443` → 8181, `https://gpu-deck-admin.localhost:58443` → 8180.

## Architecture layers

| Layer | Responsibility |
| --- | --- |
| **Routes** (`app/api/v1/routes/`, `app/api/serving/`) | HTTP only — parse, call a service, return `success_response(...)` |
| **Services** (`app/services/<domain>/service.py`) | Business logic, orchestration, side effects |
| **Repositories** (`app/db/postgres/repositories/`) | All SQL via SQLAlchemy async (`BaseRepository`); flush, never commit |
| **Models** (`app/db/postgres/models/generated_models.py`) | sqlacodegen output, committed; regenerate with `generate_models.sh` (scratch schema from the migrations; `--check` in CI), never hand-edited |
| **Workers** (`app/workers/`) | Celery tasks + APScheduler jobs |

## Extension points (how a domain plugs in without editing shared files)

| Need | Drop a file | Picked up by |
| --- | --- | --- |
| Admin endpoints | `app/api/v1/routes/<name>.py` exposing `router` | `app/api/router_registry.py` → mounted under `/api/v1` |
| Serving endpoints | `app/api/serving/{openai,anthropic,ollama}/<name>.py` exposing `router` | same registry; prefix from the package's `PREFIX` |
| Settings | `app/core/settings/<domain>.py` with a `DomainSettings` subclass | `app/core/settings/__init__.py` merges into `settings` |
| Middleware | `app/middlewares/<name>.py` with `install(app, process)` (+ `ORDER`, `PROCESSES`) | `app/middlewares/__init__.py` |
| Startup/shutdown work | `app/services/<domain>/hooks.py` with `@on_startup("admin")` etc. | `app/core/lifecycle.py` |
| Celery tasks | `app/workers/tasks/<domain>_tasks.py` (+ `QUEUE`, `BEAT_SCHEDULE`) | `app/workers/celery_app.py` |
| Periodic jobs | `@scheduled_job("domain.verb", "interval", minutes=5)` in a task module | `app/workers/scheduler.py` |
| Migrations | `app/db/postgres/alembic/migrations/NNN_slug/` in your reserved range | `alembic.ini` (`create_migration.sh` updates it) |

Redis rule: every key/channel must start with `settings.REDIS_KEY_PREFIX` (`gpu-deck:`); use `settings.redis_key(...)`, `CacheService`, or the pre-configured Celery/APScheduler/slowapi wiring. The dev Redis container's ACL user rejects any other key (NOPERM).

## Setup

```bash
cd apps/fastapi
uv venv --python 3.12 venv
uv pip install --python venv/bin/python -r requirements.txt
cp .env.example .env            # or let ../../infra/scripts/bootstrap.dev.sh create and fill it
```

Required `.env` edits: `SECRET_KEY` (`openssl rand -hex 32`), `VAULT_KEYS` (Fernet key), `ADMIN_PASSWORD`, and the Postgres/Redis blocks (`../../infra/scripts/bootstrap.dev.sh deps up` starts the containers; `bootstrap` copies their credentials from `infra/docker/.env` into `.env`, including `TEST_PG_ADMIN_URL` for integration tests that create scratch databases). Everything else has a sane local default; see `.env.example` for the full list grouped by settings module.

## Run

```bash
../../infra/scripts/bootstrap.dev.sh            # first time: Postgres/Redis containers, venv, migrations, models, seed, start all
../../infra/scripts/bootstrap.dev.sh status     # pids + ports
../../infra/scripts/bootstrap.dev.sh verify     # smoke-test endpoints
../../infra/scripts/bootstrap.dev.sh stop       # add --deps to stop the Postgres/Redis containers too

# or by hand
venv/bin/uvicorn app.main:app --port 8180 --reload --timeout-graceful-shutdown 5
venv/bin/uvicorn app.api_layer_main:app --port 8181 --reload
PYTHONPATH=. venv/bin/celery -A app.workers.celery_app worker --beat --loglevel=info --pool=threads   # macOS: prefork breaks under spawn; Linux can drop --pool
venv/bin/python -m app.workers.scheduler
```

## Database

```bash
./app/db/postgres/scripts/create_migration.sh "add node labels" 203   # NNN is optional; see fastapi-migrations.md
./app/db/postgres/scripts/migrate.sh
./app/db/postgres/scripts/generate_models.sh
```

## Tests

```bash
./tests/scripts/run_tests.sh unit          # no services needed
./tests/scripts/run_tests.sh integration   # real local Postgres + Redis
./tests/scripts/run_tests.sh e2e
```

## Code style

black / isort / flake8 at line length 100, mypy with `--ignore-missing-imports` (`.pre-commit-config.yaml`). Every module starts with a docstring and `logger = get_logger(__name__)`. Never log secrets.

See also: [Development guide](../DEVELOPMENT.md), [migrations](fastapi-migrations.md), [architecture](../architecture.md).
