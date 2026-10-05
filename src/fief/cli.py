"""`fief` CLI: hub | exit | forward | mesh | up | config | version.

Wiring only: this module composes the tunnel and mesh parts (the only
place allowed to import both — see tests/test_arch.py). Each command
module registers its own subparser; `hub` gets the mesh-sidecar starter
injected so hub.py stays import-clean.
"""

from __future__ import annotations

import argparse

from . import __version__


def run_hub(args: argparse.Namespace) -> int:
    from . import hub, mesh_run

    return hub.main(
        sidecar=lambda: mesh_run.maybe_start_from_env(log=hub.LOG.log, stop=hub.STOP)
    )


def build_parser() -> argparse.ArgumentParser:
    from . import client, config_cmd, hub, mesh_run, up

    p = argparse.ArgumentParser(
        prog="fief", description="Outbound-only chisel tunnel hub + clients."
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    hub.register(sub, run_hub)
    client.register(sub)
    mesh_run.register(sub)
    up.register(sub)
    config_cmd.register(sub)
    sub.add_parser("version", help="print version").set_defaults(func=run_version)
    return p


def run_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def main(argv: list[str] | None = None) -> int:
    from .config import ensure_local_env

    ensure_local_env()
    args = build_parser().parse_args(argv)
    return args.func(args)
