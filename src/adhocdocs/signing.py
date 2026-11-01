"""Signed, chained logs: what lets any installation pass on any other's
events, and the receiver check them (SYNC.md).

Every event an installation appends to a synced feed carries:

- ``prev``: the hash of the log's previous event (``null`` for the first), so
  a log is a hash chain and can't be reordered, trimmed in the middle or
  forked without it showing;
- ``sig``: the origin's Ed25519 signature over the event without ``sig``.

Both are over the event's canonical form: sorted keys, no whitespace,
literal UTF-8, and without what a feed adds as it reads (``feed``, ``log``,
``origin``, ``stale``). An origin's ID is the hash of its public key
(:mod:`adhocdocs.origin`), so the key that checks its events is the one
named by its ID.
"""

import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

READ_TAGS = ("feed", "log", "origin", "stale")


class BadLog(Exception):
    """Events that don't check: a bad signature, a broken chain, or a log
    that forked (two different events at one place in an origin's log)."""


def bare(ev: dict) -> dict:
    return {k: v for k, v in ev.items() if k not in READ_TAGS}


def canonical(ev: dict) -> bytes:
    return json.dumps(bare(ev), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def event_hash(ev: dict | None) -> str | None:
    """An event's hash, which the next event in its log names. A tombstone
    (an event redacted by a purge: adhocdocs.purge) keeps the hash of the
    event it replaced, so the chain still holds."""
    if ev is None:
        return None
    if is_tombstone(ev):
        return ev["redacted"]
    return "sha256:" + hashlib.sha256(canonical(ev)).hexdigest()


def is_tombstone(ev: dict) -> bool:
    return ev.get("op") == "redacted" and isinstance(ev.get("redacted"), str)


def tombstone(ev: dict, purge: str) -> dict:
    """What a redacted event becomes: its ID (for cursors), its hash (for the
    chain), and the purge that redacted it. Nothing it said."""
    return {"id": ev["id"], "op": "redacted", "redacted": event_hash(ev), "purge": purge}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(ev: dict, key: Ed25519PrivateKey, prev: dict | None) -> dict:
    """``ev`` with ``prev`` (the hash of ``prev``, the log's last event) and
    ``sig`` set."""
    body = {k: v for k, v in bare(ev).items() if k != "sig"}
    body["prev"] = event_hash(prev)
    return {**body, "sig": b64(key.sign(canonical(body)))}


def verify(ev: dict, public: Ed25519PublicKey) -> bool:
    sig = ev.get("sig")
    if not isinstance(sig, str):
        return False
    body = {k: v for k, v in bare(ev).items() if k != "sig"}
    try:
        public.verify(unb64(sig), canonical(body))
        return True
    except (InvalidSignature, ValueError):
        return False


def check_chain(last: dict | None, events: list[dict], public: Ed25519PublicKey, where: str = "log",
                redacted=frozenset()):
    """Check events that would follow ``last`` in an origin's log: each signed
    by the origin, each naming the one before. A tombstone stands in for an
    event a purge redacted, if ``redacted`` (the hashes purges authorize)
    has its hash; its signature went with what it said, and the next event
    still names its hash. Raises BadLog."""
    prev = last
    for i, ev in enumerate(events):
        if is_tombstone(ev):
            if ev["redacted"] not in redacted:
                raise BadLog(f"{where}: event {ev.get('id')} is redacted, but no purge here says so")
            prev = ev
            continue
        if not verify(ev, public):
            raise BadLog(f"{where}: event {ev.get('id')} isn't signed by its origin")
        if ev.get("prev") != event_hash(prev):
            raise BadLog(f"{where}: event {ev.get('id')} doesn't follow the one before it "
                         f"({'the log has forked' if i == 0 and last is not None else 'a broken chain'})")
        prev = ev


def public_key(raw: bytes) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(raw)
