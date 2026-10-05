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
  itself): GitHub `SOPS_AGE_KEY`, `HF_TOKEN`, `RENDER_API_KEY`.
  Target addresses live in `nodes.toml` (`space_id`, `service_id`;
  `HF_SPACE_ID` / `RENDER_SERVICE_ID` repo variables override them when
  set). Age private key also
  lives at `~/.config/fief/age.key` on hosts. Tailscale key creation and
  route/exit approvals stay in the Tailscale console.

Bootstrap (once): `age-keygen` → public key into `.sops.yaml`
(replacing the placeholder) → private key to GitHub `SOPS_AGE_KEY` + host
key files → `cp config/secrets.example.yaml config/secrets.yaml`, fill in,
`sops config/secrets.yaml` (encrypts in place), commit, push.
Fan-out refuses plaintext (sops-envelope guard) and gitleaks scans history.

## Roles: hub / exit / client

Every node in `nodes.toml` declares `role` (enforced by tests and
`config export` — a node that breaks its role fails fast with the rule):

| Role | Members | Job | Holds mesh key? | Serves? | SSH? |
|---|---|---|---|---|---|
| `hub` | hf, render | dialable relay: chisel server + mesh sidecar on `1080`/`1081` | yes (ephemeral, reusable, tagged) | `1080,1081` | opt-in per node |
| `exit` | pi | strict LAN door: reverse tunnel + optional subnet routes | yes (stable: persistent disk) | never | closed unless opted in |
| `client` | laptop | keyless consumer (`forward` / `up --system`) | never | never | n/a (initiates) |

Tailnet side (console is source of truth, this table records intent):

* Tags: `tag:fief-hub`, `tag:fief-exit`, `tag:fief-client`. Mint keys
  per role: hubs **Ephemeral** (cloud disks forget; otherwise dead
  entries pile up), exits stable, clients none.
* Route auto-approvers: `tag:fief-exit` only.
* SSH grants: `tag:fief-client` → `tag:fief-hub`, `tag:fief-exit`;
  grant against tags, never hostnames (ephemeral reboots register as
  new machines). Hubs never SSH each other; exits accept only clients.
* `site = "..."` in `nodes.toml` is reserved for grouping exits by LAN
  when a second one appears; today it is validated and passed through.

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
   `uv run fief exit` (HUB_URL defaults to the hf node in `config/nodes.toml`;
   `CHISEL_AUTH` auto-loads from env → gitignored `.env` → sops decrypt).
   Expect `Connected`. Hub log shows a session with
   `R:127.0.0.1:1080=>socks: Listening`.
3. Consumer: `uv run fief forward` (same defaults; `HUB_URL=...` env overrides).
   Expect `tun: proxy#1080=>1080: Listening` then `Connected`. `forward`
   reconnects with backoff, so sleeping free-tier hubs wake on redial.
    (`--no-ssh`, or `FIEF_NO_SSH=1`, on hubs without `SSH_PUBKEY` set —
    always for HF Space, which has no sshd binary.)
4. Use it: `proxychains xfreerdp /v:<lan-ip> /u:<user>`
   (or `proxychains curl http://<lan-ip>/` to smoke-test).
   `proxychains` must point at port `1080` for LAN exits.

## One command: fief up (tunnel + mesh together)

On a restricted network the mesh daemon can't dial out directly, so
`fief up` runs the whole sequence as one supervised flow: it starts
`forward` in the background, waits for the egress port (`1081`) to open,
then joins as an isolated mesh node *through* it
(`FIEF_MESH_PROXY` defaults to `socks5h://127.0.0.1:1081`; an explicit
`FIEF_MESH_PROXY` is respected). Ctrl-C stops everything.

```sh
uv run fief up --no-ssh            # restricted net, HF hub (no sshd there)
uv run fief up --system            # already on the tailnet: skip the tunnel,
                                   # just configure the system daemon + serve
```

`--system` never needs `FIEF_MESH_KEY` and leaves existing prefs alone
unless explicitly set (see Mesh). `mesh status --all` queries the
isolated and system daemons side by side when both exist.

## Unblocked internet via hub egress

The hub also runs `--socks5`, so a second local port exits through the
hub's connection instead of the LAN:
`curl -x socks5h://127.0.0.1:1081 ifconfig.me` shows the hub's IP.
`fief forward` opens both (`1080` = LAN, `1081` = egress).
Point a second proxychains profile (or `curl -x`) at `1081` for browsing.

## Shell on the hub via chisel

Any hub with an sshd binary runs it (key-only, localhost-only, container
port 2222) when `SSH_PUBKEY` is set. `fief forward` maps it to
localhost:2222 (drop it with `--no-ssh` / `FIEF_NO_SSH=1` on hubs without
`SSH_PUBKEY`):

```sh
fief ssh              # shell on the hub (needs `fief forward` running)
fief ssh -- ls -la    # run a remote command instead of a login shell
fief ssh -- -v        # `--` separates ssh flags from fief's own

# Manual equivalent (Docker hubs with SSH_PUBKEY set, user fief):
ssh -p 2222 fief@127.0.0.1

# HF Space has no sshd binary — shell there is via tailnet SSH (see Mesh).
```

Non-root hubs (HF, uid 1000) serve the container user — sshd can't setuid
without root. Set `SSH_USER` to change the name on root-run (Docker) hubs
(`fief ssh` reads it too; `--user` / `--port` override `SSH_USER` /
`SSH_PORT` per invocation).

Host keys regenerate on every deploy, so expect a changed-host-key prompt
after each hub update. `SSH_PUBKEY` unset = sshd stays off, chisel-only.

## Add a box (one-liner, restricted networks)

Any Linux box (amd64/arm64, `sh` + `curl` + `python3 >= 3.10`, hub
reachable) joins as a shell + mesh node with one paste. No root, no
`uv`, no GitHub access needed on the box — the hub serves the public
bundle (`/add.sh`, source, pinned chisel/mesh binaries), the tunnel
carries everything else.

1. Laptop: `fief config invite` prints a one-liner plus a single blob
   (`fief config invite --name my-box` to pick the hostname, else auto
   `fief-box-XXXX`). The blob packs `HUB_URL`, `CHISEL_AUTH`, the
   **shared** mesh key, hostname, `FIEF_MESH_SSH=1` — treat it like a
   secret. Mint that shared key once (stable, reusable, tagged
   `tag:fief-box` — never `tag:fief-exit`, so route auto-approval can't
   leak onto boxes) and store it where `invite` resolves secrets
   (env/`.env`/secrets.yaml).
2. Box: `curl -fsSL $HUB/add.sh | sh` (the `-f` matters: HTTP errors
   must fail the download, never pipe an error page into `sh`). Verifies
   checksums, stages binaries
   into the `ensure_chisel`/`ensure_mesh` layout (never re-downloads),
   prompts for the blob, writes `box.env` (`chmod 600`), starts `exit`
   then proxied `mesh up`, installs persistence (systemd user unit →
   `cron @reboot` → printed re-run notice; the script reports its tier),
   self-checks, and prints the laptop command.
3. Console: enable the SSH toggle + grant `tag:fief-client` →
   `tag:fief-box` (tags, never hostnames), delete stale offline entries.
4. Laptop: `tailscale ssh fief-box-XXXX` (verify with `id -u`), services
   via `1080` once `forward` is up.

Shell here is tailnet-only by design (chisel-into-box is a later
feature). LAN routes later: uncomment `FIEF_MESH_ROUTES` in the box's
`box.env`, restart `box-run.sh`, approve in the console — no reinstall.
Boxes stay out of `nodes.toml` (ephemeral, auto-named); backfill an
entry only if a box becomes permanent.

## Mesh (nodes join the mesh)

Every node can be a mesh node: hubs (HF Space, Render/Docker) via the
`fief hub` sidecar, Pi/any Linux box via `fief mesh up`. Dedicated
userspace daemon everywhere — no root, no TUN, no system changes.

1. Admin console → Settings → Keys: create a **reusable, tagged** auth key.
   Cloud nodes (Render, ephemeral disks, fresh identity each boot) MUST
   use an **Ephemeral** key or dead entries pile up — ephemerality comes
   from the key. Pi (persistent disk) can use a stable key. Never commit
   keys (`tskey-auth-…` is gitleaks-blocked).
2. Hubs: set `FIEF_MESH_KEY` (+ `FIEF_MESH_HOSTNAME=fief-render`,
   `FIEF_MESH_SERVE=1080,1081`) as env/secret and restart. Sidecar joins
   and serves the SOCKS ports on the node's mesh IPs:
   `curl -x socks5h://<hub-mesh-ip>:1081 ifconfig.me`.
   Port truth: `1080` is the LAN exit — it exists only while an exit
   node holds the hub's reverse remote (`R:socks` binds the hub's
   default socks port). `1081` is the hub's own egress listener
   (stdlib SOCKS5 in the hub process, `EGRESS_PORT`, direct connection
   + server-side DNS). After serve setup the sidecar probes each
   container-local target and warns (`serve target ... closed`) when a
   forward points at a dead port — e.g. 1080 with the exit offline.
   FoxyProxy over the tailnet: SOCKS5 to `<hub-mesh-ip>:1080/1081`
   with remote DNS on; the mesh ACL is the auth, so keep grants tight.
    Optional: `FIEF_MESH_ADVERTISE_EXIT=1` offers the hub as exit node
    (approve in admin console).
    Optional: `FIEF_MESH_SSH=1` enables tailnet SSH on the node
    (`tailscale ssh <hostname>` from any peer). Decided per node in
    `config/nodes.toml` vars — default off. Console side also needs the
    Tailscale SSH feature toggle + an `ssh` grant in access controls,
    otherwise connections are refused. The hf node opts in; it has no
    sshd binary in its image, so tailnet SSH is the only shell there.
    Host keys live in the run dir (`ssh/` next to `meshd.state`, via
    `--statedir` — without it the daemon logs "no var root for ssh keys"
    and SSH stays disabled). Ephemeral disks mean fresh host keys per
    boot; that pairs with the fresh node identity, so clients verify
    against the advertised keys without stale warnings.
    Two gates: the tailnet policy checks the *requested* username, but an
    unprivileged daemon can only ever run the session as its own uid —
    so `root@` passing the policy still lands you in a uid-1000 shell
    (verify with `id -u`). Gate on the real account (`users` holding the
    container user, or `autogroup:nonroot`); a `root` entry permits the
    name but grants nothing extra on these boxes.
    Write `ssh` grants against a **tag**, not the hostname: reboots and
    renames register as new machines (`fief-monitor-1` when a stale
    `fief-monitor` entry still lingers), and a name-based `dst` silently
    stops matching. Tag the auth key (e.g. `tag:fief`), grant
    `dst: ["tag:fief"]`, and delete stale offline machine entries in the
    console so the next boot reclaims the clean name.
3. Pi: `FIEF_MESH_KEY=… FIEF_MESH_HOSTNAME=fief-pi uv run fief mesh up`
   (add `FIEF_MESH_PROXY=socks5h://127.0.0.1:1081` if its network is
   restricted, `FIEF_MESH_ROUTES=192.168.x.0/24` to expose its LAN —
   routes need admin approval, or tag auto-approvers). Persist with
   systemd/tmux.
   Already on the tailnet (system daemon logged in)? Drive it instead of
   spawning a second node: `uv run fief mesh up --system`
   (same for `mesh status --system` / `mesh down --system`; no
   `FIEF_MESH_KEY` needed — the system login is reused and a bare
   `--system` leaves hostname/DNS/routes alone, only applying prefs you
   explicitly set via `FIEF_MESH_*`; `FIEF_MESH_PROXY` is ignored there,
   set the proxy on the `tailscaled` unit instead).
   `FIEF_MESH_SOCKET` overrides the
   socket path when it isn't the default
   `/var/run/tailscale/tailscaled.sock`.
   First `--system up` may report `Access denied: prefs write access
   denied` — grant once (`sudo tailscale set --operator=$USER`), then
   retry unprivileged.
4. Verify: `fief mesh status` / admin console shows the nodes.
   Leave with `fief mesh down`. (`fief version` prints the build.)
   Daemon logs are quiet by default: known-routine chatter is suppressed
   (counted, with a receipt every 100 lines) while errors and unknown
   lines always show. `FIEF_MESH_VERBOSE=1` restores full passthrough
   for debugging sessions.

Sleeping free-tier hubs drop off the mesh until woken via public URL.
Use tags (not IPs) in ACLs — ephemeral cloud nodes get new IPs each boot.

## Platform notes

* **Render free** sleeps after ~15 min idle; first connect wakes it (~30s).
* **HF Space**: keep private; free Spaces sleep when idle; client
  `--keepalive 25s` usually keeps the websocket alive. No sshd binary in
  the image, so the sidecar skips it — shell access is via tailnet SSH
  (`FIEF_MESH_SSH=1`, see Mesh) as the container user.
* **Pi/compose**: `restart: unless-stopped`; image from GHCR release tags
  (`v*` → amd64+arm64) or local `docker compose build`.
* Local forwards are bare `local:remote` — no `L:` prefix (chisel parses
  `L` as a hostname and fails with `cannot listen`).
* `Stream error ... <nil>` lines when a client disconnects are benign.
* Secrets live in env vars / dashboards only. Rotate by changing
  `CHISEL_AUTH` and restarting (no rebuild needed).
