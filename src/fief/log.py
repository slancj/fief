"""Threadsafe in-memory ring buffer for hub logs."""

from __future__ import annotations

import sys
import threading
import time
from collections import deque


class LogBuffer:
    def __init__(self, maxlen: int = 400) -> None:
        self._lines: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        with self._lock:
            self._lines.append(line)
        print(line, file=sys.stderr, flush=True)

    def snapshot(self) -> str:
        with self._lock:
            return "\n".join(self._lines)
