"""`fief` CLI: hub | exit | forward | version."""

from __future__ import annotations

import argparse

from . import __version__
from .hub import main as hub_main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fief", description="Outbound-only chisel tunnel hub + clients."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("hub", help="run the tunnel hub (chisel server + status UI)")

    sub.add_parser(
        "exit",
        help="run a LAN exit node (reverse SOCKS on the hub, reconnect loop)",
    )

    fwd = sub.add_parser(
        "forward", help="open local forwards over one client connection"
    )
    fwd.add_argument(
        "--no-ssh",
        action="store_true",
        help="skip the 2222 sshd forward (hubs without SSH_PUBKEY)",
    )

    sub.add_parser("version", help="print version")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "hub":
        return hub_main()
    if args.cmd == "exit":
        from .client import cmd_exit

        return cmd_exit()
    if args.cmd == "forward":
        from .client import cmd_forward

        return cmd_forward(no_ssh=args.no_ssh)
    print(__version__)
    return 0
