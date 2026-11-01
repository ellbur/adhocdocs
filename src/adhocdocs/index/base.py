"""What every index has: a directory, a manifest, settings, and a cursor
into the change feed advanced in the same transaction as its rows."""

import json
import sqlite3
from contextlib import ExitStack, contextmanager
from pathlib import Path

from ..matter import Matter
from ..portable import locked

BATCH = 200


class NeedsRebuild(Exception):
    pass


class Busy(Exception):
    pass


def still_true(ev: dict, state) -> bool:
    """Whether an event still describes the matter, given everything the feeds
    hold now.

    Events from other installations can arrive late, and sort before events
    an index has already applied: a document's ``add-document`` after its
    ``withdraw``, say. Indexes apply each event once, in the order they get
    them, so an event that later ones have overtaken is skipped: whatever
    overtook it is (or will be) applied instead. Then an index ends up
    agreeing with the replayed state whatever order its events came in."""
    op = ev["op"]
    if op in ("add-document", "set-metadata"):
        doc = ev["doc"]
        live = doc in state.documents and doc not in state.withdrawn
        return live and (op == "add-document" or state.metadata.get(doc) == ev["metadata"])
    if op == "withdraw":
        return ev["doc"] in state.withdrawn
    if op == "supersede":
        return ev["old"] in state.withdrawn
    if op == "set-primary":
        return state.primary.get(ev["content"]) == ev["layer"]
    return True


class Index:
    kind = ""

    def __init__(self, m: Matter, name: str):
        self.m = m
        self.name = name
        self.dir = m.indexes_dir / name
        self._db = None

    # -- manifest and settings -------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    def exists(self) -> bool:
        return self.manifest_path.exists()

    def settings(self) -> dict:
        """The settings this code would build the index with."""
        return {}

    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def create(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest({})
        self.init_db()

    def _write_manifest(self, cursor):
        man = {"type": "index", "v": 1, "name": self.name, "kind": self.kind,
               "settings": self.settings(), "cursor": cursor, "updater": f"idx-{self.kind}"}
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8", newline="\n")
        tmp.replace(self.manifest_path)

    def check_settings(self):
        if self.manifest()["settings"] != self.settings():
            raise NeedsRebuild(f"index {self.name} was built with other settings; run: idx rebuild {self.name}")

    # -- database ----------------------------------------------------------------

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = sqlite3.connect(self.dir / "index.sqlite")
            self._db.execute("PRAGMA journal_mode=WAL")
        return self._db

    def init_db(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        """)
        self.db.commit()

    def cursor(self) -> dict:
        row = self.db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
        return self.m.upgrade_cursor(json.loads(row[0]) if row else {})

    def _set_cursor(self, cursor):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('cursor', ?)", (json.dumps(cursor),))

    # -- updating ------------------------------------------------------------------

    def behind(self) -> bool:
        cur = self.cursor()
        return any(cur.get(k) != last for k, last in self.m.cursor().items())

    @contextmanager
    def lock(self, wait=True):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "lock", "w", encoding="utf-8", newline="\n") as f:
            with ExitStack() as held:
                try:
                    held.enter_context(locked(f, wait))
                except BlockingIOError:
                    raise Busy(self.name)
                yield

    def update(self, progress=None) -> int:
        """Apply events since the cursor. Returns the number applied."""
        self.check_settings()
        with self.lock():
            cursor = self.cursor()
            self.m.refresh()
            batch, n = [], 0
            for ev in self.m.changes(cursor):
                batch.append(ev)
                if len(batch) >= BATCH:
                    n += self._apply_batch(batch, cursor)
                    batch = []
                    if progress:
                        progress(n)
            if batch:
                n += self._apply_batch(batch, cursor)
            self._write_manifest(self.cursor())
            return n

    def _apply_batch(self, batch, cursor) -> int:
        with self.db:
            state = self.m.state
            for ev in batch:
                if ev["op"] == "purge":         # each document, as if withdrawn
                    for d in ev.get("docs") or []:
                        w = {**ev, "op": "withdraw", "doc": d}
                        if still_true(w, state):
                            self.apply(w)
                elif still_true(ev, state):
                    self.apply(ev)
                cursor[ev["log"]] = ev["id"]
            self._set_cursor(cursor)
        return len(batch)

    def apply(self, ev: dict):
        raise NotImplementedError

    def verify(self, repair=False) -> dict:
        return {}

    def status(self) -> dict:
        size = sum(f.stat().st_size for f in self.dir.iterdir() if f.is_file())
        return {"name": self.name, "kind": self.kind, "settings": self.manifest()["settings"],
                "cursor": self.cursor(), "behind": self.behind(), "bytes": size}


# Text-hash bookkeeping shared by the text indexes.

class TextIndex(Index):
    """An index keyed by layer text hash: each text is indexed once."""

    def init_db(self):
        super().init_db()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS texts(rowid INTEGER PRIMARY KEY, text_hash TEXT UNIQUE NOT NULL);
        """)
        self.db.commit()

    def indexed(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT text_hash FROM texts")}

    def has_text(self, text_hash: str) -> bool:
        return self.db.execute("SELECT 1 FROM texts WHERE text_hash=?", (text_hash,)).fetchone() is not None

    def wants_view(self, view: str) -> bool:
        return True

    def apply(self, ev: dict):
        op = ev["op"]
        state = self.m.state
        if op == "add-layer":
            if self.wants_view(ev["view"]) and not self.has_text(ev["text"]) and self._used(ev["content"]):
                self.add_text(ev["text"])
        elif op == "add-document":
            for l in state.layers.get(ev["content"], []):
                if self.wants_view(l.view) and not self.has_text(l.text):
                    self.add_text(l.text)
        elif op == "withdraw":
            self._remove_unshared(state.documents.get(ev["doc"]))

    def _used(self, content) -> bool:
        """Whether a live document has this content."""
        state = self.m.state
        return any(state.documents[d] == content and d not in state.withdrawn
                   for d in state.by_content.get(content, ()))

    def _remove_unshared(self, content):
        """Delete a withdrawn content's texts unless a live document still uses them."""
        if content is None:
            return
        state = self.m.state
        live_texts = {l.text for d in state.live_documents() for l in state.layers.get(state.documents[d], [])}
        for l in state.layers.get(content, []):
            if l.text not in live_texts and self.has_text(l.text):
                self.remove_text(l.text)

    def add_text(self, text_hash: str):
        raise NotImplementedError

    def remove_text(self, text_hash: str):
        raise NotImplementedError

    def verify(self, repair=False) -> dict:
        """Compare the texts held with the texts the matter's live documents have."""
        state = self.m.state
        want = {l.text for d in state.live_documents() for l in state.layers.get(state.documents[d], [])
                if self.wants_view(l.view)}
        have = self.indexed()
        missing, extra = sorted(want - have), sorted(have - want)
        if repair:
            with self.db:
                for t in missing:
                    self.add_text(t)
                for t in extra:
                    self.remove_text(t)
        return {"name": self.name, "missing": len(missing), "extra": len(extra), "repaired": repair}
