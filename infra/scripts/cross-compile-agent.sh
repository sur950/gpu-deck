#!/usr/bin/env bash
# Cross-compile the gpudeck-agent binary for every supported platform with cargo-zigbuild.
#
#   infra/scripts/cross-compile-agent.sh [--targets "t1 t2"] [--out dist/agent] [--version 1.2.3]
#
# Prerequisites (NOT installed by this script; install once on the build host / CI image):
#   cargo install --locked cargo-zigbuild@0.24.1     # or: pip install cargo-zigbuild==0.24.1
#   zig 0.17.1 on PATH                                # pip install ziglang==0.17.1 also works
#   rustup target add <each target below>
#   macOS targets: build on macOS (Apple SDK frameworks: IOKit/Metal are linked), see below.
#
# Targets (default set):
#   x86_64-unknown-linux-gnu.2.28   Linux x86_64, glibc >= 2.28 (RHEL 8+, Ubuntu 20.04+); NVML/ROCm hosts
#   aarch64-unknown-linux-gnu.2.28  Linux arm64 (Grace Hopper, Ampere), glibc >= 2.28
#   x86_64-unknown-linux-musl       static Linux x86_64 (air-gapped minimal hosts; NVML via dlopen)
#   aarch64-unknown-linux-musl      static Linux arm64
#   aarch64-apple-darwin            Apple Silicon (plain cargo on a Mac; zigbuild cannot link Metal)
#
# Output: <out>/gpudeck-agent-<version>-<target>.tar.gz + SHA256SUMS. The offline bundle and the
# CI release job consume this layout.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
AGENT_DIR="$ROOT/apps/node-agent"
OUT="$ROOT/dist/agent"
VERSION=""
TARGETS="x86_64-unknown-linux-gnu.2.28 aarch64-unknown-linux-gnu.2.28 x86_64-unknown-linux-musl aarch64-unknown-linux-musl"
if [[ "$(uname -s)" == "Darwin" ]]; then
  TARGETS="$TARGETS aarch64-apple-darwin"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --targets) TARGETS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$AGENT_DIR/Cargo.toml" | head -1)"
fi

need() { command -v "$1" >/dev/null || { echo "missing prerequisite: $1 (see header of $0)" >&2; exit 1; }; }
need cargo
mkdir -p "$OUT"

for target in $TARGETS; do
  triple="${target%.2.*}"          # strip the glibc suffix for paths
  echo "==> $target"
  if [[ "$triple" == *apple-darwin ]]; then
    [[ "$(uname -s)" == "Darwin" ]] || { echo "skip $target: build on macOS" >&2; continue; }
    (cd "$AGENT_DIR" && cargo build --release --locked -p gpudeck-agent --bin gpudeck-agent --target "$triple")
  else
    need cargo-zigbuild
    need zig
    (cd "$AGENT_DIR" && cargo zigbuild --release --locked -p gpudeck-agent --bin gpudeck-agent --target "$target")
  fi
  bin="${CARGO_TARGET_DIR:-$AGENT_DIR/target}/$triple/release/gpudeck-agent"
  stage="$(mktemp -d)"
  cp "$bin" "$stage/gpudeck-agent"
  if [[ -d "$AGENT_DIR/packaging" ]]; then
    cp -R "$AGENT_DIR/packaging" "$stage/packaging"
  fi
  tar -C "$stage" -czf "$OUT/gpudeck-agent-$VERSION-$triple.tar.gz" .
  rm -rf "$stage"
done

(cd "$OUT" && shasum -a 256 gpudeck-agent-"$VERSION"-*.tar.gz > SHA256SUMS)
echo "artifacts in $OUT:"
cat "$OUT/SHA256SUMS"
