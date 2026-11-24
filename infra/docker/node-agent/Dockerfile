# syntax=docker/dockerfile:1.7
# GPU Deck node agent image for the Kubernetes DaemonSet (infra/helm/gpu-deck, nodeAgent.*).
# Bare-metal Linux and macOS install the native binary as a service instead (no Docker layer).
#
# Build context: apps/node-agent
#   docker build -f infra/docker/node-agent/Dockerfile -t gpu-deck-node-agent:local apps/node-agent
#
# Vendor libraries are not baked in: NVML (libnvidia-ml.so) is injected by the NVIDIA container
# toolkit (runtimeClassName: nvidia); AMD/other SMI tools come from the host or a vendor image
# layered on top (ARG RUNTIME_IMAGE).
#
# Engines on Kubernetes:
# - native: the agent downloads the pinned llama.cpp / whisper.cpp / stable-diffusion.cpp builds
#   into its state directory (a hostPath) and runs them inside this container, so this image's
#   user space is what they link against: Ubuntu 24.04 (glibc 2.39, libstdc++ GLIBCXX_3.4.33)
#   runs every pinned build, including the llama.cpp CUDA / ROCm / arm64 ones that need
#   glibc >= 2.38 (a Debian 12 base, glibc 2.36, cannot). The runtime libraries they need
#   (libgomp, the Vulkan loader) are installed here, CUDA comes from the pinned cudart bundle,
#   the driver from the NVIDIA runtime class.
# - container (vLLM, SGLang, TensorRT-LLM, vLLM-Omni, custom): opt in with
#   nodeAgent.containerRuntime in the Helm chart, which mounts the node's Docker-compatible
#   socket (Docker Engine, or Podman's API socket); this image carries the docker CLI for it.
ARG RUST_IMAGE=rust:1.91.1-bookworm
ARG RUNTIME_IMAGE=ubuntu:noble-20261110.1
# Static docker CLI (official image, pinned by digest).
ARG DOCKER_CLI_IMAGE=docker:29.10.0-cli@sha256:34507eb5069d8be82c95dd3b6987d4250fd5fee49b82e2509b7d7a7030740d6d

FROM ${DOCKER_CLI_IMAGE} AS dockercli

FROM ${RUST_IMAGE} AS build
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    cargo build --release --locked -p gpudeck-agent --bin gpudeck-agent && \
    install -m 0755 target/release/gpudeck-agent /usr/local/bin/gpudeck-agent

FROM ${RUNTIME_IMAGE} AS runtime
LABEL org.opencontainers.image.title="gpu-deck-agent" \
      org.opencontainers.image.description="GPU Deck node agent" \
      org.opencontainers.image.vendor="Suresh Konakanchi" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/sur950/gpu-deck"
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates nftables procps tini \
        libgomp1 libvulkan1 tar gzip unzip && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -g 10001 gpudeck && \
    useradd -u 10001 -g gpudeck -G video -d /var/lib/gpudeck -s /usr/sbin/nologin -M gpudeck
COPY --from=build /usr/local/bin/gpudeck-agent /usr/local/bin/gpudeck-agent
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
ENV GPUDECK_AGENT_STATE_DIR=/var/lib/gpudeck \
    GPUDECK_AGENT_CACHE_DIR=/var/lib/gpudeck/cache \
    DOCKER_CONFIG=/var/lib/gpudeck/.docker
USER 10001:10001
EXPOSE 9443
# tini is PID 1: it reaps every orphan (llama.cpp router children after a router restart would
# otherwise stay <defunct>) and forwards signals. Started without it (an entrypoint override),
# the agent detects it is PID 1 and does the same itself (GPUDECK_AGENT_INIT).
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/gpudeck-agent"]
CMD ["run"]
