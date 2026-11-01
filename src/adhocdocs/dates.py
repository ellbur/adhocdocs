"""Dates: the date a document reflects, and the dates written in its text.

**A document's date** (:func:`doc_date`) is when it was sent, or what its
own metadata says, with where that came from, because metadata dates are
often wrong (a template's creation date, a collection's file times):

- an email: its ``Date`` header (``sent``);
- an attachment: its email's sent date (``attached``), unless it is an email
  itself;
- anything else: the metadata's ``date``, named by ``date_source``
  (``created``/``modified``) or, for documents ingested before that was
  recorded, ``metadata``.

**Dates in text** (:func:`detect`) are found by rules, when documents are
indexed (the ``dates`` index), like the entity layer's suggestions. Each
mention has the span, the words, the date (``YYYY-MM-DD``, ``YYYY-MM`` or
``YYYY``, by ``precision``) and how it was read:

=============  ==========================================================
``kind``
``absolute``   "March 9, 2026", "9 Mar 2026", "2026-03-09", "3/9/2026"
``month``      "March 2026"
``noyear``     "March 9", "due 3/7" (a bare M/D only after a word like
               "on", "due" or "by"): ``date`` is ``--03-09``; the year comes
               from the document's date (:func:`resolve`)
``relative``   "yesterday", "last Friday", "next Tuesday": ``rel`` says
               how; resolved against the document's date
=============  ==========================================================

A numeric date with both parts 12 or under (``3/4/2026``) is read month
first, with ``ambiguous`` and the other reading in ``alt``. A date in a
quoted email's header ("On Mar 9, 2026, R. Voss wrote:", "Sent: …") has
``header`` with the sender as written, so the viewer can find that email.

The viewer resolves ``noyear`` and ``relative`` mentions itself
(shell.js), by the same rules as :func:`resolve`.
"""

import calendar
import re
from datetime import date, timedelta

VERSION = 1

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS.update({"sept": 9})
WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}
WEEKDAYS.update({d.lower(): i for i, d in enumerate(calendar.day_abbr)})
WEEKDAYS.update({"tues": 1, "thur": 3, "thurs": 3})

_MON = r"(?P<mon>(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?)"
_WD = r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
_DAY = r"(?P<day>[0-3]?\d)(?:st|nd|rd|th)?"
_YEAR = r"(?P<year>(?:1[89]|20)\d\d)"
_B = r"(?<![\w/.-])"          # not glued to a word, a path or a number
_E = r"(?![\w/-]|\.\d)"

PATTERNS = [
    # March 9, 2026 · Mar. 9 2026 · Monday, March 9th, 2026
    ("absolute", re.compile(_B + _WD + _MON + r"\s+" + _DAY + r",?\s+" + _YEAR + _E, re.I)),
    # 9 March 2026 · 9th of March, 2026
    ("absolute", re.compile(_B + _WD + _DAY + r"\s+(?:of\s+)?" + _MON + r",?\s+" + _YEAR + _E, re.I)),
    # 2026-03-09
    ("iso", re.compile(_B + r"(?P<year>(?:1[89]|20)\d\d)-(?P<mon>[01]\d)-(?P<day>[0-3]\d)" + r"(?![\d-])")),
    # 3/9/2026 · 03/09/26 · 3-9-2026
    ("numeric", re.compile(_B + r"(?P<a>[0-3]?\d)(?P<sep>[/.-])(?P<b>[0-3]?\d)(?P=sep)(?P<year>(?:1[89]|20)?\d\d)" + _E)),
    # March 2026
    ("month", re.compile(_B + _MON + r",?\s+" + _YEAR + _E, re.I)),
    # March 9 · Mar. 9th (no year)
    ("noyear", re.compile(_B + _WD + _MON + r"\s+" + _DAY + r"(?!\w|,?\s*(?:1[89]|20)\d\d)", re.I)),
    # due 3/7 · by 12/31 (a bare M/D only after a word that takes a date, so 1/2 is still a half)
    ("noyear-numeric", re.compile(r"\b(?:on|due|by|until|till|through|thru|since|before|after|from|to|dated|of)\s+"
                                  r"(?P<d>(?P<a>[01]?\d)/(?P<b>[0-3]?\d))(?![\w/]|\.\d)", re.I)),
    # yesterday · last Friday · next Tuesday · this Monday
    ("relative", re.compile(_B + r"(?P<rel>yesterday|tomorrow|(?P<dir>last|next|this|coming|past)\s+(?P<wd>(?:mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?))" + r"(?!\w)", re.I)),
]

# a line that introduces a quoted or forwarded email, and who sent it
HEADER_LINE = re.compile(r"^[>\s]*(?:On\s+(?P<on>.+?)\s+wrote:|(?:Sent|Date)\s*:\s*(?P<sent>.+))\s*$", re.I | re.M)
# what follows the date in "On <date>[ at <time>], <who> wrote:"
TIME_LEAD = re.compile(r"^[,\s]*(?:at\s+)?(?:\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?(?:\s+(?-i:[A-Z]{2,5})\b)?)?[,\s]*", re.I)
FROM_LINE = re.compile(r"^[>\s]*From\s*:\s*(?P<who>.+?)\s*$", re.I | re.M)


def _valid(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _year(s: str) -> int:
    y = int(s)
    return y if y >= 100 else (2000 + y if y < 70 else 1900 + y)


def _month(s: str) -> int:
    return MONTHS[s.lower().rstrip(".")]


def detect(text: str) -> list[dict]:
    """The dates written in ``text``, in order, not overlapping (the first
    pattern to claim a span wins, so "March 9, 2026" isn't also "March 9")."""
    found, taken = [], []

    def free(s, e):
        return all(e <= a or b <= s for a, b in taken)

    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            s, e = m.span("d") if "d" in rx.groupindex else m.span()
            # a leading weekday is part of the match only if a date follows it; trim trailing space
            if not free(s, e):
                continue
            out = {"start": s, "end": e, "form": text[s:e]}
            if kind in ("absolute", "iso"):
                mon = _month(m["mon"]) if kind == "absolute" else int(m["mon"])
                d = _valid(int(m["year"]), mon, int(m["day"]))
                if not d:
                    continue
                out.update(kind="absolute", date=d.isoformat(), precision="day")
            elif kind == "numeric":
                a, b, y = int(m["a"]), int(m["b"]), _year(m["year"])
                if m["sep"] == "." and len(m["year"]) == 2:
                    continue                    # 1.2.26 is more often a section number than a date
                us, eu = _valid(y, a, b), _valid(y, b, a)
                if m["sep"] == "." and eu:      # 9.3.2026 is day first
                    us, eu = eu, None
                d = us or eu
                if not d:
                    continue
                out.update(kind="absolute", date=d.isoformat(), precision="day")
                if us and eu and us != eu:
                    out.update(ambiguous=True, alt=eu.isoformat())
            elif kind == "month":
                out.update(kind="month", date=f"{int(m['year']):04d}-{_month(m['mon']):02d}", precision="month")
            elif kind == "noyear-numeric":
                mon, day = int(m["a"]), int(m["b"])
                if not _valid(2024, mon, day):
                    continue
                out.update(kind="noyear", date=f"--{mon:02d}-{day:02d}", precision="day")
            elif kind == "noyear":
                mon, day = _month(m["mon"]), int(m["day"])
                if not _valid(2024, mon, day):          # a leap year, so Feb 29 is allowed
                    continue
                if m["mon"].lower().rstrip(".") == "may" and not m["mon"][0].isupper():
                    continue                            # "may 5" is a verb more often than a date
                out.update(kind="noyear", date=f"--{mon:02d}-{day:02d}", precision="day")
            else:
                rel = m["rel"].lower()
                if rel in ("yesterday", "tomorrow"):
                    out["rel"] = {"days": {"yesterday": -1, "tomorrow": 1}[rel]}
                else:
                    wd = m["wd"].lower()
                    wd = next((v for k, v in sorted(WEEKDAYS.items(), key=lambda kv: -len(kv[0])) if wd.startswith(k)), None)
                    if wd is None:
                        continue
                    direction = {"last": -1, "past": -1, "next": 1, "coming": 1, "this": 0}[m["dir"].lower()]
                    out["rel"] = {"weekday": wd, "dir": direction}
                out.update(kind="relative", precision="day")
            taken.append((s, e))
            found.append(out)
    found.sort(key=lambda d: d["start"])
    _mark_headers(text, found)
    return found


def _mark_headers(text: str, found: list[dict]):
    """Give dates in a quoted email's header line the sender as written."""
    if not found:
        return
    froms = [(m.start(), m["who"]) for m in FROM_LINE.finditer(text)]
    for m in HEADER_LINE.finditer(text):
        s, e = m.span()
        inside = [d for d in found if s <= d["start"] and d["end"] <= e]
        if not inside:
            continue
        who = None
        if m["on"]:
            who = TIME_LEAD.sub("", text[inside[-1]["end"]:m.end("on")])
        else:
            # "Sent:"/"Date:" in a forwarded block: the nearest "From:" line just above or below it
            near = [f for f in froms if abs(f[0] - s) < 300]
            who = min(near, key=lambda f: abs(f[0] - s))[1] if near else None
        for d in inside:
            d["header"] = {"who": re.sub(r"\s*\[mailto:[^\]]*\]|\s*<[^>]*>|\"", "", who or "").strip(" ,") or None}


def resolve(mention: dict, anchor: str | None) -> str | None:
    """A mention's date, with ``noyear`` and ``relative`` ones placed by
    ``anchor`` (the document's date, ``YYYY-MM-DD…``). ``noyear`` takes the
    anchor's year, or the year before when that would put it more than
    about two months after the anchor (a December date in a January
    email). ``last``/``next`` <weekday> are the one in the week before or
    after; ``this`` <weekday> is the one in the anchor's week (Monday
    first)."""
    if mention["kind"] in ("absolute", "month"):
        return mention["date"]
    try:
        a = date.fromisoformat((anchor or "")[:10])
    except ValueError:
        return None
    if mention["kind"] == "noyear":
        mon, day = int(mention["date"][2:4]), int(mention["date"][5:7])
        d = _valid(a.year, mon, day) or _valid(a.year, mon, 28)
        if d and (d - a).days > 62:
            d = _valid(a.year - 1, mon, day) or d
        return d.isoformat() if d else None
    rel = mention["rel"]
    if "days" in rel:
        return (a + timedelta(days=rel["days"])).isoformat()
    monday = a - timedelta(days=a.weekday())
    return (monday + timedelta(days=rel["weekday"] + 7 * rel["dir"])).isoformat()


# -- a document's own date --------------------------------------------------------------------

SOURCE_LABELS = {"sent": "Sent", "attached": "Attached to an email sent", "created": "Created",
                 "modified": "Last modified", "metadata": "Dated (file metadata)"}


def doc_date(meta: dict, parent_meta: dict | None = None) -> dict | None:
    """``{"date", "source"}`` for a document with metadata ``meta`` (and its
    parent's, for an attachment), or None."""
    if meta.get("kind") == "email" and meta.get("date"):
        return {"date": meta["date"], "source": "sent"}
    if parent_meta and parent_meta.get("kind") == "email" and parent_meta.get("date"):
        return {"date": parent_meta["date"], "source": "attached"}
    if meta.get("date"):
        return {"date": meta["date"], "source": meta.get("date_source") or "metadata"}
    return None


def doc_dates(m) -> dict[str, dict]:
    """Every live document's date, as :func:`doc_date`."""
    state = m.state
    metas = {}

    def meta(d):
        if d not in metas:
            mid = state.metadata.get(d)
            metas[d] = m.objects.get_object(mid) if mid else {}
        return metas[d]

    parent = {c: p for p, cs in state.children.items() for c in cs}
    out = {}
    for d in state.live_documents():
        p = parent.get(d)
        dd = doc_date(meta(d), meta(p) if p else None)
        if dd:
            out[d] = dd
    return out


# -- for Claude: a document's dates, and what happened around a date --------------------------

def _day(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10]) if s and len(s) >= 10 else None
    except ValueError:
        return None


def doc_mentions(m, doc: str) -> dict:
    """A document's own date and the dates in its primary text, each with
    its locator and its date resolved against the document's."""
    from .index import open_index
    state = m.state
    dd = doc_dates(m).get(doc)
    layer = state.primary_layer(state.documents[doc])
    found = []
    if layer:
        idx = open_index(m, "dates")
        idx.check_settings()
        if idx.behind():
            idx.update()
        for x in idx.mentions(layer.text):
            r = resolve(x, dd and dd["date"])
            found.append({"loc": f"doc:{doc}@{layer.id}#char={x['start']},{x['end']}", "form": x["form"], "kind": x["kind"],
                          "date": r, **{k: x[k] for k in ("ambiguous", "alt", "header") if k in x}})
    return {"doc": doc, "dated": dd, "mentions": found}


def around(m, when: str, days: int = 7) -> dict:
    """What the matter has near ``when`` (``YYYY-MM-DD``): documents dated
    within ``days`` either side (attachments under their email), events from
    the entity list, and chronology entries (list items with an ``event``)."""
    from . import annotations, lists
    center = _day(when)
    if not center:
        raise ValueError(f"not a date: {when!r} (YYYY-MM-DD)")
    lo, hi = center - timedelta(days=days), center + timedelta(days=days)

    def near(s):
        d = _day(s)
        if d:
            return lo <= d <= hi
        return bool(s) and len(s) >= 4 and lo.isoformat()[:len(s)] <= s <= hi.isoformat()[:len(s)]

    dds = doc_dates(m)
    docs = sorted(({"doc": d, **dd} for d, dd in dds.items() if dd["source"] != "attached" and near(dd["date"])),
                  key=lambda x: x["date"])
    ann = annotations.Annotations.load(m)
    events = [{"entity": e["entity"], "name": e["name"], "date": e["date"], "summary": e.get("summary")}
              for e in ann.entities.values() if e.get("date") and near(e["date"])]
    chron = []
    for path in sorted(m.workspace.rglob("*.list.jsonl")):
        if any(p.startswith(".") for p in path.relative_to(m.workspace).parts):
            continue
        try:
            header, items = lists.read(path)
        except (ValueError, OSError):
            continue
        for it in items:
            f = it.get("fields") or {}
            if not f.get("event"):
                continue
            try:
                doc = lists.doc_of(it)
            except Exception:
                doc = None
            dt = f.get("date") or (dds.get(doc) or {}).get("date")
            if dt and near(dt):
                chron.append({"date": dt[:10], "event": f["event"], "loc": it["loc"],
                              "list": str(path.relative_to(m.workspace)), "title": header.get("title")})
    chron.sort(key=lambda x: x["date"])
    return {"date": when, "days": days, "documents": docs, "events": sorted(events, key=lambda x: x["date"]),
            "chronology": chron}
