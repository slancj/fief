# Spider runbook (minimal)

Hub: `https://spider-chisel.onrender.com` = prebuilt
`docker.io/jpillora/chisel:1.12.1` running
`/ko-app/chisel server --port 10000 --reverse --auth <secret> --keepalive 25s`
(see `render.yaml`). The repo `Dockerfile` is NOT what runs — it is the
leftover generic hub image from the Hugging Face attempt.

## Start order

1. Windows (behind FortiGate): `windows/run-chisel.ps1`.
   Expect `Connected`. Hub log shows a session with
   `R:127.0.0.1:1080=>socks: Listening`.
2. Linux: `CHISEL_AUTH='spider:...' linux/chisel-forward.sh`.
   Expect `tun: proxy#1080=>1080: Listening` then `Connected`.
3. Use it: `proxychains xfreerdp /v:<windows-lan-ip> /u:<user>`
   (or `proxychains curl http://<lan-ip>/` to smoke-test).

## Notes

* Local forwards are bare `local:remote` — no `L:` prefix (it parses
  `L` as a hostname and fails with `cannot listen`).
* Free Render sleeps after ~15 min idle; first connect wakes it (~30s).
* `Stream error ... <nil>` lines when a client disconnects are benign.
* Secrets live in env vars / dashboard only. Rotate the auth by setting
  a new `dockerCommand` + `CHISEL_AUTH` on the service and redeploying.
