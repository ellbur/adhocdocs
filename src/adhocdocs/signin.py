"""Signing in with the browser, across the container boundary.

Claude Code signs in by opening the browser on Anthropic's sign-in page,
with a ``redirect_uri`` of ``http://localhost:<port>/callback`` where it
waits for the browser to come back. In the session there is no browser and
no network, so on its own it falls back to showing a link and asking for the
code to be pasted. This module lets the user's own browser do it, once per
``adhoc open``:

1. The session sets ``BROWSER=adhoc-browser`` (``python -m adhocdocs.signin
   browser URL``). It starts a relay in the session, from
   ``/run/adhoc/svc-signin.sock`` to Claude Code's ``127.0.0.1:<port>``, for
   one connection, and hands the URL to the host on ``signin.sock``.
2. The host (``serve``, started by the launcher) checks the URL: Anthropic's
   sign-in page, a callback to localhost and nothing else. The first one it
   accepts is the only one: any later request is refused, whether or not the
   first worked. It listens on ``127.0.0.1:<port>`` on the host and opens
   the URL in the user's browser.
3. The browser comes back to the host's port. The host forwards one request,
   ``GET /callback?…`` with the ``state`` the URL carried, to the session,
   as a request line and a ``Host`` header only: the browser's cookies and
   other headers stay on the host. It never logs or keeps the query, which
   carries the one-time code meant for Claude Code.
4. What reaches the browser is the host's: a redirect, if Claude Code sent
   one to an Anthropic site, or else a fixed page. Nothing the session
   writes is shown or run in the browser. Then the port closes.

So the session can show the user one Anthropic sign-in page per ``adhoc
open`` and receive one callback. It can't open anything else, send the
browser elsewhere, or use this as a channel out. Each step goes in the
audit log. If anything here fails, Claude Code's own fallback (the link,
and the code pasted back) still works.

    python -m adhocdocs.signin serve --socket <run>/signin.sock --sessions <run> [--audit DIR --session ID]   (host)
    python -m adhocdocs.signin browser URL                                                             (session)
"""

import argparse
import asyncio
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import endpoint
from .portable import spawn
from .proxy import pipe

# Where Claude Code's sign-in page is, and where it may send the browser after.
HOSTS = ("claude.com", "claude.ai", "platform.claude.com")
CALLBACK = re.compile(r"http://localhost:(\d{4,5})/callback")
STATE = re.compile(r"[A-Za-z0-9._~-]{8,256}")
QUERY = re.compile(r"[A-Za-z0-9._~%&=+-]{1,4096}")
LIFETIME = 600                # seconds the host's port and the session's relay stay open
EXCHANGE_TIMEOUT = 120        # Claude Code exchanges the code before it answers the browser
MAX_URL = 4096
SERVICE = "signin"            # the session's relay: /run/adhoc/svc-signin.sock

DONE_PAGE = ("Claude Code has the sign-in. Whether it worked shows in the terminal.\n"
             "You can close this tab.\n")


def log(**kw):
    print(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}), file=sys.stderr, flush=True)


def check_url(url: str) -> tuple[int, str]:
    """``(port, state)`` from a sign-in URL Claude Code may open. Raises
    ValueError, saying why, for anything else."""
    if len(url) > MAX_URL or not url.isascii() or not url.isprintable() or " " in url:
        raise ValueError("not a plain URL")
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname not in HOSTS or u.netloc != u.hostname:
        raise ValueError("not Anthropic's sign-in page")
    if not u.path.endswith("/oauth/authorize") or u.fragment:
        raise ValueError("not Anthropic's sign-in page")
    q = parse_qs(u.query, keep_blank_values=True)
    one = lambda k: q[k][0] if len(q.get(k, ())) == 1 else None
    m = CALLBACK.fullmatch(one("redirect_uri") or "")
    if not m or not 1024 <= int(m[1]) <= 65535:
        raise ValueError("the callback isn't localhost")
    if one("response_type") != "code" or not STATE.fullmatch(one("state") or ""):
        raise ValueError("not a sign-in request")
    return int(m[1]), one("state")


def safe_redirect(status: int, location: str | None) -> bool:
    """Whether Claude Code's answer is a redirect the browser may follow:
    to an Anthropic site, over HTTPS."""
    if not (300 <= status < 400 and location and location.isascii() and location.isprintable()
            and " " not in location):
        return False
    try:
        u = urlsplit(location)
    except ValueError:
        return False
    return u.scheme == "https" and u.hostname in HOSTS and u.netloc == u.hostname


def browser_response(status: int, location: str | None) -> bytes:
    """What the browser gets back: Claude Code's redirect if it's safe, else a fixed page."""
    if safe_redirect(status, location):
        return (f"HTTP/1.1 302 Found\r\nLocation: {location}\r\n"
                "Content-Length: 0\r\nConnection: close\r\n\r\n").encode()
    return page(200, DONE_PAGE)


def host_of(url: str) -> str | None:
    """The host name in ``url``, for the log; never the rest of it."""
    try:
        return urlsplit(url).hostname if url.isascii() else None
    except ValueError:
        return None


def page(status: int, text: str) -> bytes:
    body = text.encode()
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 410: "Gone", 502: "Bad Gateway"}[status]
    return (f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n").encode() + body


async def read_head(reader, timeout) -> bytes:
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)


# -- on the host -----------------------------------------------------------------------------------

def open_in_browser(url: str):
    import webbrowser
    webbrowser.open(url)


class Host:
    """The host's side: one sign-in per session. ``sessions`` is the
    session's socket directory, where ``svc-signin.sock`` (or, on Windows and
    macOS, the bridge's ``svc.endpoint``) reaches the relay in the session.
    ``bind`` is for tests, where the session's loopback is the host's."""

    def __init__(self, sessions: Path, opener=open_in_browser, audit=None, bind="127.0.0.1",
                 lifetime=LIFETIME):
        self.sessions, self.opener, self.audit, self.bind, self.lifetime = Path(sessions), opener, audit, bind, lifetime
        self.used = False
        self.state = None
        self.port = None
        self.callback = None          # the server on the host's port, while it's open
        self.forwarded = False

    def record(self, **kw):
        log(**kw)
        if self.audit:
            self.audit[0].append(session=self.audit[1], source="signin", kind="signin", **kw)

    async def request(self, reader, writer):
        """The session asks for the browser: one line, the URL."""
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            url = line.decode("ascii").strip()
        except (asyncio.TimeoutError, ConnectionError, ValueError, asyncio.LimitOverrunError, UnicodeDecodeError):
            writer.close()
            return
        answer = await self.open(url)
        writer.write(answer.encode() + b"\n")
        try:
            await writer.drain()
        except ConnectionError:
            pass
        writer.close()

    async def open(self, url: str) -> str:
        if self.used:
            self.record(event="refused", reason="the browser was already opened in this session")
            return "refused: the browser can be opened once per `adhoc open`; paste the code instead"
        try:
            port, state = check_url(url)
        except ValueError as e:
            self.record(event="refused", reason=str(e), host=host_of(url))
            return f"refused: {e}"
        self.used = True              # the first acceptable request is the only one, however it ends
        self.port, self.state = port, state
        try:
            self.callback = await asyncio.start_server(self.from_browser, self.bind, port, limit=16384)
        except OSError as e:
            self.record(event="refused", reason="the callback port is busy on this computer", port=port)
            return f"refused: port {port} is busy here ({e.strerror or e}); paste the code instead"
        asyncio.get_running_loop().call_later(self.lifetime, self.close)
        try:
            await asyncio.get_running_loop().run_in_executor(None, self.opener, url)
        except Exception as e:
            self.close()
            self.record(event="failed", reason=f"couldn't open the browser: {e}")
            return "refused: couldn't open the browser here; paste the code instead"
        self.record(event="browser-opened", host=host_of(url), port=port)
        return "ok"

    def close(self):
        if self.callback:
            self.callback.close()
            self.callback = None

    async def from_browser(self, reader, writer):
        try:
            try:
                head = await read_head(reader, 30)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
                return
            writer.write(await self.answer(head))
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async def answer(self, head: bytes) -> bytes:
        """The browser's request in, the browser's response out."""
        parts = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
        if len(parts) != 3 or parts[0] != "GET" or not parts[2].startswith("HTTP/1."):
            return page(400, "Not a sign-in.\n")
        path, _, query = parts[1].partition("?")
        if path != "/callback":
            return page(404, "Not found.\n")      # the favicon, and the like: never forwarded
        if not QUERY.fullmatch(query):
            return page(400, "Not a sign-in.\n")
        state = parse_qs(query).get("state", [""])
        if len(state) != 1 or not hmac.compare_digest(state[0].encode(), self.state.encode()):
            # Claude Code gives up on a wrong state: don't let a stray request end the sign-in
            self.record(event="callback-refused", reason="not this sign-in's state")
            return page(400, "Not this sign-in.\n")
        if self.forwarded:
            return page(410, "This sign-in was already passed on. You can close this tab.\n")
        self.forwarded = True
        self.close()                  # one callback: the port stops accepting
        try:
            status, location = await self.forward(f"/callback?{query}")
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as e:
            self.record(event="callback-failed", reason=str(e) or type(e).__name__)
            return page(502, "Couldn't reach Claude Code. Paste the code in the terminal instead.\n")
        self.record(event="callback", status=status,
                    redirect=host_of(location) if safe_redirect(status, location) else None)
        return browser_response(status, location)

    async def forward(self, target: str) -> tuple[int, str | None]:
        """Pass the callback to Claude Code; its status and Location."""
        bridged = self.sessions / "svc.endpoint"
        if bridged.exists():
            r, w = await endpoint.open_connection(bridged, "svc", SERVICE, limit=16384)
        else:
            r, w = await endpoint.open_connection(self.sessions / f"svc-{SERVICE}.sock", limit=16384)
        try:
            w.write(f"GET {target} HTTP/1.1\r\nHost: localhost:{self.port}\r\nConnection: close\r\n\r\n".encode())
            await w.drain()
            head = await read_head(r, EXCHANGE_TIMEOUT)
        finally:
            w.close()
        lines = head.decode("latin-1").split("\r\n")
        status = lines[0].split(" ")
        if len(status) < 2 or not status[0].startswith("HTTP/1.") or not status[1].isdigit():
            raise ValueError("Claude Code's answer isn't HTTP")
        location = None
        for line in lines[1:]:
            k, _, v = line.partition(":")
            if k.strip().lower() == "location":
                location = v.strip()
        return int(status[1]), location


async def serve(socket: Path, host: Host):
    server, cleanup = await endpoint.start_server(host.request, socket, limit=MAX_URL + 2)
    log(listening=str(socket))
    try:
        async with server:
            await server.serve_forever()
    finally:
        cleanup()
        host.close()


# -- in the session --------------------------------------------------------------------------------

async def relay(sock: Path, port: int, lifetime=LIFETIME):
    """In the session: one connection from ``sock`` to Claude Code's
    ``127.0.0.1:port``, then stop; or stop after ``lifetime`` seconds."""
    done = asyncio.Event()

    async def forward(r, w):
        if done.is_set():
            w.close()
            return
        done.set()
        try:
            ur, uw = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            w.close()
            return
        await asyncio.gather(pipe(r, uw), pipe(ur, w))

    sock.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(forward, str(sock))
    os.chmod(sock, 0o660)
    try:
        await asyncio.wait_for(done.wait(), lifetime)
        await asyncio.sleep(EXCHANGE_TIMEOUT)     # let the one connection finish
    except asyncio.TimeoutError:
        pass
    finally:
        server.close()
        sock.unlink(missing_ok=True)


def browser(url: str) -> int:
    """``$BROWSER`` in the session. 0 if the user's browser opened."""
    try:
        port, _ = check_url(url)
    except ValueError:
        return 1                  # Claude Code shows the link anyway
    host = Path(os.environ.get("ADHOC_SIGNIN_SOCKET", "/run/adhoc/signin.sock"))
    sock = host.parent / f"svc-{SERVICE}.sock"     # where the host's side reaches the relay
    spawn([sys.executable, "-m", "adhocdocs.signin", "relay", str(sock), str(port)])
    end = time.time() + 5
    while not sock.exists() and time.time() < end:
        time.sleep(0.05)
    if not sock.exists():
        return 1                  # nothing to pass the callback to: keep the one opening for later
    try:
        s = endpoint.connect(host, 15)
        s.sendall(url.encode() + b"\n")
        answer = endpoint.read_line(s, 30).decode("utf-8", "replace").strip()
        s.close()
    except OSError:
        return 1
    return 0 if answer == "ok" else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="adhoc-signin", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="on the host: open the browser once, and pass its callback on")
    s.add_argument("--socket", required=True, help="where the session asks (a socket, or an endpoint file)")
    s.add_argument("--sessions", required=True, help="the session's socket directory")
    s.add_argument("--audit", help="record each step in this audit directory (<matter>/audit)")
    s.add_argument("--session", help="the session ID to record it under")
    s = sub.add_parser("browser", help="in the session: $BROWSER")
    s.add_argument("url")
    s = sub.add_parser("relay", help="in the session: one connection to Claude Code's sign-in port")
    s.add_argument("socket")
    s.add_argument("port", type=int)
    a = p.parse_args(argv)
    try:
        if a.cmd == "serve":
            audit = None
            if a.audit:
                from .audit import AuditLog
                audit = (AuditLog(a.audit), a.session)
            asyncio.run(serve(Path(a.socket), Host(Path(a.sessions), audit=audit)))
        elif a.cmd == "browser":
            sys.exit(browser(a.url))
        else:
            asyncio.run(relay(Path(a.socket), a.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
