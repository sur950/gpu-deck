#!/usr/bin/env bash
# Checks that the working tree is ready for a release and prints the commands that start it.
# It never tags, commits or pushes: pushing the tag is what runs .github/workflows/release.yml.
#
# Usage: scripts/release.sh [VERSION]       (defaults to the VERSION file)
#        scripts/release.sh --bump X.Y.Z    (writes X.Y.Z into every manifest, then checks)
#
# Checks:
#   - VERSION is a semantic version, the tag does not exist yet (locally or on origin)
#   - every manifest carries that version (scripts/version.py --check)
#   - CHANGELOG.md has a "## [VERSION] - <date>" section (scripts/release_notes.sh)
#   - the working tree is clean and on main, and main is pushed (warnings before the first commit)
set -euo pipefail

cd "$(dirname "$0")/.."

err() { printf 'error: %s\n' "$1" >&2; FAILED=1; }
warn() { printf 'warning: %s\n' "$1" >&2; }
FAILED=0

if [[ "${1:-}" == "--bump" ]]; then
  python3 scripts/version.py --set "${2:?usage: scripts/release.sh --bump X.Y.Z}"
  shift 2
  echo "Now add a \"## [$(cat VERSION)] - $(date +%F)\" section to CHANGELOG.md, commit, and run scripts/release.sh again."
  exit 0
fi

VERSION="${1:-$(tr -d '[:space:]' < VERSION)}"
VERSION="${VERSION#v}"
TAG="v$VERSION"

echo "==> Release $TAG"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
  err "$VERSION is not a version like 1.2.3 or 1.2.3-rc.1"
fi
[[ "$VERSION" == "$(tr -d '[:space:]' < VERSION)" ]] || err "VERSION file says $(cat VERSION), not $VERSION (scripts/release.sh --bump $VERSION)"

echo "==> Versions"
python3 scripts/version.py --check -q || FAILED=1

echo "==> CHANGELOG.md"
if scripts/release_notes.sh --section "$VERSION" > /dev/null; then
  echo "section [$VERSION] found"
  grep -qE "^\[$VERSION\]: " CHANGELOG.md || warn "CHANGELOG.md has no [$VERSION]: link at the bottom"
else
  FAILED=1
fi

echo "==> Git"
if git rev-parse --verify HEAD >/dev/null 2>&1; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  [[ "$BRANCH" == "main" ]] || warn "on branch $BRANCH, releases are tagged on main"
  if [[ -n "$(git status --porcelain)" ]]; then
    err "the working tree has uncommitted or untracked files (git status)"
  fi
  if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    err "tag $TAG already exists locally"
  fi
  if git remote get-url origin >/dev/null 2>&1; then
    if git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
      err "tag $TAG already exists on origin"
    fi
    git fetch -q origin main 2>/dev/null || warn "could not fetch origin/main"
    if git rev-parse -q --verify origin/main >/dev/null && [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
      warn "HEAD is not origin/main: push main first so CI runs on the commit you tag"
    fi
  else
    warn "no 'origin' remote yet"
  fi
else
  err "no commits yet: commit everything and push main before tagging"
fi

if [[ "$FAILED" != "0" ]]; then
  echo
  echo "Not ready. Fix the errors above and run scripts/release.sh again." >&2
  exit 1
fi

cat <<EOF

Ready. Run scripts/ci_local.sh for a full local check, then (signed tag, needs a GPG or SSH
signing key in git config; use -a instead of -s for an unsigned annotated tag):

  git tag -s $TAG -m "GPU Deck $VERSION"
  git push origin $TAG

The tag starts .github/workflows/release.yml. Follow it with:

  gh run watch \$(gh run list --workflow release.yml --limit 1 --json databaseId -q '.[0].databaseId')

EOF
