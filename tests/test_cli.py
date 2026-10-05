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
    assert p.parse_args(["mesh", "up", "--system"]).system is True
    assert (
        p.parse_args(["mesh", "up", "--socket", "/tmp/x.sock"]).socket == "/tmp/x.sock"
    )
    assert p.parse_args(["mesh", "down"]).mesh_cmd == "down"
    assert p.parse_args(["mesh", "down", "--system"]).system is True
    assert p.parse_args(["mesh", "status"]).mesh_cmd == "status"
    assert p.parse_args(["mesh", "status", "--system"]).system is True
    assert p.parse_args(["mesh", "status"]).mesh_cmd == "status"
    assert p.parse_args(["mesh", "status", "--json"]).json is True
    assert p.parse_args(["ssh"]).cmd == "ssh"
    assert p.parse_args(["ssh", "--port", "2223"]).port == "2223"
    assert p.parse_args(["ssh", "--user", "ops"]).user == "ops"
    assert p.parse_args(["ssh", "--", "ls"]).ssh_args == ["--", "ls"]


def test_requires_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_every_subcommand_dispatches():
    p = build_parser()
    for argv in (
        ["hub"],
        ["exit"],
        ["forward"],
        ["ssh"],
        ["ssh", "--port", "2223"],
        ["version"],
        ["mesh", "up"],
        ["mesh", "down"],
        ["mesh", "status"],
        ["up"],
        ["up", "--system"],
        ["up", "--no-ssh"],
        ["config", "export", "--node", "hf"],
        ["config", "get", "CHISEL_AUTH"],
        ["config", "invite"],
        ["config", "invite", "--name", "box-1"],
        ["config", "edit"],
    ):
        assert callable(p.parse_args(argv).func), argv
