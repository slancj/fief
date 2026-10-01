"""Mesh runtime: daemon/up/serve command builders + foreground supervisor.

Pure builders (no side effects) stay separate from the supervisor loop so
tests cover command shapes without spawning anything. The supervisor runs
the daemon, joins the mesh, serves ports, and restarts with backoff —
terminating the child on stop or exception (never orphaned).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .config import DEFAULT_MESH_VERSION, MeshConfig, mesh_config_from_env
from .log import LogBuffer
from .mesh_fetch import (
    _FLAG_ACCEPT_DNS,
    _FLAG_AUTHKEY,
    _FLAG_EXIT,
    _FLAG_ROUTES,
    _FLAG_TUN,
    _clean,
    ensure_mesh,
)
from .proc import drain, spawn, wire_stop
from .store import cache_dir

LOG = LogBuffer()
STOP = threading.Event()


def default_run_dir() -> Path:
    return cache_dir("FIEF_RUN_DIR", "run", "mesh")


def daemon_env(proxy: str) -> dict[str, str]:
    """Proxy env for the daemon. Empty proxy = direct (unchanged env)."""
    if not proxy:
        return {}
    return {
        "ALL_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "HTTP_PROXY": proxy,
        "NO_PROXY": "localhost,127.0.0.1",
    }


def build_daemon_cmd(daemon: Path, run_dir: Path, proxy_port: str) -> list[str]:
    # --statedir is mandatory, not redundant with --state: without it the
    # daemon has no var root, so SSH host keys (and certs/taildrop) stay
    # disabled ("no var root for ssh keys"). Auto-derivation only kicks in
    # for one specifically-named parent dir, which ours isn't — so pass it
    # explicitly.
    return [
        str(daemon),
        _FLAG_TUN,
        f"--socket={run_dir}/meshd.sock",
        f"--state={run_dir}/meshd.state",
        f"--statedir={run_dir}",
        f"--socks5-server=127.0.0.1:{proxy_port}",
        f"--outbound-http-proxy-listen=127.0.0.1:{proxy_port}",
    ]


def build_up_cmd(cli: Path, sock: Path, cfg: MeshConfig) -> list[str]:
    cmd = [
        str(cli),
        f"--socket={sock}",
        "up",
        f"{_FLAG_AUTHKEY}{cfg.authkey}",
        f"--hostname={cfg.hostname}",
    ]
    if cfg.routes:
        cmd.append(f"{_FLAG_ROUTES}{','.join(cfg.routes)}")
    if cfg.advertise_exit:
        cmd.append(_FLAG_EXIT)
    if cfg.ssh:
        cmd.append("--ssh")
    if not cfg.accept_dns:
        cmd.append(_FLAG_ACCEPT_DNS)
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


def _wait_socket(
    sock: Path, proc: subprocess.Popen, stop: threading.Event, timeout: int = 30
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and not stop.is_set():
        if sock.exists():
            return
        if proc.poll() is not None:
            raise RuntimeError(f"daemon exited early ({proc.returncode})")
        time.sleep(0.5)
    raise RuntimeError("daemon socket did not appear in time")


def _wait_running(
    cli: Path,
    sock: Path,
    stop: threading.Event,
    timeout: int = 180,
    log: Callable[[str], None] | None = None,
) -> None:
    emit = log or (lambda msg: None)
    deadline = time.time() + timeout
    while time.time() < deadline and not stop.is_set():
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
            emit("mesh: Running")
            return
        emit(f"mesh: {state or 'waiting'}...")
        time.sleep(5)
    raise RuntimeError("mesh did not reach Running in time")


def run_mesh(
    cfg: MeshConfig | None = None,
    log: Callable[[str], None] | None = None,
    stop: threading.Event | None = None,
) -> int:
    """Foreground supervisor: daemon + up + serve, with restart backoff."""
    emit = log or LOG.log
    cfg = cfg or mesh_config_from_env()
    stop = stop or STOP
    if threading.current_thread() is threading.main_thread():
        wire_stop(stop)  # hub sidecar runs off-main: hub owns signals there
    if not cfg.authkey:
        emit("FIEF_MESH_KEY is required (reusable key; Ephemeral for cloud nodes)")
        return 2
    run_dir = cfg.run_dir or default_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    cli, daemon = ensure_mesh(cfg.version, log=emit)
    sock = run_dir / "meshd.sock"

    backoff = 5
    proc: subprocess.Popen | None = None
    try:
        while not stop.is_set():
            env = dict(os.environ)
            env.update(daemon_env(cfg.proxy))
            emit(
                f"starting daemon (isolated, proxy={'direct' if not cfg.proxy else cfg.proxy})"
            )
            proc = spawn(build_daemon_cmd(daemon, run_dir, cfg.proxy_port), env=env)
            (run_dir / "meshd.pid").write_text(str(proc.pid) + "\n")

            def _drain(p: subprocess.Popen = proc) -> None:
                drain(p, lambda line: emit("meshd | " + _clean(line)), stop)

            drain_thread = threading.Thread(target=_drain, daemon=True)
            drain_thread.start()
            try:
                _wait_socket(sock, proc, stop)
                up = subprocess.run(
                    build_up_cmd(cli, sock, cfg),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                if up.returncode != 0:
                    emit(
                        f"join failed: {_clean((up.stderr or up.stdout).strip()[-500:])}"
                    )
                    proc.terminate()
                    proc.wait(timeout=30)
                    return up.returncode or 1
                _wait_running(cli, sock, stop, log=emit)
                for cmd in build_serve_cmds(cli, sock, cfg.serve_ports):
                    r = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=30, check=False
                    )
                    detail = _clean((r.stderr or r.stdout).strip()[-200:])
                    emit(
                        f"serve {' '.join(cmd[4:])}: {'ok' if r.returncode == 0 else 'warn: ' + detail}"
                    )
            except RuntimeError as exc:
                emit(_clean(str(exc)))
                proc.terminate()
                proc.wait(timeout=30)
                return 1
            code = proc.wait()
            if stop.is_set():
                return 0
            emit(f"daemon exited ({code}), restarting in {backoff}s")
            stop.wait(backoff)
            backoff = min(backoff * 2, 60)
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()


def cmd_status(run_dir: Path | None = None, json_output: bool = False) -> int:
    rundir = run_dir or default_run_dir()
    cli, _ = ensure_mesh(os.environ.get("FIEF_MESH_VERSION", DEFAULT_MESH_VERSION))
    cmd = [str(cli), f"--socket={rundir}/meshd.sock", "status"]
    if json_output:
        cmd.append("--json")
    return subprocess.run(cmd, check=False).returncode


def cmd_down(run_dir: Path | None = None) -> int:
    rundir = run_dir or default_run_dir()
    sock = rundir / "meshd.sock"
    pidfile = rundir / "meshd.pid"
    cli, _ = ensure_mesh(os.environ.get("FIEF_MESH_VERSION", DEFAULT_MESH_VERSION))
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


def maybe_start_from_env(
    log: Callable[[str], None] | None = None,
    stop: threading.Event | None = None,
) -> bool:
    """Hub sidecar entry: no-op unless the mesh key is configured."""
    emit = log or LOG.log
    if not os.environ.get("FIEF_MESH_KEY", ""):
        return False
    emit("mesh sidecar enabled")
    thread = threading.Thread(
        target=run_mesh, kwargs={"log": emit, "stop": stop or STOP}, daemon=True
    )
    thread.start()
    return True


if __name__ == "__main__":
    raise SystemExit(run_mesh())
