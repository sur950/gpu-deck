# Alembic migrations (SQL-first, one folder per migration)

Raw SQL is the source of truth; Alembic only records which folders have been applied. Each migration is a folder:

```
app/db/postgres/alembic/migrations/
├── 001_init/            # baseline: every table of the first release
│   ├── up.sql
│   ├── down.sql
│   └── migration.py     # wrapper that executes the SQL via the raw DBAPI cursor
└── NNN_<snake_slug>/    # later migrations (see Numbering)
```

## Numbering

`001_init` is the baseline and holds every table of the first release. Later migrations are numbered from the folder name only; the number does not set the apply order. Alembic follows one linear chain, so a new migration's `down_revision` is the current head: `venv/bin/alembic heads` must print exactly one revision (`tests/unit/test_platform_conventions.py` enforces it). Pick the next free number; maintainers renumber at merge time if two pull requests collide.

Existing folders: `001_init`, `002_partition_fns_schema_aware`, `400_layout_unlisted_policy`, `600_llamacpp_router_seed`, `601_gpu_instances`, `650_cloud_instances`.

Prefer extending the schema with a new migration over editing `001_init`; never edit an applied migration.

## Commands (run from `apps/fastapi/`)

```bash
./app/db/postgres/scripts/create_migration.sh "add node labels" 203   # number is optional; defaults to last+1
./app/db/postgres/scripts/migrate.sh                                  # alembic current && upgrade head
./app/db/postgres/scripts/generate_models.sh                          # sqlacodegen -> models/generated_models.py (committed, never hand-edited; CI runs it with --check)
venv/bin/alembic downgrade -1 && venv/bin/alembic upgrade head        # test both directions
```

## Rules

- `up.sql` is idempotent where possible (`IF NOT EXISTS`), `down.sql` reverses it in the opposite order.
- `migration.py` executes the SQL with `op.get_bind().connection.cursor().execute(sql)` so `'value'::type` casts are not mistaken for bind parameters. `VACUUM` lines are stripped from `up.sql` automatically.
- Every folder must be listed in `alembic.ini` `version_locations` (the create script does it; `tests/unit/test_alembic_migrations.py` enforces it).
- Never edit an applied migration; add a new one.
- SQL style: UUID PKs `gen_random_uuid()`, `TIMESTAMPTZ`, `created_at/updated_at/deleted_at`, enums via `CREATE TYPE`, partial indexes `WHERE deleted_at IS NULL`, `pg_trgm` GIN for search columns, `COMMENT ON` for tables and non-obvious columns, `update_updated_at_column()` trigger.
