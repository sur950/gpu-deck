#!/usr/bin/env bash
# Security verification of the node agent's defaults (docs/security-model.md §5): two GPU
# machines in Linux containers (one in serving mode tunnel, one direct) joined to a running local
# control plane with the real install command, an attacker container and an "insider" container
# (whose address the direct node allow-lists) on the same Docker network.
#
#   apps/node-agent/testing/security/run.sh            # build, run every check, clean up
#   KEEP=1 apps/node-agent/testing/security/run.sh     # leave containers / test objects running
#
# Needs: Docker, a local control plane (./infra/scripts/bootstrap.dev.sh bootstrap) reachable at
# localhost:8180 / 8181 / 8443 / 8444, the backend venv, Ollama GGUF blobs in ~/.ollama/models
# (used in place, nothing is downloaded). Everything it creates is removed at the end: containers,
# network, images, build volumes, the test cluster / nodes / models / API key, the staged agent
# build and the temporary directory.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
HERE="$REPO/apps/node-agent/testing/security"
WORK="${TMPDIR:-/tmp}/gpudeck-sectest"
NET=gpudeck-sectest
SUBNET=172.30.99
ARCH="$(docker info --format '{{.Architecture}}' | sed 's/arm64/aarch64/')"
TARGET="${ARCH}-unknown-linux-gnu"
DIST="$REPO/apps/node-agent/dist/agent/1.0.0/$TARGET"
PY="$REPO/apps/fastapi/venv/bin/python"
export SECTEST_STATE="$WORK/state.json"
drive() { (cd "$REPO/apps/fastapi" && "$PY" "$HERE/drive.py" "$@" 2>/dev/null); }

cleanup() {
  [[ "${KEEP:-0}" == 1 ]] && { echo "KEEP=1: left running"; return; }
  drive cleanup >/dev/null || true
  docker rm -f gpudeck-sectest-node-t gpudeck-sectest-node-d gpudeck-sectest-attacker \
    gpudeck-sectest-insider >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker image rm gpudeck-sectest-node gpudeck-sectest-attacker >/dev/null 2>&1 || true
  docker volume rm gpudeck-sectest-cargo gpudeck-sectest-target >/dev/null 2>&1 || true
  rm -f "$DIST/gpudeck-agent" "$DIST/gpudeck-agent.sha256"
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "==> agent build for $TARGET (rust:1-bookworm)"
mkdir -p "$WORK/bin" "$WORK/ctx"
docker run --rm -v "$REPO/apps/node-agent:/src:ro" -v gpudeck-sectest-cargo:/usr/local/cargo/registry \
  -v gpudeck-sectest-target:/target -v "$WORK/bin:/out" -w /src rust:1-bookworm \
  sh -c 'cargo build -q --release --locked -p gpudeck-agent --bins --target-dir /target &&
         cp /target/release/gpudeck-agent /target/release/gpudeck-fake-engine /out/'
mkdir -p "$DIST"
cp "$WORK/bin/gpudeck-agent" "$DIST/"
(cd "$DIST" && shasum -a 256 gpudeck-agent > gpudeck-agent.sha256)

echo "==> images, network, containers"
cp "$HERE/Dockerfile.node" "$HERE/Dockerfile.attacker" "$HERE/gpudeck-fwd@.service" \
  "$HERE/../multinode/fake-nvidia-smi" "$WORK/bin/gpudeck-fake-engine" "$WORK/ctx/"
docker build -q -t gpudeck-sectest-node -f "$WORK/ctx/Dockerfile.node" "$WORK/ctx" >/dev/null
docker build -q -t gpudeck-sectest-attacker -f "$WORK/ctx/Dockerfile.attacker" "$WORK/ctx" >/dev/null
docker network create --subnet "$SUBNET.0/24" "$NET" >/dev/null
for n in t:10 d:20; do
  docker run -d --name "gpudeck-sectest-node-${n%%:*}" --hostname "sectest-${n%%:*}" --network "$NET" \
    --ip "$SUBNET.${n##*:}" --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    --tmpfs /run --tmpfs /run/lock -v "$HOME/.ollama/models:/models:ro" gpudeck-sectest-node >/dev/null
done
docker run -d --name gpudeck-sectest-attacker --network "$NET" --ip "$SUBNET.66" gpudeck-sectest-attacker >/dev/null
docker run -d --name gpudeck-sectest-insider --network "$NET" --ip "$SUBNET.60" gpudeck-sectest-attacker >/dev/null
sleep 6

echo "==> control plane: cluster (direct mode allows the Docker host NAT address + the insider)"
# OrbStack NATs host -> container traffic from the network address (.0); adjust for other runtimes.
drive setup "$SUBNET.0/32,$SUBNET.60/32" >/dev/null
tok() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['tokens'][sys.argv[2]][sys.argv[3]])" "$SECTEST_STATE" "$1" "$2"; }
docker exec gpudeck-sectest-node-t sh -c "$(tok tunnel install_command)" | tail -3
docker exec gpudeck-sectest-node-d sh -c "$(tok direct install_command | sed 's/install.sh/install.sh?mode=direct/')" | tail -3
drive wait-nodes
drive deploy sectest-t /models sectest-tunnel-model
drive deploy sectest-d /models sectest-direct-model
drive api-key >/dev/null

echo "==> the real API layer serves both nodes"
drive chat sectest-tunnel-model 30
drive chat sectest-direct-model 30
drive health

echo "==> attacks"
drive issue-service-cert "$WORK/certs" >/dev/null
docker cp "$WORK/certs" gpudeck-sectest-attacker:/certs
docker exec -w /certs gpudeck-sectest-attacker sh -c '
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 2 -subj "/CN=api-layer/O=gpudeck-services" -keyout self.key -out self.crt 2>/dev/null
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 2 -subj "/CN=Evil CA" -addext basicConstraints=critical,CA:TRUE -keyout evilca.key -out evilca.crt 2>/dev/null
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=api-layer/O=gpudeck-services" -keyout evil.key -out evil.csr 2>/dev/null
  printf "extendedKeyUsage=clientAuth,serverAuth\nsubjectAltName=DNS:localhost\n" > ext.cnf
  openssl x509 -req -in evil.csr -CA evilca.crt -CAkey evilca.key -CAcreateserial -days 2 -extfile ext.cnf -out evil.crt 2>/dev/null'
docker cp gpudeck-sectest-attacker:/certs "$WORK/certs-all"
docker cp "$WORK/certs-all" gpudeck-sectest-insider:/certs
for c in attacker insider; do docker cp "$HERE/attack.sh" "gpudeck-sectest-$c:/attack.sh"; done
docker exec gpudeck-sectest-attacker /attack.sh scan "$SUBNET.10" "$SUBNET.20"
docker exec gpudeck-sectest-insider /attack.sh tls "$SUBNET.20"
docker exec gpudeck-sectest-attacker /attack.sh replay "$(tok tunnel token)"
docker exec gpudeck-sectest-attacker /attack.sh cptunnel
docker exec gpudeck-sectest-insider /attack.sh ratelimit "$SUBNET.20"
for n in t d; do docker exec "gpudeck-sectest-node-$n" gpudeck-agent --config /etc/gpudeck/agent.toml firewall remove >/dev/null; done
docker exec gpudeck-sectest-attacker /attack.sh precheck "$SUBNET.20"
for n in t d; do docker exec "gpudeck-sectest-node-$n" gpudeck-agent --config /etc/gpudeck/agent.toml firewall apply >/dev/null; done

echo "==> spoofed control plane (the node's forwarder pointed at the attacker)"
spoof() {  # port cert label
  docker exec -d gpudeck-sectest-attacker sh -c "timeout 30 openssl s_server -www -accept $4 -tls1_3 -alpn h2 -cert /certs/$2.crt -key /certs/$2.key >/dev/null 2>&1 </dev/null"
  sleep 1
  local since; since="$(docker exec gpudeck-sectest-node-t date -u '+%Y-%m-%d %H:%M:%S')"
  docker exec gpudeck-sectest-node-t sh -c "echo $SUBNET.66:$4 > /etc/gpudeck-fwd-$1 && systemctl restart gpudeck-fwd@$1"
  sleep 20
  local why; why="$(docker exec gpudeck-sectest-node-t sh -c "journalctl -u gpudeck-agent --no-pager -o cat --since '$since'" \
    | sed 's/\x1b\[[0-9;]*m//g' | grep -oE 'not pinned by the security policy|UnknownIssuer|CaUsedAsEndEntity' | sort -u | tr '\n' ' ')"
  [[ -n "$why" ]] && printf 'PASS %-58s -- refused: %s\n' "$3" "$why" || printf 'FAIL %-58s -- not refused\n' "$3"
  docker exec gpudeck-sectest-node-t sh -c "rm -f /etc/gpudeck-fwd-$1 && systemctl restart gpudeck-fwd@$1"
  sleep 3
}
spoof 8444 self "tunnel endpoint spoofed: self-signed" 18444
spoof 8444 evil "tunnel endpoint spoofed: another CA" 28444
spoof 8444 fakehub "tunnel endpoint spoofed: our CA, unpinned key" 38444
spoof 8443 fakehub "hub spoofed: our CA, hub cert for localhost, unpinned key" 18443

echo "==> audits"
for n in t d; do docker exec "gpudeck-sectest-node-$n" gpudeck-agent --config /etc/gpudeck/agent.toml security audit; done
