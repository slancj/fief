import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent


def load_sync():
    spec = importlib.util.spec_from_file_location(
        "sync_hf_space", REPO / "scripts" / "sync_hf_space.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sync_creates_then_uploads():
    mod = load_sync()
    api = mock.MagicMock()
    api.upload_folder.return_value = SimpleNamespace(
        commit_url="https://huggingface.co/spaces/o/n/commit/abc"
    )
    logs: list[str] = []
    url = mod.sync(api, "o/n", "dist/space", "sync from fief@abc", logs.append)
    assert url.endswith("/commit/abc")
    api.create_repo.assert_called_once_with(
        "o/n", repo_type="space", space_sdk="gradio", private=True, exist_ok=True
    )
    api.upload_folder.assert_called_once()
    assert any("deployed:" in line for line in logs)


def test_sync_continues_when_create_refused():
    mod = load_sync()
    api = mock.MagicMock()
    api.create_repo.side_effect = Exception("402 Payment Required")
    api.upload_folder.return_value = SimpleNamespace(commit_url="u")
    logs: list[str] = []
    assert mod.sync(api, "o/n", "dist/space", "m", logs.append) == "u"
    api.upload_folder.assert_called_once()
    assert any("warning: create_repo failed" in line for line in logs)


def test_main_requires_space_id(monkeypatch, tmp_path):
    mod = load_sync()
    monkeypatch.delenv("HF_SPACE_ID", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "nodes.toml").write_text(
        "[nodes.pi]\ndeploy = []\nvars = {}\nsecrets = []\n"
    )
    with pytest.raises(SystemExit, match="HF_SPACE_ID"), monkeypatch.context() as m:
        m.setattr(mod, "HERE", tmp_path)
        mod.main(["--folder", "dist/space"])


def test_resolve_space_id_precedence(tmp_path, monkeypatch):
    mod = load_sync()
    nodes = tmp_path / "nodes.toml"
    nodes.write_text('[nodes.hf]\ndeploy = ["hf-space"]\nspace_id = "o/topo"\n')
    monkeypatch.delenv("HF_SPACE_ID", raising=False)
    assert mod.resolve_space_id("", nodes) == "o/topo"
    monkeypatch.setenv("HF_SPACE_ID", "o/env")
    assert mod.resolve_space_id("", nodes) == "o/env"
    assert mod.resolve_space_id("o/cli", nodes) == "o/cli"
    nodes.write_text("[nodes.pi]\ndeploy = []\nvars = {}\n")
    assert mod.resolve_space_id("", nodes) == "o/env"
