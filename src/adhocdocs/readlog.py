"""What Claude has read: every passage or document ``text`` showed it.

A page's check says how many of the documents its search found were read
for statements against the claim. Told by Claude, that is one more thing to
trust; taken from this log, it is something the tools saw. ``text`` appends
a line per read to ``workspace/.adhoc/reads.jsonl``: the document, the layer
and the character range shown (none: the whole layer). A range counts as
reading a hit when it overlaps it and is at least ``MIN_SPAN`` characters:
the words of the hit alone aren't the passage. A copy of a document
read counts as read (the same bytes, or the same email in two mailboxes).

Text written to a file (``text … > f``) was shown to no one, and reads that
come in a burst (``BURST`` within ``BURST_SECONDS``: a script running
``text`` over many documents) are a dump: neither counts as a reading, and a
check says how many of its documents were only dumped.

The log only knows what went through ``text``. A layer file read some other
way (``text --path`` and then the file, ``raw``) is recorded as a whole read
where the tool can tell, and otherwise isn't: the count is a floor.
"""

import json
from datetime import datetime, timezone

FILE = ".adhoc/reads.jsonl"
BURST = 20              # this many reads within BURST_SECONDS is a dump to files, not a reading
BURST_SECONDS = 60
MIN_SPAN = 120          # a range shorter than this (the hit's own words) doesn't count as reading the passage


def path(m):
    return m.workspace / FILE


def record(m, doc: str, layer: str | None, span=None, how="text") -> None:
    p = path(m)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "doc": doc, "layer": layer,
           "span": list(span) if span else None, "how": how}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _rows(m) -> list[dict]:
    p = path(m)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            r["_t"] = datetime.fromisoformat(r["at"]).timestamp()
        except (ValueError, KeyError):
            continue
        rows.append(r)
    # a burst (a script running text over many documents, into files) shows them to no one in particular
    times = [r["_t"] for r in rows]
    j = 0
    for i, t in enumerate(times):
        while times[j] < t - BURST_SECONDS:
            j += 1
        if i - j + 1 >= BURST:
            for k in range(j, i + 1):
                rows[k]["bulk"] = True
    return rows


def reads(m, bulk=False) -> dict[str, list[tuple]]:
    """Doc -> [(layer, span or None)]: what was read, leaving out reads in a
    bulk dump (``bulk=True``: only those)."""
    out: dict[str, list[tuple]] = {}
    for r in _rows(m):
        if bool(r.get("bulk") or r.get("how") == "file") != bulk:
            continue
        out.setdefault(r["doc"], []).append((r.get("layer"), tuple(r["span"]) if r.get("span") else None))
    return out


def exists(m) -> bool:
    return path(m).exists()


def read_doc(log: dict, doc: str, spans=()) -> bool:
    """Whether ``doc`` was read: as a whole, or in a range that overlaps one
    of ``spans`` (``(layer, a, b)``: its hits). With no spans, any read."""
    rs = log.get(doc) or []
    if not rs:
        return False
    if not spans:
        return True
    for layer, sp in rs:
        if sp is None:
            return True
        if sp[1] - sp[0] < MIN_SPAN:
            continue
        for sl, a, b in spans:
            if (layer is None or sl is None or layer == sl) and sp[0] < b and a < sp[1]:
                return True
    return False
