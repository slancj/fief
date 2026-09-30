---
title: fief-relay
emoji: 📡
colorFrom: gray
colorTo: purple
sdk: gradio
app_file: app.py
pinned: false
---

# fief-relay (Hugging Face backup hub)

Outbound-only chisel hub (same role as the Render `fief-relay`): Windows opens
a reverse SOCKS, Linux forwards it home (`1080`) and takes a second SOCKS off
HF egress (`1081`). Chisel owns port `7860`; browser traffic (healthcheck and
the status page below) is proxied by chisel `--backend` to the Gradio app on
`127.0.0.1:7861`. No sshd in this mode (Spaces runs as uid 1000).

## Deploy (automatic)

Pushing `hf-space/` to `main` syncs it to the Space via
`.github/workflows/deploy-hf-space.yml`. One-time setup:

1. HF Settings → Access Tokens: fine-grained token with Write on the Space
   (`<owner>/<name>`).
2. GitHub repo → Settings → Secrets and variables → Actions:
   `HF_TOKEN` (secret) + `HF_SPACE_ID='<owner>/<name>'` (variable).
3. Push — first run creates the Space if missing (private, gradio SDK).
4. Space Settings → Secrets: set `CHISEL_AUTH` (`user:secret`), then Restart.
5. Open the Space: status page = chisel is reachable through the same URL.

Manual fallback: `huggingface_hub` CLI / `git push` of this folder to
`https://huggingface.co/spaces/<owner>/<name>`.

## Clients

Hub URL is `https://<owner>-<space>.hf.space`. Same auth as the secret.
No `2222` sshd remote on this hub — drop that forward:

```sh
# Linux (1080 = Windows LAN exit, 1081 = HF egress exit)
HUB_URL='https://<owner>-<space>.hf.space' CHISEL_AUTH='user:secret' \
  chisel client --auth "$CHISEL_AUTH" "$HUB_URL" \
    "1080:127.0.0.1:1080" "1081:socks"
```

```powershell
# Windows (reverse SOCKS + HF-egress browsing)
$env:HUB_URL='https://<owner>-<space>.hf.space'
$env:CHISEL_AUTH='user:secret'
.\windows\run-chisel.ps1   # from the main fief repo (HUB_URL-aware)
```

## Notes

* Keep the Space **private**; this is backup infra, not a public demo.
  Tunnel/egress-proxy use can look like abuse on a public Space.
* Free Spaces sleep when idle; the clients' `--keepalive 25s` usually keeps
  the websocket (and the Space) alive, first connect after sleep is slow.
* Host has no persistent disk: nothing to rotate on restart besides the
  `CHISEL_AUTH` secret itself. Logs are in-memory (`bin/` is re-fetched).
* Main runbook (Render primary, ports, gotchas): see the `fief` repo
  `docs/RUNBOOK.md`.
