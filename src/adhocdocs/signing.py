"""Signed, chained logs: what lets any installation pass on any other's
events, and the receiver check them (SYNC.md).

Every event an installation appends to a synced feed carries:

- ``prev``: the hash of the log's previous event (``null`` for the first), so
  a log is a hash chain and can't be reordered, trimmed in the middle or
  forked without it showing;
- ``sig``: the origin's Ed25519 signature over the event without ``sig``.

**What's signed is a tree of hashes** (:func:`digest`): each value hashed,
each object or list hashed from its parts' hashes. So a part of an event
can be *hidden*, replaced by ``{"#": <its hash>}``, and the signature, the
event's hash, and so its place in its log's chain, all still check. That's
how a purge removes the words a tag or note held without breaking the
logs, or anyone being able to remove anything else (adhocdocs.purge
decides what may be hidden; :func:`hide`).

Hashes are over canonical JSON: sorted keys, no whitespace, literal UTF-8,
without what a feed adds as it reads (``feed``, ``log``, ``origin``,
``stale``). An origin's ID is the hash of its public key
(:mod:`adhocdocs.origin`), so the key that checks its events is the one
named by its ID.
"""

import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

READ_TAGS = ("feed", "log", "origin", "stale")
HIDDEN = "#"


class BadLog(Exception):
    """Events that don't check: a bad signature, a broken chain, or a log
    that forked (two different events at one place in an origin's log)."""


def bare(ev: dict) -> dict:
    return {k: v for k, v in ev.items() if k not in READ_TAGS}


def canonical(ev) -> bytes:
    return json.dumps(bare(ev) if isinstance(ev, dict) else ev, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def _json(v) -> bytes:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def is_hidden(v) -> bool:
    return isinstance(v, dict) and len(v) == 1 and isinstance(v.get(HIDDEN), str)


def digest(v) -> str:
    """The hash of a value as a tree: a hidden part stands for itself."""
    if is_hidden(v):
        return v[HIDDEN]
    if isinstance(v, dict):
        return hashlib.sha256(b"o" + _json({k: digest(x) for k, x in v.items()})).hexdigest()
    if isinstance(v, list):
        return hashlib.sha256(b"a" + _json([digest(x) for x in v])).hexdigest()
    return hashlib.sha256(b"v" + _json(v)).hexdigest()


def hide(v) -> dict:
    """A part of an event, hidden: only its hash is left."""
    return {HIDDEN: digest(v)}


def has_hidden(v) -> bool:
    if is_hidden(v):
        return True
    if isinstance(v, dict):
        return any(has_hidden(x) for x in v.values())
    if isinstance(v, list):
        return any(has_hidden(x) for x in v)
    return False


def _contains_marker(v) -> bool:
    if isinstance(v, dict):
        return HIDDEN in v or any(_contains_marker(x) for x in v.values())
    if isinstance(v, list):
        return any(_contains_marker(x) for x in v)
    return False


def event_hash(ev: dict | None) -> str | None:
    """An event's hash, which the next event in its log names: the same with
    parts of it hidden."""
    if ev is None:
        return None
    return "sha256:" + digest(bare(ev))


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _signed(ev: dict) -> bytes:
    return b"adhoc-event-v2:" + digest({k: v for k, v in bare(ev).items() if k != "sig"}).encode()


def sign(ev: dict, key: Ed25519PrivateKey, prev: dict | None) -> dict:
    """``ev`` with ``prev`` (the hash of ``prev``, the log's last event) and
    ``sig`` set."""
    body = {k: v for k, v in bare(ev).items() if k != "sig"}
    if _contains_marker(body):
        raise ValueError(f"an event can't hold a {HIDDEN!r} key")
    body["prev"] = event_hash(prev)
    return {**body, "sig": b64(key.sign(_signed(body)))}


def verify(ev: dict, public: Ed25519PublicKey) -> bool:
    sig = ev.get("sig")
    if not isinstance(sig, str):
        return False
    try:
        public.verify(unb64(sig), _signed(ev))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def check_chain(last: dict | None, events: list[dict], public: Ed25519PublicKey, where: str = "log",
                may_hide=None):
    """Check events that would follow ``last`` in an origin's log: each signed
    by the origin, each naming the one before. An event with hidden parts is
    accepted only if ``may_hide(event)`` says those are parts a purge hides
    (adhocdocs.purge.may_hide); its signature still checks. Raises BadLog."""
    prev = last
    for i, ev in enumerate(events):
        if not verify(ev, public):
            raise BadLog(f"{where}: event {ev.get('id')} isn't signed by its origin")
        if ev.get("prev") != event_hash(prev):
            raise BadLog(f"{where}: event {ev.get('id')} doesn't follow the one before it "
                         f"({'the log has forked' if i == 0 and last is not None else 'a broken chain'})")
        if has_hidden(ev) and not (may_hide and may_hide(ev)):
            raise BadLog(f"{where}: event {ev.get('id')} has parts hidden that no purge here hides")
        prev = ev


def public_key(raw: bytes) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(raw)
