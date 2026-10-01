import os
from pathlib import Path
from unittest import mock

import pytest

from fief.config import (
    DEFAULT_BACKEND_PORT,
    DEFAULT_PORT,
    KNOWN_VARS,
    client_config_from_env,
    hub_config_from_env,
    mesh_config_from_env,
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


def test_nodes_vars_are_known():
    """Every nodes.toml var must be one the code reads — typo'd vars
    deploy fine and silently do nothing."""
    import tomllib

    nodes_file = Path(__file__).resolve().parent.parent / "config" / "nodes.toml"
    with open(nodes_file, "rb") as f:
        nodes = tomllib.load(f)["nodes"]
    for name, node in nodes.items():
        unknown = set(node.get("vars", {})) - KNOWN_VARS
        assert unknown == set(), f"node {name!r} sets unknown vars: {sorted(unknown)}"


def test_mesh_config_from_env():
    env = _env(
        FIEF_MESH_KEY="tskey-auth-X",
        FIEF_MESH_HOSTNAME="fief-test",
        FIEF_MESH_SERVE="1080,1081",
        FIEF_MESH_SSH="1",
        FIEF_MESH_EXTRA_ARGS="--foo bar",
    )
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = mesh_config_from_env()
        assert cfg.authkey == "tskey-auth-X"
        assert cfg.hostname == "fief-test"
        assert cfg.serve_ports == ("1080", "1081")
        assert cfg.ssh is True
        assert cfg.extra_args == ("--foo", "bar")
        assert cfg.advertise_exit is False


def test_client_config_requires_auth_and_hub():
    with (
        mock.patch.dict(os.environ, _env(), clear=True),
        pytest.raises(SystemExit, match="CHISEL_AUTH"),
    ):
        client_config_from_env()
    with (
        mock.patch.dict(os.environ, _env(CHISEL_AUTH="u:s"), clear=True),
        pytest.raises(SystemExit, match="HUB_URL"),
    ):
        client_config_from_env()
    env = _env(CHISEL_AUTH="u:s", HUB_URL="https://hub.example", FIEF_NO_SSH="1")
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = client_config_from_env()
        assert (cfg.auth, cfg.hub_url) == ("u:s", "https://hub.example")
        assert cfg.no_ssh is True
    with mock.patch.dict(os.environ, env, clear=True):
        assert client_config_from_env(no_ssh_flag=True).no_ssh is True
