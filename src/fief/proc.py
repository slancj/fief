"""Supervised subprocesses: spawn, stream output, stop cleanly.

Both the hub (chisel server) and the mesh sidecar (daemon) spawn a
long-lived child, stream its stdout into the log, and must terminate it
on shutdown. One implementation: ``wire_stop`` routes signals to an
event, ``spawn`` starts a piped child, ``drain`` streams lines until EOF
or stop — terminating a still-running child on stop *or* exception, so
children are never orphaned (e.g. KeyboardInterrupt in the main thread).
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping


def wire_stop(stop: threading.Event) -> None:
    """Route SIGTERM/SIGINT to ``stop``. Call from the main thread."""

    def _set(*_args: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _set)
    signal.signal(signal.SIGINT, _set)


def spawn(cmd: list[str], env: Mapping[str, str] | None = None) -> subprocess.Popen:
    """Start ``cmd`` with piped, line-buffered text stdout merged to stderr."""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    proc = subprocess.Popen(
        cmd,
        env=merged,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    return proc


def drain(
    proc: subprocess.Popen,
    emit: Callable[[str], None],
    stop: threading.Event,
) -> None:
    """Stream ``proc`` stdout lines to ``emit`` until EOF or ``stop``.

    Readiness-waited (not blocking ``readline``), so ``stop`` takes effect
    within ~0.2s even for silent children. A still-running child is
    terminated when ``stop`` fires, and also on any exception (so Ctrl-C
    can't orphan it). Termination is skipped when the child already exited.
    """
    assert proc.stdout is not None
    # NOTE: raw os.read on the fd bypasses the BufferedReader — safe only
    # because drain is the sole reader of proc.stdout. Never readline() it
    # elsewhere or buffered bytes would be silently skipped.
    fd = proc.stdout.fileno()
    buf = bytearray()
    try:
        while True:
            if stop.is_set():
                break
            if proc.poll() is not None:
                # Exited: non-blocking flush of whatever is left in the pipe.
                try:
                    while True:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            break
                        buf += chunk
                except OSError:
                    pass
                break
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break  # EOF
            buf += chunk
            while b"\n" in buf:
                line, _, rest = buf.partition(b"\n")
                buf = bytearray(rest)
                emit(bytes(line).decode(errors="replace"))
        if buf:
            emit(bytes(buf).decode(errors="replace"))
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
        raise
    if stop.is_set() and proc.poll() is None:
        proc.terminate()
