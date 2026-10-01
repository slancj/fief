"""Fetch + checksum-verify the pinned chisel binary (multi-arch)."""

from __future__ import annotations

import gzip
import platform
import subprocess
from collections.abc import Callable
from pathlib import Path

from .fetch import fetch, verify_sha256
from .store import bin_dir, machine_arch, write_executable

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
    return os_part, machine_arch(MACHINE_TO_ARCH, "chisel", machine)


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
    return bin_dir()


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
    write_executable(target, gzip.decompress(gz_data), emit)
    emit(f"chisel {version} verified (sha256 {got[:12]}...)")
    return target
