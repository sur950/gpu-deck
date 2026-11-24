#!/usr/bin/env bash
# Runs what GitHub runs (.github/workflows/ci.yml, plus the checks release.yml makes before it
# builds) on a clean copy of the repository, before you push or tag. The copy has only the files
# git would push (tracked, plus untracked files that are not ignored), so a missing or ignored
# file fails here instead of on GitHub.
#
# Usage: scripts/ci_local.sh [STEP ...]
#   STEP: version workflows backend web agent desktop cli terraform infra website release images all
#   Default: every step except `images` (Docker builds of the three images, slow).
#
# Needs: git, rsync, python3, docker compose (backend, images), uv (backend, cli), node + pnpm
# (web, desktop), cargo (agent, desktop), go (terraform, workflows), helm (infra), python3 with
# the pinned site tools of scripts/requirements-docs.txt (website; installed into a venv). A step whose
# tool is missing fails with a message; pass the steps you can run.
#
# Differences from GitHub, on purpose:
#   - Rust and pnpm caches are shared with your machine; the Rust target directories live in
#     $CI_LOCAL_DIR/.cache so rebuilds are incremental across runs.
#   - The agent cross-compile runs only when cargo-zigbuild and zig are installed.
#   - The desktop Rust checks run on the host OS (GitHub runs them on Linux).
#   - Nothing is pushed, signed or published.
#
# Environment: CI_LOCAL_DIR (default $TMPDIR/gpu-deck-ci), KEEP=1 keeps the copy afterwards.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMP="${TMPDIR:-/tmp}"
BASE="${CI_LOCAL_DIR:-${TMP%/}/gpu-deck-ci}"
BASE="${BASE%/}"
WORK="$BASE/repo"
CACHE="$BASE/.cache"
export PATH="$HOME/.cargo/bin:$PATH"

ALL_STEPS=(version workflows backend web agent desktop cli terraform infra website release)
if [[ $# -eq 0 ]]; then
  STEPS=("${ALL_STEPS[@]}")
elif [[ "$1" == "all" ]]; then
  STEPS=("${ALL_STEPS[@]}" images)
else
  STEPS=("$@")
fi

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
fail() { printf 'error: %s\n' "$1" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "$1 is required for this step"; }
PASSED=()

cleanup() {
  if [[ -x "$WORK/scripts/ci_deps.sh" ]]; then
    (cd "$WORK" && scripts/ci_deps.sh down >/dev/null 2>&1) || true
  fi
  if [[ "${KEEP:-0}" != "1" ]]; then rm -rf "$WORK"; fi
}
trap cleanup EXIT

step "Clean copy of what git would push ($WORK)"
need git
need rsync
rm -rf "$WORK"
mkdir -p "$WORK" "$CACHE"
# Files deleted in the working tree are still in the index until the commit, so skip those.
(cd "$REPO" && git ls-files -z --cached --others --exclude-standard | while IFS= read -r -d '' f; do
  [[ -e "$f" ]] && printf '%s\0' "$f"
done) | (cd "$REPO" && rsync -a --from0 --files-from=- ./ "$WORK/")
cd "$WORK"
echo "$(find . -type f | wc -l | tr -d ' ') files, version $(cat VERSION)"

pnpm_install() {
  need pnpm
  [[ -d node_modules ]] || pnpm install --frozen-lockfile --filter web... --filter desktop...
}

# ------------------------------------------------------------------------------------- steps
run_version() {
  step "Versions, CHANGELOG section, shell scripts"
  python3 scripts/version.py --check -q
  scripts/release_notes.sh --section "$(cat VERSION)" > /dev/null
  for f in scripts/*.sh; do bash -n "$f"; done
}

run_workflows() {
  step "actionlint (.github/workflows)"
  if command -v actionlint >/dev/null; then
    actionlint .github/workflows/*.yml
  else
    need go
    go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12 .github/workflows/*.yml
  fi
}

run_backend() {
  step "Backend: lint, Postgres + Redis, migrations, models, env docs, pytest"
  need uv
  need docker
  (
    cd apps/fastapi
    uv venv -q --python 3.12 venv
    uv pip install -q --python venv/bin/python -r requirements.txt
    venv/bin/black --check -q -l 100 app tests
    venv/bin/isort --check-only -q --profile black -l 100 app tests
    venv/bin/flake8 app tests
  )
  scripts/ci_deps.sh up
  (
    cd apps/fastapi
    venv/bin/alembic upgrade head
    ./app/db/postgres/scripts/generate_models.sh --check
    venv/bin/python ../../scripts/check_env_docs.py
    venv/bin/pytest tests -q -p no:cacheprovider
  )
  scripts/ci_deps.sh down
}

run_web() {
  step "Web: lint, types, generated API, tests, build"
  pnpm_install
  pnpm --filter web lint
  pnpm --filter web type:check
  (cd apps/web && node scripts/gen-api.mjs --check)
  pnpm --filter web test
  NEXT_TELEMETRY_DISABLED=1 pnpm --filter web build
}

run_agent() {
  step "Node agent: fmt, clippy -D warnings, tests"
  need cargo
  (
    cd apps/node-agent
    export CARGO_TARGET_DIR="$CACHE/target-node-agent"
    if cargo fmt --version >/dev/null 2>&1; then
      cargo fmt --all --check
    else
      echo "skip cargo fmt: rustfmt is not installed (rustup component add rustfmt)"
    fi
    cargo clippy --workspace --all-targets --locked -- -D warnings
    cargo test --workspace --locked
  )
  if command -v cargo-zigbuild >/dev/null && command -v zig >/dev/null; then
    step "Node agent: cross-compile Linux x86_64 + aarch64 (glibc 2.28)"
    infra/scripts/cross-compile-agent.sh --version ci --out "$BASE/agent-dist" \
      --targets "x86_64-unknown-linux-gnu.2.28 aarch64-unknown-linux-gnu.2.28"
  else
    echo "skip cross-compile: cargo-zigbuild and zig are not installed"
  fi
}

run_desktop() {
  step "Desktop: types, tests, build, Rust fmt/tests/clippy"
  pnpm_install
  pnpm --filter desktop type:check
  pnpm --filter desktop test
  pnpm --filter desktop build
  need cargo
  (
    cd apps/desktop/src-tauri
    export CARGO_TARGET_DIR="$CACHE/target-desktop"
    cargo fmt --check
    cargo test --locked
    cargo clippy --all-targets --locked -- -D warnings
  )
}

run_cli() {
  step "CLI: black, isort, flake8, mypy, pytest, build"
  need uv
  (
    cd apps/cli
    uv venv -q --python 3.12 .venv
    uv pip install -q --python .venv/bin/python -e '.[dev]'
    .venv/bin/black --check -q gpudeck tests
    .venv/bin/isort --check-only -q gpudeck tests
    .venv/bin/flake8 gpudeck tests
    .venv/bin/mypy gpudeck
    .venv/bin/python -m pytest -q -p no:cacheprovider
    rm -rf "$BASE/cli-dist" "$BASE/cli-smoke" "$BASE/cli-smoke-home"
    uv build -q --out-dir "$BASE/cli-dist"
    # the wheel carries the bundled spec and runs from a clean venv
    wheel="$BASE/cli-dist/gpudeck_cli-$(cat ../../VERSION)-py3-none-any.whl"
    python3 -c 'import sys, zipfile; sys.exit("gpudeck/_spec/admin.yaml" not in zipfile.ZipFile(sys.argv[1]).namelist())' "$wheel"
    uv venv -q --python 3.12 "$BASE/cli-smoke"
    uv pip install -q --python "$BASE/cli-smoke/bin/python" "$wheel"
    GPUDECK_CONFIG_DIR="$BASE/cli-smoke-home" GPUDECK_CACHE_DIR="$BASE/cli-smoke-home" GPUDECK_SPEC=bundled \
      "$BASE/cli-smoke/bin/gpudeck" clusters create --help >/dev/null
    "$BASE/cli-smoke/bin/gpudeck" --version
    rm -rf "$BASE/cli-smoke" "$BASE/cli-smoke-home"
  )
}

run_terraform() {
  step "Terraform provider: gofmt, vet, tests, build"
  need go
  (
    cd apps/terraform-provider
    unformatted="$(gofmt -l .)"
    [[ -z "$unformatted" ]] || fail "gofmt: $unformatted"
    go vet ./...
    go test ./... -count=1
    go build -trimpath -o "$BASE/terraform-provider-gpudeck" .
  )
}

run_infra() {
  step "Helm lint + kubeconform, dashboards, scripts, compose"
  need helm
  helm lint infra/helm/gpu-deck --strict --set secrets.existingSecret=ci
  local kc=(kubeconform)
  command -v kubeconform >/dev/null || kc=(go run github.com/yannh/kubeconform/cmd/kubeconform@v0.8.0)
  for args in "" "--set nodeAgent.enabled=true --set nodeAgent.joinTokenSecret=jt"; do
    # shellcheck disable=SC2086
    helm template ci infra/helm/gpu-deck --set secrets.existingSecret=ci $args \
      | "${kc[@]}" -strict -summary -ignore-missing-schemas -kubernetes-version 1.31.0
  done
  python3 infra/observability/grafana/build_dashboards.py --check
  for f in infra/scripts/*.sh infra/offline/*.sh infra/docker/scripts/*.sh apps/node-agent/packaging/*.sh; do
    [[ -f "$f" ]] && bash -n "$f"
  done
  if command -v docker >/dev/null; then
    touch apps/fastapi/.env
    docker compose -f infra/docker/docker-compose.yml config -q
    GPUDECK_DEPS_PG_SUPERUSER_PASSWORD=x GPUDECK_DEPS_PG_PASSWORD=x GPUDECK_DEPS_REDIS_PASSWORD=x \
      docker compose -f infra/docker/docker-compose.deps.yml --profile proxy config -q
    rm -f apps/fastapi/.env
  fi
}

run_website() {
  step "Website: screenshots (SVG), docs build in check mode"
  need python3
  local venv="$CACHE/site-venv"
  [[ -x "$venv/bin/python" ]] || python3 -m venv "$venv"
  "$venv/bin/pip" install -q -r scripts/requirements-docs.txt
  (
    export PATH="$venv/bin:$PATH"
    # website/assets/screenshots/ is git-ignored, so the clean copy starts without it: generate
    # it as pages.yml does, then check it matches docs/screenshots/.
    website/sync-screenshots.sh
    website/sync-screenshots.sh --check
    python scripts/build_docs.py --check
  )
}

run_release() {
  step "Release dry run: notes, chart package, collect scripts"
  local v
  v="$(cat VERSION)"
  scripts/release_notes.sh "$v" > "$BASE/release-notes.md"
  head -5 "$BASE/release-notes.md"
  python3 -c "import json,sys; json.load(open('apps/terraform-provider/terraform-registry-manifest.json'))"
  if command -v helm >/dev/null; then
    helm package infra/helm/gpu-deck --version "$v" --app-version "$v" -d "$BASE/helm" >/dev/null
    echo "chart: $(ls "$BASE/helm")"
  fi
  if command -v goreleaser >/dev/null; then
    (cd apps/terraform-provider && goreleaser check)
  else
    echo "skip goreleaser check: goreleaser is not installed"
  fi
}

run_images() {
  step "Docker images (build only)"
  need docker
  docker build -q -f infra/docker/api/Dockerfile -t gpu-deck-api:ci-local .
  docker build -q -f infra/docker/web/Dockerfile -t gpu-deck-web:ci-local .
  docker build -q -f infra/docker/node-agent/Dockerfile -t gpu-deck-agent:ci-local apps/node-agent
}

for s in "${STEPS[@]}"; do
  case "$s" in
    version|workflows|backend|web|agent|desktop|cli|terraform|infra|website|release|images) "run_$s" ;;
    *) fail "unknown step: $s (version workflows backend web agent desktop cli terraform infra website release images all)" ;;
  esac
  PASSED+=("$s")
done

step "Passed: ${PASSED[*]}"
