"""Purging documents from a matter, on every installation (SYNC.md).

Sometimes documents must go: clawed back after being produced by mistake,
or destroyed under a protective order when the case ends. Withdrawing hides
a document; purging deletes it. A person (never Claude) runs ``adhoc purge``
on the host, which records a ``purge`` event in the store feed, signed and
synced like any other. Every installation that has the event, and the hub,
then:

- **deletes the documents' objects**: originals, text layers and their
  parts, metadata, and layers made from them in the workspace (``layers
  ocr``), for each document and its attachments. Text another live
  document also has stays;
- **redacts the annotation events that name them** (tags, notes, entities'
  references), since those can hold quoted words: each becomes a tombstone
  that keeps only its ID and hash, so signed logs still check
  (:func:`adhocdocs.signing.tombstone`);
- **treats them as gone for good**: out of the matter's state, its indexes
  and the viewer; not brought back by ingesting the same file again; their
  objects never fetched or accepted by sync again.

What a purge can't reach is what the matter doesn't track: the workspace
(drafts, scripts, exports, productions made there), Claude's transcripts in
``claude-home``, and copies made outside adhocdocs. Each installation gets
a **notice** (``pool/purges.json``, shown when a session opens and by
``adhoc purges``) listing the workspace files and published work that
mention the documents, so the person there can take whatever steps are
right.

Duplicates: a document whose original another live document shares (the
same file from another custodian) can't be purged alone, since the shared
original is the thing to delete; ``--with-duplicates`` purges them all.

The event lists the documents, every object to delete and every event to
redact, worked out by the installation that purges. Others do exactly
that, no more.
"""

import json
import os
import re
from pathlib import Path

from .annotations import Refused
from .matter import Matter, now
from .portable import locked
from .signing import canonical, event_hash, is_tombstone, tombstone

LAYER_PARTS = ("text", "offset_map", "segments", "geometry", "presentation")


# -- what a purge removes ---------------------------------------------------------------------


def plan(m: Matter, docs, with_duplicates=False) -> dict:
    state = m.state
    want: list[str] = []

    def add(d):
        if d in want:
            return
        want.append(d)
        for c in state.children.get(d, []):          # attachments go with their parent
            add(c)

    for d in docs:
        d = state.follow(d)
        if d not in state.documents:
            raise Refused(f"no document {d}")
        add(d)
    while True:                                       # documents sharing an original
        others = [o for d in list(want) for o in state.by_content.get(state.documents[d], []) if o not in want]
        if not others:
            break
        if not with_duplicates:
            raise Refused(f"{len(others)} other document(s) have the same original ({', '.join(o[:19] for o in others[:3])}"
                          f"{'…' if len(others) > 3 else ''}); purge them too with --with-duplicates")
        for o in others:
            add(o)
    contents = {state.documents[d] for d in want}
    gone = set(want)

    def parts(c):
        """A content's objects: it, its layers, and their parts."""
        out = [c]
        for l in state.layers.get(c, []):
            out.append(l.id)
            try:
                lo = m.objects.get_object(l.id)
            except KeyError:
                continue
            out += [lo[k] for k in LAYER_PARTS if lo.get(k)]
        return out

    # what a live document still uses stays (a plain text file's original is
    # its text, and two documents can have the same text or segments)
    ours = {h for c in contents for h in parts(c)}
    kept = set()
    for d, c in state.documents.items():
        if d in gone or d in state.withdrawn:
            continue
        kept |= {d, state.metadata.get(d)}
        if c in ours or any(l.text in ours for l in state.layers.get(c, [])):
            kept |= set(parts(c))
    objects: list[str] = []
    for h in [x for d in want for x in (d, state.metadata.get(d))] + [h for c in contents for h in parts(c)]:
        if h and h not in kept and h not in objects:
            objects.append(h)
    ids = [d.removeprefix("sha256:") for d in want]
    redact = [event_hash(ev) for ev in m.annotation_log.read()
              if not is_tombstone(ev) and any(i in canonical(ev).decode() for i in ids)]
    return {"docs": want, "objects": objects, "redact": redact}


def purge(m: Matter, author: str, docs, reason: str, with_duplicates=False) -> dict:
    """Purge documents, here and (once synced) everywhere. Host only, and
    only a person: a purge is irreversible."""
    if not author.startswith("user:"):
        raise Refused("only a person can purge documents")
    if not reason.strip():
        raise Refused("say why (clawback, a protective order…): it goes in the record")
    from . import pool
    try:
        pool.claim(m, author)
    except pool.PoolError as e:
        raise Refused(str(e))
    p = plan(m, docs, with_duplicates)
    names = _names(m, p["docs"])
    ev = m.logs["store"].append(op="purge", docs=p["docs"], objects=p["objects"], redact=p["redact"],
                                reason=reason, author=author)
    done = enforce(Matter(m.root), names)
    return {"purge": ev["id"], "documents": len(p["docs"]), "objects": len(p["objects"]),
            "redacted_events": len(p["redact"]), "notice": done.get("notices", [None])[-1]}


# -- on every installation ----------------------------------------------------------------------


def valid_purges(m: Matter) -> list[dict]:
    """The purges that count: made by a person, from an installation that
    writes for them (adhocdocs.pool)."""
    from .annotations import _pool
    pl = _pool(m)
    out = []
    for ev in m.logs["store"].read():
        if ev["op"] != "purge" or not str(ev.get("author", "")).startswith("user:"):
            continue
        if pl is not None and not pl.writes_for(ev.get("origin"), ev["author"]):
            continue
        if all(isinstance(ev.get(k), list) for k in ("docs", "objects", "redact")):
            out.append(ev)
    return out


def _record_path(m: Matter) -> Path:
    return m.store_dir / "purged.json"


def record(m: Matter) -> dict:
    """What has been purged here: ``{"applied", "docs", "objects", "redacted"}``."""
    try:
        return json.loads(_record_path(m).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"applied": [], "docs": [], "objects": [], "redacted": []}


def redactions(m: Matter) -> set[str]:
    """The event hashes purges authorize redacting: what a tombstone may stand for."""
    return {h for ev in valid_purges(m) for h in ev["redact"]}


def purged_objects(m: Matter) -> set[str]:
    return set(record(m)["objects"])


def _names(m: Matter, docs) -> dict:
    out = {}
    for d in docs:
        try:
            out[d] = m.objects.get_object(m.state.metadata[d]).get("name")
        except (KeyError, ValueError):
            out[d] = None
    return out


def enforce(m: Matter, names: dict | None = None) -> dict:
    """Carry out every purge not carried out here yet. Host only (the store
    is read-only in a session)."""
    rec = record(m)
    todo = [ev for ev in valid_purges(m) if ev["id"] not in rec["applied"]]
    if not todo:
        return {"applied": []}
    notices = []
    for ev in todo:
        docs = ev["docs"]
        names_here = {**_names(m, docs), **(names or {})}
        found = _mentions(m, docs)
        # the objects
        for h in ev["objects"]:
            if not (isinstance(h, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", h)):
                continue
            hexd = h.removeprefix("sha256:")
            for root in (m.store_dir / "objects", m.adhoc_dir / "objects"):
                (root / hexd[:2] / hexd[2:]).unlink(missing_ok=True)
        # layers made from them in the workspace
        contents = {m.state.documents.get(d) for d in docs}
        for c in contents - {None}:
            for l in m.state.layers.get(c, []):
                if l.feed == "workspace":
                    _drop_workspace_layer(m, l)
        # the events that name them
        _redact(m, set(ev["redact"]), ev["id"])
        rec["applied"].append(ev["id"])
        rec["docs"] = sorted(set(rec["docs"]) | set(docs))
        rec["objects"] = sorted(set(rec["objects"]) | {h for h in ev["objects"] if isinstance(h, str)})
        rec["redacted"] = sorted(set(rec["redacted"]) | set(ev["redact"]))
        notices.append(_notice(m, ev, names_here, found))
    tmp = _record_path(m).with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(_record_path(m))
    return {"applied": [ev["id"] for ev in todo], "notices": notices}


def _drop_workspace_layer(m: Matter, l):
    try:
        lo = m.workspace_objects.get_object(l.id)
    except KeyError:
        return
    for h in [l.id, *[lo[k] for k in LAYER_PARTS if lo.get(k)]]:
        hexd = h.removeprefix("sha256:")
        (m.adhoc_dir / "objects" / hexd[:2] / hexd[2:]).unlink(missing_ok=True)


def _redact(m: Matter, hashes: set, purge_id: str):
    """Replace the events with these hashes by tombstones, in every
    installation's annotation log here. In place, under each log's lock;
    appenders check they still have the live file (matter.Feed.append)."""
    if not hashes:
        return
    d = m.root / "annotations" / "logs"
    for path in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
        with open(path, "r+", encoding="utf-8", newline="\n") as f, locked(f):
            lines = f.read().splitlines(keepends=True)
            out, changed = [], False
            for line in lines:
                try:
                    ev = json.loads(line)
                except ValueError:
                    out.append(line)
                    continue
                if not is_tombstone(ev) and event_hash(ev) in hashes:
                    out.append(json.dumps(tombstone(ev, purge_id), ensure_ascii=False) + "\n")
                    changed = True
                else:
                    out.append(line)
            if changed:
                tmp = path.with_suffix(".tmp")
                tmp.write_text("".join(out), encoding="utf-8", newline="\n")
                os.replace(tmp, path)


# -- the notice ----------------------------------------------------------------------------------


def _mentions(m: Matter, docs) -> dict:
    """Where this installation's own copies may hold the documents: workspace
    files and published work that name them."""
    ids = [d.removeprefix("sha256:") for d in docs]
    files = []
    ws = m.workspace
    for dirpath, dirs, names in os.walk(ws):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for n in names:
            p = Path(dirpath) / n
            try:
                if p.stat().st_size > 50 << 20:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            if any(i.encode() in data for i in ids):
                files.append(str(p.relative_to(ws)))
    published = []
    try:
        from . import work
        w = work.Work.load(m)
        for name in w.names:
            cur = w.current(name)
            if not cur:
                continue
            for rel, h in work.files_of(m, cur).items():
                try:
                    data = m.objects.get_bytes(h)
                except KeyError:
                    continue
                if any(i.encode() in data for i in ids):
                    published.append(name)
                    break
    except Exception:
        pass
    return {"workspace": sorted(files), "published": sorted(published)}


def _notices_path(m: Matter) -> Path:
    return m.root / "pool" / "purges.json"


def notices(m: Matter) -> list[dict]:
    try:
        return json.loads(_notices_path(m).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


def _notice(m: Matter, ev: dict, names: dict, found: dict) -> dict:
    n = {"purge": ev["id"], "at": ev["at"], "by": ev["author"], "reason": ev.get("reason"),
         "documents": [{"doc": d, "name": names.get(d)} for d in ev["docs"]],
         "workspace_files": found["workspace"], "published_work": found["published"],
         "received": now(), "acknowledged": None,
         "steps": ("The documents are gone from this matter here: originals, text, indexes and tags. What adhocdocs "
                   "doesn't track is yours to deal with: the workspace files listed (drafts, lists, productions and "
                   "exports that name or quote them), published work that quotes them (retire or revise it), "
                   "Claude's transcripts in claude-home (they may quote the documents; deleting claude-home signs "
                   "Claude out and loses past conversations), and any copies made outside adhocdocs.")}
    all_ = notices(m)
    all_.append(n)
    p = _notices_path(m)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(all_, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(p)
    return n


def acknowledge(m: Matter, purge_id: str, author: str) -> dict:
    all_ = notices(m)
    hit = [n for n in all_ if n["purge"] == purge_id]
    if not hit:
        raise Refused(f"no notice for purge {purge_id}")
    for n in hit:
        n["acknowledged"] = {"by": author, "at": now()}
    tmp = _notices_path(m).with_suffix(".tmp")
    tmp.write_text(json.dumps(all_, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(_notices_path(m))
    return hit[0]


def pending(m: Matter) -> list[dict]:
    return [n for n in notices(m) if not n.get("acknowledged")]
