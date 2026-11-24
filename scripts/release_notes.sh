#!/usr/bin/env bash
# Prints the CHANGELOG.md section for one version, followed by an install block, for the body
# of the GitHub release.
#
# Usage: scripts/release_notes.sh VERSION            (v prefix optional)
#        scripts/release_notes.sh --section VERSION  (the CHANGELOG section only)
# Exits 1 when CHANGELOG.md has no "## [VERSION]" section, so a release never ships without notes.
# CHANGELOG_FILE=<path> reads another file. CLI_ON_PYPI=1 points the CLI line at PyPI.
# GITHUB_REPOSITORY (owner/name) overrides the repository used in links (default sur950/gpu-deck).
set -euo pipefail

cd "$(dirname "$0")/.."

SECTION_ONLY=0
if [[ "${1:-}" == "--section" ]]; then
  SECTION_ONLY=1
  shift
fi
VERSION="${1:?usage: scripts/release_notes.sh [--section] VERSION}"
VERSION="${VERSION#v}"
CHANGELOG_FILE="${CHANGELOG_FILE:-CHANGELOG.md}"
REPO="${GITHUB_REPOSITORY:-sur950/gpu-deck}"
OWNER="${REPO%%/*}"
OWNER="$(printf '%s' "$OWNER" | tr '[:upper:]' '[:lower:]')"

NOTES="$(awk -v version="$VERSION" '
  index($0, "## [" version "]") == 1 { found = 1; next }
  found && /^## \[/ { exit }
  found && /^\[[^]]+\]: / { next }
  found { print }
' "$CHANGELOG_FILE")"

# Trim blank lines at the start and the end.
NOTES="$(printf '%s\n' "$NOTES" | sed -e '/./,$!d' | sed -e ':a' -e '/^\n*$/{$d;N;ba' -e '}')"

if [[ -z "$NOTES" ]]; then
  echo "error: $CHANGELOG_FILE has no section for $VERSION. Add \"## [$VERSION] - <date>\" first." >&2
  exit 1
fi

if [[ "$SECTION_ONLY" == "1" ]]; then
  printf '%s\n' "$NOTES"
  exit 0
fi

# CLI_ON_PYPI=1 when the release workflow also publishes gpudeck-cli to PyPI.
if [[ "${CLI_ON_PYPI:-0}" == "1" ]]; then
  CLI_LINE="\`pipx install gpudeck-cli==$VERSION\` (or the wheel below)"
else
  CLI_LINE="\`pipx install ./gpudeck_cli-$VERSION-py3-none-any.whl\` with the wheel below"
fi

cat <<EOF
$NOTES

## Install

**Control plane (Docker images, signed with cosign):**

\`\`\`bash
docker pull ghcr.io/$OWNER/gpu-deck-api:$VERSION
docker pull ghcr.io/$OWNER/gpu-deck-web:$VERSION
docker pull ghcr.io/$OWNER/gpu-deck-agent:$VERSION
\`\`\`

**Kubernetes (Helm chart on GHCR):**

\`\`\`bash
helm install gpu-deck oci://ghcr.io/$OWNER/charts/gpu-deck --version $VERSION
\`\`\`

**Node agent:** \`gpudeck-agent-$VERSION-<target>.tar.gz\` below (Linux x86_64/aarch64 glibc and static musl, macOS Apple Silicon and Intel). The web UI's *Add node* dialog prints the install command.

**Desktop app:** \`.dmg\` (macOS universal), \`.msi\` / \`-setup.exe\` (Windows), \`.AppImage\` / \`.deb\` (Linux) below.

**CLI:** $CLI_LINE. **Terraform:** the \`terraform-provider-gpudeck_${VERSION}_*\` archives below.

Checksums for every file are in \`SHA256SUMS.txt\`. Verify an image with:

\`\`\`bash
cosign verify ghcr.io/$OWNER/gpu-deck-api:$VERSION \\
  --certificate-identity-regexp "^https://github.com/$REPO/.github/workflows/release.yml@refs/tags/v" \\
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
\`\`\`
EOF
