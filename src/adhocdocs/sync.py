"""Syncing a matter across installations (SYNC.md).

What's here so far works on one installation's disk: what it holds from
each origin, and the conflicts that replaying every origin's events turned
up. Sync itself (the pool, the protocol) builds on these.
"""

from . import annotations, work
from .matter import Matter

SYNCED_FEEDS = ("pool", "store", "annotations", "work")


def feeds(m: Matter) -> dict:
    """The synced feeds, membership first: it says whose events to accept."""
    return {"pool": m.pool_log, "store": m.logs["store"], "annotations": m.annotation_log, "work": m.work_log}


def vector(m: Matter) -> dict:
    """For each synced feed, how many events this installation holds from each
    origin. Two installations with the same vector hold the same logs."""
    return {name: f.vector() for name, f in feeds(m).items()}


def status(m: Matter) -> dict:
    v = vector(m)
    origins = sorted({o for per in v.values() for o in per})
    me = m.identity.origin if (m.sync_dir / "origin.json").exists() else None
    return {"matter": m.id, "origin": me,
            "origins": [{"origin": o, "this": o == me, **{f: v[f].get(o, 0) for f in SYNCED_FEEDS}} for o in origins]}


def conflicts(m: Matter, refresh=True) -> list[dict]:
    """Events that replay as something other than themselves, with why.
    ``refresh`` also brings ``annotations/replay.json`` (what the viewer
    follows) up to date, where it can be written."""
    ann = annotations.Annotations.load(m)
    if refresh:
        try:
            annotations.save_replay(m, ann)
        except OSError:
            pass        # read-only here (a session): the host keeps it current
    return ann.conflicts + work.forks(m) + disputes(m)


def disputes(m: Matter) -> list[dict]:
    """People claimed by installations that don't vouch for each other: one
    side is writing as someone it isn't (adhocdocs.pool)."""
    from . import pool
    if not pool.founded(m):
        return []
    p = pool.Pool.load(m)
    out = []
    for user, groups in sorted(p.disputed.items()):
        names = [", ".join(p.members[o]["name"] for o in g if o in p.members) for g in groups]
        out.append({"kind": "person-disputed", "author": user, "groups": groups,
                    "reason": f"{user} is claimed separately by {' and by '.join(names)}: one of them isn't who it "
                              "says. Everything both write as them counts until it's settled; remove the "
                              "installation that isn't theirs (adhoc sync remove)."})
    return out


def refresh(m: Matter):
    """After logs arrive from another installation: re-derive what depends on
    the merged order (the viewer's replay file, ``work/``). Indexes catch up
    as usual."""
    from . import pool, purge
    try:
        pool.save_cutoffs(m)
    except OSError:
        pass
    purge.enforce(Matter(m.root))
    m = Matter(m.root)
    annotations.save_replay(m)
    work.materialize(m)
