from fief import hub as hub_mod
from fief.config import HubConfig


def _cfg(**overrides):
    base = {
        "auth": "u:s",
        "port": "8080",
        "backend_port": "7861",
        "ui": "basic",
        "egress_port": "1081",
    }
    return HubConfig(**(base | overrides))


def test_status_text_reports_exit_and_egress(monkeypatch):
    monkeypatch.setattr(hub_mod, "check_listener", lambda port: port == "1080")
    text = hub_mod.status_text(_cfg(), "https://hub.example")
    assert "LAN exit: attached (exit node holding 1080)" in text
    assert "hub egress: down (127.0.0.1:1081 closed)" in text


def test_status_text_absent_exit(monkeypatch):
    monkeypatch.setattr(hub_mod, "check_listener", lambda port: False)
    text = hub_mod.status_text(_cfg(), "https://hub.example")
    assert "LAN exit: absent (no exit node on 1080)" in text
    assert "hub egress: down" in text


def test_cmd_status_all_queries_both(monkeypatch, tmp_path, capsys):
    import types

    from fief import mesh_run

    monkeypatch.setattr(
        mesh_run, "ensure_mesh", lambda *a, **k: (tmp_path / "cli", tmp_path / "d")
    )
    monkeypatch.setattr(mesh_run, "_system_cli", lambda: tmp_path / "sys-cli")
    seen: list[str] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd[1])
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(mesh_run.subprocess, "run", fake_run)
    from fief.config import MeshConfig

    cfg = MeshConfig(system=False)
    assert mesh_run.cmd_status(tmp_path, cfg=cfg, all_nodes=True) == 0
    assert seen == [
        f"--socket={tmp_path}/meshd.sock",
        "--socket=/var/run/tailscale/tailscaled.sock",
    ]
    out = capsys.readouterr().out
    assert "== isolated" in out and "== system" in out


def test_cmd_status_default_single(monkeypatch, tmp_path, capsys):
    import types

    from fief import mesh_run

    monkeypatch.setattr(
        mesh_run, "ensure_mesh", lambda *a, **k: (tmp_path / "cli", tmp_path / "d")
    )
    seen: list[str] = []
    monkeypatch.setattr(
        mesh_run.subprocess,
        "run",
        lambda cmd, **k: seen.append(cmd[1]) or types.SimpleNamespace(returncode=0),
    )
    from fief.config import MeshConfig

    assert mesh_run.cmd_status(tmp_path, cfg=MeshConfig(system=False)) == 0
    assert seen == [f"--socket={tmp_path}/meshd.sock"]
    assert capsys.readouterr().out == ""
