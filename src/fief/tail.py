"""Join this machine/container to the tailnet (dedicated userspace node).

Cloud nodes (HF/Render) reboot into fresh identities on ephemeral disks,
so they MUST use an Ephemeral auth key (admin console: key properties)
or dead node entries pile up. Ephemerality comes from the key — there is
no --ephemeral flag on `tailscale up`.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import signal
import stat
import subprocess
import tarfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from .fetch import fetch, verify_sha256
from .log import LogBuffer

LOG = LogBuffer()
STOP = threading.Event()

DEFAULT_TAILSCALE_VERSION = "1.102.4"
DEFAULT_PROXY_PORT = "1055"

MACHINE_TO_ARCH = {
    "x86_64": "amd64",
    "i386": "386",
    "i686": "386",
    "aarch64": "arm64",
    "armv7l": "arm",
    "armv6l": "arm",
}


def target_arch(machine: str | None = None) -> str:
    machine = machine or platform.machine()
    try:
        return MACHINE_TO_ARCH[machine]
    except KeyError:
        raise RuntimeError(f"unsupported CPU for tailscale fetch: {machine!r}")


def tarball_name(version: str, arch: str) -> str:
    return f"tailscale_{version}_{arch}.tgz"


def release_base() -> str:
    return "https://pkgs.tailscale.com/stable"


def default_bin_dir() -> Path:
    override = os.environ.get("FIEF_BIN_DIR")
    if override:
        return Path(override)
    cache = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(cache) / "fief" / "bin"


def default_run_dir() -> Path:
    override = os.environ.get("FIEF_RUN_DIR")
    if override:
        return Path(override)
    cache = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(cache) / "fief" / "run" / "tail"


def ensure_tailscale(
    version: str,
    bin_dir: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[Path, Path]:
    """Download (once) + verify the static tailscale tarball.

    Returns (tailscale, tailscaled) paths.
    """
    emit = log or (lambda msg: None)
    arch = target_arch()
    tgz_name = tarball_name(version, arch)
    target = (bin_dir or default_bin_dir()) / "tailscale-bin"
    target.mkdir(parents=True, exist_ok=True)
    cli, daemon = target / "tailscale", target / "tailscaled"
    marker = target / ".version"
    if cli.exists() and daemon.exists() and marker.read_text().strip() == version:
        emit(f"tailscale {version} already present")
        return cli, daemon
    base = release_base()
    data = fetch(f"{base}/{tgz_name}")
    sums = fetch(f"{base}/{tgz_name}.sha256").decode()
    got = verify_sha256(data, sums, tgz_name)
    with tarfile.open(fileobj=BytesIO(data)) as tf:
        for member in tf.getmembers():
            name = Path(member.name).name
            if name not in ("tailscale", "tailscaled") or not member.isfile():
                continue
            src = tf.extractfile(member)
            assert src is not None
            dest = target / name
            with open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    marker.write_text(version + "\n")
    emit(f"tailscale {version} verified (sha256 {got[:12]}...)")
    return cli, daemon


@dataclass(frozen=True)
class TailConfig:
    authkey: str = ""
    hostname: str = "fief-node"
    proxy: str = ""  # socks5h://127.0.0.1:1081, or "" for direct
    serve_ports: tuple[str, ...] = ()
    advertise_exit: bool = False
    routes: tuple[str, ...] = ()
    accept_dns: bool = False
    extra_args: tuple[str, ...] = ()
    version: str = DEFAULT_TAILSCALE_VERSION
    proxy_port: str = DEFAULT_PROXY_PORT
    run_dir: Path | None = None


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in value.split(",") if p.strip())


def tail_config_from_env() -> TailConfig:
    return TailConfig(
        authkey=os.environ.get("TAILSCALE_AUTHKEY", ""),
        hostname=os.environ.get("TAIL_HOSTNAME", "fief-node"),
        proxy=os.environ.get("TAIL_PROXY", ""),
        serve_ports=_split_list(os.environ.get("TAIL_SERVE", "")),
        advertise_exit=os.environ.get("TAIL_ADVERTISE_EXIT", "0") == "1",
        routes=_split_list(os.environ.get("TAIL_ROUTES", "")),
        accept_dns=os.environ.get("TAIL_ACCEPT_DNS", "0") == "1",
        extra_args=tuple(shlex.split(os.environ.get("TAIL_EXTRA_ARGS", ""))),
        version=os.environ.get("TAILSCALE_VERSION", DEFAULT_TAILSCALE_VERSION),
        proxy_port=os.environ.get("TAIL_PROXY_PORT", DEFAULT_PROXY_PORT),
        run_dir=Path(os.environ["FIEF_RUN_DIR"])
        if os.environ.get("FIEF_RUN_DIR")
        else None,
    )


def daemon_env(proxy: str) -> dict[str, str]:
    """Proxy env for tailscaled. Empty proxy = direct (unchanged env)."""
    if not proxy:
        return {}
    return {
        "ALL_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "HTTP_PROXY": proxy,
        "NO_PROXY": "localhost,127.0.0.1",
    }


def build_daemon_cmd(daemon: Path, run_dir: Path, proxy_port: str) -> list[str]:
    return [
        str(daemon),
        "--tun=userspace-networking",
        f"--socket={run_dir}/tailscaled.sock",
        f"--state={run_dir}/tailscaled.state",
        f"--socks5-server=127.0.0.1:{proxy_port}",
        f"--outbound-http-proxy-listen=127.0.0.1:{proxy_port}",
    ]


def build_up_cmd(cli: Path, sock: Path, cfg: TailConfig) -> list[str]:
    cmd = [
        str(cli),
        f"--socket={sock}",
        "up",
        f"--authkey={cfg.authkey}",
        f"--hostname={cfg.hostname}",
    ]
    if cfg.routes:
        cmd.append(f"--advertise-routes={','.join(cfg.routes)}")
    if cfg.advertise_exit:
        cmd.append("--advertise-exit-node")
    if not cfg.accept_dns:
        cmd.append("--accept-dns=false")
    cmd.extend(cfg.extra_args)
    return cmd


def build_serve_cmds(cli: Path, sock: Path, ports: tuple[str, ...]) -> list[list[str]]:
    return [
        [
            str(cli),
            f"--socket={sock}",
            "serve",
            "--bg",
            f"--tcp={p}",
            f"tcp://127.0.0.1:{p}",
        ]
        for p in ports
    ]


def _wait_socket(sock: Path, proc: subprocess.Popen, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and not STOP.is_set():
        if sock.exists():
            return
        if proc.poll() is not None:
            raise RuntimeError(f"tailscaled exited early ({proc.returncode})")
        time.sleep(0.5)
    raise RuntimeError("tailscaled socket did not appear in time")


def _wait_running(
    cli: Path, sock: Path, timeout: int = 180, log: Callable[[str], None] | None = None
) -> None:
    emit = log or (lambda msg: None)
    deadline = time.time() + timeout
    while time.time() < deadline and not STOP.is_set():
        r = subprocess.run(
            [str(cli), f"--socket={sock}", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        try:
            state = json.loads(r.stdout or "{}").get("BackendState", "")
        except ValueError:
            state = ""
        if state == "Running":
            emit("tailnet: Running")
            return
        emit(f"tailnet: {state or 'waiting'}...")
        time.sleep(5)
    raise RuntimeError("tailnet did not reach Running in time")


def run_node(
    cfg: TailConfig | None = None, log: Callable[[str], None] | None = None
) -> int:
    """Foreground supervisor: daemon + up + serve, with restart backoff."""
    emit = log or LOG.log
    cfg = cfg or tail_config_from_env()
    if not cfg.authkey:
        emit("TAILSCALE_AUTHKEY is required (reusable key; Ephemeral for cloud nodes)")
        return 2
    run_dir = cfg.run_dir or default_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    cli, daemon = ensure_tailscale(cfg.version, log=emit)
    sock = run_dir / "tailscaled.sock"

    backoff = 5
    while not STOP.is_set():
        env = dict(os.environ)
        env.update(daemon_env(cfg.proxy))
        emit(
            f"starting tailscaled (userspace, proxy={'direct' if not cfg.proxy else cfg.proxy})"
        )
        proc = subprocess.Popen(
            build_daemon_cmd(daemon, run_dir, cfg.proxy_port),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        (run_dir / "tailscaled.pid").write_text(str(proc.pid) + "\n")
        assert proc.stdout is not None

        def _drain(p: subprocess.Popen = proc) -> None:
            assert p.stdout is not None
            for line in p.stdout:
                emit("tailscaled | " + line.rstrip())
                if STOP.is_set():
                    break

        drain = threading.Thread(target=_drain, daemon=True)
        drain.start()
        try:
            _wait_socket(sock, proc)
            up = subprocess.run(
                build_up_cmd(cli, sock, cfg),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if up.returncode != 0:
                emit(f"tailscale up failed: {(up.stderr or up.stdout).strip()[-500:]}")
                proc.terminate()
                proc.wait(timeout=30)
                return up.returncode or 1
            _wait_running(cli, sock, log=emit)
            for cmd in build_serve_cmds(cli, sock, cfg.serve_ports):
                r = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30, check=False
                )
                emit(
                    f"serve {' '.join(cmd[4:])}: {'ok' if r.returncode == 0 else 'warn: ' + (r.stderr or r.stdout).strip()[-200:]}"
                )
        except RuntimeError as exc:
            emit(str(exc))
            proc.terminate()
            proc.wait(timeout=30)
            return 1
        code = proc.wait()
        if STOP.is_set():
            return 0
        emit(f"tailscaled exited ({code}), restarting in {backoff}s")
        STOP.wait(backoff)
        backoff = min(backoff * 2, 60)
    return 0


def cmd_status(run_dir: Path | None = None, json_output: bool = False) -> int:
    rundir = run_dir or default_run_dir()
    cli, _ = ensure_tailscale(
        os.environ.get("TAILSCALE_VERSION", DEFAULT_TAILSCALE_VERSION)
    )
    cmd = [str(cli), f"--socket={rundir}/tailscaled.sock", "status"]
    if json_output:
        cmd.append("--json")
    return subprocess.run(cmd, check=False).returncode


def cmd_down(run_dir: Path | None = None) -> int:
    rundir = run_dir or default_run_dir()
    sock = rundir / "tailscaled.sock"
    pidfile = rundir / "tailscaled.pid"
    cli, _ = ensure_tailscale(
        os.environ.get("TAILSCALE_VERSION", DEFAULT_TAILSCALE_VERSION)
    )
    subprocess.run(
        [str(cli), f"--socket={sock}", "down"],
        capture_output=True,
        check=False,
    )
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, OSError):
            pass
        pidfile.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    from .cli import main as _cli_main

    raise SystemExit(_cli_main(["tail", "status"]))
