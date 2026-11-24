# Releasing GPU Deck

Every component ships under **one version**: the control plane images, the web UI, the node
agent, the desktop app, the CLI, the Terraform provider, the Helm chart and the admin OpenAPI
contract. The `VERSION` file at the repository root holds it. A release is a signed git tag
`vX.Y.Z` that equals `VERSION`; pushing the tag runs `.github/workflows/release.yml`, which builds,
signs and publishes everything and creates the GitHub release.

## Versioning policy

GPU Deck follows [Semantic Versioning 2.0.0](https://semver.org/). Pre-releases use a suffix
(`1.2.0-rc.1`) and are published as GitHub pre-releases; image tags `latest`, `X` and `X.Y` move
only for final releases.

| Change | Bump |
| --- | --- |
| Removing or renaming an admin API (`/api/v1`) endpoint, field, enum value or error code; changing a field's type or meaning | **major** |
| Removing behaviour from the serving API (OpenAI, Anthropic, Ollama routes) | **major** |
| A new agent protocol major (`envelope.v` / `hello.protocol_version`), or any protocol change older agents cannot ignore | **major** |
| A database migration that is not forward-compatible on its own: it needs a manual step, drops or rewrites data the previous release still reads, or cannot run while the old version is still serving | **major** |
| Removing or renaming a setting (environment variable), a Helm value, a CLI command or flag, or a Terraform resource attribute | **major** |
| New endpoints, optional fields, settings with defaults, protocol commands gated by capabilities, additive migrations applied by `alembic upgrade head` | minor |
| Bug and security fixes with no interface change | patch |

Migrations always run forward (`alembic upgrade head`, the Helm pre-upgrade hook). Downgrading
across a migration is not supported: restore the database backup taken before the upgrade.
Deprecations are announced in `CHANGELOG.md` at least one minor release before the removal.

## Agent and control plane compatibility

The node agent and the control plane are released together but upgraded separately, so the
agent protocol (`libs/contracts/agent-protocol/`) is versioned on its own:

- **Protocol major.** `envelope.v` and `hello.protocol_version` carry the protocol major
  (currently `1`, `AGENT_PROTOCOL_VERSION`). The hub refuses an agent with another major
  (`protocol_unsupported`). A new major is a GPU Deck major release, and the hub of that release
  accepts the previous protocol major for at least one minor release so fleets can be upgraded
  node by node.
- **Within a major everything is additive.** Older agents ignore new fields; the hub never sends
  a command an agent did not announce. Agents list what they implement in
  `hello.capabilities.commands`; the hub checks it, and `COMMAND_MIN_AGENT_VERSIONS`
  (`command=semver,...`) can set a minimum agent version per command on top. A refused command
  ends as `rejected` with `unsupported_by_agent`.
- **Upgrade order:** control plane first (migrations, API, hub), then the agents. A control plane
  release works with every agent of the same protocol major; a newer agent against an older hub
  is not supported.
- Protocol changes are listed in `libs/contracts/agent-protocol/CHANGELOG.md` and summarised in
  the release's `CHANGELOG.md` section.

## One version everywhere

`scripts/version.py` reads and writes the version in every manifest:

```bash
python3 scripts/version.py              # list every file and the version it carries
python3 scripts/version.py --check      # CI: fail when any file differs from VERSION
python3 scripts/version.py --set 1.1.0  # write 1.1.0 into VERSION and every manifest
```

It covers the root, web and desktop `package.json`, `tauri.conf.json`, the desktop and node
agent `Cargo.toml` and `Cargo.lock` entries, `apps/cli/pyproject.toml` and `gpudeck/__init__.py`,
the Terraform provider's `main.go` default, `infra/helm/gpu-deck/Chart.yaml` (`version` and
`appVersion`), `info.version` in `libs/contracts/openapi/admin.yaml`, and `APP_VERSION` (settings
default, `libs/contracts/env.md`, both `.env.example` files). When `info.version` changes, run
`pnpm --filter web gen:api` so the generated client stays in step (CI checks it).

## Release checklist

1. Make sure `main` is green (the required check is **CI passed**).
2. Bump the version on a branch and merge it:
   ```bash
   scripts/release.sh --bump 1.1.0          # = python3 scripts/version.py --set 1.1.0
   ```
   Move the `## [Unreleased]` entries in `CHANGELOG.md` under `## [1.1.0] - YYYY-MM-DD`, add the
   `[1.1.0]: https://github.com/sur950/gpu-deck/releases/tag/v1.1.0` link and update the
   `[Unreleased]` compare link. Mention migrations, protocol changes and anything operators must
   do in the section; it becomes the release notes.
3. Sync the website screenshots. When the UI changed, re-shoot `docs/screenshots/`
   (`node docs/screenshots/capture.mjs` against a running stack), commit the SVGs, then check the
   site picks them up (GitHub Pages runs the same script, `website/assets/screenshots/` is
   generated and git-ignored):
   ```bash
   website/sync-screenshots.sh           # copy the display SVGs into website/assets/screenshots/
   website/sync-screenshots.sh --check   # exit 1 when the site is behind docs/screenshots/
   python scripts/build_docs.py --check  # docs pages, links, anchors, images, NAV
   ```
4. Check locally, then tag `main`:
   ```bash
   scripts/ci_local.sh            # the CI checks on a clean copy (add `images` for Docker builds)
   scripts/release.sh             # clean tree, versions, CHANGELOG section, tag not taken
   git tag -s v1.1.0 -m "GPU Deck 1.1.0"
   git push origin v1.1.0
   ```
5. Watch the run (`gh run watch`). When it finishes, check the release page: notes, every file,
   `SHA256SUMS.txt`, `latest.json` (when the updater is configured), and pull an image:
   ```bash
   cosign verify ghcr.io/sur950/gpu-deck-api:1.1.0 \
     --certificate-identity-regexp '^https://github.com/sur950/gpu-deck/.github/workflows/release.yml@refs/tags/v' \
     --certificate-oidc-issuer https://token.actions.githubusercontent.com
   gh attestation verify oci://ghcr.io/sur950/gpu-deck-api:1.1.0 --repo sur950/gpu-deck
   ```

A failed job can be re-run from the Actions page; the publish step uploads with `--clobber`, so
re-runs replace files. If the tagged commit itself is wrong and nothing was announced yet, delete
the release and the tag (`gh release delete v1.1.0 --cleanup-tag`), fix `main`, and tag again.
Published image tags and chart versions are never overwritten after an announcement: ship a patch
release instead.

## What the release workflow publishes

| Artifact | Where | Signed with |
| --- | --- | --- |
| `gpu-deck-api`, `gpu-deck-web`, `gpu-deck-agent` images (linux/amd64 + arm64), tags `X.Y.Z`, `X.Y`, `X`, `latest` | `ghcr.io/sur950/` | cosign keyless (Sigstore, GitHub OIDC), GitHub attestation, BuildKit provenance + SBOM, SPDX SBOM attested and attached to the release |
| Helm chart `gpu-deck` | `oci://ghcr.io/sur950/charts/gpu-deck` + `.tgz` on the release | cosign keyless |
| Node agent `gpudeck-agent-X.Y.Z-<target>.tar.gz` for `x86_64`/`aarch64-unknown-linux-gnu` (glibc 2.28+), `x86_64`/`aarch64-unknown-linux-musl` (static), `aarch64`/`x86_64-apple-darwin` | release | `SHA256SUMS.txt` + GitHub attestation |
| Desktop: `GPU-Deck_X.Y.Z_universal.dmg`, `_x64_en-US.msi`, `_x64-setup.exe`, `_amd64.AppImage`, `_amd64.deb`, updater archives + `.sig`, `latest.json` | release | Apple Developer ID + notarization, Authenticode, Tauri updater key (each when its secrets exist) |
| CLI `gpudeck_cli-X.Y.Z-py3-none-any.whl` + sdist | release (+ PyPI when enabled) | PyPI trusted publishing attestations |
| Terraform provider `terraform-provider-gpudeck_X.Y.Z_<os>_<arch>.zip`, `_SHA256SUMS`, `_SHA256SUMS.sig`, `_manifest.json` | release | GPG (when the key exists) |

The control plane serves agent downloads to joining nodes (through the agent hub): it fetches
`gpudeck-agent-X.Y.Z-<target>.tar.gz` of its own version from this release on first use, checks
it against `SHA256SUMS.txt` and caches it, so the asset names and `SHA256SUMS.txt` are part of the
contract. `AGENT_DIST_DIR` (local builds, e.g. `apps/node-agent/packaging/build-release.sh`)
overrides it. The offline bundle (`infra/offline/build-bundle.sh`) takes the same archives from
`dist/agent/`.

## Repository settings and secrets

Settings (once, by the repository owner):

- **Pages:** Settings, Pages, Source: **GitHub Actions** (`.github/workflows/pages.yml` deploys
  `website/` to `https://sur950.github.io/gpu-deck/`).
- **Actions:** Settings, Actions, General, Workflow permissions: *Read repository contents* (the
  workflows ask for more per job). Allow GitHub Actions to create pull requests: off.
- **Packages:** after the first release, open each package (`gpu-deck-api`, `gpu-deck-web`,
  `gpu-deck-agent`, `charts/gpu-deck`) under the profile's Packages, set visibility **Public** and
  check that it is linked to `sur950/gpu-deck` with *Actions access: write*. The workflow pushes
  with `GITHUB_TOKEN`; no personal token is needed.
- **Branch protection on `main`:** require pull requests and the status check **CI passed**;
  require signed commits if you sign them. Protect tags `v*` (Settings, Rules, Tag rulesets) so
  only maintainers can create them.
- **Code security:** enable Dependabot alerts and security updates, secret scanning with push
  protection, and private vulnerability reporting ([SECURITY.md](SECURITY.md) points to it). CodeQL runs from
  `.github/workflows/codeql.yml` (use the *Advanced* setup, not *Default*).

Secrets and variables (Settings, Secrets and variables, Actions). Every one is optional; the
release still succeeds without it and the job prints a warning saying what was skipped.

| Name | Kind | Used for |
| --- | --- | --- |
| `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | secret | signing desktop update archives (`pnpm --filter desktop tauri signer generate -w ~/.tauri/gpudeck-updater.key`) |
| `GPUDECK_UPDATER_PUBKEY` | variable | the matching public key (`.key.pub` contents); with the private key it turns the desktop updater on |
| `GPUDECK_UPDATER_ENDPOINTS` | variable | update manifest URLs; default `https://github.com/sur950/gpu-deck/releases/latest/download/latest.json` |
| `APPLE_CERTIFICATE` (base64 `.p12`), `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY` | secrets | Developer ID signing of the macOS app (otherwise signed ad hoc) |
| `APPLE_ID`, `APPLE_PASSWORD` (app-specific password), `APPLE_TEAM_ID` | secrets | notarization and stapling |
| `WINDOWS_CERTIFICATE` (base64 `.pfx`), `WINDOWS_CERTIFICATE_PASSWORD` | secrets | Authenticode signing of the msi and nsis installers |
| `GPG_PRIVATE_KEY` (ASCII-armored), `GPG_PASSPHRASE` | secrets | signing the Terraform provider `SHA256SUMS` (required by the Terraform Registry) |
| `PUBLISH_PYPI` = `true` | variable | publish `gpudeck-cli` to PyPI; first add the repository as a trusted publisher on pypi.org (workflow `release.yml`, environment `pypi`) and create the `pypi` environment |

Images, the chart and the GitHub release use the built-in `GITHUB_TOKEN`; cosign signs keyless,
so there is no signing key to keep.

### Terraform Registry

The registry only reads releases from a repository named `terraform-provider-<name>`. Until the
provider has its own repository, users install it from the release zips with a
[filesystem mirror](https://developer.hashicorp.com/terraform/cli/config/config-file#filesystem_mirror)
or `dev_overrides` ([components/terraform-provider.md](components/terraform-provider.md)). To publish to
`registry.terraform.io/sur950/gpudeck`: create `sur950/terraform-provider-gpudeck`, push the
contents of `apps/terraform-provider` there (`git subtree split --prefix apps/terraform-provider`),
add the GPG public key to the registry, and attach the GoReleaser files of each release to a
release with the same tag in that repository.

## Hotfixes

Fixes land on `main` first. To patch an older line while `main` has moved on:

```bash
git switch -c release/1.0 v1.0.0             # once per line; later: git switch release/1.0
git cherry-pick -x <fix commit>...
scripts/release.sh --bump 1.0.1              # then add "## [1.0.1] - YYYY-MM-DD" to CHANGELOG.md
git commit -am "Release 1.0.1"
scripts/release.sh                           # warns that you are not on main; that is expected
git tag -s v1.0.1 -m "GPU Deck 1.0.1"
git push origin release/1.0 v1.0.1
```

The workflow runs for any `v*.*.*` tag. GitHub marks the highest version as *Latest*, so a patch
of an older line does not replace the newest release on the download page, and the workflow
moves the image tags `latest` and `X` only for the highest final release (`X.Y` and `X.Y.Z` are
always pushed).
Add the `1.0.1` section to `CHANGELOG.md` on `main` as well.

## Local helpers

| Script | What it does |
| --- | --- |
| `scripts/version.py` | show, check or set the version in every manifest |
| `scripts/release.sh` | pre-tag checks; prints the `git tag` / `git push` commands (never runs them) |
| `scripts/release_notes.sh X.Y.Z` | the release body: the CHANGELOG section plus install instructions |
| `scripts/ci_local.sh [steps]` | the CI checks on a clean copy of what git would push |
| `scripts/ci_deps.sh up\|down` | throwaway Postgres + Redis (own compose project and ports) for the backend tests |
| `scripts/collect_desktop_bundles.sh`, `scripts/desktop_updater_manifest.py` | used by the release workflow to name desktop files and write `latest.json` |
