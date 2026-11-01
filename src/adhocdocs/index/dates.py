"""The dates index: the dates written in each layer text
(:func:`adhocdocs.dates.detect`), and each email's thread, for the viewer's
date cards (DATES.md).

Like the entities index, it keeps plain files the viewer reads without
Python:

- ``texts/<hash>.json``: ``{"text": hash, "dates": [...]}``, one per text;
- ``threads.json``: ``{"threads": {thread: [doc, …]}, "of": {doc: [thread, i]}}``:
  each thread of two or more messages (named by its first message's
  document), its messages oldest first (one document per message), and
  each email's thread and place in it (copies of a message share it).
  Rewritten after every update.

Schema::

    mentions(text_hash, start, end, kind, date, data)    data: the mention as JSON
"""

import json

from .. import dates
from .base import TextIndex


class DatesIndex(TextIndex):
    kind = "dates"

    def settings(self):
        return {"rules": dates.VERSION}

    def wants_view(self, view):
        return view not in ("markup", "original")

    def init_db(self):
        super().init_db()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS mentions(text_hash TEXT NOT NULL, start INTEGER, end INTEGER, kind TEXT,
                                                date TEXT, data TEXT);
            CREATE INDEX IF NOT EXISTS mentions_text ON mentions(text_hash);
            CREATE INDEX IF NOT EXISTS mentions_date ON mentions(date);
        """)
        self.db.commit()

    @property
    def texts_dir(self):
        return self.dir / "texts"

    def _sidecar(self, text_hash):
        return self.texts_dir / (text_hash.removeprefix("sha256:") + ".json")

    def add_text(self, text_hash):
        found = dates.detect(self.m.objects.get_text(text_hash))
        self.db.execute("INSERT INTO texts(text_hash) VALUES(?)", (text_hash,))
        self.db.executemany("INSERT INTO mentions VALUES(?,?,?,?,?,?)",
                            [(text_hash, d["start"], d["end"], d["kind"], d.get("date"), json.dumps(d, ensure_ascii=False))
                             for d in found])
        self.texts_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._sidecar(text_hash).with_suffix(".tmp")
        tmp.write_text(json.dumps({"text": text_hash, "dates": found}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._sidecar(text_hash))

    def remove_text(self, text_hash):
        self.db.execute("DELETE FROM mentions WHERE text_hash=?", (text_hash,))
        self.db.execute("DELETE FROM texts WHERE text_hash=?", (text_hash,))
        self._sidecar(text_hash).unlink(missing_ok=True)

    def update(self, progress=None) -> int:
        n = super().update(progress)
        if n or not (self.dir / "threads.json").exists():
            self.write_threads()
        return n

    def mentions(self, text_hash: str) -> list[dict]:
        return [json.loads(d) for (d,) in self.db.execute(
            "SELECT data FROM mentions WHERE text_hash=? ORDER BY start", (text_hash,))]

    def write_threads(self):
        from ..analysis import threads
        of, messages = {}, {}
        try:
            for t in threads(self.m):
                first = t["messages"][0]["loc"].removeprefix("doc:")
                messages[first] = [msg["loc"].removeprefix("doc:") for msg in t["messages"]]
                for i, msg in enumerate(t["messages"]):
                    for loc in [msg["loc"], *msg["copies"]]:
                        of[loc.removeprefix("doc:")] = [first, i]
        except Exception:           # threads are a convenience on the card; dates work without them
            of, messages = {}, {}
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "threads.tmp"
        tmp.write_text(json.dumps({"of": of, "threads": messages}), encoding="utf-8")
        tmp.replace(self.dir / "threads.json")

    def verify(self, repair=False) -> dict:
        out = super().verify(repair)
        if repair:
            self.write_threads()
        return out
