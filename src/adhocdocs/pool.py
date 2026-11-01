"""The pool: the installations a matter syncs among (SYNC.md).

Membership is a feed like the others (``pool/logs/<origin>.jsonl``), so
joining through any member is joining the pool: every member learns of
every other. Events, besides ``id``, ``at``, ``author`` (the person who did
it, ``user:<id>``), ``prev`` and ``sig``:

=================  ==============================================================
``add-member``     ``member`` (an origin), ``cert`` (its certificate, PEM),
                   ``name`` (for people: "Alice's laptop"), ``role`` (``member``
                   or ``hub``), ``addresses`` (``host:port`` where it listens)
``remove-member``  ``member``
``set-addresses``  ``member`` (the event's own origin), ``addresses``
=================  ==============================================================

**Who can change membership.** The founder (``founder`` in ``matter.toml``,
carried in every invitation) is the first member, by its own
``add-member``. After that, an event counts only if the installation that
wrote it is a member when the event is replayed, in the merge order every
installation shares. So everyone agrees on the members, and an
installation that was removed can't add itself back. Any member can add or
remove any other (SYNC.md, open questions).

A removed member's certificate stays known, so its earlier events still
check. Removing a member stops everyone syncing with it; it can't recall
what it already has.
"""

from .matter import Matter, read_toml, write_toml
from .origin import cert_origin
from .signing import BadLog, check_chain, event_hash, sign

ROLES = ("member", "hub")


class Pool:
    def __init__(self, founder: str | None):
        self.founder = founder
        self.members: dict[str, dict] = {}     # origin -> member (``active`` False once removed)
        self.ignored: list[dict] = []          # events that didn't count, and why

    @classmethod
    def load(cls, m: Matter) -> "Pool":
        p = cls(m.config.get("founder"))
        for ev in m.pool_log.read():
            p.apply(ev)
        return p

    def active(self) -> list[dict]:
        return [x for x in self.members.values() if x["active"]]

    def is_member(self, origin: str) -> bool:
        return origin in self.members and self.members[origin]["active"]

    def cert(self, origin: str) -> bytes | None:
        """An origin's certificate, if it is or ever was a member."""
        x = self.members.get(origin)
        return x["cert"].encode() if x else None

    def _ignore(self, ev, why):
        self.ignored.append({"event": ev["id"], "origin": ev.get("origin"), "op": ev["op"], "reason": why})

    def apply(self, ev: dict):
        op, by = ev["op"], ev.get("origin")
        genesis = not self.members and op == "add-member" and by == self.founder and ev.get("member") == self.founder
        if not genesis and not self.is_member(by):
            return self._ignore(ev, f"{by} wasn't a member")
        if op == "add-member":
            try:
                origin, _ = cert_origin(ev["cert"].encode())
            except (ValueError, KeyError) as e:
                return self._ignore(ev, f"bad certificate: {e}")
            if origin != ev["member"]:
                return self._ignore(ev, "the certificate is for another origin")
            if ev.get("role", "member") not in ROLES:
                return self._ignore(ev, f"no role {ev.get('role')!r}")
            self.members[origin] = {"origin": origin, "cert": ev["cert"], "name": ev.get("name") or origin,
                                    "role": ev.get("role", "member"), "addresses": list(ev.get("addresses", [])),
                                    "added_by": by, "author": ev.get("author"), "at": ev["at"], "active": True}
        elif op == "remove-member":
            x = self.members.get(ev["member"])
            if x is None:
                return self._ignore(ev, f"{ev['member']} isn't a member")
            x["active"] = False
            x["removed_at"], x["removed_by"] = ev["at"], ev.get("author")
        elif op == "set-addresses":
            if ev.get("member") != by:
                return self._ignore(ev, "a member sets only its own addresses")
            self.members[by]["addresses"] = list(ev.get("addresses", []))

    def summary(self, me: str | None = None) -> list[dict]:
        return [{k: x[k] for k in ("origin", "name", "role", "addresses", "active", "at", "author") if k in x}
                | {"this": x["origin"] == me} for x in self.members.values()]


class PoolError(Exception):
    pass


def founded(m: Matter) -> bool:
    return bool(m.config.get("founder"))


def found(m: Matter, author: str, name: str, addresses=(), role="member") -> dict:
    """Make this installation the founder of the matter's pool: seal its logs
    (sign the events it wrote before signing existed), record it as founder,
    and add it as the first member. Host only."""
    if founded(m):
        raise PoolError(f"this matter already has a pool (founded by {m.config['founder']})")
    seal(m)
    cfg = m.config
    cfg["founder"] = m.origin
    write_toml(m.root / "matter.toml", cfg)
    return add_self(m, author, name, addresses, role)


def add_self(m: Matter, author: str, name: str, addresses=(), role="member") -> dict:
    return m.pool_log.append(op="add-member", member=m.origin, cert=m.identity.cert_path().read_text(encoding="ascii"),
                             name=name, role=role, addresses=list(addresses), author=author)


def add_member(m: Matter, author: str, cert: str, name: str, role="member", addresses=()) -> dict:
    p = Pool.load(m)
    if not p.is_member(m.origin):
        raise PoolError("this installation isn't a member of the pool, so it can't add one")
    origin, _ = cert_origin(cert.encode())
    if p.is_member(origin):
        return {"member": origin, "already": True}
    return m.pool_log.append(op="add-member", member=origin, cert=cert, name=name, role=role,
                             addresses=list(addresses), author=author)


def remove_member(m: Matter, author: str, origin: str) -> dict:
    p = Pool.load(m)
    if not p.is_member(m.origin):
        raise PoolError("this installation isn't a member of the pool")
    if not p.is_member(origin):
        raise PoolError(f"{origin} isn't a member")
    return m.pool_log.append(op="remove-member", member=origin, author=author)


def set_addresses(m: Matter, author: str, addresses) -> dict | None:
    p = Pool.load(m)
    if not p.is_member(m.origin):
        raise PoolError("this installation isn't a member of the pool")
    if p.members[m.origin]["addresses"] == list(addresses):
        return None
    return m.pool_log.append(op="set-addresses", member=m.origin, addresses=list(addresses), author=author)


def seal(m: Matter):
    """Sign this installation's own events that aren't signed yet (written
    before signing existed), rewriting its logs in place, IDs unchanged.
    Only before the logs are shared: once there's a pool, others hold copies."""
    import json
    from .portable import locked
    if founded(m):
        raise PoolError("the logs are shared already; they can't be re-signed")
    key, origin = m.identity.key, m.origin
    for feed in (m.logs["store"], m.annotation_log, m.work_log, m.pool_log):
        path = feed.dir / f"{origin}.jsonl"
        if not path.exists():
            continue
        with open(path, "r+", encoding="utf-8", newline="\n") as f, locked(f):
            events = [json.loads(l) for l in f if l.strip()]
            try:
                check_chain(None, events, key.public_key())
                continue
            except BadLog:
                pass
            out, prev = [], None
            for ev in events:
                ev = sign({k: v for k, v in ev.items() if k not in ("prev", "sig")}, key, prev)
                out.append(ev)
                prev = ev
            f.seek(0)
            f.write("".join(json.dumps(ev, ensure_ascii=False) + "\n" for ev in out))
            f.truncate()


def check_log(m: Matter, feed, origin: str, pool: Pool | None = None) -> int:
    """Check one origin's whole log in a feed. Returns how many events it has."""
    import json
    pool = pool or Pool.load(m)
    cert = pool.cert(origin)
    if cert is None:
        raise BadLog(f"{origin} isn't known to the pool")
    from .origin import cert_origin as co
    from .signing import public_key
    _, raw = co(cert)
    events = list(feed.log_for(origin).events())
    check_chain(None, events, public_key(raw), f"{feed.name}/{origin}")
    return len(events)


__all__ = ["Pool", "PoolError", "found", "add_member", "remove_member", "set_addresses", "seal", "check_log",
           "event_hash", "read_toml"]
