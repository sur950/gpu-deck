# GPU Deck documentation

Start with the [project README](../README.md) for what GPU Deck is, then pick the guide for what you are doing. The website is at <https://sur950.github.io/gpu-deck/>.

## Run it

| Guide | Read it to |
| --- | --- |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Understand what runs where (control plane on a CPU server, node agent on GPU machines), the ports, sizing and HA, and install with Docker Compose, Kubernetes or an offline bundle; add a GPU machine, deploy a model, call it, upgrade |
| [operations.md](operations.md) | Install details, upgrade, backup and restore, certificate and key rotation, high availability, offline installs, troubleshooting |
| [security-model.md](security-model.md) | Network rules and security controls, and where each is enforced (including the desktop app's IPC lockdown) |
| [SECURITY.md](SECURITY.md) | Supported versions and how to report a vulnerability privately |
| [SUPPORT.md](SUPPORT.md) | Where to ask questions, report bugs and share hardware reports |

## Hardware and engines

| Guide | Read it to |
| --- | --- |
| [hardware/README.md](hardware/README.md) | See the full accelerator x engine support matrix, which model types each engine serves, and verification status |
| [hardware/engines.md](hardware/engines.md) | The same ground organised per engine: what's pinned, requirements per vendor, metrics and API formats |
| [hardware/nvidia.md](hardware/nvidia.md), [amd.md](hardware/amd.md), [apple-silicon.md](hardware/apple-silicon.md), [hygon.md](hardware/hygon.md), [metax.md](hardware/metax.md), [moore-threads.md](hardware/moore-threads.md), [t-head.md](hardware/t-head.md), [cpu.md](hardware/cpu.md) | Driver/runtime minimums, what the agent installs vs. what you install, slicing, multi-GPU support and a prepare-this-machine checklist, per accelerator |

## Understand it

| Guide | Read it to |
| --- | --- |
| [architecture.md](architecture.md) | See the components and the three flows that matter: a machine joining, a serving request and a swap; usage counters; the credentials vault; the desktop app |
| [TESTING.md](TESTING.md) | See what has been verified on real hardware and what is tested only against fixtures |
| [BRAND.md](BRAND.md) | Name, wording, marks, colours and how the brand assets are built |

## Build it

| Guide | Read it to |
| --- | --- |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Learn the architecture layers, extension points, migration rules, Redis key convention, settings and env docs, testing rules, frontend and desktop conventions |
| [RELEASING.md](RELEASING.md) | Version policy, compatibility between agents and control plane, the release checklist and what the release workflow publishes |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Set up each component, the pull request workflow and the contributor checklist |
| [../CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | How we behave |
| [../CHANGELOG.md](../CHANGELOG.md) | What changed in each release and the known limitations |

## Components

Notes for each part of the repository, relative to its own folder.

| Document | Component |
| --- | --- |
| [components/fastapi.md](components/fastapi.md) | Backend: admin API, serving API, hub, worker, scheduler (`apps/fastapi`) |
| [components/fastapi-migrations.md](components/fastapi-migrations.md) | SQL-first Alembic migrations |
| [components/fastapi-cloud.md](components/fastapi-cloud.md) | Cloud GPU VMs on AWS, Azure, GCP and DigitalOcean |
| [components/fastapi-e2e.md](components/fastapi-e2e.md) | End-to-end tests |
| [components/node-agent.md](components/node-agent.md) | The Rust node agent (`apps/node-agent`): commands, config, packaging, behaviour notes |
| [components/desktop.md](components/desktop.md) | The Tauri desktop app (`apps/desktop`): build, signing, updater |
| [components/terraform-provider.md](components/terraform-provider.md) | The Terraform provider (`apps/terraform-provider`); its Registry docs are generated into [`apps/terraform-provider/docs`](../apps/terraform-provider/docs/index.md) |
| [components/infra.md](components/infra.md) | Compose files, Helm chart, offline bundle, observability, dev scripts (`infra/`) |
| [components/contracts.md](components/contracts.md) | The shared contracts in `libs/contracts` and how clients are generated |
| [`apps/cli/README.md`](../apps/cli/README.md) | The `gpudeck` CLI (kept in place: it is the Python package readme) |

## Contracts

The specifications live next to their schemas in [`libs/contracts`](../libs/contracts/): the [environment variable reference](../libs/contracts/env.md), the [serving API](../libs/contracts/openapi/serving.md), the [agent protocol](../libs/contracts/agent-protocol/protocol.md) (with its [changelog](../libs/contracts/agent-protocol/CHANGELOG.md)), the [metrics catalog](../libs/contracts/metrics.md) and the [rules and layouts](../libs/contracts/rules/rules.md).
