"""Sealing: what a hub holds, it can't read (SYNC.md).

A matter has a **key**, made when its pool is founded, kept in each
installation's ``sync/matter.key`` (host only, never mounted, never in a
log) and carried to new members inside their invitation code. A hub never
gets it. With a *sealed* hub (the default), members send it:

- **events** sealed: ``{"id", "h", "hp", "sealed"}``, the event encrypted
  (AES-GCM) with its ID, its hash ``h`` and the previous event's hash ``hp``
  in the clear. That's enough for the hub to keep each log in order (``hp``
  must be the previous line's ``h``), to compare log ends, and to redact by
  hash, and nothing more. Members decrypt what they fetch, and check each
  event's signature and chain as always. Membership (the pool feed) isn't
  sealed: the hub needs it to know who may connect;
- **objects** encrypted in 1 MB chunks, under a *blinded* name (an HMAC of
  the object's hash), so the hub can't tell which documents it has by
  hashing known files. Members check the real hash after decrypting.

What the hub can see: how many events each installation wrote and when
(IDs are timestamps), and how many objects of what sizes. What it can't:
anything they say or hold.

What this costs: the hub can't check that an event it holds was written by
the installation whose log it's in, since the signature is inside. A
member could put junk in another's log there; the others would refuse that
log when they read it (``refused``), so it disrupts, but deceives no one.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .matter import Matter
from .signing import canonical, event_hash, is_tombstone

CHUNK = 1 << 20
TAG = 16


class NoKey(Exception):
    pass


def key_path(m: Matter) -> Path:
    return m.sync_dir / "matter.key"


def make_key(m: Matter) -> bytes:
    """A new matter key (when a pool is founded). Host only."""
    k = secrets.token_bytes(32)
    save_key(m, k)
    return k


def save_key(m: Matter, k: bytes):
    p = key_path(m)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(k)


def key(m: Matter) -> bytes:
    try:
        k = key_path(m).read_bytes()
    except FileNotFoundError:
        raise NoKey("this installation doesn't have the matter's key (it comes with an invitation, or is made "
                    "when the pool is founded), so it can't use a sealed hub")
    if len(k) != 32:
        raise NoKey("the matter key here is damaged")
    return k


def _sub(k: bytes, what: str) -> bytes:
    return hmac.new(k, b"adhoc-seal-v1:" + what.encode(), hashlib.sha256).digest()


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# -- events -----------------------------------------------------------------------------------


def seal_event(k: bytes, ev: dict, prev: dict | None) -> dict:
    """An event as a sealed hub holds it. Tombstones have nothing to hide."""
    h = event_hash(ev)
    hp = event_hash(prev)
    if is_tombstone(ev):
        return {**{x: ev[x] for x in ("id", "op", "redacted", "purge") if x in ev}, "hp": hp}
    nonce = _sub(_sub(k, "event-nonce"), h)[:12]
    ct = AESGCM(_sub(k, "events")).encrypt(nonce, canonical(ev), h.encode())
    return {"id": ev["id"], "h": h, "hp": hp, "sealed": b64(nonce + ct)}


def unseal_event(k: bytes, env: dict) -> dict:
    """The event inside, checked against its hash. Raises ValueError."""
    if is_tombstone(env):
        return {x: env[x] for x in ("id", "op", "redacted", "purge") if x in env}
    raw = unb64(env["sealed"])
    ev = json.loads(AESGCM(_sub(k, "events")).decrypt(raw[:12], raw[12:], env["h"].encode()))
    if event_hash(ev) != env["h"] or ev.get("id") != env["id"]:
        raise ValueError(f"sealed event {env.get('id')} doesn't match its hash")
    return ev


def envelope_hash(env: dict) -> str | None:
    """What a sealed line stands for in the chain (the hub's view)."""
    if env is None:
        return None
    return env["redacted"] if is_tombstone(env) else env.get("h")


# -- objects -------------------------------------------------------------------------------------


def blind(k: bytes, h: str) -> str:
    """The name an object has on a sealed hub."""
    return "sha256:" + hmac.new(_sub(k, "blind"), h.encode(), hashlib.sha256).hexdigest()


def _nonce(k: bytes, name: str, i: int) -> bytes:
    return _sub(_sub(k, "object-nonce"), f"{name}:{i}")[:12]


class Sealer:
    """Reads an object's bytes as its sealed form, chunk by chunk."""

    def __init__(self, k: bytes, h: str, f):
        self.aead, self.k, self.name, self.f = AESGCM(_sub(k, "objects")), k, blind(k, h), f
        self.i, self.buf, self.done = 0, b"", False

    def read(self, n=-1):
        while not self.done and (n < 0 or len(self.buf) < n):
            plain = self.f.read(CHUNK)
            last = len(plain) < CHUNK
            aad = f"{self.name}:{self.i}:{int(last)}".encode()
            self.buf += self.aead.encrypt(_nonce(self.k, self.name, self.i), plain, aad)
            self.i += 1
            self.done = last
        if n < 0:
            out, self.buf = self.buf, b""
        else:
            out, self.buf = self.buf[:n], self.buf[n:]
        return out


def sealed_size(size: int) -> int:
    chunks = size // CHUNK + 1
    return size + chunks * TAG


class Unsealer:
    """Reads a sealed object's plain bytes from a stream of its sealed form."""

    def __init__(self, k: bytes, h: str, f):
        self.aead, self.k, self.name, self.f = AESGCM(_sub(k, "objects")), k, blind(k, h), f
        self.i, self.buf, self.done, self.pending = 0, b"", False, b""

    def _fill(self):
        want = CHUNK + TAG
        while len(self.pending) < want + 1:
            more = self.f.read(want + 1 - len(self.pending))
            if not more:
                break
            self.pending += more
        last = len(self.pending) <= want
        chunk, self.pending = self.pending[:want], self.pending[want:]
        aad = f"{self.name}:{self.i}:{int(last)}".encode()
        self.buf += self.aead.decrypt(_nonce(self.k, self.name, self.i), chunk, aad)
        self.i += 1
        self.done = last

    def read(self, n=-1):
        while not self.done and (n < 0 or len(self.buf) < n):
            self._fill()
        if n < 0:
            out, self.buf = self.buf, b""
        else:
            out, self.buf = self.buf[:n], self.buf[n:]
        return out
