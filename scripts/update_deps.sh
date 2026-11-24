#!/usr/bin/env bash
# Bring the dependencies to their newest releases (Dependabot version updates are off; this is how
# main stays current). Run it on a clean checkout of main, then scripts/ci_local.sh, then commit.
#
# Usage: scripts/update_deps.sh [--check] [--no-install] [ECOSYSTEM ...]
#   ECOSYSTEM     npm python rust go (default: all four)
#   --check       report what is outdated and change nothing; exits 1 when something is
#   --no-install  do not install cargo-edit (Rust majors are then reported, not applied)
#
#   npm     pnpm up --latest in the workspace (root, apps/web, apps/desktop) and in
#           docs/screenshots. pnpm's minimumReleaseAge (1 day) skips versions published in the
#           last 24 hours. Held: typescript < 7 (eslint-config-next's typescript-eslint needs the
#           TypeScript 6 JS API).
#   python  scripts/update_python_pins.py on apps/fastapi/requirements.txt, apps/cli/pyproject.toml
#           and scripts/requirements-docs.txt (exact pins; holds and their reasons are listed in
#           that script).
#   rust    cargo upgrade --incompatible allow (cargo-edit, installed into a temporary directory
#           when missing), then cargo update, in apps/node-agent and apps/desktop/src-tauri.
#   go      go get -u -t ./... and the newest toolchain line, then go mod tidy, in
#           apps/terraform-provider.
#
# Not covered, bump by hand (docs/RELEASING.md, "Keeping dependencies current"): the Docker base
# and compose images, GitHub Actions, tool versions in the workflows (Node, Zig, cargo-zigbuild,
# Helm, Terraform, Trivy) and pnpm itself (packageManager in package.json).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CHECK=0
INSTALL=1
STEPS=()
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --no-install) INSTALL=0 ;;
    npm|python|rust|go) STEPS+=("$arg") ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg (see --help)" >&2; exit 2 ;;
  esac
done
[[ ${#STEPS[@]} -gt 0 ]] || STEPS=(npm python rust go)

export PATH="$HOME/.cargo/bin:$PATH"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
OUTDATED=0
NOTES=()

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "error: $1 is required for '$2'" >&2; exit 1; }; }

run_npm() {
  step "npm (pnpm workspace and docs/screenshots)"
  need pnpm npm
  cd "$REPO"
  if [[ $CHECK == 1 ]]; then
    pnpm outdated -r '!typescript' || OUTDATED=1
    (cd docs/screenshots && pnpm outdated --ignore-workspace) || OUTDATED=1
    return
  fi
  pnpm up -r --latest '!typescript'
  (cd docs/screenshots && pnpm up --latest --ignore-workspace --lockfile-only)
  NOTES+=("npm: typescript held below 7 (typescript-eslint, via eslint-config-next, needs <6.1)")
}

run_python() {
  step "Python pins (backend, CLI, site tools)"
  need python3 python
  cd "$REPO"
  local files=(apps/fastapi/requirements.txt apps/cli/pyproject.toml scripts/requirements-docs.txt)
  if [[ $CHECK == 1 ]]; then
    python3 scripts/update_python_pins.py --check "${files[@]}" || OUTDATED=1
  else
    python3 scripts/update_python_pins.py "${files[@]}"
  fi
}

cargo_upgrade_available() {
  cargo upgrade --version >/dev/null 2>&1 && return 0
  [[ $INSTALL == 1 ]] || return 1
  echo "installing cargo-edit into $TMP/cargo-edit (removed on exit)"
  CARGO_TARGET_DIR="$TMP/cargo-target" cargo install --quiet --locked cargo-edit --root "$TMP/cargo-edit"
  export PATH="$TMP/cargo-edit/bin:$PATH"
}

run_rust() {
  step "Rust (apps/node-agent, apps/desktop/src-tauri)"
  need cargo rust
  local majors=1
  cargo_upgrade_available || majors=0
  for dir in apps/node-agent apps/desktop/src-tauri; do
    echo "--- $dir"
    cd "$REPO/$dir"
    if [[ $CHECK == 1 ]]; then
      if [[ $majors == 1 ]]; then
        cargo upgrade --incompatible allow --dry-run 2>&1 | grep -v '^\s*$' || true
      fi
      cargo update --dry-run 2>&1 | grep -E '^ *(Updating|Adding|Downgrading) [^ ]+ v[0-9]' \
        && OUTDATED=1 || true
      continue
    fi
    if [[ $majors == 1 ]]; then
      cargo upgrade --incompatible allow
    fi
    cargo update
  done
  if [[ $majors == 0 ]]; then
    NOTES+=("rust: new majors not applied (no cargo-edit); run without --no-install or edit Cargo.toml")
  fi
  NOTES+=("rust: check rust-version in the Cargo.toml files against the newest dependency MSRV")
}

run_go() {
  step "Go (apps/terraform-provider)"
  need go go
  cd "$REPO/apps/terraform-provider"
  if [[ $CHECK == 1 ]]; then
    go list -m -u -f '{{if and (not .Indirect) .Update}}{{.Path}} {{.Version}} -> {{.Update.Version}}{{end}}' all \
      | grep . && OUTDATED=1 || true
    return
  fi
  go get toolchain@latest
  go get -u -t ./...
  go mod tidy
}

for s in "${STEPS[@]}"; do "run_$s"; done

step "Summary"
cd "$REPO"
if [[ $CHECK == 1 ]]; then
  [[ $OUTDATED == 1 ]] && echo "outdated dependencies found (see above)" || echo "everything is current"
  exit "$OUTDATED"
fi
git status --short -- . ':!**/node_modules/**'
for n in "${NOTES[@]}"; do echo "note: $n"; done
echo
echo "next: read the changelogs of new majors, fix what breaks, run scripts/ci_local.sh, commit."
