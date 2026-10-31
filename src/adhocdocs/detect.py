"""Entity detection: spans of a text that look like entities, by rules.

This is the entity layer's *suggestions* (see ENTITIES.md). It runs in the
``entities`` index, once per layer text, at about a megabyte of text a
second, so it is done long before anyone hovers over a word. Nothing is
decided here: a detection only lets the user point at the span, and tells
the user and Claude why it was taken. Known entities (the entity list in
the annotation log) are matched separately, live, by their aliases.

Kinds:

``person``        a name with evidence that it is a person (:mod:`adhocdocs.names`:
                  a title, an initial, a role, a signature, …)
``organization``  a capitalized name ending in a company form: "Harrowgate
                  Fastener Supply Co.", "BELLWETHER MARINE SYSTEMS, INC."
``term``          a defined term and every later use of it in the same text:
                  ``Harrowgate … Co., an Ohio corporation (“Seller”)`` or
                  ``“Delivery” means …``. A term's ``means`` is the words it
                  stands for, and ``defined`` the range that defines it.
                  Terms are local: "Buyer" in another document is another
                  Buyer, so a term is never joined across texts.
``identifier``    a document or transaction number: "INV-2026-0347",
                  "PO-BMS-26-0112", "TCF-889104". ``label`` is the word
                  before it when it says what it is ("invoice", "PO").
                  "INV-2026/0347" has the same key as "INV-2026-0347".

Every detection has a ``key``: detections with the same key in different
texts are the same candidate. A term's ``means`` and ``defined`` come from
its own text, though: "Buyer" in another document is another Buyer.
"""

import re
from dataclasses import asdict, dataclass, field

from . import names, textnorm

VERSION = 1   # part of the entities index's settings


@dataclass
class Detection:
    start: int
    end: int
    kind: str
    form: str            # as written
    key: str
    basis: list = field(default_factory=list)
    role: str | None = None        # person: a role found beside the name
    label: str | None = None       # identifier: what the text calls it
    means: str | None = None       # term: what it stands for
    defined: list | None = None    # term: [start, end] of the definition

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, [])}


def _fold(s: str) -> str:
    return " ".join("".join(textnorm.fold(c) for c in s).split())


# -- people ----------------------------------------------------------------------------------

def people(text: str) -> list[Detection]:
    from .analysis import _display
    out = []
    for x in names.mentions(text):
        if x.basis == ["name"]:
            continue                      # name-shaped only: as likely a company as a person
        out.append(Detection(x.start, x.end, "person", x.form, "person:" + _fold(_display(x.form)),
                             x.basis, role=x.role))
    return out


# -- organizations --------------------------------------------------------------------------

SUFFIX = r"(?:Inc|Incorporated|LLC|L\.L\.C|LLP|L\.L\.P|LP|L\.P|Ltd|Limited|Co|Corp|Corporation|Company|GmbH|AG|plc|PLC|S\.A|N\.A|PLLC|P\.C)"
_WORD = r"(?:[A-Z][\w’'&.-]*|&|of|and|the|for|de|du|la)"
ORG = re.compile(rf"(?<![\w])((?:[A-Z][\w’'&-]*\.?)(?: +{_WORD}){{0,6}}?),? +((?i:{SUFFIX}))(\.)?(?![\w])")
# a name broken across lines, after a lowercase word: "… between Bellwether Marine\nSystems, Inc."
WRAPPED = re.compile(r"(?:^|[ \t])[a-z]+ +((?:[A-Z][\w’'&-]* +){0,4}[A-Z][\w’'&-]*) *\n *$")
ORG_LEAD_STOP = {"the", "a", "an", "this", "that", "and", "of", "by", "between", "with", "from", "to", "for", "dear",
                 "re", "fw", "fwd", "among", "said", "our", "your", "their", "its", "his", "her", "summary"}
# a party or role before a name ends whatever came before it: "Receiving Supervisor of Respondent Bellwether …"
PARTY = {"claimant", "claimants", "respondent", "respondents", "plaintiff", "plaintiffs", "defendant", "defendants",
         "petitioner", "petitioners", "appellant", "appellants", "appellee", "appellees", "client", "debtor", "creditor",
         "buyer", "seller", "vendor", "customer", "carrier", "shipper", "consignee"} | names.ROLE_WORDS
ABBREV = re.compile(r"^(?:[A-Z]|[A-Z][a-z]{1,5})\.$")    # C. H. · Transp. · Elecs. · Rd.


def organizations(text: str) -> list[Detection]:
    out = []
    for m in ORG.finditer(text):
        words = m.group(1).split()
        if m.group(2)[0].islower():      # "an Ohio corporation" is a description, not a name
            continue
        for i in range(len(words) - 1, -1, -1):
            w = words[i]
            if w.lower().rstrip(".,") in PARTY or (w.endswith(".") and not ABBREV.match(w) and i < len(words) - 1):
                words = words[i + 1:]     # "Summary. Bellwether …", "… of Respondent Bellwether …"
                break
        while words and words[0].lower().rstrip(".") in ORG_LEAD_STOP:
            words.pop(0)
        if not words or not words[0][0].isupper():
            continue
        if any(w[0].islower() and w not in ("of", "and", "the", "for", "de", "du", "la", "&") for w in words):
            continue
        start = m.start(1) + m.group(1).index(words[0])
        if start == m.start(1) and text[start - 1:start] == "\n":
            w = WRAPPED.search(text, max(0, start - 80), start)
            if w and w.end() == start:
                start = w.start(1)
        end = m.end(3) if m.group(3) and m.group(2).lower() in ("inc", "co", "corp", "ltd") else m.end(2)
        form = " ".join(text[start:end].split())
        name = re.sub(rf",?\s+{SUFFIX}\.?$", "", form, flags=re.I)
        key = "organization:" + _fold(re.sub(r"[.,]", "", name))
        out.append(Detection(start, end, "organization", form, key, ["suffix"]))
    return out


# -- defined terms -----------------------------------------------------------------------------

Q = "\"“”"
TERM = rf"[{Q}]([A-Z][\w’'&./-]*(?:[ \t]+[\w’'&./-]+){{0,5}})[{Q}]"
# Acme Co. (“Seller”) · this Agreement (the “Agreement”) · (collectively, the “Goods”) · (hereinafter “Buyer”)
TERM_PAREN = re.compile(rf"\((?:(?:collectively|each|together|individually)\s*,?\s+)?(?:hereinafter\s+(?:referred\s+to\s+as\s+)?)?"
                        rf"(?:the\s+|an?\s+)?{TERM}\)")
# “Delivery” means … · "Goods" shall mean …
TERM_MEANS = re.compile(rf"(?:^|(?<=[\s(]))[“\"]([A-Z][\w’'&./-]*(?:[ \t]+[\w’'&./-]+){{0,5}})[”\"]\s+(?:shall\s+)?(?:means?|refers?\s+to|has\s+the\s+meaning)\b")
CLAUSE_START = re.compile(r"(?:\bbetween\s+|\bamong\s+|\band\s+(?=[A-Z])|\bby\s+(?=[A-Z])|\)\s*,?\s*|;\s*|:\s*|\n\s*\n|"
                          r"(?<=[.!?])\s+(?=[A-Z])(?<![A-Z][a-z]\.\s)(?<!\b[A-Z]\.\s)|(?<=[.!?:])\n)")
# lowercase words that can be part of what a term stands for: "Acme Co., an Ohio corporation", "… dated May 1, 2025"
ANTECEDENT_LOWER = {"of", "and", "the", "a", "an", "dated", "corporation", "company", "limited", "liability", "partnership",
                    "organized", "under", "laws", "de", "du", "la", "&", "its", "subsidiaries", "affiliates", "for",
                    "inc", "inc.", "llc", "co.", "corp.", "ltd."}


def _antecedent(text: str, paren: int) -> tuple[int, int] | None:
    """The words a parenthetical term stands for: within its clause (at most
    160 characters back), from the first capitalized word after which come
    only names, numbers and the few lowercase words that join them."""
    lo = max(0, paren - 160)
    seg = text[lo:paren]
    starts = [m.end() for m in CLAUSE_START.finditer(seg)]
    # a line with no lowercase letters is a heading, not part of the clause
    starts += [m.end() for m in re.finditer(r"\n", seg) if not any(c.islower() for c in seg[:m.start()].rsplit("\n", 1)[-1])]
    base = lo + max(starts, default=0)
    e = paren
    while e > base and text[e - 1] in " \t\n,":
        e -= 1
    words = list(re.finditer(r"\S+", text[base:e]))
    def ok(w):
        w = w.strip(",.;’'\"“”()")
        return not w or not w[0].isalpha() or w[0].isupper() or w.lower() in ANTECEDENT_LOWER
    for i, w in enumerate(words):
        if w.group()[0].isupper() and all(ok(x.group()) for x in words[i:]):
            s = base + w.start()
            if re.match(r"(?:This|That|The|Said)\s", text[s:e]) and re.match(r"(?:This|That|The|Said)\s+[A-Z]", text[s:e]) is None:
                continue
            if re.match(r"(?:This|That|The|Said)\s+", text[s:e]):
                s += re.match(r"(?:This|That|The|Said)\s+", text[s:e]).end()
            return (s, e) if e - s >= 2 else None
    return None


def _sentence_end(text: str, i: int) -> int:
    m = re.compile(r"[.;](?=\s|$)|\n").search(text, i)
    return m.end() if m else len(text)


def terms(text: str) -> list[Detection]:
    defs = {}                        # term -> (start, end of the term in the definition, means, defined range)
    for m in TERM_PAREN.finditer(text):
        term = " ".join(m.group(1).split())
        if term in defs or len(term) < 2:
            continue
        ant = _antecedent(text, m.start())
        means = " ".join(text[ant[0]:ant[1]].split()) if ant else None
        if means and _fold(means) == _fold(term):
            means = None
        if ant:
            defined = [ant[0], m.end()]
        else:                            # the sentence it is defined in
            b = max(text.rfind("\n", 0, m.start()), *(text.rfind(p, 0, m.start()) for p in (". ", "; "))) + 1
            defined = [b + len(text[b:m.start()]) - len(text[b:m.start()].lstrip()), m.end()]
        defs[term] = (m.start(1), m.end(1), means, defined)
    for m in TERM_MEANS.finditer(text):
        term = " ".join(m.group(1).split())
        if term in defs:
            continue
        end = _sentence_end(text, m.end())
        means = " ".join(text[m.end():end].strip(" \t.;").split())
        defs[term] = (m.start(1), m.end(1), means[:200] or None, [m.start(), end])
    out = []
    for term, (ts, te, means, defined) in defs.items():
        key = "term:" + _fold(term)
        out.append(Detection(ts, te, "term", term, key, ["definition"], means=means, defined=defined))
        # later uses: the same words, capitalized as defined, as whole words
        pat = re.compile(r"(?<![\w])" + r"\s+".join(map(re.escape, term.split())) + r"(?![\w])")
        for u in pat.finditer(text):
            if u.start() == ts:
                continue
            out.append(Detection(u.start(), u.end(), "term", " ".join(u.group().split()), key, ["use"],
                                 means=means, defined=defined))
    return out


# -- identifiers ---------------------------------------------------------------------------------

IDENT = re.compile(r"(?<![\w/-])([A-Z]{2,6}(?:-[A-Z0-9]+)(?:[-/][A-Z0-9]+){0,3})(?![\w/-])")
IDENT_STOP = {"COVID", "ISO", "EN", "SARS", "UTF", "SHA", "MD", "RFC", "IEC", "ANSI", "ASTM", "DIN", "MIL", "UTC",
              "GMT", "FAQ", "CC", "ID"}
LABEL = re.compile(r"\b(invoice|inv|purchase\s+order|po|order|bill\s+of\s+lading|bol|pro|tracking|claim|case|ticket|"
                   r"report|receipt|shipment|contract|agreement|account|acct|reference|ref|docket|no)\.?\s*(?:no\.?|number|#)?\s*:?\s*$", re.I)


def identifiers(text: str) -> list[Detection]:
    out = []
    for m in IDENT.finditer(text):
        ident = m.group(1)
        if ident.split("-")[0] in IDENT_STOP:
            continue
        if sum(c.isdigit() for c in ident) < 3:
            continue
        lm = LABEL.search(text[max(0, m.start() - 24):m.start()])
        label = " ".join(lm.group(1).lower().split()) if lm else None
        out.append(Detection(m.start(), m.end(), "identifier", ident, "identifier:" + ident.upper().replace("/", "-"),
                             ["pattern"], label=label))
    return out


# -- all of them -------------------------------------------------------------------------------

PRIORITY = {"term": 0, "identifier": 1, "organization": 2, "person": 3}


def detect(text: str) -> list[Detection]:
    """Every detection in a text, without overlaps: where two overlap, the
    longer wins ("Bellwether Marine Systems, Inc." over the term
    "Bellwether"), then a term, an identifier, an organization, a person."""
    found = terms(text) + identifiers(text) + organizations(text) + people(text)
    found.sort(key=lambda d: (-(d.end - d.start), PRIORITY[d.kind], d.start))
    taken: list[tuple[int, int]] = []
    out = []
    for d in found:
        if any(d.start < e and s < d.end for s, e in taken):
            continue
        taken.append((d.start, d.end))
        out.append(d)
    out.sort(key=lambda d: d.start)
    return out
