"""Universal env contract. Everything from env, no secrets in code/image.

All runtime config lives here as frozen dataclasses: HubConfig (server),
MeshConfig (mesh joiner), ClientConfig (exit/forward consumers). Modules
consume dataclasses; only this module reads os.environ.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

DEFAULT_CHISEL_VERSION = "1.12.0"
DEFAULT_KEEPALIVE = "25s"
DEFAULT_PORT = "8080"  # HF shim overrides to 7860
DEFAULT_BACKEND_PORT = "7861"


@dataclass(frozen=True)
class HubConfig:
    auth: str
    port: str = DEFAULT_PORT
    backend_port: str = DEFAULT_BACKEND_PORT
    keepalive: str = DEFAULT_KEEPALIVE
    version: str = DEFAULT_CHISEL_VERSION
    ui: str = "auto"  # auto | gradio | basic | none
    ssh_pubkey: str = ""
    ssh_port: str = "2222"
    ssh_user: str = "fief"  # login user when root; non-root serves its own user


def hub_config_from_env() -> HubConfig:
    return HubConfig(
        auth=os.environ.get("CHISEL_AUTH", ""),
        port=os.environ.get("PORT", DEFAULT_PORT),
        backend_port=os.environ.get("BACKEND_PORT", DEFAULT_BACKEND_PORT),
        keepalive=os.environ.get("CHISEL_KEEPALIVE", DEFAULT_KEEPALIVE),
        version=os.environ.get("CHISEL_VERSION", DEFAULT_CHISEL_VERSION),
        ui=os.environ.get("FIEF_UI", "auto"),
        ssh_pubkey=os.environ.get("SSH_PUBKEY", ""),
        ssh_port=os.environ.get("SSH_PORT", "2222"),
        ssh_user=os.environ.get("SSH_USER", "fief"),
    )


def gradio_available() -> bool:
    return find_spec("gradio") is not None


def resolve_ui(requested: str) -> str:
    """Map FIEF_UI to an actual backend: gradio iff installed, else basic."""
    if requested == "gradio":
        if not gradio_available():
            raise RuntimeError("FIEF_UI=gradio but gradio is not installed")
        return "gradio"
    if requested in ("basic", "none"):
        return requested
    # auto
    return "gradio" if gradio_available() else "basic"


def space_public_url(space_id: str = "") -> str:
    """Best-effort public URL for an HF Space; placeholder when unknown."""
    space_id = space_id or os.environ.get("SPACE_ID", "")
    if "/" in space_id:
        owner, name = space_id.split("/", 1)
        return f"https://{owner}-{name}.hf.space"
    return "https://<owner>-<space>.hf.space"


def _flag(name: str) -> bool:
    return os.environ.get(name, "0") == "1"


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in value.split(",") if p.strip())


DEFAULT_PROXY_PORT = "1055"
DEFAULT_MESH_VERSION = "1.102.4"


@dataclass(frozen=True)
class MeshConfig:
    authkey: str = ""
    hostname: str = "fief-node"
    proxy: str = ""  # socks5h://127.0.0.1:1081, or "" for direct
    serve_ports: tuple[str, ...] = ()
    advertise_exit: bool = False
    routes: tuple[str, ...] = ()
    accept_dns: bool = False
    ssh: bool = False
    extra_args: tuple[str, ...] = ()
    version: str = DEFAULT_MESH_VERSION
    proxy_port: str = DEFAULT_PROXY_PORT
    run_dir: Path | None = None


def mesh_config_from_env() -> MeshConfig:
    return MeshConfig(
        authkey=os.environ.get("FIEF_MESH_KEY", ""),
        hostname=os.environ.get("FIEF_MESH_HOSTNAME", "fief-node"),
        proxy=os.environ.get("FIEF_MESH_PROXY", ""),
        serve_ports=_split_list(os.environ.get("FIEF_MESH_SERVE", "")),
        advertise_exit=_flag("FIEF_MESH_ADVERTISE_EXIT"),
        routes=_split_list(os.environ.get("FIEF_MESH_ROUTES", "")),
        accept_dns=_flag("FIEF_MESH_ACCEPT_DNS"),
        ssh=_flag("FIEF_MESH_SSH"),
        extra_args=tuple(shlex.split(os.environ.get("FIEF_MESH_EXTRA_ARGS", ""))),
        version=os.environ.get("FIEF_MESH_VERSION", DEFAULT_MESH_VERSION),
        proxy_port=os.environ.get("FIEF_MESH_PROXY_PORT", DEFAULT_PROXY_PORT),
        run_dir=Path(os.environ["FIEF_RUN_DIR"])
        if os.environ.get("FIEF_RUN_DIR")
        else None,
    )


@dataclass(frozen=True)
class ClientConfig:
    auth: str
    hub_url: str
    keepalive: str = DEFAULT_KEEPALIVE
    version: str = DEFAULT_CHISEL_VERSION
    local_port: str = "1080"
    egress_port: str = "1081"
    exit_socks: str = "socks"
    ssh_port: str = "2222"
    no_ssh: bool = False


def client_config_from_env(no_ssh_flag: bool = False) -> ClientConfig:
    auth = os.environ.get("CHISEL_AUTH", "")
    if not auth:
        raise SystemExit("set CHISEL_AUTH, e.g. CHISEL_AUTH='user:...' fief exit")
    hub_url = os.environ.get("HUB_URL", "")
    if not hub_url:
        raise SystemExit(
            "set HUB_URL, e.g. HUB_URL='https://<owner>-<space>.hf.space' fief exit"
        )
    return ClientConfig(
        auth=auth,
        hub_url=hub_url,
        keepalive=os.environ.get("CHISEL_KEEPALIVE", DEFAULT_KEEPALIVE),
        version=os.environ.get("CHISEL_VERSION", DEFAULT_CHISEL_VERSION),
        local_port=os.environ.get("LOCAL_PORT", "1080"),
        egress_port=os.environ.get("EGRESS_PORT", "1081"),
        exit_socks=os.environ.get("EXIT_SOCKS", "socks"),
        ssh_port=os.environ.get("SSH_PORT", "2222"),
        no_ssh=no_ssh_flag or os.environ.get("FIEF_NO_SSH", "") == "1",
    )
