#!/bin/sh
# Minimal forward: exposes the hub's reverse-SOCKS (opened by Windows R:socks)
# on this machine's localhost for proxychains. No L: prefix — this chisel
# version takes bare local:remote forwards.
# Usage: CHISEL_AUTH='spider:...' ./chisel-forward.sh
set -e
: "${CHISEL_AUTH:?set CHISEL_AUTH, e.g. CHISEL_AUTH='spider:...' $0}"
: "${HUB_URL:=https://spider-chisel.onrender.com}"
: "${LOCAL_PORT:=1080}"
exec chisel client --auth "$CHISEL_AUTH" "$HUB_URL" "$LOCAL_PORT:127.0.0.1:1080"
