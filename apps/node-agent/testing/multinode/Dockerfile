# Test node for multi-node runs without GPU hardware (see docs/TESTING.md).
# Ubuntu 24.04 + systemd so the generated install script runs unchanged; the
# official llama.cpp Linux builds need glibc >= 2.38, libgomp1 and libvulkan1.
# The fake nvidia-smi reports one simulated NVIDIA GPU (FAKE_GPU_MEM_MIB, default
# 8192; memory.used = resident memory of the engines), so the control plane can
# schedule onto the node while llama.cpp really runs on the CPU.
#
#   docker build -t gpudeck-testnode apps/node-agent/testing/multinode
#   docker run -d --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
#     --tmpfs /run --tmpfs /run/lock -v <models>:<models>:ro gpudeck-testnode
#   docker exec <node> sh -c '<install command from the Join dialog>'
FROM ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      systemd systemd-sysv curl ca-certificates nftables procps iproute2 libgomp1 libvulkan1 \
 && rm -rf /var/lib/apt/lists/*
COPY fake-nvidia-smi /usr/local/bin/nvidia-smi
CMD ["/lib/systemd/systemd"]
