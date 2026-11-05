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
    rebuilds_itself = True

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
        if n:
            self.learn_orders()
        return n

    # how much a custodian's other texts must lean one way before their dates decide a text's
    LEAN, AT_LEAST = 3, 3

    def learn_orders(self):
        """For a text with ambiguous numeric dates (3/4/2026) and no date of
        its own that settles which way it writes them: read them as its
        custodians' other texts write dates, when those clearly lean one way
        (at least ``AT_LEAST`` dates, ``LEAN`` times as many as the other
        way). Done again after every update, so a reading follows the
        evidence, and undone when the evidence goes."""
        from collections import defaultdict
        state = self.m.state
        cust_of = defaultdict(set)
        for d in state.live_documents():
            c = (self.m.objects.get_object(d) or {}).get("custodian")
            for layer in state.layers.get(state.documents[d], []):
                if c:
                    cust_of[layer.text].add(c)
        own = defaultdict(lambda: [0, 0])          # text -> [day first, month first], from its unambiguous dates
        ambiguous = set()
        for th, data in self.db.execute("SELECT text_hash, data FROM mentions WHERE kind='absolute'"):
            x = json.loads(data)
            if x.get("order") == "day first":
                own[th][0] += 1
            elif x.get("order") == "month first":
                own[th][1] += 1
            if x.get("ambiguous"):
                ambiguous.add(th)
        by_cust = defaultdict(lambda: [0, 0])
        for th, (day, month) in own.items():
            for c in cust_of.get(th, ()):
                by_cust[c][0] += day
                by_cust[c][1] += month
        for th in ambiguous:
            if own[th][0] != own[th][1]:
                continue                            # its own dates settle it (dates.detect)
            day = sum(by_cust[c][0] for c in cust_of.get(th, ())) - own[th][0]
            month = sum(by_cust[c][1] for c in cust_of.get(th, ())) - own[th][1]
            order = basis = None
            for o, a, b in (("day first", day, month), ("month first", month, day)):
                if a >= self.AT_LEAST and a >= self.LEAN * max(b, 1):
                    who = ", ".join(sorted(c.removeprefix("cust:") for c in cust_of[th]))
                    order, basis = o, f"{who}'s other documents have {a} dates that can only be read {o}" + \
                        (f" (and {b} the other way)" if b else "")
            found = self.mentions(th)
            if dates.read_ambiguous(found, order, basis) or any(
                    x.get("ambiguous") and x.get("basis") != basis for x in found):
                for x in found:
                    if x.get("ambiguous"):
                        x["basis"] = basis
                        if basis is None:
                            x.pop("basis", None)
                self._rewrite(th, found)
        self.db.commit()

    def _rewrite(self, text_hash, found):
        self.db.execute("DELETE FROM mentions WHERE text_hash=?", (text_hash,))
        self.db.executemany("INSERT INTO mentions VALUES(?,?,?,?,?,?)",
                            [(text_hash, d["start"], d["end"], d["kind"], d.get("date"), json.dumps(d, ensure_ascii=False))
                             for d in found])
        tmp = self._sidecar(text_hash).with_suffix(".tmp")
        tmp.write_text(json.dumps({"text": text_hash, "dates": found}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._sidecar(text_hash))

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
