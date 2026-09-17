# fief

Personal infrastructure estate. First holding: an outbound-only tunnel
hub that reaches a LAN behind a firewall and borrows unblocked internet
from a free cloud relay — no inbound ports, no kernel drivers.

## Holdings

* **fief-relay** — chisel hub on Render (free tier). Windows opens a
  reverse SOCKS (`R:socks`, exits into its LAN); Linux forwards it home
  (`1080`) and takes a second SOCKS off Render's own egress (`1081`).
  See `render.yaml` + `docs/RUNBOOK.md`.

## Layout

* `render.yaml` — hub as code (image + start command, secret via dashboard)
* `windows/run-chisel.ps1` — Windows client with reconnect loop
* `linux/chisel-forward.sh` — Linux forwards (`1080` LAN, `1081` egress)
* `docs/RUNBOOK.md` — start order, healthy logs, gotchas
* `image/` — hub image (Alpine + pinned chisel, env-driven entrypoint)

## Quickstart

```sh
cp .env.example .env   # put real CHISEL_AUTH in .env, never commit it
```

1. Windows: `powershell -ExecutionPolicy Bypass -File windows/run-chisel.ps1`
2. Linux: `CHISEL_AUTH='...' linux/chisel-forward.sh`
3. `proxychains xfreerdp /v:<windows-lan-ip> /u:<user>`
4. `ssh -p 2222 fief@127.0.0.1` — shell on the hub (needs `SSH_PUBKEY` set on the service)
