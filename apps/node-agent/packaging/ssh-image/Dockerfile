# GPU Deck SSH GPU-instance image (libs/contracts/agent-protocol/protocol.md §8).
#
# Ubuntu 24.04 (glibc): the NVIDIA container runtime mounts the host's glibc `nvidia-smi` and
# driver libraries into the container, which cannot run on a musl (Alpine) base. OpenSSH listens
# on 2222 (GPU_INSTANCE_DEFAULT_CONTAINER_PORT); key login only, for the user the agent names
# (GPUDECK_SSH_USER / USER_NAME), keys from /etc/gpudeck/authorized_keys (mounted read-only by
# the agent) or PUBLIC_KEY. Runs under the agent's hardening (--cap-drop ALL + a minimal set,
# no-new-privileges): no sudo, no setuid helpers.
#
# Published by .github/workflows/release.yml as ghcr.io/<owner>/gpu-deck-ssh:<version>.
# Local build: docker build -t gpu-deck-ssh:dev apps/node-agent/packaging/ssh-image
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get -y upgrade \
    && apt-get install -y --no-install-recommends \
        openssh-server tini ca-certificates curl less nano procps python3 python3-venv \
        git rsync tmux \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/ssh/ssh_host_* /etc/update-motd.d/* \
    && mkdir -p /run/sshd /etc/ssh/authorized_keys.d \
    # The image's default `ubuntu` user (uid 1000) is replaced by the user the agent names.
    && (userdel -r ubuntu 2>/dev/null || true)

COPY sshd_config /etc/ssh/sshd_config.d/10-gpudeck.conf
COPY entrypoint.sh /usr/local/bin/gpudeck-ssh-entrypoint
RUN chmod 0755 /usr/local/bin/gpudeck-ssh-entrypoint && chmod 0644 /etc/ssh/sshd_config.d/10-gpudeck.conf

LABEL org.opencontainers.image.title="gpu-deck-ssh" \
      org.opencontainers.image.description="GPU Deck SSH GPU instance (Ubuntu 24.04 + OpenSSH, glibc for the NVIDIA runtime)" \
      org.opencontainers.image.licenses="Apache-2.0"

EXPOSE 2222
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/gpudeck-ssh-entrypoint"]
