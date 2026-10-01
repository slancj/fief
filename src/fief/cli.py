"""`fief` CLI: hub | exit | forward | mesh | config | version.

Thin dispatcher: each command module registers its own subparser and
exposes ``run(args)``. Adding a command means touching that module only.
"""

from __future__ import annotations

import argparse

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    from . import client, config_cmd, hub, mesh_run

    p = argparse.ArgumentParser(
        prog="fief", description="Outbound-only chisel tunnel hub + clients."
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    hub.register(sub)
    client.register(sub)
    mesh_run.register(sub)
    config_cmd.register(sub)
    sub.add_parser("version", help="print version").set_defaults(func=run_version)
    return p


def run_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
