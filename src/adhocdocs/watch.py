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
- Every few minutes it syncs with everyone anyway, in case anything was
  missed.

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


class Watcher:
    def __init__(self, m: Matter, every: float = 300, debounce: float = 1.0, poll: float = 1.0, wait: float = 50,
                 log=None, lan_port: int | None = None):
        self.root = m.root
        self.every, self.debounce, self.poll, self.wait = every, debounce, poll, wait
        self.lock = threading.Lock()          # one sync at a time
        self.stopping = threading.Event()
        self.listeners: dict[str, threading.Thread] = {}
        self.log = log or (lambda row: print(json.dumps(row), file=sys.stderr, flush=True))
        self.synced = 0
        self.lan_port = lan_port          # listen for members on the local network (adhocdocs.lan); None: don't
        self.lan = None

    def m(self) -> Matter:
        return Matter(self.root)

    def sync_all(self, why: str):
        with self.lock:
            for row in peer.sync_all(self.m()):
                if row.get("pulled") or row.get("pushed") or row.get("failed") or row.get("refused"):
                    self.log({"why": why, **row})
            self.synced += 1

    def sync_one(self, x: dict, why: str):
        with self.lock:
            for address in x["addresses"]:
                try:
                    row = peer.sync_with(self.m(), address, x["origin"])
                    if row.get("pulled") or row.get("pushed") or row.get("refused"):
                        self.log({"why": why, **row, "name": x["name"]})
                    break
                except Exception as e:
                    self.log({"why": why, "peer": x["origin"], "address": address, "failed": str(e)})
            self.synced += 1

    def _listen(self, x: dict):
        """Long-poll one member: sync with it whenever its logs change."""
        seen = None
        while not self.stopping.is_set():
            try:
                p = poolmod.Pool.load(self.m())
                if not p.is_member(x["origin"]) or not p.is_member(self.m().origin):
                    return
                from . import lan
                near = lan.found(self.m()).get(x["origin"])
                for address in list(dict.fromkeys(p.members[x["origin"]]["addresses"] + ([near] if near else []))):
                    conn = peer.Peer(self.m(), address, x["origin"], timeout=self.wait + 15)
                    try:
                        q = f"wait?timeout={int(self.wait)}" + (f"&seen={seen}" if seen else "")
                        out = conn.json("GET", q)
                    finally:
                        conn.close()
                    if seen is not None and out["fingerprint"] != seen:
                        self.sync_one(x, f"{x['name']} changed")
                    seen = out["fingerprint"]
                    break
                else:
                    self.stopping.wait(30)
            except Exception:
                seen = None
                self.stopping.wait(min(30, self.every))

    def _members(self):
        """Keep a listener on each member that says where it listens."""
        try:
            p = poolmod.Pool.load(self.m())
        except Exception:
            return
        me = self.m().origin if (self.root / "sync" / "origin.json").exists() else None
        from . import lan
        near = lan.found(self.m())
        for x in p.active():
            if x["origin"] == me or not (x["addresses"] or x["origin"] in near):
                continue
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
        while not self.stopping.is_set():
            self._members()
            fp = fingerprint(self.m())
            if fp != last:
                self.stopping.wait(self.debounce)       # a burst of writes goes as one sync
                self.sync_all("changed here")
                last = fingerprint(self.m())
            elif time.time() >= next_full:
                self.sync_all("every few minutes")
                last = fingerprint(self.m())
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
