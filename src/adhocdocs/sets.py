"""Key sets: the few documents that explain something, with every other
relevant document accounted for.

A key set is an ordinary list file (:mod:`adhocdocs.lists`) with a
convention:

- Each **item** is a representative document. ``fields.why`` says what it
  establishes ("the signed proof of delivery").
- Each item's **children** are the documents it covers, each with
  ``fields.covered`` (why it adds nothing) and ``fields.covered_by`` (the
  document that covers it directly, which can be a child of the same
  item). Reasons found by :func:`condense` are ``duplicate`` (same
  original), ``same-text``, ``same-message`` (one email from several
  custodians), ``quoted-in-later-email`` and ``near-duplicate``; a judgment
  ("says the same as") is recorded as ``restates``, with ``fields.note``.
- The **header** may say what the set answers (``question``) and where its
  candidates came from (``sources``: each input list's file, query and
  search coverage), so "these 4 of 23" can be checked.
- **Points** say what the set must establish: ``header.points`` is a list
  of ``{"id", "text"}`` ("delivered": "The pallet reached Dock 4 on Feb.
  24"), and an item's ``fields.points`` lists the IDs it supports.
  :func:`points_report` finds points no document supports. A covered
  document's points count for its representative, since it adds nothing
  the representative lacks.

Nothing is dropped: a covered document stays in the file, under what
covers it, and can be promoted back with :func:`uncover`, which marks it
``fields.kept`` (the reason it was folded) so condensing again leaves it be.
Condensing a key set again, with new candidates, keeps ``why``, ``kept``
and ``restates`` judgments.
"""

from . import lists, locators, textnorm
from .literate import ID
from .matter import Matter

REASONS = ("duplicate", "same-text", "same-message", "quoted-in-later-email", "near-duplicate", "restates")
SET_COLUMNS = ["name", "date", "custodian", "why", "covered", "tags"]


MIN_SHINGLES = 5


def _shingles(text: str, k=5) -> set:
    """Word k-shingles of normalized text: quote markers, punctuation and
    line wrapping don't count, so a quoted reply still matches."""
    w = textnorm.words(text)
    return {tuple(w[i:i + k]) for i in range(len(w) - k + 1)}


def _doc(loc) -> str:
    return locators.parse(loc)["doc"]


def _candidates(sources: list[tuple[dict, list[dict]]]) -> tuple[dict[str, dict], list[tuple]]:
    """(doc -> its item, judgments). Items keep their fields, in first-seen
    order. Children in the same document as their item are search hits and
    are dropped; children in other documents are candidates too. Judgments
    are earlier ``restates`` covers, as (doc, covered_by, note), so
    condensing a key set again keeps them."""
    out = {}
    judged = []

    def add(it):
        d = _doc(it["loc"])
        if d not in out:
            out[d] = {"loc": "doc:" + d, **({"fields": dict(it["fields"])} if it.get("fields") else {})}
        else:
            for k, v in (it.get("fields") or {}).items():
                out[d].setdefault("fields", {}).setdefault(k, v)

    for _, items in sources:
        for it in items:
            add(it)
            for c in it.get("children", []):
                if _doc(c["loc"]) != _doc(it["loc"]):
                    add(c)
                    f = c.get("fields") or {}
                    if f.get("covered") == "restates" and f.get("covered_by"):
                        judged.append((_doc(c["loc"]), _doc(f["covered_by"]), f.get("note")))
    for it in out.values():
        for k in ("covered", "covered_by", "similarity", "containment", "note"):
            (it.get("fields") or {}).pop(k, None)
        if "fields" in it and not it["fields"]:
            del it["fields"]
    return out, judged


def condense(m: Matter, sources: list[tuple[str, dict, list[dict]]], title=None, question=None,
             near=0.9, quoted=0.9) -> tuple[dict, list[dict], dict]:
    """Merge candidate lists and fold mechanically redundant documents under
    a representative. Returns (header, items, summary).

    In order: exact duplicates, the same text, the same email (Message-ID),
    an email whose body (after its header block) is at least ``quoted``
    contained in a later email's text (word 5-shingles, so quote markers and
    rewrapping don't matter),
    and texts at least ``near`` similar. Within each, the representative is
    the one first in list order, except that a later email covers the
    earlier one it quotes. ``near=0`` or ``quoted=0`` turns that step off.
    Versions of a document (a memo and its revision) are near-duplicates
    only if they are that similar; their differences may matter, and they
    stay visible as children either way.
    """
    from .analysis import _ready
    from .index.neardup import similarity

    state = m.state
    cands, judged = _candidates([(h, i) for _, h, i in sources])
    for d in cands:
        if d not in state.documents:
            raise ValueError(f"doc:{d} is not in this matter")
    order = {d: i for i, d in enumerate(cands)}
    fields = _ready(m, "fields").rows()
    covered = {}   # doc -> (covered_by, reason, extra fields)
    reps = list(cands)
    kept = {d for d, it in cands.items() if (it.get("fields") or {}).get("kept")}

    def fold(key_of, reason):
        nonlocal reps
        first = {}
        for d in reps:
            k = key_of(d)
            if k is None:
                continue
            if k in first and d not in kept:
                covered[d] = (first[k], reason, {})
            else:
                first[k] = d
        reps = [d for d in reps if d not in covered]

    def text_of(d):
        layer = state.primary_layer(state.documents[d])
        return layer.text if layer else None

    fold(lambda d: state.documents[d], "duplicate")
    fold(text_of, "same-text")
    mids = {r["doc"]: r["message_id"] for r in _ready(m, "threads").rows() if r.get("message_id")}
    fold(mids.get, "same-message")

    sigs = _ready(m, "neardup").signatures() if near else {}

    def sig(d):
        t = text_of(d)
        s = sigs.get(t) if t else None
        return s if s and s[1] else None

    if quoted:
        emails = sorted((d for d in reps if (fields.get(d) or {}).get("kind") == "email"
                         and (fields.get(d) or {}).get("date") and text_of(d)), key=lambda d: fields[d]["date"])
        body = {d: _shingles(m.objects.get_text(text_of(d)).split("\n\n", 1)[-1]) for d in emails}
        whole = {d: _shingles(m.objects.get_text(text_of(d))) for d in emails}
        # each earlier email goes under the latest one that quotes it
        for i, a in enumerate(emails):
            if a in kept or len(body[a]) < MIN_SHINGLES:
                continue
            for b in reversed(emails[i + 1:]):
                if fields[b]["date"] <= fields[a]["date"]:
                    continue
                c = len(body[a] & whole[b]) / len(body[a])
                if c >= quoted:
                    covered[a] = (b, "quoted-in-later-email", {"containment": round(c, 3)})
                    break
        reps = [d for d in reps if d not in covered]

    if near:
        for i, a in enumerate(reps):
            if a in covered or not sig(a):
                continue
            for b in reps[i + 1:]:
                if b in covered or b in kept or not sig(b):
                    continue
                s = similarity(sig(a)[0], sig(b)[0])
                if s >= near:
                    covered[b] = (a, "near-duplicate", {"similarity": round(s, 3)})
        reps = [d for d in reps if d not in covered]

    def top(d):
        seen = set()
        while d in covered and d not in seen:
            seen.add(d)
            d = covered[d][0]
        return d

    children = {r: [] for r in reps}
    for d in sorted(covered, key=order.get):
        by, reason, extra = covered[d]
        it = cands[d]
        it["fields"] = {**(it.get("fields") or {}), "covered": reason, "covered_by": "doc:" + by, **extra}
        children[top(d)].append(it)
    items = []
    for r in reps:
        it = cands[r]
        if children[r]:
            it["children"] = children[r]
        items.append(it)

    for d, by, note in judged:
        try:
            cover(items, [d], by, "restates", note)
        except ValueError:  # covered mechanically now, or the other one is
            pass

    first = sources[0][1] if sources else {}
    header = {"type": "list", "v": 1, "title": title or question or first.get("title") or "key set",
              "columns": SET_COLUMNS, "made_by": "claude"}
    question = question or next((h["question"] for _, h, _ in sources if h.get("question")), None)
    if question:
        header["question"] = question
    header["sources"] = []
    for f, h, i in sources:
        if "sources" in h:  # an earlier key set: where its candidates came from
            header["sources"] += [x for x in h["sources"] if x not in header["sources"]]
        else:
            header["sources"].append({"file": f, "items": len(i),
                                      **{k: h[k] for k in ("title", "query", "search") if k in h}})
    points = []
    for _, h, _ in sources:
        points += [p for p in h.get("points", []) if p["id"] not in {x["id"] for x in points}]
    if points:
        header["points"] = points
        header["columns"] = _with_points_column(header["columns"])
    header["condensed"] = {"candidates": len(cands), "representatives": len(reps), "near": near, "quoted": quoted}
    counts = {}
    for it in items:
        for c in it.get("children", []):
            r = c["fields"]["covered"]
            counts[r] = counts.get(r, 0) + 1
    header["condensed"]["representatives"] = len(items)
    return header, items, {"candidates": len(cands), "representatives": len(items), "covered": counts}


# -- editing a key set ---------------------------------------------------------------------------

def _find(items, doc):
    """(index of the top-level item, index of the child or None)."""
    for i, it in enumerate(items):
        if _doc(it["loc"]) == doc:
            return i, None
        for j, c in enumerate(it.get("children", [])):
            if _doc(c["loc"]) == doc:
                return i, j
    raise ValueError(f"doc:{doc} is not in this list")


def _take(items, doc) -> list[dict]:
    """Remove a document from the list; returns it, then its children (if it
    was an item), as entries to place elsewhere."""
    i, j = _find(items, doc)
    if j is not None:
        c = items[i]["children"].pop(j)
        if not items[i]["children"]:
            del items[i]["children"]
        return [c]
    it = items.pop(i)
    return [it, *it.pop("children", [])]


def set_fields(items, doc, values: dict, header=None):
    """Set (or, with a value of None, remove) fields on an item or child.
    ``points`` is a comma-separated list of the header's point IDs."""
    i, j = _find(items, doc)
    it = items[i] if j is None else items[i]["children"][j]
    f = it.setdefault("fields", {})
    if values.get("points") is not None:
        known = [p["id"] for p in (header or {}).get("points", [])]
        ids = [x.strip() for x in values["points"].split(",") if x.strip()]
        unknown = [x for x in ids if x not in known]
        if unknown:
            raise ValueError(f"no such point: {', '.join(unknown)} (points: {', '.join(known) or 'none yet; add one with list point'})")
        values = {**values, "points": list(dict.fromkeys(ids))}
    for k, v in values.items():
        if v is None:
            f.pop(k, None)
        else:
            f[k] = v
    if not f:
        del it["fields"]


def cover(items, docs, by: str, reason="restates", note=None):
    """Put documents under the item that ``by`` is (or is a child of). A
    covered item brings its children along, keeping their reasons."""
    if reason not in REASONS:
        raise ValueError(f"reason must be one of {', '.join(REASONS)}")
    for d in docs:
        if d == by:
            raise ValueError("a document can't cover itself")
        i, j = _find(items, by)
        if j is not None and _doc(items[i]["loc"]) == d:
            raise ValueError(f"doc:{by} is covered by doc:{d}; uncover it first")
        moved = _take(items, d)
        i, _ = _find(items, by)
        head = moved[0]
        f = {k: v for k, v in (head.get("fields") or {}).items() if k not in ("similarity", "containment", "note")}
        head["fields"] = {**f, "covered": reason, "covered_by": "doc:" + by, **({"note": note} if note else {})}
        items[i].setdefault("children", []).extend(moved)


def uncover(items, docs):
    """Make covered documents representatives again, after the item that
    covered them. What they cover (directly or through another child) comes
    with them."""
    for d in docs:
        i, j = _find(items, d)
        if j is None:
            continue
        kids = items[i]["children"]
        by = {_doc(c["loc"]): _doc(c["fields"]["covered_by"]) for c in kids
              if (c.get("fields") or {}).get("covered_by")}

        def under(x, seen=()):
            while x in by and x not in seen:
                seen = (*seen, x)
                x = by[x]
                if x == d:
                    return True
            return False

        c = kids[j]
        moving = [k for k in kids if k is not c and under(_doc(k["loc"]))]
        items[i]["children"] = [k for k in kids if k is not c and k not in moving]
        if not items[i]["children"]:
            del items[i]["children"]
        f = c.setdefault("fields", {})
        reason = f.get("covered")
        for k in ("covered", "covered_by", "similarity", "containment", "note"):
            f.pop(k, None)
        if reason != "restates":  # condense would fold it again; this says not to
            f["kept"] = reason
        if not f:
            del c["fields"]
        if moving:
            c["children"] = moving
        items.insert(i + 1, c)


# -- points ----------------------------------------------------------------------------------------

def _with_points_column(columns):
    if "points" in columns:
        return columns
    i = columns.index("why") + 1 if "why" in columns else len(columns)
    return [*columns[:i], "points", *columns[i:]]


def set_point(header, pid: str, text: str):
    """Add a point the set must establish, or change its text."""
    if not ID.match(pid):
        raise ValueError(f"bad point id {pid!r}: use lowercase letters, digits and hyphens")
    points = header.setdefault("points", [])
    for p in points:
        if p["id"] == pid:
            p["text"] = text
            break
    else:
        points.append({"id": pid, "text": text})
    header["columns"] = _with_points_column(header.get("columns") or lists.DEFAULT_COLUMNS)


def remove_point(header, items, pid: str):
    """Remove a point, and its ID from every item that supported it."""
    before = len(header.get("points", []))
    header["points"] = [p for p in header.get("points", []) if p["id"] != pid]
    if len(header["points"]) == before:
        raise ValueError(f"no such point: {pid}")
    for it in items:
        for x in (it, *it.get("children", [])):
            f = x.get("fields") or {}
            if pid in f.get("points", []):
                f["points"] = [p for p in f["points"] if p != pid]
                if not f["points"]:
                    del f["points"]


def points_report(header, items) -> dict:
    """Which representatives support each point (directly, or ``via`` a
    document they cover), the points nothing supports, and the
    representatives that support no point."""
    points = header.get("points", [])
    support = {p["id"]: [] for p in points}
    idle = []
    for it in items:
        mine = False
        for x in (it, *it.get("children", [])):
            for pid in (x.get("fields") or {}).get("points", []):
                if pid in support:
                    mine = True
                    entry = {"loc": it["loc"], **({"why": it["fields"]["why"]} if (it.get("fields") or {}).get("why") else {})}
                    if x is not it:
                        entry["via"] = x["loc"]
                    support[pid].append(entry)
        if not mine:
            idle.append(it["loc"])
    return {"question": header.get("question"),
            "points": [{**p, "supported_by": support[p["id"]]} for p in points],
            "unsupported": [p["id"] for p in points if not support[p["id"]]],
            "supporting_nothing": idle}
