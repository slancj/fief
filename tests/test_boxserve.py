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
    assert 'HUB="https://owner-name.hf.space"' in sh
    assert "invite blob" in sh
    assert "box.env" in sh
    with pytest.raises(ValueError):
        boxserve.add_sh("http://insecure/")


def test_add_sh_rerun_is_clean_and_noninteractive():
    sh = boxserve.add_sh("https://owner-name.hf.space")
    # identity preserved unless --fresh is passed
    assert "--fresh" in sh
    assert "keeping existing box.env" in sh
    assert "no box.env present and no terminal" in sh
    # staged, then the whole code tree is swapped: no stale file survives
    assert ".stage." in sh
    assert 'rm -rf "$DEST/src" "$DEST/bin"' in sh
    assert "trap 'rm -rf \"$STAGE\"'" in sh
    # preflight gates the kill: a bad bundle fails before anything live dies
    assert sh.index("fief.cli, fief.client") < sh.index('pkill -f "fief mesh"')


def _write_stub(path, body="#!/bin/sh\nexit 1\n"):
    path.write_text(body)
    path.chmod(0o755)


def _fake_hub(root):
    """Loopback hub artifacts: checksummed but inert (no network needed)."""
    import hashlib

    box = root / "box"
    box.mkdir(parents=True)
    pkg = root / "pkg" / "fief"
    pkg.mkdir(parents=True)
    (pkg / "cli.py").write_text("VALUE = 1\n")
    (pkg / "client.py").write_text("VALUE = 2\n")
    fief_tgz = box / "fief.tgz"
    with tarfile.open(fief_tgz, "w:gz") as tf:
        for name in ("cli.py", "client.py"):
            p = pkg / name
            ti = tf.gettarinfo(str(p), arcname=f"fief/{name}")
            ti.mtime = 0
            with open(p, "rb") as f:
                tf.addfile(ti, io.BytesIO(f.read()))
    chisel_bin = box / "chisel-amd64"
    chisel_bin.write_bytes(b"fake-chisel")
    (box / "chisel-arm64").write_bytes(b"fake-chisel")
    mesh_tgz = box / "mesh-amd64.tgz"
    with tarfile.open(mesh_tgz, "w") as tf:
        ti = tarfile.TarInfo("dummy")
        ti.size = 4
        tf.addfile(ti, io.BytesIO(b"junk"))
    (box / "mesh-arm64.tgz").write_bytes(mesh_tgz.read_bytes())
    sums = []
    for fname, data in (
        ("fief-0.0-test.tgz", fief_tgz.read_bytes()),
        ("chisel-amd64", chisel_bin.read_bytes()),
        ("chisel-arm64", chisel_bin.read_bytes()),
        ("mesh-amd64.tgz", mesh_tgz.read_bytes()),
        ("mesh-arm64.tgz", mesh_tgz.read_bytes()),
    ):
        sums.append(f"{hashlib.sha256(data).hexdigest()}  {fname}")
    (box / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    (box / "versions").write_text(
        "CHISEL_VERSION=0.0-test\nMESH_VERSION=0.0-test\nFIEF_VERSION=0.0-test\n"
    )
    return box


def _serve_hub(box_dir):
    import functools
    import http.server
    import threading

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(box_dir.parent)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}
    )
    thread.daemon = True
    thread.start()
    return server


def _run_installer(sh_text, home, extra_path, hub_url):
    import shutil
    import subprocess

    baked = sh_text.replace('HUB="https://hub.invalid"', f'HUB="{hub_url}"')
    assert 'HUB="https://hub.invalid"' in sh_text  # template must carry the sentinel
    script = home / "add.sh"
    script.write_text(baked)
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PATH": extra_path,
    }
    sh_bin = shutil.which("sh") or "/bin/sh"
    return subprocess.run(
        [sh_bin, str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )


def _stub_bin(bindir):
    for name in ("pkill", "systemctl", "crontab"):
        _write_stub(bindir / name)
    _write_stub(bindir / "nohup", "#!/bin/sh\nexit 0\n")


def test_add_sh_refresh_swaps_tree_and_keeps_identity(tmp_path, monkeypatch):
    import os as _os

    hub_box = _fake_hub(tmp_path / "hub")
    server = _serve_hub(hub_box)
    try:
        port = server.server_address[1]
        home = tmp_path / "home"
        dest = home / ".local" / "fief-box"
        (dest / "src").mkdir(parents=True)
        (dest / "bin").mkdir(parents=True)
        (dest / "src" / "old_stale.py").write_text("STALE = 1\n")
        (dest / "bin" / "old_junk").write_text("junk")
        box_env = dest / "box.env"
        box_env.write_text(
            "HUB_URL=https://hub.invalid\nCHISEL_AUTH=u:s\n"
            "FIEF_MESH_KEY=k\nFIEF_MESH_HOSTNAME=fief-box-1\n"
        )
        before = box_env.read_bytes()
        bindir = tmp_path / "stubs"
        bindir.mkdir()
        _stub_bin(bindir)
        monkeypatch.setenv(
            "PATH", str(bindir) + _os.pathsep + _os.environ.get("PATH", "")
        )
        proc = _run_installer(
            boxserve.add_sh("https://hub.invalid"),
            home,
            _os.environ["PATH"],
            f"http://127.0.0.1:{port}",
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        # swap: stale files are gone, the new tree is live
        assert not (dest / "src" / "old_stale.py").exists()
        assert not (dest / "bin" / "old_junk").exists()
        assert (dest / "src" / "fief" / "cli.py").exists()
        assert (dest / "bin" / "chisel").exists()
        assert "FIEF_VERSION=0.0-test" in (dest / "versions").read_text()
        # identity untouched: no blob prompt, box.env byte-identical
        assert box_env.read_bytes() == before
        assert not list(dest.glob(".stage.*"))
    finally:
        server.shutdown()
        server.server_close()


def test_add_sh_failed_download_touches_nothing_live(tmp_path, monkeypatch):
    import os as _os

    home = tmp_path / "home"
    dest = home / ".local" / "fief-box"
    (dest / "src").mkdir(parents=True)
    keep = dest / "src" / "keep.py"
    keep.write_text("KEEP = 1\n")
    bindir = tmp_path / "stubs"
    bindir.mkdir()
    _stub_bin(bindir)
    monkeypatch.setenv("PATH", str(bindir) + _os.pathsep + _os.environ.get("PATH", ""))
    proc = _run_installer(
        boxserve.add_sh("https://hub.invalid"),
        home,
        _os.environ["PATH"],
        "http://127.0.0.1:1",  # nothing listens: curl fails, set -eu aborts
    )
    assert proc.returncode != 0
    assert keep.read_bytes() == b"KEEP = 1\n"
    assert not (dest / "box-run.sh").exists()
    assert not list(dest.glob(".stage.*"))


def test_public_url_prefers_request_host():
    assert boxserve.public_url("owner-name.hf.space", "https://x") == (
        "https://owner-name.hf.space"
    )
    assert boxserve.public_url("evil host!", "https://fallback") == "https://fallback"
    assert boxserve.public_url("", "https://fallback/") == "https://fallback"


def test_source_tgz_is_py_only(tmp_path):
    name, data = boxserve.source_tgz()
    assert name.startswith("fief-") and name.endswith(".tgz")
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        names = tf.getnames()
    assert any(n.endswith("fief/boxserve.py") for n in names)
    assert any(n.endswith("fief/client.py") for n in names)
    assert not any("__pycache__" in n for n in names)
    # The box boots `python -m fief` off hub disk: the bundle must carry
    # the full client runtime (regression: hub-only subset broke boxes
    # with "No module named fief.__main__").
    for need in ("__main__.py", "cli.py", "client.py", "up.py", "config_cmd.py"):
        assert any(n == f"fief/{need}" for n in names), need
    # Same extraction add.sh performs (3.14+ filters by default).
    import sys

    kw = {"filter": "data"} if sys.version_info >= (3, 12) else {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(tmp_path / "src", **kw)
    assert (tmp_path / "src" / "fief" / "client.py").exists()


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
    assert "curl -fsSL https://hub.example/add.sh | sh" in out
    assert "BLOB" in out and "box-1" in out
