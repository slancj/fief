"""`fief` CLI: hub | exit | forward | tail | version."""

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

    tail = sub.add_parser("tail", help="tailnet node via fief")
    tail_sub = tail.add_subparsers(dest="tail_cmd", required=True)
    tail_sub.add_parser("up", help="join the tailnet (foreground supervisor)")
    tail_sub.add_parser("down", help="leave + stop the node")
    tail_status = tail_sub.add_parser("status", help="tailnet status")
    tail_status.add_argument("--json", action="store_true")

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
    if args.cmd == "tail":
        from . import tail as tail_mod

        if args.tail_cmd == "up":
            return tail_mod.run_node(log=tail_mod.LOG.log)
        if args.tail_cmd == "down":
            return tail_mod.cmd_down()
        return tail_mod.cmd_status(json_output=args.json)
    print(__version__)
    return 0
