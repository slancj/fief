import os
from unittest import mock

import pytest

from fief.config import (
    DEFAULT_BACKEND_PORT,
    DEFAULT_PORT,
    _parse_dotenv,
    client_config_from_env,
    default_hub_url,
    ensure_local_env,
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
        assert cfg.system is False
        assert cfg.socket is None
        assert cfg.hostname_set is True
        assert cfg.accept_dns_set is False


def test_mesh_config_explicitness_bits():
    env = _env()
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = mesh_config_from_env()
        assert cfg.hostname_set is False
        assert cfg.accept_dns_set is False
        assert cfg.hostname == "fief-node"
    env = _env(FIEF_MESH_HOSTNAME="", FIEF_MESH_ACCEPT_DNS="1")
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = mesh_config_from_env()
        assert cfg.hostname == "fief-node"  # empty falls back
        assert cfg.hostname_set is False  # empty counts as unset
        assert cfg.accept_dns is True
        assert cfg.accept_dns_set is True


def test_mesh_config_system_mode():
    from pathlib import Path

    import fief.config as config_mod

    env = _env(FIEF_MESH_SYSTEM="1", FIEF_MESH_SOCKET="/tmp/custom.sock")
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(config_mod, "_decrypted_secrets", return_value={}),
    ):
        cfg = mesh_config_from_env()
        assert cfg.system is True
        assert cfg.socket == Path("/tmp/custom.sock")
        assert cfg.authkey == ""


def test_client_config_requires_auth_and_hub():
    import fief.config as config_mod

    with (
        mock.patch.dict(os.environ, _env(FIEF_NO_DOTENV="1"), clear=True),
        mock.patch.object(config_mod, "_decrypted_secrets", return_value={}),
        mock.patch.object(config_mod, "_nodes_space_id", return_value=""),
        pytest.raises(SystemExit, match="CHISEL_AUTH"),
    ):
        client_config_from_env()
    with (
        mock.patch.dict(
            os.environ, _env(CHISEL_AUTH="u:s", FIEF_NO_DOTENV="1"), clear=True
        ),
        mock.patch.object(config_mod, "_decrypted_secrets", return_value={}),
        mock.patch.object(config_mod, "_nodes_space_id", return_value=""),
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


def test_client_config_uses_builtin_hub_url():
    import fief.config as config_mod

    env = _env(CHISEL_AUTH="u:s", FIEF_NO_DOTENV="1")
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(
            config_mod, "_nodes_space_id", return_value="AlisaAjer/fief-monitor"
        ),
    ):
        cfg = client_config_from_env()
        assert cfg.hub_url == "https://AlisaAjer-fief-monitor.hf.space"


def test_client_config_env_hub_url_wins_over_builtin():
    import fief.config as config_mod

    env = _env(
        CHISEL_AUTH="u:s",
        HUB_URL="https://hub.example",
        FIEF_NO_DOTENV="1",
    )
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(
            config_mod, "_nodes_space_id", return_value="AlisaAjer/fief-monitor"
        ),
    ):
        assert client_config_from_env().hub_url == "https://hub.example"


def test_client_config_loads_auth_from_dotenv(tmp_path):
    import fief.config as config_mod

    dotenv = tmp_path / ".env"
    dotenv.write_text("CHISEL_AUTH='user:dotenv-secret'\n")
    env = _env(FIEF_NO_DOTENV="0")
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(config_mod, "_candidate_env_files", return_value=[dotenv]),
        mock.patch.object(config_mod, "_decrypted_secrets", return_value={}),
        mock.patch.object(
            config_mod, "_nodes_space_id", return_value="AlisaAjer/fief-monitor"
        ),
    ):
        cfg = client_config_from_env()
        assert cfg.auth == "user:dotenv-secret"
        assert cfg.hub_url == "https://AlisaAjer-fief-monitor.hf.space"


def test_client_config_loads_auth_from_sops():
    import fief.config as config_mod

    env = _env(FIEF_NO_DOTENV="1")
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(
            config_mod, "_decrypted_secrets", return_value={"CHISEL_AUTH": "user:sops"}
        ),
        mock.patch.object(
            config_mod, "_nodes_space_id", return_value="AlisaAjer/fief-monitor"
        ),
    ):
        assert client_config_from_env().auth == "user:sops"


def test_default_hub_url_chain():
    import fief.config as config_mod

    with (
        mock.patch.dict(
            os.environ,
            _env(HUB_URL="https://hub.example", FIEF_NO_DOTENV="1"),
            clear=True,
        ),
        mock.patch.object(config_mod, "_nodes_space_id", return_value="o/n"),
    ):
        assert default_hub_url() == "https://hub.example"
    with (
        mock.patch.dict(
            os.environ,
            _env(SPACE_ID="owner/name", FIEF_NO_DOTENV="1"),
            clear=True,
        ),
        mock.patch.object(config_mod, "_nodes_space_id", return_value="o/n"),
    ):
        assert default_hub_url() == "https://owner-name.hf.space"
    with (
        mock.patch.dict(os.environ, _env(FIEF_NO_DOTENV="1"), clear=True),
        mock.patch.object(config_mod, "_nodes_space_id", return_value="o/n"),
    ):
        assert default_hub_url() == "https://o-n.hf.space"
    with (
        mock.patch.dict(os.environ, _env(FIEF_NO_DOTENV="1"), clear=True),
        mock.patch.object(config_mod, "_nodes_space_id", return_value=""),
    ):
        assert default_hub_url() == ""


def test_parse_dotenv():
    parsed = _parse_dotenv(
        "# comment\n"
        "CHISEL_AUTH='user:s3cret'\n"
        'HUB_URL="https://hub.example"\n'
        "export FIEF_MESH_HOSTNAME=fief-test\n"
        "EMPTY=\n"
        "not-a-var=1\n"
    )
    assert parsed["CHISEL_AUTH"] == "user:s3cret"
    assert parsed["HUB_URL"] == "https://hub.example"
    assert parsed["FIEF_MESH_HOSTNAME"] == "fief-test"
    assert parsed["EMPTY"] == ""


def test_ensure_local_env_never_overwrites(tmp_path):
    import fief.config as config_mod

    dotenv = tmp_path / ".env"
    dotenv.write_text("CHISEL_AUTH=user:file\nFIEF_MESH_HOSTNAME=fief-file\n")
    env = _env(CHISEL_AUTH="user:env", FIEF_NO_DOTENV="0")
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(config_mod, "_candidate_env_files", return_value=[dotenv]),
    ):
        ensure_local_env()
        assert os.environ["CHISEL_AUTH"] == "user:env"
        assert os.environ["FIEF_MESH_HOSTNAME"] == "fief-file"


def test_mesh_config_autoloads_key_from_sops():
    import fief.config as config_mod

    env = _env(FIEF_NO_DOTENV="1")
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(
            config_mod, "_decrypted_secrets", return_value={"FIEF_MESH_KEY": "k123"}
        ),
    ):
        assert mesh_config_from_env().authkey == "k123"


def test_placeholder_secrets_read_as_missing():
    import fief.config as config_mod

    env = _env(
        FIEF_NO_DOTENV="1",
        FIEF_MESH_KEY="tskey-auth-CHANGE_ME",
        CHISEL_AUTH="user:CHANGE_ME",
    )
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(
            config_mod,
            "_decrypted_secrets",
            return_value={"FIEF_MESH_KEY": "tskey-auth-CHANGE_ME"},
        ),
    ):
        assert mesh_config_from_env().authkey == ""
        assert config_mod._resolve_secret("FIEF_MESH_KEY") == ""
        assert config_mod._resolve_secret("CHISEL_AUTH") == ""
