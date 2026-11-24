#!/usr/bin/env bash
# Regenerate internal/client/gen.go from libs/contracts/openapi/admin.yaml and refresh the
# embedded rule schemas. Invoked by `go generate ./...` (see internal/client/doc.go).
#
# Steps: downgrade the 3.1 contract to 3.0.3 (infra/scripts/openapi30.py, needs uv) and run the
# pinned oapi-codegen; copy libs/contracts/rules/*.schema.json into internal/rulesschema/schemas.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOD="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$MOD/../.." && pwd)"
SPEC="$ROOT/libs/contracts/openapi/admin.yaml"
OAPI_CODEGEN_VERSION="v2.5.0"
TMP_BASE="$(mktemp -t gpudeck-admin-3.0)"
TMP="$TMP_BASE.yaml"
trap 'rm -f "$TMP_BASE" "$TMP"' EXIT

uv run --no-project --python 3.12 --with "pyyaml==6.0.3" python -I "$ROOT/infra/scripts/openapi30.py" "$SPEC" "$TMP"
go run "github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@${OAPI_CODEGEN_VERSION}" \
  -generate types,client -package client -o "$MOD/internal/client/gen.go" "$TMP"

mkdir -p "$MOD/internal/rulesschema/schemas"
cp "$ROOT"/libs/contracts/rules/*.schema.json "$MOD/internal/rulesschema/schemas/"
echo "generated internal/client/gen.go and $(ls "$MOD/internal/rulesschema/schemas" | wc -l | tr -d ' ') rule schemas"
