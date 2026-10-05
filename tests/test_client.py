import threading
from pathlib import Path
from unittest import mock

from fief.client import (
    build_exit_cmd,
    build_forward_cmd,
    build_ssh_cmd,
    cmd_exit,
    cmd_forward,
    cmd_ssh,
    wait_local_port,
)
from fief.config import ClientConfig


def _cfg(**overrides):
    base = {
        "auth": "u:s",
        "hub_url": "https://hub.example",
        "keepalive": "25s",
        "version": "1.12.0",
        "local_port": "1080",
        "egress_port": "1081",
        "exit_socks": "socks",
        "ssh_port": "2222",
        "no_ssh": False,
    }
    return ClientConfig(**(base | overrides))


def test_build_exit_cmd():
    cmd = build_exit_cmd(Path("/tmp/chisel"), _cfg())
    assert cmd == [
        "/tmp/chisel",
        "client",
        "--auth",
        "u:s",
        "--keepalive",
        "25s",
        "https://hub.example",
        "R:socks",
        "1081:socks",
    ]


def test_build_forward_cmd_with_ssh():
    cmd = build_forward_cmd(Path("/tmp/chisel"), _cfg())
    assert cmd[-3:] == ["1080:127.0.0.1:1080", "1081:socks", "2222:127.0.0.1:2222"]


def test_build_forward_cmd_no_ssh():
    cmd = build_forward_cmd(Path("/tmp/chisel"), _cfg(no_ssh=True))
    assert cmd[-2:] == ["1080:127.0.0.1:1080", "1081:socks"]
    assert not any("2222" in c for c in cmd)


def test_wait_local_port_open():
    import socket

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        assert wait_local_port(str(srv.getsockname()[1]), threading.Event()) is True
    finally:
        srv.close()


def test_wait_local_port_stopped():
    stop = threading.Event()
    stop.set()
    assert wait_local_port("1081", stop, timeout=60) is False


def test_cmd_forward_returns_on_stop(monkeypatch):
    from fief import client as client_mod

    monkeypatch.setattr(client_mod, "ensure_chisel", lambda *a, **k: Path("/t"))
    stop = threading.Event()
    stop.set()
    assert cmd_forward(cfg=_cfg(), log=lambda m: None, stop=stop) == 0


def test_cmd_forward_retries_then_stops(monkeypatch):
    import types

    from fief import client as client_mod

    monkeypatch.setattr(client_mod, "ensure_chisel", lambda *a, **k: Path("/t"))
    stop = threading.Event()
    calls = {"run": 0, "wait": 0}

    def fake_run(cmd, **kwargs):
        calls["run"] += 1
        return types.SimpleNamespace(returncode=1)

    def fake_wait(s):
        calls["wait"] += 1
        if calls["wait"] >= 2:
            stop.set()
        return True

    monkeypatch.setattr(client_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(stop, "wait", fake_wait)
    logs: list[str] = []
    assert cmd_forward(cfg=_cfg(), log=logs.append, stop=stop) == 0
    assert calls == {"run": 2, "wait": 2}
    assert any("retrying" in line for line in logs)


def test_cmd_exit_returns_on_stop(monkeypatch):
    from fief import client as client_mod

    monkeypatch.setattr(client_mod, "ensure_chisel", lambda *a, **k: Path("/t"))
    stop = threading.Event()
    stop.set()
    assert cmd_exit(cfg=_cfg(), log=lambda m: None, stop=stop) == 0


def test_forward_cli_no_ssh_flag():
    import argparse

    from fief import client as client_mod
    from fief.cli import build_parser

    assert build_parser().parse_args(["forward", "--no-ssh"]).no_ssh is True
    with mock.patch.object(client_mod, "cmd_forward", return_value=0) as m:
        client_mod.run_forward(argparse.Namespace(no_ssh=True))
        assert m.call_args.kwargs["no_ssh"] is True


def test_build_ssh_cmd_defaults(monkeypatch):
    monkeypatch.delenv("SSH_PORT", raising=False)
    monkeypatch.delenv("SSH_USER", raising=False)
    assert build_ssh_cmd() == ["ssh", "-p", "2222", "fief@127.0.0.1"]


def test_build_ssh_cmd_env_and_overrides(monkeypatch):
    monkeypatch.setenv("SSH_PORT", "2223")
    monkeypatch.setenv("SSH_USER", "ops")
    assert build_ssh_cmd() == ["ssh", "-p", "2223", "ops@127.0.0.1"]
    assert build_ssh_cmd(port="2244", user="root") == [
        "ssh",
        "-p",
        "2244",
        "root@127.0.0.1",
    ]


def test_build_ssh_cmd_passthrough_strips_separator():
    assert build_ssh_cmd(ssh_args=["ls", "-la"]) == [
        "ssh",
        "-p",
        "2222",
        "fief@127.0.0.1",
        "ls",
        "-la",
    ]
    assert build_ssh_cmd(ssh_args=["--", "-v"]) == [
        "ssh",
        "-p",
        "2222",
        "fief@127.0.0.1",
        "-v",
    ]


def test_cmd_ssh_runs_ssh(monkeypatch):
    from fief import client as client_mod

    monkeypatch.setattr(client_mod.shutil, "which", lambda _: "/usr/bin/ssh")
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        import types

        return types.SimpleNamespace(returncode=7)

    monkeypatch.setattr(client_mod.subprocess, "run", fake_run)
    assert (
        cmd_ssh(port="2222", user="fief", ssh_args=["uptime"], log=lambda m: None) == 7
    )
    assert seen == [["ssh", "-p", "2222", "fief@127.0.0.1", "uptime"]]


def test_cmd_ssh_missing_binary(monkeypatch):
    from fief import client as client_mod

    monkeypatch.setattr(client_mod.shutil, "which", lambda _: None)
    assert cmd_ssh(log=lambda m: None) == 127


def test_ssh_cli_parsing():
    from fief.cli import build_parser

    args = build_parser().parse_args(["ssh"])
    assert (args.port, args.user, args.ssh_args) == ("", "", [])
    args = build_parser().parse_args(["ssh", "--port", "2223", "--user", "ops"])
    assert (args.port, args.user) == ("2223", "ops")
    args = build_parser().parse_args(["ssh", "--", "ls", "-la"])
    assert args.ssh_args == ["--", "ls", "-la"]
    args = build_parser().parse_args(["ssh", "--port", "2223", "--", "-v"])
    assert (args.port, args.ssh_args) == ("2223", ["--", "-v"])


def test_ssh_cli_dispatches():
    import argparse

    from fief import client as client_mod
    from fief.cli import build_parser

    with mock.patch.object(client_mod, "cmd_ssh", return_value=0) as m:
        client_mod.run_ssh(argparse.Namespace(port="", user="", ssh_args=["uptime"]))
        assert m.call_args.kwargs == {"port": "", "user": "", "ssh_args": ["uptime"]}
    assert callable(build_parser().parse_args(["ssh"]).func)
