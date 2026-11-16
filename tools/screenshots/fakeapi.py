"""A stand-in for the Claude API that replays a recorded session, so Claude
Code can run in CI with no sign-in and the same answers every time. See
docs/SCREENSHOTS.md.

    python fakeapi.py replay CASSETTE --unix SOCK | --listen HOST:PORT [--log LOG] [--report REPORT]

Claude Code is pointed at it with ANTHROPIC_BASE_URL and any API key. Each
request of the main conversation (one that offers tools) is answered with
the next recorded turn, streamed the way the API streams it, at a pace set
for watching rather than by how long the model took. Other requests (a
title for the conversation, a check of a command) get the recorded answer
to the same kind of request, or a short empty one.

Before answering a request, the replay compares what Claude Code sends with
what was recorded: the prompt the user typed, and each tool's result (what
`search`, `view` … printed this time). Where they differ the session has
gone stale: the software changed since the recording, and Claude's
recorded words may describe something no longer on the screen. Every
difference goes in the report; the harness fails the scenario on any.

A cassette is JSON:

    {"version": 1, "claude_code": "2.1.325", "model": "claude-opus-5-5",
     "turns": [{"prompt": "…",              # the user's words that start it, if any
                "results": [{"tool_use_id": "…", "content": "…"}],   # what the request before must carry
                "response": {"content": [...], "stop_reason": "tool_use", "usage": {…}}}, …],
     "side": [{"kind": "…", "response": {…}}, …]}
"""

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# -- what a request is -------------------------------------------------------------------------

def is_main(body: dict) -> bool:
    """The main conversation offers tools; side requests (titles, summaries,
    checks of a command) don't."""
    return bool(body.get("tools"))


def side_kind(body: dict) -> str:
    """Side requests by their system prompt's first words, which say what
    they are for (and don't change between runs)."""
    system = body.get("system") or ""
    if isinstance(system, list):
        system = " ".join(b.get("text", "") for b in system if isinstance(b, dict))
    words = re.sub(r"\s+", " ", system).strip()[:200]
    return hashlib.sha256(words.encode()).hexdigest()[:12] + " " + words[:60]


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b.get("text", ""))
            elif b.get("type") == "tool_result":
                out.append(text_of(b.get("content")))
    return "\n".join(out)


def last_user(body: dict) -> dict | None:
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user":
            return m
    return None


def typed_prompt(msg: dict | None) -> str | None:
    """The words the user typed, from the last user message: its text blocks
    that aren't Claude Code's own reminders."""
    if not msg:
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    texts = [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
    texts = [t for t in texts if not t.lstrip().startswith("<system-reminder>")]
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content or []):
        return None
    return "\n".join(texts).strip() or None


def tool_results(msg: dict | None) -> list[dict]:
    if not msg or isinstance(msg.get("content"), str):
        return []
    return [{"tool_use_id": b.get("tool_use_id"), "content": text_of(b.get("content")),
             **({"is_error": True} if b.get("is_error") else {})}
            for b in msg.get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_result"]


def normalize(s: str) -> str:
    """What a tool printed, without what changes from run to run on its own:
    times, durations, temporary paths, process IDs."""
    s = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?", "<time>", s)
    s = re.sub(r"\b\d+(\.\d+)?\s?(ms|s|seconds)\b", "<duration>", s)
    s = re.sub(r"/tmp/[^\s\"']+", "<tmp>", s)
    s = re.sub(r"\bpid \d+", "pid <n>", s)
    # event, object and matter IDs (ULIDs: they carry the time they were made)
    s = re.sub(r"\b([a-z]+)_[0-9A-HJKMNP-TV-Z]{26}\b", r"\1_<id>", s)
    return re.sub(r"[ \t]+\n", "\n", s).strip()


# -- streaming a response ----------------------------------------------------------------------

class Pace:
    """How fast a replayed answer appears: a pause before it starts (the
    model thinking), then text at a reading pace, tool calls quickly."""
    def __init__(self, think=1.2, chars_per_second=180.0, tool_seconds=0.4):
        self.think, self.cps, self.tool = think, chars_per_second, tool_seconds


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def stream(write, response: dict, model: str, pace: Pace, msg_id: str):
    """Write ``response`` (a Messages API message) as the API streams one."""
    usage = response.get("usage") or {"input_tokens": 10, "output_tokens": 10}
    time.sleep(pace.think)
    write(sse("message_start", {"type": "message_start", "message": {
        "id": msg_id, "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {k: v for k, v in usage.items() if k != "output_tokens"} | {"output_tokens": 1}}}))
    for i, block in enumerate(response.get("content") or []):
        kind = block.get("type")
        if kind == "text":
            write(sse("content_block_start", {"type": "content_block_start", "index": i,
                                              "content_block": {"type": "text", "text": ""}}))
            text = block.get("text", "")
            step = max(1, int(pace.cps * 0.03)) if pace.cps else len(text) or 1
            for j in range(0, len(text), step):
                write(sse("content_block_delta", {"type": "content_block_delta", "index": i,
                                                  "delta": {"type": "text_delta", "text": text[j:j + step]}}))
                if pace.cps:
                    time.sleep(step / pace.cps)
        elif kind == "thinking":
            write(sse("content_block_start", {"type": "content_block_start", "index": i,
                                              "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
            if block.get("thinking"):
                write(sse("content_block_delta", {"type": "content_block_delta", "index": i,
                                                  "delta": {"type": "thinking_delta", "thinking": block["thinking"]}}))
            write(sse("content_block_delta", {"type": "content_block_delta", "index": i,
                                              "delta": {"type": "signature_delta", "signature": block.get("signature", "replayed")}}))
        elif kind == "tool_use":
            write(sse("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}))
            raw = json.dumps(block.get("input") or {})
            n = 8
            for j in range(n):
                part = raw[j * len(raw) // n:(j + 1) * len(raw) // n]
                if part:
                    write(sse("content_block_delta", {"type": "content_block_delta", "index": i,
                                                      "delta": {"type": "input_json_delta", "partial_json": part}}))
                time.sleep(pace.tool / n)
        else:
            write(sse("content_block_start", {"type": "content_block_start", "index": i, "content_block": block}))
        write(sse("content_block_stop", {"type": "content_block_stop", "index": i}))
    write(sse("message_delta", {"type": "message_delta",
                                "delta": {"stop_reason": response.get("stop_reason", "end_turn"), "stop_sequence": None},
                                "usage": {"output_tokens": usage.get("output_tokens", 10)}}))
    write(sse("message_stop", {"type": "message_stop"}))


def empty_response(body: dict) -> dict:
    return {"content": [{"type": "text", "text": ""}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1}}


# -- the replay --------------------------------------------------------------------------------

class Replay:
    def __init__(self, cassette: dict, pace: Pace, log_path: Path | None):
        self.c = cassette
        self.pace = pace
        self.turn = 0
        self.side_used: dict[str, int] = {}
        self.problems: list[dict] = []
        self.lock = threading.Lock()
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None
        self.n = 0
        self.fatal: list[str] = []         # (the recorder's: a replay always answers)
        # set when a main-conversation answer that doesn't call a tool has been sent: Claude's turn is over
        self.turn_end = threading.Event()
        self.aliases: dict[str, str] = {}     # host paths -> what a session sees (/matter)
        self.seen: list[dict] = []            # per turn: the prompt and tool results this replay sent

    def note(self, **kw):
        if self.log:
            self.log.write(json.dumps({"at": round(time.time(), 3), **kw}) + "\n")
            self.log.flush()

    def problem(self, **kw):
        self.problems.append(kw)
        log("stale:", json.dumps(kw)[:300])

    def answer(self, body: dict) -> tuple[dict, str]:
        """The response for a messages request, and a label for the log."""
        with self.lock:
            self.n += 1
            if not is_main(body):
                kind = side_kind(body)
                recorded = [s for s in self.c.get("side", []) if s.get("kind") == kind]
                i = self.side_used.get(kind, 0)
                self.side_used[kind] = i + 1
                if recorded:
                    return recorded[min(i, len(recorded) - 1)]["response"], f"side {kind}"
                return empty_response(body), f"side (unrecorded) {kind}"
            turns = self.c.get("turns", [])
            if self.turn >= len(turns):
                self.problem(turn=self.turn, kind="extra request",
                             detail="Claude Code asked for more turns than were recorded")
                return {"content": [{"type": "text", "text": "(the recording ends here)"}],
                        "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}, "main (past the end)"
            t = turns[self.turn]
            self.check(self.turn, t, body)
            self.turn += 1
            return t["response"], f"main {self.turn - 1}"

    def unalias(self, s: str) -> str:
        for a, b in self.aliases.items():
            s = s.replace(a, b)
        return s

    def check(self, i: int, t: dict, body: dict):
        msg = last_user(body)
        got_prompt = typed_prompt(msg)
        got_results = [{**r, "content": self.unalias(r["content"])} for r in tool_results(msg)]
        self.seen.append({"prompt": got_prompt, "results": got_results})
        if t.get("prompt") is not None:
            got = got_prompt
            if (got or "").strip() != t["prompt"].strip():
                self.problem(turn=i, kind="prompt", recorded=t["prompt"], replayed=got)
        want = t.get("results") or []
        got = got_results
        if len(want) != len(got):
            self.problem(turn=i, kind="tool results", detail=f"recorded {len(want)}, replayed {len(got)}")
            return
        for w, g in zip(want, got):
            if normalize(w["content"]) != normalize(g["content"]) or bool(w.get("is_error")) != bool(g.get("is_error")):
                self.problem(turn=i, kind="tool result", tool_use_id=w["tool_use_id"],
                             recorded=w["content"][:2000], replayed=g["content"][:2000])

    def served(self, label: str, response: dict):
        if label.startswith("main") and response.get("stop_reason") != "tool_use":
            self.turn_end.set()

    def report(self) -> dict:
        return {"turns_recorded": len(self.c.get("turns", [])), "turns_replayed": self.turn,
                "complete": self.turn == len(self.c.get("turns", [])), "problems": self.problems}

    def accepted(self) -> dict:
        """The cassette with the prompts and tool results this replay saw in
        place of the recorded ones: for writing a recording by hand (the
        model's side), never for hiding a stale one."""
        turns = [dict(t) for t in self.c.get("turns", [])]
        for t, s in zip(turns, self.seen):
            if s["prompt"] is not None:
                t["prompt"] = s["prompt"]
            t["results"] = s["results"]
        return {**self.c, "turns": turns}


def handler_for(replay: Replay):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                return {}

        def reply_json(self, code: int, data: dict):
            raw = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            replay.note(method="GET", path=self.path)
            self.reply_json(404, {"type": "error", "error": {"type": "not_found_error", "message": "replay: not recorded"}})

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            path = self.path.split("?")[0]
            body = self.body()
            if os.environ.get("FAKEAPI_DUMP"):         # debugging: every request's body, in order
                d = Path(os.environ["FAKEAPI_DUMP"])
                d.mkdir(parents=True, exist_ok=True)
                n = len(list(d.iterdir())) + 1
                (d / f"{n:03d}{path.replace('/', '_')}.json").write_text(json.dumps(
                    {"path": self.path, "headers": {k: v for k, v in self.headers.items()
                                                    if k.lower() not in ("authorization", "x-api-key")},
                     "body": body}, indent=1), encoding="utf-8")
            if path.endswith("/v1/messages/count_tokens"):
                replay.note(method="POST", path=path)
                return self.reply_json(200, {"input_tokens": 1000})
            if not path.endswith("/v1/messages"):
                replay.note(method="POST", path=path)
                return self.reply_json(404, {"type": "error", "error": {"type": "not_found_error",
                                                                        "message": "replay: not recorded"}})
            response, label = replay.answer(body)
            model = body.get("model") or replay.c.get("model") or "claude-opus-5-5"
            replay.note(method="POST", path=path, answer=label, stream=bool(body.get("stream")),
                        model=model, tools=len(body.get("tools") or []), messages=len(body.get("messages") or []))
            msg_id = f"msg_replay{replay.n:04d}"
            if not body.get("stream"):
                self.reply_json(200, {"id": msg_id, "type": "message", "role": "assistant", "model": model,
                                      "stop_sequence": None, **response})
                return replay.served(label, response)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            main = label.startswith("main")
            try:
                stream(lambda b: (self.wfile.write(b), self.wfile.flush()), response, model,
                       replay.pace if main else Pace(0, 0, 0), msg_id)
            except (BrokenPipeError, ConnectionResetError):
                pass
            replay.served(label, response)
    return H


class UnixHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_UNIX

    def server_bind(self):
        try:
            os.unlink(self.server_address)
        except FileNotFoundError:
            pass
        self.socket.bind(self.server_address)
        os.chmod(self.server_address, 0o666)

    def get_request(self):
        req, _ = self.socket.accept()
        return req, ("unix", 0)


def serve(replay: Replay, unix: str | None = None, listen: str | None = None) -> ThreadingHTTPServer:
    h = handler_for(replay)
    if unix:
        srv = UnixHTTPServer(unix, h)
    else:
        host, _, port = listen.rpartition(":")
        srv = ThreadingHTTPServer((host or "127.0.0.1", int(port)), h)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay")
    r.add_argument("cassette")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--unix")
    g.add_argument("--listen")
    r.add_argument("--log")
    r.add_argument("--report", help="written when stopped (SIGTERM or Ctrl-C)")
    r.add_argument("--think", type=float, default=1.2)
    r.add_argument("--cps", type=float, default=180.0)
    a = p.parse_args()
    cassette = json.loads(Path(a.cassette).read_text(encoding="utf-8")) if a.cassette != "-" else {"turns": []}
    replay = Replay(cassette, Pace(a.think, a.cps), Path(a.log) if a.log else None)
    srv = serve(replay, a.unix, a.listen)
    import signal
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    try:
        done.wait()
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    if a.report:
        Path(a.report).write_text(json.dumps(replay.report(), indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
