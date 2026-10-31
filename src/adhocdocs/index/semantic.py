"""The semantic index: passages embedded by a local model, searched by brute force.

Schema::

    texts(rowid, text_hash UNIQUE)
    passages(id PRIMARY KEY, text_hash, start, end)   id is the row in vectors.f16
    vectors.f16       float16 rows, one per passage id; rows of removed
                      passages are zeroed

The model runs in the container on the CPU. Document text never leaves it.
"""

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from .base import TextIndex

MODEL = os.environ.get("ADHOC_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
PASSAGE_WORDS = 200
OVERLAP_WORDS = 40
EMBED_BATCH = 8


def models_dir() -> Path:
    return Path(os.environ.get("ADHOC_MODELS", Path.home() / ".cache" / "adhocdocs" / "models"))


_model = None


def model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL, cache_dir=str(models_dir()))
    return _model


def model_hash() -> str:
    """SHA-256 of the model's weights. Cached by resolved path and size (the
    Hugging Face cache stores files by content), in the models directory when
    it is writable; the image's copy is read-only and hashed at build time."""
    model()
    files = sorted(models_dir().rglob("*.onnx"))
    if not files:
        return "unknown"
    f = files[0].resolve()
    key = f"{f}:{f.stat().st_size}"
    cache = models_dir() / "hashes.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    if key not in known:
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        known[key] = "sha256:" + h.hexdigest()
        try:
            cache.write_text(json.dumps(known))
        except OSError:
            pass
    return known[key]


def passages(text: str):
    """Split text into passages of about PASSAGE_WORDS words with overlap,
    breaking at line and page boundaries, never across a page."""
    out = []
    pos = 0
    for page in text.split("\f"):
        # words with their offsets, and where lines end
        words = []
        lpos = pos
        for line in page.split("\n"):
            i = 0
            for w in line.split():
                i = line.index(w, i)
                words.append((lpos + i, lpos + i + len(w), False))
                i += len(w)
            if words:
                words[-1] = (*words[-1][:2], True)
            lpos += len(line) + 1
        i = 0
        while i < len(words):
            j = min(len(words), i + PASSAGE_WORDS)
            if j < len(words):
                # end at the last line break in the second half, if there is one
                for k in range(j - 1, i + PASSAGE_WORDS // 2, -1):
                    if words[k][2]:
                        j = k + 1
                        break
            out.append((words[i][0], words[j - 1][1]))
            if j >= len(words):
                break
            i = max(i + 1, j - OVERLAP_WORDS)
        pos += len(page) + 1
    return out


class SemanticIndex(TextIndex):
    kind = "semantic"

    def settings(self):
        return {"model": MODEL, "model_hash": model_hash(), "passage_words": PASSAGE_WORDS,
                "overlap_words": OVERLAP_WORDS}

    def wants_view(self, view):
        return view not in ("markup", "original")

    @property
    def vectors_path(self):
        return self.dir / "vectors.f16"

    def init_db(self):
        super().init_db()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS passages(id INTEGER PRIMARY KEY, text_hash TEXT, start INTEGER, "end" INTEGER);
            CREATE INDEX IF NOT EXISTS passages_text ON passages(text_hash);
        """)
        self.db.commit()
        self.vectors_path.touch()

    def _dim(self):
        return len(next(iter(model().embed(["x"]))))

    def _next_id(self):
        """Rows past the last committed passage are left over from an
        interrupted update: drop them."""
        n = self.db.execute("SELECT coalesce(max(id) + 1, 0) FROM passages").fetchone()[0]
        size = n * self._dim() * 2
        if self.vectors_path.stat().st_size != size:
            os.truncate(self.vectors_path, size)
        return n

    def add_text(self, text_hash):
        text = self.m.objects.get_text(text_hash)
        spans = passages(text)
        self.db.execute("INSERT INTO texts(text_hash) VALUES(?)", (text_hash,))
        if not spans:
            return
        vecs = np.array(list(model().passage_embed([text[s:e] for s, e in spans], batch_size=EMBED_BATCH)))
        vecs = (vecs / np.linalg.norm(vecs, axis=1, keepdims=True)).astype(np.float16)
        first = self._next_id()
        with open(self.vectors_path, "ab") as f:
            f.write(vecs.tobytes())
        self.db.executemany("INSERT INTO passages VALUES(?,?,?,?)",
                            [(first + i, text_hash, s, e) for i, (s, e) in enumerate(spans)])

    def remove_text(self, text_hash):
        dim = self._dim()
        ids = [r[0] for r in self.db.execute("SELECT id FROM passages WHERE text_hash=?", (text_hash,))]
        with open(self.vectors_path, "r+b") as f:
            for i in ids:
                f.seek(i * dim * 2)
                f.write(b"\0" * dim * 2)
        self.db.execute("DELETE FROM passages WHERE text_hash=?", (text_hash,))
        self.db.execute("DELETE FROM texts WHERE text_hash=?", (text_hash,))

    def vectors(self) -> np.ndarray:
        dim = self._dim()
        n = self.db.execute("SELECT coalesce(max(id) + 1, 0) FROM passages").fetchone()[0]
        if n == 0:
            return np.zeros((0, dim), dtype=np.float16)
        return np.fromfile(self.vectors_path, dtype=np.float16, count=n * dim).reshape(n, dim)

    def embed_query(self, q: str) -> np.ndarray:
        v = np.array(next(iter(model().query_embed([q]))), dtype=np.float32)
        return v / np.linalg.norm(v)

    def embed_passage(self, text: str) -> np.ndarray:
        v = np.array(next(iter(model().passage_embed([text]))), dtype=np.float32)
        return v / np.linalg.norm(v)

    def nearest(self, qvec: np.ndarray, texts: set[str] | None = None):
        """(score, passage id, text_hash, start, end), best first, over
        passages of the given texts (all if None)."""
        vecs = self.vectors()
        if not len(vecs):
            return []
        scores = vecs.astype(np.float32) @ qvec
        rows = self.db.execute('SELECT id, text_hash, start, "end" FROM passages').fetchall()
        out = [(float(scores[i]), i, h, s, e) for i, h, s, e in rows if texts is None or h in texts]
        out.sort(key=lambda r: -r[0])
        return out

    def status(self):
        st = super().status()
        st["texts"] = self.db.execute("SELECT count(*) FROM texts").fetchone()[0]
        st["passages"] = self.db.execute("SELECT count(*) FROM passages").fetchone()[0]
        return st
