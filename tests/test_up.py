import threading

from fief import client as client_mod
from fief import mesh_run as mesh_mod
from fief import up as up_mod
from fief.config import ClientConfig, MeshConfig


def _client_cfg(**overrides):
    base = {
        "auth": "u:s",
        "hub_url": "https://hub.example",
        "local_port": "1080",
        "egress_port": "1081",
        "no_ssh": True,
    }
    return ClientConfig(**(base | overrides))


def _mesh_cfg(**overrides):
    base = {
        "authkey": "tskey-auth-X",
        "hostname": "fief-test",
        "proxy": "",
        "system": False,
        "socket": None,
    }
    return MeshConfig(**(base | overrides))


def test_up_system_skips_forward(monkeypatch):
    monkeypatch.setattr(up_mod, "mesh_config_from_env", lambda: _mesh_cfg(system=True))
    seen = {}

    def fake_mesh(cfg, log=None, stop=None):
        seen["proxy"] = cfg.proxy
        seen["system"] = cfg.system
        return 0

    monkeypatch.setattr(mesh_mod, "run_mesh", fake_mesh)
    monkeypatch.setattr(
        client_mod,
        "cmd_forward",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no tunnel here")),
    )
    stop = threading.Event()
    assert up_mod.cmd_up(system=True, log=lambda m: None, stop=stop) == 0
    assert seen == {"proxy": "", "system": True}


def test_up_isolated_wires_proxy(monkeypatch):
    monkeypatch.setattr(up_mod, "mesh_config_from_env", lambda: _mesh_cfg())
    monkeypatch.setattr(up_mod, "client_config_from_env", lambda **k: _client_cfg())
    monkeypatch.setattr(client_mod, "cmd_forward", lambda **k: 0)
    monkeypatch.setattr(client_mod, "wait_local_port", lambda *a, **k: True)
    seen = {}

    def fake_mesh(cfg, log=None, stop=None):
        seen["proxy"] = cfg.proxy
        return 0

    monkeypatch.setattr(mesh_mod, "run_mesh", fake_mesh)
    stop = threading.Event()
    assert up_mod.cmd_up(log=lambda m: None, stop=stop) == 0
    assert seen["proxy"] == "socks5h://127.0.0.1:1081"


def test_up_isolated_keeps_explicit_proxy(monkeypatch):
    monkeypatch.setattr(
        up_mod, "mesh_config_from_env", lambda: _mesh_cfg(proxy="socks5h://10.0.0.1:9")
    )
    monkeypatch.setattr(up_mod, "client_config_from_env", lambda **k: _client_cfg())
    monkeypatch.setattr(client_mod, "cmd_forward", lambda **k: 0)
    monkeypatch.setattr(client_mod, "wait_local_port", lambda *a, **k: True)
    seen = {}
    monkeypatch.setattr(
        mesh_mod,
        "run_mesh",
        lambda cfg, log=None, stop=None: seen.update(proxy=cfg.proxy) or 3,
    )
    stop = threading.Event()
    assert up_mod.cmd_up(log=lambda m: None, stop=stop) == 3
    assert seen["proxy"] == "socks5h://10.0.0.1:9"


def test_up_egress_timeout_fails(monkeypatch):
    monkeypatch.setattr(up_mod, "mesh_config_from_env", lambda: _mesh_cfg())
    monkeypatch.setattr(up_mod, "client_config_from_env", lambda **k: _client_cfg())
    monkeypatch.setattr(client_mod, "cmd_forward", lambda **k: 0)
    monkeypatch.setattr(client_mod, "wait_local_port", lambda *a, **k: False)
    monkeypatch.setattr(
        mesh_mod,
        "run_mesh",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("never joins")),
    )
    logs: list[str] = []
    stop = threading.Event()
    assert up_mod.cmd_up(log=logs.append, stop=stop) == 1
    assert any("never opened" in line for line in logs)


def test_up_cli_flags():
    from fief.cli import build_parser

    args = build_parser().parse_args(["up"])
    assert args.cmd == "up" and args.system is False and args.no_ssh is False
    args = build_parser().parse_args(["up", "--system", "--no-ssh"])
    assert args.system is True and args.no_ssh is True
    assert callable(args.func)
