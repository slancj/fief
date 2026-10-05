from fief.config import DEFAULT_SYSTEM_SOCKET, mesh_config_from_env
from fief.mesh_run import (
    MeshLogFilter,
    build_daemon_cmd,
    build_serve_cmds,
    build_up_cmd,
    check_serve_target,
    daemon_env,
    resolve_socket,
)

ROUTINE_SAMPLES = [
    "magicsock: new contact: peer=[Hferv] usec=30216435 cached=false via=derp",
    "magicsock: endpoints changed: 3.22.120.161:60934 (stun)",
    "magicsock: home is now derp-12 (ord)",
    "magicsock: 1 active derp conns: derp-12=cr0s,wr0s",
    "derphttp.Client.Connect: connecting to derp-12 (ord)",
    "update netmap cache: profile local data storage unavailable",
    "dns: Set: {DefaultResolvers:[] Routes:{} SearchDomains:[] Hosts:0}",
    "tsdial: bart table size: 6",
    "peerapi: serving on http://100.101.27.27:53935",
    "wgengine: Reconfig: configuring router",
    "control: NetInfo: NetInfo{varies=true udp=true}",
    "taildrop: Taildrop disabled; no state directory",
    "offline auto-update: stopping update checks",
    "cannot fetch existing TKA state; no state directory for lock",
]

PASS_SAMPLES = [
    "Switching ipn state Starting -> Running (WantRunning=true, nm=true)",
    "warning: unable to get SSH host keys, SSH will appear as disabled",
    "active login: someone@example.com",
    "EditPrefs: MaskedPrefs{AutoUpdate={Apply=true}}",
    "join failed: invalid authkey",
    "health(warnable=warming-up): ok",
]


def test_filter_suppresses_routine():
    f = MeshLogFilter()
    for line in ROUTINE_SAMPLES:
        assert f.check(line) is None, line
    assert f.suppressed == len(ROUTINE_SAMPLES)


def test_filter_passes_errors_and_unknown():
    f = MeshLogFilter()
    for line in PASS_SAMPLES:
        assert f.check(line) == line, line
    assert f.suppressed == 0


def test_filter_receipt_every_hundred():
    f = MeshLogFilter(receipt_every=10)
    out = [f.check(ROUTINE_SAMPLES[0]) for _ in range(25)]
    assert out[9] == "meshd: 10 routine lines suppressed (FIEF_MESH_VERBOSE=1 for full)"
    assert (
        out[19] == "meshd: 20 routine lines suppressed (FIEF_MESH_VERBOSE=1 for full)"
    )
    assert out[24] is None
    assert f.suppressed == 25


def test_filter_verbose_passes_all():
    f = MeshLogFilter(verbose=True)
    for line in ROUTINE_SAMPLES:
        assert f.check(line) == line, line
    assert f.suppressed == 0


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
        "FIEF_MESH_SYSTEM": "1" if overrides.get("system") else "0",
        "FIEF_MESH_SOCKET": overrides.get("socket", ""),
    }
    if overrides.get("bare"):
        # No hostname/DNS prefs at all: what a bare `--system` sees.
        env.pop("FIEF_MESH_HOSTNAME", None)
    if "accept_dns" in overrides:
        env["FIEF_MESH_ACCEPT_DNS"] = overrides["accept_dns"]
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


def test_build_up_cmd_omits_missing_authkey(tmp_path):
    cfg = _cfg(authkey="")
    cmd = build_up_cmd(tmp_path / "fiefmesh", tmp_path / "sock", cfg)
    assert cmd[:3] == [str(tmp_path / "fiefmesh"), f"--socket={tmp_path}/sock", "up"]
    assert "--hostname=fief-test" in cmd
    assert not any(c.startswith("--authkey=") for c in cmd)


def test_build_up_cmd_system_bare_leaves_prefs_alone(tmp_path):
    cfg = _cfg(authkey="", system=True, bare=True)
    cmd = build_up_cmd(tmp_path / "fiefmesh", tmp_path / "sock", cfg)
    assert cmd == [
        str(tmp_path / "fiefmesh"),
        f"--socket={tmp_path}/sock",
        "up",
    ]


def test_build_up_cmd_system_sends_explicit_prefs(tmp_path):
    cfg = _cfg(authkey="", system=True, hostname="my-node", accept_dns="0")
    cmd = build_up_cmd(tmp_path / "fiefmesh", tmp_path / "sock", cfg)
    assert "--hostname=my-node" in cmd
    assert "--accept-dns=false" in cmd


def test_build_up_cmd_system_accept_dns_opt_in_omits_flag(tmp_path):
    cfg = _cfg(authkey="", system=True, hostname="my-node", accept_dns="1")
    cmd = build_up_cmd(tmp_path / "fiefmesh", tmp_path / "sock", cfg)
    assert "--hostname=my-node" in cmd
    assert "--accept-dns=false" not in cmd


def test_build_up_cmd_isolated_pins_defaults(tmp_path):
    cfg = _cfg()
    cmd = build_up_cmd(tmp_path / "fiefmesh", tmp_path / "sock", cfg)
    assert "--hostname=fief-test" in cmd
    assert "--accept-dns=false" in cmd


def test_resolve_socket(tmp_path):
    from pathlib import Path

    isolated = _cfg()
    assert resolve_socket(isolated, tmp_path) == tmp_path / "meshd.sock"
    system = _cfg(authkey="", system=True)
    assert resolve_socket(system) == Path(DEFAULT_SYSTEM_SOCKET)
    explicit = _cfg(authkey="", system=True, socket="/tmp/custom.sock")
    assert resolve_socket(explicit) == Path("/tmp/custom.sock")


def test_run_mesh_system_reuses_login_without_key(monkeypatch, tmp_path):
    from fief import mesh_run

    cfg = _cfg(authkey="", system=True)
    calls: list[list[str]] = []

    class _R:
        def __init__(self, rc=0):
            self.returncode = rc
            self.stdout = "{}"
            self.stderr = ""

    monkeypatch.setattr(
        mesh_run, "ensure_mesh", lambda *a, **k: (tmp_path / "cli", tmp_path / "d")
    )
    monkeypatch.setattr(mesh_run, "_system_cli", lambda: tmp_path / "sys-cli")
    monkeypatch.setattr(mesh_run, "_wait_running", lambda *a, **k: None)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _R(0)

    monkeypatch.setattr(mesh_run.subprocess, "run", fake_run)
    assert mesh_run.run_mesh(cfg, log=lambda m: None) == 0
    up = calls[0]
    assert up[0] == str(tmp_path / "sys-cli")
    assert up[1] == f"--socket={DEFAULT_SYSTEM_SOCKET}"
    assert "up" in up
    assert not any(c.startswith("--authkey=") for c in up)


def test_run_mesh_system_passes_key_when_set(monkeypatch, tmp_path):
    from fief import mesh_run

    cfg = _cfg(system=True)  # default TESTKEY present
    seen: list[list[str]] = []

    class _R:
        returncode = 0
        stdout = "{}"
        stderr = ""

    monkeypatch.setattr(
        mesh_run, "ensure_mesh", lambda *a, **k: (tmp_path / "cli", tmp_path / "d")
    )
    monkeypatch.setattr(mesh_run, "_system_cli", lambda: tmp_path / "sys-cli")
    monkeypatch.setattr(mesh_run, "_wait_running", lambda *a, **k: None)

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return _R()

    monkeypatch.setattr(mesh_run.subprocess, "run", fake_run)
    assert mesh_run.run_mesh(cfg, log=lambda m: None) == 0
    assert "--authkey=tskey-auth-TESTKEY" in seen[0]


def test_cmd_down_system_never_kills_pid(monkeypatch, tmp_path):
    import types

    from fief import mesh_run

    rundir = tmp_path / "run"
    rundir.mkdir()
    (rundir / "meshd.pid").write_text("123456\n")
    cfg = _cfg(authkey="", system=True)
    monkeypatch.setattr(mesh_run, "_system_cli", lambda: tmp_path / "sys-cli")
    monkeypatch.setattr(
        mesh_run,
        "ensure_mesh",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    monkeypatch.setattr(
        mesh_run.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0),
    )
    assert mesh_run.cmd_down(rundir, cfg=cfg) == 0
    assert (rundir / "meshd.pid").exists()  # system service owns the daemon


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


def test_cli_for_system_prefers_path_binary(monkeypatch, tmp_path):
    from fief import mesh_run

    monkeypatch.setattr(mesh_run, "_system_cli", lambda: tmp_path / "sys-cli")
    monkeypatch.setattr(
        mesh_run,
        "ensure_mesh",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    assert mesh_run._cli_for_system(_cfg(system=True), lambda m: None) == (
        tmp_path / "sys-cli"
    )


def test_cli_for_system_fetches_when_absent(monkeypatch, tmp_path):
    from fief import mesh_run

    monkeypatch.setattr(mesh_run, "_system_cli", lambda: None)
    monkeypatch.setattr(
        mesh_run, "ensure_mesh", lambda *a, **k: (tmp_path / "cli", tmp_path / "d")
    )
    assert mesh_run._cli_for_system(_cfg(system=True), lambda m: None) == (
        tmp_path / "cli"
    )


def test_run_system_access_denied_hints_operator(monkeypatch, tmp_path):
    import types

    from fief import mesh_run

    cfg = _cfg(authkey="", system=True)
    monkeypatch.setattr(mesh_run, "_system_cli", lambda: tmp_path / "sys-cli")
    monkeypatch.setattr(
        mesh_run.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=1, stdout="", stderr="Access denied: prefs write access denied"
        ),
    )
    logs: list[str] = []
    assert mesh_run.run_system(cfg, log=logs.append) == 1
    assert any("--operator=$USER" in line for line in logs), logs
    assert any("join failed" in line for line in logs), logs
