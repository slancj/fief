#!/bin/sh
# fief-relay entrypoint. Everything from env so the image holds no secrets.
# Render injects $PORT (default 10000); $CHISEL_AUTH is the dashboard secret.
set -e
: "${PORT:=10000}"
: "${CHISEL_AUTH:?CHISEL_AUTH env var is required (format user:secret)}"
exec /usr/local/bin/chisel server \
  --port "$PORT" \
  --reverse \
  --socks5 \
  --auth "$CHISEL_AUTH" \
  --keepalive 25s
