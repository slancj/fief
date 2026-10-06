"""Stdlib-only status/health backend for the hub (no third-party deps).

Chisel proxies normal browser HTTP here via --backend; /health also serves
as the readiness signal for wait_for_backend().

Multiplexer mode (HF): box artifact paths (/add.sh, /box/*) plus /health
and / are served locally; everything else is reverse-proxied to the UI
port (a normally-launched Gradio demo, so platform scans that walk a
launched app keep working). The pump is framing-agnostic (read till
close), so event-stream responses relay live.
"""

from __future__ import annotations

import html
import threading
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import boxserve

#: Hop-by-hop headers never forwarded to the UI port.
_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",  # http.client recomputes it from the body
        "expect",
    }
)


def _split_target(proxy_to: str) -> tuple[str, int] | None:
    """Parse "host:port" or return None when proxying is off/malformed."""
    if not proxy_to or ":" not in proxy_to:
        return None
    host, _, port = proxy_to.rpartition(":")
    try:
        return host or "127.0.0.1", int(port)
    except ValueError:
        return None


def make_handler(
    snapshot: Callable[[], tuple[str, str]],
    hub_url: str = "",
    proxy_to: str = "",
) -> type[BaseHTTPRequestHandler]:
    target = _split_target(proxy_to)

    class Handler(BaseHTTPRequestHandler):
        server_version = "fief-status"

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _proxy(self) -> None:
            import http.client

            if target is None:
                self._send(404, b"Not found\n", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else None
            fwd = {
                k: v for k, v in self.headers.items() if k.lower() not in _HOP_HEADERS
            }
            try:
                conn = http.client.HTTPConnection(*target)
                conn.request(self.command, self.path, body=body, headers=fwd)
                resp = conn.getresponse()
                self.send_response(resp.status, resp.reason)
                for key, value in resp.getheaders():
                    if key.lower() not in _HOP_HEADERS:
                        self.send_header(key, value)
                self.end_headers()
                while (chunk := resp.read(65536)) != b"":
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except OSError:
                self._send(502, b"ui unavailable, retry shortly\n", "text/plain")

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
            if path == "/":
                if target is None:
                    status, logs = snapshot()
                    page = (
                        "<!doctype html><html><head><title>fief monitor</title></head>"
                        "<body><h1>fief monitor hub</h1>"
                        "<h2>Status</h2><pre>" + html.escape(status) + "</pre>"
                        "<h2>Recent log</h2><pre>" + html.escape(logs) + "</pre>"
                        "</body></html>"
                    )
                    self._send(200, page.encode(), "text/html; charset=utf-8")
                    return
                self._proxy()
                return
            self._proxy()

        do_POST = _proxy
        do_PUT = _proxy
        do_DELETE = _proxy
        do_HEAD = _proxy
        do_OPTIONS = _proxy
        do_PATCH = _proxy

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
    proxy_to: str = "",
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(
        (host, port), make_handler(snapshot, hub_url, proxy_to)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
