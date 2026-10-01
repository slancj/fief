import os
from pathlib import Path
from unittest import mock

from fief.store import bin_dir, cache_dir, machine_arch, write_executable


def test_cache_dir_override():
    with mock.patch.dict(os.environ, {"FIEF_BIN_DIR": "/opt/b"}):
        assert bin_dir() == Path("/opt/b")


def test_cache_dir_default_and_parts(tmp_path):
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ["XDG_CACHE_HOME"] = str(tmp_path)
        assert bin_dir() == tmp_path / "fief" / "bin"
        assert cache_dir("FIEF_RUN_DIR", "run", "mesh") == (
            tmp_path / "fief" / "run" / "mesh"
        )


def test_cache_dir_home_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert bin_dir() == tmp_path / ".cache" / "fief" / "bin"


def test_machine_arch_lookup():
    table = {"x86_64": "amd64"}
    assert machine_arch(table, "test", "x86_64") == "amd64"


def test_machine_arch_unsupported():
    import pytest

    with pytest.raises(RuntimeError, match="unsupported CPU for test fetch"):
        machine_arch({}, "test", "riscv64")


def test_write_executable(tmp_path):
    target = tmp_path / "sub" / "prog"
    write_executable(target, b"#!/bin/sh\n")
    assert target.read_bytes() == b"#!/bin/sh\n"
    assert target.stat().st_mode & 0o111
