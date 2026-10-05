"""Client roles: exit node (reverse SOCKS) and forward consumer.

Part 1 (tunnel): both roles are supervised reconnect loops over one chisel
client connection each. ``cmd_exit``/``cmd_forward`` take ``(cfg, log,
stop)`` like the mesh supervisor so wiring (``fief up``) can compose
them; CLI entry points wire signals when on the main thread.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .chisel import ensure_chisel
from .config import ClientConfig, client_config_from_env
from .log import LogBuffer
from .proc import wire_stop

LOG = LogBuffer()
STOP = threading.Event()


def register(sub: argparse._SubParsersAction) -> None:
    sub.add_parser(
        "exit",
        help="run a LAN exit node (reverse SOCKS on the hub, reconnect loop)",
    ).set_defaults(func=run_exit)
    fwd = sub.add_parser(
        "forward", help="open local forwards over one client connection"
    )
    fwd.add_argument(
        "--no-ssh",
        action="store_true",
        help="skip the 2222 sshd forward (hubs without SSH_PUBKEY); "
        "same as FIEF_NO_SSH=1",
    )
    fwd.set_defaults(func=run_forward)
    ssh_p = sub.add_parser(
        "ssh",
        help="shell on the hub over the chisel forward (needs `forward` running)",
    )
    ssh_p.add_argument(
        "--port",
        default="",
        help="local forward port (default SSH_PORT or 2222)",
    )
    ssh_p.add_argument(
        "--user",
        default="",
        help="hub login user (default SSH_USER or fief)",
    )
    ssh_p.add_argument(
        "ssh_args",
        nargs=argparse.REMAINDER,
        help="extra args passed to ssh, e.g. `-- ls -la /tmp` "
        "(`--` separates ssh flags like -v from fief's own)",
    )
    ssh_p.set_defaults(func=run_ssh)


def run_exit(args: argparse.Namespace) -> int:
    return cmd_exit(log=LOG.log, stop=_main_stop())


def run_forward(args: argparse.Namespace) -> int:
    return cmd_forward(no_ssh=args.no_ssh, log=LOG.log, stop=_main_stop())


def run_ssh(args: argparse.Namespace) -> int:
    return cmd_ssh(port=args.port, user=args.user, ssh_args=args.ssh_args)


def _main_stop() -> threading.Event:
    if threading.current_thread() is threading.main_thread():
        wire_stop(STOP)
    return STOP


def build_exit_cmd(binary: Path, cfg: ClientConfig) -> list[str]:
    return [
        str(binary),
        "client",
        "--auth",
        cfg.auth,
        "--keepalive",
        cfg.keepalive,
        cfg.hub_url,
        f"R:{cfg.exit_socks}",
        f"{cfg.egress_port}:socks",
    ]


def build_forward_cmd(binary: Path, cfg: ClientConfig) -> list[str]:
    remotes = [f"{cfg.local_port}:127.0.0.1:1080", f"{cfg.egress_port}:socks"]
    if not cfg.no_ssh:
        remotes.append(f"{cfg.ssh_port}:127.0.0.1:2222")
    return [
        str(binary),
        "client",
        "--auth",
        cfg.auth,
        "--keepalive",
        cfg.keepalive,
        cfg.hub_url,
        *remotes,
    ]


def ssh_port_from_env() -> str:
    """Local sshd-forward port: SSH_PORT env, else the forward default."""
    return os.environ.get("SSH_PORT", "") or "2222"


def ssh_user_from_env() -> str:
    """Hub login user: SSH_USER env, else the hub default."""
    return os.environ.get("SSH_USER", "") or "fief"


def build_ssh_cmd(
    port: str = "", user: str = "", ssh_args: Sequence[str] = ()
) -> list[str]:
    """`ssh -p <port> <user>@127.0.0.1 ...`: the other end of the
    ``SSH_PORT:127.0.0.1:2222`` forward opened by `fief forward`."""
    extra = list(ssh_args)
    if extra[:1] == ["--"]:
        extra = extra[1:]
    return [
        "ssh",
        "-p",
        port or ssh_port_from_env(),
        f"{user or ssh_user_from_env()}@127.0.0.1",
        *extra,
    ]


def cmd_ssh(
    port: str = "",
    user: str = "",
    ssh_args: Sequence[str] = (),
    log: Callable[[str], None] | None = None,
) -> int:
    """Exec ssh through the local forward. No chisel/auth needed here —
    `fief forward` already holds the tunnel; this just dials localhost."""
    emit = log or LOG.log
    if shutil.which("ssh") is None:
        emit("ssh binary not found (install openssh-client)")
        return 127
    cmd = build_ssh_cmd(port=port, user=user, ssh_args=ssh_args)
    emit(f"running: {' '.join(cmd)}")
    try:
        return subprocess.run(cmd, check=False).returncode
    except OSError as exc:
        emit(f"ssh failed to start ({exc})")
        return 127


def wait_local_port(port: str, stop: threading.Event, timeout: int = 180) -> bool:
    """Wait until something listens on 127.0.0.1:port. For orchestrators
    gating on a forward (e.g. `fief up` waits for 1081 before joining)."""
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline and not stop.is_set():
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
                return True
        except (OSError, ValueError):
            stop.wait(2)
    return False


def cmd_exit(
    cfg: ClientConfig | None = None,
    log: Callable[[str], None] | None = None,
    stop: threading.Event | None = None,
) -> int:
    """LAN exit node: reverse SOCKS on the hub + hub-egress browsing port."""
    emit = log or LOG.log
    cfg = cfg or client_config_from_env()
    stop = stop or STOP
    binary = ensure_chisel(cfg.version, log=emit)
    remotes = [f"R:{cfg.exit_socks}", f"{cfg.egress_port}:socks"]
    while not stop.is_set():
        emit(f"connecting to {cfg.hub_url} ({', '.join(remotes)}) ...")
        code = subprocess.run(
            build_exit_cmd(binary, cfg),
            check=False,
        ).returncode
        if stop.is_set():
            return 0
        emit(f"disconnected (exit {code}). retrying in 10s ...")
        stop.wait(10)
    return 0


def cmd_forward(
    no_ssh: bool = False,
    cfg: ClientConfig | None = None,
    log: Callable[[str], None] | None = None,
    stop: threading.Event | None = None,
) -> int:
    """Consumer: local forwards over one client connection, reconnecting
    with backoff (sleeping free-tier hubs wake on redial)."""
    emit = log or LOG.log
    cfg = cfg or client_config_from_env(no_ssh_flag=no_ssh)
    stop = stop or STOP
    binary = ensure_chisel(cfg.version, log=emit)
    remotes = [f"{cfg.local_port}:127.0.0.1:1080", f"{cfg.egress_port}:socks"]
    if not cfg.no_ssh:
        remotes.append(f"{cfg.ssh_port}:127.0.0.1:2222")
    backoff = 5
    while not stop.is_set():
        emit(f"connecting to {cfg.hub_url} ({', '.join(remotes)}) ...")
        code = subprocess.run(
            build_forward_cmd(binary, cfg),
            check=False,
        ).returncode
        if stop.is_set():
            return 0
        emit(f"disconnected (exit {code}). retrying in {backoff}s ...")
        stop.wait(backoff)
        backoff = min(backoff * 2, 60)
    return 0
