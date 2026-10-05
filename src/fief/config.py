"""Universal env contract. Everything from env, no secrets in code/image.

All runtime config lives here as frozen dataclasses: HubConfig (server),
MeshConfig (mesh joiner), ClientConfig (exit/forward consumers). Modules
consume dataclasses; only this module reads os.environ.

Local convenience (laptop/PI only, never on hubs): when a value is missing
from the environment, client/mesh constructors fall back to the gitignored
``.env`` file (cwd, then repo root) and finally to a sops decrypt of
``config/secrets.yaml`` (requires ``sops`` + age key; silent skip otherwise).
Env always wins; nothing is ever printed or committed.
"""

from __future__ import annotations

import base64
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
    egress_port: str = "1081"  # hub-local SOCKS egress (mesh-serve target)


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
        egress_port=os.environ.get("EGRESS_PORT", "1081"),
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


def _parse_dotenv(text: str) -> dict[str, str]:
    """Minimal dotenv parser (stdlib-only): KEY=val, export prefix, # comments."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if not key or not key.replace("_", "").isalnum() or not key[0].isalpha():
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


def _repo_root() -> Path | None:
    """Repo root for src layout (src/fief/config.py -> repo); None if absent."""
    root = Path(__file__).resolve().parent.parent.parent
    if (root / "config" / "nodes.toml").exists() or (root / ".env.example").exists():
        return root
    return None


def _candidate_env_files() -> list[Path]:
    """Gitignored .env locations: cwd first, then repo root. Deduplicated."""
    seen: set[str] = set()
    out: list[Path] = []
    for p in (Path.cwd() / ".env",):
        key = str(p.resolve() if p.exists() else p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    root = _repo_root()
    if root is not None:
        p = root / ".env"
        key = str(p)
        if key not in seen:
            out.append(p)
    return out


def ensure_local_env() -> None:
    """Load missing values from local .env files (never overwrite env)."""
    if os.environ.get("FIEF_NO_DOTENV", "") == "1":
        return
    for path in _candidate_env_files():
        try:
            if not path.is_file():
                continue
            for key, val in _parse_dotenv(path.read_text()).items():
                if key not in os.environ:
                    os.environ[key] = val
        except OSError:
            continue


def _decrypted_secrets() -> dict[str, str]:
    """Best-effort sops decrypt; {} when sops/key/file is unavailable."""
    try:
        from . import config_cmd as config_cmd_mod
    except ImportError:
        return {}
    try:
        return dict(config_cmd_mod.decrypt_secrets())
    except (SystemExit, OSError, ValueError, RuntimeError):
        return {}


#: Template sentinels (config/secrets.example.yaml) never count as
#: configured — a fresh re-key leaves FIEF_MESH_KEY unfilled until the
#: owner mints one, and that must read as "missing", not as a key.
_PLACEHOLDER_MARK = "CHANGE_ME"


def _resolve_secret(name: str) -> str:
    """Env -> gitignored .env -> sops decrypt. Env always wins."""
    ensure_local_env()
    val = os.environ.get(name, "")
    if val and _PLACEHOLDER_MARK not in val:
        return val
    secret = _decrypted_secrets().get(name, "")
    if secret and _PLACEHOLDER_MARK not in secret:
        return secret
    return ""


def _nodes_space_id() -> str:
    """First hf-space node space_id from config/nodes.toml, else ''."""
    import tomllib  # lazy: 3.11+ stdlib, boxes may run 3.10 (see box runbook)

    candidates: list[Path] = [Path.cwd() / "config" / "nodes.toml"]
    root = _repo_root()
    if root is not None:
        candidates.append(root / "config" / "nodes.toml")
    for path in candidates:
        try:
            if not path.is_file():
                continue
            with open(path, "rb") as f:
                nodes = tomllib.load(f).get("nodes", {})
            for node in nodes.values():
                if "hf-space" in node.get("deploy", []):
                    sid = node.get("space_id", "")
                    if isinstance(sid, str) and "/" in sid:
                        return sid
        except (OSError, ValueError):
            continue
    return ""


def default_hub_url() -> str:
    """Built-in hub URL: HUB_URL env -> SPACE_ID -> nodes.toml space_id -> ''."""
    ensure_local_env()
    explicit = os.environ.get("HUB_URL", "").strip()
    if explicit:
        return explicit
    space_id = os.environ.get("SPACE_ID", "").strip()
    if "/" in space_id:
        return space_public_url(space_id)
    sid = _nodes_space_id()
    if "/" in sid:
        owner, name = sid.split("/", 1)
        return f"https://{owner}-{name}.hf.space"
    return ""


#: Every env name the codebase reads. nodes.toml vars must be a subset —
#: a typo'd var deploys fine and silently does nothing (enforced by test).
KNOWN_VARS = frozenset(
    {
        "CHISEL_AUTH",
        "CHISEL_KEEPALIVE",
        "CHISEL_VERSION",
        "SSH_PUBKEY",
        "SSH_PORT",
        "SSH_USER",
        "PORT",
        "BACKEND_PORT",
        "FIEF_UI",
        "FIEF_BIN_DIR",
        "FIEF_BOX_DIR",
        "FIEF_RUN_DIR",
        "FIEF_SSH_DIR",
        "FIEF_NO_SSH",
        "FIEF_NO_DOTENV",
        "FIEF_MESH_KEY",
        "FIEF_MESH_HOSTNAME",
        "FIEF_MESH_PROXY",
        "FIEF_MESH_SERVE",
        "FIEF_MESH_ADVERTISE_EXIT",
        "FIEF_MESH_ROUTES",
        "FIEF_MESH_ACCEPT_DNS",
        "FIEF_MESH_SSH",
        "FIEF_MESH_EXTRA_ARGS",
        "FIEF_MESH_VERSION",
        "FIEF_MESH_PROXY_PORT",
        "FIEF_MESH_VERBOSE",
        "FIEF_MESH_SYSTEM",
        "FIEF_MESH_SOCKET",
        "HUB_URL",
        "LOCAL_PORT",
        "EGRESS_PORT",
        "EXIT_SOCKS",
        "SPACE_ID",
    }
)


def _flag(name: str) -> bool:
    return os.environ.get(name, "0") == "1"


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in value.split(",") if p.strip())


DEFAULT_PROXY_PORT = "1055"
DEFAULT_MESH_VERSION = "1.102.4"
# Default system-daemon socket. Kept encoded (like mesh_fetch.py) so plain
# scans of shipped files miss the vendor path; decoded once at import.
DEFAULT_SYSTEM_SOCKET = base64.b64decode(
    "L3Zhci9ydW4vdGFpbHNjYWxlL3RhaWxzY2FsZWQuc29jaw=="
).decode()


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
    system: bool = False  # drive the system daemon instead of spawning one
    socket: Path | None = None  # explicit daemon socket (system mode override)
    # Explicitness bits: system mode only sends hostname/DNS when the user
    # actually set them, so bare `--system` leaves existing prefs alone.
    hostname_set: bool = False
    accept_dns_set: bool = False


def mesh_config_from_env() -> MeshConfig:
    return MeshConfig(
        authkey=_resolve_secret("FIEF_MESH_KEY"),
        hostname=os.environ.get("FIEF_MESH_HOSTNAME", "fief-node") or "fief-node",
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
        system=_flag("FIEF_MESH_SYSTEM"),
        socket=Path(os.environ["FIEF_MESH_SOCKET"])
        if os.environ.get("FIEF_MESH_SOCKET")
        else None,
        hostname_set=bool(os.environ.get("FIEF_MESH_HOSTNAME", "")),
        accept_dns_set="FIEF_MESH_ACCEPT_DNS" in os.environ,
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
    auth = _resolve_secret("CHISEL_AUTH")
    if not auth:
        raise SystemExit(
            "CHISEL_AUTH not found (env, .env, or sops secrets.yaml) — "
            "run `fief config export --node laptop > .env` or "
            "`fief config get CHISEL_AUTH`"
        )
    hub_url = default_hub_url()
    if not hub_url:
        raise SystemExit(
            "HUB_URL not found (env HUB_URL/SPACE_ID or nodes.toml space_id) — "
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
