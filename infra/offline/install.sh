#!/usr/bin/env bash
# GPU Deck offline installer (run from the unpacked bundle on the air-gapped site).
#
#   ./install.sh verify
#   ./install.sh load-images [--registry registry.local:5000/gpu-deck]
#   ./install.sh compose  --env /etc/gpu-deck/.env [--dir /opt/gpu-deck] [--full] [--observability]
#   ./install.sh helm     --namespace gpu-deck --values my-values.yaml [--registry registry.local:5000/gpu-deck]
#   ./install.sh cli      [--prefix ~/.local]
#   ./install.sh terraform-mirror [--dir /opt/gpu-deck/terraform]
#
# GPU machines: copy install-agent.sh and agent/ to each machine (see install-agent.sh).
# Offline model sources: point HF_ENDPOINT / MODELSCOPE_ENDPOINT in the control plane .env at the
# site mirror; the control plane pushes them to every agent.
set -euo pipefail

BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(cat "$BUNDLE/VERSION")"
log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "missing tool: $1"; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

cmd_verify() {
  log "checking SHA256SUMS"
  (cd "$BUNDLE" && sha256 -c --quiet SHA256SUMS)
  if [[ -f "$BUNDLE/cosign.pub" ]] && command -v cosign >/dev/null; then
    log "cosign public key present: verify pushed images with"
    echo "    cosign verify --key $BUNDLE/cosign.pub --insecure-ignore-tlog <registry>/gpu-deck-api:$VERSION"
  fi
  log "bundle $VERSION OK"
}

cmd_load_images() {
  need docker
  local registry=""
  while [[ $# -gt 0 ]]; do
    case "$1" in --registry) registry="$2"; shift 2 ;; *) die "unknown argument $1" ;; esac
  done
  while IFS=$'\t' read -r name ref _digest; do
    log "loading $ref"
    gunzip -c "$BUNDLE/images/$name.tar.gz" | docker load >/dev/null
    if [[ -n "$registry" ]]; then
      local target
      case "$name" in
        gpu-deck-api|gpu-deck-web|gpu-deck-agent) target="$registry/$name:$VERSION" ;;
        *) target="$registry/${ref##*/}" ;;
      esac
      docker tag "$ref" "$target"
      docker push --quiet "$target" >/dev/null
      echo "    pushed $target"
    fi
  done < "$BUNDLE/images/images.txt"
}

cmd_compose() {
  need docker
  local env_file="" dir="/opt/gpu-deck" full=0 obs=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env) env_file="$2"; shift 2 ;;
      --dir) dir="$2"; shift 2 ;;
      --full) full=1; shift ;;
      --observability) obs=1; full=1; shift ;;
      *) die "unknown argument $1" ;;
    esac
  done
  [[ -n "$env_file" && -f "$env_file" ]] || die "--env <file> is required (start from compose/env.example)"
  mkdir -p "$dir"
  cp -R "$BUNDLE/compose/." "$dir/"
  local files=(-f "$dir/docker/docker-compose.yml")
  [[ $full == 1 ]] && files+=(-f "$dir/docker/docker-compose.full.yml")
  # Agent builds from the bundle: served by the admin API and the hub (no release fetch offline).
  if [[ -d "$BUNDLE/agent" ]]; then
    mkdir -p "$dir/agent-dist"
    cp "$BUNDLE"/agent/* "$dir/agent-dist/"
    cat > "$dir/docker/docker-compose.offline.yml" <<YAML
x-offline-agent: &offline-agent
  volumes: ["$dir/agent-dist:/agent-dist:ro"]
  environment:
    AGENT_DIST_DIR: /agent-dist
    AGENT_RELEASE_URL: ""
services:
  admin-api: *offline-agent
  agent-hub: *offline-agent
YAML
    files+=(-f "$dir/docker/docker-compose.offline.yml")
  fi
  local profiles=()
  [[ $obs == 1 ]] && profiles+=(--profile observability)
  export GPUDECK_ENV_FILE="$env_file" GPUDECK_VERSION="$VERSION"
  export GPUDECK_REGISTRY="${GPUDECK_REGISTRY:-ghcr.io/sur950}"
  log "migrations"
  docker compose "${files[@]}" --profile tools run --rm --pull never migrate
  log "starting services"
  docker compose "${files[@]}" "${profiles[@]}" up -d --no-build
  docker compose "${files[@]}" "${profiles[@]}" ps
}

cmd_helm() {
  need helm
  local ns="gpu-deck" values="" registry=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --namespace) ns="$2"; shift 2 ;;
      --values) values="$2"; shift 2 ;;
      --registry) registry="$2"; shift 2 ;;
      *) die "unknown argument $1" ;;
    esac
  done
  [[ -n "$values" ]] || die "--values <file> is required (secrets.existingSecret, hosts, CIDRs)"
  local chart
  chart="$(ls "$BUNDLE"/helm/gpu-deck-*.tgz | head -1)"
  local args=(upgrade --install gpu-deck "$chart" --namespace "$ns" --create-namespace -f "$values"
    --set "global.imageTag=$VERSION" --wait --timeout 15m)
  [[ -n "$registry" ]] && args+=(--set "global.imageRegistry=$registry")
  log "helm ${args[*]}"
  helm "${args[@]}"
}

cmd_cli() {
  local prefix="$HOME/.local"
  while [[ $# -gt 0 ]]; do
    case "$1" in --prefix) prefix="$2"; shift 2 ;; *) die "unknown argument $1" ;; esac
  done
  local py="${PYTHON:-python3.12}"
  command -v "$py" >/dev/null || die "Python 3.12 is required (set PYTHON=...)"
  local arch; arch="$(uname -m)"; [[ "$arch" == "arm64" ]] && arch=aarch64
  local venv="$prefix/share/gpudeck-cli"
  "$py" -m venv "$venv"
  "$venv/bin/pip" install --no-index --find-links "$BUNDLE/cli" --find-links "$BUNDLE/cli/linux-$arch" gpudeck-cli
  mkdir -p "$prefix/bin"
  ln -sf "$venv/bin/gpudeck" "$prefix/bin/gpudeck"
  log "installed $prefix/bin/gpudeck"
}

cmd_terraform_mirror() {
  local dir="/opt/gpu-deck/terraform"
  while [[ $# -gt 0 ]]; do
    case "$1" in --dir) dir="$2"; shift 2 ;; *) die "unknown argument $1" ;; esac
  done
  mkdir -p "$dir"
  cp -R "$BUNDLE/terraform/mirror/." "$dir/"
  cat <<RC
Add to ~/.terraformrc (or TF_CLI_CONFIG_FILE):

provider_installation {
  filesystem_mirror {
    path    = "$dir"
    include = ["registry.terraform.io/sur950/gpudeck"]
  }
  direct {
    exclude = ["registry.terraform.io/sur950/gpudeck"]
  }
}
RC
}

case "${1:-}" in
  verify) shift; cmd_verify "$@" ;;
  load-images) shift; cmd_load_images "$@" ;;
  compose) shift; cmd_compose "$@" ;;
  helm) shift; cmd_helm "$@" ;;
  cli) shift; cmd_cli "$@" ;;
  terraform-mirror) shift; cmd_terraform_mirror "$@" ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
