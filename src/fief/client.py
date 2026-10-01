"""Client roles: exit node (reverse SOCKS) and forward consumer."""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

from .chisel import ensure_chisel
from .config import ClientConfig, client_config_from_env
from .log import LogBuffer

LOG = LogBuffer()


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


def run_exit(args: argparse.Namespace) -> int:
    return cmd_exit()


def run_forward(args: argparse.Namespace) -> int:
    return cmd_forward(no_ssh=args.no_ssh)


def _binary(cfg: ClientConfig) -> Path:
    return ensure_chisel(cfg.version, log=LOG.log)


def cmd_exit(cfg: ClientConfig | None = None) -> int:
    """LAN exit node: reverse SOCKS on the hub + hub-egress browsing port."""
    cfg = cfg or client_config_from_env()
    binary = _binary(cfg)
    remotes = [f"R:{cfg.exit_socks}", f"{cfg.egress_port}:socks"]
    while True:
        LOG.log(f"connecting to {cfg.hub_url} ({', '.join(remotes)}) ...")
        code = subprocess.run(
            [
                str(binary),
                "client",
                "--auth",
                cfg.auth,
                "--keepalive",
                cfg.keepalive,
                cfg.hub_url,
                *remotes,
            ],
            check=False,
        ).returncode
        LOG.log(f"disconnected (exit {code}). retrying in 10s ...")
        time.sleep(10)


def cmd_forward(no_ssh: bool = False, cfg: ClientConfig | None = None) -> int:
    """Consumer: local forwards over one client connection."""
    cfg = cfg or client_config_from_env(no_ssh_flag=no_ssh)
    binary = _binary(cfg)
    remotes = [f"{cfg.local_port}:127.0.0.1:1080", f"{cfg.egress_port}:socks"]
    if not cfg.no_ssh:
        remotes.append(f"{cfg.ssh_port}:127.0.0.1:2222")
    LOG.log(f"connecting to {cfg.hub_url} ({', '.join(remotes)}) ...")
    return subprocess.run(
        [
            str(binary),
            "client",
            "--auth",
            cfg.auth,
            "--keepalive",
            cfg.keepalive,
            cfg.hub_url,
            *remotes,
        ],
        check=False,
    ).returncode
