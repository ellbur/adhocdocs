"""A matter on disk, its append-only logs, and the state they describe.

Layout::

    <matter>/
      matter.toml                       the matter's ID (shared by every installation of it)
      store/objects/                    originals, document/layer/metadata objects, part files
      store/logs/<origin>.jsonl         provenance log: the "store" change feed
      annotations/logs/<origin>.jsonl   tags, notes and entities
      work/logs/<origin>.jsonl          published work product (adhocdocs.work)
      work/<name>                       published files, laid out from that feed (derived)
      pool/logs/<origin>.jsonl          the installations it syncs among (adhocdocs.pool)
      sync/                             this installation's identity (host only; adhocdocs.origin)
      audit/                            what each session did (host only; see adhocdocs.audit)
      workspace/                        work product (read-write in the container)
        .adhoc/objects/                 derived layers made in the workspace
        .adhoc/log.jsonl                the "workspace" change feed
        .adhoc/indexes/                 indexes (derived, disposable, not in git)

Every change to what is in the matter is an event in one of the feeds,
including removals (``withdraw``) and corrections (``supersede``).

**One log per origin.** The store and annotation feeds are each a set of
logs, one per installation (an *origin*, :mod:`adhocdocs.origin`), and each
installation appends only to its own. A feed is read as all its logs
merged in one order every installation agrees on, so installations holding
the same logs reach the same state, however the logs got to them (SYNC.md).

- **Event IDs are ULIDs from a hybrid logical clock**: an installation never
  stamps an event below the newest one it holds in that feed, whatever its
  clock says, so an event made after seeing another sorts after it.
- **The merge order** takes, at each step, the earliest of the logs' next
  events (by ID; ties by origin), each log in its own file order. When every
  log is sorted, as the clock ensures for new events, that is ID order.
- **Cursors** say how far a reader has got in each log: a flat object from
  log key (``store/<origin>``, ``annotations/<origin>``, ``workspace``) to
  the last event read. Every event a feed yields says its ``feed``, its
  ``log`` key and (for synced feeds) its ``origin``, so a reader advances its
  cursor with ``cursor[ev["log"]] = ev["id"]``. An event that arrives late
  from another installation and sorts early is still past the cursor.

The workspace is not synced, so its feed is one log with no origins.

A matter made before this layout had one ``store/log.jsonl`` and one
``annotations/log.jsonl``. Opening it on the host moves each into the logs
of a new origin, unchanged; ``matter.toml`` records which (``legacy_origin``),
so cursors that name the old feeds still work.
"""

import heapq
import json
import os
import secrets
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .objects import ObjectStore
from .portable import locked

FEEDS = ("store", "workspace")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_CROCKFORD)}
_last_ulid = [0, 0]


def _encode_ulid(ms: int, rand: int) -> str:
    n = (ms << 80) | (rand & ((1 << 80) - 1))
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def _decode_ulid(s: str) -> tuple[int, int]:
    n = 0
    for c in s:
        n = (n << 5) | _DECODE[c]
    return n >> 80, n & ((1 << 80) - 1)


def new_event_id(after: str | None = None) -> str:
    """``ev_`` plus a ULID. Monotonic within a process, so IDs sort in log
    order. With ``after`` (an event ID), the new ID also sorts after it: the
    hybrid logical clock that keeps a feed's order causal across machines."""
    ms = int(time.time() * 1000)
    floor = _last_ulid[:]
    if after and after.startswith("ev_") and len(after) == 29:
        try:
            a = list(_decode_ulid(after[3:]))
        except KeyError:
            a = None
        if a and a > floor:
            floor = a
    if ms <= floor[0]:
        ms, rand = floor[0], floor[1] + 1
        if rand >= 1 << 80:
            ms, rand = ms + 1, 0
    else:
        rand = secrets.randbits(80)
    _last_ulid[:] = [ms, rand]
    return "ev_" + _encode_ulid(ms, rand)


def new_id(prefix: str) -> str:
    """A fresh ``<prefix>_<ULID>`` for things events create (notes, entities…)."""
    return f"{prefix}_" + new_event_id()[3:]


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class EventLog:
    """An append-only JSON Lines log: one file, one writer."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, **event) -> dict:
        event = {"id": new_event_id(), "at": now(), **event}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as f, locked(f):
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def events(self):
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if not line.endswith("\n"):
                    break           # being written right now: not there yet
                if line.strip():
                    yield json.loads(line)

    def read(self, since: str | None = None):
        """Events after the one with ID ``since`` (all events if None)."""
        seen = since is None
        for ev in self.events():
            if seen:
                yield ev
            elif ev["id"] == since:
                seen = True
        if not seen:
            raise ValueError(f"cursor {since} not found in {self.path}")

    def last_event(self) -> dict | None:
        """The last event, reading only the end of the file (or more of it,
        for a long line)."""
        if not self.path.exists():
            return None
        with open(self.path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            back = 65536
            while True:
                f.seek(max(0, size - back))
                data = f.read()
                lines = [l for l in data.splitlines() if l.strip()]
                if not lines:
                    return None
                if len(lines) > 1 or back >= size:
                    return json.loads(lines[-1])
                back *= 4

    def last_id(self) -> str | None:
        """ID of the last event, reading only the end of the file."""
        last = self.last_event()
        return last["id"] if last else None

    def count(self) -> int:
        if not self.path.exists():
            return 0
        with open(self.path, "rb") as f:
            return sum(1 for l in f if l.strip())


class Feed:
    """A change feed: one log per origin (or a single log), read merged.

    ``identity`` gives the origin this installation appends as; a feed
    without one (in a session, where the store and annotations are
    read-only) can only be read."""

    def __init__(self, name: str, logs_dir: Path | None = None, single: Path | None = None,
                 identity=None, legacy: Path | None = None, cutoffs: Path | None = None):
        self.name = name
        self.dir = Path(logs_dir) if logs_dir else None
        self.single = Path(single) if single else None
        self.identity = identity
        self.cutoffs_path = Path(cutoffs) if cutoffs else None
        self.legacy = Path(legacy) if legacy else None

    def logs(self) -> list[tuple[str, str | None, EventLog]]:
        """``(log key, origin, log)`` for every log, in merge tie-break order."""
        if self.single is not None:
            return [(self.name, None, EventLog(self.single))]
        found = []
        if self.dir is not None and self.dir.is_dir():
            found = [(p.stem, p) for p in self.dir.glob("*.jsonl")]
        if self.legacy is not None and self.legacy.exists():
            found.append(("legacy", self.legacy))       # an unmigrated matter, read-only
        return [(f"{self.name}/{o}", o, EventLog(p)) for o, p in sorted(found)]

    def cutoffs(self) -> dict[str, int]:
        """Origins removed from the pool, and how many of their events count
        (``pool/cutoffs.json``, written by adhocdocs.pool): what a removed
        installation wrote after its removal is set aside, here and in the
        viewer."""
        if self.cutoffs_path is None:
            return {}
        try:
            data = json.loads(self.cutoffs_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return {o: n for o, n in data.get("feeds", {}).get(self.name, {}).items() if isinstance(n, int)}

    def log_for(self, origin: str) -> EventLog:
        """One origin's log. ``origin`` often comes from another installation,
        so it must be an origin ID: never a path."""
        from .origin import is_origin
        if not is_origin(origin):
            raise ValueError(f"{origin!r} isn't an origin")
        return EventLog(self.dir / f"{origin}.jsonl")

    # -- reading -----------------------------------------------------------------

    def read(self, since=None):
        """Events in merge order. ``since`` is a cursor (log key -> last event
        read, missing keys meaning none), or an event ID: the events after it
        in merge order. Each event comes tagged with ``feed``, ``log`` and
        ``origin``."""
        if isinstance(since, str):
            seen = False
            for ev in self.read():
                if seen:
                    yield ev
                elif ev["id"] == since:
                    seen = True
            if not seen:
                raise ValueError(f"event {since} not found in the {self.name} feed")
            return
        since = since or {}
        cut = self.cutoffs()

        def tagged(key, origin, log):
            tag = {"feed": self.name, "log": key, **({"origin": origin} if origin else {})}
            limit = cut.get(origin)
            if limit is None:
                for ev in log.read(since.get(key)):
                    yield {**ev, **tag}
                return
            after = since.get(key)
            seen = after is None
            for i, ev in enumerate(log.events()):
                if i >= limit:
                    break
                if seen:
                    yield {**ev, **tag}
                elif ev["id"] == after:
                    seen = True

        streams = [tagged(*l) for l in self.logs()]
        if len(streams) == 1:
            yield from streams[0]
        else:
            yield from heapq.merge(*streams, key=lambda ev: ev["id"])

    def cursor(self) -> dict:
        """The end of every log: a cursor that has read everything."""
        cut = self.cutoffs()
        out = {}
        for key, origin, log in self.logs():
            if origin in cut:
                last = None
                for i, ev in enumerate(log.events()):
                    if i >= cut[origin]:
                        break
                    last = ev["id"]
            else:
                last = log.last_id()
            if last:
                out[key] = last
        return out

    def last_id(self) -> str | None:
        """The greatest event ID in the feed."""
        return max(self.cursor().values(), default=None)

    def vector(self) -> dict:
        """How many events each origin's log holds: what sync compares."""
        return {origin: log.count() for _, origin, log in self.logs() if origin and origin != "legacy"}

    # -- writing -----------------------------------------------------------------

    def append(self, **event) -> dict:
        """Append to this installation's own log, with an ID that sorts after
        every event the feed holds."""
        if self.single is not None:
            ev = EventLog(self.single).append(**event)
            return {**ev, "feed": self.name, "log": self.name}
        if self.identity is None:
            raise PermissionError(f"the {self.name} feed is read-only here")
        from .signing import sign
        origin = self.identity.origin
        key = self.identity.key
        path = self.dir / f"{origin}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            with open(path, "a", encoding="utf-8", newline="\n") as f, locked(f):
                if not same_file(f, path):
                    continue                # replaced while we waited (a purge's redaction): open it again
                ev = {"id": new_event_id(after=self.last_id()), "at": now(), **event}
                ev = sign(ev, key, EventLog(path).last_event())
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                break
        return {**ev, "feed": self.name, "log": f"{self.name}/{origin}", "origin": origin}


def same_file(f, path) -> bool:
    """Whether the open file ``f`` is still what ``path`` names."""
    try:
        return os.path.samestat(os.fstat(f.fileno()), os.stat(path))
    except FileNotFoundError:
        return False


class MatterNotFound(Exception):
    pass


def read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def write_toml(path: Path, data: dict):
    lines = [f"{k} = {json.dumps(v)}" for k, v in data.items() if isinstance(v, (str, int, bool))]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("# This matter's identity. Written by adhocdocs; see SYNC.md.\n" + "\n".join(lines) + "\n",
                   encoding="utf-8", newline="\n")
    tmp.replace(path)


class Matter:
    def __init__(self, root: Path):
        from .origin import Identity
        self.root = Path(root).resolve()
        self.store_dir = self.root / "store"
        self.workspace = self.root / "workspace"
        self.adhoc_dir = self.workspace / ".adhoc"
        self.sync_dir = self.root / "sync"
        self.objects = ObjectStore(self.store_dir / "objects", self.adhoc_dir / "objects")
        self.workspace_objects = ObjectStore(self.adhoc_dir / "objects", self.store_dir / "objects")
        self.identity = Identity(self.sync_dir, self.root)
        self._migrate()
        cutoffs = self.root / "pool" / "cutoffs.json"
        self.logs = {
            "store": Feed("store", self.store_dir / "logs", identity=self.identity,
                          legacy=self.store_dir / "log.jsonl", cutoffs=cutoffs),
            "workspace": Feed("workspace", single=self.adhoc_dir / "log.jsonl"),
        }
        self.annotation_log = Feed("annotations", self.root / "annotations" / "logs", identity=self.identity,
                                   legacy=self.root / "annotations" / "log.jsonl", cutoffs=cutoffs)
        self.work_log = Feed("work", self.root / "work" / "logs", identity=self.identity, cutoffs=cutoffs)
        self.pool_log = Feed("pool", self.root / "pool" / "logs", identity=self.identity)
        self.indexes_dir = self.adhoc_dir / "indexes"
        self._state = None

    @property
    def config(self) -> dict:
        cfg = read_toml(self.root / "matter.toml")
        if not cfg and os.environ.get("ADHOC_MATTER_ID"):
            # in a session, matter.toml isn't mounted: the launcher passes what it says
            cfg = {"id": os.environ["ADHOC_MATTER_ID"],
                   **({"founder": os.environ["ADHOC_POOL_FOUNDER"]} if os.environ.get("ADHOC_POOL_FOUNDER") else {})}
        return cfg

    @property
    def id(self) -> str | None:
        return self.config.get("id")

    @property
    def origin(self) -> str:
        """The origin this installation writes as (made on first use)."""
        return self.identity.origin

    def _migrate(self):
        """Give a matter made before per-origin logs its ID, and move its
        single logs into the logs of this installation's origin. Only where
        the matter is writable (the host); a session reads what's there."""
        cfg = read_toml(self.root / "matter.toml")
        legacy = [(self.store_dir / "log.jsonl", self.store_dir / "logs"),
                  (self.root / "annotations" / "log.jsonl", self.root / "annotations" / "logs")]
        todo = [(old, d) for old, d in legacy if old.exists()]
        if cfg.get("id") and not todo:
            return
        if not self.store_dir.is_dir() or not os.access(self.store_dir, os.W_OK) \
                or not os.access(self.root, os.W_OK):
            return
        if not cfg.get("id"):
            cfg["id"] = new_id("mat")
        if todo:
            origin = self.identity.origin
            cfg.setdefault("legacy_origin", origin)
            for old, d in todo:
                d.mkdir(parents=True, exist_ok=True)
                dst = d / f"{cfg['legacy_origin']}.jsonl"
                if dst.exists() and dst.stat().st_size:
                    raise RuntimeError(f"can't migrate {old}: {dst} already has events")
                os.replace(old, dst)
        write_toml(self.root / "matter.toml", cfg)

    @classmethod
    def create(cls, root: Path, matter_id: str | None = None) -> "Matter":
        root = Path(root)
        for d in ("store/objects", "store/logs", "annotations/logs", "work/logs", "pool/logs", "workspace/.adhoc/objects"):
            (root / d).mkdir(parents=True, exist_ok=True)
        (root / "workspace/.adhoc/log.jsonl").touch()
        if not (root / "matter.toml").exists():
            write_toml(root / "matter.toml", {"id": matter_id or new_id("mat")})
        gi = root / "workspace" / ".gitignore"
        if not gi.exists():
            gi.write_text(".adhoc/indexes/\n", encoding="utf-8", newline="\n")
        return cls(root)

    @classmethod
    def find(cls, explicit: str | None = None) -> "Matter":
        """``--matter``, else ``$ADHOC_MATTER``, else the nearest directory at
        or above the current one that holds a ``store/``."""
        if explicit or os.environ.get("ADHOC_MATTER"):
            root = Path(explicit or os.environ["ADHOC_MATTER"])
            if not (root / "store").is_dir():
                raise MatterNotFound(f"{root} is not a matter (no store/)")
            return cls(root)
        here = Path.cwd().resolve()
        for d in (here, *here.parents):
            if (d / "store" / "objects").is_dir():
                return cls(d)
        raise MatterNotFound("no matter here: use --matter or set ADHOC_MATTER")

    def cursor(self) -> dict:
        """The current end of every log of the feeds indexes read."""
        out = {}
        for feed in self.logs.values():
            out.update(feed.cursor())
        return out

    def upgrade_cursor(self, cursor: dict | None) -> dict:
        """A cursor in the current form. One from before per-origin logs
        (``{"store": id, "workspace": id}``) names the logs that became the
        legacy origin's."""
        cursor = dict(cursor or {})
        legacy = self.config.get("legacy_origin") or "legacy"
        if "store" in cursor:
            old = cursor.pop("store")
            if old:
                cursor.setdefault(f"store/{legacy}", old)
        return {k: v for k, v in cursor.items() if v}

    def changes(self, since: dict | None = None):
        """The store feed, then the workspace feed. Feeds are independent, so
        they are concatenated rather than interleaved."""
        since = self.upgrade_cursor(since)
        for feed in self.logs.values():
            yield from feed.read(since)

    @property
    def state(self) -> "State":
        if self._state is None:
            self._state = State.replay(self.changes())
        return self._state

    def refresh(self) -> "State":
        """Forget the replayed state, so the next look sees new events."""
        self._state = None
        return self.state


@dataclass
class LayerInfo:
    id: str
    content: str
    view: str
    text: str
    feed: str


@dataclass
class State:
    """What the feeds say is in the matter right now."""

    documents: dict = field(default_factory=dict)   # doc -> content
    withdrawn: dict = field(default_factory=dict)   # doc -> reason
    superseded: dict = field(default_factory=dict)  # old doc -> new doc
    layers: dict = field(default_factory=dict)      # content -> [LayerInfo]
    primary: dict = field(default_factory=dict)     # content -> layer
    metadata: dict = field(default_factory=dict)    # doc -> metadata object hash
    children: dict = field(default_factory=dict)    # doc -> [child docs]
    by_content: dict = field(default_factory=dict)  # content -> [docs]
    purged: set = field(default_factory=set)        # docs purged: gone for good (adhocdocs.purge)

    @classmethod
    def replay(cls, events) -> "State":
        s = cls()
        for ev in events:
            s.apply(ev)
        return s

    def apply(self, ev: dict):
        op = ev["op"]
        if op == "add-document":
            new = ev["doc"] not in self.documents
            self.documents[ev["doc"]] = ev["content"]
            if ev["doc"] not in self.purged:            # ingesting it again doesn't bring it back
                self.withdrawn.pop(ev["doc"], None)
            if new:     # the same document ingested on two installations is one document
                self.by_content.setdefault(ev["content"], []).append(ev["doc"])
                if ev.get("parent"):
                    self.children.setdefault(ev["parent"], []).append(ev["doc"])
        elif op == "add-layer":
            info = LayerInfo(ev["layer"], ev["content"], ev["view"], ev["text"], ev.get("feed", "store"))
            lst = self.layers.setdefault(ev["content"], [])
            if all(l.id != info.id for l in lst):
                lst.append(info)
        elif op == "set-primary":
            self.primary[ev["content"]] = ev["layer"]
        elif op == "set-metadata":
            self.metadata[ev["doc"]] = ev["metadata"]
        elif op == "supersede":
            self.superseded[ev["old"]] = ev["new"]
            self.withdrawn[ev["old"]] = "superseded"
        elif op == "withdraw":
            self.withdrawn[ev["doc"]] = ev.get("reason", "")
        elif op == "purge" and str(ev.get("author", "")).startswith("user:"):
            for d in ev.get("docs") or []:
                self.purged.add(d)
                self.withdrawn[d] = "purged"

    def live_documents(self) -> list[str]:
        return [d for d in self.documents if d not in self.withdrawn]

    def follow(self, doc: str) -> str:
        seen = set()
        while doc in self.superseded and doc not in seen:
            seen.add(doc)
            doc = self.superseded[doc]
        return doc

    def primary_layer(self, content: str) -> LayerInfo | None:
        layers = self.layers.get(content, [])
        pid = self.primary.get(content)
        for l in layers:
            if l.id == pid:
                return l
        return layers[0] if layers else None

    def layer(self, layer_id: str) -> LayerInfo | None:
        for lst in self.layers.values():
            for l in lst:
                if l.id == layer_id:
                    return l
        return None
