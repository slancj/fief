"""Optional sshd sidecar. Only starts when ALL hold:

- SSH_PUBKEY is set (key-only, user ``fief``)
- running as root (privilege separation + host keys need it)
- an ``sshd`` binary exists (i.e. the Docker image, not HF/Pi-native)

Otherwise skipped with a log line — the hub stays chisel-only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path


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
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    if not Path(sshd).exists():
        emit("sshd binary not found, sshd disabled")
        return False
    if os.geteuid() != 0:
        emit("not root, sshd disabled (needs privilege separation)")
        return False
    home = Path(f"/home/{user}/.ssh")
    try:
        Path("/run/sshd").mkdir(parents=True, exist_ok=True)
        home.mkdir(parents=True, exist_ok=True)
        home.chmod(0o700)
        subprocess.run(["ssh-keygen", "-A"], check=True, capture_output=True)
        (home / "authorized_keys").write_text(ssh_pubkey + "\n")
        (home / "authorized_keys").chmod(0o600)
        subprocess.run(["chown", "-R", f"{user}:{user}", str(home)], check=True)
        subprocess.run(
            [
                sshd,
                "-p",
                ssh_port,
                "-o",
                "ListenAddress=127.0.0.1",
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
    emit(f"sshd on 127.0.0.1:{ssh_port} (user {user}, key-only)")
    return True
