"""Two-part architecture enforcement (tunnel vs mesh vs kernel).

`fief` is one binary with two parts plus a shared kernel:

- TUNNEL (chisel side): hub, client, chisel, egress, sshd
- MESH (tailnet side): mesh_run, mesh_fetch
- KERNEL (owned by neither): config, store, fetch, proc, log, status,
  ui_gradio, config_cmd, __init__
- WIRING (may compose parts): cli, __main__, up

Rules, checked by parsing imports (function-level imports count — AST
sees them too):

1. Tunnel never imports mesh, mesh never imports tunnel. The hub
   sidecar is a seam, not an import: wiring injects a starter callable
   into ``hub.main``; ``hub.py`` must not name any mesh module.
2. Kernel never imports any part or wiring module.
3. Only wiring modules may import from both parts.

Update the sets below when adding modules — this test is the spec.
"""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "src" / "fief"

TUNNEL = frozenset({"hub", "client", "chisel", "egress", "sshd"})
MESH = frozenset({"mesh_run", "mesh_fetch"})
KERNEL = frozenset(
    {
        "__init__",
        "config",
        "store",
        "fetch",
        "proc",
        "log",
        "status",
        "ui_gradio",
        "config_cmd",
    }
)
WIRING = frozenset({"cli", "__main__", "up"})


def _package_imports(path: Path) -> set[str]:
    """Top-level package modules imported by ``path`` (any depth)."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module in (None, ""):
                # `from . import foo` — a submodule, or a symbol re-exported
                # by __init__ (e.g. __version__): only files count as deps.
                for a in node.names:
                    name = a.name.split(".")[0]
                    if (PKG / f"{name}.py").exists():
                        found.add(name)
                    else:
                        found.add("__init__")
            elif node.level == 1 and node.module:
                # `from .foo import ...` — foo is the dependency
                found.add(node.module.split(".")[0])
            elif node.level == 0 and node.module and node.module.startswith("fief"):
                parts = node.module.split(".")
                found.add(parts[1] if len(parts) > 1 else parts[0])
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "fief" or a.name.startswith("fief."):
                    parts = a.name.split(".")
                    found.add(parts[1] if len(parts) > 1 else parts[0])
    return found


def _part_of(mod: str) -> str:
    if mod in TUNNEL:
        return "tunnel"
    if mod in MESH:
        return "mesh"
    if mod in KERNEL:
        return "kernel"
    if mod in WIRING:
        return "wiring"
    raise AssertionError(f"tests/test_arch.py must classify new module: {mod!r}")


def test_all_modules_classified():
    mods = {p.stem for p in PKG.glob("*.py")}
    known = TUNNEL | MESH | KERNEL | WIRING
    assert mods == known, f"unclassified modules: {sorted(mods ^ known)}"


def test_parts_never_import_each_other():
    violations = []
    for mod in TUNNEL | MESH:
        own = _part_of(mod)
        for dep in _package_imports(PKG / f"{mod}.py"):
            if _part_of(dep) in ("tunnel", "mesh") and _part_of(dep) != own:
                violations.append(f"{mod}.py imports {dep} ({own} -> cross-part)")
    assert violations == [], violations


def test_kernel_imports_only_kernel():
    violations = []
    for mod in KERNEL:
        for dep in _package_imports(PKG / f"{mod}.py"):
            if _part_of(dep) != "kernel":
                violations.append(f"kernel {mod}.py imports {dep}")
    assert violations == [], violations


def test_only_wiring_composes_parts():
    violations = []
    for mod in TUNNEL | MESH | KERNEL:
        parts_touched = {
            _part_of(dep)
            for dep in _package_imports(PKG / f"{mod}.py")
            if _part_of(dep) in ("tunnel", "mesh")
        }
        if len(parts_touched) > 1:
            violations.append(f"{mod}.py composes both parts: {sorted(parts_touched)}")
    assert violations == [], violations
