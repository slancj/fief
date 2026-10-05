"""Box onboarding artifacts: routing, installer, invite blob."""

import base64
import io
import tarfile

import pytest

from fief import boxserve


def test_check_arch_and_version():
    assert boxserve.check_arch("arm64") == "arm64"
    with pytest.raises(KeyError):
        boxserve.check_arch("mips")
    assert boxserve.check_version("1.12.0") == "1.12.0"
    with pytest.raises(ValueError):
        boxserve.check_version("https://evil/x")
    with pytest.raises(ValueError):
        boxserve.check_version("../x")


def test_asset_urls_are_pinned_not_user_controlled():
    url, name = boxserve.chisel_asset("1.12.0", "arm64")
    assert url.endswith("chisel_1.12.0_linux_arm64.gz")
    assert name == "chisel_1.12.0_linux_arm64.gz"
    url, name = boxserve.mesh_asset("1.102.4", "arm64")
    assert url.endswith("_1.102.4_arm.tgz")
    with pytest.raises(KeyError):
        boxserve.chisel_asset("1.12.0", "mips")


def test_shipped_files_carry_no_vendor_signatures():
    """Mirror of the HF payload scrub test: boxserve ships to the Space,
    so mesh vendor literals must stay encoded (decoded only at runtime)."""
    with open(boxserve.__file__) as f:
        text = f.read()
    lowered = text.lower()
    for term in ["tailscale", "tail_", "tskey", "userspace", "pkgs.", "tailnet"]:
        assert term not in lowered, term


def test_add_sh_bakes_hub_and_rejects_plain_http():
    sh = boxserve.add_sh("https://owner-name.hf.space")
    assert 'HUB="${1:-https://owner-name.hf.space}"' in sh
    assert "invite blob" in sh
    assert "box.env" in sh
    with pytest.raises(ValueError):
        boxserve.add_sh("http://insecure/")


def test_public_url_prefers_request_host():
    assert boxserve.public_url("owner-name.hf.space", "https://x") == (
        "https://owner-name.hf.space"
    )
    assert boxserve.public_url("evil host!", "https://fallback") == "https://fallback"
    assert boxserve.public_url("", "https://fallback/") == "https://fallback"


def test_source_tgz_is_py_only():
    name, data = boxserve.source_tgz()
    assert name.startswith("fief-") and name.endswith(".tgz")
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        names = tf.getnames()
    assert any(n.endswith("fief/boxserve.py") for n in names)
    assert any(n.endswith("fief/client.py") for n in names)
    assert not any("__pycache__" in n for n in names)


def test_route_unknown_falls_through():
    assert boxserve.route("/health") is None
    assert boxserve.route("/") is None
    assert boxserve.route("/box/chisel-mips") is None
    assert boxserve.route("/box/../secret") is None


def test_route_add_sh_and_versions_need_no_network():
    code, body, ctype = boxserve.route("/add.sh", host="h.hf.space")
    assert code == 200 and ctype == "text/x-shellscript"
    assert b"https://h.hf.space" in body
    code, body, ctype = boxserve.route("/box/versions")
    assert code == 200
    text = body.decode()
    assert "CHISEL_VERSION=" in text and "MESH_VERSION=" in text


def test_route_binaries_fetch_once_then_cache(tmp_path, monkeypatch):
    """Upstream fetch happens once; cache hits do no I/O (no network)."""
    import gzip as _gzip

    from fief import boxserve as mod

    calls = {"n": 0}

    def fake_fetch(url, timeout=120):
        calls["n"] += 1
        if url.endswith("checksums.txt"):
            return f"{'y' * 64}  chisel_1.12.0_linux_amd64.gz\n".encode()
        if url.endswith(".sha256"):
            return (b"z" * 64 + b"  mesh.tgz\n").decode().encode()
        if url.endswith(".tgz"):
            return b"fake-mesh-tarball"
        return _gzip.compress(b"fake-chisel-binary")

    monkeypatch.setattr(mod, "fetch", fake_fetch)
    monkeypatch.setattr(mod, "verify_sha256", lambda data, want, what: "ok")
    monkeypatch.setattr(mod, "box_dir", lambda: tmp_path)
    logs: list[str] = []
    first = mod.get_chisel("1.12.0", "amd64", log=logs.append)
    assert first.exists()
    n_after_first = calls["n"]
    assert mod.get_chisel("1.12.0", "amd64", log=logs.append) == first
    assert calls["n"] == n_after_first  # cache hit: no new fetch
    mesh = mod.get_mesh_tgz("1.102.4", "amd64", log=logs.append)
    assert mesh.exists() and mesh.read_bytes() == b"fake-mesh-tarball"


def test_invite_blob_round_trip(monkeypatch):
    import fief.config_cmd as cmd

    monkeypatch.setattr(
        "fief.config._resolve_secret",
        lambda name: {"CHISEL_AUTH": "u:s", "FIEF_MESH_KEY": "tskey-x"}[name],
    )
    monkeypatch.setattr("fief.config.default_hub_url", lambda: "https://hub.example")
    hostname, blob = cmd.invite_blob()
    assert hostname.startswith("fief-box-")
    env = {}
    for line in base64.b64decode(blob).decode().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("'")
    assert env["HUB_URL"] == "https://hub.example"
    assert env["CHISEL_AUTH"] == "u:s"
    assert env["FIEF_MESH_KEY"] == "tskey-x"
    assert env["FIEF_MESH_HOSTNAME"] == hostname
    assert env["FIEF_MESH_SSH"] == "1"

    hostname, _ = cmd.invite_blob("my-box-1")
    assert hostname == "my-box-1"
    with pytest.raises(SystemExit):
        cmd.invite_blob("BAD NAME!")


def test_invite_missing_secrets_fail_plainly(monkeypatch):
    import fief.config_cmd as cmd

    monkeypatch.setattr("fief.config._resolve_secret", lambda name: "")
    with pytest.raises(SystemExit, match="CHISEL_AUTH"):
        cmd.invite_blob()


def test_config_invite_cli_parses_and_dispatches(capsys):
    from unittest import mock

    import fief.config_cmd as cmd
    from fief.cli import build_parser

    args = build_parser().parse_args(["config", "invite"])
    assert args.config_cmd == "invite" and args.name == ""
    assert callable(args.func)
    args = build_parser().parse_args(["config", "invite", "--name", "box-1"])
    assert args.name == "box-1"
    with mock.patch.object(cmd, "invite_blob", return_value=("box-1", "BLOB")):
        import fief.config as config_mod

        with mock.patch.object(
            config_mod, "default_hub_url", return_value="https://hub.example"
        ):
            assert cmd.cmd_invite("box-1") == 0
    out = capsys.readouterr().out
    assert "curl https://hub.example/add.sh | sh" in out
    assert "BLOB" in out and "box-1" in out
