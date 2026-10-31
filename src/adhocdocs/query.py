"""The search query language.

==========================================  ==================================
``pallet shipment``                         both (AND is the default)
``"never received"``                        phrase
``ship*``                                   prefix
``pallet OR crate``, ``NOT draft``,         Boolean; ``-x`` is ``NOT x``. OR binds
``-draft``, ``( … )``                       tightest, then w/ and pre/, then NOT, then AND
``pallet w/5 received``                     within 5 words, either order
``pallet pre/5 received``                   within 5 words, in this order
``pallet /s received``, ``pallet /p received``  in the same sentence, or paragraph
``from:smith@acme.com`` ``to:`` ``cc:``     field filters (an address, or part of one)
``bcc:`` ``custodian:smith-j`` ``kind:email``
``name:memo``
``date:2026-03``, ``date:2026-03-01..2026-03-15``
``tag:hot``, ``-tag:privileged``            tags, read live
==========================================  ==================================

Boolean operators apply per document (``a b`` means both somewhere in the
document); proximity applies within a text. A word that normalizes to
several tokens (``44-B/7``) is a phrase.
"""

import re
from dataclasses import dataclass

from . import textnorm

FIELDS = ("from", "to", "cc", "bcc", "custodian", "kind", "date", "tag", "name")


class QueryError(ValueError):
    pass


@dataclass(frozen=True)
class Words:
    """A term (one word), phrase (several) or prefix (last word ends in *)."""
    words: tuple
    prefix: bool = False

    def fts(self) -> str:
        q = '"' + " ".join(self.words) + '"'
        return q + " *" if self.prefix else q


@dataclass(frozen=True)
class Near:
    left: object
    right: object
    n: int
    ordered: bool
    unit: str = "w"          # w (words), s (sentence) or p (paragraph)


@dataclass(frozen=True)
class Field:
    name: str
    value: str


@dataclass(frozen=True)
class And:
    items: tuple


@dataclass(frozen=True)
class Or:
    items: tuple


@dataclass(frozen=True)
class Not:
    item: object


@dataclass(frozen=True)
class All:
    """Matches every document (an empty query)."""


TOKEN = re.compile(r'''
    \s*(?:
      (?P<lp>\() | (?P<rp>\)) |
      (?P<phrase>-?"[^"]*") |
      (?P<near>(?:w|pre)/\d+|/[sp])(?=\s) |
      (?P<word>[^\s()"]+)
    )''', re.X | re.I)


def lex(q: str):
    pos, out = 0, []
    q = q.strip()
    while pos < len(q):
        m = TOKEN.match(q, pos)
        if not m or m.end() == pos:
            raise QueryError(f"can't parse query at: {q[pos:]}")
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


class Parser:
    def __init__(self, q: str):
        self.toks = lex(q)
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def parse(self):
        if not self.toks:
            return All()
        node = self.and_()
        if self.i < len(self.toks):
            raise QueryError(f"unexpected {self.peek()[1]!r}")
        return node

    # Precedence follows Westlaw and Lexis: OR binds tightest, then
    # proximity, then NOT, then AND.

    def and_(self):
        items = []
        while True:
            kind, val = self.peek()
            if kind is None or kind == "rp":
                break
            if (kind, val) == ("word", "AND"):
                self.take()
                continue
            if (kind, val) == ("word", "NOT"):
                self.take()
                items.append(Not(self.near()))
                continue
            items.append(self.near())
        if not items:
            raise QueryError("empty expression")
        return items[0] if len(items) == 1 else And(tuple(items))

    def near(self):
        node = self.or_()
        while self.peek()[0] == "near":
            op, n = self.take()[1].lower().split("/")
            if n in ("s", "p"):
                node = Near(node, self.or_(), 0, False, n)
            else:
                node = Near(node, self.or_(), int(n), op == "pre")
            for side in (node.left, node.right):
                if not positional(side):
                    raise QueryError("proximity needs words, phrases, OR groups or other proximity on each side")
        return node

    def or_(self):
        items = [self.primary()]
        while self.peek() == ("word", "OR"):
            self.take()
            items.append(self.primary())
        return items[0] if len(items) == 1 else Or(tuple(items))

    def primary(self):
        kind, val = self.take()
        if kind == "lp":
            node = self.and_()
            if self.take()[0] != "rp":
                raise QueryError("missing )")
            return node
        if kind == "phrase":
            neg = val.startswith("-")
            words = tuple(textnorm.words(val.strip('-"')))
            if not words:
                raise QueryError(f"no words in {val}")
            node = Words(words)
            return Not(node) if neg else node
        if kind == "word":
            neg = val.startswith("-") and len(val) > 1
            body = val[1:] if neg else val
            name, sep, value = body.partition(":")
            if sep and name.lower() in FIELDS:
                node = Field(name.lower(), value.strip('"'))
            else:
                prefix = body.endswith("*")
                words = tuple(textnorm.words(body.rstrip("*")))
                if not words:
                    raise QueryError(f"no searchable characters in {val!r}; try: search grep "
                                     "(or search all, to list every document)")
                node = Words(words, prefix)
            return Not(node) if neg else node
        if kind is None:
            raise QueryError("query ends too early")
        raise QueryError(f"unexpected {val!r}")


def parse(q: str):
    return Parser(q).parse()


def positional(node) -> bool:
    """Whether a node matches at positions in the text (so has spans)."""
    if isinstance(node, (Words, Near)):
        return True
    return isinstance(node, Or) and all(positional(i) for i in node.items)


def leaves(node, positive=True):
    """(Words node, is_positive) for every text leaf."""
    if isinstance(node, Words):
        yield node, positive
    elif isinstance(node, Near):
        yield from leaves(node.left, positive)
        yield from leaves(node.right, positive)
    elif isinstance(node, (And, Or)):
        for i in node.items:
            yield from leaves(i, positive)
    elif isinstance(node, Not):
        yield from leaves(node.item, not positive)


def has_text(node) -> bool:
    return any(True for _ in leaves(node))


def to_text(node) -> str:
    """Back to query syntax, normalized (for --explain)."""
    if isinstance(node, Words):
        s = " ".join(node.words)
        s = f'"{s}"' if len(node.words) > 1 else s
        return s + "*" if node.prefix else s
    if isinstance(node, Near):
        op = f"/{node.unit}" if node.unit != "w" else f"{'pre' if node.ordered else 'w'}/{node.n}"
        return f"({to_text(node.left)} {op} {to_text(node.right)})"
    if isinstance(node, Field):
        return f"{node.name}:{node.value}"
    if isinstance(node, And):
        return "(" + " AND ".join(to_text(i) for i in node.items) + ")"
    if isinstance(node, Or):
        return "(" + " OR ".join(to_text(i) for i in node.items) + ")"
    if isinstance(node, Not):
        return f"NOT {to_text(node.item)}"
    return "*"


# -- matching within a token list ------------------------------------------------

ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "inc", "co", "corp", "ltd", "llc", "no", "nos", "v", "vs", "st", "jr", "sr",
                 "u.s", "e.g", "i.e", "etc", "art", "sec", "fig", "p", "pp", "cf", "id", "al", "jan", "feb", "mar",
                 "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "a.m", "p.m"}
_SENTENCE_END = re.compile(r"""([.!?])["”’')\]]*\s+(?=["“‘(\[]?[A-Z0-9])""")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n|\f")


class Units:
    """Which sentence and paragraph each token is in, for /s and /p.
    Paragraphs end at blank lines, page breaks, and the ends of ``paragraph``,
    ``list-item``, ``heading`` and ``row`` segments; sentences also end at
    ., ! or ? before a capital, except after common abbreviations."""

    def __init__(self, text: str, offsets, segments=()):
        self.text, self.offsets, self.segments = text, offsets, segments
        self._ids = {}

    def _breaks(self, unit) -> list[int]:
        cuts = {m.start() for m in _PARAGRAPH_BREAK.finditer(self.text)}
        cuts |= {s["end"] for s in self.segments if s["kind"] in ("paragraph", "list-item", "heading", "row")}
        if unit == "s":
            for m in _SENTENCE_END.finditer(self.text):
                word = re.search(r"([\w.]+)$", self.text[max(0, m.start() - 12):m.start()])
                if m.group(1) == "." and word and word.group(1).lower().rstrip(".") in ABBREVIATIONS:
                    continue
                cuts.add(m.end(1))
        return sorted(cuts)

    def ids(self, unit) -> list[int]:
        if unit not in self._ids:
            cuts, out, k = self._breaks(unit), [], 0
            for i in range(len(self.offsets) // 2):
                start = self.offsets[2 * i]
                while k < len(cuts) and cuts[k] <= start:
                    k += 1
                out.append(k)
            self._ids[unit] = out
        return self._ids[unit]


def spans(node, toks: list[str], units: Units | None = None) -> list[tuple[int, int]]:
    """Token spans [i, j) where a Words or Near node matches. ``units`` is
    needed for /s and /p."""
    if isinstance(node, Words):
        w = node.words
        n = len(w)
        out = []
        for i in range(len(toks) - n + 1):
            if toks[i] != w[0] and not (n == 1 and node.prefix and toks[i].startswith(w[0])):
                continue
            ok = all(toks[i + k] == w[k] for k in range(n - 1))
            last = toks[i + n - 1]
            if ok and (last == w[-1] or (node.prefix and last.startswith(w[-1]))):
                out.append((i, i + n))
        return out
    if isinstance(node, Near):
        a, b = spans(node.left, toks, units), spans(node.right, toks, units)
        out = []
        if node.unit != "w":
            if units is None:
                raise QueryError("/s and /p need the text's sentences and paragraphs")
            ids = units.ids(node.unit)
            for sa in a:
                for sb in b:
                    if ids[sa[0]] == ids[sa[1] - 1] == ids[sb[0]] == ids[sb[1] - 1]:
                        out.append((min(sa[0], sb[0]), max(sa[1], sb[1])))
            return sorted(set(out))
        for sa in a:
            for sb in b:
                if sb[0] >= sa[1] and sb[0] - sa[1] < node.n:
                    out.append((sa[0], sb[1]))
                elif not node.ordered and sa[0] >= sb[1] and sa[0] - sb[1] < node.n:
                    out.append((sb[0], sa[1]))
        return sorted(set(out))
    if isinstance(node, Or):
        return sorted({sp for i in node.items for sp in spans(i, toks, units)})
    raise TypeError(node)
