import os
from unittest import mock

import pytest

from fief.config import (
    DEFAULT_BACKEND_PORT,
    DEFAULT_PORT,
    hub_config_from_env,
    resolve_ui,
    space_public_url,
)


def _env(**overrides):
    base = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("PORT", "BACKEND", "CHISEL", "FIEF", "SSH", "SPACE", "HUB")
        )
    }
    return base | overrides


def test_defaults():
    with mock.patch.dict(os.environ, _env(), clear=True):
        cfg = hub_config_from_env()
        assert cfg.auth == ""
        assert cfg.port == DEFAULT_PORT == "8080"
        assert cfg.backend_port == DEFAULT_BACKEND_PORT == "7861"
        assert cfg.ui == "auto"


def test_from_env():
    env = _env(
        CHISEL_AUTH="u:s",
        PORT="9999",
        FIEF_UI="none",
        SSH_PUBKEY="ssh-ed25519 AAA",
        SSH_PORT="2223",
        SSH_USER="ops",
    )
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = hub_config_from_env()
        assert (cfg.auth, cfg.port, cfg.ui) == ("u:s", "9999", "none")
        assert (cfg.ssh_pubkey, cfg.ssh_port) == ("ssh-ed25519 AAA", "2223")
        assert cfg.ssh_user == "ops"


def test_resolve_ui_explicit():
    assert resolve_ui("basic") == "basic"
    assert resolve_ui("none") == "none"


def test_resolve_ui_auto_without_gradio():
    with mock.patch("fief.config.gradio_available", return_value=False):
        assert resolve_ui("auto") == "basic"


def test_resolve_ui_auto_with_gradio():
    with mock.patch("fief.config.gradio_available", return_value=True):
        assert resolve_ui("auto") == "gradio"


def test_resolve_ui_gradio_missing():
    with (
        mock.patch("fief.config.gradio_available", return_value=False),
        pytest.raises(RuntimeError, match="not installed"),
    ):
        resolve_ui("gradio")


def test_space_public_url():
    assert space_public_url("owner/name") == "https://owner-name.hf.space"
    assert space_public_url("") == "https://<owner>-<space>.hf.space"
