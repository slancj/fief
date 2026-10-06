"""Loopback HTTP-CONNECT bridge over a SOCKS5 uplink: parsing + relay."""

import socket
import threading

import pytest

from fief import bridge
from fief.bridge import (
    needs_bridge,
    parse_socks_upstream,
    socks5_connect,
    start_bridge,
)


def test_needs_bridge_only_for_socks():
    assert needs_bridge("socks5h://127.0.0.1:1081")
    assert needs_bridge("socks5://127.0.0.1:1081")
    assert not needs_bridge("http://127.0.0.1:8080")
    assert not needs_bridge("https://127.0.0.1:8080")
    assert not needs_bridge("")


def test_parse_socks_upstream():
    assert parse_socks_upstream("socks5h://127.0.0.1:1081") == ("127.0.0.1", 1081)
    assert parse_socks_upstream("socks5://proxy:9999") == ("proxy", 9999)
    assert parse_socks_upstream("socks5h://proxy") == ("proxy", 1080)
    with pytest.raises(ValueError):
        parse_socks_upstream("http://127.0.0.1:8080")
    with pytest.raises(ValueError):
        parse_socks_upstream("socks5h://user:pass@127.0.0.1:1081")
    with pytest.raises(ValueError):
        parse_socks_upstream("socks5h://")


class _FakeSocks5:
    """Minimal real SOCKS5 server: no-auth, dials the requested target."""

    def __init__(self):
        from socketserver import BaseRequestHandler, ThreadingTCPServer

        outer = self

        class Handler(BaseRequestHandler):
            def handle(self):
                conn = self.request
                conn.settimeout(10)

                def recvn(n):
                    out = b""
                    while len(out) < n:
                        chunk = conn.recv(n - len(out))
                        if not chunk:
                            raise OSError("eof in handshake")
                        out += chunk
                    return out

                try:
                    assert recvn(3) == b"\x05\x01\x00"
                    conn.sendall(b"\x05\x00")
                    ver, cmd, _, atyp = recvn(4)
                    assert (ver, cmd) == (0x05, 0x01)
                    if atyp == 0x03:
                        (ln,) = recvn(1)
                        host = recvn(ln).decode("ascii")
                    elif atyp == 0x01:
                        raw = recvn(4)
                        host = ".".join(str(b) for b in raw)
                    else:
                        raise AssertionError(f"atyp {atyp}")
                    port = int.from_bytes(recvn(2), "big")
                    outer.seen.append((host, port))
                    if outer.refuse:
                        conn.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
                        return
                    target = socket.create_connection((host, port), timeout=10)
                    conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
                    rev = threading.Thread(
                        target=bridge._pump, args=(conn, target), daemon=True
                    )
                    rev.start()
                    try:
                        bridge._pump(target, conn)
                    finally:
                        try:
                            target.close()
                        except OSError:
                            pass
                        rev.join(timeout=5)
                except (OSError, AssertionError):
                    pass
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

        self.seen: list[tuple[str, int]] = []
        self.refuse = False
        self.server = ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}
        )
        self.thread.daemon = True
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def uplink():
    up = _FakeSocks5()
    yield up
    up.close()


def _echo_server():
    from socketserver import BaseRequestHandler, ThreadingTCPServer

    class Handler(BaseRequestHandler):
        def handle(self):
            try:
                while True:
                    chunk = self.request.recv(65536)
                    if not chunk:
                        return
                    self.request.sendall(chunk)
            except OSError:
                pass

    srv = ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05})
    thread.daemon = True
    thread.start()
    return srv, port


def test_socks5_connect_tunnels(uplink):
    _, echo_port = _echo_server()
    sock = socks5_connect(("127.0.0.1", uplink.port), "127.0.0.1", echo_port)
    try:
        sock.sendall(b"hello")
        assert sock.recv(5) == b"hello"
    finally:
        sock.close()
    assert uplink.seen == [("127.0.0.1", echo_port)]


def test_socks5_connect_refused(uplink):
    uplink.refuse = True
    with pytest.raises(OSError):
        socks5_connect(("127.0.0.1", uplink.port), "127.0.0.1", 9)


def _bridge_to(uplink):
    server, _thread, port = start_bridge("127.0.0.1", uplink.port)
    return server, port


def test_connect_relay(uplink):
    _, echo_port = _echo_server()
    server, port = _bridge_to(uplink)
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            client.sendall(
                f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1\r\nHost: x\r\n\r\n".encode()
            )
            head = b""
            while b"\r\n\r\n" not in head:
                head += client.recv(4096)
            assert head.startswith(b"HTTP/1.1 200")
            client.sendall(b"ping")
            assert client.recv(4) == b"ping"
        finally:
            client.close()
    finally:
        server.shutdown()
        server.server_close()


def test_connect_bad_target_gives_400(uplink):
    server, port = _bridge_to(uplink)
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            client.sendall(b"CONNECT nonsense HTTP/1.1\r\nHost: x\r\n\r\n")
            resp = client.recv(4096)
            assert resp.startswith(b"HTTP/1.1 400")
        finally:
            client.close()
    finally:
        server.shutdown()
        server.server_close()


def test_connect_refused_upstream_gives_502(uplink):
    uplink.refuse = True
    server, port = _bridge_to(uplink)
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            client.sendall(b"CONNECT 127.0.0.1:9 HTTP/1.1\r\nHost: x\r\n\r\n")
            resp = client.recv(4096)
            assert resp.startswith(b"HTTP/1.1 502")
        finally:
            client.close()
    finally:
        server.shutdown()
        server.server_close()


def _origin(body: bytes, ctype: str = "text/plain"):
    from socketserver import BaseRequestHandler, ThreadingTCPServer

    class Handler(BaseRequestHandler):
        def handle(self):
            try:
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    head += chunk
                first = head.split(b"\r\n", 1)[0].decode("latin-1")
                length = 0
                for line in head.decode("latin-1").split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        length = int(line.split(":", 1)[1])
                if length:
                    body_in = head.split(b"\r\n\r\n", 1)[1]
                    while len(body_in) < length:
                        body_in += self.request.recv(4096)
                assert first.startswith(("GET /path", "POST /submit")), first
                resp = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: "
                    + ctype.encode()
                    + b"\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + body
                )
                self.request.sendall(resp)
            except OSError:
                pass

    srv = ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05})
    thread.daemon = True
    thread.start()
    return srv, port


def _request_through_bridge(port: int, raw: bytes) -> bytes:
    client = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        client.sendall(raw)
        out = b""
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            out += chunk
        return out
    finally:
        client.close()


def test_absolute_uri_get(uplink):
    _, origin_port = _origin(b"hello-origin")
    server, port = _bridge_to(uplink)
    try:
        resp = _request_through_bridge(
            port,
            f"GET http://127.0.0.1:{origin_port}/path HTTP/1.1\r\n"
            "Host: ignored\r\n\r\n".encode(),
        )
        assert b"200 OK" in resp.split(b"\r\n\r\n", 1)[0]
        assert resp.split(b"\r\n\r\n", 1)[1] == b"hello-origin"
    finally:
        server.shutdown()
        server.server_close()


def test_absolute_uri_post_body(uplink):
    _, origin_port = _origin(b"posted")
    server, port = _bridge_to(uplink)
    try:
        resp = _request_through_bridge(
            port,
            f"POST http://127.0.0.1:{origin_port}/submit HTTP/1.1\r\n"
            "Host: ignored\r\nContent-Length: 3\r\n\r\nabc".encode(),
        )
        assert resp.split(b"\r\n\r\n", 1)[1] == b"posted"
    finally:
        server.shutdown()
        server.server_close()


def test_parallel_connects(uplink):
    _, echo_port = _echo_server()
    server, port = _bridge_to(uplink)
    errors: list[Exception] = []

    def one(i: int):
        try:
            payload = f"msg-{i}".encode()
            client = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                client.sendall(
                    f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1\r\nHost: x\r\n\r\n".encode()
                )
                head = b""
                while b"\r\n\r\n" not in head:
                    head += client.recv(4096)
                assert head.startswith(b"HTTP/1.1 200")
                client.sendall(payload)
                got = b""
                while len(got) < len(payload):
                    got += client.recv(4096)
                assert got == payload
            finally:
                client.close()
        except Exception as exc:  # noqa: BLE001 - collected across threads
            errors.append(exc)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    server.shutdown()
    server.server_close()


def test_proxy_for_daemon_passthrough():
    from fief.mesh_run import proxy_for_daemon

    logs: list[str] = []
    assert proxy_for_daemon("", logs.append) == ""
    assert proxy_for_daemon("http://127.0.0.1:8080", logs.append) == (
        "http://127.0.0.1:8080"
    )


def test_proxy_for_daemon_bridges_socks():
    from fief.bridge import needs_bridge
    from fief.mesh_run import proxy_for_daemon

    assert needs_bridge("socks5h://127.0.0.1:1081")
    logs: list[str] = []
    got = proxy_for_daemon("socks5h://127.0.0.1:1081", logs.append)
    assert got.startswith("http://127.0.0.1:")
    assert got != "socks5h://127.0.0.1:1081"
    # The bridge URL must itself be plain http (no re-bridging downstream).
    assert not needs_bridge(got)
