import urllib.request

from fief.status import serve_forever


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
