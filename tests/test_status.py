import urllib.request

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
