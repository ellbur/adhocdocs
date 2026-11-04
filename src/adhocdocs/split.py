"""Splitting a merged PDF: where one letter, memo or form may end and the next begin.

A scan of a stack of papers, or a PDF someone made by merging files, is one
document to the store and several to a reader. Where the pieces are isn't
a fact: a letter with its enclosures is one thing or three, a contract
with exhibits one or many, a fax cover sheet a piece or part of what it
covers. So there is no "correct" split, only splits that suit a purpose,
and a split is a metadocument (METADOCS.md): recorded, if someone wants to
keep it, as bundles of page ranges, one per piece, beside the document.

What this module does is propose. For each page after the first it
weighs the evidence that a new piece starts there (:func:`boundaries`):

- **Printed page numbers.** "Page 1 of 3" after "Page 4 of 4", or the
  numbers going back to 1, is the strongest sign; "Page 3" after "Page 2"
  is as strong a sign against. A page numbered 2 after an unnumbered one is
  a letter's second page.
- **Openings and closings.** A letterhead, date line, "Dear …", a memo's
  TO/FROM/RE block, a printed email's From/Sent/To/Subject, a court
  caption or a form's title at the top of a page; "Sincerely", a signature
  block, "cc:" or "Enclosure" at the bottom of the one before.
- **A sentence running on** from the bottom of one page to the top of the
  next, which is a sign against.
- **Running headers and footers** that change, or stop, or carry on.
- **The same letterhead as an earlier first page**, and **a page with no
  text** (a slip sheet, or an unscanned page) before it.
- **Scanned and native pages** next to each other.

Each sign carries a weight, for or against; a candidate's strength is
their sum, squashed to 0–1, and comes with its reasons, so a person can
judge it. :data:`LEVELS` names three thresholds, giving three splits from
coarse to fine (``sure``, ``likely``, ``possible``); :func:`pieces` cuts
the document at any set of pages, so a split can also be chosen by hand
or adjusted (``--at``, ``--join``, ``--cut``).

It reads the text layer (and its word positions, when it has them), not
the page images: a change of paper or of handwriting is invisible to it.
"""

import math
import re
from bisect import bisect_right
from dataclasses import dataclass, field

from .matter import Matter

LEVELS = {"sure": 0.9, "likely": 0.6, "possible": 0.3}
BIAS = 1.5          # strength = logistic(score - BIAS): one moderate sign alone isn't enough
HEAD, FOOT = 0.1, 0.9    # header and footer zones, as fractions of the page height
EDGE_LINES = 3           # without word positions: this many lines at the top and bottom

_PAGE_OF = re.compile(r"\bpage\s+(\d{1,4})\s*(?:of|/)\s*(\d{1,4})\b", re.I)
_PAGE = re.compile(r"^\s*(?:page|pg\.?|p\.)\s*(\d{1,4})\b", re.I)          # "Page 3", "Page 3 TITLE 9—…"
_N_OF = re.compile(r"^\s*(\d{1,4})\s*(?:of|/)\s*(\d{1,4})\s*$", re.I)
_DASHED = re.compile(r"^\s*[-–—]\s*(\d{1,4})\s*[-–—]\s*$")
_BARE = re.compile(r"^\s*(\d{1,3})\s*$")
_TRAILING = re.compile(r"(?:\bpage|\bp\.|[—–|·]|\s-)\s*(\d{1,4})\s*$", re.I)    # "… — Internal — Page 1", "Notice — 2"
_LINE_NO = re.compile(r"^\s*\d{1,2}(?:\s+|$)")
_MONTHS = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_DATE_LINE = re.compile(rf"^\s*(?:(?:dated?|date)\s*:?\s*)?(?:{_MONTHS}\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}\s+{_MONTHS}\s+\d{{4}}"
                        rf"|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}|\d{{4}}-\d{{2}}-\d{{2}})\s*$", re.I)
_DEAR = re.compile(r"^\s*(?:dear|to whom it may concern)\b", re.I)
_RE = re.compile(r"^\s*(?:re|subject|subj)\s*:\s*(\S.*)$", re.I)
_MEMO = re.compile(r"^\s*(?:inter-?office\s+)?memo(?:randum)?\s*$", re.I)
_TOFROM = re.compile(r"^\s*(?:to|from|date|cc)\s*:", re.I)
_EMAIL = re.compile(r"^\s*(?:from|sent|to|subject|date|cc)\s*:", re.I)
_FAX = re.compile(r"^\s*(?:fax|facsimile)(?:\s+(?:cover|transmittal))?(?:\s+sheet)?\s*$", re.I)
_CAPTION = re.compile(r"^\s*(?:in the\s+)?(?:united states\s+)?(?:district|superior|circuit|supreme|bankruptcy)\s+court\b"
                      r"|^\s*(?:american arbitration association|before the\b)", re.I)
_TITLE = re.compile(r"^\s*(?:(?:commercial\s+|tax\s+|pro forma\s+)?invoice|purchase order|bill of lading|delivery receipt"
                    r"|proof of delivery|packing (?:slip|list)|credit memo|statement of account|notice of [a-z ]+"
                    r"|certificate of [a-z ]+|[a-z ]*agreement|[a-z ]*report|affidavit[a-z ]*|declaration of [a-z .]+"
                    r"|subpoena[a-z ]*|purchase requisition|work order|quotation|receipt)"
                    r"(?:\s+(?:no\.?|number|#)?\s*[\w/.-]*\d[\w/.-]*)?\s*$", re.I)    # perhaps with its number
_CLOSE = re.compile(r"^\s*(?:sincerely|very truly yours|yours (?:truly|faithfully|sincerely)|best regards|kind regards|regards"
                    r"|respectfully(?: submitted)?|thank you)\s*,?\s*$", re.I)
_SIGN = re.compile(r"^\s*(?:/s/|by\s*:\s*\S|s/\s*\w)", re.I)
_AFTER = re.compile(r"^\s*(?:cc|bcc|encl(?:osures?)?\.?|attachments?|enclosures?)\s*:?", re.I)
_END = re.compile(r"\b(?:end of (?:document|report|transmission|message)|\*\*\* end)\b", re.I)
_CONTINUED = re.compile(r"\(?(?:continued|cont'?d\.?)\)?\s*$", re.I)
_TERMINAL = re.compile(r"""[.!?:;)"'”’\]]\s*$""")


@dataclass
class Page:
    n: int                                   # 1-based
    lines: list[str]
    head: list[str] = field(default_factory=list)    # lines in the header zone
    foot: list[str] = field(default_factory=list)    # lines in the footer zone
    number: int | None = None                # the printed page number
    total: int | None = None                 # "of N"
    ocr: bool | None = None                  # read by OCR (None: unknown)

    @property
    def body(self) -> list[str]:
        """The lines between the header and footer zones."""
        start = len(self.head)
        end = len(self.lines) - len(self.foot)
        return self.lines[start:end] if end > start else []

    @property
    def plain(self) -> list[str]:
        """The lines without a transcript's line numbers, when it has them."""
        if sum(1 for x in self.lines[:15] if _LINE_NO.match(x)) >= 6:
            return [y for y in (_LINE_NO.sub("", x).strip() for x in self.lines) if y]
        return self.lines

    @property
    def chars(self) -> int:
        return sum(len(x.strip()) for x in self.lines)


def _norm(s: str) -> str:
    """A line with its numbers and spacing taken out, to compare running headers."""
    return " ".join(re.sub(r"\d+", "#", s.casefold()).split())


def _number(page: Page):
    """The page's printed number (and total), from its header and footer lines."""
    zone = page.foot[::-1] + page.head
    for line in zone:
        m = _PAGE_OF.search(line)
        if m:
            return int(m[1]), int(m[2])
    for line in zone:
        for rx in (_PAGE, _N_OF, _DASHED):
            m = rx.match(line)
            if m:
                return int(m[1]), (int(m[2]) if rx is _N_OF else None)
    for line in zone:
        m = _TRAILING.search(line)
        if m:
            return int(m[1]), None
    # a bare number, alone on the last line or the first
    for line in (page.foot[-1:] + page.head[:1]):
        m = _BARE.match(line)
        if m and 0 < int(m[1]) < 1000:
            return int(m[1]), None
    return None, None


def pages(m: Matter, doc: str) -> list[Page]:
    """The document's pages, from its primary layer (pages end at form feeds),
    with header and footer zones from the word positions when the layer has
    them, else the first and last few lines."""
    from .produce import geometry
    state = m.state
    layer = state.primary_layer(state.documents[doc])
    if layer is None:
        raise ValueError(f"{doc[:12]} has no text layer")
    text = m.objects.get_text(layer.text)
    geo = sorted(geometry(m, layer.id), key=lambda g: g["start"])
    starts = [g["start"] for g in geo]
    out, offset = [], 0
    for n, chunk in enumerate(text.split("\f"), 1):
        lines, tops, pos = [], [], offset
        for line in chunk.split("\n"):
            if line.strip():
                i = bisect_right(starts, pos + len(line) - len(line.lstrip())) - 1
                g = geo[i] if i >= 0 and geo[i]["start"] < pos + len(line) else None
                lines.append(line.strip())
                tops.append(g["box"][1] if g and g.get("page") == n and g.get("box") else None)
            pos += len(line) + 1
        p = Page(n, lines)
        if lines and all(t is not None for t in tops):
            k = 0
            while k < min(len(lines), EDGE_LINES) and tops[k] < HEAD:
                k += 1
            j = 0
            while j < min(len(lines) - k, EDGE_LINES) and tops[len(lines) - 1 - j] > FOOT:
                j += 1
            p.head, p.foot = lines[:k], (lines[len(lines) - j:] if j else [])
        else:
            p.head = lines[:min(2, len(lines))]
            p.foot = lines[max(len(p.head), len(lines) - 2):]
        if not p.head and lines:          # numbers at the very top, even if not in the zone
            p.head = lines[:1] if _PAGE_OF.search(lines[0]) or _PAGE.match(lines[0]) or _BARE.match(lines[0]) else []
        p.number, p.total = _number(p)
        words = [g for g in geo if g.get("page") == n]
        p.ocr = (any("conf" in g for g in words)) if words else None
        out.append(p)
        offset += len(chunk) + 1
    return out


def _opening(p: Page) -> list[tuple[float, str]]:
    """Signs that a page is the first of something."""
    top = [x for x in p.plain if x not in p.head][:15]
    sig = []
    email = sum(1 for x in top[:10] if _EMAIL.match(x))
    if email >= 3:
        sig.append((2.5, "an email's From/Sent/To/Subject header at the top"))
    elif _MEMO.match(" ".join(top[:3])) or any(_MEMO.match(x) for x in top[:4]) or sum(1 for x in top if _TOFROM.match(x)) >= 2:
        sig.append((2.0, "a memo's heading (MEMORANDUM, TO/FROM)"))
    if any(_FAX.match(x) for x in top[:5]):
        sig.append((2.0, "a fax cover sheet"))
    if any(_CAPTION.search(x) for x in top[:6]):
        sig.append((2.0, "a court or tribunal caption"))
    if any(_DEAR.match(x) for x in top):
        sig.append((1.5, "a salutation (Dear …)"))
    title = next((x for x in top[:6] if _TITLE.match(x) and len(x) < 60), None)
    if title:
        sig.append((1.5, f"a title: {title!r}"))
    if any(_DATE_LINE.match(x) for x in top[:10]) and not email:
        sig.append((0.75, "a date line near the top"))
    if any(_RE.match(x) for x in top) and not email:
        sig.append((0.75, "a Re:/Subject: line"))
    return sig


def _closing(p: Page) -> list[tuple[float, str]]:
    """Signs that a page is the last of something."""
    tail = p.lines[-14:]
    sig = []
    if any(_CLOSE.match(x) for x in tail):
        sig.append((1.5, "a closing (Sincerely, Regards…) at the bottom of the page before"))
    elif any(_SIGN.match(x) for x in tail):
        sig.append((1.0, "a signature line at the bottom of the page before"))
    if any(_AFTER.match(x) for x in tail[-6:]):
        sig.append((0.75, "cc:/Enclosure at the bottom of the page before"))
    if any(_END.search(x) for x in tail):
        sig.append((2.0, "“end of …” on the page before"))
    return sig


def _runs_on(prev: Page, cur: Page) -> bool:
    """The last sentence of the page before carries on at the top of this one."""
    a = prev.body or prev.lines
    b = cur.body or cur.lines
    if not a or not b:
        return False
    last, first = a[-1], b[0]
    return bool(re.search(r"[a-z,]$|[a-z]-$", last)) and not _TERMINAL.search(last) and first[:1].islower()


def score(ps: list[Page], i: int) -> dict:
    """The evidence that a new piece starts at page ``ps[i]`` (0-based ``i`` ≥ 1)."""
    prev, cur = ps[i - 1], ps[i]
    sig: list[tuple[float, str]] = []

    # printed page numbers
    if cur.number is not None and prev.number is not None:
        if cur.number == prev.number + 1 and (cur.total == prev.total or None in (cur.total, prev.total)):
            sig.append((-4.0, f"the page numbers carry on ({prev.number} → {cur.number})"))
        elif cur.number == 1:
            sig.append((4.0, f"the page numbers start again at 1 (after {prev.number})"))
        elif cur.number <= prev.number:
            sig.append((2.0, f"the page numbers go back ({prev.number} → {cur.number})"))
    elif cur.number == 1:
        sig.append((1.5, "numbered page 1"))
    elif cur.number == 2 and prev.number is None:
        sig.append((-2.5, "numbered 2 after an unnumbered page: a second page"))
    elif cur.number is None and prev.number is not None and not (prev.total and prev.number < prev.total) \
            and (i + 1 >= len(ps) or ps[i + 1].number is None):      # not a book's numbers on alternate pages
        sig.append((0.75, "the page numbering stops"))
    if prev.total and prev.number == prev.total:
        sig.append((2.5, f"the page before was the last ({prev.number} of {prev.total})"))
    elif prev.total and prev.number is not None and prev.number < prev.total and not (cur.number == 1):
        sig.append((-2.0, f"the page before was {prev.number} of {prev.total}"))
    if cur.total and prev.total and cur.total != prev.total:
        sig.append((1.5, f"a different page count ({prev.total} → {cur.total})"))

    # what the pages say
    sig += _opening(cur)
    sig += _closing(prev)
    if prev.chars < 10 and i >= 1:
        sig.append((1.5, "after a page with no text (a slip sheet, or a page not read)"))
    if cur.chars < 10:
        sig.append((-1.0, "a page with no text"))
    if _runs_on(prev, cur):
        sig.append((-3.0, "a sentence runs on from the page before"))
    if any(_CONTINUED.search(x) for x in cur.lines[:3]):
        sig.append((-2.0, "marked continued"))

    # running headers and footers
    for zone, label in (("head", "header"), ("foot", "footer")):
        a = {_norm(x) for x in getattr(prev, zone)} - {""}
        b = {_norm(x) for x in getattr(cur, zone)} - {""}
        a = {x for x in a if not _BARE.match(x.replace("#", "1"))}
        b = {x for x in b if not _BARE.match(x.replace("#", "1"))}
        if a and b and a & b:
            sig.append((-1.0, f"the same running {label}"))
        elif a and b and i >= 2 and a & {_norm(x) for x in getattr(ps[i - 2], zone)}:
            sig.append((1.0, f"the running {label} changes"))
        elif a and not b and i >= 2 and a & {_norm(x) for x in getattr(ps[i - 2], zone)}:
            sig.append((0.75, f"the running {label} stops"))

    # the same top as an earlier page that began something (page 1, or a page
    # that opens like a letter or form), and not the page just before; a top
    # holding the page number is a running header, not a letterhead
    top = [_norm(x) for x in cur.lines[:3]]
    numbered_top = cur.number is not None and any(x in cur.head for x in cur.lines[:3])
    if len(" ".join(top)) > 12 and top != [_norm(x) for x in prev.lines[:3]] and not numbered_top:
        for j in range(0, i - 1):
            if [_norm(x) for x in ps[j].lines[:3]] == top and (j == 0 or _opening(ps[j])):
                sig.append((1.5, f"the same letterhead or heading as page {ps[j].n}"))
                break

    # scanned and native pages side by side
    if prev.ocr is not None and cur.ocr is not None and prev.ocr != cur.ocr:
        sig.append((1.0, "a scanned page after a native one" if cur.ocr else "a native page after a scanned one"))

    total = sum(w for w, _ in sig)
    strength = 1 / (1 + math.exp(-(total - BIAS)))
    return {"page": cur.n, "strength": round(strength, 2), "level": level_of(strength),
            "for": [r for w, r in sig if w > 0], "against": [r for w, r in sig if w < 0], "score": round(total, 2)}


def level_of(strength: float) -> str | None:
    return next((name for name, t in LEVELS.items() if strength >= t), None)


def boundaries(ps: list[Page]) -> list[dict]:
    """Every page after the first, with the evidence that a piece starts there."""
    return [score(ps, i) for i in range(1, len(ps))]


def _title(p: Page) -> tuple[str, str | None]:
    """(a label for the piece that starts on this page, the kind of thing it seems to be)."""
    top = [x for x in p.plain if x not in p.head][:15]
    kind = None
    if sum(1 for x in top[:10] if _EMAIL.match(x)) >= 3:
        kind = "email"
    elif any(_MEMO.match(x) for x in top[:4]) or sum(1 for x in top if _TOFROM.match(x)) >= 2:
        kind = "memo"
    elif any(_FAX.match(x) for x in top[:5]):
        kind = "fax"
    elif any(_CAPTION.search(x) for x in top[:6]):
        kind = "filing"
    elif any(_DEAR.match(x) for x in top):
        kind = "letter"
    t = next((x for x in top[:6] if _TITLE.match(x) and len(x) < 60), None)
    if t and not kind:
        kind = "form"
    subj = next((_RE.match(x)[1] for x in top if _RE.match(x)), None)
    label = subj or t or next((x for x in top if len(x) > 3 and not _DATE_LINE.match(x)), None) or "(no text)"
    if subj and kind in ("letter", "memo", "email"):
        label = f"{kind.capitalize()}: {subj}"
    label = " ".join(label.split())
    return (label[:80] + "…" if len(label) > 80 else label), kind


def pieces(ps: list[Page], starts) -> list[dict]:
    """The pieces a split at pages ``starts`` (1-based; page 1 is implied) makes,
    each with its pages, a label, a kind, its date if a date line says, and
    whether its printed numbering agrees with its length."""
    starts = sorted({1, *(s for s in starts if 1 < s <= len(ps))})
    out = []
    for k, a in enumerate(starts):
        b = (starts[k + 1] - 1) if k + 1 < len(starts) else len(ps)
        first = ps[a - 1]
        label, kind = _title(first)
        date = next((x.strip() for x in first.lines[:12] if _DATE_LINE.match(x)), None)
        row = {"piece": k + 1, "pages": [a, b], "label": label}
        if kind:
            row["kind"] = kind
        if date:
            row["date_line"] = date
        totals = {p.total for p in ps[a - 1:b] if p.total}
        if len(totals) == 1:
            t = totals.pop()
            if t != b - a + 1:
                row["numbering"] = f"its pages say {t}, it has {b - a + 1}"
        elif len(totals) > 1:
            row["numbering"] = "its pages give different page counts: " + ", ".join(str(t) for t in sorted(totals))
        out.append(row)
    return out


def starts_at(bs: list[dict], level: str) -> list[int]:
    t = LEVELS[level]
    return [b["page"] for b in bs if b["strength"] >= t]


def propose(m: Matter, doc: str, level: str = "likely") -> dict:
    """The candidates, and the splits at each level: coarse to fine."""
    ps = pages(m, doc)
    bs = boundaries(ps)
    return {"pages": len(ps), "boundaries": bs,
            "splits": {lv: pieces(ps, starts_at(bs, lv)) for lv in LEVELS},
            "level": level}


def find(m: Matter, level: str = "likely", min_pages: int = 2) -> list[dict]:
    """Documents that look like several: paged documents with a candidate
    at ``level`` or stronger, most pieces first."""
    from .analysis import _ready
    rows = _ready(m, "fields").rows()
    out = []
    for d in m.state.live_documents():
        r = rows.get(d) or {}
        if r.get("kind") not in ("pdf", "image", None):
            continue
        layer = m.state.primary_layer(m.state.documents[d])
        if layer is None:
            continue
        text = m.objects.get_text(layer.text)
        if text.count("\f") + 1 < min_pages:
            continue
        ps = pages(m, d)
        bs = boundaries(ps)
        at = starts_at(bs, level)
        if at:
            out.append({"doc": d, "name": r.get("name"), "pages": len(ps), "pieces": len(at) + 1,
                        "sure": len(starts_at(bs, "sure")) + 1, "possible": len(starts_at(bs, "possible")) + 1,
                        "starts": at})
    out.sort(key=lambda x: (-x["pieces"], x["name"] or ""))
    return out


def chosen(bs: list[dict], level="likely", at=None, cut=(), join=()) -> list[int]:
    """The pages a split starts pieces at: exactly ``at``, or the candidates
    at ``level``, plus ``cut`` and less ``join``."""
    starts = set(at) if at is not None else set(starts_at(bs, level))
    return sorted((starts | set(cut)) - set(join))


def describe(ps: list[Page], bs: list[dict], starts: list[int]) -> list[dict]:
    """The pieces, each with the evidence for its first page, and the
    candidates the split passes over (as strong as ``possible`` or more)."""
    by_page = {b["page"]: b for b in bs}
    rows = pieces(ps, starts)
    for r in rows:
        b = by_page.get(r["pages"][0])
        if b:
            r["starts"] = {k: b[k] for k in ("strength", "level", "for", "against")}
    return rows


def passed_over(bs: list[dict], starts: list[int]) -> list[dict]:
    return [{"page": b["page"], "strength": b["strength"], "level": b["level"], "for": b["for"], "against": b["against"]}
            for b in bs if b["page"] not in starts and b["strength"] >= LEVELS["possible"]]


def recorded(ann, doc: str) -> dict[str, list[dict]]:
    """The splits recorded for a document: split name -> its bundles, in order."""
    out: dict[str, list[dict]] = {}
    for b in ann.bundles.values():
        sp = b.get("split")
        if sp and doc in (sp["of"], *(p["doc"] for p in b["parts"])):
            out.setdefault(sp["name"], []).append(b)
    for bs in out.values():
        bs.sort(key=lambda b: b["split"]["piece"])
    return out


def bundle_requests(doc: str, doc_name: str, split_name: str, rows: list[dict]) -> list[dict]:
    """``define-bundle`` requests, one per piece: its page range, a name that
    says which split and piece it is, and the split it belongs to."""
    n = len(rows)
    out = []
    for r in rows:
        a, b = r["pages"]
        sel = {"page": a if a == b else [a, b]}
        pages = f"p. {a}" if a == b else f"pp. {a}–{b}"
        summary = f"{pages} of {doc_name}" + "".join(f"; {x}" for x in (r.get("kind"), r.get("date_line")) if x)
        out.append({"op": "define-bundle", "name": f"{doc_name} · {split_name} {r['piece']}/{n} · {r['label']}",
                    "parts": [{"doc": doc, "select": sel}], "summary": summary, "from": f"split:{doc}",
                    "split": {"of": doc, "name": split_name, "piece": r["piece"], "pieces": n}})
    return out
