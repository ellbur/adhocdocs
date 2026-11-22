"""Endpoints (Unix socket or TCP with a token) and the bridge, run here as
plain processes: on Windows and macOS the bridge runs in Podman's VM."""

import asyncio
import http.client
import socket
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from adhocdocs import bridge, endpoint


@pytest.fixture(params=["unix", "tcp"])
def transport(request, monkeypatch):
    monkeypatch.setenv("ADHOC_TRANSPORT", request.param)
    return request.param


class Hello(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        data = b"hello"
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _get(path):
    conn = http.client.HTTPConnection("localhost", timeout=5)
    conn.sock = endpoint.connect(path, 5)
    conn.request("GET", "/")
    return conn.getresponse().read()


def test_http_server_both_ways(tmp_path, transport):
    path = tmp_path / "svc.sock"
    srv = endpoint.http_server(path, Hello, "test")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert _get(path) == b"hello"
        ep = endpoint.read_endpoint(path)
        assert (ep is not None) == (transport == "tcp")
        if ep:
            assert ep["tcp"].startswith("127.0.0.1:") and len(ep["token"]) == 64
            assert path.stat().st_mode & 0o077 == 0              # only the user can read the token
            host, port = ep["tcp"].split(":")
            # Without the token, the connection is dropped unanswered.
            with socket.create_connection((host, int(port)), timeout=5) as s:
                s.sendall(endpoint.preamble("0" * 64) + b"GET / HTTP/1.0\r\n\r\n")
                assert s.recv(100) == b""
            with socket.create_connection((host, int(port)), timeout=5) as s:
                s.sendall(b"GET / HTTP/1.0\r\n\r\n")
                assert s.recv(100) == b""
    finally:
        srv.shutdown()
        srv.__exit__(None, None, None)
    assert not path.exists()                                     # cleaned up


def test_connect_to_nothing(tmp_path, transport):
    with pytest.raises(FileNotFoundError):
        endpoint.connect(tmp_path / "missing.sock")


def test_preamble():
    t = endpoint.new_token()
    assert endpoint.parse_preamble(endpoint.preamble(t), t) == []
    assert endpoint.parse_preamble(endpoint.preamble(t, "svc", "x"), t) == ["svc", "x"]
    assert endpoint.parse_preamble(endpoint.preamble("f" * 64), t) is None
    assert endpoint.parse_preamble(b"GET / HTTP/1.1\r\n", t) is None


async def _echo(prefix: bytes):
    async def handle(r, w):
        data = await r.read(100)
        w.write(prefix + data)
        await w.drain()
        w.close()
    return handle


def test_bridge_and_link(tmp_path, monkeypatch):
    """The session's socket → bridge → link → host service, and the viewer →
    bridge → a service in the session."""
    monkeypatch.setenv("ADHOC_TRANSPORT", "tcp")               # the host side, as on Windows
    token = endpoint.new_token()
    host_dir, vm_dir = tmp_path / "host", tmp_path / "vm"
    vm_dir.mkdir()

    async def run():
        host_srv, cleanup = await endpoint.start_server(await _echo(b"host:"), host_dir / "annotations.sock")
        svc = await asyncio.start_unix_server(await _echo(b"svc:"), str(vm_dir / "svc-form.sock"))
        b = bridge.Bridge(token, {"annotations": vm_dir / "annotations.sock",
                                  "viewer": vm_dir / "viewer.sock"}, vm_dir)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        bt = asyncio.create_task(b.serve(port))
        ready = tmp_path / "ready"
        link = bridge.Link(f"127.0.0.1:{port}", token, {"annotations": host_dir / "annotations.sock",
                                                        "viewer": host_dir / "viewer.sock"}, ready)
        lt = asyncio.create_task(link.run())
        for _ in range(100):
            if ready.exists():
                break
            await asyncio.sleep(0.05)
        assert ready.exists()

        async def session_client(name):
            r, w = await asyncio.open_unix_connection(str(vm_dir / f"{name}.sock"))
            w.write(b"ping")
            await w.drain()
            return await asyncio.wait_for(r.read(100), 5)

        async def from_host(*words, tok=token):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(endpoint.preamble(tok, *words) + b"ping")
            await w.drain()
            return await asyncio.wait_for(r.read(100), 5)

        got = {
            "to_host": await session_client("annotations"),
            "twice": await session_client("annotations"),
            "host_service_down": await session_client("viewer"),     # no viewer on the host
            "svc": await from_host("svc", "form"),
            "no_svc": await from_host("svc", "other"),
            "bad_name": await from_host("svc", "../x"),
            "bad_token": await from_host("svc", "form", tok="0" * 64),
            "stray_data": await from_host("data", "99"),
        }
        for t in (bt, lt):
            t.cancel()
        host_srv.close()
        svc.close()
        cleanup()
        return got

    got = asyncio.run(run())
    assert got["to_host"] == got["twice"] == b"host:ping"
    assert got["svc"] == b"svc:ping"
    for k in ("host_service_down", "no_svc", "bad_name", "bad_token", "stray_data"):
        assert got[k] == b"", k


def test_proxy_on_an_endpoint(tmp_path, monkeypatch):
    """The allowlist proxy listens on an endpoint file on Windows."""
    from adhocdocs import proxy
    monkeypatch.setenv("ADHOC_TRANSPORT", "tcp")
    path = tmp_path / "proxy.sock"

    async def run():
        task = asyncio.create_task(proxy.serve(None, str(path), ("allowed.example",)))
        for _ in range(100):
            if path.exists():
                break
            await asyncio.sleep(0.02)
        r, w = await endpoint.open_connection(path)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        reply = await asyncio.wait_for(r.read(100), 5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return reply

    assert b" 403 " in asyncio.run(run())
    assert not path.exists()
