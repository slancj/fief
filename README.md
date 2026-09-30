# fief

Personal infrastructure estate. First holding: an outbound-only tunnel
hub that reaches a LAN behind a firewall and borrows unblocked internet
from a free cloud relay — no inbound ports, no kernel drivers.

One `uv` project, deployable anywhere: Render, Hugging Face Spaces,
Raspberry Pi (`compose.yaml`), or any Docker host / Linux box.

## Roles

* **hub** (`fief hub`) — chisel server on `$PORT`, `--reverse` + `--socks5`.
  Browser traffic on the same port is proxied to a status UI.
* **exit** (`fief exit`) — a Linux box on the LAN opens a reverse SOCKS
  (`R:socks`) on the hub; traffic to the hub's `1080` exits into its LAN.
  Reconnects forever.
* **consumer** (`fief forward`) — one client connection opens local forwards:
  `1080` (LAN via exit node), `1081` (hub-egress internet), `2222`
  (shell on hubs with `SSH_PUBKEY` set).
* **tail** (`fief tail up`) — join this box to your tailnet as a dedicated
  userspace node (proxied or direct); hubs join too when `TAILSCALE_AUTHKEY`
  is set, serving `1080`/`1081` on their tail IPs.

## Layout

* `pyproject.toml` / `uv.lock` — the project (Python ≥3.12, stdlib-only core)
* `src/fief/` — shared package: `hub`, `exit`/`forward` clients, chisel
  fetch+verify (amd64/arm64/…), stdlib status UI, optional Gradio UI,
  optional sshd sidecar (Docker only)
* `Dockerfile` — shared multi-arch image (`fief hub` as CMD)
* `compose.yaml` — `hub` / `exit` profiles for Pi and Docker hosts
* `render.yaml` — hub as code (image + start command, secrets via dashboard)
* `hf-space/` — Gradio Space shim + card (payload assembled by CI)
* `scripts/assemble_hf_space.py` — vendor `src/fief` into a Space payload
* `docs/RUNBOOK.md` — start order, healthy logs, gotchas

## Quickstart

```sh
uv sync                                  # create .venv, install fief editable
cp .env.example .env   # put real CHISEL_AUTH in .env, never commit it
```

1. Hub: `uv run fief hub` (or deploy the image — see below)
2. Exit node on the LAN: `CHISEL_AUTH='...' uv run fief exit`
3. Consumer: `CHISEL_AUTH='...' uv run fief forward`
4. `proxychains xfreerdp /v:<lan-ip> /u:<user>` (proxychains → port `1080`)
5. `ssh -p 2222 fief@127.0.0.1` — shell on the hub (Docker hubs with
   `SSH_PUBKEY` set only)

## Deploy

| Target | How |
|---|---|
| Render | Dashboard → Blueprint → `slancj/fief`; set `CHISEL_AUTH` (+ optional `SSH_PUBKEY`) |
| HF Space | Push `main`; CI assembles + syncs `hf-space/`; set `CHISEL_AUTH` in Space Secrets |
| Pi / Docker host | `docker compose --profile hub up -d` (hub) or `--profile exit up -d` (exit) |
| Any Linux box | `uv sync` + `uv run fief hub` (or `fief exit`) under systemd/tmux |
| Release image | Tag `v*` → multi-arch (amd64+arm64) image on GHCR |

Secrets live in env / dashboards only — never in the image or repo.

## Clone setup

```sh
pre-commit install   # gitleaks secret scanning + ruff on every commit
```

(Get `pre-commit` via `nix-shell -p pre-commit`, `pipx install pre-commit`, ….
CI runs the secret scan over full history on push, so a secret fails the build
even from a hookless machine.)
