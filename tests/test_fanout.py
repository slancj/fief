import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent


def load_fanout():
    spec = importlib.util.spec_from_file_location(
        "fanout", REPO / "scripts" / "fanout.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fanout():
    return load_fanout()


SOPS_BLOB = """CHISEL_AUTH: ENC[AES256_GCM,data:abc,iv:x,tag:y,type:str]
sops:
    age:
        - recipient: age1test
          enc: zzz
"""


def test_check_envelope_ok(tmp_path, fanout):
    f = tmp_path / "secrets.yaml"
    f.write_text(SOPS_BLOB)
    fanout.check_envelope(f)  # no raise


def test_check_envelope_plaintext_rejected(tmp_path, fanout):
    f = tmp_path / "secrets.yaml"
    f.write_text("CHISEL_AUTH: 'user:real-secret-value'\n")
    with pytest.raises(SystemExit, match="no sops age envelope"):
        fanout.check_envelope(f)


def test_check_envelope_missing(tmp_path, fanout):
    with pytest.raises(SystemExit, match="sops config/secrets.yaml"):
        fanout.check_envelope(tmp_path / "nope.yaml")


class FakeHF:
    def __init__(self):
        self.secrets = {}
        self.variables = {}

    def add_space_secret(self, repo_id, key, value):
        self.secrets[key] = value

    def add_space_variable(self, repo_id, key, value):
        self.variables[key] = value


def test_fanout_hf_values_and_vars(fanout):
    api = FakeHF()
    node = {"secrets": ["CHISEL_AUTH"], "vars": {"FIEF_MESH_HOSTNAME": "fief-hf"}}
    assert fanout.fanout_hf(
        api, "o/n", node, {"CHISEL_AUTH": "u:S3CR3T"}, False, lambda m: None
    )
    assert api.secrets == {"CHISEL_AUTH": "u:S3CR3T"}
    assert api.variables == {"FIEF_MESH_HOSTNAME": "fief-hf"}


def test_fanout_hf_missing_secret(fanout):
    with pytest.raises(SystemExit, match="missing"):
        fanout.fanout_hf(
            FakeHF(), "o/n", {"secrets": ["NOPE"]}, {}, False, lambda m: None
        )


def test_fanout_hf_dry_run_leaks_nothing(fanout):
    api = FakeHF()
    node = {"secrets": ["CHISEL_AUTH"], "vars": {}}
    out = io.StringIO()
    with redirect_stdout(out):
        fanout.fanout_hf(api, "o/n", node, {"CHISEL_AUTH": "u:S3CR3T"}, True, print)
    text = out.getvalue()
    assert "CHISEL_AUTH" in text
    assert "u:S3CR3T" not in text
    assert api.secrets == {}


class FakeResp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, fanout, calls):
    def fake(req, timeout=None):
        calls.append((req.method, req.full_url, req.data))
        return FakeResp()

    monkeypatch.setattr(fanout.urllib.request, "urlopen", fake)


def test_fanout_render_puts(fanout, monkeypatch):
    calls: list = []
    _patch_urlopen(monkeypatch, fanout, calls)
    node = {"secrets": ["CHISEL_AUTH"], "vars": {"FIEF_MESH_HOSTNAME": "fief-render"}}
    assert fanout.fanout_render(
        "k", "srv-1", node, {"CHISEL_AUTH": "u:S3CR3T"}, False, lambda m: None
    )
    assert len(calls) == 2
    methods = [c[0] for c in calls]
    assert methods == ["PUT", "PUT"]
    assert calls[0][1].endswith("/services/srv-1/env-vars/CHISEL_AUTH")
    assert json.loads(calls[0][2]) == {"value": "u:S3CR3T"}
    assert calls[1][1].endswith("/services/srv-1/env-vars/FIEF_MESH_HOSTNAME")


def test_fanout_render_missing_secret(fanout):
    with pytest.raises(SystemExit, match="missing"):
        fanout.fanout_render(
            "k", "srv", {"secrets": ["NOPE"]}, {}, False, lambda m: None
        )


def test_fanout_render_dry_run_no_http(fanout, monkeypatch):
    calls: list = []
    _patch_urlopen(monkeypatch, fanout, calls)
    out = io.StringIO()
    with redirect_stdout(out):
        fanout.fanout_render(
            "k",
            "srv-1",
            {"secrets": ["CHISEL_AUTH"]},
            {"CHISEL_AUTH": "u:S3CR3T"},
            True,
            print,
        )
    assert calls == []
    assert "u:S3CR3T" not in out.getvalue()


def test_render_redeploy(fanout, monkeypatch):
    calls: list = []
    _patch_urlopen(monkeypatch, fanout, calls)
    fanout.render_redeploy("k", "srv-1", lambda m: None)
    assert calls == [
        ("POST", "https://api.render.com/v1/services/srv-1/deploys", b"{}")
    ]


def _write_repo_files(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "secrets.yaml").write_text(SOPS_BLOB)
    (tmp_path / "config" / "nodes.toml").write_text(
        '[nodes.hf]\nvars = { FIEF_MESH_HOSTNAME = "fief-hf" }\nsecrets = ["CHISEL_AUTH"]\n'
        '[nodes.render]\nvars = {}\nsecrets = ["CHISEL_AUTH"]\n'
    )
    secrets_json = tmp_path / "secrets.json"
    secrets_json.write_text(json.dumps({"CHISEL_AUTH": "u:S3CR3T"}))
    return secrets_json


def test_main_dry_run_no_creds(tmp_path, monkeypatch, fanout, capsys):
    sj = _write_repo_files(tmp_path)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    with redirect_stdout(io.StringIO()) as buf:
        assert (
            fanout.main(
                [
                    "--secrets-json",
                    str(sj),
                    "--nodes",
                    str(tmp_path / "config" / "nodes.toml"),
                    "--secrets-file",
                    str(tmp_path / "config" / "secrets.yaml"),
                    "--dry-run",
                ]
            )
            == 0
        )
    text = buf.getvalue()
    assert "CHISEL_AUTH" in text and "u:S3CR3T" not in text


def test_main_skips_without_creds(tmp_path, fanout, capsys):
    sj = _write_repo_files(tmp_path)
    import os

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("HF_TOKEN", "HF_SPACE_ID", "RENDER_API_KEY", "RENDER_SERVICE_ID")
    }
    with (
        mock.patch.dict(os.environ, env, clear=True),
        redirect_stdout(io.StringIO()) as buf,
    ):
        assert (
            fanout.main(
                [
                    "--secrets-json",
                    str(sj),
                    "--nodes",
                    str(tmp_path / "config" / "nodes.toml"),
                    "--secrets-file",
                    str(tmp_path / "config" / "secrets.yaml"),
                ]
            )
            == 0
        )
    assert "skipped" in buf.getvalue()
