"""Hashed objects and the object store.

Objects (documents, layers, metadata) are serialized with the JSON
Canonicalization Scheme (RFC 8785) and identified by the SHA-256 of that
serialization. Every object is stored in a git-like layout,
``objects/<2 hex>/<62 hex>``, next to the content and part files it names.
"""

import hashlib
import json
import os
from pathlib import Path

HASH_PREFIX = "sha256:"


def canonical(obj) -> bytes:
    """RFC 8785 serialization.

    Our objects hold only strings, integers, booleans, null, lists and
    objects, for which sorted keys, no whitespace and literal UTF-8 are
    exactly JCS. Floats are refused rather than serialized approximately.
    """
    def check(o):
        if isinstance(o, float):
            raise TypeError("floats are not allowed in hashed objects")
        if isinstance(o, dict):
            for v in o.values():
                check(v)
        elif isinstance(o, list):
            for v in o:
                check(v)
    check(obj)
    # JCS sorts keys by UTF-16 code units; for the ASCII keys we use this is
    # the same as Python's sort.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(data: bytes) -> str:
    return HASH_PREFIX + hashlib.sha256(data).hexdigest()


def object_id(obj) -> str:
    return sha256(canonical(obj))


def short(h: str, n: int = 12) -> str:
    return h.removeprefix(HASH_PREFIX)[:n]


class ObjectStore:
    """A content-addressed store. Several directories can be stacked: writes
    go to the first, reads search them in order (the matter store, read-only
    in the container, plus the workspace's own store for derived layers)."""

    def __init__(self, *roots: Path):
        self.roots = [Path(r) for r in roots]

    def _path(self, root: Path, h: str) -> Path:
        hexd = h.removeprefix(HASH_PREFIX)
        return root / hexd[:2] / hexd[2:]

    def path(self, h: str) -> Path | None:
        for root in self.roots:
            p = self._path(root, h)
            if p.exists():
                return p
        return None

    def has(self, h: str) -> bool:
        return self.path(h) is not None

    def put_bytes(self, data: bytes) -> str:
        h = sha256(data)
        if not self.has(h):
            p = self._path(self.roots[0], h)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return h

    def put_text(self, text: str) -> str:
        return self.put_bytes(text.encode())

    def put_jsonl(self, rows) -> str:
        return self.put_bytes("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode())

    def put_object(self, obj) -> str:
        return self.put_bytes(canonical(obj))

    def get_bytes(self, h: str) -> bytes:
        p = self.path(h)
        if p is None:
            raise KeyError(h)
        return p.read_bytes()

    def get_text(self, h: str) -> str:
        return self.get_bytes(h).decode()

    def get_object(self, h: str) -> dict:
        return json.loads(self.get_bytes(h))

    def get_jsonl(self, h: str | None) -> list[dict]:
        if not h:
            return []
        return [json.loads(line) for line in self.get_text(h).splitlines() if line]

    def expand(self, prefix: str) -> list[str]:
        """Full hashes matching a prefix of at least 8 hex digits."""
        hexd = prefix.removeprefix(HASH_PREFIX).lower()
        if len(hexd) < 8:
            raise ValueError(f"hash prefix too short: {prefix}")
        found = set()
        for root in self.roots:
            d = root / hexd[:2]
            if d.is_dir():
                for f in d.iterdir():
                    if f.name.startswith(hexd[2:]) and not f.name.endswith(".tmp"):
                        found.add(HASH_PREFIX + hexd[:2] + f.name)
        return sorted(found)
