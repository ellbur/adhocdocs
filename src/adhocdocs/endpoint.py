"""Where the host's services listen, and how to reach them, on every platform.

Each service (the proxy, the viewer, the annotation and audit APIs) is
named by a path. On Linux the path is a Unix socket: only the user can
reach it, because only the user can reach its directory. Python on Windows
has no Unix sockets, and on Windows and macOS the session runs in Podman's
VM, where host sockets don't reach anyway. So there (or anywhere, with
``ADHOC_TRANSPORT=tcp``) the service listens on a random port on
127.0.0.1 and writes an *endpoint file* at the path instead::

    {"tcp": "127.0.0.1:53017", "token": "<64 hex digits>"}

Any local program can reach a port, so the token stands in for the
directory's permissions: a connection must start with the line
``ADHOC1 <token>\\n`` or it is dropped. The endpoint file lives in the
user's private runtime directory (or the matter's ``run/``) and is removed
when the service stops. Clients call :func:`connect`, which handles both.

The bridge (:mod:`adhocdocs.bridge`) uses the same first line, with a verb
after the token, on the one port it publishes.
"""

import asyncio
import hmac
import json
import os
import secrets
import socket
import socketserver
import sys
from pathlib import Path

PREAMBLE = b"ADHOC1"
MAX_PREAMBLE = 256


def tcp_mode() -> bool:
    """Whether services here listen on TCP with a token (see the module docstring)."""
    if os.environ.get("ADHOC_TRANSPORT") in ("tcp", "unix"):
        return os.environ["ADHOC_TRANSPORT"] == "tcp"
    return sys.platform != "linux"


def new_token() -> str:
    return secrets.token_hex(32)


def preamble(token: str, *words: str) -> bytes:
    return b" ".join([PREAMBLE, token.encode(), *(w.encode() for w in words)]) + b"\n"


def parse_preamble(line: bytes, token: str) -> list[str] | None:
    """The words after the token, or None if the line isn't ours."""
    parts = line.rstrip(b"\r\n").split(b" ")
    if len(parts) < 2 or parts[0] != PREAMBLE or not hmac.compare_digest(parts[1], token.encode()):
        return None
    return [p.decode("ascii", "replace") for p in parts[2:]]


def read_line(sock: socket.socket, timeout=10.0) -> bytes:
    """Read one line, byte by byte, so nothing after it is consumed."""
    sock.settimeout(timeout)
    buf = b""
    try:
        while not buf.endswith(b"\n") and len(buf) < MAX_PREAMBLE:
            c = sock.recv(1)
            if not c:
                break
            buf += c
    finally:
        sock.settimeout(None)
    return buf


def write_endpoint(path: Path, address: str, token: str):
    """Write an endpoint file atomically, readable only by the user."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"tcp": address, "token": token}, f)
    os.replace(tmp, path)


def read_endpoint(path: Path) -> dict | None:
    """The endpoint file at ``path``, or None if ``path`` is something else (a socket)."""
    path = Path(path)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _split(address: str) -> tuple[str, int]:
    host, _, port = address.rpartition(":")
    return host.strip("[]"), int(port)


def connect(path, timeout: float | None = None, *words: str) -> socket.socket:
    """A connected socket to the service at ``path``. Raises
    FileNotFoundError or ConnectionRefusedError if nothing is listening."""
    path = Path(path)
    ep = read_endpoint(path)
    if ep is not None:
        s = socket.create_connection(_split(ep["tcp"]), timeout=timeout)
        s.sendall(preamble(ep["token"], *words))
        return s
    if not hasattr(socket, "AF_UNIX") or not path.exists():
        raise FileNotFoundError(str(path))
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
    except OSError:
        s.close()
        raise
    return s


# -- serving HTTP (socketserver) ---------------------------------------------------------------

def http_server(path, handler, name: str, mode=0o600):
    """A threading HTTP server for ``handler`` (a BaseHTTPRequestHandler)
    at ``path``, ready for ``serve_forever()``. Use it as a context manager:
    leaving it removes the socket or endpoint file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.unlink(missing_ok=True)
    if tcp_mode():
        return _TokenTCPServer(path, handler, name)
    return _UnixServer(path, handler, name, mode)


class _Cleanup:
    path: Path

    def __exit__(self, *exc):
        try:
            super().__exit__(*exc)            # closes the server
        finally:
            self.path.unlink(missing_ok=True)


if hasattr(socketserver, "UnixStreamServer"):
    class _UnixServer(_Cleanup, socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def __init__(self, path, handler, name, mode):
            self.path, self.name = path, name
            super().__init__(str(path), handler)
            os.chmod(path, mode)

        def get_request(self):
            sock, _ = super().get_request()
            return sock, (self.name, 0)       # BaseHTTPRequestHandler wants a (host, port)
else:
    def _UnixServer(*a, **kw):
        raise OSError("Unix sockets aren't available here; set ADHOC_TRANSPORT=tcp")


class _TokenTCPServer(_Cleanup, socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path, handler, name):
        self.path, self.name, self.token = path, name, new_token()
        super().__init__(("127.0.0.1", 0), handler)
        write_endpoint(path, "%s:%d" % self.server_address[:2], self.token)

    def finish_request(self, request, client_address):
        # In the request's own thread, so a slow client can't hold up others.
        if parse_preamble(read_line(request), self.token) is None:
            return                            # not ours: drop it unanswered
        super().finish_request(request, (self.name, 0))


# -- serving and connecting with asyncio (the proxy, the bridge) --------------------------------------

async def start_server(handler, path, limit=2 ** 16, mode=0o600):
    """An asyncio server at ``path``; returns ``(server, cleanup)``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.unlink(missing_ok=True)
    if not tcp_mode():
        server = await asyncio.start_unix_server(handler, str(path), limit=limit)
        os.chmod(path, mode)
        return server, lambda: path.unlink(missing_ok=True)
    token = new_token()

    async def checked(reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
        except (asyncio.TimeoutError, ConnectionError, ValueError, asyncio.LimitOverrunError):
            line = b""
        if parse_preamble(line, token) is None:
            writer.close()
            return
        await handler(reader, writer)

    server = await asyncio.start_server(checked, "127.0.0.1", 0, limit=limit)
    host, port = server.sockets[0].getsockname()[:2]
    write_endpoint(path, f"{host}:{port}", token)
    return server, lambda: path.unlink(missing_ok=True)


async def open_connection(path, *words: str, limit=2 ** 16):
    """asyncio streams to the service at ``path`` (socket or endpoint file)."""
    path = Path(path)
    ep = read_endpoint(path)
    if ep is not None:
        reader, writer = await asyncio.open_connection(*_split(ep["tcp"]), limit=limit)
        writer.write(preamble(ep["token"], *words))
        await writer.drain()
        return reader, writer
    if not hasattr(asyncio, "open_unix_connection") or not path.exists():
        raise FileNotFoundError(str(path))
    return await asyncio.open_unix_connection(str(path), limit=limit)
