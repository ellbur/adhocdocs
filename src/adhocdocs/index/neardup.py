"""The near-duplicate index: a MinHash signature of each layer text's word
5-shingles (after search normalization). Two texts' signatures agree in
about the same fraction of places as their shingle sets overlap (Jaccard
similarity), so comparing signatures finds versions and near copies
without comparing texts.

Schema::

    sigs(text_hash PRIMARY KEY, sig BLOB, shingles INTEGER)
        sig: PERMUTATIONS uint32 values
"""

import zlib

import numpy as np

from .. import textnorm
from .base import TextIndex

SHINGLE = 5
PERMUTATIONS = 128
_rng = np.random.default_rng(20261031)        # fixed: signatures must be comparable forever
_A = _rng.integers(1, 2**63, PERMUTATIONS, dtype=np.uint64) | np.uint64(1)
_B = _rng.integers(0, 2**63, PERMUTATIONS, dtype=np.uint64)


def signature(text: str) -> tuple[np.ndarray, int]:
    words = textnorm.words(text)
    if len(words) < SHINGLE:
        grams = [" ".join(words)] if words else []
    else:
        grams = [" ".join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)]
    if not grams:
        return np.full(PERMUTATIONS, np.iinfo(np.uint32).max, dtype=np.uint32), 0
    x = np.unique(np.fromiter((zlib.crc32(g.encode()) for g in grams), dtype=np.uint64, count=len(grams)))
    with np.errstate(over="ignore"):
        h = (x[:, None] * _A[None, :] + _B[None, :]) >> np.uint64(32)
    return h.min(axis=0).astype(np.uint32), len(x)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(a == b))


class NearDupIndex(TextIndex):
    kind = "neardup"

    def settings(self):
        return {"shingle": SHINGLE, "permutations": PERMUTATIONS, "normalization": textnorm.VERSION, "seed": 20261031}

    def wants_view(self, view):
        return view not in ("markup", "original")

    def init_db(self):
        super().init_db()
        self.db.executescript("CREATE TABLE IF NOT EXISTS sigs(text_hash TEXT PRIMARY KEY, sig BLOB, shingles INTEGER);")
        self.db.commit()

    def add_text(self, text_hash):
        sig, n = signature(self.m.objects.get_text(text_hash))
        self.db.execute("INSERT INTO texts(text_hash) VALUES(?)", (text_hash,))
        self.db.execute("INSERT INTO sigs VALUES(?,?,?)", (text_hash, sig.tobytes(), n))

    def remove_text(self, text_hash):
        self.db.execute("DELETE FROM sigs WHERE text_hash=?", (text_hash,))
        self.db.execute("DELETE FROM texts WHERE text_hash=?", (text_hash,))

    def signatures(self) -> dict[str, tuple[np.ndarray, int]]:
        return {h: (np.frombuffer(b, dtype=np.uint32), n) for h, b, n in self.db.execute("SELECT * FROM sigs")}
