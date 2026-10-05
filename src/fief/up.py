"""`fief up`: restricted-network join in one supervised flow.

Wiring only (see tests/test_arch.py): composes Part 1 (tunnel) with
Part 2 (mesh) through their public entry points.

Isolated (default): starts ``forward`` in a background thread, waits for
the egress port (hub internet), then joins as an isolated mesh node
*through* that port — the two-terminal dance as one foreground process.
``--system`` short-circuits: no forward, no proxy, just configure the
system daemon and serve (same as ``mesh up --system``).
"""

from __future__ import annotations

import argparse
import dataclasses
import threading
from collections.abc import Callable

from . import client as client_mod
from . import mesh_run as mesh_mod
from .config import client_config_from_env, mesh_config_from_env
from .log import LogBuffer
from .proc import wire_stop

LOG = LogBuffer()
STOP = threading.Event()


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("up", help="join everything at once (forward + proxied mesh)")
    p.add_argument(
        "--no-ssh",
        action="store_true",
        help="skip the 2222 sshd forward; same as FIEF_NO_SSH=1",
    )
    p.add_argument(
        "--system",
        action="store_true",
        help="drive the system daemon (reuse its login, no new node)",
    )
    p.add_argument("--socket", default="", help="daemon socket (system mode)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    return cmd_up(
        no_ssh=args.no_ssh,
        system=args.system,
        socket=args.socket,
        log=LOG.log,
        stop=_main_stop(),
    )


def _main_stop() -> threading.Event:
    if threading.current_thread() is threading.main_thread():
        wire_stop(STOP)
    return STOP


def cmd_up(
    no_ssh: bool = False,
    system: bool = False,
    socket: str = "",
    log: Callable[[str], None] | None = None,
    stop: threading.Event | None = None,
) -> int:
    """One flow: forward thread + readiness gate + mesh join."""
    emit = log or LOG.log
    stop = stop or STOP

    mesh_cfg = mesh_mod.apply_cli_overrides(
        mesh_config_from_env(), argparse.Namespace(system=system, socket=socket)
    )
    if mesh_cfg.system:
        # No tunnel needed: reuse the system login, configure + serve.
        return mesh_mod.run_mesh(mesh_cfg, log=emit, stop=stop)

    client_cfg = client_config_from_env(no_ssh_flag=no_ssh)
    fwd = threading.Thread(
        target=_forward_guarded,
        kwargs={"cfg": client_cfg, "log": emit, "stop": stop},
        daemon=True,
        name="fief-forward",
    )
    fwd.start()
    emit(f"waiting for egress 127.0.0.1:{client_cfg.egress_port} ...")
    if not client_mod.wait_local_port(client_cfg.egress_port, stop):
        emit("egress port never opened (hub unreachable?)")
        stop.set()
        fwd.join(timeout=30)
        return 1
    emit("egress up, joining mesh through it")
    proxy = mesh_cfg.proxy or f"socks5h://127.0.0.1:{client_cfg.egress_port}"
    try:
        return mesh_mod.run_mesh(
            dataclasses.replace(mesh_cfg, proxy=proxy), log=emit, stop=stop
        )
    finally:
        stop.set()
        fwd.join(timeout=30)


def _forward_guarded(**kwargs) -> None:
    """Forward thread body: never let an exception kill the flow silently."""
    try:
        client_mod.cmd_forward(**kwargs)
    except Exception as exc:  # noqa: BLE001 — supervisor boundary
        (kwargs.get("log") or LOG.log)(f"forward died: {exc}")
