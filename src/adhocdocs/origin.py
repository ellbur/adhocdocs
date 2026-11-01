"""This installation's identity in a matter: its origin and signing key.

An **origin** is one installation of one matter. It is the only writer of
its own logs (``store/logs/<origin>.jsonl``, ``annotations/logs/<origin>.jsonl``),
so logs from different installations never conflict as files (SYNC.md).

Kept in ``<matter>/sync/``, on the host only and never mounted into a session::

    sync/origin.json            {"origin", "host", "path", "created"}
    sync/keys/<origin>.pem      the origin's Ed25519 private key

An origin's ID is derived from its public key: ``o_`` plus the first 26
Crockford base32 digits of the SHA-256 of the raw key. So anyone who has the
key can check the ID, and events an origin signs can be checked by anyone
who has its ID and key, however they arrived.

**Copies get their own origin.** ``origin.json`` records the host and path
it was made for. A matter copied to another place (a backup restored, a
directory copied to another machine) would otherwise have two writers
appending to one log, which forks it. So when the host or path doesn't
match, a new origin is made. Moving a matter costs only a new log file.
Earlier origins' keys stay in ``sync/keys/``.
"""

import hashlib
import json
import os
import socket
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .portable import locked

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def b32(data: bytes, n: int) -> str:
    """The first ``n`` Crockford base32 digits of ``data``."""
    v = int.from_bytes(data, "big")
    bits = len(data) * 8
    return "".join(_CROCKFORD[(v >> (bits - 5 * (i + 1))) & 31] for i in range(n))


def raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def origin_id(public: bytes) -> str:
    return "o_" + b32(hashlib.sha256(public).digest(), 26)


def is_origin(s: str) -> bool:
    return isinstance(s, str) and len(s) == 28 and s.startswith("o_") and all(c in _CROCKFORD for c in s[2:])


def _place() -> dict:
    return {"host": socket.gethostname()}


class Identity:
    """The origin this installation writes as, made on first use."""

    def __init__(self, sync_dir: Path, matter_root: Path):
        self.dir = Path(sync_dir)
        self.matter_root = Path(matter_root).resolve()
        self._origin = None
        self._key = None

    @property
    def origin(self) -> str:
        if self._origin is None:
            self._load()
        return self._origin

    @property
    def key(self) -> Ed25519PrivateKey:
        if self._key is None:
            self._load_key()
        return self._key

    def public(self) -> bytes:
        return raw_public(self.key.public_key())

    def cert_path(self) -> Path:
        """This origin's TLS certificate (made on first use)."""
        p = self.dir / "keys" / f"{self.origin}.crt"
        if not p.exists():
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(make_cert(self.key, self.origin))
            tmp.replace(p)
        return p

    def key_path(self) -> Path:
        self.origin
        return self.dir / "keys" / f"{self.origin}.pem"

    def _here(self) -> dict:
        return {**_place(), "path": str(self.matter_root)}

    def _load(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / "origin.json"
        with open(self.dir / ".lock", "w", encoding="utf-8", newline="\n") as lf, locked(lf):
            rec = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            here = self._here()
            if rec is None or any(rec.get(k) != v for k, v in here.items()):
                key = Ed25519PrivateKey.generate()
                origin = origin_id(raw_public(key.public_key()))
                keys = self.dir / "keys"
                keys.mkdir(exist_ok=True)
                pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
                kp = keys / f"{origin}.pem"
                fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(pem)
                from .matter import now
                rec = {"origin": origin, **here, "created": now(),
                       **({"previous": rec["origin"]} if rec else {})}
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8", newline="\n")
                tmp.replace(path)
            self._origin = rec["origin"]

    def _load_key(self):
        with open(self.dir / "keys" / f"{self.origin}.pem", "rb") as f:
            self._key = serialization.load_pem_private_key(f.read(), password=None)


def make_cert(key: Ed25519PrivateKey, origin: str) -> bytes:
    """A self-signed certificate for an origin's key, for TLS. Peers don't
    check it against any authority: they check that its key hashes to the
    origin they meant to reach (:func:`cert_origin`)."""
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, origin)])
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(int.from_bytes(raw_public(key.public_key())[:16], "big") >> 1)
            .not_valid_before(start).not_valid_after(start.replace(year=2126)).sign(key, None))
    return cert.public_bytes(serialization.Encoding.PEM)


def cert_origin(cert: bytes) -> tuple[str, bytes]:
    """The origin a certificate (PEM or DER) is for, and its raw public key."""
    from cryptography import x509
    c = x509.load_pem_x509_certificate(cert) if cert.lstrip().startswith(b"-----") else x509.load_der_x509_certificate(cert)
    pub = c.public_key()
    if not isinstance(pub, Ed25519PublicKey):
        raise ValueError("not an Ed25519 certificate")
    raw = raw_public(pub)
    return origin_id(raw), raw
