"""Recording a session: the Claude API, through a recorder that keeps what
it answered. See docs/SCREENSHOTS.md.

It stands where fakeapi.py stands in a replay (Claude Code's
ANTHROPIC_BASE_URL, on a socket the session reaches through `adhoc open
--api-socket`), forwards each request to the Claude API with a credential
of its own, streams the answer back as it comes, and keeps it: the main
conversation's answers in order, with the prompt and tool results that led
to each, and side requests' answers by kind. The session never holds the
credential.

The credential, the first that is there:

- GitHub Actions with Workload Identity Federation: ACTIONS_ID_TOKEN_REQUEST_URL
  and _TOKEN (the job has `id-token: write`) and ANTHROPIC_FEDERATION_RULE_ID,
  ANTHROPIC_ORGANIZATION_ID, ANTHROPIC_SERVICE_ACCOUNT_ID (and
  ANTHROPIC_WORKSPACE_ID): a fresh GitHub token for each exchange, since
  each can be exchanged once.
- The same federation variables with ANTHROPIC_IDENTITY_TOKEN_FILE.
- ANTHROPIC_API_KEY.
"""

import http.client
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import fakeapi

# where the requests go: the Claude API (another address only to test the recorder)
UPSTREAM = os.environ.get("ADHOC_RECORD_UPSTREAM", "https://api.anthropic.com")


def connect(timeout: float) -> http.client.HTTPConnection:
    scheme, _, host = UPSTREAM.partition("://")
    return (http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection)(host, timeout=timeout)
AUDIENCE = "https://api.anthropic.com"
# A federated token is an OAuth access token (sk-ant-oat01-…): the API takes it with this beta
# on each request, and the exchange with the federation one too (as Claude Code sends them).
OAUTH_BETA = "oauth-2025-04-20"
FEDERATION_BETA = "oidc-federation-2026-04-01"
# Betas Claude Code sends, believing it has an API key, that a federated token's request is refused
# for: with `claude-code-20250219`, the API answers "Your credit balance is too low" though the
# organization has credit (anthropic-wif-test.yml tries each beta alone). Claude Code signing in
# with federation itself doesn't run into it.
FEDERATED_DROP_BETAS = {"claude-code-20250219"}


def upstream_headers(incoming: dict, credential: dict, federated: bool) -> dict:
    """The headers a request goes to the API with: the client's, less hop-by-hop ones and its
    own credential, plus ours; and for a federated token, the OAuth beta and not the betas
    it's refused for."""
    headers = {k: v for k, v in incoming.items() if k.lower() not in DROP_HEADERS}
    headers["accept-encoding"] = "identity"
    headers.update(credential)
    if federated:
        betas = [b.strip() for k, v in headers.items() if k.lower() == "anthropic-beta"
                 for b in v.split(",") if b.strip() and b.strip() not in FEDERATED_DROP_BETAS]
        headers = {k: v for k, v in headers.items() if k.lower() != "anthropic-beta"}
        headers["anthropic-beta"] = ",".join(dict.fromkeys(betas + [OAUTH_BETA]))
    return headers
DROP_HEADERS = {"host", "content-length", "connection", "authorization", "x-api-key", "accept-encoding",
                "proxy-connection", "keep-alive", "transfer-encoding"}


# -- the credential ----------------------------------------------------------------------------------

class Credential:
    def __init__(self):
        e = os.environ
        self.fed = {k: e.get(k) for k in ("ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID",
                                          "ANTHROPIC_SERVICE_ACCOUNT_ID", "ANTHROPIC_WORKSPACE_ID")}
        self.github = bool(e.get("ACTIONS_ID_TOKEN_REQUEST_URL") and e.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN"))
        self.token_file = e.get("ANTHROPIC_IDENTITY_TOKEN_FILE")
        self.api_key = e.get("ANTHROPIC_API_KEY")
        federated = all(self.fed[k] for k in ("ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID",
                                              "ANTHROPIC_SERVICE_ACCOUNT_ID"))
        self.kind = ("federation (GitHub)" if federated and self.github else
                     "federation (token file)" if federated and self.token_file else
                     "API key" if self.api_key else None)
        if not self.kind:
            raise RuntimeError("recording needs a credential: Workload Identity Federation (the "
                               "ANTHROPIC_FEDERATION_* variables, in a GitHub job with id-token: write or with "
                               "ANTHROPIC_IDENTITY_TOKEN_FILE) or ANTHROPIC_API_KEY")
        self.token, self.until = None, 0.0
        self.lock = threading.Lock()

    def identity_token(self) -> str:
        if self.github:
            req = urllib.request.Request(f"{os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']}&audience={AUDIENCE}",
                                         headers={"Authorization": f"Bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())["value"]
        return Path(self.token_file).read_text(encoding="utf-8").strip()

    def headers(self) -> dict:
        if self.kind == "API key":
            return {"x-api-key": self.api_key}
        with self.lock:
            if not self.token or time.time() > self.until:
                body = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": self.identity_token(),
                        "federation_rule_id": self.fed["ANTHROPIC_FEDERATION_RULE_ID"],
                        "organization_id": self.fed["ANTHROPIC_ORGANIZATION_ID"],
                        "service_account_id": self.fed["ANTHROPIC_SERVICE_ACCOUNT_ID"]}
                if self.fed["ANTHROPIC_WORKSPACE_ID"]:
                    body["workspace_id"] = self.fed["ANTHROPIC_WORKSPACE_ID"]
                c = connect(30)
                c.request("POST", "/v1/oauth/token", json.dumps(body),
                          {"content-type": "application/json", "anthropic-beta": f"{OAUTH_BETA},{FEDERATION_BETA}"})
                r = c.getresponse()
                data = json.loads(r.read() or b"{}")
                if r.status != 200 or not data.get("access_token"):
                    # the reason is on the Console's Workload identity history page; the body says little
                    raise RuntimeError(f"token exchange failed ({r.status}): {data.get('error', data)}")
                self.token = data["access_token"]
                self.until = time.time() + max(60, int(data.get("expires_in", 600)) - 120)
            return {"authorization": f"Bearer {self.token}"}


# -- putting a streamed answer back together ---------------------------------------------------------

class Message:
    """A Messages API answer, rebuilt from its server-sent events."""

    def __init__(self):
        self.msg: dict = {}
        self.blocks: dict[int, dict] = {}
        self.partial: dict[int, str] = {}
        self.buf = b""

    def feed(self, data: bytes):
        self.buf += data
        while b"\n\n" in self.buf:
            event, self.buf = self.buf.split(b"\n\n", 1)
            for line in event.split(b"\n"):
                if line.startswith(b"data:"):
                    try:
                        self.on(json.loads(line[5:].strip()))
                    except ValueError:
                        pass

    def on(self, e: dict):
        t = e.get("type")
        if t == "message_start":
            self.msg = dict(e["message"])
        elif t == "content_block_start":
            self.blocks[e["index"]] = dict(e["content_block"])
        elif t == "content_block_delta":
            b, d = self.blocks.setdefault(e["index"], {}), e["delta"]
            dt = d.get("type")
            if dt == "text_delta":
                b["text"] = b.get("text", "") + d["text"]
            elif dt == "thinking_delta":
                b["thinking"] = b.get("thinking", "") + d["thinking"]
            elif dt == "signature_delta":
                b["signature"] = d["signature"]
            elif dt == "input_json_delta":
                self.partial[e["index"]] = self.partial.get(e["index"], "") + d["partial_json"]
        elif t == "content_block_stop":
            i = e["index"]
            if i in self.partial:
                try:
                    self.blocks[i]["input"] = json.loads(self.partial.pop(i) or "{}")
                except ValueError:
                    self.blocks[i]["input"] = {}
        elif t == "message_delta":
            self.msg.update({k: v for k, v in (e.get("delta") or {}).items()})
            if e.get("usage"):
                self.msg["usage"] = {**(self.msg.get("usage") or {}), **e["usage"]}

    def result(self) -> dict:
        return {"content": [self.blocks[i] for i in sorted(self.blocks)],
                "stop_reason": self.msg.get("stop_reason"), "usage": self.msg.get("usage") or {}}


# -- the recorder --------------------------------------------------------------------------------------

class Recorder:
    """The same face as fakeapi.Replay toward scenario.py: turn_end, aliases,
    report(); and cassette(), what it kept."""

    def __init__(self, log_path: Path | None = None):
        self.cred = Credential()
        self.turns: list[dict] = []
        self.side: list[dict] = []
        self.model = None
        self.turn_end = threading.Event()
        self.aliases: dict[str, str] = {}
        self.problems: list[dict] = []
        self.fatal: list[str] = []         # the API or the credential refused: no answer is coming
        self.requests = 0
        self.lock = threading.Lock()
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None

    def note(self, **kw):
        if self.log:
            self.log.write(json.dumps({"at": round(time.time(), 3), **kw}) + "\n")
            self.log.flush()

    def unalias(self, s: str) -> str:
        for a, b in self.aliases.items():
            s = s.replace(a, b)
        return s

    def keep(self, body: dict, response: dict):
        with self.lock:
            if fakeapi.is_main(body):
                self.model = self.model or body.get("model")
                msg = fakeapi.last_user(body)
                prompt = fakeapi.typed_prompt(msg)
                turn = {**({"prompt": prompt} if prompt is not None else {}),
                        "results": [{**r, "content": self.unalias(r["content"])} for r in fakeapi.tool_results(msg)],
                        "response": response}
                self.turns.append(turn)
                if response.get("stop_reason") != "tool_use":
                    self.turn_end.set()
            else:
                self.side.append({"kind": fakeapi.side_kind(body), "response": response})

    def report(self) -> dict:
        return {"turns_recorded": len(self.turns), "turns_replayed": len(self.turns), "complete": True,
                "recorded": True, "credential": self.cred.kind, "problems": self.problems}

    def cassette(self, claude_code: str, note: str = "") -> dict:
        return {"version": 1, "claude_code": claude_code, "model": self.model,
                "recorded": time.strftime("%Y-%m-%d"), **({"note": note} if note else {}),
                "turns": self.turns, "side": self.side}


def handler_for(rec: Recorder):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def forward(self, method: str):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            path = self.path
            try:
                headers = upstream_headers(dict(self.headers.items()), rec.cred.headers(),
                                           federated=rec.cred.kind != "API key")
            except Exception as e:
                rec.problems.append({"kind": "credential", "detail": str(e)})
                rec.fatal.append(f"signing in ({rec.cred.kind}): {e}")
                rec.note(error=str(e))
                print(f"    api: signing in failed: {e}", file=sys.stderr, flush=True)
                body = json.dumps({"type": "error", "error": {"type": "authentication_error",
                                                              "message": f"recorder: {e}"}}).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                up = connect(900)
                up.request(method, path, raw or None, headers)
                r = up.getresponse()
            except OSError as e:
                rec.fatal.append(f"couldn't reach {UPSTREAM}: {e}")
                print(f"    api: couldn't reach {UPSTREAM}: {e}", file=sys.stderr, flush=True)
                body = json.dumps({"type": "error", "error": {"type": "api_error",
                                                              "message": f"recorder: {e}"}}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            ctype = r.getheader("content-type", "")
            self.send_response(r.status)
            for k in ("content-type", "request-id", "retry-after", "anthropic-ratelimit-requests-remaining"):
                if r.getheader(k):
                    self.send_header(k, r.getheader(k))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            is_messages = method == "POST" and path.split("?")[0].endswith("/v1/messages")
            body = json.loads(raw or b"{}") if is_messages else {}
            msg = Message() if is_messages and "event-stream" in ctype else None
            whole = b""
            while chunk := r.read1(65536):
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                if msg:
                    msg.feed(chunk)
                elif is_messages:
                    whole += chunk
            rec.requests += 1
            kind = ("main" if fakeapi.is_main(body) else "side") if is_messages else ""
            rec.note(method=method, path=path.split("?")[0], status=r.status, main=kind == "main" if kind else None)
            # in the job's log, so a failed recording says why without its artifact
            print(f"    api: {method} {path.split('?')[0]} {r.status} {kind}".rstrip(), file=sys.stderr, flush=True)
            if is_messages and r.status == 200:
                if msg:
                    rec.keep(body, msg.result())
                else:
                    m = json.loads(whole or b"{}")
                    rec.keep(body, {"content": m.get("content", []), "stop_reason": m.get("stop_reason"),
                                    "usage": m.get("usage") or {}})
            elif is_messages:
                detail = whole[:500].decode("utf-8", "replace")
                # what was asked, to tell why it was refused (nothing secret: no headers' values but the betas)
                asked = {"request_id": r.getheader("request-id"), "model": body.get("model"),
                         "max_tokens": body.get("max_tokens"), "speed": body.get("speed"),
                         "betas": headers.get("anthropic-beta"), "path": path}
                rec.problems.append({"kind": "API error", "status": r.status, "detail": detail, "asked": asked})
                rec.fatal.append(f"the Claude API answered {r.status}: {detail}")
                print(f"    api: {r.status}: {detail}\n    api: the request: {json.dumps(asked)}", file=sys.stderr, flush=True)

        def do_POST(self):
            self.forward("POST")

        def do_GET(self):
            self.forward("GET")
    return H


def serve(rec: Recorder, unix: str | None = None, listen: str | None = None):
    from http.server import ThreadingHTTPServer
    h = handler_for(rec)
    if unix:
        srv = fakeapi.UnixHTTPServer(unix, h)
    else:
        host, _, port = listen.rpartition(":")
        srv = ThreadingHTTPServer((host or "127.0.0.1", int(port)), h)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
