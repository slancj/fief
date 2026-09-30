"""Universal env contract. Everything from env, no secrets in code/image."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.util import find_spec

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
