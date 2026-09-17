#!/bin/sh
# Minimal forwards over one client connection:
#   localhost:$LOCAL_PORT  -> hub's reverse-SOCKS (exits via Windows LAN)
#   localhost:$EGRESS_PORT -> hub's server-SOCKS (exits via Render internet)
# No L: prefix — this chisel version takes bare local:remote forwards.
# Usage: CHISEL_AUTH='spider:...' ./chisel-forward.sh
set -e
: "${CHISEL_AUTH:?set CHISEL_AUTH, e.g. CHISEL_AUTH='spider:...' $0}"
: "${HUB_URL:=https://spider-chisel.onrender.com}"
: "${LOCAL_PORT:=1080}"
: "${EGRESS_PORT:=1081}"
exec chisel client --auth "$CHISEL_AUTH" "$HUB_URL" \
  "$LOCAL_PORT:127.0.0.1:1080" \
  "$EGRESS_PORT:socks"
