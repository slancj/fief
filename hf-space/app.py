#!/usr/bin/env python3
"""fief-relay hub on Hugging Face Spaces (Gradio SDK).

Single externally exposed port (7860) is owned by chisel. Browser traffic
(HF healthcheck, this Gradio status UI) is proxied by chisel itself via
--backend to the Gradio app on 127.0.0.1:BACKEND_PORT. Chisel websocket
sessions are recognised by their Sec-WebSocket-Protocol header and handled
as tunnels, everything else falls through to the backend proxy.

Secrets: set CHISEL_AUTH in Space Settings -> Secrets (format user:secret).
No sshd in this mode (Spaces runs as uid 1000); use the Render hub when
you need `ssh -p 2222 fief@127.0.0.1` over the tunnel.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

CHISEL_VERSION = "1.12.0"
BASE_URL = (
    f"https://github.com/jpillora/chisel/releases/download/v{CHISEL_VERSION}"
)
GZ_NAME = f"chisel_{CHISEL_VERSION}_linux_amd64.gz"

HERE = Path(__file__).resolve().parent
BIN_DIR = HERE / "bin"
CHISEL_BIN = BIN_DIR / "chisel"

PORT = os.environ.get("PORT", "7860")  # externally exposed (HF expects 7860)
BACKEND_PORT = os.environ.get("BACKEND_PORT", "7861")  # Gradio, localhost only
KEEPALIVE = os.environ.get("CHISEL_KEEPALIVE", "25s")

LOG_LINES = deque(maxlen=400)
LOG_LOCK = threading.Lock()
STOP = threading.Event()


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    with LOG_LOCK:
        LOG_LINES.append(line)
    print(line, file=sys.stderr, flush=True)


def ensure_chisel() -> str:
    """Download (once) + checksum-verify the pinned chisel binary."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    if CHISEL_BIN.exists():
        r = subprocess.run(
            [str(CHISEL_BIN), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if CHISEL_VERSION in (r.stdout + r.stderr):
            log(f"chisel {CHISEL_VERSION} already present")
            return str(CHISEL_BIN)
        log("chisel version mismatch, re-downloading")
    gz_data = _fetch(f"{BASE_URL}/{GZ_NAME}")
    sums = _fetch(f"{BASE_URL}/chisel_{CHISEL_VERSION}_checksums.txt").decode()
    want = None
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].endswith(GZ_NAME):
            want = parts[0]
            break
    if not want:
        raise RuntimeError("checksum entry not found for " + GZ_NAME)
    got = hashlib.sha256(gz_data).hexdigest()
    if got != want:
        raise RuntimeError(f"checksum mismatch: {got} != {want}")
    with gzip.open(io.BytesIO(gz_data), "rb") as src:
        with open(CHISEL_BIN, "wb") as dst:
            shutil.copyfileobj(src, dst)
    CHISEL_BIN.chmod(
        CHISEL_BIN.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    log(f"chisel {CHISEL_VERSION} verified (sha256 {got[:12]}...)")
    return str(CHISEL_BIN)


def _fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fief-relay"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def space_public_url() -> str:
    """Best-effort public URL; falls back to a placeholder."""
    space_id = os.environ.get("SPACE_ID", "")  # e.g. "owner/name"
    if "/" in space_id:
        owner, name = space_id.split("/", 1)
        return f"https://{owner}-{name}.hf.space"
    return "https://<owner>-<space>.hf.space"


def build_ui(auth_ok: bool, hub_url: str) -> object:
    import gradio as gr

    if not auth_ok:
        with gr.Blocks(title="fief-relay") as demo:
            gr.Markdown("# fief-relay hub (not configured)")
            gr.Markdown(
                "Set the `CHISEL_AUTH` secret in Space Settings "
                "(format `user:secret`), then Restart this Space.\n\n"
                "The secret is never baked into the build."
            )
        return demo

    status_box = None
    log_box = None
    with gr.Blocks(title="fief-relay") as demo:
        gr.Markdown("# fief-relay hub (Hugging Face backup)")
        gr.Markdown(
            f"Chisel hub on `{hub_url}`. Browser traffic here is proxied "
            "through chisel to this page; tunnel clients connect to the "
            "same URL with the chisel client.\n\n"
            "No sshd in Spaces mode (chisel-only). For a shell on the hub, "
            "use the Render hub."
        )
        with gr.Row():
            status_box = gr.Textbox(
                label="Hub status", value="starting...", lines=6
            )
        log_box = gr.Textbox(label="Recent hub log", value="", lines=20)
        refresh = gr.Button("Refresh")
        gr.Markdown(
            "## Clients (no `2222` sshd remote on this hub)\n\n"
            f"Windows (PowerShell): `$env:HUB_URL='{hub_url}'; "
                "$env:CHISEL_AUTH='user:secret'; "
                ".\\windows\\run-chisel.ps1`\n\n"
            f"Linux: `HUB_URL='{hub_url}' CHISEL_AUTH='user:secret' "
                "linux/chisel-forward.sh` without the ssh remote, i.e. "
                "`chisel client --auth \"$CHISEL_AUTH\" \"$HUB_URL\" "
                '"1080:127.0.0.1:1080" "1081:socks"`\n\n'
            "`1080` exits via the Windows LAN, `1081` via HF egress."
        )

        def _snapshot() -> tuple[str, str]:
            with LOG_LOCK:
                logs = "\n".join(LOG_LINES)
            return (_status_text(), logs)

        refresh.click(fn=_snapshot, outputs=[status_box, log_box])
        demo.load(fn=_snapshot, outputs=[status_box, log_box])
    return demo


_started_at = time.time()
_chisel_state = {"running": False, "restarts": 0}


def _status_text() -> str:
    uptime = int(time.time() - _started_at)
    state = "running" if _chisel_state["running"] else "restarting"
    return (
        f"chisel: {state}\n"
        f"uptime: {uptime}s\n"
        f"restarts: {_chisel_state['restarts']}\n"
        f"external port: {PORT} (chisel, --backend to Gradio :{BACKEND_PORT})\n"
        f"public URL: {space_public_url()}"
    )


def wait_for_backend(timeout: int = 90) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{BACKEND_PORT}/"
    while time.time() < deadline and not STOP.is_set():
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status < 500:
                    log("Gradio backend is up")
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("Gradio backend did not start in time")


def run_chisel(binary: str, auth: str) -> int:
    """Run chisel in the foreground; restarts are handled by the caller."""
    cmd = [
        binary,
        "server",
        "--port",
        PORT,
        "--reverse",
        "--socks5",
        "--auth",
        auth,
        "--keepalive",
        KEEPALIVE,
        "--backend",
        f"http://127.0.0.1:{BACKEND_PORT}",
    ]
    log(f"starting: chisel server --port {PORT} --reverse --socks5 --backend :{BACKEND_PORT}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    _chisel_state["running"] = True
    for line in proc.stdout:
        log("chisel | " + line.rstrip())
        if STOP.is_set():
            break
    _chisel_state["running"] = False
    if STOP.is_set():
        proc.terminate()
    return proc.wait(timeout=30)


def main() -> int:
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())

    auth = os.environ.get("CHISEL_AUTH", "")
    hub_url = space_public_url()
    demo = build_ui(bool(auth), hub_url)
    if not auth:
        log("CHISEL_AUTH not set, serving config-error page only")
        demo.launch(server_name="0.0.0.0", server_port=int(PORT))
        return 0

    binary = ensure_chisel()
    demo.launch(
        server_name="127.0.0.1",
        server_port=int(BACKEND_PORT),
        prevent_thread_lock=True,
        show_api=False,
    )
    try:
        wait_for_backend()
    except RuntimeError as exc:
        log(str(exc))
        return 1

    backoff = 5
    while not STOP.is_set():
        code = run_chisel(binary, auth)
        if STOP.is_set():
            return 0
        _chisel_state["restarts"] += 1
        log(f"chisel exited ({code}), restarting in {backoff}s")
        STOP.wait(backoff)
        backoff = min(backoff * 2, 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
