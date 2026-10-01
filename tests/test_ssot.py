"""Single-source-of-truth enforcement (cross-cutting parity tests).

Each test pins one sync point so duplicated knowledge fails CI instead of
drifting: package version, env documentation, node vars, secret inventory,
and CLI docs. Nothing here tests behavior — see the per-module suites.
"""

import re
import tomllib
from pathlib import Path

from fief.config import KNOWN_VARS

REPO = Path(__file__).resolve().parent.parent


def _top_level_keys(path: Path) -> set[str]:
    """Mapping keys of a YAML-ish file without decrypting (sops keeps keys
    plaintext; values stay encrypted at rest)."""
    names = set()
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):", line)
        if m and m.group(1) != "sops":
            names.add(m.group(1))
    return names


def test_package_version_matches_pyproject():
    import fief

    text = (REPO / "pyproject.toml").read_text()
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert m, "no version in pyproject.toml"
    assert fief.__version__ == m.group(1)


def test_env_example_covers_known_vars():
    """Every KNOWN_VAR must appear in .env.example (set or commented) so
    new config lands documented."""
    found = set()
    for line in (REPO / ".env.example").read_text().splitlines():
        m = re.match(r"^#?\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m:
            found.add(m.group(1))
    missing = KNOWN_VARS - found
    assert missing == set(), f".env.example missing: {sorted(missing)}"


def _load_nodes() -> dict:
    with open(REPO / "config" / "nodes.toml", "rb") as f:
        return tomllib.load(f)["nodes"]


def test_nodes_vars_are_known():
    """Every nodes.toml var must be one the code reads — typo'd vars
    deploy fine and silently do nothing."""
    for name, node in _load_nodes().items():
        unknown = set(node.get("vars", {})) - KNOWN_VARS
        assert unknown == set(), f"node {name!r} sets unknown vars: {sorted(unknown)}"


def test_secrets_inventory():
    """Every secret a node requires must exist in secrets.yaml AND the
    example template. Key names are plaintext under sops — no decryption,
    no keys needed."""
    required: set[str] = set()
    for node in _load_nodes().values():
        required.update(node.get("secrets", []))
    assert required, "no node requires any secret?"
    for path in (
        REPO / "config" / "secrets.yaml",
        REPO / "config" / "secrets.example.yaml",
    ):
        missing = required - _top_level_keys(path)
        assert missing == set(), f"{path.name} missing secrets: {sorted(missing)}"


def _registered_commands() -> set[str]:
    from fief.cli import build_parser

    names: set[str] = set()

    def walk(parser) -> None:
        for action in parser._actions:
            if type(action).__name__ == "_SubParsersAction":
                for name, sub in action.choices.items():
                    names.add(name)
                    walk(sub)

    walk(build_parser())
    return names


def test_runbook_covers_commands():
    """Every CLI (sub)command must be mentioned in RUNBOOK.md so new
    commands can't ship undocumented. Mentions only — prose stays manual."""
    text = (REPO / "docs" / "RUNBOOK.md").read_text()
    missing = [c for c in sorted(_registered_commands()) if c not in text]
    assert missing == [], f"RUNBOOK.md never mentions: {missing}"
