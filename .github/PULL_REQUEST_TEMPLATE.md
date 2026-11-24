## What and why

<!-- What does this change do, and why is it needed? Link the issue: Fixes #123 -->

## Components touched

<!-- Control plane, API layer, web, node agent, desktop, CLI, Terraform, Helm/Docker, docs -->

## How it was tested

<!-- Commands you ran, and hardware or engines if relevant. -->

## Screenshots

<!-- For UI changes: before and after, light and dark mode. -->

## Checklist

- [ ] Tests added or updated and passing for every component touched
- [ ] Lint and type checks pass (black, isort, flake8, mypy, eslint, tsc, cargo fmt, clippy, gofmt, go vet, helm lint)
- [ ] Docs updated, and `CHANGELOG.md` has an entry under `[Unreleased]`
- [ ] New or changed settings are in `libs/contracts/env.md` and `python scripts/check_env_docs.py --write` was run
- [ ] Migration added, `generate_models.sh` re-run, and `alembic heads` prints one revision (if the schema changed)
- [ ] API contract changed in `libs/contracts/openapi/admin.yaml` and the web types and Terraform client regenerated (the CLI reads the spec at runtime) (if the API changed)
- [ ] Screenshots attached for UI changes, in light and dark mode
- [ ] No secrets, tokens, keys, certificates or personal data anywhere in the change
- [ ] Commits follow Conventional Commits
