"""Mesh runtime: daemon/up/serve command builders + foreground supervisor.

Pure builders (no side effects) stay separate from the supervisor loop so
tests cover command shapes without spawning anything. The supervisor runs
the daemon, joins the mesh, serves ports, and restarts with backoff —
terminating the child on stop or exception (never orphaned).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .config import (
    DEFAULT_MESH_VERSION,
    DEFAULT_SYSTEM_SOCKET,
    MeshConfig,
    mesh_config_from_env,
)
from .log import LogBuffer
from .mesh_fetch import (
    _BRAND,
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


#: Daemon lines positively identified as routine chatter (observed volume:
#: hundreds/hour — endpoint churn, keepalives, cache/config notices).
#: Everything NOT listed here passes through, so unknown future lines
#: (including novel failure modes) stay visible by default.
_ROUTINE_PATTERNS = (
    r"^magicsock:",
    r"^derphttp\.",
    r"^(dns|tsdial|peerapi|wgengine|control):",
    r"^netmap:",
    r"^update netmap cache:",
    r"^taildrop:",
    r"^offline auto-update:",
    r"cannot fetch existing TKA state",
)
_ROUTINE_RES = tuple(re.compile(p, re.IGNORECASE) for p in _ROUTINE_PATTERNS)

#: Any line carrying these always passes — errors must never be silenced
#: by a routine pattern above.
_ERROR_RE = re.compile(
    r"error|fail|warn|denied|refus|unable|invalid|expired|panic|fatal"
    r"|reject|timeout|reset|broken|down|unhealthy|degraded|blocked",
    re.IGNORECASE,
)


class MeshLogFilter:
    """Daemon-log policy: silence known-routine, always show errors+unknown.

    Suppressed lines are counted; every ``receipt_every``-th one emits a
    summary so healthy silence is distinguishable from a dead daemon.
    ``verbose`` restores full passthrough (FIEF_MESH_VERBOSE=1).
    """

    def __init__(self, verbose: bool = False, receipt_every: int = 100) -> None:
        self.verbose = verbose
        self.receipt_every = receipt_every
        self.suppressed = 0

    def check(self, line: str) -> str | None:
        """Return the line to emit, a receipt line, or None to drop."""
        if self.verbose or _ERROR_RE.search(line) is not None:
            return line
        if any(rx.search(line) for rx in _ROUTINE_RES):
            self.suppressed += 1
            if self.suppressed % self.receipt_every == 0:
                return (
                    f"meshd: {self.suppressed} routine lines suppressed "
                    "(FIEF_MESH_VERBOSE=1 for full)"
                )
            return None
        return line


def register(sub: argparse._SubParsersAction) -> None:
    mesh = sub.add_parser("mesh", help="mesh node via fief")
    mesh_sub = mesh.add_subparsers(dest="mesh_cmd", required=True)
    up = mesh_sub.add_parser(
        "up", help="join the mesh (foreground supervisor, or system daemon)"
    )
    up.add_argument(
        "--system",
        action="store_true",
        help="drive the system daemon (reuse its login, no new node)",
    )
    up.add_argument("--socket", default="", help="daemon socket (system mode)")
    up.set_defaults(func=run)
    down = mesh_sub.add_parser("down", help="leave + stop the node")
    down.add_argument("--system", action="store_true")
    down.add_argument("--socket", default="")
    down.set_defaults(func=run)
    status = mesh_sub.add_parser("status", help="mesh status")
    status.add_argument("--json", action="store_true")
    status.add_argument("--system", action="store_true", help="query the system daemon")
    status.add_argument("--socket", default="")
    status.add_argument(
        "--all",
        action="store_true",
        help="query the isolated and system daemons, labeled",
    )
    status.set_defaults(func=run)


def apply_cli_overrides(cfg: MeshConfig, args: argparse.Namespace) -> MeshConfig:
    """Fold --system/--socket CLI flags over the env-derived config."""
    import dataclasses

    system = cfg.system or bool(getattr(args, "system", False))
    socket = cfg.socket
    if getattr(args, "socket", ""):
        socket = Path(args.socket)
    if system != cfg.system or socket != cfg.socket:
        cfg = dataclasses.replace(cfg, system=system, socket=socket)
    return cfg


def run(args: argparse.Namespace) -> int:
    cfg = apply_cli_overrides(mesh_config_from_env(), args)
    if args.mesh_cmd == "up":
        return run_mesh(cfg, log=LOG.log)
    if args.mesh_cmd == "down":
        return cmd_down(cfg=cfg)
    return cmd_status(cfg=cfg, json_output=args.json, all_nodes=args.all)


def default_run_dir() -> Path:
    return cache_dir("FIEF_RUN_DIR", "run", "mesh")


def _system_cli() -> Path | None:
    """On-PATH mesh CLI (system mode only). Named via the encoded brand so
    shipped files stay free of vendor signatures (see mesh_fetch)."""
    import shutil

    found = shutil.which(_BRAND)
    return Path(found) if found else None


def _cli_for_system(cfg: MeshConfig, emit: Callable[[str], None]) -> Path:
    """System-mode CLI: prefer the installed binary (no download, works
    offline); fetch only when nothing is on PATH."""
    cli = _system_cli()
    if cli is not None:
        return cli
    emit("no system CLI on PATH, fetching (requires network)")
    fetched, _ = ensure_mesh(cfg.version, log=emit)
    return fetched


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


def resolve_socket(cfg: MeshConfig, run_dir: Path | None = None) -> Path:
    """Which daemon socket to drive: explicit override, system default, or
    the isolated run-dir socket."""
    if cfg.socket is not None:
        return cfg.socket
    if cfg.system:
        return Path(DEFAULT_SYSTEM_SOCKET)
    return (run_dir or default_run_dir()) / "meshd.sock"


def build_up_cmd(cli: Path, sock: Path, cfg: MeshConfig) -> list[str]:
    # Authkey is optional: fresh isolated joins need it, but system mode
    # reuses the existing login (and isolated restarts reuse meshd.state).
    # System mode is non-destructive by default: hostname/DNS are only
    # passed when explicitly configured, so a bare `up --system` leaves
    # the node's existing prefs untouched. Isolated joins always pin
    # both (fresh nodes need deterministic prefs).
    cmd = [
        str(cli),
        f"--socket={sock}",
        "up",
    ]
    if cfg.authkey:
        cmd.append(f"{_FLAG_AUTHKEY}{cfg.authkey}")
    if not cfg.system or cfg.hostname_set:
        cmd.append(f"--hostname={cfg.hostname}")
    if cfg.routes:
        cmd.append(f"{_FLAG_ROUTES}{','.join(cfg.routes)}")
    if cfg.advertise_exit:
        cmd.append(_FLAG_EXIT)
    if cfg.ssh:
        cmd.append("--ssh")
    if not cfg.accept_dns and (not cfg.system or cfg.accept_dns_set):
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


def check_serve_target(port: str, emit: Callable[[str], None] | None = None) -> bool:
    """Probe the container-local backend a serve forward points at.

    Serve accepts mesh connections even when the backend is dead (the
    client sees a reset), so verify the listener exists and warn plainly
    instead of leaving a silent void. Uses stdlib sockets, no new deps.
    """
    from .status import check_listener

    log = emit or (lambda msg: None)
    if check_listener(port):
        return True
    log(f"serve target 127.0.0.1:{port} closed (exit node offline?)")
    return False


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
    """Foreground supervisor (isolated) or one-shot configure (system).

    Isolated (default): spawn the per-user daemon + up + serve, with
    restart backoff. System (--system): drive the already-running system
    daemon once (reuse its login; authkey optional) and return.
    """
    emit = log or LOG.log
    cfg = cfg or mesh_config_from_env()
    stop = stop or STOP
    if threading.current_thread() is threading.main_thread():
        wire_stop(stop)  # hub sidecar runs off-main: hub owns signals there
    if cfg.system:
        return run_system(cfg, log=emit, stop=stop)
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
            log_filter = MeshLogFilter(
                verbose=os.environ.get("FIEF_MESH_VERBOSE", "") == "1"
            )

            def _drain(
                p: subprocess.Popen = proc, f: MeshLogFilter = log_filter
            ) -> None:
                def _emit(line: str) -> None:
                    out = f.check(_clean(line))
                    if out is not None:
                        emit("meshd | " + out)

                drain(p, _emit, stop)

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
                for p in cfg.serve_ports:
                    check_serve_target(p, emit)
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


def run_system(
    cfg: MeshConfig,
    log: Callable[[str], None] | None = None,
    stop: threading.Event | None = None,
) -> int:
    """One-shot configure of the system daemon: up + serve, then return.

    Reuses the system login, so no authkey is needed (one is passed
    through when set, for first-time auth). No daemon is spawned, no
    pidfile is written, nothing loops — the system service owns the
    process. FIEF_MESH_PROXY cannot apply here (daemon env is fixed at
    service start); set the proxy on the system service instead.
    """
    emit = log or LOG.log
    stop = stop or STOP
    if cfg.proxy:
        emit("ignoring FIEF_MESH_PROXY in --system mode (set it on the service)")
    cli = _cli_for_system(cfg, emit)
    sock = resolve_socket(cfg)
    up = subprocess.run(
        build_up_cmd(cli, sock, cfg),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if up.returncode != 0:
        raw = (up.stderr or up.stdout).strip()[-500:]
        if "access denied" in raw.lower():
            # Brand rendered at runtime (like mesh_fetch): shipped files
            # must stay free of vendor signatures, but the user needs the
            # real binary name here — the daemon's own hint gets scrubbed.
            emit(
                f"system socket needs operator rights once: "
                f"sudo {_BRAND} set --operator=$USER, then retry unprivileged"
            )
        emit(f"join failed: {_clean(raw)}")
        return up.returncode or 1
    _wait_running(cli, sock, stop, log=emit)
    for cmd in build_serve_cmds(cli, sock, cfg.serve_ports):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        detail = _clean((r.stderr or r.stdout).strip()[-200:])
        emit(
            f"serve {' '.join(cmd[4:])}: {'ok' if r.returncode == 0 else 'warn: ' + detail}"
        )
    for p in cfg.serve_ports:
        check_serve_target(p, emit)
    return 0


def cmd_status(
    run_dir: Path | None = None,
    json_output: bool = False,
    cfg: MeshConfig | None = None,
    all_nodes: bool = False,
) -> int:
    """Query one daemon (default) or both with ``--all`` (labeled)."""
    config = cfg or mesh_config_from_env()
    rundir = run_dir or config.run_dir or default_run_dir()
    targets: list[tuple[str, Path, Path]] = []
    if all_nodes or not config.system:
        cli, _ = ensure_mesh(os.environ.get("FIEF_MESH_VERSION", DEFAULT_MESH_VERSION))
        targets.append(("isolated", cli, rundir / "meshd.sock"))
    if all_nodes or config.system:
        targets.append(
            (
                "system",
                _cli_for_system(config, lambda msg: None),
                config.socket or Path(DEFAULT_SYSTEM_SOCKET),
            )
        )
    rc = 0
    for name, cli, sock in targets:
        if len(targets) > 1:
            print(f"== {name} ({sock})")
        cmd = [str(cli), f"--socket={sock}", "status"]
        if json_output:
            cmd.append("--json")
        code = subprocess.run(cmd, check=False).returncode
        if rc == 0:
            rc = code
    return rc


def cmd_down(
    run_dir: Path | None = None,
    cfg: MeshConfig | None = None,
) -> int:
    config = cfg or mesh_config_from_env()
    rundir = run_dir or config.run_dir or default_run_dir()
    sock = resolve_socket(config, rundir)
    if config.system:
        cli = _cli_for_system(config, lambda msg: None)
    else:
        cli, _ = ensure_mesh(os.environ.get("FIEF_MESH_VERSION", DEFAULT_MESH_VERSION))
    subprocess.run(
        [str(cli), f"--socket={sock}", "down"],
        capture_output=True,
        check=False,
    )
    if config.system:
        return 0  # system service owns the daemon; never kill it
    pidfile = rundir / "meshd.pid"
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
    if os.environ.get("FIEF_MESH_SYSTEM", "") == "1":
        return False  # containers have no system daemon; isolated sidecar only
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
