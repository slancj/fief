# fief runbook (minimal)

Hub: `fief-relay` web service, built from `image/` via Blueprint
(`render.yaml`). Alpine + pinned chisel 1.12.0 (checksum-verified);
`image/entrypoint.sh` reads `$PORT`/`$CHISEL_AUTH` and runs
`server --reverse --socks5`. The secret lives only in the dashboard
env var — never in the image or repo.

## Start order

1. Windows (behind FortiGate): `windows/run-chisel.ps1`.
   Expect `Connected`. Hub log shows a session with
   `R:127.0.0.1:1080=>socks: Listening`.
2. Linux: `CHISEL_AUTH='fief:...' linux/chisel-forward.sh`.
   Expect `tun: proxy#1080=>1080: Listening` then `Connected`.
3. Use it: `proxychains xfreerdp /v:<windows-lan-ip> /u:<user>`
   (or `proxychains curl http://<lan-ip>/` to smoke-test).
   `proxychains` must point at port `1080` for LAN exits.

## Unblocked internet via Render egress

The hub also runs `--socks5`, so a second local port exits through
Render's connection instead of Windows:
`curl -x socks5h://127.0.0.1:1081 ifconfig.me` shows a Render Oregon IP.
`linux/chisel-forward.sh` opens both (`1080` = LAN, `1081` = egress).
Point a second proxychains profile (or `curl -x`) at `1081` for browsing.

## Shell on the hub via chisel

The image also runs `sshd` (key-only, user `fief`, container port 2222)
when the `SSH_PUBKEY` env var is set on the service. `linux/chisel-forward.sh`
maps it to localhost:2222:

```sh
ssh -p 2222 fief@127.0.0.1
```

Host keys regenerate on every deploy, so expect a changed-host-key prompt
after each hub update. `SSH_PUBKEY` unset = sshd stays off, chisel-only.

## Notes

* Local forwards are bare `local:remote` — no `L:` prefix (it parses
  `L` as a hostname and fails with `cannot listen`).
* Free Render sleeps after ~15 min idle; first connect wakes it (~30s).
* `Stream error ... <nil>` lines when a client disconnects are benign.
* Secrets live in env vars / dashboard only. Rotate by changing the
  `CHISEL_AUTH` env var on the service and restarting (no rebuild needed).
