import os
import stat
from pathlib import Path

from fief.sshd import maybe_start_sshd

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY fief-test"


def _stub_sshd(tmp_path: Path, record: Path) -> Path:
    stub = tmp_path / "sshd"
    stub.write_text(f'#!/bin/sh\necho "$@" > {record}\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return stub


def test_no_pubkey_skips():
    msgs: list[str] = []
    assert maybe_start_sshd("", log=msgs.append) is False
    assert any("not set" in m for m in msgs)


def test_missing_binary_skips(tmp_path, monkeypatch):
    from fief import sshd as sshd_mod

    monkeypatch.setenv("FIEF_SSH_DIR", str(tmp_path / "ssh"))
    monkeypatch.setattr(sshd_mod, "_find_sshd", lambda: None)
    msgs: list[str] = []
    assert maybe_start_sshd(PUBKEY, log=msgs.append) is False
    assert any("not found" in m for m in msgs)


def test_startup_layout_and_args(tmp_path, monkeypatch):
    keydir = tmp_path / "ssh"
    record = tmp_path / "argv"
    monkeypatch.setenv("FIEF_SSH_DIR", str(keydir))
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    _stub_sshd(tmp_path, record)

    msgs: list[str] = []
    assert (
        maybe_start_sshd(PUBKEY, ssh_port="2222", user="fief", log=msgs.append) is True
    )
    assert (keydir / "ssh_host_ed25519_key").exists()
    assert (keydir / "authorized_keys").read_text() == PUBKEY + "\n"
    argv = record.read_text()
    for flag in (
        "-f /dev/null",
        "PidFile=",
        "HostKey=",
        "AuthorizedKeysFile=",
        "StrictModes=no",
        "PasswordAuthentication=no",
        "PubkeyAuthentication=yes",
        "ListenAddress=127.0.0.1",
    ):
        assert flag in argv, flag
    assert any("127.0.0.1:2222" in m for m in msgs)


def test_nonroot_serves_own_user(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        import pytest

        pytest.skip("needs non-root")
    import getpass

    keydir = tmp_path / "ssh"
    record = tmp_path / "argv"
    monkeypatch.setenv("FIEF_SSH_DIR", str(keydir))
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    _stub_sshd(tmp_path, record)

    msgs: list[str] = []
    assert maybe_start_sshd(PUBKEY, user="fief", log=msgs.append) is True
    assert any(getpass.getuser() in m for m in msgs)


def test_keydir_override(tmp_path, monkeypatch):
    from fief.sshd import _keydir

    monkeypatch.setenv("FIEF_SSH_DIR", str(tmp_path / "custom"))
    assert _keydir() == tmp_path / "custom"
    monkeypatch.delenv("FIEF_SSH_DIR")
    assert str(_keydir()).endswith(".cache/fief/ssh") or "fief" in str(_keydir())
