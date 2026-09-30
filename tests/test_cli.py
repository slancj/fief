import pytest

from fief.cli import build_parser


def test_subcommands():
    p = build_parser()
    assert p.parse_args(["hub"]).cmd == "hub"
    assert p.parse_args(["exit"]).cmd == "exit"
    assert p.parse_args(["forward"]).cmd == "forward"
    assert p.parse_args(["forward", "--no-ssh"]).no_ssh is True
    assert p.parse_args(["version"]).cmd == "version"
    assert p.parse_args(["mesh", "up"]).mesh_cmd == "up"
    assert p.parse_args(["mesh", "down"]).mesh_cmd == "down"
    assert p.parse_args(["mesh", "status"]).mesh_cmd == "status"
    assert p.parse_args(["mesh", "status", "--json"]).json is True


def test_requires_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
