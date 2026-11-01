"""The session's only way out: an HTTPS proxy that allows listed host names.

The session container has no network at all (``--network none``). This
proxy runs on the host, listening on a Unix socket mounted into the
session, and a relay inside the session forwards ``127.0.0.1:3128`` to it
for Claude Code's ``HTTPS_PROXY``. Clients ask for ``CONNECT host:443``; if
the host is on the allowlist, the proxy opens the connection and passes the
encrypted bytes through untouched, never decrypting or reading them.
Everything else is refused, and every decision is logged as a line of JSON
on stderr and, with ``--audit``, in the matter's audit log. Killing the
relay only cuts the session off.

    python -m adhocdocs.proxy --unix /run/…/proxy.sock [--allow host …] [--audit DIR --session ID]   (host side)
    python -m adhocdocs.proxy relay 127.0.0.1:3128 /run/adhoc/proxy.sock     (in the session)
"""

import argparse
import asyncio
import json
import sys
import time

# What Claude Code needs to sign in, talk to the API and update itself. See
# https://code.claude.com/docs/en/network-config#network-access-requirements
CLAUDE_CODE_HOSTS = (
    "api.anthropic.com",
    "claude.ai",
    "claude.com",
    "platform.claude.com",
    "sso.claude.com",
    "downloads.claude.ai",
)

MAX_HEADER = 16384
PORT = 443          # the only port clients may ask for (a variable so tests can use another)


def allowed(host: str, allow) -> bool:
    host = host.lower().rstrip(".")
    for a in allow:
        a = a.lower()
        if a.startswith("*.") and host.endswith(a[1:]) and host != a[2:]:
            return True
        if host == a:
            return True
    return False


AUDIT = None       # (AuditLog, session) when decisions also go to the audit log


def log(**kw):
    print(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}), file=sys.stderr, flush=True)
    if AUDIT and "decision" in kw:
        kw.pop("peer", None)
        AUDIT[0].append(session=AUDIT[1], source="proxy", kind="connection", **kw)


async def pipe(reader, writer) -> int:
    """Copy until either side closes; returns the bytes copied."""
    n = 0
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
            n += len(data)
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass
    return n


async def handle(reader, writer, allow):
    peer = writer.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
        writer.close()
        return
    line = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split()
    if len(parts) != 3 or parts[0] != "CONNECT":
        log(decision="refused", reason="only CONNECT is allowed", request=line[:200], peer=str(peer))
        writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    host, _, port = parts[1].rpartition(":")
    host = host.strip("[]")
    if port != str(PORT) or not allowed(host, allow):
        log(decision="refused", host=host, port=port, peer=str(peer))
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    try:
        up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, PORT), 30)
    except (OSError, asyncio.TimeoutError) as e:
        log(decision="failed", host=host, error=str(e))
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    log(decision="allowed", host=host, peer=str(peer))
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    start = time.monotonic()
    sent, received = await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))
    log(decision="closed", host=host, bytes_sent=sent, bytes_received=received,
        seconds=round(time.monotonic() - start, 1))


async def serve(listen: str | None, unix: str | None, allow):
    handler = lambda r, w: handle(r, w, allow)
    cleanup = None
    if unix:
        # a Unix socket, or an endpoint file on Windows and macOS (adhocdocs.endpoint)
        from .endpoint import start_server
        server, cleanup = await start_server(handler, unix, limit=MAX_HEADER)
    else:
        host, _, port = listen.rpartition(":")
        server = await asyncio.start_server(handler, host or "127.0.0.1", int(port), limit=MAX_HEADER)
    log(listening=unix or listen, allow=list(allow))
    try:
        async with server:
            await server.serve_forever()
    finally:
        if cleanup:
            cleanup()


async def relay(listen: str, target: str):
    """Forward TCP connections on ``listen`` to the Unix socket ``target``, byte for byte."""
    async def forward(r, w):
        try:
            ur, uw = await asyncio.open_unix_connection(target)
        except OSError:
            w.close()
            return
        await asyncio.gather(pipe(r, uw), pipe(ur, w))
    host, _, port = listen.rpartition(":")
    server = await asyncio.start_server(forward, host, int(port))
    async with server:
        await server.serve_forever()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["relay"]:
        try:
            asyncio.run(relay(argv[1], argv[2]))
        except KeyboardInterrupt:
            pass
        return
    p = argparse.ArgumentParser(prog="adhoc-proxy", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--unix", help="listen on this Unix socket")
    g.add_argument("--listen", help="listen on host:port")
    p.add_argument("--allow", action="append", help="a host name, or *.domain; default: Claude Code's hosts")
    p.add_argument("--audit", help="also record decisions in this audit directory (<matter>/audit)")
    p.add_argument("--session", help="the session ID to record them under")
    a = p.parse_args(argv)
    if a.audit:
        from .audit import AuditLog
        global AUDIT
        AUDIT = (AuditLog(a.audit), a.session)
    try:
        asyncio.run(serve(a.listen, a.unix, tuple(a.allow or CLAUDE_CODE_HOSTS)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
