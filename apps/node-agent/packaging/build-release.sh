#!/usr/bin/env bash
# Build release binaries of gpudeck-agent and lay them out for the control plane's download
# endpoint (`GET /agent/v1/downloads/{version}/{target}`, served from this directory).
#
#   apps/node-agent/packaging/build-release.sh [--out DIR] [--version X.Y.Z] [target ...]
#
# Default target: the host triple (aarch64-apple-darwin on an Apple Silicon Mac).
# Output (DIR defaults to apps/node-agent/dist/agent):
#   DIR/<version>/<target>/gpudeck-agent                          the binary (0755)
#   DIR/<version>/<target>/gpudeck-agent.sha256                   "<sha256>  gpudeck-agent"
#   DIR/<version>/<target>/gpudeck-agent-<version>-<target>.tar.gz  binary + packaging/ (systemd, launchd, k8s)
#   DIR/<version>/<target>/gpudeck-agent-<version>-<target>.tar.gz.sha256
#   DIR/<version>/<target>/manifest.json                          {version, target, files[{name,size_bytes,sha256}], built_at}
#   DIR/<version>/SHA256SUMS                                      every file of every target of the version
#   DIR/latest                                                    the version string
#
# Cross targets are built only when their std is installed (`rustup target add <t>`) and a
# linker exists; otherwise they are skipped with a message (this script never installs
# toolchains). Linux targets from a Mac: use infra/scripts/cross-compile-agent.sh (cargo-zigbuild)
# or build natively on Linux:
#   x86_64-unknown-linux-gnu, aarch64-unknown-linux-gnu   (glibc >= 2.28; NVML/ROCm via dlopen)
#   x86_64-unknown-linux-musl, aarch64-unknown-linux-musl (static, air-gapped hosts)
#   x86_64-apple-darwin                                   (Intel Macs; build on macOS)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$HERE/.." && pwd)"
OUT="$AGENT_DIR/dist/agent"
VERSION=""
TARGETS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) TARGETS+=("$1"); shift ;;
  esac
done

export PATH="$HOME/.cargo/bin:$PATH"
command -v cargo >/dev/null || { echo "cargo not found" >&2; exit 1; }
if [[ -z "$VERSION" ]]; then
  VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$AGENT_DIR/Cargo.toml" | head -1)"
fi
HOST="$(rustc -vV | sed -n 's/^host: //p')"
if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=("$HOST")
fi

sha256() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
filesize() { wc -c < "$1" | tr -d ' '; }

installed_targets="$(rustup target list --installed 2>/dev/null || echo "$HOST")"
built=0
for t in "${TARGETS[@]}"; do
  if ! grep -qx "$t" <<<"$installed_targets"; then
    echo "skip $t: std not installed (rustup target add $t) - see header for cross builds" >&2
    continue
  fi
  echo "==> building gpudeck-agent $VERSION for $t"
  if ! (cd "$AGENT_DIR" && cargo build --release --locked -p gpudeck-agent --bin gpudeck-agent --target "$t"); then
    echo "skip $t: build failed (missing cross linker?)" >&2
    continue
  fi
  dest="$OUT/$VERSION/$t"
  rm -rf "$dest"
  mkdir -p "$dest"
  install -m 0755 "${CARGO_TARGET_DIR:-$AGENT_DIR/target}/$t/release/gpudeck-agent" "$dest/gpudeck-agent"
  (cd "$dest" && echo "$(sha256 gpudeck-agent)  gpudeck-agent" > gpudeck-agent.sha256)
  stage="$(mktemp -d)"
  cp "$dest/gpudeck-agent" "$stage/gpudeck-agent"
  mkdir -p "$stage/packaging"
  cp -R "$AGENT_DIR/packaging/systemd" "$AGENT_DIR/packaging/launchd" "$AGENT_DIR/packaging/kubernetes" "$stage/packaging/"
  tarball="gpudeck-agent-$VERSION-$t.tar.gz"
  tar -C "$stage" -czf "$dest/$tarball" .
  rm -rf "$stage"
  (cd "$dest" && echo "$(sha256 "$tarball")  $tarball" > "$tarball.sha256")
  cat > "$dest/manifest.json" <<JSON
{
  "version": "$VERSION",
  "target": "$t",
  "protocol_version": 1,
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "files": [
    {"name": "gpudeck-agent", "size_bytes": $(filesize "$dest/gpudeck-agent"), "sha256": "$(sha256 "$dest/gpudeck-agent")"},
    {"name": "$tarball", "size_bytes": $(filesize "$dest/$tarball"), "sha256": "$(sha256 "$dest/$tarball")"}
  ]
}
JSON
  built=$((built + 1))
done

if [[ $built -eq 0 ]]; then
  echo "nothing built" >&2
  exit 1
fi
(cd "$OUT/$VERSION" && for f in */gpudeck-agent */*.tar.gz; do [[ -f "$f" ]] && echo "$(sha256 "$f")  $f"; done > SHA256SUMS)
echo "$VERSION" > "$OUT/latest"
echo "artifacts in $OUT/$VERSION:"
cat "$OUT/$VERSION/SHA256SUMS"
