#!/usr/bin/env python3
"""Assemble the HF Space payload from this repo.

Copies the single source of truth (src/fief) plus the thin shim
(hf-space/app.py), requirements and README into an output dir that the
deploy workflow uploads with upload_folder().

Usage: python scripts/assemble_hf_space.py --out dist/space
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

# Explicit allowlist: ONLY these modules ship to HF. The mesh joiner ships
# as mesh_fetch (scrubbed literals) + mesh_run (supervisor), plus store and
# proc which they build on (see tests/test_hf_payload_clean.py), because the
# hub sidecar is what joins cloud nodes to the mesh. The box bundle served
# at /box/fief.tgz needs the FULL client runtime (cli/client/up/__main__
# plus config_cmd, which cli imports) — the repo is public and these files
# are scrub-clean, so serving them adds no exposure; the scrub test below
# stays the real gate.
PAYLOAD_MODULES = (
    "__init__.py",
    "__main__.py",
    "boxserve.py",
    "chisel.py",
    "cli.py",
    "client.py",
    "config.py",
    "config_cmd.py",
    "egress.py",
    "fetch.py",
    "hub.py",
    "log.py",
    "mesh_fetch.py",
    "mesh_run.py",
    "proc.py",
    "sshd.py",
    "status.py",
    "store.py",
    "ui_gradio.py",
    "up.py",
)


def assemble(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    (out / "fief").mkdir(parents=True)
    for name in PAYLOAD_MODULES:
        src = HERE / "src" / "fief" / name
        assert src.exists(), f"payload module missing: {name}"
        shutil.copy2(src, out / "fief" / name)
    for name in ("app.py", "requirements.txt", "README.md"):
        shutil.copy2(HERE / "hf-space" / name, out / name)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="dist/space", type=Path)
    args = p.parse_args()
    out = assemble(args.out)
    files = sorted(str(f.relative_to(out)) for f in out.rglob("*") if f.is_file())
    print("assembled", out)
    for f in files:
        print(" ", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
