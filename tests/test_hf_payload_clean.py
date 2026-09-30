"""The HF payload must contain zero mesh-join signatures.

Assembles the real Space payload and greps every file. Banned list covers
the vendor name (any case), its domain, env-style prefixes, key prefixes,
and the distinctive flags. mesh.py SHIPS (scrubbed: sensitive literals stay
base64-encoded, decoded only at runtime) because the hub sidecar is what
joins cloud nodes. Chisel MUST still be present (non-vacuous).
"""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

BANNED = ["tailscale", "tail_", "tskey", "userspace", "pkgs.", "tailnet"]


def _assemble(tmp_path: Path) -> Path:
    spec = importlib.util.spec_from_file_location(
        "assemble", REPO / "scripts" / "assemble_hf_space.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.assemble(tmp_path / "space")


def _payload_files(out: Path) -> list[Path]:
    return sorted(f for f in out.rglob("*") if f.is_file())


def test_no_banned_substrings(tmp_path):
    out = _assemble(tmp_path)
    violations = []
    for f in _payload_files(out):
        text = f.read_text(errors="replace")
        lowered = text.lower()
        for term in BANNED:
            if term in lowered:
                for i, line in enumerate(text.splitlines(), 1):
                    if term in line.lower():
                        violations.append(f"{f.name}:{i}: {term}")
    assert violations == [], violations


def test_excluded_modules_absent(tmp_path):
    out = _assemble(tmp_path)
    names = {f.name for f in _payload_files(out)}
    for banned in (
        "tail.py",
        "cli.py",
        "client.py",
        "config_cmd.py",
        "__main__.py",
    ):
        assert banned not in names, banned
    assert "hub.py" in names and "app.py" in names
    assert "mesh.py" in names, "sidecar must ship or HF nodes never join"


def test_hub_still_functional(tmp_path):
    """The denylist must not gut the hub: chisel + sidecar hook remain."""
    out = _assemble(tmp_path)
    hub = (out / "fief" / "hub.py").read_text()
    assert "chisel" in hub
    assert "mesh_mod" in hub  # neutral lazy hook, ImportError-tolerant
    chisel = (out / "fief" / "chisel.py").read_text()
    assert "chisel_" in chisel
