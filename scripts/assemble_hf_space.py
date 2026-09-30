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


def assemble(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    (out / "fief").mkdir(parents=True)
    for src in sorted((HERE / "src" / "fief").glob("*.py")):
        shutil.copy2(src, out / "fief" / src.name)
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
