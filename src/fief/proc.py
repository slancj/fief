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

    A still-running child is terminated when ``stop`` fires, and also on
    any exception (so Ctrl-C can't orphan it). Termination is skipped
    when the child already exited.
    """
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            emit(line.rstrip("\n"))
            if stop.is_set():
                break
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
        raise
    if stop.is_set() and proc.poll() is None:
        proc.terminate()
