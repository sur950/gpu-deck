# Security policy

## Supported versions

Security fixes are made in the latest minor release. This is a one-person project, so older minor versions are not promised fixes; where practical, a critical fix is also backported to the previous minor release for up to 90 days after the next minor release ships.

| Version | Supported |
| --- | --- |
| 1.0.x | Yes |
| < 1.0 | No (pre-release builds) |

Every component (control plane, node agent, desktop app, CLI, Terraform provider, Helm chart and images) is released together under one version number, so "latest" always means one matching set.

## Reporting a vulnerability

Do not open a public issue for a security problem.

Report it privately through GitHub. Open the repository's [Security tab](https://github.com/sur950/gpu-deck/security/advisories/new) and choose Report a vulnerability. Include what you found, steps to reproduce it, the component and version (`gpudeck --version`, the About screen, or the image tag), your deployment type and the impact you expect. Please do not include real API keys, vault keys, certificates or customer data; redact them.

Response timelines, from the day we receive the report:

| Step | Target |
| --- | --- |
| Acknowledge the report | 5 working days |
| Initial assessment (confirmed, needs more information, or not a vulnerability) | 10 working days |
| Fix or mitigation for critical and high severity | 30 days (goal) |
| Fix for medium and low severity | Next regular release |

This is a one-person open source project, so these are goals and not guarantees. We keep you informed until the issue is resolved, publish a GitHub Security Advisory with the fix, and credit reporters in the release notes unless they prefer otherwise. Please give us a reasonable chance to ship a fix before you disclose publicly.

## Scope

In scope:

- The control plane: admin API (sessions, CSRF, login limits, IP rules, audit log), serving API (API-key authentication, OpenAI, Anthropic and Ollama routes, IP allow-list) and the agent hub.
- The node agent and its trust model: join tokens (one-time and multi-use), the internal certificate authority, mutual TLS on the hub WebSocket (8443) and the engine proxy (9443), certificate revocation, the egress lock, and engines staying bound to localhost.
- API keys (`gd-` keys, hashing, show-once, rotation and revocation) and sessions.
- The credentials vault: Fernet encryption at rest, `VAULT_KEYS` rotation, write-only secret fields, the reveal endpoint, and SSH and cloud credentials.
- The desktop app's IPC lockdown, deep links and updater.
- The CLI, the Terraform provider, the Helm chart, the compose files and the container images, including default settings that weaken a protection described in [security-model.md](security-model.md).

Out of scope:

- Vulnerabilities in third-party engines (llama.cpp, vLLM, SGLang, MLX and others), model files, GPU drivers or cloud providers. Report those upstream.
- Issues that need an already-compromised admin account, root on a GPU machine or database access.
- Findings from deployments that ignore [security-model.md](security-model.md), for example exposing the admin API to the internet or disabling TLS.
- Denial of service through resource exhaustion on a machine you already control, and automated scanner output with no demonstrated impact.

## How GPU Deck protects you

The network rules and controls are listed in [security-model.md](security-model.md). In short:

- GPU machines dial out to the agent hub. They join with a join token (one-time, or multi-use for node pools) and then use certificates from the internal CA. The only inbound port on a GPU machine is the agent proxy (9443), which accepts only the API layer's certificate over mutual TLS.
- API keys are stored as SHA-256 hashes and shown once. Admin passwords use Argon2id. Sessions are opaque tokens in HttpOnly cookies with CSRF protection.
- Secrets live in the vault, encrypted at rest and never written to logs.
- The control-plane containers run non-root with a read-only root file system and dropped capabilities (Compose and Helm).
