#!/usr/bin/env bash
# Preview the website exactly as GitHub Pages serves it: under /gpu-deck/, with
# folder URLs (docs/architecture/) resolving to their index.html.
#
#   scripts/serve_site.sh            # build docs, serve on http://localhost:8000/gpu-deck/
#   PORT=9000 scripts/serve_site.sh  # another port
#   NO_BUILD=1 scripts/serve_site.sh # skip the docs build
#
# Opening website/index.html straight from disk (file://) does not work: folder links
# need a web server, and 404.html uses the /gpu-deck/ base path.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
VENV="${DOCS_VENV:-${TMPDIR:-/tmp}/gpu-deck-docs-venv}"

if [[ -z "${NO_BUILD:-}" ]]; then
  if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r "$ROOT/scripts/requirements-docs.txt"
  fi
  [[ -x "$ROOT/website/sync-screenshots.sh" ]] && "$ROOT/website/sync-screenshots.sh" >/dev/null
  "$VENV/bin/python" "$ROOT/scripts/build_docs.py"
fi

SERVE_DIR="$(mktemp -d)"
trap 'rm -rf "$SERVE_DIR"' EXIT
ln -s "$ROOT/website" "$SERVE_DIR/gpu-deck"

echo "GPU Deck site: http://localhost:$PORT/gpu-deck/   (Ctrl-C to stop)"
cd "$SERVE_DIR"
python3 -m http.server "$PORT" --bind 127.0.0.1
