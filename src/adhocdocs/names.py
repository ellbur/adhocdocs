"""People named in document text: name-shaped spans, and the evidence that
each is a person.

Rules, not a model: every mention says why it was taken (``basis``), so a
cast of characters built from them can be checked, and nothing leaves the
machine. A mention is a run of capitalized words, initials and titles
("José Ibáñez", "J. Ibáñez", "Mr. Delacroix-Hale", "THOMAS KETTERING"),
cut at words that aren't names (months, "Agreement", "LLP", …). Its basis
is any of:

``title``      Mr., Ms., Dr., Judge, … before it
``initial``    a given-name initial ("R. Ames")
``address``    an email address after it ("Evelyn Stroud <estroud@…>")
``by``         "By:", "/s/", "cc:", "signed by", "requested by", …
``self``       "I, Linnea Fairbrother, …"
``signature``  alone on its line after a sign-off ("Regards,")
``role``       a role beside it: "José Ibáñez, a dock associate", "driver
               R. Ames", "Raymond Voss, Controller", or a role on the next
               line ("Dock Associate"); the role is kept
``esq``        "Esq." after it

A mention with no basis is ``name`` only: it can be a company ("Atlantic
Gasket") as easily as a person, so it counts only for people known some
other way (email headers, or a mention with a basis).

Not found: first names alone ("José"), initials alone ("TK"), and names
whose words also appear in lowercase in the same text ("Will Brown" next
to "brown pallet"), since those are usually words, not names.
"""

import re
from dataclasses import dataclass

from . import textnorm

VERSION = 1   # part of the names index's settings

TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "judge", "justice", "hon", "honorable",
          "sgt", "det", "detective", "officer", "rev", "sir", "dame", "messrs"}
ROLES = ("associate|manager|supervisor|clerk|driver|controller|counsel|attorney|lawyer|paralegal|director|"
         "president|officer|engineer|analyst|coordinator|specialist|representative|agent|assistant|secretary|"
         "accountant|technician|inspector|foreman|operator|dispatcher|buyer|partner|owner|chairman|chair|ceo|"
         "cfo|coo|cto|vp|witness|deponent|employee|contractor|consultant|investigator|auditor|adjuster|broker|"
         "judge|notary|reporter|lead|treasurer|administrator|receptionist|salesperson|guard|mechanic|"
         "nurse|physician|founder|executive|superintendent|planner|scheduler|bookkeeper|cashier|handler")
ROLE_WORDS = set(ROLES.split("|"))
_R = rf"(?:{ROLES})s?"
_DET = r"(?:(?:a|an|the|our|their|his|her|its|my|your)\s+)?"
ROLE_BEFORE = re.compile(rf"\b{_DET}((?:[a-z]+\s+){{0,2}}{_R}),?\s+$")
ROLE_AFTER = re.compile(rf"^,\s+{_DET}((?:[a-z]+\s+){{0,2}}{_R})\b")
ROLE_PAREN = re.compile(rf"^\s*\({_DET}((?:[a-z]+\s+){{0,2}}{_R})\)")
ROLE_TITLED = re.compile(rf"^,\s+((?:[A-Z][a-z]+\s+){{0,2}}(?i:{_R}))\b")
ROLE_NEXT_LINE = re.compile(rf"^[ \t]*\n[ \t]*((?:[A-Z][a-z]+[ \t]+){{0,2}}(?i:{_R}))\b")
ESQ = re.compile(r"^,?\s*Esq\b", re.I)
ADDRESS = re.compile(r"^\s*<[^<>\s]+@[^<>\s]+>")
BY = re.compile(r"(?:\bby|/s/|\b(?:attn|attention|cc|bcc|to|from|name|signed|signature|witness)\s*:)\s*$", re.I)
SELF = re.compile(r"\bI,\s*$")
SIGN_OFF = re.compile(r"^(?:regards|best regards|kind regards|warm regards|thanks|thank you|many thanks|sincerely|"
                      r"yours truly|very truly yours|truly yours|respectfully|respectfully submitted|best|cheers|"
                      r"yours|all the best)\s*,?$", re.I)

STOP = set("""
dear hi hello hey thanks thank regards sincerely yours re fwd fw subject sent date page line lines
monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs fri sat sun
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
exhibit exhibits agreement order orders invoice purchase receipt report notice deposition examination
certificate memorandum memo court street st road rd avenue ave parkway pkwy drive boulevard blvd lane ln
suite ste box county state states united federal rule rules section title act code department dept office
dock bay inc incorporated llc llp lp ltd co corp corporation company group holdings bank systems services
supply freight lines marine logistics industries international national american association
the this that these those our your we he she they it its if in on at for and of to from as by per please
note notes see yes no ok okay q a an or not but so all any each every new old north south east west
general claimant respondent plaintiff plaintiffs defendant defendants appellant appellee petitioner
esq rpr ccr phd md jd cpa ii iii iv jr sr am pm
procurement accounts payable receivable dispatch sales support team desk help admin customer customers
billing finance legal hr payroll purchasing receiving shipping warehouse operations
background analysis availability assistance discussion introduction conclusion conclusions summary facts
argument overview purpose scope procedural history district circuit government reserve citizens customs
supp app ct ed rep doc
""".split()) | ROLE_WORDS
FUNCTION = set("that the a an our their his her its my your and or by to of with from for is was were said told "
               "per as at on in".split())

TOKEN = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*\.?")
LAST_FIRST = re.compile(r"\"?([^\W\d_]+(?:[-'’][^\W\d_]+)*),[ \t]*([^\W\d_]+(?:[-'’][^\W\d_]+)*)\"?[ \t]*<[^<>\s]+@[^<>\s]+>")
POSSESSIVE = re.compile(r"['’]s$")
CITATION = re.compile(r"\.\s*(?:\d|\(|\)|,|;|[A-Z][a-z]{2}\.)")
ORG_AFTER = re.compile(r"^\.?,?\s*(?:Inc|LLC|L\.L\.C|LLP|LP|Ltd|Co|Corp|Corporation|Company|GmbH|plc|S\.A)\b\.?")


@dataclass
class Mention:
    start: int
    end: int
    form: str           # as written, with the title
    given: str | None   # folded given name
    initial: str | None # folded first initial
    surname: str        # folded
    basis: list
    role: str | None = None


def key(word: str) -> str:
    return "".join(textnorm.fold(c) for c in word).replace("’", "'")


def _kind(word: str) -> str | None:
    w = POSSESSIVE.sub("", word.rstrip("."))
    if not w:
        return None
    if w.lower() in TITLES and (word.endswith(".") or len(w) > 3 or w.isupper() or w.istitle()):
        return "title"
    if len(w) == 1 and w.isupper() and word.endswith("."):
        return "initial"
    if w.isupper() and len(w) >= 2:
        return "caps"
    if w[0].isupper() and any(c.islower() for c in w[1:]):
        return "cap"
    return None


def _runs(text: str):
    """Runs of name-like tokens separated by single spaces: [(start, end, word, kind)].
    A full stop ends a run, except after a title or an initial."""
    run = []
    for m in TOKEN.finditer(text):
        word, kind = m.group(), _kind(m.group())
        if kind is None:
            if run:
                yield run
            run = []
            continue
        if run:
            prev = run[-1]
            if text[prev[1]:m.start()] != " " or (prev[2].endswith(".") and prev[3] not in ("title", "initial")):
                yield run
                run = []
        run.append((m.start(), m.end(), word, kind))
    if run:
        yield run


def _segments(run):
    """Split a run at stop words (a title is kept as the start of the next
    segment); all-caps and mixed-case words don't mix."""
    seg = []
    for tok in run:
        w = key(tok[2].rstrip("."))
        if tok[3] != "title" and w in STOP:
            if seg:
                yield seg
            seg = []
            continue
        if seg and (tok[3] == "caps") != (seg[-1][3] == "caps") and seg[-1][3] not in ("title", "initial") \
                and tok[3] not in ("title", "initial"):
            yield seg
            seg = []
        if tok[3] == "title" and seg and seg[-1][3] != "title":
            yield seg
            seg = []
        seg.append(tok)
    if seg:
        yield seg


def _line_bounds(text, s, e):
    ls = text.rfind("\n", 0, s) + 1
    le = text.find("\n", e)
    return ls, (le if le >= 0 else len(text))


def mentions(text: str) -> list[Mention]:
    words_only = re.sub(r"\S+@\S+|\w+://\S+|www\.\S+", " ", text)   # addresses aren't words
    lower = {t for t in (m.group().rstrip(".") for m in TOKEN.finditer(words_only)) if t.islower()}
    lower = {key(t) for t in lower}
    out = []
    for run in _runs(text):
        for seg in _segments(run):
            titles = [t for t in seg if t[3] == "title"]
            name = [t for t in seg if t[3] != "title"]
            if not name or len(name) > 4 or name[-1][3] == "initial":
                continue
            if len(name) == 1 and name[0][3] == "caps" and len(name[0][2].rstrip(".")) < 3:
                continue
            if any(t[3] == "title" for t in seg[seg.index(name[0]):]):
                continue
            s, e = name[0][0], name[-1][1]
            if name[-1][2].endswith(".") and name[-1][3] != "initial":
                e -= 1                                    # a sentence's full stop, not the name's
            if POSSESSIVE.search(text[s:e]):
                e -= 2                                    # Welch's
            surname = key(POSSESSIVE.sub("", name[-1][2].rstrip(".")))
            if name[0][3] == "initial" and (re.search(r"[^\W\d_]\.$", text[max(0, s - 2):s])
                                           or (len(surname) <= 5 and CITATION.match(text[e:e + 6]))):
                continue                                  # U.S. Customs, F. Supp. 2d, D. Tex. 2013
            first = name[0] if len(name) > 1 else None
            given = key(first[2].rstrip(".")) if first and first[3] != "initial" else None
            initial = (given or key(first[2]))[0] if first else None
            line_start = text[_line_bounds(text, s, e)[0]:s]
            if first and first[3] == "initial" and first[2][0] in "QA" and not line_start.strip():
                continue                                  # Q. and A. in a transcript
            if len(name) >= 3 and name[0][3] == "cap" and not titles \
                    and re.search(r"(?:^|[.?!:]\s|[\"“‘(]|\n)\s*$", text[max(0, s - 3):s] if s else ""):
                name = name[1:]                           # "Prepare Tom Kettering", "That's José Ibáñez"
                s = name[0][0]
                first = name[0]
                given = key(first[2].rstrip(".")) if first[3] != "initial" else None
                initial = (given or key(first[2]))[0]
            caps = all(t[3] == "caps" for t in name if t[3] != "initial")
            if len(name) == 1 and (titles == [] and (caps or not ROLE_BEFORE.search(text[_line_bounds(text, s, e)[0]:s]))):
                continue
            words = [key(t[2].rstrip(".")) for t in name if t[3] != "initial"]
            if surname in lower or (not titles and any(w in lower for w in words)) or ORG_AFTER.match(text[e:e + 16]):
                continue
            ls0, le0 = _line_bounds(text, s, e)
            if first and first[3] == "initial" and len(name) == 2 and not text[ls0:s].strip() \
                    and not text[e:le0].strip(" \t.:"):
                continue                                  # "I. Background", an outline heading
            basis, role = [], None
            if titles:
                basis.append("title")
            if first and first[3] == "initial":
                basis.append("initial")
            before, after = text[max(0, s - 60):titles[0][0] if titles else s], text[e:e + 120]
            if ADDRESS.match(after):
                basis.append("address")
            if BY.search(before):
                basis.append("by")
            if SELF.search(before):
                basis.append("self")
            if ESQ.match(after):
                basis.append("esq")
                role = "attorney"
            ls, le = _line_bounds(text, s, e)
            alone = not text[ls:titles[0][0] if titles else s].strip(" \t/s-–—•*") and not text[e:le].strip(" \t,")
            if alone:
                prev = text[:ls].rstrip("\n").rsplit("\n", 1)[-1].strip()
                if SIGN_OFF.match(prev):
                    basis.append("signature")
            for pat, ctx in ((ROLE_AFTER, after), (ROLE_PAREN, after), (ROLE_TITLED, after),
                             (ROLE_BEFORE, before), *(((ROLE_NEXT_LINE, after),) if alone else ())):
                rm = pat.search(ctx)
                if rm and not caps and not rm.group(1).isupper():
                    words = rm.group(1).lower().split()
                    cut = max((i + 1 for i, w in enumerate(words) if w in FUNCTION), default=0)
                    words = words[cut:]
                    role = " ".join(words)
                    basis.append("role")
                    break
            if caps and not titles and not set(basis) & {"initial", "address", "by", "self", "esq", "signature"}:
                basis = []
            form = text[titles[0][0] if titles else s:e]
            out.append(Mention(s, e, " ".join(form.split()), given, initial, surname, basis or ["name"], role))
    for lm in LAST_FIRST.finditer(text):                  # "Muller, Zoe" <zmuller@…>, as mail programs write it
        last, first = lm.group(1), lm.group(2)
        if _kind(last) in ("cap", "caps") and _kind(first) in ("cap", "caps") and key(last) not in STOP:
            out.append(Mention(lm.start(1), lm.end(2), f"{last}, {first}", key(first), key(first)[0], key(last),
                               ["address"]))
    out.sort(key=lambda x: x.start)
    return out
