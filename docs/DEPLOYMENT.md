# Deploying GPU Deck

What runs where, and how to install it. GPU Deck has two halves:

- The **control plane** runs on **one CPU-only server** (or on Kubernetes) in Docker. It holds the web UI, the APIs, the database and the scheduler. It never touches a GPU.
- Every **GPU machine** runs **only the GPU Deck node agent**, plus the vendor driver and the inference engines the agent starts. The agent dials out to the control plane. Models are cached on the GPU machine.

Nothing runs on a GPU machine except the agent and its engines, and nothing on a GPU machine listens for the internet. Put any AI gateway (LiteLLM, Kong, Portkey, your own) in front of the serving API for users, quotas and rate limits; GPU Deck stores nothing about end users.

```mermaid
flowchart LR
  subgraph Clients["Clients"]
    GW["Your AI gateway or apps<br/>OpenAI, Anthropic, Ollama formats"]
    ADM["Admin browser, CLI, Terraform"]
  end

  subgraph CP["Control plane: ONE CPU server (Docker Compose) or Kubernetes"]
    WEB["web UI + /api gateway :3180"]
    ADMIN["admin API :8180"]
    SERVE["serving API (API layer) :8181"]
    HUB["agent hub :8443 (mTLS)"]
    JOBS["worker + beat + scheduler"]
    PG[("PostgreSQL 17")]
    RD[("Redis 7.4")]
    OBS["optional: Prometheus, Grafana"]
  end

  subgraph G1["GPU machine 1: Linux, NVIDIA or AMD"]
    A1["gpudeck-agent<br/>systemd service, proxy :9443"]
    E1["engines on 127.0.0.1<br/>llama.cpp, vLLM, SGLang, ..."]
    M1[("model cache")]
    A1 --> E1
    E1 --- M1
  end

  subgraph G2["GPU machine 2: Mac, Apple silicon"]
    A2["gpudeck-agent<br/>launchd service, proxy :9443"]
    E2["engines on 127.0.0.1<br/>llama.cpp, MLX"]
    A2 --> E2
  end

  GW -->|"HTTPS, API key"| SERVE
  ADM --> WEB
  ADM -.->|"CLI, Terraform"| ADMIN
  WEB -->|"/api/*"| ADMIN
  ADMIN --> PG
  ADMIN --> RD
  SERVE --> RD
  JOBS --> PG
  JOBS --> RD
  HUB --> PG
  HUB --> RD
  OBS -.-> ADMIN
  A1 ==>|"dials out: mTLS WSS :8443"| HUB
  A2 ==>|"dials out: mTLS WSS :8443"| HUB
  SERVE -->|"mTLS :9443"| A1
  SERVE -->|"mTLS :9443"| A2
```

Solid arrows are connections opened by the arrow's source. The thick arrows are the agents' outbound connections: the only thing a GPU machine opens toward the control plane, for the install as well as at run time. The only inbound port on a GPU machine is the agent's proxy (9443), and it accepts only the API layer's certificate.

**What has been verified.** The Compose control plane, the node agent installed with the one-line command on a Mac (launchd), llama.cpp and MLX serving, and all three API formats have been run end to end. The Linux install path (systemd), NVIDIA and AMD machines, the container engines (vLLM, SGLang, TensorRT-LLM), the Kubernetes chart and DaemonSet (linted and rendered in CI, not yet installed on a live cluster) and the cloud providers are implemented and tested against fixtures but not yet verified on real hardware or accounts. [TESTING.md](TESTING.md) has the full list; please send a [hardware report](https://github.com/sur950/gpu-deck/issues/new?template=hardware_report.yml) if you try them.

## 1. What runs where

### Control plane (one CPU server)

One image (`ghcr.io/sur950/gpu-deck-api`) provides six of the processes; the web UI has its own image. The Compose files are [`infra/docker/docker-compose.yml`](../infra/docker/docker-compose.yml) plus the self-contained overlay [`docker-compose.full.yml`](../infra/docker/docker-compose.full.yml).

| Container | Image | Port | Default bind | What it does |
| --- | --- | --- | --- | --- |
| `web` | `gpu-deck-web` | 3180 | 127.0.0.1 (`GPUDECK_BIND`) | Admin web UI (Next.js) and a small gateway that forwards `/api/*` to the admin API (`GPUDECK_API_INTERNAL_URL`, set by Compose and Helm), so the UI and its API share one origin. Nothing deployment-specific is compiled into the image: it works on any hostname |
| `admin-api` | `gpu-deck-api` | 8180 | 127.0.0.1 (`GPUDECK_BIND`) | `/api/v1`: auth, fleet, models, deployments, rules, billing, audit. Used by the web UI (through its gateway), the CLI and Terraform |
| `api-layer` | `gpu-deck-api` | 8181 | 127.0.0.1 (`GPUDECK_SERVING_BIND`) | Serving API: OpenAI, Anthropic and Ollama formats, API-key auth, routes to the node agents over mTLS |
| `agent-hub` | `gpu-deck-api` | 8443 | 0.0.0.0 (`GPUDECK_HUB_BIND`) | Everything a GPU machine needs: the install script (`/agent/v1/install.sh`), agent builds and their sha256 (`/agent/v1/downloads/...`), the CA (`/agent/v1/ca.pem`), node join (`/agent/v1/join`) and the agents' mTLS WebSocket (`/agent/v1/ws`) |
| `worker` | `gpu-deck-api` | 9464 (metrics, internal) | none | Celery: usage writes, rollups, billing, alert delivery |
| `beat` | `gpu-deck-api` | none | none | Celery beat. Exactly one per deployment |
| `scheduler` | `gpu-deck-api` | 9465 (metrics, internal) | none | Rules, layouts, swaps, housekeeping; elects a leader, so more than one is safe |
| `postgres` | `postgres:17.12-alpine` | 5432 (internal) | none | All state (full overlay) |
| `redis` | `redis:7.4.12-alpine` | 6379 (internal) | none | Cache, routing table, pub/sub, Celery broker (full overlay) |
| `prometheus`, `grafana`, `otel-collector` | upstream images | 9090, 3000, 4317/4318 | 127.0.0.1 | Optional, `--profile observability` |

To use your own PostgreSQL and Redis instead of the overlay, leave out `docker-compose.full.yml`, set `GPUDECK_DATABASE_URL` and `GPUDECK_REDIS_URL` (credentials such as `POSTGRES_USER` and `REDIS_PASSWORD` come from the `.env`), and create the Docker network the base file joins first: `docker network create gpu-deck-deps`.

All GPU Deck containers run as a non-root user with a read-only root file system, dropped capabilities and `no-new-privileges`. Two named volumes hold state that is not in the database: `pki` (the CA bundle and the hub, API layer and control-plane certificates, all re-created from the vault when missing) and `agent-cache` (node-agent builds fetched from the GitHub release).

For HTTPS in front of the web UI and the serving API, use your own reverse proxy (Caddy, nginx, Traefik, a cloud load balancer). The web container already serves `/api` on the UI's origin, so one proxied host covers the UI and the admin API. A development-only Caddy (`tls internal`) is the `proxy` profile of `docker-compose.deps.yml`; see [DEVELOPMENT.md](DEVELOPMENT.md). The agent hub is **not** behind an HTTP proxy: it terminates its own TLS 1.3 and requires client certificates, so forward TCP 8443 straight to it.

#### Sizing

These are starting points derived from the Helm chart's resource requests and the container defaults, not benchmark results. The API layer adds roughly 1.2 to 1.4 ms of median time to first byte on a streamed response in the repository's overhead test; the engines, not the control plane, decide your throughput.

| Fleet | Server | Notes |
| --- | --- | --- |
| Evaluation, 1 to 5 GPU machines | 4 vCPU, 8 GB RAM, 50 GB SSD | Everything on one host, including Postgres and Redis |
| Small production, up to about 20 machines | 8 vCPU, 16 GB RAM, 100 GB SSD | Add Prometheus and Grafana on the same host or elsewhere |
| Larger | Kubernetes, or external managed Postgres and Redis | Scale the API layer on CPU; see HA below |

Disk is dominated by PostgreSQL time series. Raw metrics are kept 48 hours, one-minute rollups 90 days, logs 14 days, traces 7 days, usage events 400 days and the audit log 365 days; lower the `*_RETENTION_*` settings in [`env.md`](../libs/contracts/env.md) to keep less. Redis holds only caches, queues and the routing table, so it needs no backup.

#### High availability

The single-server Compose setup is not highly available. For HA run the [Kubernetes chart](#6-kubernetes-helm): two or more replicas of the admin API, API layer, agent hub and scheduler, PodDisruptionBudgets, an HPA on the API layer, and managed or operator-run PostgreSQL and Redis (Patroni, CloudNativePG, Sentinel or a cloud service). The scheduler elects one leader with Postgres leases, and a swap resumes from its persisted step on the new leader. Agents keep their engines running while the control plane is down, and serving continues as long as the API layer and Redis are up. Details: [operations.md](operations.md) section 6.

### GPU machine (agent only)

Each GPU machine runs exactly one extra program, the **node agent** (`gpudeck-agent`, a single Rust binary), as a native service: systemd on Linux, launchd on macOS. It detects GPUs, joins the control plane, starts and stops engines on command, ships metrics and logs, caches model files and runs the mTLS proxy on port 9443. The control plane never SSHes in and never runs anything on the machine except through the agent.

A Kubernetes DaemonSet (`gpu-deck-agent` image) runs the agent on GPU nodes that are part of a cluster; see [section 6](#6-kubernetes-helm).

#### What the agent installs, and what you install

| | The agent / its install script | You |
| --- | --- | --- |
| Agent | The binary (`/usr/local/bin/gpudeck-agent`, or `~/.gpudeck/bin` for a Mac install without sudo), a `gpudeck` system user in the `video` and `render` groups, the systemd unit or launchd plist, its state directory (`/var/lib/gpudeck`), its certificate from the internal CA | Nothing beyond running the install command |
| GPU driver and runtime | Nothing. It only reads what the driver provides | NVIDIA: the NVIDIA driver (the agent reads NVML, falling back to `nvidia-smi`). AMD: the amdgpu driver and ROCm user space (`amd-smi` or `rocm-smi`). Hygon, MetaX, Moore Threads, T-Head: the vendor driver and its SMI tool (`hy-smi`, `mx-smi`, `mthreads-gmi`, `ppu-smi`). Apple silicon: nothing (Metal and IOKit ship with macOS) |
| Container runtime | Nothing | Docker, Podman or nerdctl, only if you use container engines (vLLM, SGLang, TensorRT-LLM, vLLM-Omni, custom images) or SSH GPU instances. NVIDIA: the NVIDIA Container Toolkit so `--gpus` works. AMD: `/dev/kfd` and `/dev/dri` are passed through. The agent user must be allowed to run the container CLI (typically membership of the `docker` group). Container engines are not supported on macOS nodes |
| Engines | Downloads the pinned build of each native engine on first use into `<state dir>/engines/<engine>/<version>/`, verified by sha256, picking the build for the machine's GPU (see the table below); creates a Python venv for MLX, mlx-audio and SGLang Diffusion; builds whisper.cpp from the pinned source on a Mac. Container engines are pulled by the container runtime on first start, pinned by tag and digest | Python 3.11+ for the venv engines (3.12 for SGLang Diffusion), `cmake` and the Xcode Command Line Tools for whisper.cpp on a Mac. Or install any engine yourself and list it under `[engine_paths]` in `agent.toml` (or put it on `PATH`): an installed engine always wins over a download |
| Models | Downloads the model files you deploy (Hugging Face or ModelScope, or your mirror), verifies them and keeps them in the cache directory (`/var/lib/gpudeck/cache`, limit 70 % of the volume by default, least-recently-used sweep) | Disk space on that volume. Local model directories (Ollama store, LM Studio, plain GGUF) are used in place |
| Firewall | Optionally programs an egress lock (`pf` on macOS, nftables on Linux) when started with `--apply-egress-lock` | Allow inbound 9443 from the control plane server only; allow outbound 8443 to the hub, and 443 to Hugging Face / ModelScope or your mirror |

Notes that save time:

- The packaged systemd unit runs the agent as `gpudeck` with `ProtectSystem=strict` and `ProtectHome=yes`. Install engines in a system path such as `/usr/local/bin` or `/opt`, not under a home directory.
- On macOS the install script keeps your shell `PATH` in the launchd plist, so engines you installed yourself (and the Python the agent uses for MLX venvs) resolve. The MLX venvs need Python 3.11 or newer (python.org or Homebrew; the Xcode tools ship 3.9); point `GPUDECK_AGENT_PYTHON` at a specific interpreter if needed.
- The agent refuses an engine build without a pinned sha256. Linux builds of llama.cpp, whisper.cpp and stable-diffusion.cpp need `libgomp1` (and `libvulkan1` for the Vulkan builds), which most distributions have, and a recent enough glibc: see [Linux requirements per platform](#linux-requirements-per-platform).
- An empty llama.cpp router (every model moved away) is stopped after `router_idle_stop_seconds` (agent config, default 120, `0` keeps it running).
- In a container the agent runs under `tini` (the agent image's entrypoint). Started as PID 1 without it, the agent reaps orphaned engine processes itself (set `GPUDECK_AGENT_INIT=0` to turn that off).
- In locked-down environments without internet access install the engines yourself and set `engine_paths`, or allow the agent to reach `github.com`, `objects.githubusercontent.com` and `files.pythonhosted.org` / `pypi.org`.

#### Engines at a glance

| Engine | Pinned build | Runs as | What the agent installs | You provide |
| --- | --- | --- | --- | --- |
| llama.cpp (GGUF, router mode) | ggml-org release `b12500` | native (official image as a fallback) | macOS arm64 (Metal) or x86_64; Linux x86_64: CUDA 12.8 (with its CUDA runtime bundle, driver 525.60+), ROCm 10.0, Vulkan or CPU; Linux arm64: CUDA 13.5 (driver 580+), Vulkan or CPU, picked from the machine's GPU, driver and glibc | Linux: `libgomp1` (Vulkan: `libvulkan1`); see [Linux requirements per platform](#linux-requirements-per-platform) for the glibc each build needs |
| MLX (`mlx_lm.server`) | mlx-lm 0.32.2 | native | A venv with the pinned wheel | Python 3.11+ on the Mac |
| mlx-audio | mlx-audio 0.6.7 | native | A venv with the pinned wheel (`server`, `stt`, `tts` extras) | Python 3.11+ on the Mac |
| vLLM | `vllm/vllm-openai:v0.34.0@sha256:20fd...` | container | Pulled by the runtime | Container runtime, NVIDIA driver 580+ (CUDA 13.0 image). AMD: add an engine version with a ROCm image |
| vLLM-Omni | `vllm/vllm-omni:v0.32.0@sha256:f713...` | container | Pulled by the runtime | Container runtime, NVIDIA driver 580+ |
| SGLang | `lmsysorg/sglang:v0.5.23@sha256:f832...` | container | Pulled by the runtime | Container runtime, NVIDIA driver 580+. AMD: add a version with `lmsysorg/sglang:v0.5.23-rocm720-mi30x` |
| TensorRT-LLM | `nvcr.io/nvidia/tensorrt-llm/release:1.3.0@sha256:bb9a...` | container | Pulled by the runtime | Container runtime, NVIDIA driver 580+ (CUDA 13.4 image) |
| SGLang Diffusion | sglang 0.5.23 wheel | native | A venv with the pinned wheel and its `diffusion` extra (Linux x86_64 and arm64) | Python 3.12, glibc 2.34+ |
| whisper.cpp | ggml-org release `b5907` | native | Linux x86_64 / arm64 release build (CPU); on a Mac, the pinned source built with CMake (Metal) | Mac: `cmake` and the Xcode Command Line Tools |
| stable-diffusion.cpp | release `master-1106-532d2c1` | native | macOS arm64 build (macOS 26+); Linux x86_64 CPU, Vulkan or ROCm 10.1 build (built on Ubuntu 26.04, so it needs a glibc that new) | On older systems, your own `sd-server` in `engine_paths` |
| Custom | your image | container | Pulled by the runtime | Your image and its launch, health and metrics settings |

#### Linux requirements per platform

Prebuilt Linux engines need the glibc of the system they were built on. The agent reads the
machine's glibc (`getconf GNU_LIBC_VERSION`, reported to the control plane), skips builds the
machine cannot run, and checks the installed binary with `ldd` before starting it, so a missing
library is reported by name instead of an engine that exits with code 127.

| llama.cpp `b12500` build | Built on | Needs | On older systems (Ubuntu 22.04, Debian 12, RHEL 9) |
| --- | --- | --- | --- |
| Linux x86_64 CPU, Vulkan | Ubuntu 22.04 | glibc 2.34+, libstdc++ (GLIBCXX 3.4.30), `libgomp1`, `libssl3`; Vulkan: `libvulkan1` and the GPU's Vulkan driver | Runs as is |
| Linux x86_64 CUDA 12.8 | Ubuntu 24.04 | glibc 2.38+, NVIDIA driver 525.60+ | Official image `ghcr.io/ggml-org/llama.cpp:server-cuda-b12464` when Docker or Podman (with the NVIDIA container toolkit) is installed; otherwise the x86_64 Vulkan build |
| Linux x86_64 ROCm 10.0 | Ubuntu 24.04 | glibc 2.38+, ROCm 10.0 | The `b10375` ROCm 7.14 build (built on Ubuntu 22.04, needs ROCm 7.14+); else the image `server-rocm-b12464` (ROCm 7.2.1 inside); else Vulkan |
| Linux arm64 CPU, Vulkan | Ubuntu 24.04 | glibc 2.38+, libstdc++ (GLIBCXX 3.4.32), `libgomp1`, `libssl3` | Official image (`server-b12464`, `server-vulkan-b12464`, both multi-arch) |
| Linux arm64 CUDA 13.5 | Ubuntu 24.04 | glibc 2.38+, NVIDIA driver 580+ | Official image `server-cuda13-b12464` |
| macOS arm64 / x86_64 | macOS | Nothing | — |

In order, the agent uses: an engine you installed yourself (`engine_paths` in `agent.toml`, or
`GPUDECK_AGENT_ENGINE_PATHS_LLAMA_CPP`), always first; the pinned build when the machine runs
it; an older pinned build of the same accelerator; the official image (only when the machine
has Docker or Podman; images are published for some builds only, `b12464` is the closest to
`b12500`); otherwise it refuses with a message that names the glibc the machine has, the one the
build needs and these options. The control plane uses the same rules before it places a model,
so a machine nothing fits is shown as unable to run the engine with the same explanation.

Machines with glibc 2.38 or newer (Ubuntu 24.04, Debian 13, RHEL 10, Fedora 39 and later) run
every pinned build natively. Musl distributions (Alpine) run none of the glibc builds: use the
official image or your own build. The agent's Kubernetes image is Ubuntu 24.04 based, so on
Kubernetes every build runs inside it whatever the host OS is.

whisper.cpp's Linux release builds come from Ubuntu 22.04 (glibc 2.35+); stable-diffusion.cpp's
from Ubuntu 26.04 (use your own `sd-server` on older systems); the SGLang Diffusion wheel is
`manylinux_2_34` (glibc 2.34+).

The full digests and per-platform files are in [`apps/fastapi/app/services/engines/seed.py`](../apps/fastapi/app/services/engines/seed.py); a new release of GPU Deck adds its pins as new engine versions and never changes what running deployments use.

Which of these have run on real hardware, and which are tested only against recorded fixtures, is listed in the [README](../README.md#hardware-and-engine-support) and in [TESTING.md](TESTING.md). The control plane checks driver, CUDA and ROCm minimums and the platform before it lets you deploy.

## 2. Network and ports

Deny everything, then open only these. The full table of rules and where each is enforced is in [security-model.md](security-model.md).

| From | To | Port | Protocol | Why | Who opens it |
| --- | --- | --- | --- | --- | --- |
| Your AI gateway or apps | Control plane, serving API | 443 (to 8181) | HTTPS + API key | Inference requests | Client. Restrict to the gateway's IPs |
| Admins | Control plane, web UI and admin API | 443 (to 3180 / 8180) | HTTPS | Management | Browser, CLI, Terraform. Private network or VPN only |
| **Node agent** | **Control plane, agent hub** | **8443** | **TLS 1.3; mTLS on the WebSocket** | Install script, agent build and CA (with a join token), join, then heartbeats, commands, metrics, logs | **The install command and the agent, outbound** |
| **Control plane (API layer, jobs)** | **Node agent proxy** | **9443** | **mTLS** (API layer certificate) | Inference traffic, health probes | **The control plane, inbound to the GPU machine: the only inbound port** |
| Node agent | Engines | 127.0.0.1, ports 10240 to 10340 | HTTP | Engines never bind a network interface | Local only |
| Node agent | Hugging Face, ModelScope | 443 | HTTPS | Model downloads (or your mirror) | The agent, outbound |
| Node agent | GitHub, PyPI | 443 | HTTPS | Pinned engine builds on first use (not needed when you install engines yourself) | The agent, outbound |
| Control plane (admin API, hub) | GitHub | 443 | HTTPS | Node-agent builds of the matching release, once per build (not needed offline, see section 7) | The control plane, outbound |
| Control plane | PostgreSQL, Redis | 5432, 6379 | TLS recommended | State | Private network only |
| Admins | GPU machines | 22 | SSH | Maintenance | Your bastion or VPN (outside GPU Deck) |
| Admins' users | GPU machines | 2200 to 2299 | SSH | Only if you use SSH GPU instances | Per instance, optional CIDR allow-list |

Trust: on first start the control plane creates an internal certificate authority (key encrypted in the vault) and, from it, the hub's server certificate and the API layer's and control plane's client certificates. A node joins with a join token (one-time by default, 15 minutes; or a multi-use enrolment token for node pools) plus a certificate signing request, and gets a certificate signed by that CA. The install command pins the CA's sha256 before it fetches anything from the hub. The agent hub requires a valid client certificate on the WebSocket; the agent proxy requires the API layer's client certificate; revoking a certificate drops the node at once.

## 3. Deploy the control plane with Docker Compose

You need a Linux server (or a Mac) with Docker and the Compose plugin, about 10 GB free for images, and DNS names if you want HTTPS. The images come from GHCR (`ghcr.io/sur950/gpu-deck-api`, `-web`, `-agent`); the Compose files, observability config and helper scripts come from the repository at the release tag.

### 3.1 Get the files and the images

```bash
git clone --branch v1.0.0 --depth 1 https://github.com/sur950/gpu-deck.git
cd gpu-deck
export GPUDECK_VERSION=1.0.0            # image tag; without it Compose looks for the tag "local"
COMPOSE="docker compose -f infra/docker/docker-compose.yml -f infra/docker/docker-compose.full.yml"
$COMPOSE pull
```

All images are signed (cosign keyless) and carry SBOMs and provenance; the verification commands are in [RELEASING.md](RELEASING.md).

### 3.2 Configure

```bash
cp apps/fastapi/.env.example apps/fastapi/.env
openssl rand -hex 32                                                            # SECRET_KEY
openssl rand -base64 32 | tr '+/' '-_'                                         # VAULT_KEYS (a Fernet key)
```

Edit `apps/fastapi/.env` and replace every `CHANGE-ME`. These matter most:

| Variable | Set it to |
| --- | --- |
| `SECRET_KEY` | The `openssl` output |
| `VAULT_KEYS` | The Fernet key. Back it up separately: without it the database's credentials and CA key are unreadable |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Your admin login (12+ characters). Created on first start; changing the password in `.env` later re-applies it |
| `APP_ENV` | `production` (the Compose default) turns on Secure cookies and strict host checks, so serve the UI over HTTPS. For a trial on one machine over plain HTTP, export `APP_ENV=development` before `docker compose` |
| `AGENT_HUB_PUBLIC_URL` | `https://<server DNS name>:8443`, exactly as the GPU machines reach it. It goes into join and install commands and the hub certificate, and it is the only address a GPU machine needs |
| `AGENT_HUB_TLS_SANS` | Extra DNS names or IPs for the hub certificate (default `localhost,127.0.0.1`) |
| `PUBLIC_API_URL`, `PUBLIC_SERVING_URL`, `FRONTEND_URL`, `CORS_ORIGINS` | The public URLs of the admin API (the UI's URL + `/api/v1` when you use the web container's gateway), serving API and web UI. With `APP_ENV=production` the admin API only answers the host names listed here (plus `TRUSTED_HOSTS`) |
| `GATEWAY_IP_ALLOWLIST` | Source CIDRs of your AI gateway (serving API) |
| `TRUSTED_PROXIES` | Your reverse proxy's addresses, so `X-Forwarded-For` is believed. Add the Compose network (for example `172.16.0.0/12`) so client addresses forwarded by the web container count for IP rules |

`DATABASE_URL` and `REDIS_URL` are set by the overlay for its own containers. Give the database and Redis passwords in `infra/docker/.env` (Compose reads it next to the Compose file) or in your shell:

```bash
cat >> infra/docker/.env <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)
EOF
```

Every setting is documented in [`libs/contracts/env.md`](../libs/contracts/env.md).

**Nothing else to prepare.** The published images need no build-time settings and no mounted files:

- The web image serves the UI and forwards `/api/*` to the admin API (`GPUDECK_API_INTERNAL_URL=http://admin-api:8180`, set in the Compose file), so it works on whatever host name you open it with, behind any TLS proxy and in the desktop app. Branding (`NEXT_PUBLIC_APP_NAME`, `NEXT_PUBLIC_BRAND_COLOR`, ...) is the only thing still compiled in; rebuild the web image to change it (`$COMPOSE build web`).
- The internal CA, the hub certificate and the API layer's client certificate for the agent proxies are created on first start, kept in the vault and written to the shared `pki` volume. To bring your own API layer certificate instead, mount it and set `API_LAYER_CLIENT_CERT_PATH` / `API_LAYER_CLIENT_KEY_PATH`.
- Node-agent builds are fetched by the admin API and the hub from the GitHub release that matches the control plane's version (`AGENT_RELEASE_URL`), checked against that release's `SHA256SUMS.txt` and cached in the `agent-cache` volume, the first time a machine (or an admin) downloads them. Offline sites use the files of the bundle instead (section 7); to serve your own builds, mount a directory with `gpudeck-agent-<version>-<target>.tar.gz` and `SHA256SUMS` (or `<version>/<target>/` folders) and set `AGENT_DIST_DIR` to it; local builds win over the release.

### 3.4 Start it

```bash
$COMPOSE --profile tools run --rm migrate        # database schema (alembic upgrade head)
$COMPOSE up -d                                   # add: --profile observability  for Prometheus + Grafana
$COMPOSE ps                                      # every service should become healthy
```

On first start the admin API creates the admin user from `.env` and the internal CA; the hub and the API layer issue their certificates from it. The web UI is at `http://127.0.0.1:3180` (its `/api/v1` is the admin API) and the serving API at `http://127.0.0.1:8181`; both bind to localhost until you set `GPUDECK_BIND` and `GPUDECK_SERVING_BIND` (put a TLS reverse proxy in front, see the Caddy example below). Health checks: `http://127.0.0.1:3180/api/v1/health/ready` (or 8180 directly) and `http://127.0.0.1:8181/health`.

### 3.5 Optional: HTTPS in front

A minimal Caddy example, run on the same server (adjust names; set `GPUDECK_BIND=127.0.0.1`, `TRUSTED_PROXIES` to Caddy's address, and the `PUBLIC_*` / `CORS_ORIGINS` variables to these URLs):

```caddyfile
gpudeck.example.com {
    reverse_proxy 127.0.0.1:3180 { flush_interval -1 }   # UI and /api (live updates stream)
}
gpudeck-api.example.com {
    reverse_proxy 127.0.0.1:8181 { flush_interval -1 }
}
```

Then `PUBLIC_API_URL=https://gpudeck.example.com`, `FRONTEND_URL` and `CORS_ORIGINS=https://gpudeck.example.com`, and point the CLI and Terraform at `https://gpudeck.example.com`.

The hub (8443) is published directly by Compose; do not proxy it.

## 4. First run: log in, add a GPU machine, serve a model

1. **Log in.** Open the web UI and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
2. **Create a cluster.** Clusters, new cluster. A cluster groups machines and carries a price plan; choose `bare_metal` or `vm` for machines you operate yourself.
3. **Generate a join token.** Nodes, **Add node**, pick the cluster. You get a one-time token (`gdj_...`, valid 15 minutes by default) and the install command; for a pool of machines choose how many may join with it (a multi-use enrolment token, up to a year). The same call over the API is `POST /api/v1/nodes/join-tokens` (`max_uses`, `ttl_minutes`); the CLI is `gpudeck nodes join-tokens create --cluster-id <id> [--max-uses 50 --ttl-minutes 43200]`. Every join is audited with the token it used; revoke a token at any time.
4. **Install the agent on the GPU machine.** Log in to the machine, install its GPU driver (and container runtime if needed, section 1), then run the command shown in the UI. It looks like this:

   ```bash
   sh -c 'c=$(mktemp) && curl -fsSk -o "$c" https://gpudeck-hub.example.com:8443/agent/v1/ca.pem && echo "<ca sha256>  $c" | (sha256sum -c - 2>/dev/null || shasum -a 256 -c -) >/dev/null && curl -fsSL --cacert "$c" -H "X-GPUDeck-Join-Token: gdj_..." https://gpudeck-hub.example.com:8443/agent/v1/install.sh | sh'
   ```

   Everything comes from the agent hub, so the machine needs outbound 8443 only. The command fetches the hub's CA and checks it against the sha256 pinned in the command, then fetches the install script over TLS verified by that CA. The script (authorised only by the join token) works out the OS and CPU, downloads the matching build from `/agent/v1/downloads/<version>/<target>` with its SHA-256 (the hub fetches it from the GitHub release on first use), verifies it, installs it, creates the `gpudeck` user and the systemd unit (Linux) or a launchd agent (macOS), runs `gpudeck-agent join` with the hub URL and the pinned CA fingerprint, and starts the service. It needs `curl` and `sudo` (a Mac without sudo installs under `~/.gpudeck`). When the hub has a certificate from a public CA (`AGENT_HUB_TLS_CERT_FILE`) the command is a plain `curl -fsSL ... | sh`.

   **Without the script** (nothing but the hub port is needed): download `gpudeck-agent-<version>-<target>.tar.gz` and `SHA256SUMS.txt` from the [release](https://github.com/sur950/gpu-deck/releases), verify the checksum, unpack, then:

   ```bash
   sudo install -m 0755 gpudeck-agent /usr/local/bin/gpudeck-agent
   sudo gpudeck-agent join --hub https://gpudeck-hub.example.com:8443 --token gdj_... --ca-fingerprint <sha256>
   # then install packaging/systemd/gpudeck-agent.service (Linux) or the launchd plist (macOS) from the archive
   ```

   Verify the machine: Nodes shows it `online` within a few seconds, with its GPUs, driver and installed engines.
5. **Add a model.** Catalog, **Add a model**: search Hugging Face or ModelScope (store a token under Credentials for gated models) or scan a local path.
6. **Deploy it.** Catalog, **Deploy** (or Deployments, New deployment). The wizard picks an engine that fits the model format and the GPU, and shows a scheduler dry run of where it would be placed. The agent downloads the files into the machine's model cache, starts the engine on 127.0.0.1 and the instance turns `ready`.
7. **Create an API key.** API keys, new API key. The `gd-...` key is shown once; only its hash is stored.
8. **Call it.** The serving API speaks three formats on one port. Replace the host and model name:

   ```bash
   # OpenAI
   curl https://gpudeck-api.example.com/v1/chat/completions \
     -H "Authorization: Bearer gd-..." -H 'content-type: application/json' \
     -d '{"model":"qwen3-0.6b","messages":[{"role":"user","content":"hi"}]}'

   # Anthropic
   curl https://gpudeck-api.example.com/anthropic/v1/messages \
     -H "x-api-key: gd-..." -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
     -d '{"model":"qwen3-0.6b","max_tokens":128,"messages":[{"role":"user","content":"hi"}]}'

   # Ollama
   curl https://gpudeck-api.example.com/ollama/api/chat \
     -H "Authorization: Bearer gd-..." \
     -d '{"model":"qwen3-0.6b","messages":[{"role":"user","content":"hi"}],"stream":false}'
   ```

   The explicit prefixes (`/openai/v1`, `/anthropic/v1`, `/ollama/api`) always work; the bare `/v1` and `/api` paths are kept for drop-in clients. Point SDKs at `OPENAI_BASE_URL=https://host/openai/v1`, `ANTHROPIC_BASE_URL=https://host/anthropic` or `OLLAMA_HOST=https://host/ollama`. Contract: [`libs/contracts/openapi/serving.md`](../libs/contracts/openapi/serving.md).
9. **Put any AI gateway in front.** In your gateway, add GPU Deck as an OpenAI-compatible (or Anthropic or Ollama) upstream at the serving API URL with a `gd-` key, and keep users, quotas, rate limits and guardrails there. Restrict the serving API to the gateway's IPs with `GATEWAY_IP_ALLOWLIST` or an in-app IP rule. GPU Deck meters per API key, so give the gateway its own key (or one per tenant) and read usage and cost under Metering and Billing.

Rules (idle unload, high-load grow, time-of-day layouts and the rest) are in the web UI under Rules and Layouts; see [architecture.md](architecture.md) for how swaps work.

## 5. Add more machines, clouds and SSH instances

- More GPU machines: repeat steps 3 and 4, with one token per machine or one multi-use token for the batch. With Terraform, the `gpudeck_join_token` resource issues the tokens (`max_uses` for a pool).
- Cloud GPU VMs (AWS, Azure, GCP, DigitalOcean): store the account credential in the vault and provision from Cloud; cloud-init installs the agent and joins the node. These providers are implemented and tested against recorded API replies but have not been run against real accounts, see [components/fastapi-cloud.md](components/fastapi-cloud.md).
- SSH GPU instances: a container with sshd and a slice of a GPU, on a node that has Docker or Podman.

### Observing engines GPU Deck didn't start

A GPU machine can run model servers you started yourself (a vLLM launched by hand, an image or embedding service). List them in the agent's `agent.toml` and GPU Deck shows them as **External** deployments with live sessions, throughput, latency, KV cache, logs and their GPU memory, without ever controlling them:

```toml
[[observe]]
name = "main"                         # unique on this node
engine = "vllm"                       # vllm | sglang | llamacpp | tgi | openai | generic
url = "http://127.0.0.1:8000"         # GET /metrics, /health, /v1/models only
log_file = "/var/log/engines/main.log" # optional: followed read-only, lines shipped redacted

[[observe]]
name = "img"
engine = "generic"                    # health + access-log stats (requests, status codes, endpoints)
log_file = "/var/log/engines/img.log"
model = "example-image-model"        # display name
listen_port = 8100                    # find its processes for per-process GPU memory
```

Restart the agent after editing the file. The agent only reads: it never starts, stops or signals those processes, never binds their ports, and with only `log_file` set it makes no network request. External deployments are skipped by the scheduler, rules, layouts, swaps, the serving routing table and benchmarks; every change from the UI, API or CLI answers `409 EXTERNAL_DEPLOYMENT`. They work on a drained node too, and are not billed. The memory they hold counts as used when GPU Deck places models or slices, so nothing is planned into it. Metrics read from a log have no time-to-first-token or end-to-end histograms (point `url` at the server's `/metrics` for those). Matching a process by `listen_port` needs the agent to see the server's `/proc/<pid>/fd` (same user or root); otherwise use `pid_cmd_contains = "<text in its command line>"`. GPUs and nodes running only such servers show as *Running (external)* rather than *Idle*, and a node whose agent is gone (offline, heartbeat stale, certificate revoked) shows a red *Detached* state with its last known processes greyed; *Drained* stays a neutral scheduling state. Every key: [node-agent.md](components/node-agent.md#observed-engines).

## 6. Kubernetes (Helm)

The chart deploys every control-plane component with PodDisruptionBudgets, an HPA on the API layer, default-deny NetworkPolicies, ingress, a migration hook and optional ServiceMonitors and dashboards. PostgreSQL and Redis are **not** part of the chart: bring managed or operator-run ones.

```bash
kubectl create namespace gpu-deck
kubectl -n gpu-deck create secret generic gpu-deck-env \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=VAULT_KEYS="<fernet key>" \
  --from-literal=ADMIN_USERNAME=admin --from-literal=ADMIN_PASSWORD="<12+ chars>" \
  --from-literal=DATABASE_URL="postgresql://user:pass@postgres.internal:5432/gpu_deck" \
  --from-literal=REDIS_URL="redis://:pass@redis.internal:6379/0"

helm upgrade --install gpu-deck oci://ghcr.io/sur950/charts/gpu-deck --version 1.0.0 \
  -n gpu-deck --set secrets.existingSecret=gpu-deck-env -f my-values.yaml
```

In `my-values.yaml` set the hosts and URLs (`ingress.admin.host`, `ingress.serving.host`, `config.ADMIN_PUBLIC_URL`, `config.API_LAYER_PUBLIC_URL`, `config.AGENT_HUB_PUBLIC_URL`, `config.FRONTEND_URL`, `config.CORS_ORIGINS`), the gateway's addresses (`ingress.serving.sourceRanges`, `networkPolicy.gatewayCIDRs`), where the nodes, database and Redis live (`networkPolicy.nodeCIDRs`, `databaseCIDRs`, `redisCIDRs`) and the ingress classes. The agent hub is a `LoadBalancer` Service (TCP 8443, TLS inside the pod), never an HTTP ingress. The chart's defaults and every value are in [`infra/helm/gpu-deck/values.yaml`](../infra/helm/gpu-deck/values.yaml); the notes in [components/infra.md](components/infra.md).

There is nothing to provision after the install: the published images are used as they are (the web pods forward `/api` to the admin API service, and the admin ingress also routes `/api` there directly), the API layer and control-plane certificates are issued from the vault when the pods start, and the admin API and hub pods fetch node-agent builds from the matching GitHub release on first use (cached per pod; `agentDownloads.persistentVolumeClaim` shares a cache, and the agent hub's NetworkPolicy allows HTTPS egress for it). The `gpu-deck-web` image needs no rebuild for your host names.

**GPU nodes in the same cluster.** `nodeAgent.enabled=true` runs the agent as a DaemonSet on nodes labelled `gpudeck.sur950.github.io/gpu-node=true`, with the NVIDIA runtime class, host networking and a host-path state directory. All pods share one multi-use enrolment token:

```bash
TOKEN=$(gpudeck -o json nodes join-tokens create --cluster-id <id> --max-uses 100 --ttl-minutes 43200 | jq -r .token)
kubectl -n gpu-deck create secret generic gpudeck-agent-join --from-literal=GPUDECK_AGENT_JOIN_TOKEN="$TOKEN"
helm upgrade gpu-deck oci://ghcr.io/sur950/charts/gpu-deck -n gpu-deck --reuse-values \
  --set nodeAgent.enabled=true --set nodeAgent.joinTokenSecret=gpudeck-agent-join
```

Each pod joins once under its node's host name and keeps its identity in the host path, so restarts do not use the token again. Revoke the token after the rollout and issue a new one before adding nodes (or keep a long-lived one with a high `max_uses`).

Engines on these nodes:

- **Native engines** (llama.cpp, whisper.cpp, stable-diffusion.cpp) work out of the box: the agent downloads the pinned build for the node's GPU into the state directory (the image carries `libgomp` and the Vulkan loader; the CUDA runtime comes with the pinned llama.cpp CUDA bundle and the driver from the NVIDIA runtime class).
- **Container engines** (vLLM, SGLang, TensorRT-LLM, vLLM-Omni, custom images) run as sibling containers through the node's container runtime when you opt in: `--set nodeAgent.containerRuntime.enabled=true --set nodeAgent.containerRuntime.socketGroupId=<gid of the socket>`. The chart mounts the node's Docker-compatible socket (`containerRuntime.socketPath`, default `/var/run/docker.sock`: Docker Engine with the NVIDIA Container Toolkit, or Podman's `podman.socket`), and the agent image carries the docker CLI. Model files are mounted into the engine containers by their host paths, which the chart keeps identical inside the pod. Mounting a runtime socket gives the agent root-equivalent access to the node, so it is off by default; nodes that run only containerd (no Docker-compatible API) cannot run container engines this way, so install the agent natively there.

## 7. Offline and air-gapped sites

On a connected machine build the bundle, then carry it to the site:

```bash
infra/offline/build-bundle.sh --version 1.0.0 [--with-data-services] [--with-observability]
```

It contains the images (control plane, web, agent, optional Postgres, Redis, Prometheus, Grafana), the Compose files, the Helm chart, the agent builds for each target, the CLI wheels and a Terraform provider mirror, with `SHA256SUMS`. On the site:

```bash
./install.sh verify
./install.sh load-images --registry registry.local:5000/gpu-deck
./install.sh compose --env /etc/gpu-deck/.env [--full] [--observability]     # or: ./install.sh helm --values my-values.yaml
./install.sh cli && ./install.sh terraform-mirror
sudo ./install-agent.sh --hub https://hub:8443 --token gdj_...              # on each GPU machine
```

`install.sh compose` mounts the bundle's agent builds into the admin API and the hub (`AGENT_DIST_DIR`, with `AGENT_RELEASE_URL` empty so nothing is fetched), so the one-line installer works on the site as it does online; with Helm, copy them into a PVC and set `agentDownloads.persistentVolumeClaim`, `config.AGENT_DIST_DIR=/app/agent-cache` and `config.AGENT_RELEASE_URL=""`. You can also copy `install-agent.sh` and `agent/` to every GPU machine. For models, point `HF_ENDPOINT` and `MODELSCOPE_ENDPOINT` at a site mirror (the control plane pushes them to every agent) or use local model files (Catalog, Add a model, scan a local path). The agent cannot download engine builds from GitHub or PyPI on an isolated network: install the engines your models need on each GPU machine ahead of time and list them in `engine_paths`, and load container-engine images (by the pinned tag and digest) into the machine's container runtime yourself. Egress rules are described in [security-model.md](security-model.md).

## 8. Upgrades

1. Read the release notes in [CHANGELOG.md](../CHANGELOG.md) for new settings, migrations and protocol changes, and diff your `.env` against the new `.env.example`.
2. Back up PostgreSQL and the `VAULT_KEYS` / `SECRET_KEY` (see [operations.md](operations.md) section 3).
3. **Control plane first.** Compose: `export GPUDECK_VERSION=<new>`, `$COMPOSE pull`, `$COMPOSE --profile tools run --rm migrate`, `$COMPOSE up -d`. Helm: `helm upgrade` with `--version <new>` (the pre-upgrade hook runs the migrations before pods roll). The API layer keeps serving from its in-memory routing table while the rest restarts.
4. **Then the agents**, node by node: re-run the install command with a fresh join token (the hub serves the new release's build, fetched from GitHub or from `AGENT_DIST_DIR` offline), or replace the binary and restart the service. Agents reconnect on their own and replay pending commands. A control plane release works with every agent of the same protocol major; a newer agent against an older hub is not supported. New engine pins arrive as new engine versions; switch a deployment to one when you are ready.
5. Verify `/api/v1/health/ready`, serving `/health` and that every node is online.

Downgrading across a migration is not supported: restore the backup. Versioning and compatibility rules: [RELEASING.md](RELEASING.md).

## 9. When something does not work

[operations.md](operations.md) section 8 has a symptom table. The three most common:

- A node stays `pending` or `offline`: the machine cannot reach `AGENT_HUB_PUBLIC_URL` on 8443, or its clock is wrong (TLS fails).
- Serving returns 502 `upstream_error`: the API layer cannot reach the agent proxy on 9443, or its client certificate was revoked (restart the API layer to have a new one issued) or `API_LAYER_CLIENT_CERT_PATH` points at a certificate from another CA.
- The install command fails with a checksum error on `ca.pem`: the machine does not reach the hub at `AGENT_HUB_PUBLIC_URL`, or something in between answers instead of it. "No agent build": the control plane cannot reach GitHub (set `AGENT_DIST_DIR` for offline sites).
- Serving returns 503 `model_not_ready`: the deployment has no ready instance yet; check the deployment's instance state and logs.
