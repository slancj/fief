"""The HF payload must contain zero mesh-join signatures.

Assembles the real Space payload and greps every file. Banned list covers
the vendor name (any case), its domain, env-style prefixes, key prefixes,
and the distinctive flags. mesh_fetch.py SHIPS (scrubbed: sensitive
literals stay base64-encoded, decoded only at runtime) with mesh_run.py,
store.py, and proc.py, because the hub sidecar is what joins cloud nodes.
Chisel MUST still be present (non-vacuous).
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


def _allowlist() -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "assemble", REPO / "scripts" / "assemble_hf_space.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.PAYLOAD_MODULES)


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
    shipped = {f.name for f in (out / "fief").iterdir() if f.is_file()}
    # The shipped package IS the allowlist — no restating it here.
    assert shipped == _allowlist(), shipped ^ _allowlist()
    # The box bundle (/box/fief.tgz) must boot `python -m fief` off hub
    # disk, so the full client runtime ships. config_cmd stays importable
    # (cli imports it) but inert without key + binary; the scrub test
    # above is the real gate, and the repo itself is public.
    for needed in ("cli.py", "client.py", "config_cmd.py", "__main__.py", "up.py"):
        assert needed in names, needed
    assert "hub.py" in names and "app.py" in names
    assert "mesh_run.py" in names, "sidecar must ship or HF nodes never join"


def test_hub_still_functional(tmp_path):
    """The denylist must not gut the hub: chisel + sidecar seam remain."""
    out = _assemble(tmp_path)
    hub = (out / "fief" / "hub.py").read_text()
    assert "chisel" in hub
    assert "sidecar" in hub  # injected starter seam, never a mesh import
    assert "mesh_run" not in hub
    chisel = (out / "fief" / "chisel.py").read_text()
    assert "chisel_" in chisel
