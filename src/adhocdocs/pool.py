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
``bind-user``      ``user`` (``user:<id>``), ``member``: that installation writes
                   for that person
=================  ==============================================================

**People and installations.** An event authored ``user:alice`` counts only
if it comes from an installation bound to Alice (annotations, published
work). The first installation to write as a person claims them (a
``bind-user`` of itself, written automatically); after that, only an
installation already bound to Alice can bind another to her: her laptop
vouching for her desktop (``adhoc sync vouch``, or ``invite
--same-person``). So a member can't write as someone who works elsewhere.
A person no installation has claimed is anyone's to write as, which is how
a matter worked before it had a pool.

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
        self.cutoffs: dict[str, int] = {}      # removed origin -> how many of its pool events count
        self.users: dict[str, list[str]] = {}  # user:<id> -> the installations that write for them
        self._vouched: dict[str, str] = {}     # invitation digest -> the person it's for
        self._invited: list[tuple] = []        # (add-member event, digest)
        self.members: dict[str, dict] = {}     # origin -> member (``active`` False once removed)
        self.ignored: list[dict] = []          # events that didn't count, and why

    @classmethod
    def load(cls, m: Matter) -> "Pool":
        return cls.replay(m.config.get("founder"), list(m.pool_log.read()))

    @classmethod
    def replay(cls, founder, events) -> "Pool":
        """The pool the events describe.

        An event's ID is chosen by whoever wrote it, so a removed member could
        give a new event an ID from before its removal, and have it replay as
        if written while it was still a member. So a removal records how much
        of the removed installation's logs the remover had (``upto``), and its
        events past that point don't count, whatever their IDs. Since that can
        change who removed whom, this replays until the cutoffs settle."""
        cutoffs: dict = {}
        for _ in range(8):
            p = cls(founder)
            seen: dict[str, int] = {}
            for ev in events:
                o = ev.get("origin")
                i = seen[o] = seen.get(o, -1) + 1
                limit = cutoffs.get(o)
                if limit is not None and i >= limit:
                    p._ignore(ev, "written after its installation was removed")
                    continue
                try:
                    p.apply(ev)
                except (KeyError, TypeError, AttributeError, ValueError) as e:
                    p._ignore(ev, f"malformed: {type(e).__name__}")
            # an invitation "for the same person" binds whoever accepted it,
            # wherever the vouch and the acceptance fall in the order
            for ev, digest in p._invited:
                user = p._vouched.get(digest)
                if user and ev["member"] in p.members and ev["member"] not in p.users.get(user, []):
                    p.users.setdefault(user, []).append(ev["member"])
            new = {o: x["upto"] for o, x in p.members.items() if not x["active"] and x.get("upto") is not None}
            if new == cutoffs:
                break
            cutoffs = new
        p.cutoffs = cutoffs
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
            for u in ev.get("users") or []:          # "the same person as me": the inviter vouches
                self._bind(ev, u, origin, by)
            if isinstance(ev.get("invitation"), str):
                self._invited.append((ev, ev["invitation"]))
        elif op == "remove-member":
            x = self.members.get(ev["member"])
            if x is None:
                return self._ignore(ev, f"{ev['member']} isn't a member")
            x["active"] = False
            x["removed_at"], x["removed_by"] = ev["at"], ev.get("author")
            upto = ev.get("upto")
            if isinstance(upto, dict) and isinstance(upto.get("pool"), int):
                x["upto"] = upto["pool"]
                x["upto_feeds"] = {k: v for k, v in upto.items() if isinstance(v, int)}
        elif op == "set-addresses":
            if ev.get("member") != by:
                return self._ignore(ev, "a member sets only its own addresses")
            self.members[by]["addresses"] = list(ev.get("addresses", []))
        elif op == "bind-user" and isinstance(ev.get("invitation"), str):
            # a vouch for whoever accepts an invitation, made by an installation of that person
            if by in self.users.get(ev["user"], []):
                self._vouched[ev["invitation"]] = ev["user"]
            else:
                return self._ignore(ev, f"{by} doesn't write for {ev['user']}")
        elif op == "bind-user":
            if ev.get("member") not in self.members:
                return self._ignore(ev, f"{ev.get('member')} isn't a member")
            self._bind(ev, ev["user"], ev["member"], by)

    def _bind(self, ev, user, member, by):
        if not isinstance(user, str) or not user.startswith("user:"):
            return self._ignore(ev, f"not a person: {user!r}")
        bound = self.users.get(user)
        if bound and member in bound:
            return
        # the first claim is the installation's own; after that, a bound one vouches
        if (not bound and member == by) or (bound and by in bound):
            self.users.setdefault(user, []).append(member)
        else:
            return self._ignore(ev, f"{by} can't say {member} writes for {user}"
                                    + (f" (that's {', '.join(bound)})" if bound else ""))

    def writes_for(self, origin: str | None, author: str) -> bool:
        """Whether an event by ``author`` from ``origin`` counts: Claude's and
        unclaimed people's do; a claimed person's only from their installations."""
        if not author or not author.startswith("user:") or origin is None:
            return True
        bound = self.users.get(author)
        return not bound or origin in bound

    def summary(self, me: str | None = None) -> list[dict]:
        return [{k: x[k] for k in ("origin", "name", "role", "addresses", "active", "at", "author") if k in x}
                | {"this": x["origin"] == me, "people": sorted(u for u, os_ in self.users.items() if x["origin"] in os_)}
                for x in self.members.values()]


class PoolError(Exception):
    pass


def founded(m: Matter) -> bool:
    return bool(m.config.get("founder"))


def found(m: Matter, author: str, name: str, addresses=(), role="member") -> dict:
    """Make this installation the founder of the matter's pool: seal its logs
    (sign the events it wrote before signing existed), record it as founder,
    and add it as the first member. The people its events were written by
    are its own. Host only."""
    if founded(m):
        raise PoolError(f"this matter already has a pool (founded by {m.config['founder']})")
    seal(m)
    cfg = m.config
    cfg["founder"] = m.origin
    write_toml(m.root / "matter.toml", cfg)
    ev = add_self(m, author, name, addresses, role)
    people = {author} if author.startswith("user:") else set()
    for feed in (m.annotation_log, m.work_log):
        people |= {e["author"] for e in feed.read() if str(e.get("author", "")).startswith("user:")}
    for u in sorted(people):
        m.pool_log.append(op="bind-user", user=u, member=m.origin, author=author)
    return ev


def claim(m: Matter, author: str):
    """Before this installation writes as a person: they must be its own, or
    nobody's yet (then it claims them). Raises PoolError otherwise. Nothing
    to do before there's a pool, or for Claude."""
    if not author.startswith("user:") or not founded(m):
        return
    p = Pool.load(m)
    if not p.is_member(m.origin):
        return          # joining: not a member yet, and nothing it writes counts until it is
    bound = p.users.get(author)
    if bound is None:
        m.pool_log.append(op="bind-user", user=author, member=m.origin, author=author)
    elif m.origin not in bound:
        names = ", ".join(p.members[o]["name"] for o in bound if o in p.members)
        raise PoolError(f"{author} works on another installation ({names}). To write as them here, have that "
                        f"installation vouch for this one: adhoc sync vouch {m.origin} --user {author[5:]}")


def vouch(m: Matter, author: str, member: str) -> dict:
    """Say another member writes for ``author`` too (from an installation
    bound to them)."""
    p = Pool.load(m)
    if member not in p.members or not p.members[member]["active"]:
        raise PoolError(f"{member} isn't a member")
    if m.origin not in p.users.get(author, []):
        raise PoolError(f"this installation doesn't write for {author}, so it can't vouch for another")
    return m.pool_log.append(op="bind-user", user=author, member=member, author=author)


def add_self(m: Matter, author: str, name: str, addresses=(), role="member") -> dict:
    return m.pool_log.append(op="add-member", member=m.origin, cert=m.identity.cert_path().read_text(encoding="ascii"),
                             name=name, role=role, addresses=list(addresses), author=author)


def add_member(m: Matter, author: str, cert: str, name: str, role="member", addresses=(), same_person=False,
               invitation: str | None = None) -> dict:
    p = Pool.load(m)
    if not p.is_member(m.origin):
        raise PoolError("this installation isn't a member of the pool, so it can't add one")
    origin, _ = cert_origin(cert.encode())
    if p.is_member(origin):
        return {"member": origin, "already": True}
    return m.pool_log.append(op="add-member", member=origin, cert=cert, name=name, role=role,
                             addresses=list(addresses), author=author,
                             **({"users": [author]} if same_person and author.startswith("user:") else {}),
                             **({"invitation": invitation} if invitation else {}))


def remove_member(m: Matter, author: str, origin: str) -> dict:
    p = Pool.load(m)
    if not p.is_member(m.origin):
        raise PoolError("this installation isn't a member of the pool")
    if not p.is_member(origin):
        raise PoolError(f"{origin} isn't a member")
    # how much of its logs counts: what this installation holds of them now
    upto = {name: feed.log_for(origin).count()
            for name, feed in (("pool", m.pool_log), ("store", m.logs["store"]), ("annotations", m.annotation_log),
                               ("work", m.work_log))}
    ev = m.pool_log.append(op="remove-member", member=origin, upto=upto, author=author)
    save_cutoffs(m)
    return ev


def save_cutoffs(m: Matter) -> dict:
    """Write ``pool/cutoffs.json``: for each feed, each removed origin and how
    many of its events count. Every reader of the logs (adhocdocs.matter's
    feeds, so the matter's state, annotations, published work and indexes,
    and the viewer) stops each such log there. Written whenever membership
    changes here or arrives; only when it differs."""
    import json
    p = Pool.load(m)
    feeds: dict = {}
    for o, x in p.members.items():
        if not x["active"] and x.get("upto_feeds"):
            for feed, n in x["upto_feeds"].items():
                if feed != "pool":           # membership has its own replay (Pool.replay)
                    feeds.setdefault(feed, {})[o] = n
    data = {"v": 1, "feeds": feeds}
    path = m.root / "pool" / "cutoffs.json"
    text = json.dumps(data, indent=1, sort_keys=True) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return data
    except FileNotFoundError:
        if not feeds:
            return data
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return data


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
