"""Optional sshd sidecar: shell on the hub over the chisel tunnel.

Starts when SSH_PUBKEY is set (key-only auth). Works both as root
(Docker image: serves SSH_USER, default ``fief``) and as non-root
(HF Spaces uid 1000: serves the container user — sshd cannot setuid
without root). Key-only, localhost-only, host keys in the cache dir
(regenerated per boot on ephemeral disks: accept-new on clients).

Consumers reach it via a ``<ssh_port>:127.0.0.1:2222``-style forward
(``fief forward`` adds ``SSH_PORT:127.0.0.1:2222`` by default).
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path


def _keydir() -> Path:
    override = os.environ.get("FIEF_SSH_DIR")
    if override:
        return Path(override)
    cache = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(cache) / "fief" / "ssh"


def _find_sshd() -> Path | None:
    found = shutil.which("sshd")
    cand = Path(found) if found else Path("/usr/sbin/sshd")
    return cand if cand.exists() else None


def maybe_start_sshd(
    ssh_pubkey: str,
    ssh_port: str = "2222",
    user: str = "fief",
    log: Callable[[str], None] | None = None,
) -> bool:
    emit = log or (lambda msg: None)
    if not ssh_pubkey:
        emit("SSH_PUBKEY not set, sshd disabled")
        return False
    sshd = _find_sshd()
    if sshd is None:
        emit("sshd binary not found, sshd disabled")
        return False

    is_root = os.geteuid() == 0
    login_user = user if is_root else getpass.getuser()
    if not is_root and user != login_user:
        emit(f"not root, serving '{login_user}' instead of '{user}'")

    kd = _keydir()
    hostkey = kd / "ssh_host_ed25519_key"
    try:
        kd.mkdir(parents=True, exist_ok=True)
        if not hostkey.exists():
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", str(hostkey), "-N", ""],
                check=True,
                capture_output=True,
            )
        if is_root:
            home = Path(f"/home/{login_user}/.ssh")
            home.mkdir(parents=True, exist_ok=True)
            home.chmod(0o700)
            (home / "authorized_keys").write_text(ssh_pubkey + "\n")
            (home / "authorized_keys").chmod(0o600)
            subprocess.run(
                ["chown", "-R", f"{login_user}:{login_user}", str(home)],
                check=True,
            )
        else:
            # Non-root: keep everything inside the keydir so we never
            # touch the invoking user's real ~/.ssh.
            (kd / "authorized_keys").write_text(ssh_pubkey + "\n")
            (kd / "authorized_keys").chmod(0o600)
        authkeys = (
            f"/home/{login_user}/.ssh/authorized_keys"
            if is_root
            else str(kd / "authorized_keys")
        )
        subprocess.run(
            [
                sshd,
                "-f",
                "/dev/null",  # no /etc/ssh/sshd_config on minimal images
                "-p",
                ssh_port,
                "-o",
                "ListenAddress=127.0.0.1",
                "-o",
                f"PidFile={kd}/sshd.pid",
                "-o",
                f"HostKey={hostkey}",
                "-o",
                f"AuthorizedKeysFile={authkeys}",
                "-o",
                "StrictModes=no",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                "PermitRootLogin=no",
                "-o",
                "PubkeyAuthentication=yes",
            ],
            check=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        emit(f"sshd failed to start ({exc}), continuing chisel-only")
        return False
    emit(f"sshd on 127.0.0.1:{ssh_port} (user {login_user}, key-only)")
    return True
