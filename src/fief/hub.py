"""Hub orchestrator: status backend + chisel server + restart loop.

Chisel owns $PORT. Normal browser HTTP (healthchecks, status UI) is proxied
by chisel itself via --backend to the local backend; chisel websocket
sessions (matched on Sec-WebSocket-Protocol) become tunnels.
With ui=none no --backend is passed and chisel serves /health itself.
"""

from __future__ import annotations

import argparse
import threading
import time
import urllib.request
from http.client import HTTPException
from pathlib import Path

from . import __version__
from .chisel import ensure_chisel
from .config import HubConfig, hub_config_from_env, resolve_ui, space_public_url
from .egress import start_egress_server
from .log import LogBuffer
from .proc import drain, spawn, wire_stop
from .sshd import maybe_start_sshd
from .status import serve_forever as serve_status

LOG = LogBuffer()
STOP = threading.Event()

_started_at = time.time()
_chisel_state = {"running": False, "restarts": 0}


def register(sub: argparse._SubParsersAction) -> None:
    sub.add_parser(
        "hub", help="run the tunnel hub (chisel server + status UI)"
    ).set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    return main()


def status_text(cfg: HubConfig, hub_url: str) -> str:
    uptime = int(time.time() - _started_at)
    state = "running" if _chisel_state["running"] else "restarting"
    backend = "none" if cfg.ui == "none" else f"127.0.0.1:{cfg.backend_port}"
    return (
        f"fief {__version__} | chisel: {state}\n"
        f"uptime: {uptime}s\n"
        f"restarts: {_chisel_state['restarts']}\n"
        f"external port: {cfg.port} (chisel, --backend to {backend})\n"
        f"public URL: {hub_url}"
    )


def wait_for_backend(backend_port: str, timeout: int = 90) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{backend_port}/health"
    while time.time() < deadline and not STOP.is_set():
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status < 500:
                    LOG.log("status backend is up")
                    return
        except (OSError, HTTPException):
            time.sleep(1)
    raise RuntimeError("status backend did not start in time")


def run_chisel(binary: Path, cfg: HubConfig) -> int:
    """Run chisel server in the foreground; the caller handles restarts."""
    cmd = [
        str(binary),
        "server",
        "--port",
        cfg.port,
        "--reverse",
        "--socks5",
        "--auth",
        cfg.auth,
        "--keepalive",
        cfg.keepalive,
    ]
    if cfg.ui != "none":
        cmd += ["--backend", f"http://127.0.0.1:{cfg.backend_port}"]
    LOG.log(f"starting: chisel server --port {cfg.port} --reverse --socks5")
    proc = spawn(cmd)
    _chisel_state["running"] = True
    try:
        drain(proc, lambda line: LOG.log("chisel | " + line), STOP)
    finally:
        _chisel_state["running"] = False
    return proc.wait(timeout=30)


def start_backend(
    cfg: HubConfig, ui: str, hub_url: str, port_override: str | None = None
) -> None:
    if ui == "gradio":
        from .ui_gradio import build_ui, launch_demo

        demo = build_ui(bool(cfg.auth), hub_url, lambda: snapshot(cfg, hub_url))
        if not cfg.auth:
            launch_demo(demo, server_name="0.0.0.0", server_port=int(cfg.port))
            return
        launch_demo(demo, server_name="127.0.0.1", server_port=int(cfg.backend_port))
    else:  # basic stdlib status page
        serve_status(
            int(port_override or cfg.backend_port),
            lambda: snapshot(cfg, hub_url),
        )


def _maybe_start_tail() -> None:
    """Mesh sidecar (hub keeps owning the foreground).

    Lazy + ImportError-tolerant so minimal hosts without the module still
    boot; absence is a clean skip, not an error.
    """
    try:
        from . import mesh_run as mesh_mod
    except ImportError:
        return
    mesh_mod.maybe_start_from_env(log=LOG.log, stop=STOP)


def snapshot(cfg: HubConfig, hub_url: str) -> tuple[str, str]:
    return status_text(cfg, hub_url), LOG.snapshot()


def main(cfg: HubConfig | None = None) -> int:
    wire_stop(STOP)

    cfg = cfg or hub_config_from_env()
    hub_url = space_public_url()

    if not cfg.auth:
        if cfg.ui == "none":
            LOG.log("CHISEL_AUTH is required")
            return 2
        ui = resolve_ui(cfg.ui)
        LOG.log("CHISEL_AUTH not set, serving config-error page only")
        start_backend(
            cfg, ui, hub_url, port_override=cfg.port if ui == "basic" else None
        )
        return 0

    ui = resolve_ui(cfg.ui)
    if ui != "none":
        start_backend(cfg, ui, hub_url)
        try:
            wait_for_backend(cfg.backend_port)
        except RuntimeError as exc:
            LOG.log(str(exc))
            return 1

    maybe_start_sshd(cfg.ssh_pubkey, cfg.ssh_port, cfg.ssh_user, log=LOG.log)
    _maybe_start_tail()
    start_egress_server(cfg.egress_port, STOP, LOG.log)
    binary = ensure_chisel(cfg.version, log=LOG.log)

    backoff = 5
    while not STOP.is_set():
        code = run_chisel(binary, cfg)
        if STOP.is_set():
            return 0
        _chisel_state["restarts"] += 1
        LOG.log(f"chisel exited ({code}), restarting in {backoff}s")
        STOP.wait(backoff)
        backoff = min(backoff * 2, 60)
    return 0
