# fief runbook (minimal)

Hub: `fief hub` — chisel `server --reverse --socks5` on `$PORT` (default
8080; Render injects its own, HF shim uses 7860). Normal browser HTTP on the
same port is proxied via `--backend` to the status UI (Gradio when installed,
stdlib page otherwise). `$CHISEL_AUTH` is the only required secret — env var
/ dashboard / Space Secrets, never in the image or repo.

## Start order

1. Hub — one of:
   * Render Blueprint (`render.yaml`), *or*
   * HF Space (CI-synced `hf-space/`), *or*
   * `docker compose --profile hub up -d`, *or*
   * `uv run fief hub` on any Linux box.
2. Exit node (a Linux box on the LAN, behind the firewall):
   `CHISEL_AUTH='user:...' fief exit`.
   Expect `Connected`. Hub log shows a session with
   `R:127.0.0.1:1080=>socks: Listening`.
3. Consumer: `CHISEL_AUTH='user:...' fief forward`.
   Expect `tun: proxy#1080=>1080: Listening` then `Connected`.
   (`--no-ssh` on hubs without `SSH_PUBKEY` set.)
4. Use it: `proxychains xfreerdp /v:<lan-ip> /u:<user>`
   (or `proxychains curl http://<lan-ip>/` to smoke-test).
   `proxychains` must point at port `1080` for LAN exits.

## Unblocked internet via hub egress

The hub also runs `--socks5`, so a second local port exits through the
hub's connection instead of the LAN:
`curl -x socks5h://127.0.0.1:1081 ifconfig.me` shows the hub's IP.
`fief forward` opens both (`1080` = LAN, `1081` = egress).
Point a second proxychains profile (or `curl -x`) at `1081` for browsing.

## Shell on the hub via chisel

Any hub runs `sshd` (key-only, localhost-only, container port 2222) when
`SSH_PUBKEY` is set. `fief forward` maps it to localhost:2222 (drop it
with `--no-ssh` on hubs without `SSH_PUBKEY`):

```sh
ssh -p 2222 fief@127.0.0.1    # Docker hubs (Render, Pi/compose): user fief
ssh -p 2222 user@127.0.0.1    # HF Space: container user, not fief
```

Non-root hubs (HF, uid 1000) serve the container user — sshd can't setuid
without root. Set `SSH_USER` to change the name on root-run (Docker) hubs.

Host keys regenerate on every deploy, so expect a changed-host-key prompt
after each hub update. `SSH_PUBKEY` unset = sshd stays off, chisel-only.

## Platform notes

* **Render free** sleeps after ~15 min idle; first connect wakes it (~30s).
* **HF Space**: keep private; free Spaces sleep when idle; client
  `--keepalive 25s` usually keeps the websocket alive. sshd works here too
  (uid 1000, serves the `user` account) once `SSH_PUBKEY` is set in Secrets.
* **Pi/compose**: `restart: unless-stopped`; image from GHCR release tags
  (`v*` → amd64+arm64) or local `docker compose build`.
* Local forwards are bare `local:remote` — no `L:` prefix (chisel parses
  `L` as a hostname and fails with `cannot listen`).
* `Stream error ... <nil>` lines when a client disconnects are benign.
* Secrets live in env vars / dashboards only. Rotate by changing
  `CHISEL_AUTH` and restarting (no rebuild needed).
