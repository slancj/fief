"""`fief config`: read the one-place config (nodes.toml + sops secrets).

- `export --node NAME` renders dotenv (node vars + decrypted secrets) for
  compose/systemd. Redirect to .env (gitignored).
- `get KEY` prints one decrypted secret value (for scripts; keep out of logs).
- `edit` opens config/secrets.yaml in sops (encrypts in place per .sops.yaml).
- `invite [--name NAME]` prints a one-liner + single paste blob that onboards
  a box (`curl $HUB/add.sh | sh`): HUB_URL, CHISEL_AUTH, shared mesh key,
  auto hostname (fief-box-XXXX), mesh SSH on. The blob decodes to dotenv
  lines — keep it out of logs like any secret.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NODES_FILE = REPO_ROOT / "config" / "nodes.toml"
SECRETS_FILE = REPO_ROOT / "config" / "secrets.yaml"


def register(sub: argparse._SubParsersAction) -> None:
    cfg = sub.add_parser("config", help="one-place config (nodes.toml + secrets)")
    cfg_sub = cfg.add_subparsers(dest="config_cmd", required=True)
    export = cfg_sub.add_parser("export", help="render dotenv for a node")
    export.add_argument("--node", required=True)
    export.set_defaults(func=run)
    get = cfg_sub.add_parser("get", help="print one decrypted secret")
    get.add_argument("key")
    get.set_defaults(func=run)
    invite = cfg_sub.add_parser("invite", help="print a one-paste box onboarding blob")
    invite.add_argument("--name", default="", help="box hostname (auto fief-box-XXXX)")
    invite.set_defaults(func=run)
    cfg_sub.add_parser("edit", help="edit secrets.yaml in sops").set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if args.config_cmd == "export":
        return cmd_export(args.node)
    if args.config_cmd == "get":
        return cmd_get(args.key)
    if args.config_cmd == "invite":
        return cmd_invite(args.name)
    return cmd_edit()


def sops_bin() -> str:
    found = shutil.which("sops")
    if not found:
        raise SystemExit(
            "sops binary not found (nix: nix-shell -p sops | apt: see "
            "https://github.com/mozilla/sops/releases)"
        )
    return found


def load_nodes(path: Path | None = None) -> dict:
    import tomllib  # lazy: 3.11+ stdlib, boxes may run 3.10 (see box runbook)

    with open(path or NODES_FILE, "rb") as f:
        return tomllib.load(f)


def assert_encrypted(path: Path | None = None) -> None:
    """Refuse plaintext: the committed secrets file must carry a sops envelope."""
    target = path or SECRETS_FILE
    if not target.exists():
        raise SystemExit(
            f"{target} missing — copy config/secrets.example.yaml over it and "
            "encrypt with `sops config/secrets.yaml`"
        )
    text = target.read_text()
    if not re.search(r"(?m)^sops:", text) or "age:" not in text:
        raise SystemExit(
            f"{target} has no sops age envelope — refusing (encrypt it first)"
        )


def decrypt_secrets(path: Path | None = None) -> dict[str, str]:
    """Decrypt secrets.yaml via sops. Requires age key (SOPS_AGE_KEY/file)."""
    target = path or SECRETS_FILE
    if not target.exists():
        raise SystemExit(
            f"{target} missing — copy config/secrets.example.yaml and encrypt "
            "with `sops config/secrets.yaml`"
        )
    r = subprocess.run(
        [sops_bin(), "--decrypt", "--output-type", "json", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise SystemExit(f"sops decrypt failed: {r.stderr.strip()[-300:]}")
    data = json.loads(r.stdout or "{}")
    return {k: v for k, v in data.items() if not k.startswith("sops")}


#: Roles every node must declare (see config/nodes.toml header).
ROLES = ("hub", "exit", "client")

#: Mesh identity key: only hubs and exits hold one; clients stay keyless.
_MESH_KEY = "FIEF_MESH_KEY"

#: Per-role forbidden node entries (non-empty vars values, or secrets).
#: Exits are strict doors (no serve, SSH closed unless opted in per node);
#: clients are keyless consumers (no serve/advertise/routes/identity).
_ROLE_FORBIDDEN_VARS = {
    "hub": frozenset(),
    "exit": frozenset({"FIEF_MESH_SERVE"}),
    "client": frozenset(
        {
            "FIEF_MESH_SERVE",
            "FIEF_MESH_ADVERTISE_EXIT",
            "FIEF_MESH_ROUTES",
        }
    ),
}
_ROLE_FORBIDDEN_SECRETS = {
    "hub": frozenset(),
    "exit": frozenset(),
    "client": frozenset({_MESH_KEY}),
}


def validate_node_role(node: str, cfg: dict) -> None:
    """Enforce the role contract for one node entry (fail fast, plainly)."""
    role = cfg.get("role", "")
    if role not in ROLES:
        raise SystemExit(
            f"node {node!r} needs role = one of {', '.join(ROLES)} "
            "(see config/nodes.toml header)"
        )
    site = cfg.get("site", "")
    if site is not None and not isinstance(site, str):
        raise SystemExit(f"node {node!r}: site must be a string")
    if isinstance(site, str) and site == "":
        pass  # unset; reserved for grouping exits by LAN later
    forbidden_vars = _ROLE_FORBIDDEN_VARS[role]
    bad_vars = sorted(
        key
        for key, value in cfg.get("vars", {}).items()
        if key in forbidden_vars and str(value).strip() != ""
    )
    if bad_vars:
        raise SystemExit(
            f"node {node!r} (role {role!r}) must not set: {', '.join(bad_vars)}"
        )
    bad_secrets = sorted(set(cfg.get("secrets", [])) & _ROLE_FORBIDDEN_SECRETS[role])
    if bad_secrets:
        raise SystemExit(
            f"node {node!r} (role {role!r}) must not hold secrets: "
            f"{', '.join(bad_secrets)}"
        )


def _shell_quote(value: object) -> str:
    text = str(value)
    if not text or any(c in text for c in " \t\n\"'\\$`!#"):
        return "'" + text.replace("'", "'\"'\"'") + "'"
    return text


def render_dotenv(node: str, nodes: dict, secrets: dict[str, str]) -> str:
    try:
        cfg = nodes["nodes"][node]
    except KeyError:
        raise SystemExit(f"unknown node {node!r} (see config/nodes.toml)")
    validate_node_role(node, cfg)
    lines = [f"# generated by `fief config export --node {node}` — DO NOT COMMIT"]
    for key, value in cfg.get("vars", {}).items():
        lines.append(f"{key}={_shell_quote(value)}")
    for key in cfg.get("secrets", []):
        if key not in secrets:
            raise SystemExit(f"secret {key!r} required by node {node!r} is missing")
        lines.append(f"{key}={_shell_quote(secrets[key])}")
    return "\n".join(lines) + "\n"


def cmd_export(node: str) -> int:
    sys.stdout.write(render_dotenv(node, load_nodes(), decrypt_secrets()))
    return 0


def cmd_get(key: str) -> int:
    secrets = decrypt_secrets()
    if key not in secrets:
        raise SystemExit(f"secret {key!r} not found")
    sys.stdout.write(str(secrets[key]))
    return 0


_HOSTNAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,30}$")


def invite_blob(name: str = "") -> tuple[str, str]:
    """Build (hostname, base64 dotenv blob) for one box onboarding paste.

    Offline formatter: resolves the shared secrets (env/.env/sops) and
    packs them with an auto hostname. The blob decodes to dotenv lines
    that add.sh writes straight to box.env — treat it like a secret.
    """
    from .config import _resolve_secret, default_hub_url

    hostname = name or f"fief-box-{secrets.token_hex(2)}"
    if not _HOSTNAME_RE.match(hostname):
        raise SystemExit(
            f"bad --name {name!r}: lowercase letters, digits, dashes (max 31)"
        )
    auth = _resolve_secret("CHISEL_AUTH")
    if not auth:
        raise SystemExit(
            "CHISEL_AUTH not found (env, .env, or sops secrets.yaml) — "
            "export one first, e.g. `fief config export --node laptop > .env`"
        )
    mesh_key = _resolve_secret("FIEF_MESH_KEY")
    if not mesh_key:
        raise SystemExit(
            "FIEF_MESH_KEY not found — mint a stable reusable tagged key "
            "in the admin console and store it (env or secrets.yaml)"
        )
    hub_url = default_hub_url()
    if not hub_url:
        raise SystemExit(
            "HUB_URL not found (env HUB_URL/SPACE_ID or nodes.toml space_id)"
        )
    lines = [
        "# generated by `fief config invite` — onboarding paste, keep secret",
        f"HUB_URL={_shell_quote(hub_url)}",
        f"CHISEL_AUTH={_shell_quote(auth)}",
        f"FIEF_MESH_KEY={_shell_quote(mesh_key)}",
        f"FIEF_MESH_HOSTNAME={_shell_quote(hostname)}",
        "FIEF_MESH_SSH=1",
    ]
    blob = base64.b64encode(("\n".join(lines) + "\n").encode()).decode()
    return hostname, blob


def cmd_invite(name: str = "") -> int:
    from .config import default_hub_url

    hostname, blob = invite_blob(name)
    hub_url = default_hub_url()
    sys.stdout.write(
        f"on the new box, run:\n  curl {hub_url}/add.sh | sh\n"
        f"paste this blob when asked (box joins as {hostname}):\n{blob}\n"
    )
    return 0


def cmd_edit() -> int:
    return subprocess.run([sops_bin(), str(SECRETS_FILE)], check=False).returncode
