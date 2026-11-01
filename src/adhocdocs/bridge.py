"""The bridge: how a session reaches the host where Unix sockets can't.

On Linux the session mounts the host's sockets directly. On Windows and
macOS, Podman runs containers in a VM, and a host socket doesn't reach into
it. There, two more pieces stand between the session and the host's
services, and the session itself doesn't change: it still finds
``/run/adhoc/proxy.sock`` and the rest.

- **The bridge**, a container from the same image, in the VM. It shares
  the session's socket volumes (``/run/adhoc``, and ``/run/adhoc-audit``,
  which the session mounts read-only) and listens on the sockets there. It
  has a network, but the session doesn't share it: the session still runs
  with ``--network none``. The bridge publishes one port, on the host's
  127.0.0.1 only.
- **The link**, a process on the host. It dials into the bridge's port and
  holds a control connection open. When something in the session connects
  to a socket, the bridge asks the link for a connection, and the link
  dials in again and joins that connection to the host service's own
  endpoint (:mod:`adhocdocs.endpoint`).

The host always dials in and the VM never dials out, so this needs only
port publishing, the best-supported way into Podman's VM. Every connection
to the port starts with ``ADHOC1 <token> <verb>``:

=====================  ==================================================
``control``            the link's control connection. The bridge answers
                       ``ok``, then sends ``open <id> <service>`` lines.
``data <id>``          the link's answer to one ``open``.
``svc <name>``         the viewer, reaching ``svc-<name>.sock``, a service
                       Claude runs in the session (``view serve``).
=====================  ==================================================

The token is the session's, made by the launcher and passed in
``ADHOC_BRIDGE_TOKEN``, so other programs on the host can reach neither the
session's services nor the host's through the port.

    python -m adhocdocs.bridge serve --port 7100 --socket proxy=/run/adhoc/proxy.sock …   (in the VM)
    python -m adhocdocs.bridge link 127.0.0.1:PORT --service proxy=<run>/proxy.sock … [--ready FILE]   (host)
"""

import argparse
import asyncio
import itertools
import json
import os
import re
import sys
from pathlib import Path

from . import endpoint
from .proxy import pipe

SERVICE_NAME = re.compile(r"[a-z0-9-]+")
OPEN_TIMEOUT = 15


def log(**kw):
    print(json.dumps(kw), file=sys.stderr, flush=True)


def _pair(specs) -> dict[str, Path]:
    out = {}
    for s in specs or ():
        name, _, path = s.partition("=")
        if not SERVICE_NAME.fullmatch(name) or not path:
            raise SystemExit(f"bad service {s!r}: want name=path")
        out[name] = Path(path)
    return out


async def _join(r1, w1, r2, w2):
    await asyncio.gather(pipe(r1, w2), pipe(r2, w1))


# -- in the VM -------------------------------------------------------------------------------------

class Bridge:
    def __init__(self, token: str, sockets: dict[str, Path], svc_dir: Path):
        self.token, self.sockets, self.svc_dir = token, sockets, svc_dir
        self.control = None
        self.pending: dict[str, asyncio.Future] = {}
        self.ids = itertools.count(1)

    async def from_session(self, name, reader, writer):
        """Something in the session connected to ``name``'s socket."""
        if self.control is None:
            log(refused=name, reason="no link")
            writer.close()
            return
        cid = str(next(self.ids))
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = fut
        try:
            self.control.write(f"open {cid} {name}\n".encode())
            await self.control.drain()
            dr, dw, done = await asyncio.wait_for(fut, OPEN_TIMEOUT)
        except (asyncio.TimeoutError, ConnectionError, AttributeError):
            writer.close()
            return
        finally:
            self.pending.pop(cid, None)
        try:
            await _join(reader, writer, dr, dw)
        finally:
            done.set()

    async def from_host(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
        except (asyncio.TimeoutError, ConnectionError, ValueError, asyncio.LimitOverrunError):
            line = b""
        words = endpoint.parse_preamble(line, self.token)
        if not words:
            writer.close()
            return
        verb, args = words[0], words[1:]
        if verb == "control" and not args:
            await self._control(reader, writer)
        elif verb == "data" and len(args) == 1:
            fut = self.pending.get(args[0])
            if fut is None or fut.done():
                writer.close()
                return
            done = asyncio.Event()
            fut.set_result((reader, writer, done))
            await done.wait()               # keep this connection's handler alive while it's in use
        elif verb == "svc" and len(args) == 1 and SERVICE_NAME.fullmatch(args[0]):
            try:
                sr, sw = await asyncio.open_unix_connection(str(self.svc_dir / f"svc-{args[0]}.sock"))
            except OSError:
                writer.close()
                return
            await _join(reader, writer, sr, sw)
        else:
            writer.close()

    async def _control(self, reader, writer):
        if self.control is not None:
            self.control.close()            # a new link replaces the old one
        self.control = writer
        writer.write(b"ok\n")
        await writer.drain()
        log(link="connected")
        try:
            while await reader.readline():
                pass
        except ConnectionError:
            pass
        finally:
            if self.control is writer:
                self.control = None
                log(link="gone")
            writer.close()

    async def serve(self, port: int):
        servers = []
        for name, path in self.sockets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink(missing_ok=True)
            servers.append(await asyncio.start_unix_server(
                lambda r, w, n=name: self.from_session(n, r, w), str(path)))
            os.chmod(path, 0o660)
        # Sockets first: once the link hears "ok", the session can start.
        servers.append(await asyncio.start_server(self.from_host, "0.0.0.0", port))
        log(bridge="listening", port=port, sockets={n: str(p) for n, p in self.sockets.items()})
        await asyncio.gather(*(s.serve_forever() for s in servers))


# -- on the host -----------------------------------------------------------------------------------

class Link:
    def __init__(self, address: str, token: str, services: dict[str, Path], ready: Path | None = None):
        host, _, port = address.rpartition(":")
        self.host, self.port, self.token, self.services, self.ready = host, int(port), token, services, ready

    async def _dial(self, *words):
        r, w = await asyncio.open_connection(self.host, self.port)
        w.write(endpoint.preamble(self.token, *words))
        await w.drain()
        return r, w

    async def _open(self, cid, name):
        local = None
        if name in self.services:
            try:
                local = await endpoint.open_connection(self.services[name])
            except (OSError, ValueError) as e:
                log(open=name, error=str(e))
        try:
            dr, dw = await self._dial("data", cid)
        except OSError:
            if local:
                local[1].close()
            return
        if local is None:
            dw.close()                      # the session's client sees the connection close
            return
        await _join(dr, dw, *local)

    async def run_once(self) -> bool:
        """One control connection, until it drops. False if it never got going."""
        try:
            r, w = await self._dial("control")
            ok = await asyncio.wait_for(r.readline(), 5)
        except (OSError, asyncio.TimeoutError):
            return False
        if ok != b"ok\n":
            w.close()
            return False
        log(link="connected", port=self.port)
        if self.ready:
            self.ready.parent.mkdir(parents=True, exist_ok=True)
            self.ready.write_text("ok\n", encoding="utf-8", newline="\n")
        tasks = set()
        try:
            while line := await r.readline():
                parts = line.decode("ascii", "replace").split()
                if len(parts) == 3 and parts[0] == "open":
                    t = asyncio.create_task(self._open(parts[1], parts[2]))
                    tasks.add(t)
                    t.add_done_callback(tasks.discard)
        except ConnectionError:
            pass
        log(link="lost")
        return True

    async def run(self):
        delay = 0.1
        while True:
            if await self.run_once():
                delay = 0.1
            await asyncio.sleep(delay)
            delay = min(delay * 2, 2.0)


def main(argv=None):
    p = argparse.ArgumentParser(prog="adhoc-bridge", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="in the VM: the sockets the session uses, and the port the link dials")
    s.add_argument("--port", type=int, default=7100)
    s.add_argument("--socket", action="append", help="name=path of a socket to listen on for the session")
    s.add_argument("--svc-dir", default="/run/adhoc", help="where the session's own services listen")
    s = sub.add_parser("link", help="on the host: dial the bridge and join its connections to the host's services")
    s.add_argument("address", help="the bridge's published port, host:port")
    s.add_argument("--service", action="append", help="name=path of a host service (socket or endpoint file)")
    s.add_argument("--ready", help="write this file once the bridge answers")
    a = p.parse_args(argv)
    token = os.environ.get("ADHOC_BRIDGE_TOKEN") or sys.exit("ADHOC_BRIDGE_TOKEN is not set")
    try:
        if a.cmd == "serve":
            asyncio.run(Bridge(token, _pair(a.socket), Path(a.svc_dir)).serve(a.port))
        else:
            asyncio.run(Link(a.address, token, _pair(a.service), Path(a.ready) if a.ready else None).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
