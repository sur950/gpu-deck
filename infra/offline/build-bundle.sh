#!/usr/bin/env bash
# Build the offline (air-gapped) GPU Deck bundle on a connected machine.
#
#   infra/offline/build-bundle.sh --version 1.2.3 [--registry REG] [--out dist/offline]
#                                 [--with-data-services] [--with-observability] [--skip-agent]
#
# Contents of gpu-deck-offline-<version>.tar.gz:
#   images/*.tar.gz          docker save of gpu-deck-api, -web, -agent (+ postgres/redis, prometheus/
#                            grafana/otel with the flags); images.txt lists ref + digest
#   helm/gpu-deck-*.tgz      Helm chart
#   compose/                 infra/docker compose files, scripts, observability configs
#   agent/                   gpudeck-agent-<version>-<target>.tar.gz per platform (+ SHA256SUMS)
#   cli/                     gpudeck CLI wheel + dependency wheels (pip --no-index install)
#   terraform/mirror/        provider filesystem mirror (linux/darwin, amd64/arm64)
#   install.sh, install-agent.sh, cosign.pub (when COSIGN_PUBLIC_KEY is set), SHA256SUMS
#
# Images are pulled from REG (default: the CI registry) and, when COSIGN_PUBLIC_KEY points to the
# release public key, verified with cosign before they are saved. Agent binaries come from
# dist/agent (infra/scripts/cross-compile-agent.sh) unless --skip-agent.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
VERSION=""
REGISTRY="${GPUDECK_REGISTRY:-ghcr.io/sur950}"
OUT="$ROOT/dist/offline"
WITH_DATA=0
WITH_OBS=0
SKIP_AGENT=0

# Third-party images, pinned (same tags as infra/docker/docker-compose.full.yml).
DATA_IMAGES="postgres:17.12-alpine redis:7.4.12-alpine"
OBS_IMAGES="prom/prometheus:v3.16.0 grafana/grafana:13.3.1 otel/opentelemetry-collector-contrib:0.165.0"
PY_VERSION="3.12"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --registry) REGISTRY="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --with-data-services) WITH_DATA=1; shift ;;
    --with-observability) WITH_OBS=1; shift ;;
    --skip-agent) SKIP_AGENT=1; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$VERSION" ]] || { echo "--version is required" >&2; exit 2; }

need() { command -v "$1" >/dev/null || { echo "missing tool: $1" >&2; exit 1; }; }
need docker; need helm; need go; need uv; need tar; need shasum

STAGE="$OUT/gpu-deck-offline-$VERSION"
rm -rf "$STAGE"
mkdir -p "$STAGE"/{images,helm,compose,agent,cli,terraform}
log() { printf '==> %s\n' "$*"; }

# ----- images ---------------------------------------------------------------------------------
save_image() {
  local ref="$1" name="$2"
  log "image $ref"
  docker pull --quiet "$ref" >/dev/null
  if [[ -n "${COSIGN_PUBLIC_KEY:-}" && "$ref" == "$REGISTRY"/* ]]; then
    need cosign
    cosign verify --key "$COSIGN_PUBLIC_KEY" "$ref" >/dev/null
  fi
  local digest
  digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$ref" 2>/dev/null || echo "$ref")"
  docker save "$ref" | gzip -1 > "$STAGE/images/$name.tar.gz"
  printf '%s\t%s\t%s\n' "$name" "$ref" "$digest" >> "$STAGE/images/images.txt"
}
for component in gpu-deck-api gpu-deck-web gpu-deck-agent; do
  save_image "$REGISTRY/$component:$VERSION" "$component"
done
if [[ $WITH_DATA == 1 ]]; then
  for ref in $DATA_IMAGES; do save_image "$ref" "$(echo "${ref%%:*}" | tr '/' '-')"; done
fi
if [[ $WITH_OBS == 1 ]]; then
  for ref in $OBS_IMAGES; do save_image "$ref" "$(echo "${ref%%:*}" | tr '/' '-')"; done
fi

# ----- helm + compose ---------------------------------------------------------------------------
log "helm chart"
helm package "$ROOT/infra/helm/gpu-deck" --version "$VERSION" --app-version "$VERSION" -d "$STAGE/helm" >/dev/null

log "compose files"
mkdir -p "$STAGE/compose/docker" "$STAGE/compose/observability"
cp "$ROOT"/infra/docker/docker-compose*.yml "$STAGE/compose/docker/"
cp -R "$ROOT/infra/docker/scripts" "$STAGE/compose/docker/scripts"
cp -R "$ROOT/infra/observability/prometheus" "$ROOT/infra/observability/otel" "$STAGE/compose/observability/"
mkdir -p "$STAGE/compose/observability/grafana"
cp -R "$ROOT/infra/observability/grafana/provisioning" "$ROOT/infra/observability/grafana/dashboards" \
  "$STAGE/compose/observability/grafana/"
cp "$ROOT/.env.example" "$STAGE/compose/env.example"

# ----- agent ----------------------------------------------------------------------------------
if [[ $SKIP_AGENT == 0 ]]; then
  log "agent binaries"
  if ! ls "$ROOT"/dist/agent/gpudeck-agent-*.tar.gz >/dev/null 2>&1; then
    "$ROOT/infra/scripts/cross-compile-agent.sh" --version "$VERSION"
  fi
  cp "$ROOT"/dist/agent/gpudeck-agent-*.tar.gz "$ROOT/dist/agent/SHA256SUMS" "$STAGE/agent/"
fi

# ----- CLI ------------------------------------------------------------------------------------
log "cli wheels"
(cd "$ROOT/apps/cli" && uv build --wheel --out-dir "$STAGE/cli" >/dev/null)
for arch in x86_64 aarch64; do
  # Dependency wheels for Linux admin hosts (pure-Python wheels are shared, binary ones per arch).
  uv run --no-project --python "$PY_VERSION" --with pip==25.3 -- \
    pip download --quiet --only-binary=:all: --python-version "$PY_VERSION" \
    --platform "manylinux_2_28_$arch" --platform "manylinux_2_17_$arch" \
    --platform "manylinux2014_$arch" --platform any \
    --dest "$STAGE/cli/linux-$arch" "$STAGE"/cli/gpudeck_cli-*.whl
done

# ----- terraform provider ---------------------------------------------------------------------
log "terraform provider mirror"
MIRROR="$STAGE/terraform/mirror/registry.terraform.io/sur950/gpudeck/$VERSION"
for target in linux_amd64 linux_arm64 darwin_arm64 darwin_amd64; do
  os="${target%_*}"; arch="${target#*_}"
  mkdir -p "$MIRROR/$target"
  (cd "$ROOT/apps/terraform-provider" && CGO_ENABLED=0 GOOS="$os" GOARCH="$arch" GOTOOLCHAIN=local \
    go build -trimpath -ldflags "-s -w -X main.version=$VERSION" \
    -o "$MIRROR/$target/terraform-provider-gpudeck_v$VERSION" .)
done

# ----- installers, keys, checksums -------------------------------------------------------------
cp "$HERE/install.sh" "$HERE/install-agent.sh" "$STAGE/"
chmod +x "$STAGE/install.sh" "$STAGE/install-agent.sh"
[[ -n "${COSIGN_PUBLIC_KEY:-}" ]] && cp "$COSIGN_PUBLIC_KEY" "$STAGE/cosign.pub"
printf '%s\n' "$VERSION" > "$STAGE/VERSION"
(cd "$STAGE" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 shasum -a 256 > SHA256SUMS)

log "archive"
tar -C "$OUT" -czf "$OUT/gpu-deck-offline-$VERSION.tar.gz" "gpu-deck-offline-$VERSION"
(cd "$OUT" && shasum -a 256 "gpu-deck-offline-$VERSION.tar.gz" > "gpu-deck-offline-$VERSION.tar.gz.sha256")
echo "bundle: $OUT/gpu-deck-offline-$VERSION.tar.gz"
