"""Syncing as soon as something changes (SYNC.md).

A watcher runs beside a matter on the host (``adhoc sync watch``; ``adhoc
sync serve`` runs one too, and ``adhoc open`` starts one when the matter
has a pool). It keeps the installation level with the pool's members:

- **When this installation's logs grow** (a tag, a note, a publication, an
  ingest, or events another member just sent), it syncs with every member
  that says where it listens, a moment later so a burst goes as one sync.
  Passing on what it received is what lets changes travel between members
  that can't reach each other.
- **When a member's logs grow**, it hears at once: it keeps a long poll open
  to each member it can reach (``GET wait``), which answers when that
  member's logs change. A laptop that can't be reached itself learns of
  changes this way, usually from the hub.
- **When a member is reached again** (it was off, or this machine slept),
  it syncs with it at once: either side may have changed meanwhile.
- Every few minutes it syncs with everyone anyway, in case anything was
  missed.

Each member is synced on a thread of its own, one sync at a time: a member
that's slow or out of reach holds up no one else (and is given up on after
``peer.CONNECT_TIMEOUT``), and changes made while a sync runs go together
in the next. A sync that fails is tried again, after 5 seconds and then
less often. Nothing here holds up work on the matter: writers append to
their logs, and the watcher notices. So a change normally reaches every
member that can be reached within a few seconds.

Only one watcher runs per matter (``run/sync.lock``); a second exits at
once. What it does goes to ``run/sync.log``.
"""

import hashlib
import json
import sys
import threading
import time

from . import peer, pool as poolmod
from .matter import Matter
from .portable import locked

FEED_DIRS = ("pool/logs", "store/logs", "annotations/logs", "work/logs")


def fingerprint(m: Matter) -> str:
    """Changes whenever any synced log grows (by the logs' sizes: cheap)."""
    h = hashlib.sha256()
    for d in FEED_DIRS:
        p = m.root / d
        if p.is_dir():
            for f in sorted(p.glob("*.jsonl")):
                try:
                    h.update(f"{d}/{f.name}:{f.stat().st_size};".encode())
                except FileNotFoundError:
                    pass
    return h.hexdigest()[:32]


def wait_for_change(m: Matter, seen: str | None, timeout: float) -> str:
    """The fingerprint, once it differs from ``seen`` or ``timeout`` passes."""
    end = time.time() + timeout
    while True:
        fp = fingerprint(m)
        if fp != seen or time.time() >= end:
            return fp
        time.sleep(0.25)


class _Link:
    """Syncs with one member, one sync at a time, on a thread of its own, so a
    member that's slow or gone delays no one else. Asking while a sync runs
    folds into the next one: a burst of changes goes as one sync. A sync
    that fails is tried again, sooner at first, until it goes through."""

    RETRY_FIRST, RETRY_MOST = 5, 120        # seconds

    def __init__(self, w: "Watcher", origin: str):
        self.w, self.origin = w, origin
        self.wanted = threading.Event()
        self.whys: list[str] = []
        self.lock = threading.Lock()
        self.failures = 0
        self.address: str | None = None       # try here first (just found on the local network)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def request(self, why: str, address: str | None = None):
        with self.lock:
            if why not in self.whys:
                self.whys.append(why)
            if address:
                self.address = address
        self.wanted.set()

    def alive(self) -> bool:
        return self.thread.is_alive()

    def run(self):
        while not self.w.stopping.is_set():
            if not self.wanted.wait(timeout=0.5):
                continue
            self.wanted.clear()
            with self.lock:
                whys, self.whys = self.whys, []
                first, self.address = self.address, None
            x = self.w.member(self.origin)
            if x is None:
                return                  # removed, or nowhere to reach it: nothing for this link to do
            if first:
                x = {**x, "addresses": list(dict.fromkeys([first, *x["addresses"]]))}
            if self.w.sync_member(x, "; ".join(whys)):
                self.failures = 0
                continue
            self.failures += 1
            with self.lock:
                self.whys = list(dict.fromkeys(whys + self.whys))
            self.wanted.set()           # what was to go still has to
            self.w.stopping.wait(min(self.RETRY_MOST, self.RETRY_FIRST * 2 ** (self.failures - 1)))


class Watcher:
    def __init__(self, m: Matter, every: float = 300, debounce: float = 1.0, poll: float = 1.0, wait: float = 50,
                 log=None, lan_port: int | None = None):
        self.root = m.root
        self.every, self.debounce, self.poll, self.wait = every, debounce, poll, wait
        self.stopping = threading.Event()
        self.listeners: dict[str, threading.Thread] = {}
        self.links: dict[str, _Link] = {}
        self.links_lock = threading.Lock()
        self.log = log or (lambda row: print(json.dumps(row), file=sys.stderr, flush=True))
        self.synced = 0
        self.lan_port = lan_port          # listen for members on the local network (adhocdocs.lan); None: don't
        self.lan = None

    def m(self) -> Matter:
        return Matter(self.root)

    def member(self, origin: str) -> dict | None:
        """An active member other than this one, with every address it might
        be reached at (the pool's, and where it was found nearby); None if it
        isn't one or can't be reached."""
        from . import lan
        m = self.m()
        try:
            p = poolmod.Pool.load(m)
        except Exception:
            return None
        me = m.origin if (self.root / "sync" / "origin.json").exists() else None
        if origin == me or not p.is_member(origin) or (me and not p.is_member(me)):
            return None
        x = next((x for x in p.active() if x["origin"] == origin), None)
        if x is None:
            return None
        near = lan.found(m).get(origin)
        addresses = list(dict.fromkeys([*x["addresses"], *([near] if near else [])]))
        return {**x, "addresses": addresses} if addresses else None

    def reachable(self) -> list[dict]:
        try:
            p = poolmod.Pool.load(self.m())
        except Exception:
            return []
        return [x for x in (self.member(o["origin"]) for o in p.active()) if x]

    def link(self, origin: str) -> _Link:
        with self.links_lock:
            k = self.links.get(origin)
            if k is None or not k.alive():
                k = self.links[origin] = _Link(self, origin)
            return k

    def sync_member(self, x: dict, why: str) -> bool:
        """Sync with one member now, trying each of its addresses. Whether it
        went through."""
        for address in x["addresses"]:
            try:
                row = peer.sync_with(self.m(), address, x["origin"])
            except Exception as e:
                self.log({"why": why, "peer": x["origin"], "name": x["name"], "address": address, "failed": str(e)})
                continue
            if row.get("pulled") or row.get("pushed") or row.get("refused"):
                self.log({"why": why, **row, "name": x["name"]})
            self.synced += 1
            return True
        return False

    def sync_all(self, why: str):
        """Ask for a sync with every member that can be reached. Doesn't wait:
        each member's link does it, side by side."""
        for x in self.reachable():
            self.link(x["origin"]).request(why)

    def sync_one(self, x: dict, why: str):
        self.link(x["origin"]).request(why, x["addresses"][0] if x.get("addresses") else None)

    def _listen(self, x: dict):
        """Long-poll one member: sync with it whenever its logs change, and
        whenever it's reached again after being out of reach (it may have
        changed meanwhile, and what changed here may not have reached it)."""
        seen, failures = None, 0
        while not self.stopping.is_set():
            here = self.member(x["origin"])
            if here is None:
                return
            answered = False
            for address in here["addresses"]:
                conn = peer.Peer(self.m(), address, x["origin"], timeout=self.wait + 15)
                try:
                    q = f"wait?timeout={int(self.wait)}" + (f"&seen={seen}" if seen else "")
                    out = conn.json("GET", q)
                except Exception:
                    continue
                finally:
                    conn.close()
                if out["fingerprint"] != seen:
                    self.sync_one(here, f"{x['name']} changed" if seen else f"reached {x['name']}")
                seen, answered = out["fingerprint"], True
                break
            if answered:
                failures = 0
            else:
                seen, failures = None, failures + 1
                self.stopping.wait(min(30, 2 ** failures))

    def _members(self):
        """Keep a listener on each member that can be reached."""
        for x in self.reachable():
            t = self.listeners.get(x["origin"])
            if t is None or not t.is_alive():
                t = threading.Thread(target=self._listen, args=(x,), daemon=True)
                t.start()
                self.listeners[x["origin"]] = t

    def _found_nearby(self, member: dict, address: str):
        self.log({"found": member["origin"], "name": member["name"], "address": address, "on": "the local network"})
        self.sync_one({**member, "addresses": [address]}, f"found {member['name']} on the local network")

    def run(self):
        if self.lan_port is not None and self.lan is None:
            from . import lan
            try:
                self.lan = lan.Listener(self.m(), self._found_nearby, self.lan_port).start()
            except OSError as e:
                self.log({"lan": f"not listening for members on the local network: {e}"})
        last = fingerprint(self.m())
        next_full = time.time()
        skew = time.time() - time.monotonic()
        while not self.stopping.is_set():
            self._members()
            fp = fingerprint(self.m())
            now_skew = time.time() - time.monotonic()
            if abs(now_skew - skew) > 5:
                # the clock jumped against the monotonic one: the machine slept, or its clock was set.
                # Connections may be dead and members changed meanwhile: catch up now.
                skew = now_skew
                self.sync_all("woke up")
                next_full = time.time() + self.every
            if fp != last:
                self.stopping.wait(self.debounce)       # a burst of writes goes as one sync
                last = fingerprint(self.m())
                self.sync_all("changed here")
            elif time.time() >= next_full:
                self.sync_all("every few minutes")
                next_full = time.time() + self.every
            self.stopping.wait(self.poll)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    def stop(self):
        self.stopping.set()
        if self.lan:
            self.lan.stop()


def run(m: Matter, every: float = 300, lan_port: int | None = None) -> int:
    """``adhoc sync watch``: run until stopped, unless another watcher has
    this matter."""
    (m.root / "run").mkdir(exist_ok=True)
    with open(m.root / "run" / "sync.lock", "w", encoding="utf-8", newline="\n") as f:
        try:
            with locked(f, wait=False):
                if not poolmod.founded(m):
                    print(json.dumps({"watching": False, "why": "this matter has no pool"}), file=sys.stderr)
                    return 0
                print(json.dumps({"watching": m.id, "origin": m.origin}), file=sys.stderr, flush=True)
                w = Watcher(m, every, lan_port=lan_port)
                try:
                    w.run()
                except KeyboardInterrupt:
                    w.stop()
                return 0
        except BlockingIOError:
            print(json.dumps({"watching": False, "why": "another watcher has this matter"}), file=sys.stderr)
            return 0
