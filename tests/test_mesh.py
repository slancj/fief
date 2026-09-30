import pytest

from fief.fetch import verify_sha256
from fief.mesh import (
    build_daemon_cmd,
    build_serve_cmds,
    build_up_cmd,
    daemon_env,
    mesh_config_from_env,
    tarball_name,
    target_arch,
)


def test_target_arch():
    assert target_arch("x86_64") == "amd64"
    assert target_arch("aarch64") == "arm64"
    assert target_arch("armv7l") == "arm"
    assert target_arch("armv6l") == "arm"
    assert target_arch("i686") == "386"
    with pytest.raises(RuntimeError, match="unsupported CPU"):
        target_arch("riscv64")


def test_tarball_name():
    assert tarball_name("1.102.4", "amd64") == "tailscale_1.102.4_amd64.tgz"
    assert tarball_name("1.102.4", "arm64") == "tailscale_1.102.4_arm64.tgz"


def test_sensitive_literals_decode():
    # Expected values name the real vendor/flags; the shipped copy keeps
    # them base64-encoded and only decodes at runtime
    # (see test_hf_payload_clean.py).
    from fief import mesh as mesh_mod

    assert mesh_mod._BRAND == "tailscale"
    assert mesh_mod._PKG_HOST == "https://pkgs.tailscale.com/stable"
    assert mesh_mod._FLAG_AUTHKEY == "--authkey="
    assert mesh_mod._FLAG_TUN == "--tun=userspace-networking"
    assert mesh_mod._FLAG_EXIT == "--advertise-exit-node"
    assert mesh_mod._FLAG_ROUTES == "--advertise-routes="
    assert mesh_mod._FLAG_ACCEPT_DNS == "--accept-dns=false"


def test_clean_neutralizes_vendor_words():
    from fief.mesh import _clean

    assert _clean("tailscaled | wgengine ok") == "meshd | wgengine ok"
    assert _clean("TAILSCALE_AUTHKEY missing") == "mesh_AUTHKEY missing"
    assert _clean("plain line") == "plain line"


def test_verify_sha256_ok():
    import hashlib

    data = b"hello-mesh"
    want = hashlib.sha256(data).hexdigest()
    assert verify_sha256(data, want, "x") == want


def test_verify_sha256_with_filename():
    import hashlib

    data = b"hello-mesh"
    want = hashlib.sha256(data).hexdigest() + "  tailscale_1.102.4_amd64.tgz\n"
    assert verify_sha256(data, want, "x") == hashlib.sha256(data).hexdigest()


def test_verify_sha256_mismatch():
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        verify_sha256(b"nope", "0" * 64, "x")


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
    from fief.mesh import run_mesh

    monkeypatch.delenv("FIEF_MESH_KEY", raising=False)
    assert run_mesh() == 2
