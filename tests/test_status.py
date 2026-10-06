import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fief.status import check_listener, serve_forever


def test_check_listener_open_closed():
    import socket

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        assert check_listener(str(srv.getsockname()[1])) is True
    finally:
        srv.close()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = str(s.getsockname()[1])
    assert check_listener(closed) is False
    assert check_listener("notaport") is False


def test_health_and_index():
    server, _thread = serve_forever(0, lambda: ("STATUS-OK", "LOG-OK"))
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as r:
            assert r.status == 200
            assert r.read() == b"OK\n"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            body = r.read().decode()
            assert "STATUS-OK" in body and "LOG-OK" in body
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope")
        except urllib.error.HTTPError as e:
            assert e.code == 404
        else:
            raise AssertionError("expected 404")
    finally:
        server.shutdown()


def test_box_routes_on_same_backend():
    server, _thread = serve_forever(0, lambda: ("STATUS-OK", "LOG-OK"))
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/add.sh") as r:
            assert r.status == 200
            assert "box.env" in r.read().decode()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/box/versions") as r:
            assert b"MESH_VERSION=" in r.read()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/box/fief.tgz") as r:
            assert r.status == 200
    finally:
        server.shutdown()


def _fake_ui():
    """Stub UI port: echoes method/path/body, streams two chunks."""

    class Upstream(BaseHTTPRequestHandler):
        server_version = "fake-ui"

        def _handle(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            if self.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"data: one\n\n")
                self.wfile.flush()
                self.wfile.write(b"data: two\n\n")
                return
            payload = f"{self.command} {self.path} {body.decode()}".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _handle
        do_POST = _handle

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_proxy_forwards_method_path_query_body():
    ui = _fake_ui()
    try:
        server, _thread = serve_forever(
            0,
            lambda: ("S", "L"),
            proxy_to=f"127.0.0.1:{ui.server_address[1]}",
        )
        port = server.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/some/path?a=b") as r:
                assert r.read() == b"GET /some/path?a=b "
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/submit", data=b"hi=1", method="POST"
            )
            with urllib.request.urlopen(req) as r:
                assert r.read() == b"POST /submit hi=1"
        finally:
            server.shutdown()
    finally:
        ui.shutdown()


def test_proxy_streams_chunks_live():
    ui = _fake_ui()
    try:
        server, _thread = serve_forever(
            0,
            lambda: ("S", "L"),
            proxy_to=f"127.0.0.1:{ui.server_address[1]}",
        )
        port = server.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/stream") as r:
                assert r.read() == b"data: one\n\ndata: two\n\n"
        finally:
            server.shutdown()
    finally:
        ui.shutdown()


def test_proxy_local_paths_win_and_downstream_is_502():
    ui = _fake_ui()
    ui_port = ui.server_address[1]
    # Truly dark port: shutdown stops the loop AND server_close frees the
    # socket (shutdown alone leaves it accepting into the backlog, which
    # would hang instead of refusing).
    ui.shutdown()
    ui.server_close()
    server, _thread = serve_forever(
        0, lambda: ("S", "L"), proxy_to=f"127.0.0.1:{ui_port}"
    )
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as r:
            assert r.read() == b"OK\n"  # local, no upstream needed
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/ui/thing")
        except urllib.error.HTTPError as e:
            assert e.code == 502
        else:
            raise AssertionError("expected 502")
    finally:
        server.shutdown()
    server, _thread = serve_forever(0, lambda: ("STATUS-OK", "LOG-OK"))
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/add.sh") as r:
            assert r.status == 200
            assert "box.env" in r.read().decode()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/box/versions") as r:
            assert b"MESH_VERSION=" in r.read()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/box/fief.tgz") as r:
            assert r.status == 200
    finally:
        server.shutdown()
    server, _thread = serve_forever(0, lambda: ("STATUS-OK", "LOG-OK"))
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as r:
            assert r.status == 200
            assert r.read() == b"OK\n"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            body = r.read().decode()
            assert "STATUS-OK" in body and "LOG-OK" in body
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope")
        except urllib.error.HTTPError as e:
            assert e.code == 404
        else:
            raise AssertionError("expected 404")
    finally:
        server.shutdown()
