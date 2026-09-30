---
title: fief-relay
emoji: 📡
colorFrom: gray
colorTo: purple
sdk: gradio
python_version: "3.12"
app_file: app.py
pinned: false
---

# fief-relay (Hugging Face hub)

Outbound-only chisel hub. A Linux exit node opens a reverse SOCKS, consumers
forward it home (`1080`) and take a second SOCKS off HF egress (`1081`).
Chisel owns port `7860`; browser traffic (healthcheck, status page) is proxied
by chisel `--backend` to the Gradio app on `127.0.0.1:7861`. No sshd here
(Spaces runs as uid 1000) — use a Docker-capable hub for the shell-on-hub
feature.

> This folder is assembled by CI (`scripts/assemble_hf_space.py`): `app.py`
> is a thin shim, the `fief/` package is vendored from `src/fief` at deploy
> time. Edit the package, not the payload.

## Deploy (automatic)

Pushing `src/`, `hf-space/` or the workflow to `main` syncs an assembled
payload to the Space via `.github/workflows/deploy-hf-space.yml`. One-time
setup:

1. HF Settings → Access Tokens: fine-grained token with Write on the Space
   (`<owner>/<name>`).
2. GitHub repo → Settings → Secrets and variables → Actions:
   `HF_TOKEN` (secret) + `HF_SPACE_ID='<owner>/<name>'` (variable).
3. Push — first run creates the Space if missing (private, gradio SDK).
4. Space Settings → Secrets: set `CHISEL_AUTH` (`user:secret`), then Restart.
5. Open the Space: status page = chisel is reachable through the same URL.

Manual fallback: `python scripts/assemble_hf_space.py --out dist/space`,
then `git push` that folder to
`https://huggingface.co/spaces/<owner>/<name>`.

## Clients

Hub URL is `https://<owner>-<space>.hf.space`. Same auth as the secret.
No `2222` sshd forward on this hub — pass `--no-ssh`:

```sh
# Exit node (a Linux box on the LAN):
HUB_URL='https://<owner>-<space>.hf.space' CHISEL_AUTH='user:secret' fief exit

# Consumer (1080 = LAN exit, 1081 = HF egress exit):
HUB_URL='https://<owner>-<space>.hf.space' CHISEL_AUTH='user:secret' \
  fief forward --no-ssh
```

## Notes

* Keep the Space **private**; this is backup infra, not a public demo.
  Tunnel/egress-proxy use can look like abuse on a public Space.
* Free Spaces sleep when idle; the clients' `--keepalive 25s` usually keeps
  the websocket (and the Space) alive, first connect after sleep is slow.
* No persistent disk: the chisel binary is re-fetched per boot, logs are
  in-memory. Rotate by changing the `CHISEL_AUTH` secret and restarting.
* Main runbook: see the `fief` repo `docs/RUNBOOK.md`.
