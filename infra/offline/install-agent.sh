#!/usr/bin/env bash
# Install the GPU Deck node agent as a native service on a GPU machine (Linux systemd or macOS
# launchd) and join it to the control plane with a one-time token.
#
#   sudo ./install-agent.sh --hub https://gpudeck-hub.example.com:8443 --token gdj_... \
#        --ca-fingerprint SHA256 [--name gpu-a1] [--label zone=a] [--agent-dir ./agent]
#        [--egress-lock auto|none] [--serving-mode tunnel|direct] [--no-firewall]
#        [--insecure-skip-ca-pin]
#
# Serving mode tunnel (default): the machine opens no inbound port, the agent dials out to the
# API layer. The host firewall (nftables / pf) for the agent's ports is applied and verified
# unless --no-firewall; the ports sshd listens on are always kept open.
#
# Uses agent/gpudeck-agent-<version>-<target>.tar.gz from the offline bundle (or --tarball).
# Packaging from the node-agent tarball (packaging/ inside the tarball) is used when present;
# otherwise a hardened default unit is written. The agent runs as its own user with access only
# to GPU devices (video/render groups) and its state/cache directories.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$HERE/agent"
HUB="" TOKEN="" CA_FP="" NAME="" TARBALL="" EGRESS="auto" MODE="tunnel" FIREWALL=1 INSECURE=0
LABELS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --hub) HUB="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --ca-fingerprint) CA_FP="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --label) LABELS+=("$2"); shift 2 ;;
    --agent-dir) AGENT_DIR="$2"; shift 2 ;;
    --tarball) TARBALL="$2"; shift 2 ;;
    --egress-lock) EGRESS="$2"; shift 2 ;;
    --serving-mode) MODE="$2"; shift 2 ;;
    --no-firewall) FIREWALL=0; shift ;;
    --insecure-skip-ca-pin) INSECURE=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$HUB" && -n "$TOKEN" ]] || { echo "--hub and --token are required" >&2; exit 2; }
[[ -n "$CA_FP" || "$INSECURE" == 1 ]] || {
  echo "--ca-fingerprint is required (pins the control plane's CA; --insecure-skip-ca-pin to skip)" >&2
  exit 2
}
[[ "$MODE" == tunnel || "$MODE" == direct ]] || { echo "--serving-mode: tunnel or direct" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "run as root (installs a system service)" >&2; exit 1; }

OS="$(uname -s)"
ARCH="$(uname -m)"; [[ "$ARCH" == "arm64" ]] && ARCH=aarch64
case "$OS" in
  Linux)
    if ldd --version 2>&1 | grep -qi musl; then TRIPLE="$ARCH-unknown-linux-musl"; else TRIPLE="$ARCH-unknown-linux-gnu"; fi
    STATE_DIR=/var/lib/gpudeck ;;
  Darwin)
    TRIPLE="$ARCH-apple-darwin"
    STATE_DIR=/usr/local/var/gpudeck ;;
  *) echo "unsupported OS $OS" >&2; exit 1 ;;
esac

if [[ -z "$TARBALL" ]]; then
  TARBALL="$(ls "$AGENT_DIR"/gpudeck-agent-*-"$TRIPLE".tar.gz 2>/dev/null | head -1 || true)"
  if [[ -z "$TARBALL" && "$TRIPLE" == *-linux-gnu ]]; then
    TARBALL="$(ls "$AGENT_DIR"/gpudeck-agent-*-"$ARCH"-unknown-linux-musl.tar.gz 2>/dev/null | head -1 || true)"
  fi
fi
[[ -f "$TARBALL" ]] || { echo "no agent tarball for $TRIPLE in $AGENT_DIR" >&2; exit 1; }
if [[ -f "$AGENT_DIR/SHA256SUMS" ]]; then
  (cd "$(dirname "$TARBALL")" && grep " $(basename "$TARBALL")\$" SHA256SUMS | \
    { if command -v sha256sum >/dev/null; then sha256sum -c --quiet; else shasum -a 256 -c --quiet; fi; })
fi

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
tar -C "$WORK" -xzf "$TARBALL"
install -m 0755 "$WORK/gpudeck-agent" /usr/local/bin/gpudeck-agent
echo "installed /usr/local/bin/gpudeck-agent ($(/usr/local/bin/gpudeck-agent --version 2>/dev/null || echo unknown))"

# ----- user and directories -------------------------------------------------------------------
if [[ "$OS" == Linux ]]; then
  id gpudeck >/dev/null 2>&1 || useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin gpudeck
  for g in video render; do getent group "$g" >/dev/null && usermod -aG "$g" gpudeck; done
  install -d -o gpudeck -g gpudeck -m 0750 "$STATE_DIR" "$STATE_DIR/cache"
  install -d -o gpudeck -g gpudeck -m 0700 "$STATE_DIR/tmp"
  RUN_AS=(sudo -u gpudeck)
else
  # launchd daemons run as root on macOS; IOKit/Metal statistics need the console session's GPU.
  install -d -m 0750 "$STATE_DIR" "$STATE_DIR/cache"
  RUN_AS=()
fi

# ----- join -----------------------------------------------------------------------------------
JOIN=(/usr/local/bin/gpudeck-agent join --token "$TOKEN" --url "$HUB" --state-dir "$STATE_DIR")
[[ -n "$CA_FP" ]] && JOIN+=(--ca-fingerprint "$CA_FP")
[[ "$INSECURE" == 1 ]] && JOIN+=(--insecure-skip-ca-pin)
JOIN+=(--serving-mode "$MODE")
[[ "$FIREWALL" == 1 ]] || JOIN+=(--no-firewall)
[[ -n "$NAME" ]] && JOIN+=(--name "$NAME")
for l in "${LABELS[@]+"${LABELS[@]}"}"; do JOIN+=(--label "$l"); done
"${RUN_AS[@]+"${RUN_AS[@]}"}" "${JOIN[@]}"

RUN_ARGS="run"
[[ "$EGRESS" != none ]] && RUN_ARGS="run --apply-egress-lock"

# ----- host firewall (agent ports only; sshd ports and this session's port stay open) --------
if [[ "$FIREWALL" == 1 ]]; then
  if [[ "$OS" == Linux ]] && ! command -v nft >/dev/null 2>&1; then
    echo "host firewall: nftables (nft) is not installed; skipped (tunnel mode opens no port)" >&2
  else
    SSH_PORT="$(echo "${SSH_CONNECTION:-}" | awk '{print $4}')"
    /usr/local/bin/gpudeck-agent firewall apply ${SSH_PORT:+--preserve-port "$SSH_PORT"}
    /usr/local/bin/gpudeck-agent firewall status >/dev/null \
      || { echo "host firewall verification failed (gpudeck-agent firewall status)" >&2; exit 1; }
  fi
fi

# ----- service --------------------------------------------------------------------------------
if [[ "$OS" == Linux ]]; then
  if [[ -f "$WORK/packaging/systemd/gpudeck-agent.service" ]]; then
    install -m 0644 "$WORK/packaging/systemd/gpudeck-agent.service" /etc/systemd/system/gpudeck-agent.service
  else
    cat > /etc/systemd/system/gpudeck-agent.service <<UNIT
[Unit]
Description=GPU Deck node agent
After=network-online.target
Wants=network-online.target

[Service]
User=gpudeck
Group=gpudeck
SupplementaryGroups=video render
Environment=GPUDECK_AGENT_STATE_DIR=$STATE_DIR
ExecStart=/usr/local/bin/gpudeck-agent $RUN_ARGS
Restart=always
RestartSec=5
# Engines outlive agent restarts / upgrades (re-adopted on start); only the agent stops.
KillMode=process
LimitNOFILE=65536
# Egress lock (nftables) needs CAP_NET_ADMIN; nothing else is granted.
AmbientCapabilities=CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_ADMIN
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$STATE_DIR
# Writable /tmp for engines that outlives agent restarts (PrivateTmp is wiped on restart).
BindPaths=$STATE_DIR/tmp:/tmp
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
UNIT
  fi
  systemctl daemon-reload
  systemctl enable --now gpudeck-agent.service
  systemctl --no-pager status gpudeck-agent.service | head -5 || true
  # Self-update helper (agent protocol 14): a root path unit that applies staged,
  # signature-verified releases when the control plane updates this node.
  GPUDECK_AGENT_STATE_DIR="$STATE_DIR" /usr/local/bin/gpudeck-agent update install-helper \
    || echo "update helper not installed: update this agent by re-running the installer" >&2
else
  PLIST=/Library/LaunchDaemons/io.github.sur950.gpudeck-agent.plist
  if [[ -f "$WORK/packaging/launchd/io.github.sur950.gpudeck-agent.plist" ]]; then
    install -m 0644 "$WORK/packaging/launchd/io.github.sur950.gpudeck-agent.plist" "$PLIST"
  else
    ARGS_XML=""
    for a in $RUN_ARGS; do ARGS_XML+="    <string>$a</string>"$'\n'; done
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>io.github.sur950.gpudeck-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/gpudeck-agent</string>
$ARGS_XML  </array>
  <key>EnvironmentVariables</key>
  <dict><key>GPUDECK_AGENT_STATE_DIR</key><string>$STATE_DIR</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>AbandonProcessGroup</key><true/>
  <key>StandardOutPath</key><string>/usr/local/var/log/gpudeck-agent.log</string>
  <key>StandardErrorPath</key><string>/usr/local/var/log/gpudeck-agent.log</string>
</dict>
</plist>
PLIST
  fi
  mkdir -p /usr/local/var/log
  launchctl bootout system "$PLIST" 2>/dev/null || true
  launchctl bootstrap system "$PLIST"
  echo "loaded $PLIST"
  GPUDECK_AGENT_STATE_DIR="$STATE_DIR" /usr/local/bin/gpudeck-agent update install-helper \
    --restart launchd:system/io.github.sur950.gpudeck-agent \
    || echo "update helper not installed: update this agent by re-running the installer" >&2
fi
echo "node agent installed and joined to $HUB"
