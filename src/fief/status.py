"""Stdlib-only status/health backend for the hub (no third-party deps).

Chisel proxies normal browser HTTP here via --backend; /health also serves
as the readiness signal for wait_for_backend().
"""

from __future__ import annotations

import html
import threading
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import boxserve


def make_handler(
    snapshot: Callable[[], tuple[str, str]],
    hub_url: str = "",
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "fief-status"

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path == "/health":
                self._send(200, b"OK\n", "text/plain")
                return
            served = boxserve.route(
                path,
                host=self.headers.get("Host", ""),
                hub_url=hub_url,
                log=lambda msg: None,
            )
            if served is not None:
                code, body, ctype = served
                self._send(code, body, ctype)
                return
            if path != "/":
                self._send(404, b"Not found\n", "text/plain")
                return
            status, logs = snapshot()
            page = (
                "<!doctype html><html><head><title>fief monitor</title></head>"
                "<body><h1>fief monitor hub</h1>"
                "<h2>Status</h2><pre>" + html.escape(status) + "</pre>"
                "<h2>Recent log</h2><pre>" + html.escape(logs) + "</pre>"
                "</body></html>"
            )
            self._send(200, page.encode(), "text/html; charset=utf-8")

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def check_listener(port: str, host: str = "127.0.0.1", timeout: float = 5) -> bool:
    """True when something accepts TCP on host:port. Kernel probe shared
    by the hub status page (exit/egress presence) and mesh serve-target
    checks — stdlib sockets, no new deps."""
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def serve_forever(
    port: int,
    snapshot: Callable[[], tuple[str, str]],
    host: str = "127.0.0.1",
    hub_url: str = "",
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer((host, port), make_handler(snapshot, hub_url))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
