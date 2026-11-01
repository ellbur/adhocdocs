"""The hub: an always-on member with a stable address (SYNC.md).

A hub holds replicas of many matters and serves them, so members can sync
through it while each other's laptops are closed, and invitations can name
it. It's an ordinary member of each matter's pool, with the role ``hub``.
It runs no sessions and needs no viewer: it only stores and relays, and
everything it holds is checked like any member's.

**Anyone the administrator enrolled can push a matter to it**, without the
administrator doing anything per matter. ``adhoc hub enroll`` prints a hub
code for a person: the hub's addresses, the hash of its key (so the client
can tell it's talking to this hub), and a token. ``adhoc sync push <code>``
on a member sends the matter's membership log, the hub makes a replica and
answers with the certificate of its new origin, the member adds the hub to
the pool, and then syncs everything to it.

Layout::

    <hub>/hub.json                  name, addresses, enrolled people (tokens as hashes)
    <hub>/sync/                     the hub's own key, for its admin routes (adhocdocs.origin)
    <hub>/matters/<matter ID>/      one matter each, as on any installation
"""

import base64
import hashlib
import json
import secrets
import socket
from contextlib import contextmanager
from pathlib import Path

from . import pool as poolmod
from .matter import Matter, now, write_toml
from .origin import Identity, cert_origin
from .portable import locked

PREFIX = "adhochub1."


class HubError(Exception):
    pass


class Hub:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not (self.root / "hub.json").exists():
            raise HubError(f"{self.root} isn't a hub (no hub.json): make one with adhoc hub init")
        self.identity = Identity(self.root / "sync", self.root)

    @classmethod
    def init(cls, root, name: str, addresses=()) -> "Hub":
        root = Path(root)
        (root / "matters").mkdir(parents=True, exist_ok=True)
        if not (root / "hub.json").exists():
            _write(root / "hub.json", {"name": name, "addresses": list(addresses), "people": {}})
        h = cls(root)
        h.identity.cert_path()
        return h

    @property
    def config(self) -> dict:
        return json.loads((self.root / "hub.json").read_text(encoding="utf-8"))

    def matters(self) -> list[Matter]:
        d = self.root / "matters"
        return [Matter(p) for p in sorted(d.iterdir()) if (p / "matter.toml").exists()] if d.is_dir() else []

    def set_addresses(self, addresses):
        cfg = self.config
        cfg["addresses"] = list(addresses)
        _write(self.root / "hub.json", cfg)

    # -- enrollment -------------------------------------------------------------------------

    def enroll(self, person: str) -> dict:
        """A hub code for one person, to push matters here. The token stays
        valid until revoked; it's a password."""
        cfg = self.config
        if not cfg["addresses"]:
            raise HubError("the hub needs an address the others can reach: adhoc hub init --address host:port")
        token = secrets.token_urlsafe(24)
        with self._lock():
            cfg = self.config
            cfg["people"][_digest(token)] = {"name": person, "enrolled": now()}
            _write(self.root / "hub.json", cfg)
        body = {"a": cfg["addresses"], "h": self.identity.origin, "n": cfg["name"], "t": token}
        return {"code": PREFIX + _b64(body), "person": person, "hub": cfg["name"]}

    def revoke(self, person: str) -> int:
        with self._lock():
            cfg = self.config
            gone = [k for k, v in cfg["people"].items() if v["name"] == person and not v.get("revoked")]
            for k in gone:
                cfg["people"][k]["revoked"] = now()
            _write(self.root / "hub.json", cfg)
        return len(gone)

    @contextmanager
    def _lock(self):
        with open(self.root / ".lock", "w", encoding="utf-8", newline="\n") as f, locked(f):
            yield

    # -- adopting a pushed matter -----------------------------------------------------------------

    def adopt(self, req: dict, requester: str) -> tuple[Matter, dict]:
        """Make (or find) the replica of a matter pushed by an enrolled person.
        ``req``: ``token``, ``matter``, ``founder``, ``pool`` (``{origin:
        [events]}``, the pusher's membership logs), ``cert``. The pusher must
        be a member by those logs."""
        from .peer import SyncError, append_remote
        from .signing import BadLog
        person = self.config["people"].get(_digest(str(req.get("token", ""))))
        if person is None or person.get("revoked"):
            raise PermissionError("not enrolled at this hub")
        mid, founder = str(req["matter"]), str(req["founder"])
        if not mid.startswith("mat_") or "/" in mid or "\\" in mid or ".." in mid:
            raise ValueError(f"bad matter ID {mid!r}")
        with self._lock():
            path = self.root / "matters" / mid
            new = not (path / "matter.toml").exists()
            if new:
                m = Matter.create(path, matter_id=mid)
                cfg = m.config
                cfg.update(founder=founder, adopted_by=person["name"], adopted=now())
                write_toml(path / "matter.toml", cfg)
            m = Matter(path)
            if m.config.get("founder") != founder:
                raise PermissionError("this hub has that matter with another founder")
            # the pusher's membership, so the hub knows who may sync
            pending = {o: evs for o, evs in req.get("pool", {}).items()}
            for _ in range(len(pending) + 1):
                progress = False
                for o in list(pending):
                    have = m.pool_log.log_for(o).count()
                    evs = pending[o][have:] if have < len(pending[o]) else []
                    try:
                        if evs:
                            append_remote(m, "pool", o, have, evs)
                        del pending[o]
                        progress = True
                    except (SyncError, BadLog):
                        continue          # its certificate may be in a log not appended yet
                if not progress:
                    break
            p = poolmod.Pool.load(m)
            if not p.is_member(requester):
                if new:
                    import shutil
                    shutil.rmtree(path)
                raise PermissionError("the pusher isn't a member of the matter's pool")
        return m, {"matter": mid, "origin": m.origin, "cert": m.identity.cert_path().read_text(encoding="ascii"),
                   "name": self.config["name"], "addresses": self.config["addresses"], "new": new}


# -- the member's side ----------------------------------------------------------------------------


def parse(code: str) -> dict:
    code = "".join(code.split())
    if not code.startswith(PREFIX):
        raise HubError("that isn't a hub code (adhoc hub enroll makes them)")
    try:
        body = json.loads(base64.urlsafe_b64decode(code[len(PREFIX):] + "=" * (-len(code[len(PREFIX):]) % 4)))
        assert all(k in body for k in ("a", "h", "t"))
    except Exception:
        raise HubError("the hub code is damaged; copy it again")
    return body


def push(m: Matter, code: str, author: str) -> dict:
    """Put a matter on a hub: found the pool if there isn't one, have the hub
    adopt the matter, add it to the pool as a hub, and sync."""
    from .peer import HUB_SNI, Peer, SyncError, sync_with
    hc = parse(code)
    if not poolmod.founded(m):
        poolmod.found(m, author, socket.gethostname())
        m = Matter(m.root)
    p = poolmod.Pool.load(m)
    if not p.is_member(m.origin):
        raise HubError("this installation isn't a member of the matter's pool")
    for x in p.active():
        if x["role"] == "hub" and x["addresses"] and set(x["addresses"]) & set(hc["a"]):
            # already there: just sync
            return sync_with(m, x["addresses"][0], x["origin"])
    pool_logs = {o: list(m.pool_log.log_for(o).events()) for o in m.pool_log.vector()}
    req = {"token": hc["t"], "matter": m.id, "founder": m.config["founder"], "pool": pool_logs,
           "cert": m.identity.cert_path().read_text(encoding="ascii")}
    errors = []
    for address in hc["a"]:
        hub = Peer(m, address, hc["h"], sni=HUB_SNI, base="/hub/v1")
        try:
            out = hub.json("POST", "adopt", req)
        except (OSError, SyncError) as e:
            errors.append(f"{address}: {e}")
            continue
        finally:
            hub.close()
        if cert_origin(out["cert"].encode())[0] != out["origin"]:
            raise HubError("the hub's answer doesn't hold together")
        poolmod.add_member(m, author, out["cert"], out.get("name") or hc.get("n") or "hub", "hub", out["addresses"])
        return {"hub": out["origin"], "name": out.get("name"), **sync_with(Matter(m.root), address, out["origin"])}
    raise HubError("couldn't reach the hub: " + "; ".join(errors))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()


def _write(path: Path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)
