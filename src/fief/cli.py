"""`fief` CLI: hub | exit | forward | mesh | config | version."""

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
        help="skip the 2222 sshd forward (hubs without SSH_PUBKEY); "
        "same as FIEF_NO_SSH=1",
    )

    mesh = sub.add_parser("mesh", help="mesh node via fief")
    mesh_sub = mesh.add_subparsers(dest="mesh_cmd", required=True)
    mesh_sub.add_parser("up", help="join the mesh (foreground supervisor)")
    mesh_sub.add_parser("down", help="leave + stop the node")
    mesh_status = mesh_sub.add_parser("status", help="mesh status")
    mesh_status.add_argument("--json", action="store_true")

    cfg = sub.add_parser("config", help="one-place config (nodes.toml + secrets)")
    cfg_sub = cfg.add_subparsers(dest="config_cmd", required=True)
    cfg_export = cfg_sub.add_parser("export", help="render dotenv for a node")
    cfg_export.add_argument("--node", required=True)
    cfg_get = cfg_sub.add_parser("get", help="print one decrypted secret")
    cfg_get.add_argument("key")
    cfg_sub.add_parser("edit", help="edit secrets.yaml in sops")

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
    if args.cmd == "mesh":
        from . import mesh as mesh_mod

        if args.mesh_cmd == "up":
            return mesh_mod.run_mesh(log=mesh_mod.LOG.log)
        if args.mesh_cmd == "down":
            return mesh_mod.cmd_down()
        return mesh_mod.cmd_status(json_output=args.json)
    if args.cmd == "config":
        from . import config_cmd as config_mod

        if args.config_cmd == "export":
            return config_mod.cmd_export(args.node)
        if args.config_cmd == "get":
            return config_mod.cmd_get(args.key)
        return config_mod.cmd_edit()
    print(__version__)
    return 0
