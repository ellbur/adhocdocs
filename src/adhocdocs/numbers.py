"""Reserving Bates and exhibit numbers before they're used (SYNC.md).

Productions and exhibit sets number documents in series (``bates:HFS``,
``exhibit:Exhibit ``) that must not repeat across a matter's installations.
So before numbering, a production takes the range it needs with a
``reserve-numbers`` event in the annotation feed, which syncs: other
installations see it, and their next production starts after it. When two
installations reserve overlapping ranges before hearing of each other, the
replay keeps the earlier and reports the later (``adhoc sync conflicts``).

With a hub in the pool, reserving syncs with it first (to hear of others'
reservations) and after (to tell them, and to check that no one took the
same numbers meanwhile), which makes a clash unlikely: two people would
have to reserve within the same moment. Without one, a clash is found when
installations next sync.

Productions and sets made before reservations existed are reserved the
first time their series is used again, so the others learn of them.

In a session, reserving goes through the annotation API (stamped
``claude``), which does the syncing on the host.
"""

import getpass
import os

from . import annotations
from .annotations import Refused
from .matter import Matter


def bates_series(prefix: str) -> str:
    return f"bates:{prefix}"


def exhibit_series(prefix: str) -> str:
    return f"exhibit:{prefix}"


def _sync_hubs(m: Matter) -> list:
    """Sync with the pool's hubs, if any can be reached. Best effort."""
    from . import pool, peer
    try:
        p = pool.Pool.load(m)
    except Exception:
        return []
    out = []
    for x in p.active():
        if x["role"] != "hub" or x["origin"] == m.origin or not x["addresses"]:
            continue
        for address in x["addresses"]:
            try:
                out.append(peer.sync_with(Matter(m.root), address, x["origin"]))
                break
            except Exception:
                continue
    return out


def reserve(m: Matter, author: str, series: str, count: int, start: int | None = None, name: str | None = None,
            local=(), requested_by=None) -> dict:
    """Reserve ``count`` numbers in ``series``: from ``start``, or the next
    free number. ``local``: ``(first, last, name)`` ranges this installation
    used before reservations existed, reserved first if nothing covers them.
    Host only. Returns ``{"reservation", "first", "last", "synced"}``."""
    if count < 1:
        raise Refused("nothing to number")
    synced = bool(_sync_hubs(m))
    m = Matter(m.root)
    ann = annotations.Annotations.load(m)
    for first, last, used_by in local:
        if not any(r["first"] <= first and last <= r["last"] for r in ann.ranges(series)):
            try:
                annotations.write(m, author, {"op": "reserve-numbers", "series": series, "first": first, "last": last,
                                              "name": used_by})
            except Refused:
                pass        # someone else reserved those meanwhile: the conflict will show
    ann = annotations.Annotations.load(Matter(m.root))
    first = start if start is not None else ann.next_number(series)
    req = {"op": "reserve-numbers", "series": series, "first": first, "last": first + count - 1,
           **({"name": name} if name else {}), **({"requested_by": requested_by} if requested_by else {})}
    (ev,) = annotations.write(Matter(m.root), author, req)
    if synced and _sync_hubs(m):
        ann = annotations.Annotations.load(Matter(m.root))
        if ev["id"] not in ann.reservations:
            clash = next((c for c in ann.conflicts if c["event"] == ev["id"]), {})
            raise Refused(f"{series.split(':', 1)[1]} {first}–{first + count - 1} were reserved on another "
                          f"installation at the same time ({clash.get('reason', 'overlap')}); run it again")
    return {"reservation": ev["id"], "series": series, "first": first, "last": first + count - 1, "synced": synced}


def release(m: Matter, author: str, reservation: str) -> dict:
    (ev,) = annotations.write(m, author, {"op": "release-numbers", "reservation": reservation})
    return {"released": reservation, "event": ev["id"]}


def _in_session(m: Matter) -> bool:
    return bool(os.environ.get("ADHOC_ANN_SOCKET")) and not os.access(m.root / "annotations", os.W_OK)


def host_author() -> str:
    return "user:" + (os.environ.get("ADHOC_USER") or getpass.getuser())


def reserve_here(m: Matter, series: str, count: int, start=None, name=None, local=()) -> dict:
    """Reserve from wherever this runs: through the annotation API in a
    session, directly on the host (as the person running it)."""
    if _in_session(m):
        from . import annapi
        return annapi.request(m, {"series": series, "count": count, "start": start, "name": name,
                                  "local": [list(x) for x in local]}, "/reserve")
    return reserve(m, host_author(), series, count, start, name, local)


def release_here(m: Matter, reservation: str) -> dict:
    if _in_session(m):
        from . import annapi
        return annapi.request(m, {"reservation": reservation}, "/release")
    return release(m, host_author(), reservation)
