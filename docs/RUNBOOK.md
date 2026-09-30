# fief runbook (minimal)

## One place: config/ + fan-out (read this first)

All config lives in git: `config/nodes.toml` (plaintext topology per node)
+ `config/secrets.yaml` (**SOPS+age encrypted** — opaque blob, safe to
commit). Nothing secret lives in dashboards, `.env` files, or chat.

* **Edit**: `sops config/secrets.yaml` (needs age key; bootstrap below).
* **Rotate**: edit → commit → push. CI fans out to HF Space + Render in
  ~1 min (Render redeploys so env applies; Space restarts). Hosts
  (Pi/laptop): `git pull`, then `fief config export --node <name> > .env`,
  restart services.
* **Dry run**: Actions → `fanout` → Run workflow with dry_run (prints
  keys/targets, never values).
* **Root secrets** (the only manual ones, they authenticate the fan-out
  itself): GitHub `SOPS_AGE_KEY`, `HF_TOKEN`, `RENDER_API_KEY`
  (+ `HF_SPACE_ID`, `RENDER_SERVICE_ID` variables). Age private key also
  lives at `~/.config/fief/age.key` on hosts. Tailscale key creation and
  route/exit approvals stay in the Tailscale console.

Bootstrap (once): `age-keygen` → public key into `.sops.yaml`
(replacing the placeholder) → private key to GitHub `SOPS_AGE_KEY` + host
key files → `cp config/secrets.example.yaml config/secrets.yaml`, fill in,
`sops config/secrets.yaml` (encrypts in place), commit, push.
Fan-out refuses plaintext (sops-envelope guard) and gitleaks scans history.

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

## Tailnet (all nodes join)

Every fief node can be a tailnet node: hubs (Render/HF/Docker) via the
`fief hub` sidecar, Pi/any Linux box via `fief tail up`. Dedicated
userspace `tailscaled` everywhere — no root, no TUN, no system changes.

1. Admin console → Settings → Keys: create a **reusable, tagged** auth key.
   Cloud nodes (HF/Render, ephemeral disks, fresh identity each boot) MUST
   use an **Ephemeral** key or dead entries pile up — ephemerality comes
   from the key, there is no `--ephemeral` flag on `tailscale up`.
   Pi (persistent disk) can use a stable key. Never commit keys
   (`tskey-auth-…` is gitleaks-blocked).
2. Hubs: set `TAILSCALE_AUTHKEY` (+ `TAIL_HOSTNAME=fief-hf|fief-render`,
   `TAIL_SERVE=1080,1081`) as secret/env and restart. Sidecar joins and
   serves the SOCKS ports on the node's tail IPs:
   `curl -x socks5h://<hub-tail-ip>:1081 ifconfig.me`.
   Optional: `TAIL_ADVERTISE_EXIT=1` offers the hub as exit node
   (approve in admin console).
3. Pi: `TAILSCALE_AUTHKEY=… TAIL_HOSTNAME=fief-pi uv run fief tail up`
   (add `TAIL_PROXY=socks5h://127.0.0.1:1081` if its network is restricted,
   `TAIL_ROUTES=192.168.x.0/24` to expose its LAN — routes need admin
   approval, or tag auto-approvers). Persist with systemd/tmux.
4. Verify: `fief tail status` / admin console shows the nodes; ping by
   tail IP or MagicDNS name.

Sleeping free-tier hubs drop off the tailnet until woken via public URL.
Use tags (not IPs) in ACLs — ephemeral cloud nodes get new IPs each boot.

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
