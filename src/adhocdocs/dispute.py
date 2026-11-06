"""A dispute: contrary claims, who holds which and when, and the tensions
(docs/DISPUTES.md).

A dispute has **sides**: claims that can't all be true ("the shipment was
never received", "it arrived at Dock 4"). Each side is a trace (TRACE.md):
a seed passage, a keyword query, or both, with passages included or
excluded. Its appearances are cut into **statements**: the sentences that
make the claim (for a query, the sentences that satisfy it by themselves),
cut again at ``;``, ``:``, dashes and "but", each with

- a **speaker**: the email's sender, the chat message's author, the
  attributed author of a quoted part, the witness or examiner in a
  transcript (by its Q. and A. turns), or else the file's author or the
  custodian it came from;
- a **stance** toward its side's claim, from the words around it:
  ``asserts``, ``qualifies`` (hedged: "as far as I knew", "may have"),
  ``denies`` (negated: "no record of it arriving at Dock 4"), ``doubts``
  (hedged and negated), ``rejects`` (reported and contradicted: "their
  assertion that … is contradicted by"), ``reports`` (someone else's
  words: "Müller wrote that …"), ``quotes`` (a quoted part of a reply),
  ``asks`` (a question) and ``supposes`` ("if …");
- a **camp**: the side it supports, when the stance takes one. Denying one
  of two sides is the other's camp.

Words decide stances, so some are wrong: ``stance`` overrides take a
locator (any part of the statement) and a stance, by Claude's or the user's
judgment. Copies say nothing new: a statement in a copy (the same message
in two mailboxes, an attachment of a file) is the original's, and only
counts as reaching whoever it reached.

The **tensions** are what a reader wants out of a dispute:

- ``both``: one speaker on both sides in one document;
- ``switch``: a speaker on one side, later on the other;
- ``despite``: a speaker on one side after a statement for another had
  reached them (they received it, sent it on, or held it in their mail),
  from a colleague (the same email domain) or from anyone else.
"""

import re
from collections import Counter, defaultdict
from datetime import datetime

from . import locators, query as querymod, textnorm, trace
from .matter import Matter

STANCES = {"asserts": "for", "qualifies": "for", "denies": "against", "doubts": "against", "rejects": "against",
           "reports": None, "quotes": None, "asks": None, "supposes": None}
VERBS = {"asserts": "says", "qualifies": "suggests", "denies": "denies", "doubts": "doubts", "rejects": "rejects",
         "reports": "reports", "quotes": "quotes", "asks": "asks about", "supposes": "supposes"}
CONTEXT = 300

# sentences end at . ! ? before a capital (not after an abbreviation), at blank lines and at a transcript's turns
_END = re.compile(r"""([.!?])["”’')\]]*\s*(?=["“‘(\[]?[A-Z0-9])|\n[ \t]*\n""")
_TURN = re.compile(r"(?:(?<=[\n\d.?!\"”])|^)[ \t]*(?:\d+[ \t]+)?(Q|A)\.[ \t]+"
                   r"|(?:(?<=\n)|^)[ \t]*((?:MR|MS|MRS|DR)\.[ \t]+[A-Z][A-Z'’-]+):[ \t]")
_CLAUSE = re.compile(r";\s+|:\s+|\s+[—–]\s+|[—–](?=\s)|,\s+(?:but|though|although|yet|whereas)\s+|\s+but\s+(?=\w)", re.I)
_NEG = re.compile(r"\b(?:no(?!\.\s*[A-Z0-9#])|not|never|nothing|none|neither|nor|without|cannot)\b|\w+n[’']t\b", re.I)
_HEDGE = re.compile(r"\bas far as (?:i|we) (?:knew|know|could tell)\b|\bto (?:my|our) knowledge\b"
                    r"|\bestimated\b|\bexpected\b|\bscheduled\b|\bshould have\b|\bsupposed to\b"
                    r"|\b(?:i|we) (?:believe|think|understand)\b|\bas i understand\b|\bmay\b|\bmight\b|\bcould have\b"
                    r"|\bappears?\b|\bapparently\b|\bseems?\b|\bpossibly\b|\bprobably\b|\blikely\b|\bperhaps\b"
                    r"|\bnot sure\b|\bunclear\b", re.I)
_REPORT = re.compile(r"\b(?:wrote|writes?|said|says?|stated|states?|told|tells?|claims?|claimed|asserts?|asserted|assertion"
                     r"|contends?|contended|contention|position|replied|replies|responded|maintains?|maintained"
                     r"|insists?|insisted|alleges?|alleged|allegation|according to|reports?|reported|testified)\b"
                     r"|\bper(?=\s+[A-Z])", re.I)
_REJECT = re.compile(r"\b(?:contradict\w*|false|untrue|incorrect|wrong|otherwise|not true|disputes?|disputed|denies"
                     r"|denied|refuted|rebutted|belied|without merit|meritless|baseless|mistaken)\b", re.I)
_SEP = re.compile(r",\s|\band\b|\bor\b|\bwhile\b", re.I)
_SUPPOSE = re.compile(r"\b(?:if|unless|whether)\b", re.I)
_NAME = re.compile(r"[A-ZÀ-Þ][\w’'.-]*(?:\s+[A-ZÀ-Þ][\w’'.-]*)*")
_SELF = {"i", "we", "me", "us", "our", "my"}
_ROLE_WORDS = {"customer", "carrier", "buyer", "seller", "vendor", "supplier", "procurement", "receiving", "they", "she",
               "he", "counsel", "plaintiff", "defendant", "claimant", "respondent", "driver", "dispatch"}
_NOT_NAMES = {"the", "our", "so", "first", "second", "third", "but", "and", "replied", "told", "on", "in", "a", "an",
              "this", "that", "it", "we", "i", "if", "either", "as", "per", "dear", "re", "q", "ex", "exhibit"}


class DisputeError(ValueError):
    pass


def side(name: str, seed: str | None = None, text: str | None = None, query: str | None = None,
         include=(), exclude=()) -> dict:
    """A side of a dispute: a name (a few words for the claim) and what traces it."""
    if not (seed or text or query):
        raise DisputeError(f"side {name!r}: give a seed passage, its text, or a query")
    return {"name": name, "seed": seed, "text": text, "query": query,
            "include": list(include), "exclude": list(exclude)}


# -- the dispute -----------------------------------------------------------------------------

def dispute(m: Matter, sides: list[dict], stances=()) -> dict:
    """Who holds which side, when, and the tensions. ``stances``: ``(LOC,
    STANCE)`` judgments that override the words."""
    from .metadocs import Units
    if not sides:
        raise DisputeError("give at least one side")
    names = [s["name"] for s in sides]
    if len(set(names)) != len(names):
        raise DisputeError("each side needs its own name")
    for loc, st in stances:
        if st not in STANCES:
            raise DisputeError(f"{st!r} isn't a stance: one of {', '.join(STANCES)}")
    traces = []
    for s in sides:
        seed = trace.seed_of(m, s["seed"], s["text"]) if (s["seed"] or s["text"]) else None
        traces.append(trace.trace(m, seed, query=s["query"], include=s["include"], exclude=s["exclude"],
                                  paraphrases=0))
    u = Units(m)
    rows = u.fields
    people = trace.People(m, rows)
    voices = _Voices(m, u, rows, people)

    # one numbering for every side's appearances, in date order; routes follow it
    merged = []
    for k, r in enumerate(traces):
        for o in r["appearances"]:
            merged.append((k, o))
    merged.sort(key=lambda x: (trace._when(x[1]["date"]) is None, trace._when(x[1]["date"]) or datetime.min,
                               x[1]["doc"], x[1]["range"][0], x[0]))
    number = {(k, o["n"]): i for i, (k, o) in enumerate(merged, 1)}
    apps = []
    for i, (k, o) in enumerate(merged, 1):
        o = {**o, "n": i, "side": k, "route": dict(o["route"])}
        if o["route"].get("from"):
            o["route"]["from"] = number[(k, o["route"]["from"])]
        apps.append(o)
    original = {}                    # appearance -> the appearance it is a copy of
    for o in apps:
        r = o["route"]
        original[o["n"]] = original.get(r["from"], r["from"]) if r["kind"] == "copy" and r.get("from") else o["n"]

    seed_negs = [_negs((tr["seed"] or {}).get("text") or "") for tr in traces]
    qnodes = [querymod.parse(s["query"]) if s["query"] else None for s in sides]
    statements = []
    for o in apps:
        k = o["side"]
        for st in _statements(u, voices, o, qnodes[k], seed_negs[k] if o["how"] in ("verbatim", "edited") else None):
            if any(x["doc"] == o["doc"] and x["side"] == k and x["range"] == st["range"] for x in statements):
                continue
            statements.append({**st, "side": k, "appearance": o["n"], "doc": o["doc"], "name": o["name"],
                               "date": o["date"], "date_basis": o["date_basis"],
                               "copy_of": original[o["n"]] if original[o["n"]] != o["n"] else None,
                               "loc": locators.format({"doc": o["doc"], "layer": o["layer"],
                                                       "select": {"char": st["range"]}})})
    for loc, st in stances:
        _override(m, statements, loc, st)
    for i, st in enumerate(statements, 1):
        st["id"] = i
        st["position"] = STANCES[st["stance"]]
        st["camp"] = _camp(names, st["side"], st["position"])
        st["speaker_name"] = people.names.get(st["speaker"], st["speaker"]) if st["speaker"] else None
        st["org"] = voices.org(st["speaker"])

    by_app = defaultdict(list)
    for st in statements:
        by_app[st["appearance"]].append(st)
    tensions = _tensions(apps, statements, by_app, people, voices, names)
    summary = _people(apps, statements, people, voices, names)
    notes = [n for tr in traces for n in tr["notes"]]
    if not statements:
        notes.append("no statements: no sentence makes any side's claim by itself; try a seed passage or a looser query")
    return {"sides": [{**s, "appearances": len(tr["appearances"]),
                       "statements": sum(1 for st in statements if st["side"] == k)}
                      for k, (s, tr) in enumerate(zip(sides, traces))],
            "appearances": apps, "statements": statements, "people": summary, "tensions": tensions, "notes": notes}


def _camp(names, k, position):
    if position is None:
        return None
    if position == "for":
        return names[k]
    return names[1 - k] if len(names) == 2 else "not " + names[k]


def _conflict(a: str, b: str) -> bool:
    if a == b:
        return False
    return not (a.startswith("not ") and b != a[4:]) and not (b.startswith("not ") and a != b[4:])


def _override(m, statements, loc, stance):
    r = locators.resolve(m, loc)
    if r["status"] == "broken":
        raise DisputeError(f"{loc}: {r.get('error')}")
    d = r["loc"]["doc"]
    rng = (r["loc"].get("select") or {}).get("char")
    hit = [st for st in statements if st["doc"] == d and (not rng or (st["range"][0] < rng[1] and rng[0] < st["range"][1]))]
    if not hit:
        raise DisputeError(f"{loc}: no statement there to judge")
    for st in hit:
        st["stance"], st["cue"], st["judged"] = stance, "judged", True


# -- statements ------------------------------------------------------------------------------

def _negs(s: str) -> int:
    return len(_NEG.findall(s))


def _sentences(text: str, lo: int, hi: int, transcript: bool) -> list[tuple[int, int]]:
    cuts = {lo, hi}
    for mt in _END.finditer(text, lo, hi):
        if mt.group(1) == ".":
            word = re.search(r"([\w.]+)$", text[max(0, mt.start() - 12):mt.start()])
            abbr = word and word.group(1).lower().rstrip(".")
            if abbr == "no" and not re.match(r"\s*[#\d]", text[mt.end(1):]):
                abbr = None                         # "No." is a number's only before one
            if abbr and (abbr in querymod.ABBREVIATIONS or (transcript and word.group(1) in ("Q", "A"))):
                continue
        cuts.add(mt.end(1) if mt.group(1) else mt.end())
    if transcript:
        cuts |= {mt.start() for mt in _TURN.finditer(text, lo, hi)}
    cuts = sorted(c for c in cuts if lo <= c <= hi)
    out = []
    for a, b in zip(cuts, cuts[1:]):
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b > a:
            out.append((a, b))
    return out


def _clauses(text: str, a: int, b: int) -> list[tuple[int, int]]:
    mt = re.match(r"(?:\d+[ \t]+)?(?:Q|A)\.[ \t]+", text[a:b])     # a transcript's turn isn't its words
    out, s = [], a + (mt.end() if mt else 0)
    for mt in _CLAUSE.finditer(text, a, b):
        if mt.start() > s:
            out.append((s, mt.start()))
        s = mt.end()
    if b > s:
        out.append((s, b))
    return out


def _query_hits(node, text: str, a: int, b: int):
    """Char spans in text[a:b] where the query holds within that sentence alone, or None."""
    toks = textnorm.tokenize(text[a:b])
    words = [t for t, _, _ in toks]

    def ev(n):
        if isinstance(n, querymod.Near) and n.unit != "w":     # /s, /p: one sentence is both
            left, right = ev(n.left), ev(n.right)
            return left[0] and right[0], left[1] + right[1]
        if isinstance(n, (querymod.Words, querymod.Near)):
            sp = querymod.spans(n, words)
            return bool(sp), sp
        if isinstance(n, querymod.Or):
            got = [g for g in (ev(i) for i in n.items) if g[0]]
            return bool(got), [sp for g in got for sp in g[1]]
        if isinstance(n, querymod.And):
            got = [ev(i) for i in n.items]
            ok = all(g[0] for g in got)
            return ok, [sp for g in got for sp in g[1]] if ok else []
        if isinstance(n, querymod.Not):
            return not ev(n.item)[0], []
        return True, []                         # field filters and * are the document's, not the sentence's
    ok, sp = ev(node)
    if not ok or not sp:
        return None
    return sorted({(a + toks[i][1], a + toks[j - 1][2]) for i, j in sp})


def _statements(u, voices, o, qnode, seed_negs) -> list[dict]:
    """The statements an appearance makes: its claim-making sentences, cut
    into clauses, each with a speaker and a stance."""
    t = u.text(o["doc"])
    if not t:
        return []
    _, text, segs = t
    s, e = o["range"]
    part = min((g for g in segs if g["kind"] in ("body", "quoted", "message") and g["start"] <= s and e <= g["end"]),
               key=lambda g: g["end"] - g["start"], default={"start": 0, "end": len(text)})
    lo, hi = max(part["start"], s - CONTEXT), min(part["end"], e + CONTEXT)
    transcript = voices.transcript(o["doc"], text)
    quoted = [(g["start"], g["end"]) for g in segs if g["kind"] == "quoted"]
    sents = _sentences(text, lo, hi, transcript)
    found = []                                      # (sentence, hits)
    for a, b in sents:
        if b <= s or a >= e:
            continue
        if qnode is not None and o["how"] == "mentions":
            hits = _query_hits(qnode, text, a, b)
        else:
            hits = [(max(a, x), min(b, y)) for x, y in (o.get("hits") or [o["range"]]) if x < b and a < y]
        if hits:
            found.append(((a, b), hits))
    out = []
    for (a, b), hits in found:
        nxt = next((x for x in sents if x[0] >= b), None)
        merged = []
        for c, d in _clauses(text, a, b):
            hs = [h for h in hits if h[0] < d and c < h[1]]
            if not hs:
                continue
            q = any(x <= hs[0][0] < y for x, y in quoted)
            speaker = voices.speaker(o, text, c, q)
            stance, cue, about = _stance(text, (a, b), (c, d), hs, nxt, q, seed_negs, speaker, voices)
            if merged and merged[-1]["speaker"] == speaker and merged[-1]["stance"] == stance:
                merged[-1]["range"][1] = d
                continue
            merged.append({"range": [c, d], "speaker": speaker, "stance": stance, "cue": cue,
                           **({"attributed": about} if about else {})})
        for st in merged:
            st["text"] = text[st["range"][0]:st["range"][1]]
            if len(st["text"]) > trace.EXCERPT:
                st["text"] = st["text"][:trace.EXCERPT - 1] + "…"
        out += merged
    return out


def _stance(text, sent, clause, hits, nxt, quoted, seed_negs, speaker, voices):
    """(stance, the words that decided it, who it is attributed to)."""
    (a, b), (c, d) = sent, clause
    h0, h1 = hits[0][0], max(h[1] for h in hits)
    if quoted:
        return "quotes", "in a quoted part", None
    whole = text[a:b].rstrip()
    if whole.endswith("?"):
        return "asks", "a question", None
    before = text[a:h0]
    mt = _SUPPOSE.search(before)
    if mt:
        return "supposes", mt.group(0), None
    rep = list(_REPORT.finditer(before))
    about = None
    if rep:
        mt = rep[-1]
        about = voices.subject(before[:mt.start()], speaker)
        if about is not None:
            after = text[h1:b] + " " + (text[nxt[0]:nxt[1]] if nxt else "")
            rj = _REJECT.search(after)
            if rj:
                return "rejects", f"{mt.group(0)} … {rj.group(0)}", about
            return "reports", mt.group(0), about
    start = max(c, h0 - 60)
    seps = [mt.end() for mt in _SEP.finditer(text, start, h0)]
    window = text[seps[-1] if seps else start:h0]
    gaps = [text[x[1]:y[0]] for x, y in zip(hits, hits[1:]) if 0 < y[0] - x[1] < 25]
    negs = _negs(window) + sum(_negs(g) for g in gaps)
    if seed_negs is not None:
        negs += sum(_negs(text[x:y]) for x, y in hits) - seed_negs
    hedge = _HEDGE.search(text[c:d])
    neg = negs % 2 == 1
    if neg and hedge:
        return "doubts", f"{_NEG.search(window).group(0) if _NEG.search(window) else 'not'} … {hedge.group(0)}", None
    if neg:
        mt = [x for x in _NEG.finditer(window)]
        return "denies", mt[-1].group(0) if mt else "a negation", None
    if hedge:
        return "qualifies", hedge.group(0), None
    return "asserts", None, None


# -- speakers --------------------------------------------------------------------------------

class _Voices:
    """Who is speaking where, and what organization they belong to."""

    def __init__(self, m, u, rows, people):
        self.m, self.u, self.rows, self.people = m, u, rows, people
        self._transcript = {}
        self._addr = defaultdict(set)
        for addr, pid in people.by_addr.items():
            self._addr[pid].add(addr)

    def org(self, pid):
        if not pid:
            return None
        addrs = self._addr.get(pid) or ({pid} if "@" in pid else set())
        doms = Counter(".".join(a.rsplit("@", 1)[1].split(".")[-2:]) for a in addrs if "@" in a)
        return doms.most_common(1)[0][0] if doms else None

    def transcript(self, doc, text) -> bool:
        if doc not in self._transcript:
            md = trace._meta(self.m, doc)
            self._transcript[doc] = bool(md.get("transcript")) or len(re.findall(r"(?m)^\s*Q\.\s", text)) >= 5
        return self._transcript[doc]

    def named(self, name: str | None, create=False):
        """A known person by a name as written: whole, or a unique first name or surname."""
        if not name:
            return None
        from .analysis import _name_key
        nk = _name_key(name)
        if nk in self.people.by_name:
            return self.people.by_name[nk]
        want = [trace._fold(w).strip(".,’'") for w in name.split()]
        want = [w for w in want if w and w not in ("mr", "ms", "mrs", "dr")]
        if len(want) == 1 and any(want[0] in a.rsplit("@", 1)[-1] for a in self.people.by_addr):
            return None                         # "Bellwether": a company, by its domain
        fits = []
        for pid, full in self.people.names.items():
            if pid.startswith("custodian:") or trace._short(full) == full:
                continue
            have = [trace._fold(w).strip(".,") for w in full.replace(",", " ").split()]
            if want and all(w in have for w in want):
                fits.append(pid)
        if len(set(fits)) == 1:
            return fits[0]
        return self.people.person(None, name) if create else None

    def speaker(self, o, text, pos, quoted=False):
        """The person speaking at ``pos`` in an appearance's document."""
        doc = o["doc"]
        msg_author = [p["person"] for p in sorted(o["people"], key=lambda p: p["role"] != "wrote")
                      if p["role"] in ("wrote", "sent")]
        if quoted:
            seg = text[:pos]
            mt = list(re.finditer(r"On [^\n]*(?:\d{4}|[AP]M),?\s+([^\n<>]+?)\s*(?:<[^>\n]*>)?\s*wrote:", seg))
            fm = list(re.finditer(r"(?m)^[>\s]*From:\s*([^\n<]+?)\s*(?:<|$)", seg))
            name = None
            if mt and (not fm or mt[-1].start() > fm[-1].start()):
                name = mt[-1].group(1)
            elif fm:
                name = fm[-1].group(1)
            return self.named(name, create=True) if name else None
        if o["where"] in ("chat message", "own text", "quoted"):
            return msg_author[0] if msg_author else None
        if self.transcript(doc, text):
            turns = [mt for mt in _TURN.finditer(text, 0, min(len(text), pos + 12)) if mt.start() <= pos]
            if turns:
                t = turns[-1]
                if t.group(2):                                        # MS. STROUD: colloquy
                    return self.named(t.group(2).split(".", 1)[1].strip().title(), create=True)
                if t.group(1) == "A":
                    md = trace._meta(self.m, doc)
                    w = re.search(r"(?i)deposition of\s+([^\n,-]+?)\s*(?:[-–—,\n]|$)", md.get("title") or "") \
                        or re.search(r"DEPOSITION OF\s+([A-Z][A-Z .'’-]+?)\s*\n", text)
                    return self.named(w.group(1).strip().title(), create=True) if w else None
                by = list(re.finditer(r"BY\s+(?:MR|MS|MRS|DR)\.\s+([A-Z][A-Z'’-]+):", text[:pos]))
                return self.named(by[-1].group(1).title(), create=True) if by else None
        author = (self.rows.get(doc) or {}).get("author")
        if author and trace._short(author) != author:
            return self.named(author, create=True)
        if o["where"] == "attachment":
            senders = [p["person"] for p in o["people"] if p["role"] == "sent"]
            if senders:
                return senders[0]
        cust = (self.rows.get(doc) or {}).get("custodian")
        return self.people.custodian(cust) if cust else None

    def subject(self, before: str, speaker):
        """Who a report verb's subject is, in the words before it: a person,
        a party ("Bellwether", "the customer"), or None for the speaker
        themself ("I told you", "Replied: …")."""
        words = re.findall(r"[\w’'-]+", before)
        tail = [trace._fold(w) for w in words[-4:]]
        if not words or (tail and tail[-1] in _SELF):
            return None
        names = []                              # (end, name), nearest the verb last
        for mt in _NAME.finditer(before):
            n = re.sub(r"(?:’s|'s)$", "", mt.group(0)).strip(" .,")
            ws = n.split()
            if len(ws) == 1 and (trace._fold(n) in _NOT_NAMES or (n.isupper() and len(n) > 3)):
                continue                        # a sentence's first word, a status in capitals
            n = " ".join(w for w in ws if trace._fold(w) not in _NOT_NAMES)
            if n:
                names.append((mt.end(), n))
        roles = [(mt.end(), trace._fold(mt.group(0))) for mt in re.finditer(r"[\w’'-]+", before[-60:])
                 if trace._fold(mt.group(0)) in _ROLE_WORDS]
        roles = [(e + max(0, len(before) - 60), w) for e, w in roles]
        if roles and (not names or roles[-1][0] > names[-1][0]):
            return {"person": None, "name": roles[-1][1]}
        if names:
            name = names[-1][1]
            pid = self.named(name)
            if pid == speaker and speaker:
                return None
            return {"person": pid, "name": self.people.names.get(pid, name) if pid else name}
        return None


# -- who holds what, and the tensions --------------------------------------------------------

def _firm(st) -> bool:
    return bool(st["date"]) and st["date_basis"] != trace.LOWER_BOUND


def _key(st):
    w = trace._when(st["date"])
    return (w is None, w or datetime.min, st["appearance"], st["range"][0])


def _positions(statements):
    """Statements that take a side in the speaker's own voice, not copies."""
    return [st for st in statements if st["camp"] and st["speaker"] and not st["copy_of"]]


def _exposures(apps):
    """person -> [(appearance, role)] in date order, of appearances that reached them."""
    out = defaultdict(list)
    for o in apps:
        for p in o["people"]:
            if p["role"] == "held" and o["kind"] not in ("email", "chat") and o["where"] != "attachment":
                continue                   # a file in their folder says nothing of when they had it
            out[p["person"]].append((o, p["role"]))
    return out


def _tensions(apps, statements, by_app, people, voices, names) -> list[dict]:
    name = lambda pid: people.names.get(pid, pid)
    pos = _positions(statements)
    by_speaker = defaultdict(list)
    for st in pos:
        by_speaker[st["speaker"]].append(st)
    exposed = _exposures(apps)
    out = []
    for pid, sts in by_speaker.items():
        sts.sort(key=_key)
        # both: one document, both ways
        by_doc = defaultdict(list)
        for st in sts:
            by_doc[st["doc"]].append(st)
        for doc, xs in by_doc.items():
            camps = list(dict.fromkeys(x["camp"] for x in xs))
            if len(camps) > 1 and any(_conflict(a, b) for a in camps for b in camps):
                first = {c: next(x for x in xs if x["camp"] == c) for c in camps}
                out.append({"kind": "both", "person": name(pid), "date": xs[0]["date"], "name": xs[0]["name"],
                            "camps": camps, "statements": [first[c]["id"] for c in camps],
                            "text": f"{name(pid)} is on both sides in {xs[0]['name']}: "
                                    + "; ".join(f"{VERBS[first[c]['stance']]} {_q(first[c])}" for c in camps)})
        # switch: one side, later the other (documents on both sides don't count as either)
        seq = [(doc, xs[0]["camp"], xs[0]) for doc, xs in sorted(by_doc.items(), key=lambda kv: _key(kv[1][0]))
               if len({x["camp"] for x in xs}) == 1 and _firm(xs[0])]
        for (_, c0, s0), (_, c1, s1) in zip(seq, seq[1:]):
            if _conflict(c0, c1):
                out.append({"kind": "switch", "person": name(pid), "date": s1["date"], "from": c0, "to": c1,
                            "statements": [s0["id"], s1["id"]],
                            "text": f"{name(pid)} {VERBS[s0['stance']]} {_q(s0)} ({_day(s0)}, {s0['name']}), "
                                    f"then {VERBS[s1['stance']]} {_q(s1)} ({_day(s1)}, {s1['name']})"})
        # despite: on a side after a statement for another reached them
        org = voices.org(pid)
        for camp, colleague in [(c, w) for c in dict.fromkeys(st["camp"] for st in sts) for w in (True, False)]:
            mine = [st for st in sts if st["camp"] == camp and _firm(st)]
            best = None
            for st in mine:
                when = trace._when(st["date"])
                before = [y for y in mine if trace._when(y["date"]) < when]
                if not before:
                    continue
                had = []
                since = trace._when(before[0]["date"])
                for o, role in exposed.get(pid, []):
                    w = trace._when(o["date"])
                    if w is None or o["date_basis"] == trace.LOWER_BOUND or w >= when or w <= since:
                        continue
                    for x in by_app.get(o["n"], []):
                        if x["camp"] and _conflict(x["camp"], camp) and x["speaker"] and x["speaker"] != pid \
                                and (bool(org) and voices.org(x["speaker"]) == org) == colleague:
                            had.append((w, x["id"], x, o, role))
                if had:
                    _, _, x, o, role = min(had, key=lambda h: h[:2])
                    best = (st, x, o, role, before)
                    break
            if best:
                st, x, o, role, before = best
                later = [y["id"] for y in mine if _key(y) > _key(st)]
                who = name(x["speaker"]) + (", a colleague," if colleague else "")
                out.append({"kind": "despite", "person": name(pid), "date": st["date"], "camp": camp,
                            "statement": st["id"], "against": x["id"], "had": o["n"], "role": role,
                            "relation": "colleague" if colleague else "other", "later": later,
                            "text": f"{name(pid)} {VERBS[st['stance']]} {_q(st)} ({_day(st)}, {st['name']}) after "
                                    f"{who} {VERBS[x['stance']]} {_q(x)} ({_day(x)}, {x['name']}: {_role(role)})"
                                    + (f"; and {len(later)} more time{'s' * (len(later) != 1)}" if later else "")
                                    + f"; on that side since {_day(before[0])}"})
    order = {"both": 0, "switch": 1, "despite": 2}
    out.sort(key=lambda t: (order[t["kind"]], t.get("relation") == "other", trace._when(t["date"]) or datetime.max))
    return out


def _role(role):
    return {"received": "they received it", "sent": "they sent it on", "wrote": "in their own message",
            "held": "it was in their mail"}[role]


def _q(st) -> str:
    t = " ".join(st["text"].split())
    return "“" + (t if len(t) <= 90 else t[:89] + "…") + "”"


def _day(st) -> str:
    return (st["date"] or "undated")[:10]


def _people(apps, statements, people, voices, names) -> list[dict]:
    """Each speaker: their camps (first and last statement in each), and
    what others attributed to them."""
    out = {}
    for st in _positions(statements):
        p = out.setdefault(st["speaker"], {"person": people.names.get(st["speaker"], st["speaker"]),
                                           "org": voices.org(st["speaker"]), "camps": {}, "attributed": []})
        c = p["camps"].setdefault(st["camp"], {"first": None, "last": None, "statements": [], "_all": []})
        c["statements"].append(st["id"])
        c["_all"].append(st)
    for p in out.values():
        for c in p["camps"].values():
            xs = sorted(c.pop("_all"), key=_key)
            firm = [x for x in xs if _firm(x)] or xs
            c["first"], c["last"] = firm[0]["date"], firm[-1]["date"]
            if not _firm(firm[0]):
                c["date_basis"] = firm[0]["date_basis"] if firm[0]["date"] else "undated"
    for st in statements:
        who = (st.get("attributed") or {}).get("person")
        if who and who in out and st["stance"] in ("reports", "rejects"):
            out[who]["attributed"].append({"statement": st["id"], "by": st["speaker_name"], "side": names[st["side"]],
                                           "stance": st["stance"]})
    res = list(out.values())
    res.sort(key=lambda p: min((trace._when(c["first"]) or datetime.max for c in p["camps"].values()),
                               default=datetime.max))
    return res


# -- as a list -------------------------------------------------------------------------------

def to_list(result: dict, sides: list[dict], stances=()) -> tuple[dict, list[dict]]:
    """A dispute as a list file (docs/LISTS.md): a row per statement in date
    order (a document's later statements under its first), and in the
    header what makes it again (``dispute``), who holds what and the
    tensions."""
    title = "Dispute: " + " vs ".join(s["name"] for s in sides)
    head = {"type": "list", "v": 1, "title": title if len(title) <= 90 else title[:89] + "…",
            "made_by": "analyze dispute", "columns": ["n", "date", "who", "stance", "side", "camp"],
            "dispute": {"sides": sides, "stances": [list(x) for x in stances]},
            "people": result["people"], "tensions": result["tensions"]}
    names = [s["name"] for s in sides]
    items, first = [], {}
    for st in sorted(result["statements"], key=_key):
        row = {"loc": st["loc"], "fields": {"n": st["id"], "date": (st["date"] or "")[:16].replace("T", " "),
                                             "who": st["speaker_name"] or "", "stance": st["stance"],
                                             "side": names[st["side"]], "camp": st["camp"] or "",
                                             **({"copy of": st["copy_of"]} if st["copy_of"] else {})}}
        if st["doc"] in first:
            first[st["doc"]].setdefault("children", []).append(row)
        else:
            first[st["doc"]] = row
            items.append(row)
    return head, items


def again(m: Matter, head: dict, stances=()) -> tuple[dict, dict]:
    """The dispute a list file's header (``to_list``) describes, with more
    stance judgments. (result, its terms)."""
    t = head.get("dispute")
    if not t:
        raise DisputeError("this list wasn't written by analyze dispute --list")
    have = [tuple(x) for x in t.get("stances") or []]
    new = [tuple(x) for x in stances]
    terms = {"sides": t["sides"], "stances": [x for x in have if x[0] not in {n[0] for n in new}] + new}
    return dispute(m, terms["sides"], terms["stances"]), terms


# -- the time DAG ----------------------------------------------------------------------------

def dag_html(m: Matter, result: dict, title: str) -> tuple[dict, list[str], dict]:
    """The dispute as a time DAG (trace's, over every side): each event
    colored by the camps its speakers take, and a red edge where someone
    took a side after a colleague's statement for another reached them."""
    names = [s["name"] for s in result["sides"]]
    by_app = defaultdict(list)
    for st in result["statements"]:
        by_app[st["appearance"]].append(st)
    stmt = {st["id"]: st for st in result["statements"]}
    extra = []
    for t in result["tensions"]:
        if t["kind"] == "despite" and t["relation"] == "colleague":
            extra.append((t["had"], stmt[t["statement"]]["appearance"], f"still: {t['camp']}", "despite"))
        elif t["kind"] == "switch":
            a, b = (stmt[i]["appearance"] for i in t["statements"])
            extra.append((a, b, f"{trace._short(t['person'])} turns: {t['to']}", "despite"))
    camps = list(dict.fromkeys([*names, *(st["camp"] for st in result["statements"] if st["camp"])]))
    both = {stmt[i]["doc"] for t in result["tensions"] if t["kind"] == "both" for i in t["statements"]}

    def decorate(e):
        sts = [st for o in e["appearances"] for st in by_app.get(o["n"], [])]
        own = [st for st in sts if st["camp"]]
        held = list(dict.fromkeys(st["camp"] for st in own))
        cls = ["adhoc-dispute"]
        if len(held) > 1:
            cls.append("adhoc-dispute-mixed")
        elif held:
            cls.append(f"adhoc-dispute-c{camps.index(held[0]) % 4}")
        if any(o["doc"] in both for o in e["appearances"]):
            cls.append("adhoc-dispute-both")
        first = e["appearances"][0]
        date = (first["date"] or "undated")[:10]
        if first["date"] and first["date_basis"] == trace.LOWER_BOUND:
            date = "≥ " + date
        said = []
        for who in dict.fromkeys(st["speaker_name"] for st in (own or sts)):
            mine = [st for st in (own or sts) if st["speaker_name"] == who]
            camps_ = list(dict.fromkeys(st["camp"] for st in mine if st["camp"]))
            short = trace._short(who) or "someone"
            if len(camps_) > 1:
                said.append(f"{short}: both sides")
            elif camps_:
                st = mine[0]
                said.append(f"{short}: {camps_[0]}" if st["stance"] == "asserts" else
                            f"{short} {VERBS[st['stance']]} {names[st['side']]}")
            else:
                said.append(f"{short} {VERBS[mine[0]['stance']]} {names[mine[0]['side']]}")
        said = "; ".join(said[:2]) + (" +%d" % (len(said) - 2) if len(said) > 2 else "")
        tip = list(dict.fromkeys(f"{trace._short(st['speaker_name']) or '?'} {VERBS[st['stance']]} {names[st['side']]}"
                                 + (f" ({st['cue']})" if st.get("cue") else "") + f": {_q(st)}" for st in sts))
        return {"class": " ".join(cls), "sub": " · ".join(x for x in (date, said) if x), "tip": tip}

    n_t = Counter(t["kind"] for t in result["tensions"])
    intro = (", ".join(f"{n_t[k]} {w}" for k, w in (("both", "both ways"), ("switch", "switched"),
                                                    ("despite", "held despite")) if n_t[k]) or "no tensions")
    intro += (" · red edges: a side kept after a colleague's statement for another reached them, or a switch"
              " · red boxes: someone on both sides in one document")
    pseudo = {"appearances": result["appearances"]}
    files, sequence, summary = trace.dag_html(m, pseudo, title, extra=extra, decorate=decorate, intro=intro)
    import base64
    page = base64.b64decode(files["index.html"]).decode()
    key = "".join(f'<span class="adhoc-dispute-key c{i % 4}">■ {n}</span> ' for i, n in enumerate(camps))
    page = page.replace("</h2>", f'</h2>\n<p class="adhoc-query">{key}</p>', 1)
    files["index.html"] = base64.b64encode(page.encode()).decode()
    return files, sequence, {**summary, "tensions": dict(n_t), "despite_edges": len(extra)}
