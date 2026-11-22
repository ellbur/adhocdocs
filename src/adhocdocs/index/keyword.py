"""The keyword index: FTS5 over normalized tokens, one row per layer text.

Schema::

    texts(rowid, text_hash UNIQUE, tokens, offsets, low_conf)
        tokens   normalized tokens joined by spaces (see textnorm)
        offsets  uint32 pairs: each token's [start, end) in the layer text
    fts          FTS5 over texts.tokens (external content)
    layer_conf(layer PRIMARY KEY, mean_conf)   OCR confidence, for coverage

FTS5 only proposes candidate texts. Matches are confirmed, and positioned,
by scanning the token list, so FTS5's own tokenizer never decides a result.
"""

from .. import textnorm
from .base import TextIndex

LOW_CONFIDENCE = 0.6


class KeywordIndex(TextIndex):
    kind = "keyword"

    def settings(self):
        return {"normalization": textnorm.VERSION, "stemming": False}

    def init_db(self):
        super().init_db()
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(texts)")}
        if "tokens" not in cols:
            self.db.executescript("""
                ALTER TABLE texts ADD COLUMN tokens TEXT;
                ALTER TABLE texts ADD COLUMN offsets BLOB;
                CREATE VIRTUAL TABLE fts USING fts5(tokens, content='texts', content_rowid='rowid',
                                                    tokenize='unicode61 remove_diacritics 0');
                CREATE TABLE layer_conf(layer TEXT PRIMARY KEY, mean_conf REAL);
            """)
        self.db.commit()

    def apply(self, ev):
        super().apply(ev)
        if ev["op"] == "add-layer":
            self._record_conf(ev["layer"])

    def _record_conf(self, layer_id):
        obj = self.m.objects.get_object(layer_id)
        if not obj.get("geometry"):
            return
        confs = [g["conf"] for g in self.m.objects.get_jsonl(obj["geometry"]) if "conf" in g]
        if confs:
            self.db.execute("INSERT OR REPLACE INTO layer_conf VALUES(?,?)", (layer_id, sum(confs) / len(confs)))

    def add_text(self, text_hash):
        toks = textnorm.tokenize(self.m.objects.get_text(text_hash))
        tokens = " ".join(t for t, _, _ in toks)
        cur = self.db.execute("INSERT INTO texts(text_hash, tokens, offsets) VALUES(?,?,?)",
                              (text_hash, tokens, textnorm.pack_offsets(toks)))
        self.db.execute("INSERT INTO fts(rowid, tokens) VALUES(?,?)", (cur.lastrowid, tokens))

    def remove_text(self, text_hash):
        row = self.db.execute("SELECT rowid, tokens FROM texts WHERE text_hash=?", (text_hash,)).fetchone()
        if row:
            self.db.execute("INSERT INTO fts(fts, rowid, tokens) VALUES('delete', ?, ?)", row)
            self.db.execute("DELETE FROM texts WHERE rowid=?", (row[0],))

    # -- querying ------------------------------------------------------------------

    def candidates(self, fts_query: str) -> dict[str, float]:
        """Text hashes FTS5 proposes for a query, with bm25 scores (higher is better)."""
        rows = self.db.execute(
            "SELECT t.text_hash, bm25(fts) FROM fts JOIN texts t ON t.rowid = fts.rowid WHERE fts MATCH ?",
            (fts_query,))
        return {h: -score for h, score in rows}

    def tokens(self, text_hash: str):
        row = self.db.execute("SELECT tokens, offsets FROM texts WHERE text_hash=?", (text_hash,)).fetchone()
        if row is None:
            return None
        return row[0].split(" ") if row[0] else [], textnorm.unpack_offsets(row[1])

    def low_confidence_layers(self) -> set[str]:
        return {l for (l,) in self.db.execute("SELECT layer FROM layer_conf WHERE mean_conf < ?", (LOW_CONFIDENCE,))}

    def status(self):
        st = super().status()
        st["texts"] = self.db.execute("SELECT count(*) FROM texts").fetchone()[0]
        return st
