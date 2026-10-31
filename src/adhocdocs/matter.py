"""A matter on disk, its append-only logs, and the state they describe.

Layout::

    <matter>/
      store/objects/          originals, document/layer/metadata objects, part files
      store/log.jsonl         provenance log: the "store" change feed
      annotations/log.jsonl   tags and notes
      workspace/              work product (read-write in the container)
        .adhoc/objects/       derived layers made in the workspace
        .adhoc/log.jsonl      the "workspace" change feed
        .adhoc/indexes/       indexes (derived, disposable, not in git)

Every change to what is in the matter is an event in one of the two feeds,
including removals (``withdraw``) and corrections (``supersede``).
"""

import fcntl
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .objects import ObjectStore

FEEDS = ("store", "workspace")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_last_ulid = [0, 0]


def new_event_id() -> str:
    """``ev_`` plus a ULID. Monotonic within a process, so IDs sort in log order."""
    ms = int(time.time() * 1000)
    if ms <= _last_ulid[0]:
        ms = _last_ulid[0]
        rand = _last_ulid[1] + 1
    else:
        rand = secrets.randbits(80)
    _last_ulid[:] = [ms, rand]
    n = (ms << 80) | rand
    s = "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))
    return "ev_" + s


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class EventLog:
    """An append-only JSON Lines log."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, **event) -> dict:
        event = {"id": new_event_id(), "at": now(), **event}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def read(self, since: str | None = None):
        """Events after the one with ID ``since`` (all events if None)."""
        if not self.path.exists():
            return
        seen = since is None
        with open(self.path) as f:
            for line in f:
                if not line.strip():
                    continue
                ev = json.loads(line)
                if seen:
                    yield ev
                elif ev["id"] == since:
                    seen = True
        if not seen:
            raise ValueError(f"cursor {since} not found in {self.path}")

    def last_id(self) -> str | None:
        """ID of the last event, reading only the end of the file."""
        if not self.path.exists():
            return None
        with open(self.path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            lines = [l for l in f.read().splitlines() if l.strip()]
        return json.loads(lines[-1])["id"] if lines else None


class MatterNotFound(Exception):
    pass


class Matter:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.store_dir = self.root / "store"
        self.workspace = self.root / "workspace"
        self.adhoc_dir = self.workspace / ".adhoc"
        self.objects = ObjectStore(self.store_dir / "objects", self.adhoc_dir / "objects")
        self.workspace_objects = ObjectStore(self.adhoc_dir / "objects", self.store_dir / "objects")
        self.logs = {
            "store": EventLog(self.store_dir / "log.jsonl"),
            "workspace": EventLog(self.adhoc_dir / "log.jsonl"),
        }
        self.annotation_log = EventLog(self.root / "annotations" / "log.jsonl")
        self.indexes_dir = self.adhoc_dir / "indexes"
        self._state = None

    @classmethod
    def create(cls, root: Path) -> "Matter":
        root = Path(root)
        for d in ("store/objects", "annotations", "workspace/.adhoc/objects"):
            (root / d).mkdir(parents=True, exist_ok=True)
        for f in ("store/log.jsonl", "annotations/log.jsonl", "workspace/.adhoc/log.jsonl"):
            (root / f).touch()
        gi = root / "workspace" / ".gitignore"
        if not gi.exists():
            gi.write_text(".adhoc/indexes/\n")
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
        """The current end of each feed."""
        return {name: log.last_id() for name, log in self.logs.items()}

    def changes(self, since: dict | None = None):
        """Both feeds, each event tagged with its feed. Feeds are independent,
        so they are concatenated rather than interleaved."""
        since = since or {}
        for name, log in self.logs.items():
            for ev in log.read(since.get(name)):
                yield {"feed": name, **ev}

    @property
    def state(self) -> "State":
        if self._state is None:
            self._state = State.replay(self.changes())
        return self._state


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

    @classmethod
    def replay(cls, events) -> "State":
        s = cls()
        for ev in events:
            s.apply(ev)
        return s

    def apply(self, ev: dict):
        op = ev["op"]
        if op == "add-document":
            self.documents[ev["doc"]] = ev["content"]
            self.withdrawn.pop(ev["doc"], None)
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
