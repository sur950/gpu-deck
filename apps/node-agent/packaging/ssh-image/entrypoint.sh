#!/bin/sh
# GPU Deck SSH instance entrypoint: create the login user, install its keys (root-owned copy so
# sshd StrictModes accepts them whatever uid owns the agent's mounted file), export the GPU
# environment to login sessions, fresh host keys, then sshd in the foreground.
set -eu

USER_NAME="${GPUDECK_SSH_USER:-${USER_NAME:-gpu}}"
case "$USER_NAME" in
  root|"") echo "refusing ssh user '$USER_NAME'" >&2; exit 64 ;;
esac
if ! printf '%s' "$USER_NAME" | grep -Eq '^[a-z_][a-z0-9_-]{0,31}$'; then
  echo "invalid ssh user name: $USER_NAME" >&2
  exit 64
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --uid "${GPUDECK_SSH_UID:-1000}" "$USER_NAME" 2>/dev/null \
    || useradd --create-home --shell /bin/bash "$USER_NAME"
fi
# No usable password, but not locked ('!'): key login only.
usermod -p '*' "$USER_NAME"

# GPU device groups: whatever groups own the passed-through device nodes.
for dev in /dev/nvidia* /dev/kfd /dev/dri/renderD* /dev/mtgpu* /dev/mxcd*; do
  [ -e "$dev" ] || continue
  gid="$(stat -c %g "$dev")"
  [ "$gid" = "0" ] && continue
  grp="$(getent group "$gid" | cut -d: -f1 || true)"
  if [ -z "$grp" ]; then
    grp="gpu$gid"
    groupadd --gid "$gid" "$grp" 2>/dev/null || true
  fi
  usermod -aG "$grp" "$USER_NAME" 2>/dev/null || true
done

KEYS_SRC="${GPUDECK_AUTHORIZED_KEYS_FILE:-${PUBLIC_KEY_FILE:-/etc/gpudeck/authorized_keys}}"
KEYS_DST="/etc/ssh/authorized_keys.d/$USER_NAME"
umask 022
: > "$KEYS_DST"
if [ -r "$KEYS_SRC" ]; then
  cat "$KEYS_SRC" >> "$KEYS_DST"
fi
if [ -n "${PUBLIC_KEY:-}" ]; then
  printf '%s\n' "$PUBLIC_KEY" >> "$KEYS_DST"
fi
sort -u "$KEYS_DST" -o "$KEYS_DST"
chown root:root "$KEYS_DST"
chmod 0644 "$KEYS_DST"
if ! grep -q . "$KEYS_DST"; then
  echo "no authorized keys for $USER_NAME" >&2
  exit 65
fi

# sshd starts sessions with a clean environment: hand the GPU settings the agent passed
# (device visibility, MPS VRAM / compute limits, GPU Deck limits) to every login.
ENV_FILE=/etc/profile.d/gpudeck-gpu.sh
GPU_ENV='^(CUDA_|NVIDIA_|HIP_|ROCR_|HSA_|GPUDECK_(VRAM|COMPUTE|DEVICE))'
# `export -p` quotes the values for the shell; pam_env (/etc/environment) takes KEY=value.
export -p | sed -n "s/^export //p" | grep -E "$GPU_ENV" | sed 's/^/export /' > "$ENV_FILE" || true
env | grep -E "$GPU_ENV" >> /etc/environment || true
chmod 0644 "$ENV_FILE"

ssh-keygen -A >/dev/null
exec /usr/sbin/sshd -D -e
