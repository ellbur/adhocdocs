"""Comparing a quotation with its source, word by word. See docs/QUOTEBENCH.md.

A quotation is verbatim when its words are a contiguous run of the source's
words, apart from marked changes: an ellipsis for an omission, brackets for
an alteration ([T]he, [the vendor]), [sic]. What counts as the same word is
deliberately forgiving about things that are not the quoter's choice or
that reasonable people transcribe differently:

- whitespace and line breaks;
- straight and curly quote marks and apostrophes; dashes;
- a hyphen at the end of a line (``ex-`` / ``pected``): joined or kept;
- letter case is compared, but reported apart from word errors (a changed
  capital at the start of a quotation is a style slip, not a misquote);
- punctuation is compared and reported apart from word errors.

Everything else (a word dropped, added, changed or moved) is a word error.
"""

import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
# a dash between words separates them ("sent—or"); only a hyphen joins ("well-known")
QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "„": '"', "″": '"',
                        "–": " – ", "—": " – ", "‑": "-", "‒": " – ", "―": " – ", "\u00a0": " "})
# a hyphen at a line end, between letters: the word may be joined or kept
LINE_HYPHEN = re.compile(r"(?<=[^\W\d_])-[ \t]*\n[ \t]*(?=[^\W\d_])")
# a word may carry a source's own bracketed alteration: "[o]nly"
TOKEN = re.compile(r"(?:\[[^\W_]{1,3}\])?[^\W_]+(?:['\-][^\W_]+)*|[^\w\s]")
ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:…|\.\s?\.\s?\.)\s*\]|…|\.\s\.\s\.(?:\s\.)?|\.\.\.\.?)\s*")
BRACKET = re.compile(r"\[([^\[\]]{0,60})\]")


def clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(INVISIBLE).translate(QUOTES)
    return LINE_HYPHEN.sub("-", text)


@dataclass
class Tok:
    text: str
    start: int            # offset in the cleaned text
    end: int
    word: bool
    marked: bool = False  # inside brackets in a quotation: an alteration the quoter marked

    @property
    def key(self) -> str:
        return re.sub(r"[\-'\[\]]", "", self.text.lower()) if self.word else self.text


def tokens(text: str, marked_spans=()) -> list[Tok]:
    out = []
    for m in TOKEN.finditer(text):
        t = m.group()
        word = bool(re.match(r"\[?[^\W_]", t)) and len(t) > 1 or bool(re.match(r"[^\W_]", t))
        marked = any(a < m.end() and m.start() < b for a, b in marked_spans)
        out.append(Tok(t, m.start(), m.end(), word, marked))
    return out


class Source:
    """A document's text, tokenized once, with an index for finding where a quotation is."""

    def __init__(self, name: str, text: str):
        self.name = name
        self.raw = text
        self.text = clean(text)
        self.toks = tokens(self.text)
        self.words = [t for t in self.toks if t.word]
        self.keys = [t.key for t in self.words]
        self.grams: dict[tuple, list[int]] = {}
        for i in range(len(self.keys) - 2):
            self.grams.setdefault(tuple(self.keys[i:i + 3]), []).append(i)


@dataclass
class Segment:
    """One run of a quotation between ellipses, and where it matched."""
    words: list[Tok]
    text: str = ""
    src_start: int | None = None      # word index in the source
    src_end: int | None = None
    errors: list[dict] = field(default_factory=list)
    marked: list[dict] = field(default_factory=list)
    case: list[dict] = field(default_factory=list)
    matched: int = 0


# a line-end hyphen copied with the line break as a space: "ex- pected"
COPIED_HYPHEN = re.compile(r"(?<=[^\W\d_])- (?=[a-z])")


def parse_quotation(q: str) -> list[tuple[str, list[Tok]]]:
    """The quotation's word runs, split at ellipses; bracketed text is marked
    (and [sic] dropped)."""
    q = COPIED_HYPHEN.sub("-", clean(q))
    q = re.sub(r"\[\s*sic\s*\]", " ", q, flags=re.I)
    runs = []
    for part in ELLIPSIS.split(q):
        # brackets out, their contents marked: "[T]he" is the word "The", altered
        text, spans, pos = "", [], 0
        for m in BRACKET.finditer(part):
            text += part[pos:m.start()]
            spans.append((len(text), len(text) + len(m.group(1))))
            text += m.group(1)
            pos = m.end()
        text += part[pos:]
        toks = [t for t in tokens(text, spans) if t.word]
        if toks:
            runs.append((text, toks))
    return runs


def locate(src: Source, words: list[Tok], after: int = 0) -> int | None:
    """Where in the source a run of words most likely starts: the start most of its 3-grams vote for."""
    keys = [t.key for t in words]
    votes = Counter()
    for i in range(len(keys) - 2):
        for p in src.grams.get(tuple(keys[i:i + 3]), ()):
            if p - i >= after - 5:
                votes[(p - i) // 8] += 1
    if not votes and len(keys) < 3:
        for p, k in enumerate(src.keys[after:], after):
            if src.keys[p:p + len(keys)] == keys:
                return p
        return None
    if not votes:
        return None
    bucket, n = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))
    if n < max(1, (len(keys) - 2) // 10):
        return None
    starts = [p - i for i in range(len(keys) - 2) for p in src.grams.get(tuple(keys[i:i + 3]), ())
              if (p - i) // 8 == bucket]
    return Counter(starts).most_common(1)[0][0] if starts else bucket * 8


def _words(toks) -> str:
    return " ".join(t.text for t in toks)


def align(src: Source, words: list[Tok], after: int = 0) -> Segment:
    seg = Segment(words)
    start = locate(src, words, after)
    if start is None:
        seg.errors.append({"type": "not-found", "quote": _words(words)})
        return seg
    lo = max(0, start - 40)
    hi = min(len(src.words), start + int(len(words) * 1.5) + 40)
    win = src.keys[lo:hi]
    qkeys = [t.key for t in words]
    sm = difflib.SequenceMatcher(None, win, qkeys, autojunk=False)
    ops = sm.get_opcodes()
    # the source around the quotation isn't part of it
    while ops and ops[0][0] == "delete":
        ops.pop(0)
    while ops and ops[-1][0] == "delete":
        ops.pop()
    if not ops:
        seg.errors.append({"type": "not-found", "quote": _words(words)})
        return seg
    # a changed word at either end: only as many source words as the quotation has there
    tag, i1, i2, j1, j2 = ops[0]
    if tag == "replace":
        ops[0] = (tag, max(i1, i2 - (j2 - j1)), i2, j1, j2)
    tag, i1, i2, j1, j2 = ops[-1]
    if tag == "replace":
        ops[-1] = (tag, i1, min(i2, i1 + (j2 - j1)), j1, j2)
    seg.src_start, seg.src_end = lo + ops[0][1], lo + ops[-1][2]
    for tag, i1, i2, j1, j2 in ops:
        s_toks, q_toks = src.words[lo + i1:lo + i2], words[j1:j2]
        if tag == "equal":
            seg.matched += i2 - i1
            for s, q in zip(s_toks, q_toks):
                if s.text.replace("-", "") != q.text.replace("-", "") and not q.marked:
                    seg.case.append({"source": s.text, "quote": q.text})
            continue
        # where in the source it happened, for a person to look
        at = src.words[lo + i1] if lo + i1 < len(src.words) else src.words[-1]
        ctx = src.text[max(0, at.start - 60):at.start + 60].replace("\n", " ")
        e = {"type": {"replace": "changed", "delete": "dropped", "insert": "added"}[tag],
             "source": _words(s_toks), "quote": _words(q_toks), "context": ctx, "at": at.start}
        if q_toks and all(t.marked for t in q_toks):
            seg.marked.append(e)            # [the vendor] for "it": marked, so not a misquote
        else:
            seg.errors.append(e)
    return seg


def compare(src: Source, quotation: str) -> dict:
    """How a quotation compares with a source: its word errors, marked
    changes, case and punctuation differences, and the source span it covers."""
    runs = parse_quotation(quotation)
    segs, after = [], 0
    moved = False
    for text, r in runs:
        s = align(src, r, after)
        if s.src_start is None and after:
            # before the run it follows: the quotation reorders the source
            s2 = align(src, r, 0)
            if s2.src_start is not None:
                s, moved = s2, True
        s.text = text
        segs.append(s)
        if s.src_end is not None:
            after = s.src_end
    errors = [e for s in segs for e in s.errors]
    nwords = sum(len(r) for _, r in runs)
    matched = sum(s.matched for s in segs)
    found = [s for s in segs if s.src_start is not None]
    out_of_order = moved or any(b.src_start < a.src_end for a, b in zip(found, found[1:]))
    span = None
    if found and not out_of_order:
        a, b = src.words[found[0].src_start], src.words[found[-1].src_end - 1]
        span = [a.start, b.end]
    return {
        "source": src.name,
        "words": nwords,
        "matched": matched,
        "segments": len(runs),
        "span": span,
        "verbatim": not errors and not out_of_order and bool(found),
        "errors": errors,
        "words_wrong": sum(max(len(e.get("source", "").split()), len(e.get("quote", "").split())) for e in errors),
        "out_of_order": out_of_order,
        "marked": [e for s in segs for e in s.marked],
        "case": [c for s in segs for c in s.case],
        "punctuation": sum(punctuation_differences(src, s) for s in found),
    }


def punctuation_differences(src: Source, seg: Segment) -> int:
    """How many punctuation marks differ between a run of the quotation and the span it matched."""
    q = [t.key for t in tokens(seg.text)]
    # only between its first and last words: the span is word to word
    while q and not re.match(r"\w", q[0]):
        q.pop(0)
    while q and not re.match(r"\w", q[-1]):
        q.pop()
    a, b = src.words[seg.src_start].start, src.words[seg.src_end - 1].end
    s = [t.key for t in tokens(src.text[a:b])]
    n = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, s, q, autojunk=False).get_opcodes():
        if tag != "equal":
            n += sum(1 for k in s[i1:i2] + q[j1:j2] if not re.match(r"\w", k) and k not in "\"'")
    return n


def coverage(expected: str, quotation: str) -> float:
    """The share of the expected passage's words the quotation reproduces."""
    src = Source("expected", expected)
    total = len(src.words)
    if not total:
        return 0.0
    covered = set()
    after = 0
    for _, r in parse_quotation(quotation):
        seg = align(src, r, after)
        if seg.src_start is None:
            continue
        after = seg.src_end
        sm = difflib.SequenceMatcher(None, src.keys[seg.src_start:seg.src_end], [t.key for t in r], autojunk=False)
        for blk in sm.get_matching_blocks():
            covered.update(range(seg.src_start + blk.a, seg.src_start + blk.a + blk.size))
    return round(len(covered) / total, 4)
