"""Signing in with the browser across the container boundary (adhocdocs.signin)."""

import asyncio
import json
import os
import socket
import sys

import pytest

from adhocdocs import signin

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="the session's side runs on Linux")

STATE = "n6l38PMn_Kw5lKBGPbWxQynmREsNZCx5EdxCd8Xpw8k"


def url(port=35139, host="claude.com", path="/cai/oauth/authorize", redirect=None, extra=""):
    # the shape Claude Code 2.1 opens, with its redirect to localhost
    redirect = redirect or f"http%3A%2F%2Flocalhost%3A{port}%2Fcallback"
    return (f"https://{host}{path}?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code"
            f"&redirect_uri={redirect}&scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference"
            f"&code_challenge=M8GEfdut0RCVDUuvZT-bOJNmn-uaE9HG5TnBe6etYOk&code_challenge_method=S256"
            f"&state={STATE}{extra}")


def test_check_url_accepts_claude_codes_sign_in():
    assert signin.check_url(url()) == (35139, STATE)
    assert signin.check_url(url(host="platform.claude.com", path="/oauth/authorize"))[0] == 35139
    assert signin.check_url(url(host="claude.ai", path="/oauth/authorize"))[0] == 35139


@pytest.mark.parametrize("bad", [
    url().replace("https://", "http://"),
    url(host="claude.com.evil.net"),
    url(host="evil.net"),
    url(host="claude.com:8443"),
    url(host="me@claude.com"),
    url(host="Claude.com"),
    url(path="/somewhere/else"),
    url() + "#x",
    url(redirect="https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"),   # the paste flow: nothing to relay
    url(redirect="http%3A%2F%2Fevil.net%3A35139%2Fcallback"),
    url(redirect="http%3A%2F%2Flocalhost%3A22%2Fcallback"),
    url(redirect="http%3A%2F%2Flocalhost%3A35139%2Fcallback%2Fx"),
    url(extra="&redirect_uri=http%3A%2F%2Flocalhost%3A4000%2Fcallback"),           # two of them
    url().replace("response_type=code", "response_type=token"),
    url().replace(STATE, "x"),
    url() + " ",
    url() + "\n",
    url() + "&x=" + "a" * 5000,
])
def test_check_url_refuses_anything_else(bad):
    with pytest.raises(ValueError):
        signin.check_url(bad)


def test_browser_response_passes_only_safe_redirects():
    ok = signin.browser_response(302, "https://platform.claude.com/oauth/code/success?app=claude-code")
    assert ok.startswith(b"HTTP/1.1 302 ") and b"Location: https://platform.claude.com/oauth/code/success" in ok
    for status, location in ((302, "https://evil.net/?data=secret"), (302, "http://platform.claude.com/"),
                             (302, "https://platform.claude.com.evil.net/"), (302, "javascript:alert(1)"),
                             (302, "https://platform.claude.com/\r\nSet-Cookie: x=1"), (200, None), (500, None)):
        out = signin.browser_response(status, location)
        assert out.startswith(b"HTTP/1.1 200 ") and signin.DONE_PAGE.encode() in out
        assert b"evil" not in out and b"Set-Cookie" not in out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def browser_get(port, target, headers=""):
    """What a browser on the host gets for ``GET target``."""
    r, w = await asyncio.open_connection("127.0.0.2", port)
    w.write(f"GET {target} HTTP/1.1\r\nHost: localhost:{port}\r\n{headers}\r\n".encode())
    await w.drain()
    out = await asyncio.wait_for(r.read(), 10)
    w.close()
    return out


def run_sign_in(tmp_path, answer: bytes):
    """Claude Code's listener (fake) in the session, the session's relay, the
    host: open once, and the browser's callback. In this test the session's
    loopback is the host's, so the host listens on 127.0.0.2 instead."""
    port = free_port()
    seen = []
    opened = []

    async def run():
        async def claude_code(r, w):
            seen.append(await r.readuntil(b"\r\n\r\n"))
            w.write(answer + b"<script>steal()</script>")
            await w.drain()
            w.close()
        listener = await asyncio.start_server(claude_code, "127.0.0.1", port)
        relay = asyncio.create_task(signin.relay(tmp_path / "svc-signin.sock", port, lifetime=30))
        host = signin.Host(tmp_path, opener=opened.append, bind="127.0.0.2")
        sock = tmp_path / "signin.sock"
        server, cleanup = await signin.endpoint.start_server(host.request, sock)
        for _ in range(50):
            if (tmp_path / "svc-signin.sock").exists():
                break
            await asyncio.sleep(0.05)

        async def ask(u):
            r, w = await asyncio.open_unix_connection(str(sock))
            w.write(u.encode() + b"\n")
            await w.drain()
            out = (await r.readline()).decode().strip()
            w.close()
            return out

        results = {
            "elsewhere": await ask(url(port, host="evil.net")),
            "first": await ask(url(port)),
            "again": await ask(url(port)),
            "favicon": await browser_get(port, "/favicon.ico"),
            "wrong_state": await browser_get(port, "/callback?code=abc&state=wrong-state-value"),
            "callback": await browser_get(port, f"/callback?code=the-code&state={STATE}",
                                          "Cookie: other-app=secret\r\nUser-Agent: Browser\r\n"),
        }
        try:
            results["after"] = await browser_get(port, f"/callback?code=again&state={STATE}")
        except OSError:
            results["after"] = b"refused"
        relay.cancel()
        listener.close()
        server.close()
        cleanup()
        return results

    return asyncio.run(run()), seen, opened


def test_sign_in_once_through_the_host(tmp_path):
    success = b"HTTP/1.1 302 Found\r\nLocation: https://platform.claude.com/oauth/code/success?app=claude-code\r\n\r\n"
    out, seen, opened = run_sign_in(tmp_path, success)
    assert out["elsewhere"].startswith("refused: not Anthropic") and out["first"] == "ok"
    assert out["again"].startswith("refused: the browser can be opened once")
    assert len(opened) == 1 and opened[0].startswith("https://claude.com/")
    # only the callback with this sign-in's state reaches Claude Code: a stray one would end its sign-in
    assert b" 404 " in out["favicon"] and b" 400 " in out["wrong_state"]
    assert len(seen) == 1
    req = seen[0].decode()
    assert req.startswith(f"GET /callback?code=the-code&state={STATE} HTTP/1.1\r\n")
    assert "Cookie" not in req and "User-Agent" not in req            # the browser's headers stay on the host
    # the browser follows Claude Code's redirect, and gets nothing else from the session
    assert out["callback"].startswith(b"HTTP/1.1 302 ")
    assert b"Location: https://platform.claude.com/oauth/code/success?app=claude-code" in out["callback"]
    assert b"steal" not in out["callback"]
    assert out["after"] == b"refused"                                  # the port closed after one callback


def test_session_cant_send_the_browser_elsewhere(tmp_path):
    out, seen, opened = run_sign_in(tmp_path, b"HTTP/1.1 302 Found\r\nLocation: https://evil.net/?docs=secret\r\n\r\n")
    assert len(seen) == 1
    assert out["callback"].startswith(b"HTTP/1.1 200 ") and signin.DONE_PAGE.encode() in out["callback"]
    assert b"evil" not in out["callback"] and b"steal" not in out["callback"]


def test_steps_go_in_the_audit_log_without_the_code(tmp_path):
    from adhocdocs.audit import AuditLog
    log = AuditLog(tmp_path / "audit")
    port = free_port()

    async def run():
        host = signin.Host(tmp_path, opener=lambda u: None, audit=(log, "ses_1"), bind="127.0.0.2")
        assert await host.open(url(port)) == "ok"
        await browser_get(port, f"/callback?code=the-code&state={STATE}")       # nothing in the session to answer
        host.close()
    asyncio.run(run())
    text = log.path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in text.splitlines()]
    assert [e["event"] for e in events] == ["browser-opened", "callback-failed"]
    assert all(e["source"] == "signin" and e["session"] == "ses_1" for e in events)
    assert "the-code" not in text and STATE not in text


def test_browser_command_refuses_without_asking(tmp_path, monkeypatch):
    # anything but Claude Code's sign-in page: no relay started, nothing sent to the host
    monkeypatch.setenv("ADHOC_SIGNIN_SOCKET", str(tmp_path / "signin.sock"))
    assert signin.browser("https://evil.net/") == 1
    assert not os.listdir(tmp_path)
