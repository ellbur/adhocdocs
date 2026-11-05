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
    boxes: list = field(default_factory=list)  # per line: (x0, top, x1, word height), or None without word positions
    look: dict | None = None                 # what the page image shows (:func:`looks`), or None without one

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


def pages(m: Matter, doc: str, images: bool = True) -> list[Page]:
    """The document's pages, from its primary layer (pages end at form feeds),
    with header and footer zones from the word positions when the layer has
    them, else the first and last few lines; and, with ``images``, what each
    page's image shows (:func:`looks`), when the original is at hand."""
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
        lines, tops, boxes, pos = [], [], [], offset
        for line in chunk.split("\n"):
            if line.strip():
                i = bisect_right(starts, pos + len(line) - len(line.lstrip())) - 1
                g = geo[i] if i >= 0 and geo[i]["start"] < pos + len(line) else None
                lines.append(line.strip())
                tops.append(g["box"][1] if g and g.get("page") == n and g.get("box") else None)
                # the line's extent: its words' boxes
                k, ws = max(i, 0), []
                while k < len(geo) and geo[k]["start"] < pos + len(line):
                    if geo[k]["start"] >= pos and geo[k].get("box") and geo[k].get("page") == n:
                        ws.append(geo[k]["box"])
                    k += 1
                boxes.append((min(b[0] for b in ws), min(b[1] for b in ws), max(b[2] for b in ws),
                              sorted(b[3] - b[1] for b in ws)[len(ws) // 2]) if ws else None)
            pos += len(line) + 1
        p = Page(n, lines, boxes=boxes)
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
    if images:
        looks(m, doc, out)
    return out


# -- what the page images show -------------------------------------------------------------------

LOOK_DPI = 24          # small: the paper, the page's shape, and what the top of it looks like
TOP_BAND = 0.18        # the top of the page, where letterheads are, as a fraction of its height


def looks(m: Matter, doc: str, ps: list[Page]) -> None:
    """Set each page's ``look`` from a small picture of it: its size in
    points, the colour of its paper (the light pixels' median, so a tinted
    or greyed sheet in a scan stands out from white), how much ink it has,
    and its top band as a coarse grid of ink, to compare letterheads
    whatever their text (a logo reads as nothing). Only for documents with
    pages of their own (PDFs, images) whose original is here; a page
    without a picture keeps ``look`` None, and the detector does without."""
    try:
        import numpy as np
        meta = m.objects.get_object(m.state.metadata[doc]) if doc in m.state.metadata else {}
        if meta.get("kind") not in ("pdf", "image"):
            return
        path = m.objects.path(m.state.documents[doc])       # here already: a limited copy doesn't fetch for this
        if path is None or not path.exists():
            return
        import pymupdf
        pdf = pymupdf.open(path) if meta["kind"] == "pdf" else pymupdf.open("pdf", pymupdf.open(path).convert_to_pdf())
    except Exception:
        return
    for p in ps:
        if p.n > len(pdf):
            break
        try:
            page = pdf[p.n - 1]
            pix = page.get_pixmap(dpi=LOOK_DPI, colorspace=pymupdf.csRGB, alpha=False)
            a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).astype(np.float32)
        except Exception:
            continue
        lum = a.mean(axis=2)
        light = lum >= np.percentile(lum, 60)
        paper = np.median(a[light], axis=0) if light.any() else np.array([255.0, 255.0, 255.0])
        ink = 1 - lum / max(float(np.median(lum[light])) if light.any() else 255.0, 1.0)
        band = ink[: max(4, int(pix.height * TOP_BAND))]
        rows, cols = 8, 32
        bh, bw = band.shape[0] // rows, band.shape[1] // cols
        grid = band[: bh * rows, : bw * cols].reshape(rows, bh, cols, bw).mean(axis=(1, 3)) if bh and bw else np.zeros((rows, cols))
        w, h = page.rect.width, page.rect.height
        p.look = {"size": (round(w), round(h)), "paper": tuple(int(x) for x in paper),
                  "ink": round(float((ink > 0.35).mean()), 4), "top": grid.clip(0, 1).round(3).flatten().tolist()}


def _inches(size) -> str:
    w, h = size
    f = lambda x: f"{x / 72:.1f}".rstrip("0").rstrip(".")
    return f"{f(w)}×{f(h)} in"


def tops_alike(a: dict, b: dict) -> float | None:
    """How alike two pages' top bands are (correlation of their ink, -1 to 1),
    or None if either is blank there."""
    import numpy as np
    x, y = np.array(a["top"]), np.array(b["top"])
    if x.sum() < 0.5 or y.sum() < 0.5 or x.std() < 1e-3 or y.std() < 1e-3:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _seen(prev: Page, cur: Page, ps: list[Page], i: int, text_letterhead: bool) -> list[tuple[float, str]]:
    """Signs from the page images: page size and shape, paper, letterhead art."""
    a, b = prev.look, cur.look
    if not a or not b:
        return []
    sig = []
    (wa, ha), (wb, hb) = a["size"], b["size"]
    if (wa > ha) != (wb > hb):
        sig.append((0.75, "turned to landscape" if wb > hb else "back to portrait"))
    elif abs(wa - wb) > 0.03 * max(wa, wb) or abs(ha - hb) > 0.03 * max(ha, hb):
        sig.append((1.5, f"a different page size ({_inches(a['size'])} → {_inches(b['size'])})"))
    if max(abs(x - y) for x, y in zip(a["paper"], b["paper"])) >= PAPER:
        sig.append((1.0, "different paper (its colour or tone changes)"))
    if not text_letterhead and b["ink"] > 0.002:
        near = tops_alike(a, b)
        for j in range(0, i - 1):
            o = ps[j].look
            same = o and tops_alike(o, b)
            # a letterhead is on a first page and not on the page after it (a running header is on both)
            after = ps[j + 1].look and tops_alike(o, ps[j + 1].look) if o else None
            if same is not None and same >= SAME_TOP and (near is None or near < UNLIKE_TOP) \
                    and (after is None or after < UNLIKE_TOP) and (j == 0 or _opening(ps[j])):
                sig.append((1.25, f"the top of the page looks like page {ps[j].n}'s (the same letterhead?)"))
                break
    return sig


def other_sheet(prev: Page, cur: Page) -> bool:
    """The page is a different size or on different paper from the one before."""
    a, b = prev.look, cur.look
    if not a or not b:
        return False
    (wa, ha), (wb, hb) = a["size"], b["size"]
    size = abs(wa - wb) > 0.03 * max(wa, wb) or abs(ha - hb) > 0.03 * max(ha, hb)
    return size or max(abs(x - y) for x, y in zip(a["paper"], b["paper"])) >= PAPER


PAPER = 18           # a change in the paper's colour, in any channel (0-255), that counts
SAME_TOP = 0.92      # tops this alike (correlation) look like the same letterhead
UNLIKE_TOP = 0.5     # and the pages between don't look like it


# -- do two pages look like the same document? ----------------------------------------------------

_STOP = set("""the and for that with this from have has are was were will would shall which their there been not but
any all its our your you they them his her who what when where into upon such than then also may can should each
other more most only same some these those under over about after before between per please thank dear very""".split())
_TOKEN = re.compile(r"[a-z0-9][a-z0-9/._-]*[a-z0-9]")


def tokens(p: Page) -> list[str]:
    """The page's words for comparing it with others: its body (running
    headers and footers are compared on their own), lower-cased, without
    short words, common words and bare numbers; reference numbers kept."""
    body = [x for x in p.plain if x not in p.head and x not in p.foot]
    out = []
    text = re.sub(r"([a-z])-\n([a-z])", r"\1\2", "\n".join(body).casefold())     # words hyphenated across lines
    for w in _TOKEN.findall(text):
        if w.isdigit() or w in _STOP or (len(w) < 4 and not any(c.isdigit() for c in w)):
            continue
        out.append(w)
    return out


def layout(p: Page) -> dict | None:
    """Where the text sits and how big it is: the body's left and right
    edges, its type size and its line spacing, as fractions of the page.
    None without word positions, or with too few lines to say."""
    bx = [b for x, b in zip(p.lines, p.boxes) if b and x not in p.head and x not in p.foot] if p.boxes else []
    if len(bx) < 8:
        return None
    lefts = sorted(b[0] for b in bx)
    rights = sorted(b[2] for b in bx)
    tops = sorted(b[1] for b in bx)
    gaps = sorted(b - a for a, b in zip(tops, tops[1:]) if 0.003 < b - a < 0.08)
    return {"left": lefts[len(lefts) // 5], "right": rights[len(rights) * 4 // 5],
            "size": sorted(b[3] for b in bx)[len(bx) // 2], "pitch": gaps[len(gaps) // 2] if gaps else None}


LAYOUT_SCALE = {"left": ("the left margin", 0.03), "size": ("the type size", 0.12), "pitch": ("the line spacing", 0.15)}
# (not the right margin: ragged text and tables move it too much within a document)


def layout_distance(a: dict | None, b: dict | None) -> tuple[float, str] | None:
    """How far apart two pages' layouts are (about 1: the variation within a
    document; 3 or more: set differently), and what differs most."""
    if not a or not b:
        return None
    parts = []
    for k, (what, scale) in LAYOUT_SCALE.items():
        if a.get(k) and b.get(k):
            d = abs(math.log(a[k] / b[k])) if k in ("size", "pitch") else abs(a[k] - b[k])
            parts.append((d / scale, what))
    return max(parts) if parts else None


def similarities(ps: list[Page]) -> list[dict | None]:
    """For each page after the first, how much it looks like the page before:
    ``text`` (cosine of the pages' words, each weighted by how rare it is in
    the document, so what every page says counts for little), ``shared``
    (the rarest words and reference numbers the two have in common), and
    ``layout`` (:func:`layout_distance`)."""
    toks = [tokens(p) for p in ps]
    n = len(ps)
    df: dict[str, int] = {}
    for t in toks:
        for w in set(t):
            df[w] = df.get(w, 0) + 1
    idf = {w: math.log((n + 1) / (c + 0.5)) for w, c in df.items()}
    vecs = []
    for t in toks:
        v: dict[str, float] = {}
        for w in t:
            v[w] = v.get(w, 0) + idf[w]
        norm = math.sqrt(sum(x * x for x in v.values())) or 1
        vecs.append({w: x / norm for w, x in v.items()})
    lays = [layout(p) for p in ps]
    def window(lo, hi):
        """The pages lo..hi-1 as one vector."""
        v: dict[str, float] = {}
        for k in range(max(lo, 0), min(hi, n)):
            for w, x in vecs[k].items():
                v[w] = v.get(w, 0) + x
        norm = math.sqrt(sum(x * x for x in v.values())) or 1
        return {w: x / norm for w, x in v.items()}

    out: list[dict | None] = [None]
    for i in range(1, n):
        a, b = vecs[i - 1], vecs[i]
        wa, wb = window(i - WINDOW, i), window(i, i + WINDOW)
        common = set(a) & set(b)
        rare = sorted((w for w in common if df[w] <= max(2, n // 3)), key=lambda w: (-idf[w], w))
        lay = layout_distance(lays[i - 1], lays[i])
        if lay and i >= 2 and lay[0] > SAME_LAYOUT:
            # a book's facing pages: margins mirrored, so like the page two back, not the one before
            two = layout_distance(lays[i - 2], lays[i])
            if two and two[0] <= SAME_LAYOUT * 2:
                lay = (two[0], two[1] + ", like page " + str(ps[i - 2].n))
        out.append({"text": round(sum(a[w] * b[w] for w in common), 2),
                    "around": round(sum(wa[w] * wb[w] for w in set(wa) & set(wb)), 2), "shared": rare[:5],
                    "words": (sum(len(toks[k]) for k in range(max(i - WINDOW, 0), i)),
                              sum(len(toks[k]) for k in range(i, min(i + WINDOW, n)))),
                    "layout": round(lay[0], 1) if lay else None, "differs": lay[1] if lay else None})
    return out


def _sameness(sim: dict | None) -> list[tuple[float, str]]:
    """Signs from how alike a page and the page before it look."""
    if not sim:
        return []
    sig = []
    enough = min(sim["words"]) >= 60
    if max(sim["text"], sim["around"]) >= SAME_TEXT:
        sig.append((-1.5, f"the same subject as the pages before (text similarity {max(sim['text'], sim['around']):.2f}"
                          + (f"; both mention {', '.join(sim['shared'][:3])})" if sim["shared"] else ")")))
    elif enough and sim["around"] <= OTHER_TEXT:
        sig.append((1.0, f"little in common with the pages before (text similarity {sim['around']:.2f})"))
    if sim["layout"] is not None:
        if sim["layout"] <= SAME_LAYOUT:
            sig.append((-0.5, "laid out like the page before"))
        elif sim["layout"] >= OTHER_LAYOUT:
            sig.append((1.0, f"laid out differently from the page before ({sim['differs']})"))
    if unlike(sim):
        sig.append((0.75, "neither its words nor its layout look like the page before"))
    return sig


def unlike(sim: dict | None) -> bool:
    """The pages look like different documents, in their words and their layout both."""
    return bool(sim) and min(sim["words"]) >= 60 and sim["around"] <= OTHER_TEXT * 2 / 3 \
        and sim["layout"] is not None and sim["layout"] >= OTHER_LAYOUT * 1.5


SAME_TEXT, OTHER_TEXT = 0.3, 0.06
WINDOW = 2          # pages either side compared as a whole ("around")
SAME_LAYOUT, OTHER_LAYOUT = 0.5, 2.0


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


def score(ps: list[Page], i: int, sim: dict | None = None) -> dict:
    """The evidence that a new piece starts at page ``ps[i]`` (0-based ``i`` ≥ 1).
    ``sim``: how alike it and the page before look (:func:`similarities`)."""
    prev, cur = ps[i - 1], ps[i]
    sig: list[tuple[float, str]] = list(_sameness(sim))

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
        # a lowercase start after an unfinished line is also what two documents' middle pages look
        # like; and a sentence doesn't go on onto a sheet of another size or paper
        if unlike(sim) or other_sheet(prev, cur):
            sig.append((-0.75, "a sentence seems to run on from the page before, though the pages look unalike"))
        else:
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

    # what the page images show (size, paper, letterhead art), when there are pictures
    sig += _seen(prev, cur, ps, i, any("letterhead or heading" in r for _, r in sig))

    # scanned and native pages side by side
    if prev.ocr is not None and cur.ocr is not None and prev.ocr != cur.ocr:
        sig.append((1.0, "a scanned page after a native one" if cur.ocr else "a native page after a scanned one"))

    total = sum(w for w, _ in sig)
    strength = 1 / (1 + math.exp(-(total - BIAS)))
    row = {"page": cur.n, "strength": round(strength, 2), "level": level_of(strength),
           "for": [r for w, r in sig if w > 0], "against": [r for w, r in sig if w < 0], "score": round(total, 2)}
    if sim:
        row["like_previous"] = {k: sim[k] for k in ("text", "around", "shared", "layout") if sim[k] not in (None, [])}
    return row


def level_of(strength: float) -> str | None:
    return next((name for name, t in LEVELS.items() if strength >= t), None)


def boundaries(ps: list[Page]) -> list[dict]:
    """Every page after the first, with the evidence that a piece starts there."""
    sims = similarities(ps)
    return [score(ps, i, sims[i]) for i in range(1, len(ps))]


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
    return [piece(ps, k + 1, [[a, (starts[k + 1] - 1) if k + 1 < len(starts) else len(ps)]])
            for k, a in enumerate(starts)]


def piece(ps: list[Page], k: int, ranges: list[list[int]], label=None, kind=None, summary=None) -> dict:
    """Piece ``k`` of a split: its page ranges, a label and kind (given, or
    read from its first page), its date if a date line says, and whether
    its printed numbering agrees with its length."""
    first = ps[ranges[0][0] - 1]
    found_label, found_kind = _title(first)
    row = {"piece": k, "ranges": ranges, "label": label or found_label}
    if len(ranges) == 1:
        row["pages"] = list(ranges[0])
    if kind or found_kind:
        row["kind"] = kind or found_kind
    if summary:
        row["summary"] = summary
    date = next((x.strip() for x in first.lines[:12] if _DATE_LINE.match(x)), None)
    if date:
        row["date_line"] = date
    nos = [n for a, b in ranges for n in range(a, b + 1)]
    totals = {ps[n - 1].total for n in nos if ps[n - 1].total}
    if len(totals) == 1:
        t = totals.pop()
        if t != len(nos):
            row["numbering"] = f"its pages say {t}, it has {len(nos)}"
    elif len(totals) > 1:
        row["numbering"] = "its pages give different page counts: " + ", ".join(str(t) for t in sorted(totals))
    return row


def parse_ranges(spec, n: int) -> list[list[int]]:
    """``"1-2,7"``, ``[1, 2]`` (one range), ``[[1, 2], [7, 7]]`` or ``5`` as
    sorted, merged ranges of pages 1..n."""
    if isinstance(spec, int):
        spec = [[spec, spec]]
    elif isinstance(spec, str):
        spec = [[int(x) for x in (part.split("-", 1) if "-" in part else (part, part))]
                for part in spec.replace(" ", "").split(",") if part]
    elif spec and all(isinstance(x, int) for x in spec):
        spec = [list(spec)] if len(spec) == 2 else [[x, x] for x in spec]
    out = []
    for a, b in sorted([list(r) for r in spec]):
        if not (1 <= a <= b <= n):
            raise ValueError(f"pages {a}-{b} aren't in 1-{n}")
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    if not out:
        raise ValueError("a piece needs pages")
    return out


def from_plan(ps: list[Page], plan: list[dict]) -> tuple[list[dict], list[int]]:
    """Pieces as someone decided them: each ``{"pages": "1-2,7", "label"?,
    "kind"?, "summary"?}``, in the order given. A piece may be in several
    ranges (a misfiled page, an enclosure scanned out of order). Pieces of
    one split can't share a page; pages in no piece (slip sheets, blank
    backs) are returned so the caller can say so."""
    rows, seen = [], {}
    for k, item in enumerate(plan, 1):
        if not isinstance(item, dict) or "pages" not in item:
            raise ValueError(f"piece {k} of the plan needs \"pages\"")
        ranges = parse_ranges(item["pages"], len(ps))
        for a, b in ranges:
            for n in range(a, b + 1):
                if n in seen:
                    raise ValueError(f"page {n} is in pieces {seen[n]} and {k}: pieces of one split don't overlap "
                                     "(record another split for another way of grouping)")
                seen[n] = k
        rows.append(piece(ps, k, ranges, item.get("label"), item.get("kind"), item.get("summary")))
    return rows, [n for n in range(1, len(ps) + 1) if n not in seen]


def read_plan(text: str) -> list[dict]:
    """A plan: a JSON array, or one JSON object per line."""
    import json
    text = text.strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def page_rows(ps: list[Page], bs: list[dict], which=None) -> list[dict]:
    """What each page shows at its edges, for reading a document's cut points
    without reading it all: its printed number, header and footer, first and
    last lines, how it is laid out, how much it is like the page before, and
    the evidence for starting a piece there."""
    by = {b["page"]: b for b in bs}
    out = []
    for p in ps:
        if which and p.n not in which:
            continue
        body = [x for x in p.plain if x not in p.head and x not in p.foot]
        row = {"page": p.n}
        if p.number is not None:
            row["printed"] = f"{p.number}" + (f" of {p.total}" if p.total else "")
        if p.ocr is not None:
            row["ocr"] = p.ocr
        if p.head:
            row["header"] = p.head
        row["top"] = body[:4]
        row["bottom"] = body[-3:] if len(body) > 4 else []
        if p.foot:
            row["footer"] = p.foot
        if not p.lines:
            row["blank"] = True
        if p.look:
            row["look"] = {"size": _inches(p.look["size"]), "paper": "#%02x%02x%02x" % p.look["paper"], "ink": p.look["ink"]}
        b = by.get(p.n)
        if b:
            row["starts_piece"] = {k: b[k] for k in ("strength", "level", "for", "against")}
            if b.get("like_previous"):
                row["like_previous"] = b["like_previous"]
        out.append(row)
    return out


def contact_sheet(m: Matter, doc: str, path, which=None, cols: int = 4, per_sheet: int = 16) -> list[str]:
    """Pictures of the pages, small and numbered, in a grid: to see what the
    text can't say (letterhead art, paper, handwriting, stamps, a page
    upside down). Several files (``-2``, ``-3``…) past ``per_sheet`` pages.
    Returns the files written."""
    import pymupdf
    from pathlib import Path
    from .produce import pages_for
    src = pages_for(m, doc)[0]
    nos = [n for n in range(1, len(src) + 1) if not which or n in which]
    path = Path(path)
    written = []
    cell_w, cell_h, pad = 200, 260, 18
    for s, at in enumerate(range(0, len(nos), per_sheet)):
        chunk = nos[at:at + per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        out = pymupdf.open()
        page = out.new_page(width=cols * cell_w, height=rows * (cell_h + pad))
        for j, n in enumerate(chunk):
            x, y = (j % cols) * cell_w, (j // cols) * (cell_h + pad)
            box = pymupdf.Rect(x + 6, y + pad, x + cell_w - 6, y + pad + cell_h - 6)
            page.draw_rect(box, color=(0.75, 0.75, 0.75), width=0.5)
            page.show_pdf_page(box, src, n - 1, keep_proportion=True)
            page.insert_text((x + 8, y + 13), f"p. {n}", fontsize=11, color=(0.8, 0.1, 0.1))
        f = path if s == 0 else path.with_name(f"{path.stem}-{s + 1}{path.suffix}")
        f.write_bytes(page.get_pixmap(dpi=110).tobytes("png"))
        written.append(str(f))
    return written


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
        ranges = r.get("ranges") or [r["pages"]]
        parts = [{"doc": doc, "select": {"page": a if a == b else [a, b]}} for a, b in ranges]
        pages = ", ".join(f"p. {a}" if a == b else f"pp. {a}–{b}" for a, b in ranges)
        summary = (f"{r['summary']} ({pages} of {doc_name})" if r.get("summary") else
                   f"{pages} of {doc_name}" + "".join(f"; {x}" for x in (r.get("kind"), r.get("date_line")) if x))
        out.append({"op": "define-bundle", "name": f"{doc_name} · {split_name} {r['piece']}/{n} · {r['label']}",
                    "parts": parts, "summary": summary, "from": f"split:{doc}",
                    "split": {"of": doc, "name": split_name, "piece": r["piece"], "pieces": n}})
    return out


def splits_list(ann, doc: str, doc_name: str, name: str | None = None) -> tuple[dict, list[dict]]:
    """(header, items): a document's recorded splits as a list file, a group
    per split, a row per piece (its first range; any others under it), so
    the user can step through the pieces with a preview."""
    from . import locators
    splits = recorded(ann, doc)
    if name is not None:
        if name not in splits:
            raise ValueError(f"{doc_name} has no split named {name!r}" +
                             (f"; its splits: {', '.join(splits)}" if splits else "; it has none (meta split --record)"))
        splits = {name: splits[name]}
    if not splits:
        raise ValueError(f"{doc_name} has no recorded splits: meta split {doc[:16]} proposes one, --record keeps it")
    items = []
    for sname, bs in splits.items():
        for b in bs:
            parts = [{**p, "doc": p["doc"]} for p in b["parts"]]
            label = b["name"].split(" · ", 2)[-1] if b["name"].count(" · ") >= 2 else b["name"]
            pages = ", ".join(x for x in ((("p. " + str(p["select"]["page"])) if isinstance(p.get("select", {}).get("page"), int)
                                          else f"pp. {p['select']['page'][0]}–{p['select']['page'][1]}")
                                         if (p.get("select") or {}).get("page") is not None else "whole"
                                         for p in parts))
            it = {"loc": locators.format(parts[0]), "group": sname,
                  "fields": {"piece": f"{b['split']['piece']}/{b['split']['pieces']}", "part": label, "pages": pages,
                             **({"summary": b["summary"]} if b.get("summary") else {}), "by": b["by"]}}
            if len(parts) > 1:
                it["children"] = [{"loc": locators.format(p), "fields": {"part": "(continued)"}} for p in parts[1:]]
            items.append(it)
    header = {"type": "list", "v": 1, "title": f"{doc_name}: " + ", ".join(splits) + (" splits" if len(splits) > 1 else " split"),
              "made_by": "view meta --splits", "query": f"recorded splits of {doc_name}",
              "columns": ["piece", "part", "pages", "summary", "tags"]}
    return header, items
