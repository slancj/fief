#!/bin/sh
# fief-relay entrypoint. Everything from env so the image holds no secrets.
# Render injects $PORT (default 10000); $CHISEL_AUTH is the dashboard secret.
set -e
: "${PORT:=10000}"
: "${CHISEL_AUTH:?CHISEL_AUTH env var is required (format user:secret)}"
: "${SSH_PORT:=2222}"

# sshd (key-only, user `fief`) so the hub itself is reachable over the
# tunnel via a `2222:127.0.0.1:2222` forward. Optional: skipped when
# SSH_PUBKEY is unset (chisel-only mode). Host keys are generated fresh
# at each boot, so accept-new on first connect after every redeploy.
if [ -n "${SSH_PUBKEY:-}" ]; then
  mkdir -p /run/sshd /home/fief/.ssh
  chmod 700 /home/fief/.ssh
  ssh-keygen -A >/dev/null 2>&1
  printf '%s\n' "$SSH_PUBKEY" > /home/fief/.ssh/authorized_keys
  chmod 600 /home/fief/.ssh/authorized_keys
  chown -R fief:fief /home/fief/.ssh
  /usr/sbin/sshd -p "$SSH_PORT" \
    -o ListenAddress=127.0.0.1 \
    -o PasswordAuthentication=no \
    -o PermitRootLogin=no \
    -o PubkeyAuthentication=yes
  echo "sshd on 127.0.0.1:$SSH_PORT (user fief, key-only)"
else
  echo "SSH_PUBKEY not set, sshd disabled"
fi

exec /usr/local/bin/chisel server \
  --port "$PORT" \
  --reverse \
  --socks5 \
  --auth "$CHISEL_AUTH" \
  --keepalive 25s
