"""Sync between members of a matter's pool: the protocol (SYNC.md).

**Transport.** HTTPS (TLS 1.3) on one port, which can serve several matters:
the client names the matter, by its ID and its pool's founder
(:func:`pool_key`), in SNI, and the server answers with that matter's
origin certificate. Certificates aren't checked
against any authority. The client checks that the server's key hashes to
the origin it meant to reach (an origin's ID is the hash of its key), so
it can't be talked to by anyone else.

**Requests are signed** by the client's origin key, over the method, path,
a timestamp, a nonce, the body's hash and the server's origin (so a request
can't be replayed, or sent to another server). The server accepts them
from the pool's active members, and ``/join`` from an installation holding
an invitation. Nothing is read or written without one.

Routes, under ``/v1/<matter ID>.<founder>/``:

====================================  ===========================================
``GET vector``                        how many events the server holds, per feed
                                      and origin
``GET events/<feed>/<origin>?from=N``  that log's events from the Nth (``limit``)
``POST events/<feed>/<origin>``        ``{"from": N, "events": [...]}``: append to
                                      the server's copy, after checking them
``POST missing``                      ``{"hashes": [...]}``: which it lacks
``GET objects/<hash>``                an object's bytes
``PUT objects/<hash>``                store an object (checked against its hash)
``POST join``                         ``{"digest", "cert", "name"}``: an invitation
                                      used (:mod:`adhocdocs.invite`)
``POST invites``                      a member leaves an invitation here (a hub)
``GET wait?seen=F``                   answers when the logs' fingerprint isn't
                                      ``F`` any more (a long poll: adhocdocs.watch)
====================================  ===========================================

A hub (:mod:`adhocdocs.hub`) also answers ``POST /hub/v1/adopt``, under SNI
``adhoc-hub`` and its own key, to take in a matter pushed to it.

**A sync** (:func:`sync_with`) runs from the client: membership first (it
says whose events to accept), then the other feeds. For each log, whoever
has more events sends the rest, with the objects they name, objects first,
so an event never arrives before what it points at. Each event is checked
against its origin's key and the event before it, so a member can pass on
another's events and the receiver can still tell they're genuine.
"""

import hashlib
import http.client
import json
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import pool as poolmod
from .matter import EventLog, Matter
from .objects import HASH_PREFIX, ObjectStore
from .origin import cert_origin, is_origin
from .portable import locked
from .signing import BadLog, b64, check_chain, has_hidden, public_key, unb64

PORT = 7480
BATCH = 2000
CLOCK_SKEW = 300          # seconds a request's timestamp may be off
FEEDS = ("pool", "store", "annotations", "work")
HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")


class SyncError(Exception):
    pass


class _TooLarge(Exception):
    pass


JSON_LIMIT = 64 << 20      # a batch of events, or a list of hashes
JOIN_LIMIT = 64 << 10      # from someone not yet a member
ADOPT_LIMIT = 16 << 20     # a matter's membership logs, from someone enrolled at the hub


def pool_key(m: Matter) -> str:
    """How the protocol names a matter: its ID and its pool's founder. A
    matter ID alone isn't secret (it's in invitations, and in clear in SNI),
    so a hub keys replicas by both: someone else can't take a matter's place
    by founding a pool of their own under its ID."""
    founder = m.config.get("founder")
    if not m.id or not founder:
        raise SyncError("this matter has no pool yet: invite someone, push it to a hub, or serve it with "
                        "--advertise, which founds one")
    return f"{m.id}.{founder}"


def sni_name(key: str) -> str:
    return key.lower().replace("_", "-").replace(".", "-")


def feed_of(m: Matter, name: str):
    return {"pool": m.pool_log, "store": m.logs["store"], "annotations": m.annotation_log, "work": m.work_log}[name]


def counts(m: Matter) -> dict:
    """``{feed: {origin: events held}}``."""
    return {name: feed_of(m, name).vector() for name in FEEDS}


def heads(m: Matter) -> dict:
    """``{feed: {origin: hash of the last event}}``: two copies of a log with
    the same count and different heads have forked. (A sealed hub keeps each
    event's hash beside it: adhocdocs.seal.)"""
    from .seal import envelope_hash
    from .signing import event_hash
    sealed = is_sealed(m)
    return {name: {o: (envelope_hash if sealed and name != "pool" else event_hash)(feed_of(m, name).log_for(o).last_event())
                   for o in feed_of(m, name).vector()} for name in FEEDS}


def is_sealed(m: Matter) -> bool:
    """A hub's replica that holds only sealed events and objects (adhocdocs.seal)."""
    return bool(m.config.get("sealed"))


# -- what events point at ---------------------------------------------------------------------


def direct_refs(feed: str, ev: dict) -> list[str]:
    """The objects an event names itself."""
    if feed == "store":
        op = ev["op"]
        if op == "add-document":
            return [ev["doc"], ev["content"]]
        if op == "add-layer":
            return [ev["layer"]]
        if op == "set-metadata":
            return [ev["metadata"]]
    if feed == "work" and ev["op"] == "publish":
        return [ev["publication"]]
    return []


def nested_refs(data: bytes) -> list[str]:
    """The objects an object names: a layer's text and its parts, the layer
    it derives from, a publication's files, a document's content."""
    if not data.startswith(b"{"):
        return []
    try:
        obj = json.loads(data)
    except ValueError:
        return []
    t = obj.get("type") if isinstance(obj, dict) else None
    if t == "layer":
        return [obj[k] for k in ("text", "offset_map", "segments", "geometry", "presentation", "derived_from") if obj.get(k)]
    if t == "publication":
        return list(obj.get("files", {}).values())
    if t == "document":
        return [obj["content"]] if obj.get("content") else []
    return []


def _small_object(store: ObjectStore, h: str) -> bytes | None:
    """An object's bytes if it's small enough to be one of ours (JSON), else None."""
    p = store.path(h)
    if p is None or p.stat().st_size > 4 << 20:
        return None
    return p.read_bytes()


def closure(store: ObjectStore, hashes) -> list[str]:
    """``hashes`` and everything they name, as far as ``store`` has them
    (a purged object isn't there, and isn't sent)."""
    out, todo = [], list(hashes)
    seen = set()
    while todo:
        h = todo.pop()
        if h in seen or not HASH_RE.fullmatch(h):
            continue
        seen.add(h)
        if not store.has(h):
            continue
        out.append(h)
        data = _small_object(store, h)
        if data:
            todo += nested_refs(data)
    return out


# -- writing what arrives ------------------------------------------------------------------------


def _purged(m: Matter, feed: str, origin: str, events, pool) -> tuple[set, set, set]:
    """What's purged here, counting purges among ``events`` (a store batch):
    documents, their contents, and objects already deleted. (A sealed hub
    can't read purges: members tell it what to drop.)"""
    from . import purge
    if is_sealed(m):
        return set(), set(), set()
    docs = purge.purged_docs(m)
    if feed == "store":
        for ev in events:
            if ev.get("op") == "purge" and isinstance(ev.get("docs"), list) \
                    and origin in (pool.users.get(ev.get("author"), []) if pool else [origin]):
                docs |= {d for d in ev["docs"] if isinstance(d, str)}
    state = m.state
    contents = {state.documents[d] for d in docs if d in state.documents} | set(purge.record(m)["contents"])
    if feed == "store":     # documents purged in the same batch they arrive in
        contents |= {ev["content"] for ev in events if ev.get("op") == "add-document" and ev.get("doc") in docs}
    return docs, contents, purge.purged_objects(m)


def _refs(feed: str, ev: dict, docs: set, contents: set, objects: set) -> list[str]:
    """The objects an event needs, leaving out what a purge took."""
    if feed == "store" and (ev.get("doc") in docs or ev.get("content") in contents):
        return []
    return [h for h in direct_refs(feed, ev) if h not in objects]


def store_only(m: Matter) -> ObjectStore:
    return ObjectStore(m.store_dir / "objects")


def known_key(m: Matter, origin: str, pool: poolmod.Pool, events=()):
    """The public key that checks an origin's events: its certificate in the
    pool log, or for the founder, the certificate in its own first pool
    event (it adds itself), which names itself by the key's hash. ``events``
    are ones about to be appended to its pool log."""
    cert = pool.cert(origin)
    if cert is None and origin == pool.founder:
        first = next(m.pool_log.log_for(origin).events(), None) or next(iter(events), None)
        if first and first.get("op") == "add-member" and first.get("member") == origin:
            cert = first["cert"].encode()
    if cert is None:
        return None
    o, raw = cert_origin(cert)
    if o != origin:
        raise BadLog(f"the certificate for {origin} is someone else's")
    return public_key(raw)


def append_remote(m: Matter, feed: str, origin: str, start: int, events: list[dict], pool: poolmod.Pool | None = None) -> int:
    """Append another installation's events to this one's copy of its log,
    after checking them. ``start`` is how many events the sender thinks this
    copy has. Returns the count after. Objects the events name must be here
    already."""
    if not events:
        return feed_of(m, feed).log_for(origin).count()
    pool = pool or poolmod.Pool.load(m)
    key = known_key(m, origin, pool, events if feed == "pool" else ())
    if key is None:
        raise SyncError(f"{origin} isn't a member of this matter's pool")
    store = store_only(m)
    from . import purge
    docs, contents, objects = _purged(m, feed, origin, events, pool)
    missing = [h for ev in events for h in _refs(feed, ev, docs, contents, objects) if not store.has(h)]
    if missing:
        raise SyncError(f"objects missing for {feed}/{origin}: {missing[:3]}")
    log = feed_of(m, feed).log_for(origin)
    log.path.parent.mkdir(parents=True, exist_ok=True)
    gone = pool.members.get(origin)
    limit = gone["upto_feeds"].get(feed) if gone and not gone["active"] and gone.get("upto_feeds") else None
    from .matter import same_file
    with open(log.path, "a", encoding="utf-8", newline="\n") as f, locked(f):
        if not same_file(f, log.path):
            raise SyncError(f"{feed}/{origin}: the log was being rewritten; try again")
        have = log.count()
        if have != start:
            if have > start:        # the events (or some) arrived meanwhile: keep the rest
                skip = have - start
                if skip >= len(events):
                    return have
                events = events[skip:]
            else:
                raise SyncError(f"{feed}/{origin}: this copy has {have} events, not {start}")
        if limit is not None and have + len(events) > limit:
            # written after the installation was removed: they don't count, so they don't come
            events = events[:max(0, limit - have)]
            if not events:
                raise SyncError(f"{feed}/{origin}: written after the installation was removed")
        last = log.last_event()
        check_chain(last, events, key, f"{feed}/{origin}", lambda ev: purge.may_hide(ev, docs))
        if feed == "annotations" and docs:
            # about a purged document, and arriving late: kept here only redacted
            events = [purge.redact(ev, docs) or ev for ev in events]
        if feed == "pool":
            # membership is replayed by ID: an origin's IDs must only go up
            prev = last["id"] if last else ""
            for ev in events:
                if not isinstance(ev.get("id"), str) or ev["id"] <= prev:
                    raise BadLog(f"pool/{origin}: event {ev.get('id')} is dated before the one it follows")
                prev = ev["id"]
        f.write("".join(json.dumps({k: v for k, v in ev.items() if k not in ("feed", "log", "origin", "stale")},
                                   ensure_ascii=False) + "\n" for ev in events))
    return have + len(events)


def read_events(m: Matter, feed: str, origin: str, start: int, limit: int) -> list[dict]:
    out = []
    for i, ev in enumerate(feed_of(m, feed).log_for(origin).events()):
        if i >= start + limit:
            break
        if i >= start:
            out.append(ev)
    return out


def append_sealed(m: Matter, feed: str, origin: str, start: int, envelopes: list[dict], pool=None) -> int:
    """A sealed hub's side of appending: the events are sealed, so it checks
    what it can (whose log, the order of hashes, the form), and stores them
    as they are. Members check the rest when they read them."""
    from .seal import envelope_hash
    pool = pool or poolmod.Pool.load(m)
    if pool.cert(origin) is None:
        raise SyncError(f"{origin} isn't a member of this matter's pool")
    gone = pool.members.get(origin)
    limit = gone["upto_feeds"].get(feed) if gone and not gone["active"] and gone.get("upto_feeds") else None
    log = feed_of(m, feed).log_for(origin)
    log.path.parent.mkdir(parents=True, exist_ok=True)
    from .matter import same_file
    with open(log.path, "a", encoding="utf-8", newline="\n") as f, locked(f):
        if not same_file(f, log.path):
            raise SyncError(f"{feed}/{origin}: the log was being rewritten; try again")
        have = log.count()
        if have != start:
            if have > start and have - start < len(envelopes):
                envelopes = envelopes[have - start:]
            elif have > start:
                return have
            else:
                raise SyncError(f"{feed}/{origin}: this copy has {have} events, not {start}")
        if limit is not None:
            envelopes = envelopes[:max(0, limit - have)]
        prev = envelope_hash(log.last_event())
        out = []
        for env in envelopes:
            ok = (isinstance(env, dict) and isinstance(env.get("id"), str) and len(env["id"]) < 64
                  and isinstance(env.get("h"), str) and HASH_RE.fullmatch(env["h"]) is not None
                  and isinstance(env.get("sealed"), str) and len(env["sealed"]) < 8 << 20
                  and isinstance(env.get("e", 0), int) and 0 <= env.get("e", 0) < 1 << 20)
            if ok:
                env = {k: env.get(k) for k in ("id", "h", "hp", "sealed")} | {"e": env.get("e", 0)}
            if not ok or env.get("hp") != prev:
                raise BadLog(f"{feed}/{origin}: event {env.get('id') if isinstance(env, dict) else '?'} doesn't "
                             "follow the one before it")
            prev = envelope_hash(env)
            out.append(env)
        f.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in out))
    return have + len(out)


def after_arrival(m: Matter, feeds):
    """Re-derive what depends on arrived events (adhocdocs.sync.refresh)."""
    from . import sync
    if is_sealed(m):
        from . import pool
        pool.save_cutoffs(m) if "pool" in feeds else None
        return                      # nothing here can be read: nothing to derive
    if set(feeds) & {"annotations", "work", "pool", "store"}:
        sync.refresh(Matter(m.root))


# -- request signing ------------------------------------------------------------------------------


def _message(method, path, ts, nonce, body_hash, server_origin) -> bytes:
    return f"adhoc-sync-v1\n{method}\n{path}\n{ts}\n{nonce}\n{body_hash}\n{server_origin}".encode()


def body_hash(body: bytes | None) -> str:
    return "UNSIGNED" if body is None else hashlib.sha256(body).hexdigest()


def auth_header(m: Matter, method: str, path: str, body: bytes | None, server_origin: str) -> str:
    ts, nonce = int(time.time()), secrets.token_hex(12)
    sig = b64(m.identity.key.sign(_message(method, path, ts, nonce, body_hash(body), server_origin)))
    return f"origin={m.origin},ts={ts},nonce={nonce},sig={sig}"


class _Nonces:
    def __init__(self):
        self.seen: dict[str, float] = {}
        self.lock = threading.Lock()

    def fresh(self, nonce: str) -> bool:
        now = time.time()
        with self.lock:
            if len(self.seen) > 10000:
                self.seen = {n: t for n, t in self.seen.items() if now - t < 2 * CLOCK_SKEW}
            if nonce in self.seen:
                return False
            self.seen[nonce] = now
            return True


# -- the server --------------------------------------------------------------------------------------


HUB_SNI = "adhoc-hub"


def _server_context(identity) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(identity.cert_path(), identity.key_path())
    return ctx


class Server:
    """Serves matters to their pools' members on one port."""

    def __init__(self, matters: list[Matter], host="0.0.0.0", port=PORT, on_change=None, hub=None):
        self._list = list(matters)
        self._stamp, self._by_key = None, {}
        self._waits: dict[str, int] = {}
        self._wait_lock = threading.Lock()
        self.nonces = _Nonces()
        self.on_change = on_change
        self.hub = hub                      # adhocdocs.hub.Hub: its own routes, under /hub/v1/
        self._contexts: dict[Path, ssl.SSLContext] = {}
        if not matters and hub is None:
            raise SyncError("nothing to serve")
        first = hub.identity if hub is not None else matters[0].identity
        default = _server_context(first)

        def pick(sock, name, _ctx):
            if name == HUB_SNI and self.hub is not None:
                sock.context = self._context(self.hub.identity)
                return
            m = next((m for k, m in self.matters.items() if sni_name(k) == name), None)
            if m is not None:
                sock.context = self._context(m.identity)
        default.sni_callback = pick
        server = self

        class Handler(_Handler):
            srv = server
        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.httpd.socket = default.wrap_socket(self.httpd.socket, server_side=True, do_handshake_on_connect=False)

    MAX_WAITS = 4

    def wait_slot(self, origin: str, delta: int) -> bool:
        """Count a member's long polls; refuse more than a few at once."""
        with self._wait_lock:
            n = self._waits.get(origin, 0)
            if delta > 0 and n >= self.MAX_WAITS:
                return False
            self._waits[origin] = n + delta
            return True

    @property
    def matters(self) -> dict[str, Matter]:
        """The matters served, by :func:`pool_key`. A matter's pool can be
        founded while it's served (its first invitation), so this follows
        ``matter.toml``, rereading only when one changes."""
        stamp = []
        for m in self._list:
            try:
                stamp.append((m.root, (m.root / "matter.toml").stat().st_mtime_ns))
            except FileNotFoundError:
                stamp.append((m.root, None))
        if stamp != self._stamp:
            out = {}
            for m in self._list:
                try:
                    out[pool_key(m)] = m
                except SyncError:
                    pass            # no pool yet: nothing to sync
            self._by_key, self._stamp = out, stamp
        return self._by_key

    def _context(self, identity) -> ssl.SSLContext:
        if identity.dir not in self._contexts:
            self._contexts[identity.dir] = _server_context(identity)
        return self._contexts[identity.dir]

    @property
    def address(self) -> tuple[str, int]:
        return self.httpd.server_address[:2]

    def add(self, m: Matter):
        """Serve another matter (a hub, when one is pushed to it)."""
        if all(x.root != m.root for x in self._list):
            self._list.append(m)

    def serve_forever(self):
        self.httpd.serve_forever()

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.serve_forever, daemon=True)
        t.start()
        return t

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _Handler(BaseHTTPRequestHandler):
    srv: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (ssl.SSLError, ConnectionError, TimeoutError):
            pass        # the client went away (or wasn't who it should be): nothing to answer

    def setup(self):
        # Many small requests: without this, each waits on a delayed ACK (~40 ms)
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        super().setup()
        try:
            self.connection.do_handshake()
        except (ssl.SSLError, OSError):
            self.close_connection = True

    def _json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self, limit: int) -> bytes:
        """The request's body, which must say its length, and be at most
        ``limit`` bytes. Read only after the headers check out."""
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise _TooLarge("a request needs a Content-Length")
        if n < 0 or n > limit:
            raise _TooLarge("request too large")
        return self.rfile.read(n)

    def _precheck(self, m: Matter | None):
        """What can be checked before reading a body: a signature header with
        a fresh time, and (with ``m``) an origin that's an active member. So
        no one else can make the server read anything."""
        h = self.headers.get("Authorization", "")
        fields = dict(x.split("=", 1) for x in h.removeprefix("Adhoc ").split(",") if "=" in x)
        try:
            origin, ts = fields["origin"], int(fields["ts"])
        except (KeyError, ValueError):
            raise PermissionError("not signed")
        if abs(time.time() - ts) > CLOCK_SKEW:
            raise PermissionError("the request's clock is too far off this server's")
        if m is not None and not poolmod.Pool.load(m).is_member(origin):
            raise PermissionError(f"{origin} isn't a member of this matter's pool")

    def _route(self):
        parts = urllib.parse.urlsplit(self.path)
        segs = [urllib.parse.unquote(s) for s in parts.path.split("/") if s]
        if len(segs) < 3 or segs[0] != "v1" or segs[1] not in self.srv.matters:
            return None, None, None
        return self.srv.matters[segs[1]], segs[2:], urllib.parse.parse_qs(parts.query)

    def _auth(self, m: Matter | None, body: bytes | None, join_cert: bytes | None = None,
              server_origin: str | None = None) -> str:
        """The requesting origin, if the request is signed by it, fresh, and
        (except to join, with ``join_cert``) from an active member of ``m``'s
        pool."""
        h = self.headers.get("Authorization", "")
        fields = dict(x.split("=", 1) for x in h.removeprefix("Adhoc ").split(",") if "=" in x)
        try:
            origin, ts, nonce, sig = fields["origin"], int(fields["ts"]), fields["nonce"], fields["sig"]
        except (KeyError, ValueError):
            raise PermissionError("not signed")
        if abs(time.time() - ts) > CLOCK_SKEW:
            raise PermissionError("the request's clock is too far off this server's")
        if join_cert is not None:
            o, raw = cert_origin(join_cert)
            if o != origin:
                raise PermissionError("the certificate isn't the requester's")
        else:
            p = poolmod.Pool.load(m)
            if not p.is_member(origin):
                raise PermissionError(f"{origin} isn't a member of this matter's pool")
            _, raw = cert_origin(p.cert(origin))
        msg = _message(self.command, self.path, ts, nonce, body_hash(body), server_origin or m.origin)
        try:
            public_key(raw).verify(unb64(sig), msg)
        except Exception:
            raise PermissionError("bad signature")
        if not self.srv.nonces.fresh(nonce):
            raise PermissionError("replayed request")
        return origin

    def _handle(self):
        if self.srv.hub is not None and self.path.startswith("/hub/v1/"):
            return self._hub()
        m, segs, q = self._route()
        if m is None:
            self.close_connection = True
            return self._json(404, {"error": "no such matter here"})
        streaming = self.command == "PUT" and segs[:1] == ["objects"]
        joining = segs == ["join"] and self.command == "POST"
        body = None
        try:
            self._precheck(None if joining else m)
            if not streaming and self.command != "GET":
                body = self._body(JOIN_LIMIT if joining else JSON_LIMIT)
            if joining:
                req = json.loads(body)
                self._auth(m, body, req.get("cert", "").encode())
                from . import invite
                return self._json(200, invite.accept(m, req))
            who = self._auth(m, None if streaming else (body if body is not None else b""))
            return self._dispatch(m, segs, q, body, who)
        except PermissionError as e:
            if streaming or body is None:
                self.close_connection = True       # the body wasn't read
            return self._json(403, {"error": str(e)})
        except _TooLarge as e:
            self.close_connection = True
            return self._json(413, {"error": str(e)})
        except (SyncError, BadLog, poolmod.PoolError, KeyError, ValueError, TypeError, AttributeError) as e:
            if streaming or body is None:
                self.close_connection = True
            return self._json(409, {"error": f"{type(e).__name__}: {e}"})

    def _hub(self):
        """The hub's own routes: adopting a matter pushed to it."""
        hub = self.srv.hub
        body = None
        try:
            self._precheck(None)
            body = self._body(ADOPT_LIMIT)
            req = json.loads(body)
            who = self._auth(None, body, req.get("cert", "").encode(), hub.identity.origin)
            if self.path == "/hub/v1/adopt" and self.command == "POST":
                m, out = hub.adopt(req, who)
                self.srv.add(m)
                return self._json(200, out)
            return self._json(404, {"error": "no such route"})
        except PermissionError as e:
            self.close_connection = body is None
            return self._json(403, {"error": str(e)})
        except _TooLarge as e:
            self.close_connection = True
            return self._json(413, {"error": str(e)})
        except (SyncError, BadLog, poolmod.PoolError, KeyError, ValueError, TypeError, AttributeError) as e:
            return self._json(409, {"error": f"{type(e).__name__}: {e}"})

    def _dispatch(self, m, segs, q, body, who):
        cmd = self.command
        if segs == ["invites"] and cmd == "POST":
            from . import invite
            return self._json(200, invite.register(m, json.loads(body), who))
        if segs == ["wait"] and cmd == "GET":
            # a long poll: answers when this matter's logs change (adhocdocs.watch)
            import math
            from .watch import wait_for_change
            try:
                timeout = float(q.get("timeout", ["50"])[0])
            except ValueError:
                timeout = 0
            timeout = min(max(timeout, 0), 120) if math.isfinite(timeout) else 0
            seen = q.get("seen", [None])[0]
            if not self.srv.wait_slot(who, +1):
                return self._json(429, {"error": "too many waits at once"})
            try:
                fp = wait_for_change(m, seen, timeout) if seen else wait_for_change(m, None, 0)
            finally:
                self.srv.wait_slot(who, -1)
            return self._json(200, {"fingerprint": fp})
        if segs == ["vector"] and cmd == "GET":
            return self._json(200, {"matter": m.id, "origin": m.origin, "vector": counts(m), "heads": heads(m),
                                    "sealed": is_sealed(m)})
        if segs == ["redact"] and cmd == "POST" and is_sealed(m):
            # a member gives a sealed hub the redacted form of events a purge redacted (the hub can't
            # read either): it must have the same hash, so it's the same event, and readers check
            # that what it hides is what a purge hides (adhocdocs.purge.may_hide)
            envs = json.loads(body).get("envelopes", [])
            if not isinstance(envs, list) or len(envs) > 100000:
                raise ValueError("envelopes must be a list")
            return self._json(200, {"replaced": _redact_sealed(m, who, envs)})
        if segs == ["forget"] and cmd == "POST" and is_sealed(m):
            # and which objects purges deleted, by their sealed names
            req = json.loads(body)
            names, purge_id = req.get("objects", []), str(req.get("purge", ""))[:64]
            if not isinstance(names, list) or len(names) > 100000 or not purge_id:
                raise ValueError("forget needs objects and the purge that deleted them")
            store = store_only(m)
            gone = [h for h in names if isinstance(h, str) and HASH_RE.fullmatch(h) and store.path(h) is not None]
            for h in gone:
                store.path(h).unlink(missing_ok=True)
            if gone:
                # it can't check the purge (sealed): it keeps a record of who asked, and why
                with open(m.root / "forgotten.jsonl", "a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps({"by": who, "purge": purge_id, "objects": gone,
                                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
            return self._json(200, {"forgotten": len(gone)})
        if len(segs) == 3 and segs[0] == "events" and segs[1] in FEEDS:
            feed, origin = segs[1], segs[2]
            if not is_origin(origin):
                return self._json(404, {"error": "no such log"})
            if cmd == "GET":
                start = int(q.get("from", ["0"])[0])
                limit = min(int(q.get("limit", [str(BATCH)])[0]), BATCH)
                return self._json(200, {"events": read_events(m, feed, origin, start, limit)})
            if cmd == "POST":
                req = json.loads(body)
                if is_sealed(m) and feed != "pool":
                    n = append_sealed(m, feed, origin, int(req["from"]), req["events"])
                else:
                    n = append_remote(m, feed, origin, int(req["from"]), req["events"])
                if self.srv.on_change:
                    self.srv.on_change(m, feed)
                else:
                    after_arrival(m, [feed])
                return self._json(200, {"count": n})
        if segs == ["missing"] and cmd == "POST":
            store = store_only(m)
            hashes = json.loads(body)["hashes"]
            if not isinstance(hashes, list) or len(hashes) > 10000:
                raise ValueError("hashes must be a list of at most 10000")
            return self._json(200, {"missing": [h for h in hashes
                                                if isinstance(h, str) and HASH_RE.fullmatch(h) and not store.has(h)]})
        if len(segs) == 2 and segs[0] == "objects" and HASH_RE.fullmatch(segs[1]):
            h, store = segs[1], store_only(m)
            if cmd == "GET":
                p = store.path(h)
                if p is None:
                    return self._json(404, {"error": "no such object"})
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(p.stat().st_size))
                self.end_headers()
                with open(p, "rb") as f:
                    while block := f.read(1 << 20):
                        self.wfile.write(block)
                return
            if cmd == "PUT":
                from . import purge
                if h in purge.purged_objects(m):
                    self.close_connection = True
                    return self._json(410, {"error": "purged from this matter"})
                n = int(self.headers.get("Content-Length", 0))
                if is_sealed(m):
                    # sealed: named by a blinded hash the hub can't check; members do, on reading
                    _put_named(store, h, _Limited(self.rfile, n))
                    return self._json(200, {"stored": h})
                got = store.put_stream(_Limited(self.rfile, n))
                if got != h:
                    return self._json(409, {"error": f"the object's bytes hash to {got}, not {h}"})
                return self._json(200, {"stored": h})
        return self._json(404, {"error": "no such route"})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()


def _put_named(store: ObjectStore, name: str, src):
    """Store a sealed object under its blinded name, once: what's there stays."""
    import os
    p = store._path(store.roots[0], name)
    if p.exists():
        while src.read(1 << 20):
            pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{secrets.token_hex(6)}.tmp")
    with open(tmp, "wb") as f:
        while block := src.read(1 << 20):
            f.write(block)
    os.replace(tmp, p)


def _redact_sealed(m: Matter, who: str, envs: list) -> int:
    """Replace a sealed hub's lines with their redacted form: same ID, same
    hash, the content sealed afresh. Under each log's lock."""
    import os
    from .matter import same_file
    want: dict[tuple, dict] = {}
    for e in envs:
        if (isinstance(e, dict) and e.get("feed") == "annotations" and is_origin(str(e.get("origin")))
                and isinstance(e.get("id"), str) and isinstance(e.get("h"), str) and isinstance(e.get("sealed"), str)
                and len(e["sealed"]) < 8 << 20):
            want[(e["origin"], e["id"])] = e
    n = 0
    for origin in {o for o, _ in want}:
        path = m.annotation_log.log_for(origin).path
        if not path.exists():
            continue
        while True:
            with open(path, "r+", encoding="utf-8", newline="\n") as f, locked(f):
                if not same_file(f, path):
                    continue
                lines, out, changed = f.read().splitlines(keepends=True), [], False
                for line in lines:
                    env = json.loads(line)
                    new = want.get((origin, env.get("id")))
                    if new and new["h"] == env.get("h") and new["sealed"] != env.get("sealed"):
                        e = new.get("e", 0) if isinstance(new.get("e", 0), int) else 0
                        out.append(json.dumps({**env, "sealed": new["sealed"], "e": e}) + "\n")
                        changed = True
                        n += 1
                    else:
                        out.append(line)
                if changed:
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text("".join(out), encoding="utf-8", newline="\n")
                    os.replace(tmp, path)
                break
    return n


class _Limited:
    """A file object that reads at most ``n`` bytes of another."""

    def __init__(self, f, n):
        self.f, self.left = f, n

    def read(self, size=-1):
        if self.left <= 0:
            return b""
        size = self.left if size < 0 else min(size, self.left)
        data = self.f.read(size)
        self.left -= len(data)
        return data


# -- the client --------------------------------------------------------------------------------------


def split_address(address: str) -> tuple[str, int]:
    host, _, port = address.rpartition(":")
    if not host:
        return address, PORT
    return host.strip("[]"), int(port)


class _Connection(http.client.HTTPSConnection):
    """HTTPS to one origin: SNI names the matter, and the server's key must
    hash to the origin. Every (re)connect checks, including the ones
    http.client makes on its own after the server closes a connection."""

    def __init__(self, host, port, origin, sni, timeout):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE            # no authority: we check the key ourselves
        super().__init__(host, port, context=ctx, timeout=timeout)
        self.ctx, self.origin, self.sni = ctx, origin, sni

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        tls = self.ctx.wrap_socket(sock, server_hostname=self.sni)
        got, _ = cert_origin(tls.getpeercert(binary_form=True))
        if got != self.origin:
            tls.close()
            raise SyncError(f"{self.host}:{self.port} is {got}, not {self.origin}")
        self.sock = tls


class Peer:
    """A connection to one member, checked to be the origin we meant.
    (``sni`` and ``base`` reach a hub's own routes instead.)"""

    def __init__(self, m: Matter, address: str, origin: str, timeout=60, sni=None, base=None):
        self.m, self.origin = m, origin
        self.host, self.port = split_address(address)
        self.timeout = timeout
        self.k = None                   # the matter key, when this is a sealed hub (adhocdocs.seal)
        self.sni = sni or sni_name(pool_key(m))
        self.base = base or f"/v1/{urllib.parse.quote(pool_key(m))}"
        self.conn = None

    def _connect(self):
        self.conn = _Connection(self.host, self.port, self.origin, self.sni, self.timeout)
        self.conn.connect()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def request(self, method, route, obj=None, raw=None, stream=None, length=None):
        path = f"{self.base}/{route}"
        body = json.dumps(obj).encode() if obj is not None else raw
        signed = None if stream is not None else (body if body is not None else b"")
        headers = {"Authorization": "Adhoc " + auth_header(self.m, method, path, signed, self.origin)}
        if obj is not None:
            headers["Content-Type"] = "application/json"
        for attempt in (0, 1):
            if self.conn is None:
                self._connect()
            try:
                if stream is not None:
                    headers["Content-Length"] = str(length)
                    self.conn.request(method, path, body=stream, headers=headers)
                else:
                    self.conn.request(method, path, body=body, headers=headers)
                return self.conn.getresponse()
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError):
                self.close()
                if attempt or stream is not None:
                    raise
                headers["Authorization"] = "Adhoc " + auth_header(self.m, method, path, signed, self.origin)

    def json(self, method, route, obj=None):
        r = self.request(method, route, obj)
        data = r.read()
        out = json.loads(data) if data else {}
        if r.status != 200:
            raise SyncError(f"{method} {route}: {r.status} {out.get('error', '')}")
        return out

    def fetch_object(self, h: str, store: ObjectStore):
        from . import seal
        if not self.k:
            r = self.request("GET", f"objects/{h}")
            if r.status != 200:
                r.read()
                raise SyncError(f"the peer doesn't have {h}")
            got = store.put_stream(r)
        else:
            # sealed under some epoch's key: the newest first
            for epoch in sorted(self.k.keys, reverse=True):
                r = self.request("GET", f"objects/{seal.blind(self.k, h, epoch)}")
                if r.status == 200:
                    break
                r.read()
            else:
                raise SyncError(f"the hub doesn't have {h}")
            try:
                got = store.put_stream(seal.Unsealer(self.k, h, r, epoch))
            except Exception as e:
                raise SyncError(f"{h} from the hub doesn't decrypt: {type(e).__name__}")
        if got != h:
            raise SyncError(f"the peer sent bytes hashing to {got} for {h}")

    def send_object(self, h: str, store: ObjectStore):
        from . import seal
        p = store.path(h)
        with open(p, "rb") as f:
            if self.k:
                size = p.stat().st_size
                r = self.request("PUT", f"objects/{seal.blind(self.k, h)}", stream=seal.Sealer(self.k, h, f, size),
                                 length=seal.sealed_size(size))
            else:
                r = self.request("PUT", f"objects/{h}", stream=f, length=p.stat().st_size)
            out = json.loads(r.read() or b"{}")
        if r.status != 200:
            raise SyncError(f"PUT {h}: {r.status} {out.get('error', '')}")


def _pull_log(peer: Peer, m: Matter, feed: str, origin: str, mine: int, theirs: int, pool) -> int:
    store = store_only(m)
    got = 0
    while mine < theirs:
        events = peer.json("GET", f"events/{feed}/{origin}?from={mine}&limit={BATCH}")["events"]
        if not events:
            break
        if peer.k and feed != "pool":
            from . import seal
            try:
                events = [seal.unseal_event(peer.k, e) for e in events]
            except Exception as e:
                raise BadLog(f"{feed}/{origin}: a sealed event from the hub doesn't open ({type(e).__name__})")
        # check before fetching anything they point at
        key = known_key(m, origin, pool, events)
        if key is None:
            raise SyncError(f"{origin} isn't a member of this matter's pool")
        from . import purge
        docs, contents, objects = _purged(m, feed, origin, events, pool)
        check_chain(feed_of(m, feed).log_for(origin).last_event(), events, key, f"{feed}/{origin}",
                    lambda ev: purge.may_hide(ev, docs))
        need = [h for ev in events for h in _refs(feed, ev, docs, contents, objects)]
        seen = set()
        while need:
            h = need.pop()
            if h in seen or h in objects:
                continue
            seen.add(h)
            if not store.has(h):
                peer.fetch_object(h, store)
            data = _small_object(store, h)
            if data:
                need += nested_refs(data)
        mine = append_remote(m, feed, origin, mine, events, pool)
        got += len(events)
    return got


def _push_log(peer: Peer, m: Matter, feed: str, origin: str, theirs: int, mine: int) -> int:
    store = store_only(m)
    sent = 0
    while theirs < mine:
        events = read_events(m, feed, origin, theirs, BATCH)
        objs = closure(store, [h for ev in events for h in direct_refs(feed, ev)])
        if peer.k:
            from . import seal
            # under any epoch's key, it's there already
            names = {seal.blind(peer.k, h, e): h for h in objs for e in peer.k.keys}
        else:
            names = {h: h for h in objs}
        listed = list(names)
        missing: dict[str, int] = {}
        for i in range(0, len(listed), 5000):
            for n in peer.json("POST", "missing", {"hashes": listed[i:i + 5000]})["missing"]:
                missing[names[n]] = missing.get(names[n], 0) + 1
        per = len(peer.k.keys) if peer.k else 1
        for h, n in missing.items():
            if n == per:            # missing under every name
                peer.send_object(h, store)
        if peer.k and feed != "pool":
            prev = read_events(m, feed, origin, theirs - 1, 1)[0] if theirs else None
            sealed = []
            for ev in events:
                sealed.append(seal.seal_event(peer.k, ev, prev))
                prev = ev
            events = sealed
        theirs = peer.json("POST", f"events/{feed}/{origin}", {"from": theirs, "events": events})["count"]
        sent += len(events)
    return sent


def sync_with(m: Matter, address: str, origin: str) -> dict:
    """Bring this installation and one member level: each gets the events
    (and objects) the other has and it lacks. Membership first.

    A log that doesn't check (a bad signature, a broken chain, a fork) is
    left as it is on both sides and reported under ``refused``; the other
    logs still sync, since each is checked on its own."""
    peer = Peer(m, address, origin)
    pulled, pushed, refused = {}, {}, []

    def attempt(what, fn):
        try:
            return fn()
        except (BadLog, SyncError) as e:
            if isinstance(e, SyncError) and " 403 " in str(e):
                raise
            refused.append({"log": what, "error": str(e)})
            return 0

    try:
        said = peer.json("GET", "vector").get("sealed")
        pinned = poolmod.Pool.load(m).members.get(origin, {}).get("sealed")
        if pinned and not said:
            # the pool says this hub holds the matter sealed: it doesn't get to stop on its own
            raise SyncError("the pool says this hub holds the matter sealed, and it now says it doesn't: not syncing "
                            "with it (adhocdocs.seal)")
        if said or pinned:
            # a sealed hub: what goes is sealed, what comes is opened and checked here (adhocdocs.seal)
            from . import seal
            try:
                peer.k = seal.Keyring.of(m)
            except seal.NoKey as e:
                raise SyncError(str(e))
        for feed in FEEDS:
            # membership can take rounds: an origin's certificate may arrive in another's log
            for _ in range(10 if feed == "pool" else 1):
                theirs = peer.json("GET", "vector")["vector"]
                pool = poolmod.Pool.load(m)
                mine = counts(m)
                progress = 0
                for o, n in sorted(theirs.get(feed, {}).items()):
                    have, what = mine[feed].get(o, 0), f"{feed}/{o}"
                    if n <= have or any(r["log"] == what for r in refused):
                        continue
                    if feed != "pool" and pool.cert(o) is None:
                        continue
                    if feed == "pool" and known_key(m, o, pool) is None and o != pool.founder:
                        continue
                    k = attempt(what, lambda: _pull_log(peer, m, feed, o, have, n, pool))
                    if k:
                        pulled[what] = pulled.get(what, 0) + k
                        progress += k
                if not progress:
                    break
            if feed == "pool" and peer.k:
                from . import seal
                peer.k = seal.Keyring.of(m)         # a key boxed for this one may just have arrived
            reply = peer.json("GET", "vector")
            theirs, their_heads = reply["vector"], reply.get("heads", {})
            mine_heads = heads(m)[feed]
            for o, n in sorted(counts(m)[feed].items()):
                what, t = f"{feed}/{o}", theirs.get(feed, {}).get(o, 0)
                if any(r["log"] == what for r in refused):
                    continue
                if n > t:
                    k = attempt(what, lambda: _push_log(peer, m, feed, o, t, n))
                    if k:
                        pushed[what] = k
                elif n == t and their_heads.get(feed, {}).get(o) not in (None, mine_heads.get(o)):
                    refused.append({"log": what, "error": f"{what}: the log has forked: the same number of events, "
                                                         "ending differently"})
        if peer.k:
            # a sealed hub can't read purges: say what they deleted and redacted
            from . import purge, seal
            rec = purge.record(m)
            if rec["applied"]:
                envs = []
                for o in m.annotation_log.vector():
                    prev = None
                    for ev in m.annotation_log.log_for(o).events():
                        if has_hidden(ev):
                            envs.append({"feed": "annotations", "origin": o, **seal.seal_event(peer.k, ev, prev)})
                        prev = ev
                if envs:
                    peer.json("POST", "redact", {"envelopes": envs})
                if rec["objects"]:
                    peer.json("POST", "forget", {"objects": [seal.blind(peer.k, h, e) for h in rec["objects"]
                                                         for e in peer.k.keys],
                                                 "purge": rec["applied"][-1]})
    finally:
        peer.close()
    if pulled:
        after_arrival(m, {k.split("/")[0] for k in pulled})
    return {"peer": origin, "address": address, "pulled": pulled, "pushed": pushed,
            **({"refused": refused} if refused else {})}


def sync_all(m: Matter) -> list[dict]:
    """Sync with every active member that says where it listens."""
    from . import lan
    p = poolmod.Pool.load(m)
    near = lan.found(m)                 # found on the local network (adhocdocs.lan)
    out = []
    for x in p.active():
        if x["origin"] == m.origin:
            continue
        addresses = list(dict.fromkeys([*x["addresses"], *([near[x["origin"]]] if x["origin"] in near else [])]))
        if not addresses:
            out.append({"peer": x["origin"], "name": x["name"], "skipped": "it doesn't say where it listens"})
            continue
        errors = []
        for address in addresses:
            try:
                out.append({**sync_with(m, address, x["origin"]), "name": x["name"]})
                break
            except (OSError, SyncError, BadLog, ssl.SSLError) as e:
                errors.append(f"{address}: {e}")
        else:
            out.append({"peer": x["origin"], "name": x["name"], "failed": errors})
    return out
