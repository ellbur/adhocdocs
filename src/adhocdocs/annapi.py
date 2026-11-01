"""The annotation API: the container's only way to write annotations.

The server runs on the host (the launcher starts it with the session) and
listens on a Unix socket that is mounted into the container (or, on Windows
and macOS, on a port the bridge reaches: :mod:`adhocdocs.endpoint`). It stamps
every write ``claude``, whatever the request says, and applies the rules in
:mod:`adhocdocs.annotations`. The log itself is mounted read-only in the
container, so this is the only path, and Claude can't write as the user.

Protocol: ``POST /write`` with a JSON request (``op`` and its fields, plus
optional ``requested_by``). The reply is ``{"events": [...]}`` and, for
notes, ``{"check": [...]}``, or ``{"refused": reason}`` with status 403.

It also publishes work product for Claude (:mod:`adhocdocs.work`), stamped
``claude`` the same way: ``POST /publish`` with ``{"name", "revises"?,
"merge"?, "requested_by"?}`` (``name`` is the path in the workspace), and
``POST /retire`` with ``{"name", "requested_by"?}``. And it reserves
numbers for productions and exhibit sets (:mod:`adhocdocs.numbers`), syncing
with a hub on the host: ``POST /reserve`` with ``{"series", "count",
"start"?, "name"?, "local"?}``, ``POST /release`` with ``{"reservation"}``.
"""

import hashlib
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from . import annotations, endpoint
from .matter import Matter
from .portable import runtime_dir


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
    return runtime_dir() / "adhocdocs" / (hashlib.sha256(str(m.root).encode()).hexdigest()[:16] + ".sock")


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
        if self.path not in ("/write", "/publish", "/retire", "/reserve", "/release"):
            return self._reply(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            m = Matter(self.matter.root)          # fresh state for every request
            if self.path in ("/reserve", "/release"):
                from . import numbers
                if self.path == "/release":
                    return self._reply(200, numbers.release(m, annotations.CLAUDE, str(req.get("reservation", ""))))
                local = [(int(a), int(b), str(c) if c else None) for a, b, c in req.get("local", [])]
                return self._reply(200, numbers.reserve(m, annotations.CLAUDE, str(req["series"]), int(req["count"]),
                                                        req.get("start"), req.get("name"), local,
                                                        req.get("requested_by")))
            if self.path != "/write":
                from . import work
                if self.path == "/publish":
                    return self._reply(200, work.publish(m, annotations.CLAUDE, str(req.get("name", "")),
                                                         req.get("revises"), bool(req.get("merge")),
                                                         req.get("requested_by")))
                return self._reply(200, work.retire(m, annotations.CLAUDE, str(req.get("name", "")),
                                                    req.get("requested_by")))
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


def serve(m: Matter, path: Path | None = None):
    handler = type("Handler", (_Handler,), {"matter": m})
    with endpoint.http_server(path or socket_path(m), handler, "annotation-api", mode=0o660) as srv:
        srv.serve_forever()


class _Connection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost")
        self._path = path

    def connect(self):
        self.sock = endpoint.connect(self._path)


class Unavailable(Exception):
    pass


def request(m: Matter, req: dict, route="/write") -> dict:
    """Send a write to the API. Raises Unavailable if it isn't running, and
    annotations.Refused if the write was refused."""
    path = socket_path(m)
    conn = _Connection(path)
    try:
        body = json.dumps(req).encode()
        conn.request("POST", route, body, {"Content-Type": "application/json"})
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
