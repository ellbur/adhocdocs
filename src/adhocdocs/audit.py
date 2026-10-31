"""The audit log: what happened in each session, recorded outside Claude's reach.

It lives in ``<matter>/audit/``, which is never mounted into the session
container, and only host processes write it:

- ``adhoc open`` records each session's start (image, mounts, allowlist,
  who opened it) and end (exit status).
- The audit API, on a Unix socket mounted read-only into the session (so
  nothing there can replace it with a listener of its own), records what
  Claude Code's hooks send it: every prompt, every tool call before it runs,
  and its result after. The hooks are in the image's managed settings, which
  Claude can't change, and the one before a tool call fails closed: if the
  record can't be written, the call is blocked.
- The allowlist proxy records every connection the session asks for, allowed
  or refused, with the bytes each way.

Layout::

    <matter>/audit/
      log.jsonl     one event per line, hash-chained
      objects/      full tool results, by SHA-256 (the log keeps a preview)

Each line carries ``prev``, the SHA-256 of the line before it, so ``adhoc
audit verify`` finds any line edited, removed or inserted after the fact
(short of rewriting every line after it). Writers share the file under a
lock, so sessions can run at once.

What it does not prove: anything in the session can also post to the audit
socket, so a record "from the hooks" shows that it was reported, not that it
happened. What it does show is that nothing Claude Code ran is missing, and
that nothing written was changed from inside the session.
"""

import fcntl
import hashlib
import json
import os
import socketserver
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .matter import Matter, new_event_id, now

PREVIEW = 2000          # characters of a tool result kept in the log line itself
HOOK_EVENTS = {"SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
               "PostToolUseFailure", "PermissionDenied", "Stop", "SubagentStop", "PreCompact", "Notification"}


def audit_dir(m: Matter) -> Path:
    return m.root / "audit"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class AuditLog:
    """An append-only, hash-chained JSON Lines log."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "log.jsonl"
        self.objects = self.root / "objects"

    def append(self, **event) -> dict:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.path, "a+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            event = {"id": new_event_id(), "at": now(), **event, "prev": self._last_hash(f)}
            f.write(json.dumps(event, ensure_ascii=False).encode() + b"\n")
            f.flush()
            os.fsync(f.fileno())
        return event

    @staticmethod
    def _last_hash(f) -> str | None:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if not size:
            return None
        back = 65536
        while True:
            f.seek(max(0, size - back))
            lines = f.read().rstrip(b"\n").split(b"\n")
            if len(lines) > 1 or back >= size:
                return _sha(lines[-1])
            back *= 4

    def put(self, data: bytes) -> str:
        """Keep a blob (a full tool result); returns its SHA-256."""
        h = _sha(data)
        p = self.objects / h[:2] / h[2:]
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.rename(p)
        return h

    def get(self, h: str) -> bytes:
        return (self.objects / h[:2] / h[2:]).read_bytes()

    def read(self):
        if not self.path.exists():
            return
        with open(self.path, "rb") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def verify(self) -> dict:
        """Check the chain, the stored results, and that every tool call that
        was started has a result. Problems are listed, never repaired."""
        problems, prev, n, sessions = [], None, 0, {}
        if self.path.exists():
            with open(self.path, "rb") as f:
                for i, line in enumerate(f, 1):
                    line = line.rstrip(b"\n")
                    if not line:
                        continue
                    n += 1
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        problems.append({"line": i, "problem": "not JSON"})
                        prev = _sha(line)
                        continue
                    if ev.get("prev") != prev:
                        problems.append({"line": i, "id": ev.get("id"), "problem": "chain broken: an earlier line was changed, removed or inserted"})
                    prev = _sha(line)
                    if (h := ev.get("result_sha256")) and ev.get("result_stored"):
                        try:
                            if _sha(self.get(h)) != h:
                                problems.append({"line": i, "id": ev["id"], "problem": "stored result altered"})
                        except FileNotFoundError:
                            problems.append({"line": i, "id": ev["id"], "problem": "stored result missing"})
                    s = sessions.setdefault(ev.get("session"), {"started": set(), "finished": set(), "ended": False})
                    if ev.get("kind") == "tool" and ev.get("phase") == "before":
                        s["started"].add(ev.get("tool_use_id"))
                    elif ev.get("kind") == "tool":
                        s["finished"].add(ev.get("tool_use_id"))
                    elif ev.get("kind") == "session-end":
                        s["ended"] = True
        unfinished = [{"session": k, "tool_use_id": t} for k, s in sessions.items()
                      for t in sorted(s["started"] - s["finished"], key=str)]
        open_sessions = [k for k, s in sessions.items() if k and not s["ended"]]
        return {"events": n, "ok": not problems, "problems": problems,
                # Not problems in themselves: interrupted calls, or a session still running or killed.
                "tool_calls_without_result": unfinished, "sessions_without_end": open_sessions}


# -- what the hooks send ------------------------------------------------------------------------

def from_hook(log: AuditLog, session: str, payload: dict) -> dict:
    """Turn a Claude Code hook payload into an audit event. Tool results are
    stored whole as objects; the line keeps their hash, size and a preview."""
    name = payload.get("hook_event_name")
    if name not in HOOK_EVENTS:
        raise ValueError(f"not a hook event: {name!r}")
    ev = {"session": session, "source": "hook", "claude_session": payload.get("session_id")}
    if payload.get("agent_id"):                  # a subagent's call
        ev.update(agent_id=payload["agent_id"], agent_type=payload.get("agent_type"))
    if name in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied"):
        phase = {"PreToolUse": "before", "PostToolUse": "after", "PostToolUseFailure": "failed"}.get(name, "denied")
        ev.update(kind="tool", phase=phase,
                  tool=payload.get("tool_name"), tool_use_id=payload.get("tool_use_id"),
                  input=payload.get("tool_input"))
        if name in ("PostToolUse", "PostToolUseFailure"):
            if payload.get("is_interrupt"):
                ev["interrupted"] = True
            result = payload.get("tool_response", payload.get("error"))
            data = (result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)).encode()
            text = data.decode(errors="replace")
            ev.update(result_sha256=log.put(data), result_stored=True, result_bytes=len(data),
                      result_preview=text[:PREVIEW] + ("…" if len(text) > PREVIEW else ""))
    elif name == "UserPromptSubmit":
        ev.update(kind="prompt", prompt=payload.get("prompt"))
    else:
        ev.update(kind="claude", event=name)
        ev.update({k: payload[k] for k in ("reason", "trigger", "message") if k in payload})
        if "source" in payload:                  # SessionStart: startup, resume, clear or compact
            ev["start_source"] = payload["source"]
    return log.append(**ev)


class _Handler(BaseHTTPRequestHandler):
    log: AuditLog
    session: str

    def log_message(self, fmt, *args):
        pass

    def _reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/hook":
            return self._reply(404, {"error": "not found"})
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            ev = from_hook(self.log, self.session, payload)
            self._reply(200, {"recorded": ev["id"]})
        except Exception as e:
            self._reply(400, {"error": f"{type(e).__name__}: {e}"})


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        sock, _ = super().get_request()
        return sock, ("audit-api", 0)


def serve(m: Matter, path: Path, session: str):
    """The audit API for one session, on a Unix socket: ``POST /hook`` with
    a hook's JSON payload."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        path.unlink()
    handler = type("Handler", (_Handler,), {"log": AuditLog(audit_dir(m)), "session": session})
    with _Server(str(path), handler) as srv:
        os.chmod(path, 0o600)
        srv.serve_forever()


# -- reading it -----------------------------------------------------------------------------------

def sessions(m: Matter) -> list[dict]:
    """One summary per session: when, who, how long, and counts."""
    out = {}
    for ev in AuditLog(audit_dir(m)).read():
        s = out.setdefault(ev.get("session"), {"session": ev.get("session"), "first": ev["at"], "prompts": 0,
                                                "tool_calls": 0, "connections": 0, "refused": 0})
        s["last"] = ev["at"]
        k = ev.get("kind")
        if k == "session-start":
            s.update(started=ev["at"], user=ev.get("user"), command=ev.get("command"))
        elif k == "session-end":
            s.update(ended=ev["at"], exit=ev.get("exit"))
        elif k == "prompt":
            s["prompts"] += 1
        elif k == "tool" and ev.get("phase") == "before":
            s["tool_calls"] += 1
        elif k == "connection":
            s["connections"] += ev.get("decision") == "allowed"
            s["refused"] += ev.get("decision") == "refused"
    return list(out.values())


def show(m: Matter, session=None, kinds=None, full=False):
    log = AuditLog(audit_dir(m))
    for ev in log.read():
        if session and ev.get("session") != session:
            continue
        if kinds and ev.get("kind") not in kinds:
            continue
        if full and ev.get("result_stored"):
            ev = {**ev, "result": log.get(ev["result_sha256"]).decode(errors="replace")}
            ev.pop("result_preview", None)
        yield ev
