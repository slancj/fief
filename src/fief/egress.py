"""Hub-local SOCKS5 egress: browse via the hub's own connection.

Stdlib-asyncio CONNECT-only proxy on 127.0.0.1:<egress port>. No auth of
its own — callers arrive via mesh serve or chisel forwards, where the
mesh ACL and chisel auth already gate. Hostnames resolve server-side
(socks5h semantics), so clients leak no DNS.

This is what backs the hub-egress port (1081): before it existed, mesh
serve forwarded 1081 to a port nothing listened on.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

_REP_SUCCESS = 0x00
_REP_FAILURE = 0x01
_REP_HOST_UNREACHABLE = 0x04
_REP_REFUSED = 0x05
_REP_CMD_UNSUPPORTED = 0x07
_REP_ADDR_UNSUPPORTED = 0x08

_BUF = 65536


async def _pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await src.read(_BUF)
            if not chunk:
                return
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        return


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        ver, nmethods = await reader.readexactly(2)
        if ver != 0x05:
            return
        methods = await reader.readexactly(nmethods)
        if 0x00 not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            return
        writer.write(b"\x05\x00")
        await writer.drain()

        ver, cmd, _, atyp = await reader.readexactly(4)
        if ver != 0x05 or cmd != 0x01:  # CONNECT only
            writer.write(bytes((0x05, _REP_CMD_UNSUPPORTED, 0x00, 0x01)))
            await writer.drain()
            return
        if atyp == 0x01:  # IPv4
            host = ".".join(str(b) for b in await reader.readexactly(4))
        elif atyp == 0x03:  # domain — resolved server-side, no client leak
            (length,) = await reader.readexactly(1)
            host = (await reader.readexactly(length)).decode("ascii", "replace")
        elif atyp == 0x04:  # IPv6
            raw = await reader.readexactly(16)
            host = ":".join(
                f"{int.from_bytes(raw[i : i + 2], 'big'):x}" for i in range(0, 16, 2)
            )
        else:
            writer.write(bytes((0x05, _REP_ADDR_UNSUPPORTED, 0x00, 0x01)))
            await writer.drain()
            return
        port = int.from_bytes(await reader.readexactly(2), "big")
        try:
            remote_reader, remote_writer = await asyncio.open_connection(host, port)
        except TimeoutError:
            code = _REP_HOST_UNREACHABLE
            remote_reader = remote_writer = None
        except OSError:
            code = _REP_REFUSED
            remote_reader = remote_writer = None
        else:
            code = _REP_SUCCESS
        writer.write(bytes((0x05, code, 0x00, 0x01)) + b"\x00" * 6)
        await writer.drain()
        if code != _REP_SUCCESS:
            return
        assert remote_reader is not None and remote_writer is not None
        await asyncio.wait(
            [
                asyncio.create_task(_pump(reader, remote_writer)),
                asyncio.create_task(_pump(remote_reader, writer)),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
    except (asyncio.IncompleteReadError, ConnectionError):
        return
    finally:
        try:
            writer.close()
        except (ConnectionError, RuntimeError):
            pass


async def _amain(port: int, stop: threading.Event, log: Callable[[str], None]) -> None:
    server = await asyncio.start_server(_handle, "127.0.0.1", port)
    log(f"egress socks on 127.0.0.1:{port}")
    async with server:
        while not stop.is_set():
            await asyncio.sleep(0.2)
        server.close()
        await server.wait_closed()


def start_egress_server(
    port: str | int,
    stop: threading.Event,
    log: Callable[[str], None] | None = None,
) -> threading.Thread:
    """Serve SOCKS5 on 127.0.0.1:port in a daemon thread. Never raises."""
    emit = log or (lambda msg: None)
    thread = threading.Thread(
        target=lambda: asyncio.run(_amain(int(port), stop, emit)),
        name="fief-egress",
        daemon=True,
    )
    thread.start()
    return thread
