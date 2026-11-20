"""Originals on demand, for a limited copy (docs/SYNC.md, limited copies).

A limited copy holds every log, metadata object and text layer, but not
the originals: it fetches one when something reads it, from a member that
holds them all, and keeps it in a **cache** with a size limit
(``cache_limit_bytes`` in ``matter.toml``, 10 GB by default; ``adhoc sync copy
limited --cache-gb N``).

The cache's ledger is ``sync/cache.json``: for each original it may let go
of, its size, when it was last used, and whether it was fetched or ingested
here and since **held** by a full member. Only those can be evicted, least
recently used first, and never:

- an original no full member is known to hold (ingested here, not yet
  synced to one): it isn't in the ledger until a full member has it;
- one kept with ``adhoc fetch`` (pinned);
- bytes a text layer is made of (a text file's layer text is its original's
  very bytes): a limited copy keeps all text, so indexes build locally.

Fetching happens on the host, which can reach the other members. Every
reader goes through the matter's object store, which calls
:func:`on_demand` for an original it lacks. In a session (the store
read-only, no network but Claude's), that asks the host through the
annotation API (``POST /fetch``), and then reads the original from the
store as mounted. The viewer, on the host, runs ``adhoc fetch-original``.
"""

import json
import os
import time
from pathlib import Path

from .matter import Matter
from .portable import locked

DEFAULT_LIMIT_GB = 10
FAILED_RETRY = 30           # seconds before an original that couldn't be fetched is tried again


class Unavailable(Exception):
    """The original isn't here and couldn't be fetched."""


# -- what this copy is ----------------------------------------------------------------------------


def limited(m: Matter) -> bool:
    from . import peer
    return not peer.holds_all(m)


def limit_bytes(m: Matter) -> int:
    v = m.config.get("cache_limit_bytes")
    return v if isinstance(v, int) and v >= 0 else DEFAULT_LIMIT_GB << 30


def set_limit(m: Matter, gb: float):
    """The cache's room, in GB (kept in ``matter.toml`` as bytes)."""
    from .matter import write_toml
    if not gb > 0:
        raise ValueError("the cache limit must be more than 0")
    cfg = m.config
    cfg["cache_limit_bytes"] = int(gb * (1 << 30))
    write_toml(m.root / "matter.toml", cfg)


def is_original(m: Matter, h: str) -> bool:
    return h in set(m.state.documents.values())


def protected(m: Matter) -> set[str]:
    """Bytes that are also a text layer's text: never evicted."""
    return {l.text for ls in m.state.layers.values() for l in ls}


# -- the ledger -----------------------------------------------------------------------------------


class Ledger:
    """``sync/cache.json``, read and written under ``sync/cache.lock``."""

    def __init__(self, m: Matter):
        self.m = m
        self.path = m.sync_dir / "cache.json"

    def __enter__(self):
        self.m.sync_dir.mkdir(parents=True, exist_ok=True)
        self._f = open(self.m.sync_dir / "cache.lock", "w", encoding="utf-8", newline="\n")
        self._lock = locked(self._f)
        self._lock.__enter__()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            data = {}
        self.entries: dict = data.get("entries", {}) if isinstance(data, dict) else {}
        self.pinned: set = set(data.get("pinned", [])) if isinstance(data, dict) else set()
        self.failed: dict = data.get("failed", {}) if isinstance(data, dict) else {}
        return self

    def __exit__(self, *exc):
        try:
            if exc[0] is None:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"v": 1, "entries": self.entries, "pinned": sorted(self.pinned),
                                           "failed": self.failed}, indent=1, sort_keys=True) + "\n",
                               encoding="utf-8", newline="\n")
                tmp.replace(self.path)
        finally:
            self._lock.__exit__(None, None, None)
            self._f.close()


def read_ledger(m: Matter) -> dict:
    try:
        return json.loads((m.sync_dir / "cache.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"entries": {}, "pinned": []}


def _record(led: Ledger, h: str, how: str, size: int):
    e = led.entries.get(h)
    led.entries[h] = {"how": e["how"] if e else how, "size": size, "used": time.time()}


def touch(m: Matter, h: str):
    """An original in the cache was just used: it's the last to go."""
    if h not in read_ledger(m).get("entries", {}):
        return
    try:
        with Ledger(m) as led:
            if h in led.entries:
                led.entries[h]["used"] = time.time()
    except OSError:
        pass        # read-only here: the host keeps the ledger


def mark_held(m: Matter, hashes) -> int:
    """These originals, here, are held by a full member too: they can be let
    go of when the cache is full. Returns how many were new to the ledger."""
    from .peer import store_only
    store, n = store_only(m), 0
    hashes = [h for h in hashes if store.has(h)]
    if not hashes:
        return 0
    with Ledger(m) as led:
        for h in hashes:
            if h not in led.entries:
                led.entries[h] = {"how": "held", "size": store.path(h).stat().st_size, "used": time.time()}
                n += 1
    return n


# -- fetching -------------------------------------------------------------------------------------


def sources(m: Matter) -> list[dict]:
    """The full members to fetch from, hubs first: each with every address it
    might be reached at."""
    from . import lan, pool as poolmod
    p = poolmod.Pool.load(m)
    near = lan.found(m)
    out = []
    for x in p.active():
        if x["origin"] == m.origin or not p.holds_all(x["origin"]):
            continue
        addresses = list(dict.fromkeys([*x["addresses"], *([near[x["origin"]]] if x["origin"] in near else [])]))
        if addresses:
            out.append({**x, "addresses": addresses})
    return sorted(out, key=lambda x: (x["role"] != "hub", x["name"]))


def _from(m: Matter, x: dict, address: str, hashes: list[str]) -> list[str]:
    """Fetch what of ``hashes`` this member has, from one address."""
    from . import seal
    from .peer import NotHeld, Peer, store_only
    conn = Peer(m, address, x["origin"])
    got = []
    try:
        if x.get("sealed"):
            conn.k = seal.Keyring.of(m)
        store = store_only(m)
        for h in hashes:
            if store.has(h):
                got.append(h)
                continue
            try:
                conn.fetch_object(h, store)
                got.append(h)
            except NotHeld:
                continue
    finally:
        conn.close()
    return got


def fetch(m: Matter, hashes, pin=False) -> dict:
    """Bring these originals here (host only), from the full members, and
    record them in the cache, which then makes room. Returns ``{"fetched",
    "here", "unavailable": [{"content", "why"}]}``."""
    from .peer import store_only
    m = Matter(m.root)
    store = store_only(m)
    hashes = list(dict.fromkeys(hashes))
    here = [h for h in hashes if store.has(h)]
    todo = [h for h in hashes if h not in here]
    originals = set(m.state.documents.values())
    errors: dict[str, str] = {}
    for h in [h for h in todo if h not in originals]:
        errors[h] = "not an original in this matter"
    todo = [h for h in todo if h in originals]
    fetched: list[str] = []
    for x in sources(m) if todo else []:
        for address in x["addresses"]:
            try:
                got = _from(m, x, address, todo)
            except Exception as e:
                for h in todo:
                    errors.setdefault(h, f"{x['name']} at {address}: {e}")
                continue
            fetched += got
            todo = [h for h in todo if h not in got]
            break
        if not todo:
            break
    if todo and not sources(m):
        for h in todo:
            errors[h] = "no member that holds every original says where it listens"
    for h in todo:
        errors.setdefault(h, "no member that holds every original has it")
    now = time.time()
    with Ledger(m) as led:
        for h in fetched:
            _record(led, h, "fetched", store.path(h).stat().st_size)
            led.failed.pop(h, None)
        for h in here:
            if h in led.entries:
                led.entries[h]["used"] = now
        for h in todo:
            led.failed[h] = now
        if pin:
            led.pinned |= set(hashes)
    evict(m, keep=set(hashes))
    return {"fetched": fetched, "here": here,
            "unavailable": [{"content": h, "why": errors[h]} for h in hashes if h in errors]}


def evict(m: Matter, keep=()) -> list[str]:
    """Let go of the least recently used originals until the cache is under
    its limit. Only what the ledger lists (held by a full member), never what's
    pinned, in ``keep``, or a text layer's bytes. Only on a limited copy."""
    from .peer import store_only
    if not limited(m):
        return []
    store, limit, gone = store_only(m), limit_bytes(m), []
    with Ledger(m) as led:
        for h in [h for h in led.entries if not store.has(h)]:
            del led.entries[h]                  # purged, or deleted by hand
        total = sum(e["size"] for e in led.entries.values())
        if total <= limit:
            return []
        safe = protected(m)
        for h, e in sorted(led.entries.items(), key=lambda kv: kv[1]["used"]):
            if total <= limit:
                break
            if h in led.pinned or h in keep or h in safe:
                continue
            p = store.path(h)
            if p is not None:
                p.unlink()
            del led.entries[h]
            total -= e["size"]
            gone.append(h)
    return gone


def pin(m: Matter, docs, unpin=False) -> dict:
    """Keep these documents' originals (and their attachments') here, fetching
    what's missing; or stop keeping them."""
    m = Matter(m.root)
    s = m.state
    todo, seen = list(docs), set()
    while todo:
        d = todo.pop()
        if d in seen or d not in s.documents:
            continue
        seen.add(d)
        todo += s.children.get(d, [])
    hashes = sorted({s.documents[d] for d in seen if d not in s.purged})
    if unpin:
        with Ledger(m) as led:
            led.pinned -= set(hashes)
        evict(m)
        return {"unpinned": len(hashes)}
    out = fetch(m, hashes, pin=True)
    return {"pinned": len(hashes), "fetched": len(out["fetched"]), "unavailable": out["unavailable"]}


def status(m: Matter) -> dict:
    from .peer import missing_originals
    led = read_ledger(m)
    entries = led.get("entries", {})
    return {"limit_gb": limit_bytes(m) / (1 << 30),
            "cached_gb": round(sum(e["size"] for e in entries.values()) / (1 << 30), 3),
            "cached": len(entries), "pinned": len(led.get("pinned", [])),
            "originals_missing": len(missing_originals(m))}


# -- the store's hook -----------------------------------------------------------------------------


def _on_host(m: Matter) -> bool:
    """The host can reach members and write the store; a session can't."""
    return (m.sync_dir / "origin.json").exists() and os.access(m.store_dir / "objects", os.W_OK)


def on_demand(m: Matter, h: str) -> Path | None:
    """Called by the matter's object store for an object it lacks. If this
    is a limited copy and ``h`` an original, fetch it (on the host) or ask the
    host to (in a session); the object's path, or None."""
    if not h.startswith("sha256:") or not (m.root / "pool" / "logs").is_dir():
        return None
    try:
        if not limited(m) or not is_original(m, h):
            return None
    except Exception:
        return None
    if _on_host(m):
        if time.time() - read_ledger(m).get("failed", {}).get(h, 0) < FAILED_RETRY:
            return None
        try:
            fetch(m, [h])
        except Exception:
            return None
    else:
        from . import annapi
        try:
            annapi.request(m, {"content": h}, "/fetch")
        except Exception:
            return None
    from .peer import store_only
    return store_only(m).path(h)


def describe_missing(m: Matter, h: str) -> str:
    """Why an original can't be read, for an error."""
    try:
        if limited(m):
            return (f"{h}: this is a limited copy, and the original couldn't be fetched from a member that holds them "
                    "all (is the hub reachable?). Its text is here. To work through many originals, the user can "
                    "fetch them first, on the host: adhoc fetch --help")
    except Exception:
        pass
    return f"{h}: the original isn't in the store"


def original_path(m: Matter, h: str) -> Path:
    """Where an original is, fetching it if this copy lacks it; Unavailable,
    saying why, if it can't be had."""
    p = m.objects.path(h)
    if p is None:
        raise Unavailable(describe_missing(m, h))
    return p
