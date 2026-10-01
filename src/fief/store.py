"""Shared kernel: cache dirs, arch lookup, executable install.

Chisel, mesh, and sshd all resolve per-user cache paths, map
``platform.machine()`` to vendor asset names, and install verified
binaries. One implementation instead of four near-copies.
"""

from __future__ import annotations

import os
import platform
import stat
from collections.abc import Callable, Mapping
from pathlib import Path


def cache_dir(env_override: str, *parts: str) -> Path:
    """Per-user cache subdir, overridable per component via env.

    ``FIEF_BIN_DIR=/x`` replaces the whole path; otherwise
    ``$XDG_CACHE_HOME/fief/...`` (falling back to ``~/.cache``).
    """
    override = os.environ.get(env_override)
    if override:
        return Path(override)
    cache = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(cache).joinpath("fief", *parts)


def bin_dir() -> Path:
    return cache_dir("FIEF_BIN_DIR", "bin")


def machine_arch(
    table: Mapping[str, str],
    what: str,
    machine: str | None = None,
) -> str:
    """Map ``platform.machine()`` through a vendor asset table.

    Tables stay per-vendor (chisel's ``armv7`` is not mesh's ``arm``);
    only the lookup + error contract is shared.
    """
    machine = machine or platform.machine()
    try:
        return table[machine]
    except KeyError:
        raise RuntimeError(f"unsupported CPU for {what} fetch: {machine!r}")


def write_executable(
    path: Path, data: bytes, log: Callable[[str], None] | None = None
) -> Path:
    """Write bytes as an executable file (parents created)."""
    emit = log or (lambda msg: None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    emit(f"installed {path.name}")
    return path
