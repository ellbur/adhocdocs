"""Invitations: how an installation joins a matter's pool (SYNC.md).

``adhoc sync invite`` (on a member) makes a code: the matter's ID, its
founder, the origin to join through, where it listens, the role offered,
and a one-time secret. When the pool has a hub, the invitation is left
there (only the secret's hash) and the code names the hub, so it works
while the inviting laptop is closed. The code is the whole of what the new installation needs,
and it should travel like a password. ``adhoc sync join <code> <dir>``
makes an empty matter in ``<dir>``, connects to the inviter (checking it's
the origin the code names), proves it holds the secret, and is added to the
pool by the inviter, in the name of the person who invited it. Then it
syncs.

Secrets are kept only as hashes, in ``sync/invites.json`` (host only), and
work once, for a limited time (three days unless said otherwise).
"""

import base64
import hashlib
import json
import secrets
import socket
import time
from pathlib import Path

from . import pool as poolmod
from .matter import Matter, now, write_toml
from .portable import locked

PREFIX = "adhoc1."


class InviteError(Exception):
    pass


def _path(m: Matter) -> Path:
    return m.sync_dir / "invites.json"


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create(m: Matter, author: str, addresses=(), role="member", hours=72, name=None, same_person=False) -> dict:
    """An invitation to this matter's pool, founding the pool if there isn't
    one yet. Host only."""
    if role not in poolmod.ROLES:
        raise InviteError(f"no role {role!r}")
    if not poolmod.founded(m):
        poolmod.found(m, author, name or socket.gethostname(), addresses)
    p = poolmod.Pool.load(m)
    if not p.is_member(m.origin):
        raise InviteError("this installation isn't a member of the pool, so it can't invite")
    if addresses:
        poolmod.set_addresses(m, author, addresses)
    secret = secrets.token_urlsafe(24)
    record = {"by": author, "role": role, "made": now(), "expires": int(time.time() + hours * 3600),
              **({"same_person": True} if same_person else {})}
    if same_person:
        # whoever joins with this writes for the same person: vouched for now, by an installation of theirs
        poolmod.claim(m, author)
        _, pub = poolmod.vouch_key(secret)
        m.pool_log.append(op="bind-user", user=author, vouch_key=pub, author=author)
    hubs = [x for x in p.active() if x["role"] == "hub" and x["addresses"] and x["origin"] != m.origin]
    if hubs and not addresses:
        # through the hub, which is always there: the invitation works while this one is off
        from .peer import Peer, SyncError
        hub, errors = hubs[0], []
        for address in hub["addresses"]:
            peer = Peer(m, address, hub["origin"])
            try:
                peer.json("POST", "invites", {"digest": _digest(secret), **record})
                if same_person:     # the hub must have the vouch before anyone accepts
                    from .peer import sync_with
                    sync_with(m, address, hub["origin"])
                break
            except (OSError, SyncError) as e:
                errors.append(f"{address}: {e}")
            finally:
                peer.close()
        else:
            raise InviteError(f"couldn't reach the hub ({hub['name']}) to leave the invitation there: "
                              + "; ".join(errors) + ". Pass --address to invite through this installation instead")
        through, addrs = hub["origin"], hub["addresses"]
    else:
        addrs = list(addresses) or p.members[m.origin]["addresses"]
        if not addrs:
            raise InviteError("the invitation needs an address where this installation listens: pass --address "
                              "host:port, and run `adhoc sync serve` there")
        m.sync_dir.mkdir(parents=True, exist_ok=True)
        with open(m.sync_dir / ".lock", "w", encoding="utf-8", newline="\n") as lf, locked(lf):
            invites = _load(m)
            invites[_digest(secret)] = record
            _save(m, invites)
        through = m.origin
    body = {"m": m.id, "f": m.config["founder"], "o": through, "a": addrs, "r": role, "s": secret,
            **({"u": author} if same_person else {})}
    code = PREFIX + base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return {"code": code, "matter": m.id, "role": role, "addresses": addrs, "expires_in_hours": hours,
            "through": "the hub" if through != m.origin else "this installation"}


def register(m: Matter, req: dict, member: str) -> dict:
    """Keep an invitation another member made (the hub does this), so the
    new installation can join here. Only its secret's hash comes."""
    digest = str(req.get("digest", ""))
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("bad invitation")
    if req.get("role", "member") not in poolmod.ROLES:
        raise ValueError(f"no role {req.get('role')!r}")
    m.sync_dir.mkdir(parents=True, exist_ok=True)
    with open(m.sync_dir / ".lock", "w", encoding="utf-8", newline="\n") as lf, locked(lf):
        invites = _load(m)
        if digest not in invites:
            invites[digest] = {"by": str(req.get("by", "")), "role": req.get("role", "member"), "made": now(),
                               **({"same_person": True} if req.get("same_person") else {}),
                               "expires": min(int(req.get("expires", 0)), int(time.time() + 30 * 86400)),
                               "registered_by": member}
            _save(m, invites)
    return {"registered": True}


def parse(code: str) -> dict:
    code = "".join(code.split())
    if not code.startswith(PREFIX):
        raise InviteError("that isn't an adhocdocs invitation")
    raw = code[len(PREFIX):]
    try:
        body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        assert all(k in body for k in ("m", "f", "o", "a", "s"))
    except Exception:
        raise InviteError("the invitation is damaged; copy it again")
    return body


def _load(m: Matter) -> dict:
    try:
        return json.loads(_path(m).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save(m: Matter, invites: dict):
    p = _path(m)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(invites, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(p)


def accept(m: Matter, req: dict) -> dict:
    """The inviter's side of ``/join`` (the request is already checked to be
    signed by the key in ``req["cert"]``)."""
    from .origin import cert_origin
    with open(m.sync_dir / ".lock", "w", encoding="utf-8", newline="\n") as lf, locked(lf):
        invites = _load(m)
        # only the secret's hash comes: whoever accepts can't prove it's the person
        inv = invites.get(str(req.get("digest", "")))
        if inv is None:
            raise PermissionError("no such invitation")
        origin, _ = cert_origin(req["cert"].encode())
        if inv.get("used"):
            if inv["used"]["origin"] == origin:      # asked again: the answer is the same
                return {"matter": m.id, "founder": m.config.get("founder"), "member": origin}
            raise PermissionError("that invitation has been used")
        if time.time() > inv["expires"]:
            raise PermissionError("that invitation has expired")
        poolmod.add_member(m, inv["by"], req["cert"], str(req.get("name") or origin)[:200], inv["role"],
                           [str(a) for a in req.get("addresses", [])][:8])
        inv["used"] = {"origin": origin, "at": now()}
        _save(m, invites)
    return {"matter": m.id, "founder": m.config.get("founder"), "member": origin}


def join(code: str, path, name: str | None = None, addresses=()) -> dict:
    """Join a matter's pool with an invitation, in a new matter at ``path``,
    then sync with the inviter."""
    from .peer import Peer, SyncError, sync_with
    inv = parse(code)
    path = Path(path)
    if path.exists() and any(path.iterdir()):
        raise InviteError(f"{path} isn't empty: join into a new directory")
    m = Matter.create(path, matter_id=inv["m"])
    cfg = m.config
    cfg["founder"] = inv["f"]
    write_toml(m.root / "matter.toml", cfg)
    m = Matter(m.root)
    req = {"digest": _digest(inv["s"]), "cert": m.identity.cert_path().read_text(encoding="ascii"),
           "name": name or socket.gethostname(), "addresses": list(addresses)}
    errors = []
    for address in inv["a"]:
        peer = Peer(m, address, inv["o"])
        try:
            peer.json("POST", "join", req)
        except (OSError, SyncError) as e:
            errors.append(f"{address}: {e}")
            continue
        finally:
            peer.close()
        result = sync_with(m, address, inv["o"])
        if inv.get("u"):
            # the same person as the inviter: prove it holds the secret their vouch key came from
            poolmod.prove(Matter(m.root), inv["s"], inv["u"])
            result = sync_with(Matter(m.root), address, inv["o"])
        return {"matter": str(m.root), "id": m.id, "origin": m.origin, "joined_through": inv["o"], **result}
    raise InviteError("couldn't reach the inviting installation: " + "; ".join(errors))
