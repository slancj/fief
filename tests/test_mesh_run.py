from fief.config import mesh_config_from_env
from fief.mesh_run import (
    build_daemon_cmd,
    build_serve_cmds,
    build_up_cmd,
    check_serve_target,
    daemon_env,
)


def test_daemon_env_direct():
    assert daemon_env("") == {}


def test_daemon_env_proxy():
    env = daemon_env("socks5h://127.0.0.1:1081")
    assert env["ALL_PROXY"] == "socks5h://127.0.0.1:1081"
    assert env["HTTPS_PROXY"] == "socks5h://127.0.0.1:1081"
    assert env["HTTP_PROXY"] == "socks5h://127.0.0.1:1081"
    assert env["NO_PROXY"] == "localhost,127.0.0.1"


def test_build_daemon_cmd_neutral_binary(tmp_path):
    cmd = build_daemon_cmd(tmp_path / "fiefmeshd", tmp_path / "run", "1055")
    assert "tailscale" not in " ".join(cmd).lower()
    assert f"--socket={tmp_path}/run/meshd.sock" in cmd
    assert f"--state={tmp_path}/run/meshd.state" in cmd
    # var root: without --statedir the daemon disables SSH host keys
    assert f"--statedir={tmp_path}/run" in cmd
    assert "--socks5-server=127.0.0.1:1055" in cmd
    assert "--outbound-http-proxy-listen=127.0.0.1:1055" in cmd
    assert "--tun=userspace-networking" in " ".join(cmd)


def _cfg(**overrides):
    import os
    from unittest import mock

    env = {
        "FIEF_MESH_KEY": overrides.get("authkey", "tskey-auth-TESTKEY"),
        "FIEF_MESH_HOSTNAME": overrides.get("hostname", "fief-test"),
        "FIEF_MESH_PROXY": overrides.get("proxy", ""),
        "FIEF_MESH_SERVE": ",".join(overrides.get("serve", ())),
        "FIEF_MESH_ADVERTISE_EXIT": "1" if overrides.get("exit") else "0",
        "FIEF_MESH_ROUTES": ",".join(overrides.get("routes", ())),
        "FIEF_MESH_SSH": "1" if overrides.get("ssh") else "0",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        return mesh_config_from_env()


def test_build_up_cmd_minimal(tmp_path):
    cfg = _cfg()
    cmd = build_up_cmd(tmp_path / "fiefmesh", tmp_path / "sock", cfg)
    assert cmd[:4] == [
        str(tmp_path / "fiefmesh"),
        f"--socket={tmp_path}/sock",
        "up",
        "--authkey=tskey-auth-TESTKEY",
    ]
    assert "--hostname=fief-test" in cmd
    assert "--accept-dns=false" in cmd
    assert not any("advertise" in c for c in cmd)


def test_build_up_cmd_full(tmp_path):
    cfg = _cfg(routes=("192.168.1.0/24",), exit=True)
    cmd = build_up_cmd(tmp_path / "fiefmesh", tmp_path / "sock", cfg)
    assert "--advertise-routes=192.168.1.0/24" in cmd
    assert "--advertise-exit-node" in cmd


def test_build_up_cmd_ssh_opt_in(tmp_path):
    cmd = build_up_cmd(tmp_path / "fiefmesh", tmp_path / "sock", _cfg(ssh=True))
    assert "--ssh" in cmd


def test_build_up_cmd_ssh_default_off(tmp_path):
    cmd = build_up_cmd(tmp_path / "fiefmesh", tmp_path / "sock", _cfg())
    assert "--ssh" not in cmd


def test_build_serve_cmds(tmp_path):
    cmds = build_serve_cmds(tmp_path / "fiefmesh", tmp_path / "sock", ("1080", "1081"))
    assert cmds == [
        [
            str(tmp_path / "fiefmesh"),
            f"--socket={tmp_path}/sock",
            "serve",
            "--bg",
            "--tcp=1080",
            "tcp://127.0.0.1:1080",
        ],
        [
            str(tmp_path / "fiefmesh"),
            f"--socket={tmp_path}/sock",
            "serve",
            "--bg",
            "--tcp=1081",
            "tcp://127.0.0.1:1081",
        ],
    ]
    assert build_serve_cmds(tmp_path / "fiefmesh", tmp_path / "sock", ()) == []


def test_run_mesh_needs_key(monkeypatch):
    from fief.mesh_run import run_mesh

    monkeypatch.delenv("FIEF_MESH_KEY", raising=False)
    assert run_mesh() == 2


def test_check_serve_target_open():
    import socket

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = str(srv.getsockname()[1])
    logs: list[str] = []
    try:
        assert check_serve_target(port, logs.append) is True
    finally:
        srv.close()
    assert logs == []


def test_check_serve_target_closed():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = str(s.getsockname()[1])
    logs: list[str] = []
    assert check_serve_target(port, logs.append) is False
    assert logs == [f"serve target 127.0.0.1:{port} closed (exit node offline?)"]


def test_check_serve_target_garbage():
    logs: list[str] = []
    assert check_serve_target("notaport", logs.append) is False
    assert len(logs) == 1
