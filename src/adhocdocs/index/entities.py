"""The entities index: detected entities in each layer text
(:mod:`adhocdocs.detect`), the entity layer's suggestions.

Besides its table, it keeps plain files for the viewer, which reads them
without Python (the viewer must answer within milliseconds of a keypress):

- ``texts/<hash>.json``: ``{"text": hash, "detections": [...]}``, one per text;
- ``candidates.json``: ``{"candidates": {key: {kind, name, docs, texts, ...}}}``,
  each detected candidate across the matter, rewritten after every update.

Schema::

    detections(text_hash, start, end, kind, key, data)    data: the detection as JSON
"""

import json
from collections import defaultdict

from .. import detect
from .base import TextIndex


class EntitiesIndex(TextIndex):
    kind = "entities"

    def settings(self):
        return {"rules": detect.VERSION}

    def wants_view(self, view):
        return view not in ("markup", "original")

    def init_db(self):
        super().init_db()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS detections(text_hash TEXT NOT NULL, start INTEGER, end INTEGER, kind TEXT,
                                                  key TEXT, data TEXT);
            CREATE INDEX IF NOT EXISTS detections_text ON detections(text_hash);
            CREATE INDEX IF NOT EXISTS detections_key ON detections(key);
        """)
        self.db.commit()

    @property
    def texts_dir(self):
        return self.dir / "texts"

    def _sidecar(self, text_hash):
        return self.texts_dir / (text_hash.removeprefix("sha256:") + ".json")

    def add_text(self, text_hash):
        found = [d.as_dict() for d in detect.detect(self.m.objects.get_text(text_hash))]
        self.db.execute("INSERT INTO texts(text_hash) VALUES(?)", (text_hash,))
        self.db.executemany("INSERT INTO detections VALUES(?,?,?,?,?,?)",
                            [(text_hash, d["start"], d["end"], d["kind"], d["key"], json.dumps(d, ensure_ascii=False))
                             for d in found])
        self.texts_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._sidecar(text_hash).with_suffix(".tmp")
        tmp.write_text(json.dumps({"text": text_hash, "detections": found}, ensure_ascii=False))
        tmp.replace(self._sidecar(text_hash))

    def remove_text(self, text_hash):
        self.db.execute("DELETE FROM detections WHERE text_hash=?", (text_hash,))
        self.db.execute("DELETE FROM texts WHERE text_hash=?", (text_hash,))
        self._sidecar(text_hash).unlink(missing_ok=True)

    def update(self, progress=None) -> int:
        n = super().update(progress)
        if n or not (self.dir / "candidates.json").exists():
            self.write_candidates()
        return n

    def detections(self, texts=None) -> list[dict]:
        rows = self.db.execute("SELECT text_hash, data FROM detections ORDER BY text_hash, start")
        return [{"text_hash": t, **json.loads(d)} for t, d in rows if texts is None or t in texts]

    def candidates(self) -> dict:
        """Each candidate key with its kind, the forms it was written in, and how
        many live documents and texts have it."""
        state = self.m.state
        docs_of = defaultdict(set)
        for d in state.live_documents():
            for layer in state.layers.get(state.documents[d], []):
                docs_of[layer.text].add(d)
        by_key = {}
        for t, data in self.db.execute("SELECT text_hash, data FROM detections"):
            d = json.loads(data)
            c = by_key.setdefault(d["key"], {"kind": d["kind"], "forms": defaultdict(int), "texts": set(), "docs": set(),
                                             "basis": defaultdict(int), "roles": defaultdict(int), "labels": defaultdict(int)})
            c["forms"][d["form"]] += 1
            c["texts"].add(t)
            c["docs"] |= docs_of.get(t, set())
            for b in d.get("basis", []):
                c["basis"][b] += 1
            if d.get("role"):
                c["roles"][d["role"]] += 1
            if d.get("label"):
                c["labels"][d["label"]] += 1
            if d.get("means") and "means" not in c:
                c["means"] = d["means"]
        out = {}
        for key, c in by_key.items():
            if not c["docs"]:
                continue
            top = lambda x: sorted(x, key=lambda k: -x[k])
            forms = top(c["forms"])
            out[key] = {"kind": c["kind"], "name": next((f for f in forms if not f.isupper()), forms[0]), "forms": forms[:6],
                        "docs": len(c["docs"]), "texts": len(c["texts"]), "mentions": sum(c["forms"].values()),
                        "basis": dict(sorted(c["basis"].items(), key=lambda kv: -kv[1])),
                        **({"roles": top(c["roles"])[:3]} if c["roles"] else {}),
                        **({"labels": top(c["labels"])[:3]} if c["labels"] else {}),
                        **({"means": c["means"]} if c.get("means") else {})}
        return out

    def write_candidates(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "candidates.tmp"
        tmp.write_text(json.dumps({"candidates": self.candidates()}, ensure_ascii=False))
        tmp.replace(self.dir / "candidates.json")

    def verify(self, repair=False) -> dict:
        out = super().verify(repair)
        if repair:
            self.write_candidates()
        return out
