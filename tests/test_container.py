import asyncio

import pytest

from adhocdocs import container, proxy
from adhocdocs.matter import Matter


def test_allowlist():
    allow = ("api.anthropic.com", "*.example.com")
    assert proxy.allowed("api.anthropic.com", allow) and proxy.allowed("API.Anthropic.com.", allow)
    assert proxy.allowed("a.example.com", allow) and not proxy.allowed("example.com", allow)
    assert not proxy.allowed("api.anthropic.com.evil.net", allow) and not proxy.allowed("evilexample.com", allow)


async def _exchange(proxy_port, request: bytes, then: bytes = b"") -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", proxy_port)
    w.write(request)
    await w.drain()
    head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
    body = b""
    if then and b" 200 " in head:
        w.write(then)
        await w.drain()
        body = await asyncio.wait_for(r.read(100), 5)
    w.close()
    return head + body


def test_proxy_allows_only_listed_hosts(monkeypatch):
    async def run():
        async def echo(r, w):
            w.write(b"echo:" + await r.read(100))
            await w.drain()
            w.close()
        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        monkeypatch.setattr(proxy, "PORT", upstream.sockets[0].getsockname()[1])
        server = await asyncio.start_server(lambda r, w: proxy.handle(r, w, ("localhost",)), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        up = proxy.PORT
        ok = await _exchange(port, f"CONNECT localhost:{up} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode(), b"hello")
        no_host = await _exchange(port, f"CONNECT 127.0.0.1:{up} HTTP/1.1\r\n\r\n".encode())
        no_port = await _exchange(port, b"CONNECT localhost:22 HTTP/1.1\r\n\r\n")
        plain = await _exchange(port, b"GET http://localhost/ HTTP/1.1\r\n\r\n")
        server.close(); upstream.close()
        return ok, no_host, no_port, plain
    ok, no_host, no_port, plain = asyncio.run(run())
    assert b" 200 " in ok and ok.endswith(b"echo:hello")       # bytes pass through untouched
    assert b" 403 " in no_host and b" 403 " in no_port and b" 405 " in plain


def test_session_args(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    m = Matter.create(tmp_path / "Smith v Jones")
    args = container.session_args(m, "s1", tmp_path / "run")
    joined = " ".join(args)
    assert f"{m.root}/store:/matter/store:ro" in joined and f"{m.root}/annotations:/matter/annotations:ro" in joined
    assert f"{m.root}/workspace:/matter/workspace " in joined and f"{m.root}/claude-home:/home/adhoc/.claude" in joined
    assert "--network none" in joined and "--cap-drop=ALL" in joined
    assert "HTTPS_PROXY=http://127.0.0.1:3128" in joined
    assert "podman.sock" not in joined and str(tmp_path.parent) + ":" not in joined
    # the API key is passed by name: its value never appears in the arguments
    assert "-e ANTHROPIC_API_KEY" in joined and "sk-secret" not in joined
    assert args[-1] == container.IMAGE
    assert container.slug(m).startswith("adhoc-smith-v-jones-")


def test_relay_over_unix_socket(tmp_path):
    async def run():
        async def echo(r, w):
            w.write(b"via-socket:" + await r.read(100))
            await w.drain()
            w.close()
        sock = str(tmp_path / "p.sock")
        upstream = await asyncio.start_unix_server(echo, sock)
        relay = asyncio.create_task(proxy.relay("127.0.0.1:38912", sock))
        await asyncio.sleep(0.2)
        r, w = await asyncio.open_connection("127.0.0.1", 38912)
        w.write(b"hi")
        await w.drain()
        out = await asyncio.wait_for(r.read(100), 5)
        relay.cancel(); upstream.close()
        return out
    assert asyncio.run(run()) == b"via-socket:hi"


def test_session_guide_names_real_tools():
    """The guide Claude reads in the session must only name installed commands."""
    import re
    import tomllib
    from pathlib import Path
    root = Path(__file__).parent.parent
    scripts = set(tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"])
    guide = (root / "container" / "CLAUDE.md").read_text(encoding="utf-8")
    named = set(re.findall(r"^\| `(\w+)`", guide, re.M))
    assert named and named <= scripts, named - scripts
    assert len(guide.splitlines()) < 65          # it's loaded into every session: keep it short


def test_bridged_session_args(tmp_path):
    """Windows and macOS: the session mounts the bridge's volumes, not host
    sockets, and still has no network."""
    m = Matter.create(tmp_path / "m")
    args = container.session_args(m, "s1", tmp_path / "run", volumes=("s1-run", "s1-audit"))
    joined = " ".join(args)
    assert f"type=bind,source={m.root / 'store'},target=/matter/store,ro" in joined
    assert f"type=bind,source={m.root / 'workspace'},target=/matter/workspace " in joined
    assert "-v s1-run:/run/adhoc " in joined and "-v s1-audit:/run/adhoc-audit:ro" in joined
    assert str(tmp_path / "run") not in joined and "--network none" in joined


def test_bridge_args(monkeypatch):
    monkeypatch.setenv("ADHOC_BRIDGE_TOKEN", "secret-token")
    args = container.bridge_args("s1", ("s1-run", "s1-audit"))
    joined = " ".join(args)
    assert "-p 127.0.0.1::7100" in joined                       # the host's loopback only
    assert "-v s1-audit:/run/adhoc-audit " in joined            # read-write here: the bridge makes the socket
    assert "-e ADHOC_BRIDGE_TOKEN " in joined and "secret-token" not in joined
    assert "--cap-drop=ALL" in joined and "--network" not in joined
    for name in ("proxy", "annotations", "viewer", "audit"):
        assert f"--socket {name}=" in joined
    assert args[args.index("--entrypoint") + 2] == container.IMAGE
