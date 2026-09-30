"""Client roles: exit node (reverse SOCKS) and forward consumer."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .chisel import ensure_chisel
from .config import DEFAULT_CHISEL_VERSION
from .log import LogBuffer

LOG = LogBuffer()


def _binary() -> Path:
    return ensure_chisel(
        os.environ.get("CHISEL_VERSION", DEFAULT_CHISEL_VERSION), log=LOG.log
    )


def _auth() -> str:
    auth = os.environ.get("CHISEL_AUTH", "")
    if not auth:
        raise SystemExit("set CHISEL_AUTH, e.g. CHISEL_AUTH='user:...' fief exit")
    return auth


def _hub() -> str:
    return os.environ.get("HUB_URL", "https://spider-chisel.onrender.com")


def _keepalive() -> str:
    return os.environ.get("CHISEL_KEEPALIVE", "25s")


def cmd_exit() -> int:
    """LAN exit node: reverse SOCKS on the hub + hub-egress browsing port."""
    binary, auth, hub = _binary(), _auth(), _hub()
    egress = os.environ.get("EGRESS_PORT", "1081")
    remotes = [f"R:{os.environ.get('EXIT_SOCKS', 'socks')}", f"{egress}:socks"]
    while True:
        LOG.log(f"connecting to {hub} ({', '.join(remotes)}) ...")
        code = subprocess.run(
            [
                str(binary),
                "client",
                "--auth",
                auth,
                "--keepalive",
                _keepalive(),
                hub,
                *remotes,
            ],
            check=False,
        ).returncode
        LOG.log(f"disconnected (exit {code}). retrying in 10s ...")
        time.sleep(10)


def cmd_forward(no_ssh: bool = False) -> int:
    """Consumer: local forwards over one client connection."""
    binary, auth, hub = _binary(), _auth(), _hub()
    local = os.environ.get("LOCAL_PORT", "1080")
    egress = os.environ.get("EGRESS_PORT", "1081")
    remotes = [f"{local}:127.0.0.1:1080", f"{egress}:socks"]
    if not no_ssh and os.environ.get("FIEF_NO_SSH", "") != "1":
        ssh_port = os.environ.get("SSH_PORT", "2222")
        remotes.append(f"{ssh_port}:127.0.0.1:2222")
    LOG.log(f"connecting to {hub} ({', '.join(remotes)}) ...")
    return subprocess.run(
        [
            str(binary),
            "client",
            "--auth",
            auth,
            "--keepalive",
            _keepalive(),
            hub,
            *remotes,
        ],
        check=False,
    ).returncode
