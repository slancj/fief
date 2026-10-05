#!/usr/bin/env python3
"""HF Spaces entrypoint (Gradio SDK). Thin shim over the shared fief package.

The deploy workflow vendors src/fief into this folder before upload, so
`fief.*` resolves locally on the Space. All logic lives in the package.

Box downloads (/add.sh, /box/*) ride next to the Gradio UI: the UI mounts
inside a small FastAPI app whose explicit routes serve the onboarding
artifacts (public code + public binaries only, never secrets). When the
mount stack is unavailable the shim falls back to the plain UI.
"""

import os
import threading
from dataclasses import replace

os.environ.setdefault("PORT", "7860")  # gradio SDK exposes 7860

from fief.config import hub_config_from_env
from fief.hub import LOG, STOP, main, snapshot
from fief.mesh_run import maybe_start_from_env


def serve_with_downloads(cfg, ui, hub_url) -> None:
    """HTTP backend for HF: Gradio UI + box artifact routes on one port."""
    try:
        import gradio as gr
        import uvicorn
        from fastapi import FastAPI, Request
        from fastapi.responses import Response

        from fief import boxserve
        from fief.ui_gradio import build_ui
    except ImportError as exc:
        LOG.log(f"download routes unavailable ({exc}), UI only")
        from fief.hub import start_backend

        start_backend(cfg, ui, hub_url)
        return

    async def box_endpoint(request: Request) -> Response:
        res = boxserve.route(
            request.url.path,
            host=request.headers.get("host", ""),
            hub_url=hub_url,
            log=LOG.log,
        )
        if res is None:
            return Response(b"Not found\n", status_code=404)
        code, body, ctype = res
        return Response(body, status_code=code, media_type=ctype)

    async def health() -> Response:
        return Response(b"OK\n", media_type="text/plain")

    demo = build_ui(bool(cfg.auth), hub_url, lambda: snapshot(cfg, hub_url))
    try:
        app = FastAPI()
        app.add_api_route("/health", health, methods=["GET"])
        app.add_api_route("/add.sh", box_endpoint, methods=["GET"])
        app.add_api_route("/box/{_path:path}", box_endpoint, methods=["GET"])
        gr.mount_gradio_app(app, demo, path="/")
        LOG.log("box backend: UI + /add.sh + /box/* on one port")
    except Exception as exc:  # noqa: BLE001 — a Space without downloads
        LOG.log(f"box backend unavailable ({exc}), UI only")  # beats a 503 Space
        from fief.hub import start_backend

        start_backend(cfg, ui, hub_url)
        return
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=int(cfg.backend_port), log_level="warning"
        )
    )
    threading.Thread(target=server.run, daemon=True).start()


if __name__ == "__main__":
    cfg = replace(hub_config_from_env(), ui="gradio")
    raise SystemExit(
        main(
            cfg,
            sidecar=lambda: maybe_start_from_env(log=LOG.log, stop=STOP),
            serve=serve_with_downloads,
        )
    )
