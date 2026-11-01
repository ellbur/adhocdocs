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
from .signing import canonical, event_hash

CHUNK = 1 << 20
TAG = 16
LEN = 8                 # bytes of the true length, inside the padding


class NoKey(Exception):
    pass


# -- the matter's keys --------------------------------------------------------------------------
#
# Keys have epochs. The first is made when the pool is founded; removing a
# member makes the next (a ``rekey`` event in the pool log, the new key
# boxed for each remaining member's own key), so a removed installation,
# even with the hub's help, can't read what's sealed after. Older keys stay,
# to read what was sealed with them. They live in ``sync/matter.keys.json``.


def keys_path(m: Matter) -> Path:
    return m.sync_dir / "matter.keys.json"


def key_path(m: Matter) -> Path:          # before epochs: one key
    return m.sync_dir / "matter.key"


def load_keys(m: Matter) -> dict[int, bytes]:
    try:
        data = json.loads(keys_path(m).read_text(encoding="utf-8"))
        return {int(e): unb64(k) for e, k in data["keys"].items()}
    except FileNotFoundError:
        pass
    try:
        return {0: key_path(m).read_bytes()}
    except FileNotFoundError:
        return {}


def save_keys(m: Matter, keys: dict[int, bytes]):
    p = keys_path(m)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"keys": {str(e): b64(k) for e, k in sorted(keys.items())}}) + "\n")
    os.replace(p.with_suffix(".tmp"), p)


def make_key(m: Matter) -> bytes:
    """The first matter key (when a pool is founded). Host only."""
    k = secrets.token_bytes(32)
    save_keys(m, {0: k})
    return k


def save_key(m: Matter, k: bytes):
    save_keys(m, {**load_keys(m), 0: k})


class Keyring:
    """A matter's keys, by epoch: the latest seals, any opens."""

    def __init__(self, keys: dict[int, bytes]):
        if not keys or any(len(k) != 32 for k in keys.values()):
            raise NoKey("this installation doesn't have the matter's key (it comes with an invitation, or is made "
                        "when the pool is founded), so it can't use a sealed hub")
        self.keys = dict(keys)
        self.epoch = max(keys)

    @classmethod
    def of(cls, m: Matter) -> "Keyring":
        learn(m)
        return cls(load_keys(m))

    def key(self, epoch: int | None = None) -> bytes:
        e = self.epoch if epoch is None else epoch
        if e not in self.keys:
            raise NoKey(f"this installation doesn't have the matter's key from epoch {e}")
        return self.keys[e]


def _ring(k) -> Keyring:
    return k if isinstance(k, Keyring) else Keyring({0: k})


def key(m: Matter) -> bytes:
    """The current matter key."""
    return Keyring.of(m).key()


# boxes: the next key, for one member, sealed to its own X25519 key


def box_private(m: Matter):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    p = m.sync_dir / "keys" / f"{m.origin}.box"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            from cryptography.hazmat.primitives import serialization
            f.write(X25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw,
                                                               serialization.PrivateFormat.Raw,
                                                               serialization.NoEncryption()))
    return X25519PrivateKey.from_private_bytes(p.read_bytes())


def box_public(m: Matter) -> str:
    from cryptography.hazmat.primitives import serialization
    return box_private(m).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _box_key(shared: bytes, eph: bytes, to: bytes) -> bytes:
    return hashlib.sha256(b"adhoc-box-v1:" + shared + eph + to).digest()


def box(k: bytes, to_hex: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    to = bytes.fromhex(to_hex)
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(to))
    return b64(eph_pub + AESGCM(_box_key(shared, eph_pub, to)).encrypt(b"\0" * 12, k, b"adhoc-rekey"))


def unbox(m: Matter, sealed: str) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    raw = unb64(sealed)
    eph_pub, ct = raw[:32], raw[32:]
    priv = box_private(m)
    me = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    return AESGCM(_box_key(shared, eph_pub, me)).decrypt(b"\0" * 12, ct, b"adhoc-rekey")


def learn(m: Matter) -> dict[int, bytes]:
    """Take any new keys boxed for this installation in the pool log."""
    keys = load_keys(m)
    if not (m.sync_dir / "origin.json").exists():
        return keys
    me = m.origin
    from .pool import Pool
    try:
        p = Pool.load(m)
    except Exception:
        return keys
    changed = False
    for ev in m.pool_log.read():
        if ev.get("op") != "rekey" or not isinstance(ev.get("epoch"), int) or ev["epoch"] in keys:
            continue
        if not p.is_member(ev.get("origin")) and ev.get("origin") not in p.members:
            continue
        mine = (ev.get("boxes") or {}).get(me)
        if not mine:
            continue
        try:
            keys[ev["epoch"]] = unbox(m, mine)
            changed = True
        except Exception:
            continue
    if changed:
        save_keys(m, keys)
    return keys


def rekey(m: Matter, author: str) -> dict | None:
    """A new key for everyone still in the pool (after a removal): boxed for
    each active member with a box key, and for this installation. Hubs get
    none: they never hold a key."""
    from .pool import Pool
    keys = learn(m)
    if not keys:
        return None
    p = Pool.load(m)
    k = secrets.token_bytes(32)
    epoch = max(keys) + 1
    boxes = {x["origin"]: box(k, x["box"]) for x in p.active()
             if x.get("box") and x["role"] != "hub" and x["origin"] != m.origin}
    boxes[m.origin] = box(k, box_public(m))
    ev = m.pool_log.append(op="rekey", epoch=epoch, boxes=boxes, author=author)
    keys[epoch] = k
    save_keys(m, keys)
    return ev


def _sub(k: bytes, what: str) -> bytes:
    return hmac.new(k, b"adhoc-seal-v1:" + what.encode(), hashlib.sha256).digest()


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def padded(n: int) -> int:
    """How much a length becomes, padded: at least 4 KB, then within about
    6% of its size, so the hub can't recognize a known file by its exact
    size."""
    if n <= 4096:
        return 4096
    step = 1 << max(12, n.bit_length() - 4)
    return -(-n // step) * step


# -- events -----------------------------------------------------------------------------------


def seal_event(k, ev: dict, prev: dict | None) -> dict:
    """An event as a sealed hub holds it, sealed with the current key."""
    ring = _ring(k)
    k = ring.key()
    h = event_hash(ev)
    hp = event_hash(prev)
    plain = canonical(ev)
    plain += b" " * (-(len(plain)) % 256)            # sizes in steps, not exact
    nonce = _sub(_sub(k, "event-nonce"), h)[:12]
    ct = AESGCM(_sub(k, "events")).encrypt(nonce, plain, h.encode())
    return {"id": ev["id"], "h": h, "hp": hp, "sealed": b64(nonce + ct), "e": ring.epoch}


def unseal_event(k, env: dict) -> dict:
    """The event inside, checked against its hash. Raises ValueError."""
    k = _ring(k).key(int(env.get("e", 0)))
    raw = unb64(env["sealed"])
    ev = json.loads(AESGCM(_sub(k, "events")).decrypt(raw[:12], raw[12:], env["h"].encode()))
    if event_hash(ev) != env["h"] or ev.get("id") != env["id"]:
        raise ValueError(f"sealed event {env.get('id')} doesn't match its hash")
    return ev


def envelope_hash(env: dict) -> str | None:
    """What a sealed line stands for in the chain (the hub's view)."""
    return None if env is None else env.get("h")


# -- objects -------------------------------------------------------------------------------------


def blind(k, h: str, epoch: int | None = None) -> str:
    """The name an object has on a sealed hub (sealed with this epoch's key)."""
    k = _ring(k).key(epoch)
    return "sha256:" + hmac.new(_sub(k, "blind"), h.encode(), hashlib.sha256).hexdigest()


def _nonce(k: bytes, name: str, i: int) -> bytes:
    return _sub(_sub(k, "object-nonce"), f"{name}:{i}")[:12]


class _Padded:
    """An object's bytes as sealed: its length, the bytes, then zeros up to
    the padded size."""

    def __init__(self, f, size: int):
        self.f, self.left = f, padded(LEN + size) - LEN - size
        self.head = size.to_bytes(LEN, "big")

    def read(self, n):
        out = self.head[:n]
        self.head = self.head[len(out):]
        if len(out) < n:
            out += self.f.read(n - len(out))
        if len(out) < n and self.left:
            z = min(self.left, n - len(out))
            out += b"\0" * z
            self.left -= z
        return out


class Sealer:
    """Reads an object's bytes as its sealed form, chunk by chunk."""

    def __init__(self, k, h: str, f, size: int):
        k = _ring(k).key()
        self.aead, self.k, self.name, self.f = AESGCM(_sub(k, "objects")), k, blind(k, h), _Padded(f, size)
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
    total = padded(LEN + size)
    return total + (total // CHUNK + 1) * TAG


class Unsealer:
    """Reads a sealed object's plain bytes from a stream of its sealed form
    (sealed with the key of ``epoch``)."""

    def __init__(self, k, h: str, f, epoch: int | None = None):
        k = _ring(k).key(epoch)
        self.aead, self.k, self.name, self.f = AESGCM(_sub(k, "objects")), k, blind(k, h), f
        self.i, self.buf, self.done, self.pending = 0, b"", False, b""
        self.length = None

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
        if self.length is None and len(self.buf) >= LEN:
            self.length = int.from_bytes(self.buf[:LEN], "big")
            self.buf = self.buf[LEN:]

    def read(self, n=-1):
        while not self.done and (n < 0 or len(self.buf) < n or self.length is None):
            self._fill()
        if self.length is None:
            raise ValueError("a sealed object too short to hold its length")
        if n < 0:
            n = len(self.buf)
        n = min(n, self.length)
        out, self.buf = self.buf[:n], self.buf[n:]
        self.length -= len(out)
        return out
