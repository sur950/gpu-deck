# gpudeck CLI

`gpudeck` is the command line for the GPU Deck admin API. Its commands are built at runtime from
the server's OpenAPI document, so every admin API operation is a command and the CLI never carries
a copy of the backend's models. Output is a table on a terminal and JSON when piped.

## Install

Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
cd apps/cli
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/gpudeck --help
```

For a user install: `uv tool install ./apps/cli`.

## Log in

```bash
gpudeck login -e http://localhost:8180 -u admin            # prompts for the password
echo "$PW" | gpudeck login -e https://deck.example.com -u admin --password-stdin
gpudeck auth whoami
gpudeck logout                                               # revokes the session server-side
```

`login` posts to `/api/v1/auth/login` and keeps the session token from the `gpudeck_session`
cookie. The token is stored per profile in the OS keychain (`keyring`, service `gpudeck-cli`);
when no keychain is usable (headless Linux, CI) or `--no-keyring` is given it goes to
`~/.config/gpudeck/credentials.json` with mode `0600`. The token is never printed.

Requests send `Authorization: Bearer <token>`. If the server rejects Bearer, the CLI retries once
with the session cookie plus the CSRF double-submit header (`X-CSRF-Token`, taken from the login
response or `GET /auth/csrf`) and remembers cookie mode for that profile.

## Profiles

`~/.config/gpudeck/config.toml` (or `$GPUDECK_CONFIG_DIR`, `$XDG_CONFIG_HOME/gpudeck`):

```bash
gpudeck config set prod -e https://deck.example.com --ca-bundle ~/deck-ca.pem
gpudeck config set lab -e http://10.0.0.5:8180 --no-verify-tls --no-use
gpudeck config list
gpudeck config use prod
gpudeck -P lab nodes list
```

| Environment | Effect |
| --- | --- |
| `GPUDECK_PROFILE` | profile to use |
| `GPUDECK_ENDPOINT` | endpoint for this run (not saved) |
| `GPUDECK_TOKEN` | session token; skips the stored one, never written to disk |
| `GPUDECK_OUTPUT` | `table`, `json`, `yaml` or `csv` |
| `GPUDECK_INSECURE` | `1` disables TLS verification |
| `GPUDECK_CA_BUNDLE` | PEM bundle for TLS verification |
| `GPUDECK_NO_KEYRING` | `1` stores sessions in the 0600 file |
| `GPUDECK_CONFIG_DIR` | config directory |
| `GPUDECK_SPEC` | `auto` (default), `bundled` (never fetch the spec) or a path to an OpenAPI file |
| `GPUDECK_SPEC_TTL` | seconds a downloaded spec is reused (default `86400`) |
| `GPUDECK_CACHE_DIR` | spec cache (default `~/Library/Caches/gpudeck`, `$XDG_CACHE_HOME/gpudeck` or `~/.cache/gpudeck`) |

## Commands

```
gpudeck [-P profile] [-e endpoint] [-o table|json|yaml|csv] [--fields a,b.c] [-k] [-v]
        [--refresh-spec] [--no-validate] GROUP ... COMMAND
```

Groups: `auth`, `api-keys`, `audit`, `ip-rules`, `vault`, `price-plans`, `clusters`, `nodes`
(`join-tokens`, `certificates`), `devices`, `topology`, `catalog`, `models` (`versions`), `engines`
(`versions`), `deployments`, `instances`, `cache`, `layouts`, `rules`, `rule-runs`, `swaps`,
`alerts` (`channels`, `events`), `metrics`, `logs`, `traces`, `benchmarks`, `benchmark-runs`,
`metering` (`usage`, `batches`), `billing` (`reports`, `estimate`), `pki`, `system`, `live`,
`agent` (downloads, install script), `gpu-instances` (SSH GPU instances), `cloud` (`providers`,
`nodes`), `config`.
`gpudeck GROUP --help` lists the commands; `gpudeck/registry.py` `COMMAND_MAP` maps each
operationId to its command.

How arguments map to the API:

- path parameters are positional arguments (`gpudeck clusters get <id>`),
- query parameters are options with the schema's types, enum choices, defaults and required flags
  (`--status ready --page-size 100`); list parameters repeat (`--metrics a --metrics b`); `--all`
  follows pagination on list commands,
- request bodies: one option per simple top-level field (`--name lab --type bare_metal`), plus
  `--data/-d` (inline JSON, `@file.json` or `@file.yaml`, `-` = stdin), `--file/-f body.yaml`
  and repeatable `--set key.path=value` (values parsed as JSON when they parse, else strings).
  Order of precedence: `--data`/`--file` < options < `--set`. The body is checked against the
  operation's JSON schema before it is sent (`--no-validate` skips the check),
- help text (`--help`) comes from the operation's summary and description.

Examples:

```bash
gpudeck clusters create --name lab --type bare_metal --set labels.zone=office
gpudeck nodes join-tokens create --cluster-id "$CID" --ttl-minutes 15 -o json | jq -r .join_command
# one multi-use enrolment token for a Kubernetes GPU pool (30 days, up to 100 machines)
gpudeck nodes join-tokens create --cluster-id "$CID" --max-uses 100 --ttl-minutes 43200 -o json | jq -r .token
gpudeck deployments create -f deploy.yaml
gpudeck clusters create -d '{"name": "lab", "type": "bare_metal"}'
gpudeck -o csv --fields name,status nodes list --all > nodes.csv
gpudeck rules create -f idle-unload.yaml && gpudeck rules dry-run "$RULE_ID"
gpudeck layouts apply "$LAYOUT_ID"
gpudeck api-keys create --name gateway -o json     # the key is shown once
gpudeck metrics gpu --metrics gpu_utilization_pct --from 2026-11-17T00:00:00 --aggregate avg
gpudeck billing reports export "$REPORT_ID" > report.csv
gpudeck live events -t nodes -t deployments       # SSE stream, Ctrl-C to stop
gpudeck logs stream --deployment-id "$DID" --level error
```

CSV, PEM and Prometheus text endpoints (`audit export`, `pki ca`, `metrics prometheus`, ...) print
the raw body; binary downloads (`agent download VERSION TARGET > agent.tar.gz`) write raw bytes.

Exit codes: `0` ok, `1` API/server error, `2` usage or validation error, `3` authentication,
`4` not found, `5` cannot connect.

## Where the commands come from

Commands are built from an OpenAPI document when they are needed (`gpudeck --help` only lists
groups and never touches the network):

1. the server's own `GET /api/v1/openapi.json`, cached per server and API version in the cache
   directory for `GPUDECK_SPEC_TTL` seconds (`--refresh-spec` downloads it again),
2. the last cached copy for that server when it cannot be reached (a failed download is retried
   after five minutes),
3. the bundled contract: `libs/contracts/openapi/admin.yaml` when running from a checkout, or the
   copy the wheel build puts at `gpudeck/_spec/admin.yaml` (`hatch_build.py`; never committed).

When the server's API version differs from the bundled one (major or minor), the CLI warns once
per download and follows the server. `gpudeck config spec` shows which spec is in use.

`gpudeck/registry.py` maps each operation to its place in the tree (`COMMAND_MAP`, keyed by the
contract operationId in snake_case; live operations are matched to it by method and path).
Operations a newer server has that the map does not know still appear, under the group named after
their first tag. Hand-written commands (`login`, `logout`, `config`, `live events`, `logs stream`,
`live ws`) live in `gpudeck/commands/`. A new operation in the contract needs one `COMMAND_MAP`
line; the coverage test fails until it has one, and `tests/data/command_tree_v1.json` keeps every
existing command and option from disappearing.

## Development

```bash
.venv/bin/python -m pytest              # respx-mocked admin API, no server needed
.venv/bin/black gpudeck tests && .venv/bin/isort gpudeck tests
.venv/bin/flake8 gpudeck tests && .venv/bin/mypy gpudeck
uv build --out-dir dist/                # wheel + sdist, both carrying gpudeck/_spec/admin.yaml
```
