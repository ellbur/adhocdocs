"""Search normalization. Layers stay literal; this is how search sees them.

A token is a run of letters and digits after compatibility folding
(ligatures, full-width forms), case folding and removing diacritics.
Everything else separates tokens, so curly and straight quotes, dashes and
line breaks are all alike. A hyphen at the end of a line between two
letters joins them (``ship-\\nment`` is the token ``shipment``). No stemming.

Each token keeps the layer range it came from, so a match maps straight
back to layer offsets.
"""

import unicodedata
from array import array
from functools import lru_cache

HYPHENS = "-­‐‑"
VERSION = 1  # part of an index's settings: changing the rules means a rebuild


@lru_cache(maxsize=4096)
def fold(c: str) -> str:
    out = []
    for d in unicodedata.normalize("NFKD", c):
        if unicodedata.category(d) == "Mn":
            continue
        out.append(d.casefold())
    return "".join(out)


def tokenize(text: str):
    """[(token, start, end)] with layer offsets, end exclusive."""
    tokens = []
    cur, start, end = [], 0, 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        f = fold(c)
        if f and all(ch.isalnum() for ch in f):
            if not cur:
                start = i
            cur.append(f)
            end = i + 1
            i += 1
            continue
        if cur and c in HYPHENS:
            # line-end hyphen between letters: skip it and the line break
            j = i + 1
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j] == "\n":
                j += 1
                while j < n and text[j] in " \t":
                    j += 1
                if j < n and text[j].isalpha() and text[i - 1].isalpha():
                    i = j
                    continue
        if cur:
            tokens.append(("".join(cur), start, end))
            cur = []
        i += 1
    if cur:
        tokens.append(("".join(cur), start, end))
    return tokens


def words(s: str) -> list[str]:
    return [t for t, _, _ in tokenize(s)]


def pack_offsets(tokens) -> bytes:
    a = array("I")
    for _, s, e in tokens:
        a.append(s)
        a.append(e)
    return a.tobytes()


def unpack_offsets(b: bytes) -> array:
    a = array("I")
    a.frombytes(b)
    return a
