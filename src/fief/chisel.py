"""Fetch + checksum-verify the pinned chisel binary (multi-arch)."""

from __future__ import annotations

import gzip
import io
import os
import platform
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

from .fetch import fetch, verify_sha256

MACHINE_TO_ARCH = {
    "x86_64": "amd64",
    "i386": "386",
    "i686": "386",
    "aarch64": "arm64",
    "armv7l": "armv7",
    "armv6l": "armv6",
}

OS_NAMES = {"linux": "linux", "darwin": "darwin"}


def target_triple(
    os_name: str | None = None, machine: str | None = None
) -> tuple[str, str]:
    """Return (os, arch) as used in chisel release asset names."""
    os_name = os_name or platform.system().lower()
    machine = machine or platform.machine()
    try:
        os_part = OS_NAMES[os_name]
    except KeyError:
        raise RuntimeError(f"unsupported OS for chisel fetch: {os_name!r}")
    try:
        arch = MACHINE_TO_ARCH[machine]
    except KeyError:
        raise RuntimeError(f"unsupported CPU for chisel fetch: {machine!r}")
    return os_part, arch


def asset_name(version: str, os_name: str, arch: str) -> str:
    return f"chisel_{version}_{os_name}_{arch}.gz"


def release_base(version: str) -> str:
    return f"https://github.com/jpillora/chisel/releases/download/v{version}"


def parse_checksum(sums_text: str, gz_name: str) -> str | None:
    """Find the sha256 for exactly gz_name in a checksums.txt body."""
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == gz_name:
            return parts[0]
    return None


def default_bin_dir() -> Path:
    override = os.environ.get("FIEF_BIN_DIR")
    if override:
        return Path(override)
    cache = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(cache) / "fief" / "bin"


def ensure_chisel(
    version: str,
    bin_dir: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    """Download (once) + sha256-verify the chisel binary. Return its path."""
    emit = log or (lambda msg: None)
    os_part, arch = target_triple()
    gz_name = asset_name(version, os_part, arch)
    base = release_base(version)
    target = (bin_dir or default_bin_dir()) / "chisel"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        r = subprocess.run(
            [str(target), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if version in (r.stdout + r.stderr):
            emit(f"chisel {version} already present")
            return target
        emit("chisel version mismatch, re-downloading")
    gz_data = fetch(f"{base}/{gz_name}")
    sums = fetch(f"{base}/chisel_{version}_checksums.txt").decode()
    want = parse_checksum(sums, gz_name)
    if not want:
        raise RuntimeError("checksum entry not found for " + gz_name)
    got = verify_sha256(gz_data, want, gz_name)
    with gzip.open(io.BytesIO(gz_data), "rb") as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    emit(f"chisel {version} verified (sha256 {got[:12]}...)")
    return target
