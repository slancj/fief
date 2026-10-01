import os
import signal
import sys
import threading
import time

from fief.proc import drain, spawn, wire_stop


def test_drain_collects_lines():
    proc = spawn([sys.executable, "-c", "print('one'); print('two')"])
    out: list[str] = []
    drain(proc, out.append, threading.Event())
    assert out == ["one", "two"]
    assert proc.wait(timeout=10) == 0


def test_drain_emits_partial_tail_without_newline():
    proc = spawn([sys.executable, "-c", "print('tail', end='')"])
    out: list[str] = []
    drain(proc, out.append, threading.Event())
    assert out == ["tail"]
    assert proc.wait(timeout=10) == 0


def test_drain_terminates_silent_child_on_stop():
    stop = threading.Event()
    proc = spawn([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(0.2)  # let it start producing nothing
    stop.set()
    drain(proc, lambda line: None, stop)
    assert proc.wait(timeout=10) != 0  # SIGTERM, not natural exit


def test_drain_leaves_exited_child_alone():
    proc = spawn([sys.executable, "-c", "pass"])
    assert proc.wait(timeout=10) == 0
    stop = threading.Event()
    stop.set()
    drain(proc, lambda line: None, stop)  # must not raise on a reaped child


def test_wire_stop_routes_sigterm():
    stop = threading.Event()
    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)
    try:
        wire_stop(stop)
        os.kill(os.getpid(), signal.SIGTERM)
        assert stop.wait(timeout=5)
    finally:
        signal.signal(signal.SIGTERM, prev_term)
        signal.signal(signal.SIGINT, prev_int)
