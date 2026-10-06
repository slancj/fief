"""Loopback HTTP-CONNECT bridge over a SOCKS5 uplink (stdlib only).

Why this exists: the only uplink on a filtered box is a SOCKS5 listener
(box 1081, carried by the tunnel), but the mesh daemon's relay dialer
speaks HTTP CONNECT to its configured proxy target. Pointed at a SOCKS5
port it gets an instant EOF on every relay dial — the symptom is a relay
loop (`... unexpected EOF`) with the node otherwise online, while plain
SOCKS5 clients (curl, the control client) work fine through the same port.

The bridge listens on 127.0.0.1 (ephemeral port) and relays each request
through the SOCKS5 uplink with remote DNS (socks5h semantics): CONNECT
targets are tunnelled byte-for-byte, absolute-URI http:// requests are
forwarded with `Connection: close` and relayed with proper response
framing. Nothing request-specific is logged: URLs may carry key material.
"""

from __future__ import annotations

import socket
import threading
import urllib.parse
from collections.abc import Callable
from socketserver import BaseRequestHandler, ThreadingTCPServer

BRIDGE_HOST = "127.0.0.1"
_BUF = 65536
_HEAD_LIMIT = 65536
_HANDSHAKE_TIMEOUT = 30


def needs_bridge(proxy: str) -> bool:
    """True when the uplink URL needs bridging (SOCKS5-only speakers fail)."""
    scheme = urllib.parse.urlsplit(proxy or "").scheme.lower()
    return scheme in ("socks5", "socks5h")


def parse_socks_upstream(url: str) -> tuple[str, int]:
    """Split a socks5(h):// URL into (host, port). No-auth only (fail fast)."""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme.lower() not in ("socks5", "socks5h"):
        raise ValueError(f"not a socks5 proxy URL: {url!r}")
    if parts.username or parts.password:
        raise ValueError("authenticated socks5 uplink not supported")
    host = parts.hostname or ""
    if not host:
        raise ValueError(f"socks5 proxy URL has no host: {url!r}")
    return host, parts.port or 1080


def _recvn(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise OSError("unexpected EOF from peer")
        out += chunk
    return bytes(out)


def socks5_connect(
    upstream: tuple[str, int],
    dest_host: str,
    dest_port: int,
    timeout: float = _HANDSHAKE_TIMEOUT,
) -> socket.socket:
    """Open a SOCKS5 CONNECT tunnel via the uplink (remote DNS)."""
    if not dest_host or not (0 < dest_port < 65536):
        raise ValueError(f"bad tunnel target: {dest_host!r}:{dest_port!r}")
    host_b = dest_host.encode("idna")
    if len(host_b) > 255:
        raise ValueError(f"tunnel hostname too long: {dest_host!r}")
    sock = socket.create_connection(upstream, timeout=timeout)
    try:
        sock.sendall(b"\x05\x01\x00")  # greeting: no auth
        if _recvn(sock, 2) != b"\x05\x00":
            raise OSError(f"socks5 {upstream[0]}:{upstream[1]} refused no-auth")
        sock.sendall(
            b"\x05\x01\x00\x03"
            + bytes([len(host_b)])
            + host_b
            + dest_port.to_bytes(2, "big")
        )
        ver, rep, _, atyp = _recvn(sock, 4)
        if ver != 0x05 or rep != 0x00:
            raise OSError(
                f"socks5 CONNECT {dest_host}:{dest_port} refused (rep={rep:#x})"
            )
        if atyp == 0x01:  # bound IPv4 + port
            _recvn(sock, 4 + 2)
        elif atyp == 0x03:  # bound domain + port
            (ln,) = _recvn(sock, 1)
            _recvn(sock, ln + 2)
        elif atyp == 0x04:  # bound IPv6 + port
            _recvn(sock, 16 + 2)
        else:
            raise OSError(f"socks5 gave malformed reply (atyp={atyp:#x})")
        sock.settimeout(None)
        return sock
    except Exception:
        sock.close()
        raise


class _Buffered:
    """Minimal buffered reader over a socket (head lines + exact reads)."""

    def __init__(self, sock: socket.socket, initial: bytes = b"") -> None:
        self._sock = sock
        self._buf = bytearray(initial)

    def read_until(self, marker: bytes, limit: int) -> bytes:
        while marker not in self._buf:
            if len(self._buf) > limit:
                raise ValueError("request head too large")
            chunk = self._sock.recv(_BUF)
            if not chunk:
                raise OSError("client closed before request head")
            self._buf += chunk
        head, _, rest = bytes(self._buf).partition(marker)
        self._buf = bytearray(rest)
        return head + marker

    def read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(_BUF)
            if not chunk:
                raise OSError("unexpected EOF in request body")
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def pending(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out


def _parse_head(head: bytes) -> tuple[str, str, list[tuple[str, str]]]:
    """Split a request head into (METHOD, target, [(name, value)])."""
    method, target, header_lines = _split_head(head)
    return method.upper(), target, header_lines


def _split_head(head: bytes) -> tuple[str, str, list[tuple[str, str]]]:
    """Split any head (request or response) into (first, second, headers).

    For requests that is (METHOD, target, headers); for responses (VERSION,
    status, headers) — one parser serves both.
    """
    try:
        text = head.decode("latin-1")
    except ValueError as exc:
        raise ValueError("head is not text") from exc
    lines = text.split("\r\n")
    try:
        first, second, _rest = lines[0].split(" ", 2)
    except ValueError as exc:
        raise ValueError(f"bad first line: {lines[0]!r}") from exc
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or not name.strip():
            raise ValueError(f"bad header line: {line!r}")
        headers.append((name.strip(), value.strip()))
    return first, second, headers


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(_BUF)
            if not chunk:
                return
            dst.sendall(chunk)
    except OSError:
        return


class _BridgeServer(ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        addr: tuple[str, int],
        upstream: tuple[str, int],
        log: Callable[[str], None] | None,
    ) -> None:
        self.upstream = upstream
        self._emit = log or (lambda msg: None)
        super().__init__(addr, _BridgeHandler, bind_and_activate=False)
        self.server_bind()
        self.server_activate()


class _BridgeHandler(BaseRequestHandler):
    server: _BridgeServer  # set by socketserver

    def handle(self) -> None:
        try:
            self._serve()
        except (OSError, ValueError):
            pass  # client gone or bad request; error reply already attempted
        finally:
            try:
                self.request.close()
            except OSError:
                pass

    def _serve(self) -> None:
        sock = self.request
        sock.settimeout(_HANDSHAKE_TIMEOUT)
        buf = _Buffered(sock)
        try:
            raw = buf.read_until(b"\r\n\r\n", _HEAD_LIMIT)
        except (OSError, ValueError):
            return
        head, _, _ = raw.partition(b"\r\n\r\n")
        try:
            method, target, headers = _parse_head(head + b"\r\n\r\n")
        except ValueError:
            self._err(b"400 Bad Request")
            return
        sock.settimeout(None)
        if method == "CONNECT":
            host, _, port_s = target.rpartition(":")
            host = host.strip("[]").strip()
            if not host or not port_s.isdigit():
                self._err(b"400 Bad Request")
                return
            self._tunnel(host, int(port_s), buf.pending())
        elif "://" in target:
            self._forward(method, target, headers, buf)
        else:
            self._err(b"400 Bad Request")

    def _err(self, status: bytes) -> None:
        try:
            self.request.sendall(
                b"HTTP/1.1 "
                + status
                + b"\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
            )
        except OSError:
            pass

    def _tunnel(self, host: str, port: int, pending: bytes) -> None:
        if not 0 < port < 65536:
            self._err(b"400 Bad Request")
            return
        try:
            upstream = socks5_connect(self.server.upstream, host, port)
        except (OSError, ValueError):
            self._err(b"502 Bad Gateway")
            return
        client = self.request
        try:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if pending:
                upstream.sendall(pending)
            reverse = threading.Thread(
                target=_pump, args=(upstream, client), daemon=True
            )
            reverse.start()
            _pump(client, upstream)
        except OSError:
            pass
        finally:
            try:
                upstream.close()
            except OSError:
                pass
            reverse.join(timeout=5)

    def _forward(
        self,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
        buffered: _Buffered,
    ) -> None:
        parts = urllib.parse.urlsplit(target)
        if parts.scheme.lower() != "http" or not parts.hostname:
            self._err(b"400 Bad Request")
            return
        length: int | None = None
        chunked = False
        kept: list[tuple[str, str]] = []
        for name, value in headers:
            low = name.lower()
            if low.startswith("proxy-"):
                continue
            if low == "connection":
                continue
            if low == "content-length":
                try:
                    length = int(value)
                except ValueError:
                    self._err(b"400 Bad Request")
                    return
                if length < 0:
                    self._err(b"400 Bad Request")
                    return
                continue
            if low == "transfer-encoding":
                chunked = "chunked" in value.lower()
                continue
            kept.append((name, value))
        if chunked:
            self._err(b"501 Not Implemented")
            return
        body = buffered.pending()
        try:
            if length is not None:
                want = length - len(body)
                if want > 0:
                    body += buffered.read_exact(want)
                body = body[:length]
            elif body:
                # No framing for a body: only possible when the client
                # pipelined bytes we cannot delimit — refuse rather than
                # corrupt the relay.
                self._err(b"400 Bad Request")
                return
        except OSError:
            self._err(b"400 Bad Request")
            return
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        out = [f"{method} {path} HTTP/1.1".encode("latin-1")]
        out.append(f"Host: {parts.hostname}".encode("latin-1"))
        for name, value in kept:
            if name.lower() == "host":
                continue
            out.append(f"{name}: {value}".encode("latin-1"))
        out.append(b"Connection: close")
        if length is not None:
            out.append(f"Content-Length: {length}".encode("latin-1"))
        head_out = b"\r\n".join(out) + b"\r\n\r\n"
        try:
            upstream = socks5_connect(
                self.server.upstream, parts.hostname, parts.port or 80
            )
        except (OSError, ValueError):
            self._err(b"502 Bad Gateway")
            return
        try:
            upstream.sendall(head_out + body)
            self._relay_response(upstream)
        except OSError:
            pass
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    def _relay_response(self, upstream: socket.socket) -> None:
        """Relay one origin response (framed) back to the client."""
        client = self.request
        buf = _Buffered(upstream)
        try:
            head = buf.read_until(b"\r\n\r\n", _HEAD_LIMIT)
            _status, _, headers = _split_head(head)
        except (OSError, ValueError):
            return
        length: int | None = None
        chunked = False
        for name, value in headers:
            low = name.lower()
            if low == "content-length" and length is None:
                try:
                    length = int(value)
                except ValueError:
                    length = None
            elif low == "transfer-encoding" and "chunked" in value.lower():
                chunked = True
        try:
            client.sendall(head)
            if chunked:
                self._relay_chunked(buf, client)
            elif length is not None:
                client.sendall(buf.read_exact(length))
            else:
                _pump(upstream, client)
        except OSError:
            pass

    def _relay_chunked(self, buf: _Buffered, client: socket.socket) -> None:
        while True:
            line = buf.read_until(b"\r\n", 1024)
            client.sendall(line)
            try:
                size = int(line.split(b";", 1)[0].strip(), 16)
            except ValueError:
                return
            if size == 0:
                client.sendall(buf.read_until(b"\r\n", _HEAD_LIMIT))
                return
            client.sendall(buf.read_exact(size + 2))  # chunk + CRLF


def start_bridge(
    upstream_host: str,
    upstream_port: int,
    *,
    host: str = BRIDGE_HOST,
    port: int = 0,
    log: Callable[[str], None] | None = None,
) -> tuple[_BridgeServer, threading.Thread, int]:
    """Serve the bridge on loopback (ephemeral port). Returns (server, thread, port)."""
    emit = log or (lambda msg: None)
    server = _BridgeServer((host, port), (upstream_host, upstream_port), emit)
    actual = server.server_address[1]
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.2},
        daemon=True,
        name="fief-bridge",
    )
    thread.start()
    emit(f"proxy bridge on {host}:{actual} -> socks5 {upstream_host}:{upstream_port}")
    return server, thread, actual
