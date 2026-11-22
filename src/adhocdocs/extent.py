"""Finding a passage from roughly what it says: for ``page ref add --from``.

``--words`` asks for the passage's exact words, which means typing them all,
and so Claude doesn't: it writes scripts to find where a paragraph ends and
builds locators by hand.
Here Claude says roughly where a passage starts (``--from``), and either
roughly where it ends (``--to``) or how far it goes: a number of paragraphs
(``--paras``), a whole numbered item (``--item``: "(b) …" with the items
under it, up to the next item of its level), or the rest of a section or
rule (``--section``: up to its source credit or history note, or the next
heading).

The words are matched loosely: case, punctuation, quote marks, a hyphen at
a line end, and about one word in five may differ. The quotation is still
the layer's text at a range: nothing typed reaches the page, so loose
matching can pick the wrong passage but never change its words. To guard
against the wrong passage, it is found only when it is clear:

- the best match is good and no other is close (the same passage in an
  identical copy of the document isn't another), or the other close matches
  are outside what Claude read last (the read log, readlog.py: a passage
  shown by ``text`` is the context the words came from);
- otherwise nothing is written, and the candidates come back, each with its
  opening and closing words, to pick with ``--pick N``.

Where it looks, without ``--in``: what Claude read last, then what it read
recently, then every document (the words must then be in one document).
The result says which, and shows the passage's first and last words, the
words just outside it, how many words and paragraphs it has, and whether a
page break falls inside, so it can be checked without reading it back.
"""

import difflib
import re
from collections import Counter
from dataclasses import dataclass

WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
SOFT_HYPHEN = re.compile(r"-[ \t]*\n\f?[ \t]*")
GOOD = 0.9                  # a match this good needs no confirmation, unless another is as good
FLOOR = 0.7                 # below this, a match isn't offered at all
RECENT = 20                 # reads that count as "recently"
MAX_WORDS = 6000            # the farthest --to is looked for after --from
PREVIEW = 12
LONG = 120                  # a paragraph found in text without blank lines, longer than this, is asked about

# a numbered item at a line start: (a) (1) (A) (i) (iv) …, 1. a. A.
MARKER = re.compile(r"^[ \t]*\((?P<m>[0-9]{1,3}|[a-zA-Z]{1,5})\)[ \t]|^[ \t]*(?P<d>[0-9]{1,3}|[A-Z])\.[ \t]+(?=[A-Z])", re.M)
# what ends the last item of a rule or a section: its history, a source credit, the next heading
TAIL = re.compile(r"^[ \t]*(?:\((?:As amended|As added|Added|Amended|Pub\. ?L\.|"
                  r"(?:Jan|Feb|Mar|Apr|May|June?|July?|Aug|Sept?|Oct|Nov|Dec)[a-z]*\.? \d)|"
                  r"Rule \d+(?:\.\d+)?\. |§ ?\d+[a-z]?\. |Committee Notes?|Editorial Notes|Derivation\b)", re.M)
ROMAN = re.compile(r"^(?:i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|xiii|xiv|xv)$")


class ExtentError(Exception):
    pass


@dataclass
class Tok:
    key: str
    start: int
    end: int


def tokens(text: str) -> list[Tok]:
    """Words, lowercased, with a word broken by a line-end hyphen joined
    ("unrep-" / "resented" is "unrepresented")."""
    out = []
    for m in WORD.finditer(text):
        k = m.group().lower().replace("’", "'")
        if out and SOFT_HYPHEN.fullmatch(text, out[-1].end, m.start()):
            out[-1] = Tok(out[-1].key + k, out[-1].start, m.end())
            continue
        out.append(Tok(k, m.start(), m.end()))
    return out


def query_keys(words: str) -> list[str]:
    words = re.sub(r"(?<=[^\W\d_])- (?=[a-z])", "", words)          # "unrep- resented"
    return [m.group().lower().replace("’", "'") for m in WORD.finditer(words)]


class Layer:
    """One layer's text, tokenized, with an index of where each word is."""

    def __init__(self, doc: str, layer_id: str, name: str, text: str):
        self.doc, self.layer, self.name, self.text = doc, layer_id, name, text
        self.toks = tokens(text)
        self.keys = [t.key for t in self.toks]
        self.where: dict[str, list[int]] = {}
        for i, k in enumerate(self.keys):
            self.where.setdefault(k, []).append(i)
        self.blank_lines = text.count("\n\n") > 0.05 * max(1, text.count("\n"))

    def index_at(self, offset: int) -> int:
        lo, hi = 0, len(self.toks)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.toks[mid].end <= offset:
                lo = mid + 1
            else:
                hi = mid
        return lo


@dataclass
class Match:
    layer: Layer
    first: int          # token index of the first word matched
    last: int           # … and of the last
    score: float
    missed: int = 0     # words of the query not matched


def find(layer: Layer, q: list[str], lo: int = 0, hi: int | None = None) -> list[Match]:
    """Where the words ``q`` are, roughly, between token ``lo`` and ``hi``: the
    places most of their words point to, each scored by how much of ``q`` it
    reproduces in order."""
    hi = len(layer.keys) if hi is None else hi
    if not q:
        return []
    votes = Counter()
    for i, k in enumerate(q):
        for p in layer.where.get(k, ()):
            if lo <= p < hi:
                votes[p - i] += 1
    # a skipped or extra word shifts the rest by one: pool nearby starts
    pooled = Counter()
    for s, n in votes.items():
        for d in (-2, -1, 0, 1, 2):
            pooled[s + d] += n
    need = max(1, int(len(q) * (FLOOR - 0.15)))
    starts = sorted((s for s, n in pooled.items() if n >= need), key=lambda s: -pooled[s])
    out, taken = [], []
    for s in starts:
        if any(abs(s - t) <= len(q) for t in taken):
            continue
        a, b = max(lo, s - 3), min(hi, s + len(q) + 3)
        sm = difflib.SequenceMatcher(None, layer.keys[a:b], q, autojunk=False)
        blocks = [x for x in sm.get_matching_blocks() if x.size]
        if not blocks:
            continue
        matched = sum(x.size for x in blocks)
        score = matched / len(q)
        if score < FLOOR:
            continue
        taken.append(s)
        out.append(Match(layer, a + blocks[0].a, a + blocks[-1].a + blocks[-1].size - 1, round(score, 3),
                         len(q) - matched))
        if len(out) >= 8:
            break
    return sorted(out, key=lambda m: -m.score)


# -- how far a passage goes ----------------------------------------------------------------------

TRAILING = ".,;:!?)]\"”’'"


def end_after(text: str, offset: int) -> int:
    """A passage ending at a word takes the punctuation right after it."""
    while offset < len(text) and text[offset] in TRAILING:
        offset += 1
    return offset


def paragraph_end(layer: Layer, start: int, paras: int) -> int:
    """The end of the ``paras``-th paragraph from ``start``. A paragraph ends at
    a blank line; in text without blank lines (a PDF's), at a line ending a
    sentence that is followed by a numbered item, the end of a rule or
    section, an indented line or a page break."""
    text, pos, end = layer.text, start, len(layer.text)
    for n in range(paras):
        end = _next_break(layer, pos)
        if n < paras - 1:
            nm = WORD.search(text, end)
            if not nm:
                break
            pos = nm.start()
    return _trim(text, start, end)


def _next_break(layer: Layer, pos: int) -> int:
    text = layer.text
    if layer.blank_lines:
        m = re.compile(r"\n[ \t]*\n").search(text, pos)
        return m.start() if m else len(text)
    for m in re.compile(r"[.:;?!”\")][ \t]*\n").finditer(text, pos):
        nxt = m.end()
        if text.startswith("\f", nxt) or MARKER.match(text, nxt) or TAIL.match(text, nxt) or \
                re.match(r"[ \t]{2,}\S", text[nxt:nxt + 40]):
            return m.end() - 1
    return len(text)


def _paragraph_break_at(layer: Layer, pos: int) -> bool:
    """Whether the line break at ``pos`` ends a paragraph (and what follows isn't numbered)."""
    text = layer.text
    if layer.blank_lines:
        m = re.compile(r"\n[ \t]*\n[ \t]*").match(text, pos)
        return bool(m) and not MARKER.match(text, m.end() - len(m.group()) + m.group().rfind("\n") + 1)
    return _next_break(layer, max(0, pos - 2)) == pos


def _trim(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1] in " \t\n\f":
        end -= 1
    return end


def _kind(mark: str, letters_seen: list[str]) -> str:
    if mark.isdigit():
        return "digit"
    if mark.isupper():
        return "upper-roman" if ROMAN.match(mark.lower()) and len(mark) > 1 else "upper"
    if ROMAN.match(mark) and not (mark in ("i", "v", "x") and letters_seen and
                                  ord(letters_seen[-1]) == ord(mark) - 1):
        return "roman"
    return "lower"


RANK = {"lower": 0, "digit": 1, "upper": 2, "roman": 3, "upper-roman": 4}


def item_end(layer: Layer, start: int) -> int:
    """The end of the numbered item that starts at ``start``: before the next
    item of the same or a higher level, or a rule's history, a source credit
    or the next heading. An item without a number at the start runs to the
    next numbered line of any level, or to its paragraph's end."""
    text = layer.text
    line_start = text.rfind("\n", 0, start) + 1
    m = MARKER.match(text, line_start)
    letters = []
    level = None
    if m and m.start() <= start <= m.end() + 2:
        mark = m.group("m") or m.group("d")
        level = RANK[_kind(mark, letters)]
        if _kind(mark, letters) == "lower":
            letters.append(mark)
    pos = text.find("\n", start)
    while 0 <= pos < len(text):
        nxt = pos + 1
        if text.startswith("\f", nxt):
            nxt += 1
        if TAIL.match(text, nxt):
            return _trim(text, start, pos)
        mm = MARKER.match(text, nxt)
        if mm:
            mark = mm.group("m") or mm.group("d")
            k = _kind(mark, letters)
            if k == "lower":
                letters.append(mark)
            if level is not None and RANK[k] <= level:
                return _trim(text, start, pos)
        elif level is None and _paragraph_break_at(layer, pos):
            # unnumbered text (a section's opening) takes the numbered items under it, and stops
            # at the next paragraph that isn't one
            return _trim(text, start, pos)
        pos = text.find("\n", nxt)
    return _trim(text, start, len(text))


def section_end(layer: Layer, start: int) -> int:
    """The end of the section (or rule) ``start`` is in: before its source
    credit or history note, or the next section's or rule's heading."""
    m = TAIL.search(layer.text, layer.text.find("\n", start) + 1 or len(layer.text))
    return _trim(layer.text, start, m.start() if m else len(layer.text))


# -- previews ---------------------------------------------------------------------------------

def _words(text: str, n: int, tail=False) -> str:
    ws = text.split()
    return " ".join(ws[-n:] if tail else ws[:n])


HEADING = re.compile(r"^[ \t]*(?:Rule \d+(?:\.\d+)?\. .{3,120}|§ ?\d+[a-z]?\. .{3,200}|"
                     r"(?:[IVX]+|[A-Z]|\d{1,2})\.[ \t]+[A-Z][^\n]{2,100}(?<![.;:,]))[ \t]*$", re.M)


def heading_before(text: str, a: int) -> str | None:
    """The nearest heading above ``a`` ("Rule 31. …", "§ 4. …", "4. Cost of …")."""
    last = None
    for m in HEADING.finditer(text, max(0, a - 60000), a):
        last = m
    return " ".join(last.group().split())[:120] if last else None


def preview(layer: Layer, a: int, b: int) -> dict:
    text = layer.text
    body = text[a:b]
    out = {"words": len(body.split()), "starts": _words(body, PREVIEW), "ends": _words(body, PREVIEW, tail=True),
           "before": _words(text[max(0, a - 400):a], 8, tail=True), "after": _words(text[b:b + 400], 8),
           "paragraphs": (len(re.findall(r"\n[ \t]*\n", body)) + 1) if layer.blank_lines else None}
    under = heading_before(text, a)
    if under:
        out["under"] = under
    if "\f" in body:
        out["page_break"] = ("a page break falls inside: the running head or foot of the page may be in the "
                             "quotation (quote around it with two locators, an elided quotation)")
    # the paragraph or item began before the words given
    para = text.rfind("\n\n", 0, a) if layer.blank_lines else text.rfind("\n", 0, a)
    lead = text[para + 1 if para >= 0 else 0:a].split()
    if 0 < len(lead) <= 15:
        out["unit_begins_earlier"] = " ".join(lead)
    return out


# -- the whole search -------------------------------------------------------------------------------

def locate(scopes: list[tuple[str, list[tuple[Layer, int, int]]]], start: str, end: str | None = None,
           paras: int | None = None, item: bool = False, pick: int | None = None, section: bool = False) -> dict:
    """``scopes``: (name, [(layer, lo, hi)]) in the order to try, each a set of
    token ranges (what was read last, recently, …). The first scope with a
    clear answer gives it. Returns ``{"status": "found", "layer", "span",
    "found_in", "score", "preview"}`` or ``{"status": "confirm", "candidates",
    "why"}``."""
    q = query_keys(start)
    if len(q) < 3:
        raise ExtentError("--from needs at least three words of the passage's opening")
    qe = query_keys(end) if end else None
    if end is not None and len(qe) < 2:
        raise ExtentError("--to needs at least two words of the passage's end")
    seen = []
    for name, ranges in scopes:
        ms = [m for layer, lo, hi in ranges for m in find(layer, q, lo, hi)]
        if not ms:
            continue
        ms.sort(key=lambda m: -m.score)
        # the same place reached through two scopes' ranges is one match, and so is the same
        # place in an identical copy of the document (one email in two mailboxes)
        uniq, copies = [], {}
        for m in ms:
            same = next((u for u in uniq if u.first == m.first and
                         (u.layer.layer == m.layer.layer or u.layer.text == m.layer.text)), None)
            if same is None:
                uniq.append(m)
            elif same.layer.layer != m.layer.layer:
                copies.setdefault(id(same), []).append(m.layer.name)
        cands = []
        for m in uniq:
            c = _extent(m, qe, paras, item, section)
            if c and copies.get(id(m)):
                c["preview"]["copies"] = copies[id(m)]
            if c:
                cands.append(c)
        if not cands:
            continue
        cands.sort(key=lambda c: (c["missed"], -c["score"], c["span"][0]))      # as listed, for --pick
        if pick is not None:
            if not 1 <= pick <= len(cands):
                raise ExtentError(f"--pick {pick}: there are {len(cands)} candidates")
            return {"status": "found", **cands[pick - 1], "found_in": name}
        best = cands[0]
        # an exact match beats any loose one; a loose one needs a clear margin (two words)
        rivals = [c for c in cands[1:] if c["missed"] <= best["missed"] + (1 if best["missed"] else 0)]
        if rivals and not best["missed"]:
            # tied on the words: the one whose punctuation is the opening's too, if just one is
            same = [c for c in [best] + rivals if _literally(c, start)]
            if len(same) == 1:
                best, rivals = same[0], []
        if best["score"] >= GOOD and not rivals and not best.get("end_unsure") and not best.get("long"):
            return {"status": "found", **best, "found_in": name}
        seen = cands
        why = ("several passages match about as well" if rivals else
               "the end words matched loosely" if best.get("end_unsure") else
               f"it runs {best['preview']['words']} words: this text has no blank lines to end a paragraph "
               "(a log, a table, a transcript), so say where it ends with --to" if best.get("long") else
               "the best match is loose")
        return {"status": "confirm", "found_in": name, "why": why,
                "candidates": [{"n": i + 1, **{k: v for k, v in c.items() if k != "layer"},
                                "doc": c["layer"].doc, "name": c["layer"].name} for i, c in enumerate(cands[:6])]}
    if not seen:
        raise ExtentError("no passage starts with words like those (case, punctuation and about one word in "
                          "five may differ). Check the document, or give --in")
    return {}


def _flat(s: str) -> str:
    s = re.sub(r"(?<=[^\W\d_])-\s*\n\s*(?=[^\W\d_])", "", s).lower()
    s = s.translate(str.maketrans("“”‘’", "\"\"''"))
    return " ".join(s.split())


def _literally(c: dict, opening: str) -> bool:
    """Whether a candidate starts with the opening as typed, punctuation and all."""
    a, b = c["span"]
    return _flat(c["layer"].text[a:b]).lstrip("([\"'").startswith(_flat(opening).lstrip("([\"'"))


def _extent(m: Match, qe, paras, item, section=False) -> dict | None:
    layer = m.layer
    a = layer.toks[m.first].start
    # an opening mark belongs to the passage: "(b) …", "“Only …"
    while a > 0 and layer.text[a - 1] in "([“\"‘'":
        a -= 1
    unsure = False
    if qe:
        ends = find(layer, qe, m.first, min(len(layer.keys), m.first + MAX_WORDS))
        ends = [e for e in ends if e.first >= m.first]
        if not ends:
            return None
        e = min(ends, key=lambda e: e.first)        # the nearest end that matches
        unsure = e.score < GOOD
        b = end_after(layer.text, layer.toks[e.last].end)
        score = min(m.score, e.score)
        missed = m.missed + e.missed
    elif section:
        b = section_end(layer, a)
        score = m.score
    elif item:
        b = item_end(layer, a)
        score = m.score
    else:
        b = paragraph_end(layer, a, paras or 1)
        score = m.score
    if b <= a:
        return None
    long = not qe and not item and not section and not layer.blank_lines and len(layer.text[a:b].split()) > LONG
    out = {"layer": layer, "span": [a, b], "score": score, "missed": missed if qe else m.missed,
           "preview": preview(layer, a, b)}
    if unsure:
        out["end_unsure"] = True
    if long:
        out["long"] = True
    return out
