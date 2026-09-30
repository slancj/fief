---
title: fief monitor
emoji: 📊
colorFrom: gray
colorTo: purple
sdk: gradio
python_version: "3.12"
app_file: app.py
pinned: false
---

# fief monitor

Private status page for a fief node. Shows service status and recent log
output. Nothing here is a demo — if you found this page without an invite,
there is nothing to see.

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
4. Secrets arrive via fan-out (`config/secrets.yaml` → Space Secrets on
   every push touching `config/`; manual fallback is setting `CHISEL_AUTH`
   (+ optional `SSH_PUBKEY`) in Space Settings). Restart after any change.
5. Open the Space: the status page loading means the service is up.

Manual fallback: `python scripts/assemble_hf_space.py --out dist/space`,
then `git push` that folder to
`https://huggingface.co/spaces/<owner>/<name>`.

## Operators

Connection strings, client commands, and rotation procedure live in the
`fief` repo `docs/RUNBOOK.md` — not here.

## Notes

* Keep the Space **private**.
* Free Spaces sleep when idle; first visit after sleep is slow.
* No persistent disk: state is in-memory and re-fetched per boot. Rotate
  by changing the `CHISEL_AUTH` secret and restarting.
