#!/usr/bin/env python3
"""HF Spaces entrypoint (Gradio SDK). Thin shim over the shared fief package.

The deploy workflow vendors src/fief into this folder before upload, so
`fief.*` resolves locally on the Space. All logic lives in the package.

Box downloads (/add.sh, /box/*) ride next to the Gradio UI: the demo
launches normally on a loopback port (so platform scans that walk a
launched app keep working) while the stdlib multiplexer on the backend
port serves box/health/local paths itself and proxies everything else
to the demo. Stdlib only — no extra server dependency.
"""

import os
from dataclasses import replace

os.environ.setdefault("PORT", "7860")  # gradio SDK exposes 7860

from fief.config import hub_config_from_env
from fief.hub import LOG, STOP, main, snapshot
from fief.mesh_run import maybe_start_from_env


def serve_with_downloads(cfg, ui, hub_url) -> None:
    """HTTP backend for HF: stdlib multiplexer in front of the demo."""
    from fief.status import serve_forever
    from fief.ui_gradio import build_ui, launch_demo

    gradio_port = str(int(cfg.backend_port) + 1)
    launch_demo(
        build_ui(bool(cfg.auth), hub_url, lambda: snapshot(cfg, hub_url)),
        server_name="127.0.0.1",
        server_port=int(gradio_port),
    )
    LOG.log(f"box backend: demo on 127.0.0.1:{gradio_port}, multiplexer first")
    serve_forever(
        int(cfg.backend_port),
        lambda: snapshot(cfg, hub_url),
        hub_url=hub_url,
        proxy_to=f"127.0.0.1:{gradio_port}",
    )


if __name__ == "__main__":
    cfg = replace(hub_config_from_env(), ui="gradio")
    raise SystemExit(
        main(
            cfg,
            sidecar=lambda: maybe_start_from_env(log=LOG.log, stop=STOP),
            serve=serve_with_downloads,
        )
    )
