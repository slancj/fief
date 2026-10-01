import socket
import threading
import time

from fief.egress import start_egress_server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _echo_once(listen_port: int, stop: threading.Event) -> None:
    """Serve a single echo connection (enough for one CONNECT test)."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", listen_port))
    srv.listen(1)
    srv.settimeout(15)
    try:
        conn, _ = srv.accept()
    except TimeoutError:
        return
    finally:
        stop.set()
    with conn:
        conn.settimeout(10)
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                conn.sendall(chunk)
        except TimeoutError:
            return


def _start_proxy() -> tuple[threading.Event, threading.Thread, int, list[str]]:
    port = _free_port()
    stop = threading.Event()
    logs: list[str] = []
    thread = start_egress_server(port, stop, logs.append)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.1)
    return stop, thread, port, logs


def _handshake(proxy_port: int, host: str, port: int) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", proxy_port), timeout=10)
    s.settimeout(10)
    s.sendall(b"\x05\x01\x00")
    assert s.recv(2) == b"\x05\x00"
    try:
        parts = [int(p) for p in host.split(".")]
        assert len(parts) == 4
        addr = b"\x01" + bytes(parts)
    except (ValueError, AssertionError):
        raw = host.encode("ascii")
        addr = b"\x03" + bytes([len(raw)]) + raw
    s.sendall(b"\x05\x01\x00" + addr + port.to_bytes(2, "big"))
    resp = s.recv(10)
    assert resp[0] == 0x05 and resp[1] == 0x00, resp
    return s


def test_connect_ipv4_proxies_bytes():
    echo_port = _free_port()
    echo_stop = threading.Event()
    threading.Thread(
        target=_echo_once, args=(echo_port, echo_stop), daemon=True
    ).start()
    stop, thread, proxy_port, logs = _start_proxy()
    try:
        assert any("egress socks on 127.0.0.1:" in line for line in logs)
        with _handshake(proxy_port, "127.0.0.1", echo_port) as s:
            s.sendall(b"hello-egress")
            assert s.recv(12) == b"hello-egress"
    finally:
        stop.set()
        thread.join(timeout=10)
    assert echo_stop.wait(timeout=10)


def test_connect_domain_resolves_server_side():
    echo_port = _free_port()
    echo_stop = threading.Event()
    threading.Thread(
        target=_echo_once, args=(echo_port, echo_stop), daemon=True
    ).start()
    stop, thread, proxy_port, _ = _start_proxy()
    try:
        with _handshake(proxy_port, "localhost", echo_port) as s:
            s.sendall(b"ping")
            assert s.recv(4) == b"ping"
    finally:
        stop.set()
        thread.join(timeout=10)
    assert echo_stop.wait(timeout=10)


def test_auth_required_rejected():
    stop, thread, proxy_port, _ = _start_proxy()
    try:
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=10) as s:
            s.settimeout(10)
            s.sendall(b"\x05\x01\x02")  # user/pass only, no no-auth
            assert s.recv(2) == b"\x05\xff"
    finally:
        stop.set()
        thread.join(timeout=10)


def test_non_connect_rejected():
    stop, thread, proxy_port, _ = _start_proxy()
    try:
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=10) as s:
            s.settimeout(10)
            s.sendall(b"\x05\x01\x00")
            assert s.recv(2) == b"\x05\x00"
            s.sendall(b"\x05\x02\x00\x01\x7f\x00\x00\x01\x00\x50")  # BIND
            resp = s.recv(10)
            assert resp[0] == 0x05 and resp[1] == 0x07
    finally:
        stop.set()
        thread.join(timeout=10)


def test_refused_backend_reported():
    closed = _free_port()
    stop, thread, proxy_port, _ = _start_proxy()
    try:
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=10) as s:
            s.settimeout(10)
            s.sendall(b"\x05\x01\x00")
            assert s.recv(2) == b"\x05\x00"
            s.sendall(b"\x05\x01\x00\x01\x7f\x00\x00\x01" + closed.to_bytes(2, "big"))
            resp = s.recv(10)
            assert resp[0] == 0x05 and resp[1] != 0x00
    finally:
        stop.set()
        thread.join(timeout=10)
