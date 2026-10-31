"""The annotation API: the container's only way to write annotations.

The server runs on the host (the launcher starts it with the session) and
listens on a Unix socket that is mounted into the container. It stamps
every write ``claude``, whatever the request says, and applies the rules in
:mod:`adhocdocs.annotations`. The log itself is mounted read-only in the
container, so this is the only path, and Claude can't write as the user.

Protocol: ``POST /write`` with a JSON request (``op`` and its fields, plus
optional ``requested_by``). The reply is ``{"events": [...]}`` and, for
notes, ``{"check": [...]}``, or ``{"refused": reason}`` with status 403.
"""

import hashlib
import http.client
import json
import os
import socket
import socketserver
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from . import annotations
from .matter import Matter


MAX_SOCKET_PATH = 100   # AF_UNIX paths are limited to about 108 bytes


def socket_path(m: Matter) -> Path:
    """``$ADHOC_ANN_SOCKET`` (set in the container), else
    ``<matter>/run/annotations.sock``, else, if that is too long for a Unix
    socket, a per-matter path under ``$XDG_RUNTIME_DIR``."""
    if os.environ.get("ADHOC_ANN_SOCKET"):
        return Path(os.environ["ADHOC_ANN_SOCKET"])
    p = m.root / "run" / "annotations.sock"
    if len(str(p)) <= MAX_SOCKET_PATH:
        return p
    run = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/adhocdocs-{os.getuid()}")
    return run / "adhocdocs" / (hashlib.sha256(str(m.root).encode()).hexdigest()[:16] + ".sock")


class _Handler(BaseHTTPRequestHandler):
    matter: Matter

    def log_message(self, fmt, *args):
        pass

    def _reply(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/write":
            return self._reply(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            m = Matter(self.matter.root)          # fresh state for every request
            events = annotations.write(m, annotations.CLAUDE, req)
            out = {"events": events}
            if req.get("op") in ("note", "reply", "edit-note"):
                out["check"] = annotations.note_check(m, req.get("body", ""))
            if req.get("op") in ("define-entity", "edit-entity"):
                out["check"] = annotations.entity_check(m, req)
            self._reply(200, out)
        except annotations.Refused as e:
            self._reply(403, {"refused": str(e)})
        except Exception as e:  # report, don't kill the server
            self._reply(400, {"error": f"{type(e).__name__}: {e}"})


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        sock, _ = super().get_request()
        return sock, ("annotation-api", 0)   # BaseHTTPRequestHandler wants a (host, port)


def serve(m: Matter, path: Path | None = None):
    path = Path(path or socket_path(m))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        path.unlink()
    handler = type("Handler", (_Handler,), {"matter": m})
    with _Server(str(path), handler) as srv:
        os.chmod(path, 0o660)
        srv.serve_forever()


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost")
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(self._path))


class Unavailable(Exception):
    pass


def request(m: Matter, req: dict) -> dict:
    """Send a write to the API. Raises Unavailable if it isn't running, and
    annotations.Refused if the write was refused."""
    path = socket_path(m)
    conn = _UnixConnection(path)
    try:
        body = json.dumps(req).encode()
        conn.request("POST", "/write", body, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        out = json.loads(resp.read())
    except (FileNotFoundError, ConnectionRefusedError):
        raise Unavailable(f"the annotation API isn't running (no server at {path}); "
                          "it is started on the host with the session, so tell the user")
    finally:
        conn.close()
    if "refused" in out:
        raise annotations.Refused(out["refused"])
    if "error" in out:
        raise ValueError(out["error"])
    return out
