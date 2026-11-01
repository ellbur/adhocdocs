"""Finding a matter's members on the local network (SYNC.md).

No public discovery servers: they would tell a third party which machines
share a matter. Instead, an installation that serves (``adhoc sync serve``)
broadcasts a small UDP packet on the local network every half minute, for
each matter it serves::

    {"adhoc": 1, "t": <tag>, "p": <its port>}

The tag is an HMAC of the current ten-minute window, keyed by the hash of
the matter's ID and founder (:func:`adhocdocs.peer.pool_key`): it says
neither which matter nor which installation, and changes every ten minutes.
Someone on the network who already knows a matter's ID and founder could
recognise it, which tells them no more than TLS's SNI already does when
members sync over that network.

A watcher (adhocdocs.watch) listens. When a tag is one of its matter's, it
connects to the sender, reads which origin its certificate is for, and if
that's an active member, syncs with it there. Addresses found this way are
kept in ``run/lan.json`` (this installation's only; never synced).
"""

import hashlib
import hmac
import json
import socket
import threading
import time

from . import peer, pool as poolmod
from .matter import Matter
from .origin import cert_origin

PORT = 7481
EVERY = 30
WINDOW = 600


def tag(m: Matter, port: int, at: float | None = None) -> str:
    """Over the window and the announced port: a packet heard can't be sent
    again pointing somewhere else."""
    key = hashlib.sha256(("adhoc-lan-v1:" + peer.pool_key(m)).encode()).digest()
    window = int((at if at is not None else time.time()) // WINDOW)
    return hmac.new(key, f"{window}:{port}".encode(), hashlib.sha256).hexdigest()[:32]


def tags(m: Matter, port: int) -> set[str]:
    """This window's tag and the last one's, so a packet sent just before the
    window turned still counts."""
    now = time.time()
    return {tag(m, port, now), tag(m, port, now - WINDOW)}


def packet(m: Matter, port: int) -> bytes:
    return json.dumps({"adhoc": 1, "t": tag(m, port), "p": port}, separators=(",", ":")).encode()


def _targets(target):
    return [target] if target else [("255.255.255.255", PORT)]


class Announcer:
    """Broadcast the matters a server serves."""

    def __init__(self, server: "peer.Server", target=None, every=EVERY):
        self.server, self.target, self.every = server, target, every
        self.stopping = threading.Event()

    def send_once(self):
        port = self.server.address[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for m in self.server.matters.values():
                for t in _targets(self.target):
                    try:
                        s.sendto(packet(m, port), t)
                    except OSError:
                        pass            # no network, or broadcast not allowed: nothing to do

    def run(self):
        while not self.stopping.is_set():
            self.send_once()
            self.stopping.wait(self.every)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def stop(self):
        self.stopping.set()


def probe(m: Matter, address: str, timeout=2) -> str:
    """Which origin answers for this matter at ``address`` (by its certificate)."""
    import ssl
    host, port = peer.split_address(address)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection((host, port), timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=peer.sni_name(peer.pool_key(m))) as tls:
            origin, _ = cert_origin(tls.getpeercert(binary_form=True))
    return origin


def found(m: Matter) -> dict[str, str]:
    """Members found on the local network: origin -> address."""
    try:
        return {o: x["address"] for o, x in json.loads((m.root / "run" / "lan.json").read_text(encoding="utf-8")).items()}
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return {}


def _remember(m: Matter, origin: str, address: str):
    p = m.root / "run" / "lan.json"
    p.parent.mkdir(exist_ok=True)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    data[origin] = {"address": address, "seen": int(time.time())}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(p)


class Listener:
    """Hear announcements; for one of this matter's members, call
    ``on_member(member, address)``."""

    PER_SOURCE = 6          # packets a minute from one address that get looked at
    AGAIN = 120             # seconds before the same address is probed again
    QUEUE = 8

    def __init__(self, m: Matter, on_member, port=PORT):
        self.root, self.on_member, self.port = m.root, on_member, port
        self.stopping = threading.Event()
        self.heard: dict[str, list[float]] = {}      # source ip -> recent packet times
        self.probed: dict[str, float] = {}           # address -> when last probed
        import queue
        self.todo = queue.Queue(self.QUEUE)          # probes wait here; full means dropped
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind(("", port))
        self.sock.settimeout(1.0)

    def handle(self, data: bytes, ip: str):
        """Cheap checks only: probing happens on the worker, a few at a time,
        so packets from anyone on the network can't make this open
        connections at their pace."""
        now = time.time()
        recent = [t for t in self.heard.get(ip, []) if now - t < 60]
        if len(recent) >= self.PER_SOURCE:
            return
        self.heard[ip] = recent + [now]
        if len(self.heard) > 1024:
            self.heard = {k: v for k, v in self.heard.items() if v and now - v[-1] < 60}
        try:
            msg = json.loads(data)
            t, port = str(msg["t"]), int(msg["p"])
            if not 0 < port < 65536:
                return
        except (ValueError, KeyError, TypeError):
            return
        m = Matter(self.root)
        try:
            if t not in tags(m, port):
                return
        except peer.SyncError:
            return              # no pool yet
        address = f"{ip}:{port}"
        if now - self.probed.get(address, 0) < self.AGAIN:
            return
        self.probed[address] = now
        try:
            self.todo.put_nowait(address)
        except Exception:
            pass                # busy: it'll be announced again

    def _probe(self, address: str):
        m = Matter(self.root)
        try:
            origin = probe(m, address)
        except Exception:
            return
        p = poolmod.Pool.load(m)
        if origin == m.origin or not p.is_member(origin):
            return
        known = found(m).get(origin)
        _remember(m, origin, address)
        if known != address:
            self.on_member(p.members[origin], address)

    def _work(self):
        import queue
        while not self.stopping.is_set():
            try:
                address = self.todo.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._probe(address)
            except Exception:
                pass

    def run(self):
        threading.Thread(target=self._work, daemon=True).start()
        while not self.stopping.is_set():
            try:
                data, (ip, _) = self.sock.recvfrom(2048)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            self.handle(data, ip)
        self.sock.close()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def stop(self):
        self.stopping.set()
