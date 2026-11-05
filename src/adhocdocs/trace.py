"""Trace a fact: where a passage appears across the matter, how it got
from one place to the next, and who had it when (docs/TRACE.md).

The seed is a passage (a locator with a char range, or text). Its
**appearances** are found by aligning words: candidates from the keyword
index (texts holding most of the seed's rarest words), then the best
windows in each, kept when enough of the seed's words are there in order.
An appearance is ``verbatim`` or ``edited``. Paraphrases aren't found by
words: semantically similar passages come back as ``candidates``, and only
the ones a reader includes (``include``, Claude's or the user's judgment)
join the trace, as ``reworded``.

Each appearance gets a date (when the message was sent or posted, when the
email carrying an attachment was sent, else the file's own date), the
people it reached (``wrote``, ``sent``, ``received``, ``held``: found in
their files) and a **route**, the first of these that holds:

- ``quoted``: it is in a quoted part of a reply or forward, and comes from
  the message quoted (metadocs ``origin``);
- ``copy``: the same message or file as an earlier appearance;
- ``attached``: an attachment, the same file or a version of one seen
  earlier;
- ``draft``: kept from an earlier version (metadocs ``version_origin``);
- ``knew``: whoever wrote, sent or held it had it already, from an
  earlier appearance;
- ``earliest``: the first appearance;
- ``unexplained``: none of these. Someone had it, and nothing in the
  matter shows how. These are the **gaps**: a call, a meeting, a document
  not collected.

``people`` gives each person's first exposure: the answer to "who knew,
and when".
"""

import difflib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import locators, textnorm
from .matter import Matter

VERBATIM = 0.97
EDITED = 0.6
MIN_COVER = 0.5         # share of the seed's words an appearance must have
DISTINCT = 8            # the seed's rarest words that pick candidate texts
MAX_TEXTS = 400
MAX_PER_TEXT = 5
EXCERPT = 240
ROLES = ("wrote", "sent", "received", "held")
LOWER_BOUND = "not before a date it mentions"


class TraceError(ValueError):
    pass


# -- the seed --------------------------------------------------------------------------------

def seed_of(m: Matter, loc: str | None = None, text: str | None = None) -> dict:
    if text:
        return {"text": text}
    r = locators.resolve(m, loc)
    if r["status"] == "broken":
        raise TraceError(f"the seed's locator is broken: {r.get('error')}")
    sel = r["loc"].get("select") or {}
    if not r.get("text") or "char" not in sel:
        raise TraceError("the seed must be a passage (a locator with a char range, or msg=), not a whole document; "
                         "or give the text")
    return {"loc": locators.format(r["loc"]), "doc": r["loc"]["doc"], "range": list(sel["char"]), "text": r["text"]}


# -- finding appearances ---------------------------------------------------------------------

def _windows(want: list[str], toks: list[str]) -> list[int]:
    """Starts of the windows of len(want) tokens sharing the most words with
    ``want`` (as multisets), best first, not overlapping, each with at
    least MIN_COVER of them."""
    n = len(want)
    w = min(n, len(toks))
    need = Counter(want)
    have: Counter = Counter()
    common = 0
    scored = []
    for i, t in enumerate(toks):
        have[t] += 1
        if have[t] <= need[t]:
            common += 1
        if i >= w:
            u = toks[i - w]
            if have[u] <= need[u]:
                common -= 1
            have[u] -= 1
        if i >= w - 1 and common >= MIN_COVER * n:
            scored.append((common, -(i - w + 1)))
    out = []
    for _, neg in sorted(scored, reverse=True):
        s = -neg
        if all(abs(s - o) >= w for o in out):
            out.append(s)
            if len(out) >= MAX_PER_TEXT:
                break
    return out


def _align(want: list[str], toks: list[str], start: int):
    """The seed aligned with the text near ``start``: (first token, end
    token, words matched in order) or None."""
    n = len(want)
    lo, hi = max(0, start - n // 2), min(len(toks), start + n + n // 2)
    sm = difflib.SequenceMatcher(None, want, toks[lo:hi], autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    # a lone common word at either end isn't part of it
    while len(blocks) > 1 and n > 4 and blocks[0].size == 1:
        blocks.pop(0)
    while len(blocks) > 1 and n > 4 and blocks[-1].size == 1:
        blocks.pop()
    if not blocks:
        return None
    matched = sum(b.size for b in blocks)
    return lo + blocks[0].b, lo + blocks[-1].b + blocks[-1].size, matched


def find(m: Matter, text: str, docs=None) -> list[dict]:
    """Where the words of ``text`` appear, verbatim or edited: dicts with
    ``doc``, ``layer``, ``range``, ``how`` and ``score`` (2·matched /
    (seed words + span words), 1.0 for verbatim)."""
    from .analysis import _ready
    from .search import STOPWORDS
    want = textnorm.words(text)
    if not want:
        raise TraceError("the seed has no words")
    kw = _ready(m, "keyword")
    content = sorted(set(want) - STOPWORDS) or sorted(set(want))
    df = {}
    for t in content:
        df[t] = kw.candidates('"' + t.replace('"', '""') + '"')
    rare = sorted((t for t in content if df[t]), key=lambda t: len(df[t]))[:DISTINCT]
    if not rare:
        return []
    need = len(rare) if len(rare) <= 2 else math.ceil(len(rare) / 2)
    hits = Counter(h for t in rare for h in df[t])
    texts = [h for h, c in hits.most_common() if c >= need][:MAX_TEXTS]
    state = m.state
    live = docs if docs is not None else state.live_documents()
    by_text = defaultdict(list)
    for d in live:
        layer = state.primary_layer(state.documents[d])
        if layer is not None:
            by_text[layer.text].append((d, layer))
    out = []
    n = len(want)
    for h in texts:
        if h not in by_text:
            continue
        got = kw.tokens(h)
        if not got:
            continue
        toks, offs = got
        found = []
        for s in _windows(want, toks):
            a = _align(want, toks, s)
            if a is None:
                continue
            first, end, matched = a
            span = end - first
            score = 2 * matched / (n + span)
            if matched < MIN_COVER * n or score < EDITED:
                continue
            rng = (offs[2 * first], offs[2 * (end - 1) + 1])
            if any(rng[0] < b and a_ < rng[1] for a_, b, _ in found):
                continue
            found.append((*rng, score))
        for s, e, score in found:
            for d, layer in by_text[h]:
                out.append({"doc": d, "layer": layer.id, "range": [s, e], "score": round(score, 3),
                            "how": "verbatim" if score >= VERBATIM else "edited"})
    return out


CLUSTER = 160          # hits this close are one passage
SENTENCE = re.compile(r"[.!?][)\]”\"']*\s|\n\s*\n")


def _passage(text: str, s: int, e: int, rows: bool) -> tuple[int, int]:
    """[s, e) widened to its sentence (a line, for a spreadsheet's rows),
    no more than about 300 characters either way."""
    lo = max(0, s - 300)
    if rows:
        a = text.rfind("\n", lo, s) + 1 or lo
        b = text.find("\n", e, e + 300)
    else:
        before = [mt.end() for mt in SENTENCE.finditer(text, lo, s)]
        a = before[-1] if before else lo
        mt = SENTENCE.search(text, e, min(len(text), e + 300))
        b = mt.start() + 1 if mt else -1
    b = b if b != -1 else min(len(text), e + 300)
    return a, max(b, e)


def find_query(m: Matter, query: str) -> list[dict]:
    """Passages a keyword query finds (search's query language): in each
    document, hits near each other make one passage, widened to its
    sentence. Each is an appearance ``mentions``: the query describes the
    fact, and what doesn't belong is excluded by whoever wrote it."""
    from .search import Options, Searcher
    s = Searcher(m, Options())
    _, items = s.keyword(query)
    terms = len({w for w in textnorm.words(query) if w not in ("and", "or", "not")})
    out = []
    state = m.state
    for it in items:
        d = it["doc"]["loc"][4:] if isinstance(it.get("doc"), dict) and "loc" in it["doc"] else it["loc"][4:]
        spans = sorted(tuple(h["loc"]["select"]["char"]) for h in it["hits"]
                       if "char" in (h["loc"].get("select") or {}))
        if not spans:
            continue
        layer_id = it["hits"][0]["loc"].get("layer")
        layer = next((l for l in state.layers.get(state.documents[d], []) if l.id == layer_id), None) \
            or state.primary_layer(state.documents[d])
        text = m.objects.get_text(layer.text)
        segs = m.objects.get_jsonl(m.objects.get_object(layer.id).get("segments"))
        heads = [(g["start"], g["end"]) for g in segs if g["kind"] == "header"]
        body = [sp for sp in spans if not any(a <= sp[0] and sp[1] <= b for a, b in heads)]
        spans = body or spans               # a subject line is the message's, not a passage
        parts = sorted({(g["start"], g["end"]) for g in segs if g["kind"] in ("body", "quoted", "message")})
        groups = [[spans[0]]]
        for sp in spans[1:]:
            if sp[0] - groups[-1][-1][1] <= CLUSTER:
                groups[-1].append(sp)
            else:
                groups.append([sp])
        rows = (m.objects.get_object(state.metadata[d]).get("kind") if d in state.metadata else None) == "spreadsheet"
        for g in groups:
            words = {textnorm.words(text[a:b])[0] if textnorm.words(text[a:b]) else "" for a, b in g}
            if terms >= 2 and len(words) < 2:
                continue                    # one word alone isn't the fact
            a, b = _passage(text, g[0][0], g[-1][1], rows)
            inner = [(x, y) for x, y in parts if x <= g[0][0] and g[-1][1] <= y]
            if inner:                       # not out of its quoted part, or its chat message
                x, y = max(inner)
                a, b = max(a, x), min(b, y)
            out.append({"doc": d, "layer": layer.id, "range": [a, b], "score": None, "how": "mentions"})
    return out


def _semantic_ready() -> bool:
    """Whether the embedding model is here, without trying to fetch it."""
    try:
        from .index.semantic import models_dir
        return any(models_dir().rglob("*.onnx"))
    except Exception:
        return False


def candidates(m: Matter, text: str, exclude: set, limit: int = 10) -> tuple[list[dict], str | None]:
    """Passages that may say the same thing in other words (the semantic
    index), not already appearances: for a reader to judge. (items, note)."""
    if not _semantic_ready():
        return [], "no paraphrase candidates: the embedding model isn't installed; describe the fact with --query"
    try:
        from .search import Options, Searcher
        s = Searcher(m, Options(limit=limit * 3))
        sem = s.ready("semantic")
        _, items = s.semantic(text, qvec=sem.embed_passage(text))
    except Exception as e:          # no model, no index: the literal trace stands alone
        return [], f"no paraphrase candidates: the semantic index isn't available ({str(e)[:80]})"
    out = []
    for it in items:
        if "via" in it:
            continue
        for h in it["hits"]:
            loc = h["loc"]
            d, rng = loc["doc"], (loc.get("select") or {}).get("char")
            if d in exclude:
                continue
            out.append({"loc": locators.format(loc), "score": h.get("score"),
                        "excerpt": (h.get("snippet") or {}).get("text")})
            break
        if len(out) >= limit:
            break
    return out, None


# -- dates and people ------------------------------------------------------------------------

def _when(s: str | None):
    """A sortable UTC time for an ISO date or time (naive ones taken as UTC)."""
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            t = datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    return t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


class People:
    """People by address and name, so an email's sender, a chat's author and
    a custodian (``kettering-t``) come out as one person."""

    def __init__(self, m: Matter, rows: dict):
        from .analysis import _name_key
        self._key = _name_key
        self.names: dict[str, str] = {}         # person id -> display name
        self.by_addr: dict[str, str] = {}
        self.by_name: dict[str, str] = {}
        # everyone the matter's emails and chats name, first, so custodians match them
        state = m.state
        for d in state.live_documents():
            if (rows.get(d) or {}).get("kind") in ("email", "chat"):
                md = _meta(m, d)
                for f in ("from", "to", "cc", "bcc"):
                    for addr, name in zip(md.get(f, []), md.get(f + "_names", md.get(f, []))):
                        self.person(addr, name)

    def person(self, addr: str | None = None, name: str | None = None) -> str:
        addr = (addr or "").lower() or None
        nk = self._key(name) if name else None
        pid = (addr and self.by_addr.get(addr)) or (nk and self.by_name.get(nk))
        if pid is None:
            pid = addr or nk or _fold(name or "unknown")
            self.names[pid] = name or addr or pid
        elif name and "@" in self.names.get(pid, "@") and "@" not in name:
            self.names[pid] = name
        if addr:
            self.by_addr[addr] = pid
        if nk:
            self.by_name.setdefault(nk, pid)
        return pid

    def custodian(self, slug: str) -> str:
        """``kettering-t`` -> Thomas Kettering, when one known person's
        surname starts so and the initial fits; else the custodian as is."""
        base, _, initial = slug.rpartition("-")
        if not base:
            base, initial = slug, ""
        base, initial = _fold(base), _fold(initial)
        fits = []
        for pid, name in self.names.items():
            words = [_fold(w) for w in name.replace(",", " ").split() if "@" not in w]
            if len(words) >= 2 and words[-1].startswith(base) and (not initial or words[0].startswith(initial)):
                fits.append(pid)
        if len(fits) == 1:
            return fits[0]
        pid = "custodian:" + slug
        self.names.setdefault(pid, slug)
        return pid


# -- the trace -------------------------------------------------------------------------------

def trace(m: Matter, seed: dict | None, query: str | None = None, include=(), exclude=(),
          paraphrases: int = 10) -> dict:
    """The trace of a seed (``seed_of``), or of what a keyword ``query``
    finds, or both. ``include``: locators of passages judged to say the
    same thing; ``exclude``: locators or documents that don't belong."""
    from .metadocs import Units
    u = Units(m)
    rows = u.fields
    state = m.state
    notes = []

    if not seed and not query:
        raise TraceError("give a seed passage, its text, or a query")
    found = find(m, seed["text"]) if seed else []
    if query:
        for f in find_query(m, query):
            if not any(f["doc"] == g["doc"] and f["range"][0] < g["range"][1] and g["range"][0] < f["range"][1]
                       for g in found):
                found.append(f)
    drop_docs, drop_ranges = set(), []
    for x in exclude:
        loc = locators.parse(x if x.lstrip().startswith(("{", "doc:", "tr:")) else "doc:" + x, m.objects)
        d = state.follow(loc["doc"])
        rng = (loc.get("select") or {}).get("char")
        if rng:
            drop_ranges.append((d, tuple(rng)))
        else:
            drop_docs.add(d)
    found = [f for f in found if f["doc"] not in drop_docs
             and not any(f["doc"] == d and f["range"][0] < b and a < f["range"][1] for d, (a, b) in drop_ranges)]
    for x in include:
        r = locators.resolve(m, x)
        if r["status"] == "broken":
            raise TraceError(f"{x}: {r.get('error')}")
        sel = r["loc"].get("select") or {}
        if "char" not in sel:
            raise TraceError(f"{x}: include a passage (a char range), not a whole document")
        d = r["loc"]["doc"]
        if any(f["doc"] == d and f["range"][0] < sel["char"][1] and sel["char"][0] < f["range"][1] for f in found):
            continue
        found.append({"doc": d, "layer": r["loc"].get("layer") or state.primary_layer(state.documents[d]).id,
                      "range": list(sel["char"]), "score": None, "how": "reworded", "included": True})

    occ = [_describe(m, u, rows, f) for f in found]
    occ.sort(key=lambda o: (o["_when"] is None, o["_when"] or datetime.min, o["doc"], o["range"][0]))
    for i, o in enumerate(occ, 1):
        o["n"] = i

    people = People(m, rows)
    for o in occ:
        o["people"] = _exposures(m, u, rows, o, people)
    _routes(m, u, occ, people)

    # each person's first exposure: the first firmly dated one, with an earlier
    # record dated only by what it mentions noted beside it
    reached: dict[str, list] = defaultdict(list)
    for o in occ:
        for p in o["people"]:
            reached[p["person"]].append((o, p["role"]))
    summary = []
    for pid, xs in reached.items():
        firm = [x for x in xs if x[0]["_firm"]]
        o, role = (firm or xs)[0]
        v = {"person": people.names.get(pid, pid), "first": o["date"], "role": role, "appearance": o["n"],
             "how": _route_text(o["route"], people), "roles": dict(Counter(r for _, r in xs))}
        if not o["_firm"]:
            v["date_basis"] = o["date_basis"] if o["date"] else "undated"
        before = [x for x in xs if not x[0]["_firm"] and x[0]["n"] < o["n"]]
        if firm and before:
            v["maybe_earlier"] = {"appearance": before[0][0]["n"], "date": before[0][0]["date"],
                                  "role": before[0][1], "date_basis": before[0][0]["date_basis"]}
        summary.append(v)
    summary.sort(key=lambda v: (_when(v["first"]) is None, _when(v["first"]) or datetime.min))

    cands, note = (candidates(m, seed["text"], {o["doc"] for o in occ}, paraphrases)
                   if paraphrases and seed else ([], None))
    if note:
        notes.append(note)
    if not occ:
        notes.append("the seed's words weren't found anywhere: try a shorter or more distinctive passage")
    gaps = [{"appearance": o["n"], "date": o["date"], "loc": o["loc"], "name": o["name"],
             "who": [people.names.get(p["person"], p["person"]) for p in o["people"] if p["role"] in ("wrote", "sent", "held")],
             "why": "it quotes a message that isn't in the matter" if o["route"].get("missing")
             else "nothing in the matter shows how they had it"}
            for o in occ if o["route"]["kind"] == "unexplained" or o["route"].get("missing")]
    for o in occ:
        for k in [k for k in o if k.startswith("_")]:
            del o[k]
        for p in o["people"]:
            p["name"] = people.names.get(p["person"], p["person"])
        o["route"]["text"] = _route_text(o["route"], people)
    return {"seed": seed, "query": query, "appearances": occ, "people": summary, "gaps": gaps, "candidates": cands, "notes": notes}


def _describe(m, u, rows, f) -> dict:
    """An appearance with what its document says about when and where."""
    doc, (s, e) = f["doc"], f["range"]
    row = rows.get(doc) or {}
    t = u.text(doc)
    text, segs = (t[1], t[2]) if t else ("", [])
    within = lambda kind: next((g for g in segs if g["kind"] == kind and g["start"] <= s and e <= g["end"]), None)
    parent = u.parent.get(doc)
    prow = rows.get(parent) or {} if parent else {}
    msg = within("message")
    if msg and msg.get("time"):
        date, basis, where = msg["time"], "posted", "chat message"
    elif row.get("kind") == "email":
        date, basis, where = row.get("date"), "sent", "quoted" if within("quoted") else "own text"
    elif parent and prow.get("kind") in ("email", "chat"):
        date, basis, where = prow.get("date"), f"attached to {'an email' if prow.get('kind') == 'email' else 'a chat'}", "attachment"
    else:
        date, basis, where = row.get("date"), "the file's date", "file"
    if not date:
        # an undated file (a log, notes): no earlier than the latest full date the passage writes
        from . import dates as dates_
        written = [d for x in dates_.detect(text[s:e]) if x["kind"] == "absolute"
                   and (d := dates_.resolve(x, None))]
        if written:
            date, basis = max(written), LOWER_BOUND
    excerpt = text[s:e]
    if len(excerpt) > EXCERPT:
        excerpt = excerpt[:EXCERPT - 1] + "…"
    loc = locators.format({"doc": doc, "layer": f["layer"], "select": {"char": list(f["range"])}})
    return {**f, "loc": loc, "name": u.name(doc), "kind": row.get("kind"), "date": date, "date_basis": basis,
            "where": where, "excerpt": excerpt, "custodian": row.get("custodian"),
            "_when": _when(date), "_parent": parent, "_message": msg,
            "_firm": bool(date) and basis != LOWER_BOUND}


def _meta(m, doc) -> dict:
    md = m.state.metadata.get(doc)
    return m.objects.get_object(md) if md else {}


def _exposures(m, u, rows, o, people: People) -> list[dict]:
    """Who an appearance reached, and how: ``wrote`` (its author), ``sent``
    (passed it on: quoted, forwarded or attached it), ``received``, and
    ``held`` (it is in their files)."""
    out: dict[str, str] = {}

    def add(pid, role):
        if pid and (pid not in out or ROLES.index(role) < ROLES.index(out[pid])):
            out[pid] = role

    def mail(doc, own: bool):
        md = _meta(m, doc)
        for addr, name in zip(md.get("from", []), md.get("from_names", md.get("from", []))):
            add(people.person(addr, name), "wrote" if own else "sent")
        for f in ("to", "cc", "bcc"):
            for addr, name in zip(md.get(f, []), md.get(f + "_names", md.get(f, []))):
                add(people.person(addr, name), "received")

    doc = o["doc"]
    if o["_message"]:
        md = _meta(m, doc)
        author = o["_message"].get("name")
        named = dict(zip(md.get("from_names", []) + md.get("to_names", []), md.get("from", []) + md.get("to", [])))
        if author:
            add(people.person(named.get(author), author), "wrote")
        for name in list(md.get("participants", [])) + list(md.get("to_names", [])):
            add(people.person(named.get(name), name), "received")
    elif o["kind"] == "email":
        mail(doc, o["where"] == "own text")
    elif o["where"] == "attachment":
        mail(o["_parent"], False) if (rows.get(o["_parent"]) or {}).get("kind") == "email" else None
        md = _meta(m, o["_parent"])
        if md.get("kind") == "chat":
            for name in md.get("participants", []):
                add(people.person(None, name), "sent")
    else:
        author = (rows.get(doc) or {}).get("author")
        if author:
            add(people.person(None, author), "wrote")
    for d in u.docs("copies:" + doc):
        cust = (rows.get(d) or {}).get("custodian")
        if cust:
            add(people.custodian(cust), "held")
    return [{"person": pid, "role": role} for pid, role in out.items()]


def _routes(m, u, occ: list[dict], people: People):
    """Each appearance's route: how it got there, from which earlier one."""
    by_doc = defaultdict(list)
    for o in occ:
        by_doc[o["doc"]].append(o)

    def earlier(o, docs):
        """The latest appearance before ``o`` in one of ``docs``."""
        c = [x for d in docs for x in by_doc.get(d, []) if x["n"] < o["n"]]
        return max(c, key=lambda x: x["n"]) if c else None

    seen: dict[str, list] = defaultdict(list)       # person -> appearances that reached them, in order
    for o in occ:
        doc, (s, e) = o["doc"], o["range"]
        route = None
        if o["where"] == "quoted":
            src = u.origin(doc, s, e)
            if src:
                frm = earlier(o, u.docs("copies:" + src[0]))
                route = {"kind": "quoted", "from": frm["n"] if frm else None, "doc": None if frm else "doc:" + src[0]}
            elif not any(o["n"] > x["n"] and x["doc"] in u.docs("copies:" + doc) for x in occ):
                route = {"kind": "quoted", "missing": True}     # what it quotes was never collected
        if route is None:
            frm = earlier(o, [d for d in u.docs("copies:" + doc) if d != doc])
            if frm:
                route = {"kind": "copy", "from": frm["n"]}
        if route is None and o["where"] == "attachment":
            frm = earlier(o, u.docs("versions:" + doc))
            route = {"kind": "attached", **({"from": frm["n"]} if frm else {})}
        if route is None and o["where"] in ("file", "own text", "attachment"):
            v = u.version_origin(doc, s, e)
            if v:
                frm = earlier(o, u.docs("copies:" + v[0]))
                if frm:
                    route = {"kind": "draft", "from": frm["n"]}
        if route is None or (route["kind"] == "attached" and "from" not in route):
            senders = [p["person"] for p in o["people"] if p["role"] in ("wrote", "sent", "held")]
            # the earliest record that reached one of them, a firmly dated one if there is one
            knew = [(x, p) for p in senders for x in seen.get(p, [])[:1] + [y for y in seen.get(p, []) if y["_firm"]][:1]
                    if x["n"] < o["n"]]
            if knew:
                frm, pid = min(knew, key=lambda x: (not x[0]["_firm"], x[0]["n"]))
                route = {**(route or {}), "kind": route["kind"] if route else "knew",
                         "from": frm["n"], "person": pid, **({} if frm["_firm"] else {"lower_bound": True})}
        if route is None:
            route = {"kind": "earliest" if o["n"] == 1 else "unexplained" if o["_firm"] else "unplaced"}
        o["route"] = route
        for p in o["people"]:
            seen[p["person"]].append(o)


def _route_text(r: dict, people: People) -> str:
    text = _route_words(r, people)
    return text + " (dated only by a date it mentions)" if r.get("lower_bound") else text


def _route_words(r: dict, people: People) -> str:
    k = r["kind"]
    frm = f" #{r['from']}" if r.get("from") else ""
    who = people.names.get(r.get("person"), r.get("person")) if r.get("person") else None
    if k == "quoted" and r.get("missing"):
        return "quoted from a message that isn't in the matter"
    if k == "quoted":
        return f"quoted from{frm}" if frm else f"quoted from {r.get('doc')}, which doesn't have it word for word"
    if k == "copy":
        return f"copy of{frm}"
    if k == "attached":
        return f"attached; {who} had it from{frm}" if who else (f"attached, a version of{frm}" if frm else "attached")
    if k == "draft":
        return f"kept from an earlier draft,{frm}"
    if k == "knew":
        return f"{who} had it from{frm}"
    if k == "earliest":
        return "earliest"
    if k == "unplaced":
        return "undated: no route can be placed"
    return "no known route"


# -- as a list -------------------------------------------------------------------------------

def to_list(result: dict, query=None, include=(), exclude=()) -> tuple[dict, list[dict]]:
    """A trace as a list file (docs/LISTS.md): its appearances in date order (a
    document's other passages under its first), and in the header what
    makes it again (``trace``: ``analyze trace --again``), who had it when
    (``people``) and the gaps."""
    seed = result["seed"] or {}
    what = seed.get("text") or query or ""
    title = "Trace: " + (what if len(what) <= 60 else what[:59] + "…")
    head = {"type": "list", "v": 1, "title": title, "made_by": "analyze trace",
            "columns": ["n", "date", "name", "how", "route", "who"],
            "trace": {"seed": seed.get("loc"), "text": None if seed.get("loc") else seed.get("text"),
                      "query": query, "include": list(include), "exclude": list(exclude)},
            "people": result["people"], "gaps": result["gaps"]}
    items, first = [], {}
    for o in result["appearances"]:
        who = "; ".join(f"{p['name']} ({p['role']})" for p in o["people"])
        row = {"loc": o["loc"], "fields": {"n": o["n"], "date": (o["date"] or "")[:16].replace("T", " "),
                                            "how": o["how"], "route": o["route"]["text"], "who": who}}
        if o["doc"] in first:
            first[o["doc"]].setdefault("children", []).append(row)
        else:
            first[o["doc"]] = row
            items.append(row)
    return head, items


def again(m: Matter, head: dict, include=(), exclude=(), paraphrases: int = 0) -> tuple[dict, dict]:
    """Trace again what a list file's header (``to_list``) says was traced,
    with more judgments added. (result, the trace's terms)."""
    t = head.get("trace")
    if not t:
        raise TraceError("this list wasn't written by analyze trace --list")
    terms = {"seed": t.get("seed"), "text": t.get("text"), "query": t.get("query"),
             "include": list(dict.fromkeys(list(t.get("include") or []) + list(include))),
             "exclude": list(dict.fromkeys(list(t.get("exclude") or []) + list(exclude)))}
    seed = seed_of(m, terms["seed"], terms["text"]) if (terms["seed"] or terms["text"]) else None
    return trace(m, seed, query=terms["query"], include=terms["include"], exclude=terms["exclude"],
                 paraphrases=paraphrases), terms


# -- the time DAG ----------------------------------------------------------------------------

EDGE_WORDS = {"quoted": "quoted", "attached": "attached", "draft": "kept from a draft", "knew": "via someone"}


def dag(result: dict) -> list[dict]:
    """The trace as a time DAG, in **strands**: the appearances joined by
    their routes, each group of connected ones a strand (a subplot), with
    no order claimed between strands. An event is a document with its
    copies and its other passages; an edge is a route from an earlier
    event; a gap is an event with a route from nowhere (``?``). Columns
    follow the routes, not the calendar: an event sits one step after the
    latest event it came from."""
    apps = result["appearances"]
    if not apps:
        return []
    # events: an appearance and its copies (and a document's other passages) are one
    event_of: dict[int, int] = {}
    docs_event: dict[str, int] = {}
    for o in apps:
        if o["route"]["kind"] == "copy" and o["route"].get("from") in event_of:
            event_of[o["n"]] = event_of[o["route"]["from"]]
        elif o["doc"] in docs_event:
            event_of[o["n"]] = docs_event[o["doc"]]
        else:
            event_of[o["n"]] = o["n"]
        docs_event.setdefault(o["doc"], event_of[o["n"]])
    events: dict[int, dict] = {}
    for o in apps:
        e = events.setdefault(event_of[o["n"]], {"id": event_of[o["n"]], "appearances": [], "in": [], "gap": None})
        e["appearances"].append(o)
    edges = set()
    for o in apps:
        to = event_of[o["n"]]
        r = o["route"]
        frm = event_of.get(r.get("from")) if r.get("from") else None
        if frm is not None and frm != to:
            word = EDGE_WORDS.get(r["kind"], r["kind"])
            if r["kind"] in ("knew",) or (r["kind"] == "attached" and r.get("person")):
                who = next((p["name"] for p in o["people"] if p["person"] == r.get("person")), None)
                word = f"via {_short(who)}" if who else word
            edges.add((frm, to, word, bool(r.get("lower_bound"))))
        elif r["kind"] == "unexplained" or r.get("missing"):
            events[to]["gap"] = "quoted from a message not in the matter" if r.get("missing") else "no record of how"
    for frm, to, word, weak in sorted(edges):
        events[to]["in"].append({"from": frm, "label": word, "weak": weak})
    # strands: connected groups of events
    parent = {i: i for i in events}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for frm, to, *_ in edges:
        a, b = find(frm), find(to)
        if a != b:
            parent[max(a, b)] = min(a, b)
    strands: dict[int, list] = defaultdict(list)
    for i in sorted(events):
        strands[find(i)].append(events[i])
    out = []
    for root, evs in sorted(strands.items()):
        col = {}
        for e in evs:                              # in date order, so sources come first
            col[e["id"]] = 1 + max((col.get(x["from"], 0) for x in e["in"]), default=-1 if not e["gap"] else 0)
        for e in evs:
            e["column"] = col[e["id"]]
        people = Counter(p["name"] for e in evs for o in e["appearances"] for p in o["people"])
        out.append({"events": evs, "people": [n for n, _ in people.most_common(4)],
                    "first": next((o["date"] for e in evs for o in e["appearances"] if o["date"]), None),
                    "gaps": sum(1 for e in evs if e["gap"])})
    # strands with the most events first; a single undated or unplaced record last
    out.sort(key=lambda s: (len(s["events"]) == 1, -len(s["events"]), s["first"] or "9999"))
    return out


ORG_WORDS = {"inc", "llc", "ltd", "co", "corp", "company", "accounts", "payable", "receivable", "reporting",
             "freight", "lines", "dispatch", "systems", "supply", "services", "group", "bank", "court", "department",
             "team", "office", "portal", "noreply", "support", "sales", "the"}


def _short(name: str | None) -> str:
    """A surname for an edge or box: "Thomas Kettering" -> "Kettering";
    an organization's name stays whole."""
    if not name:
        return ""
    words = name.replace(",", " ").split()
    folded = {_fold(w).strip(".") for w in words}
    if 2 <= len(words) <= 4 and "@" not in name and not folded & ORG_WORDS and all(w[:1].isupper() for w in words):
        return words[-1]
    return name


def _event_text(e: dict) -> tuple[str, str]:
    """An event's box: its name, and a line of when and who."""
    first = e["appearances"][0]
    copies = len({o["doc"] for o in e["appearances"]})
    passages = len(e["appearances"]) - copies + 1
    roles = defaultdict(list)
    for o in e["appearances"]:
        for p in o["people"]:
            if p["name"] not in roles[p["role"]]:
                roles[p["role"]].append(p["name"])
    who_from = [_short(n) for n in roles["wrote"] + roles["sent"]]
    who_to = [_short(n) for n in roles["received"]]
    who = ", ".join(dict.fromkeys(who_from))
    if who_to:
        who += " → " + ", ".join(dict.fromkeys(who_to[:3])) + (" +%d" % (len(who_to) - 3) if len(who_to) > 3 else "")
    elif roles["held"] and not who:
        who = "held by " + ", ".join(_short(n) for n in roles["held"])
    date = (first["date"] or "undated")[:10]
    if first["date"] and first["date_basis"] == LOWER_BOUND:
        date = "≥ " + date
    extra = [f"{copies} copies"] if copies > 1 else []
    extra += [f"{passages} passages"] if passages > 1 else []
    return first["name"], " · ".join([date, *([who] if who else []), *extra])


def dag_html(m: Matter, result: dict, title: str) -> tuple[dict, list[str], dict]:
    """(files, sequence, summary): the time DAG as an SVG diagram for a
    viewer pane, like ``view graph``'s: a click on an event previews its
    passage in the linked document pane, and ]/[ step through the events,
    strand by strand."""
    import base64
    import html as html_
    from .graph import Graph, PAD, _layout, _path, _trim, CHAR_W, SUB_W
    esc = html_.escape
    strands = dag(result)
    out = [f"<h2>{esc(title)}</h2>"]
    n_events = sum(len(s["events"]) for s in strands)
    n_gaps = sum(s["gaps"] for s in strands)
    out.append(f'<p class="adhoc-query">{n_events} events in {len(strands)} strand{"s" * (len(strands) != 1)}'
               + (f" · {n_gaps} gap{'s' * (n_gaps != 1)} (dashed ?)" if n_gaps else "")
               + " · columns follow what came from what, not the calendar; strands are unrelated to each other"
               + " · click an event to see its passage</p>")
    sequence, body, y0, width = [], [], 0.0, 0.0
    for k, s in enumerate(strands):
        g = Graph()
        col = {}
        for e in s["events"]:
            name, sub = _event_text(e)
            nid = f"e{e['id']}"
            g.node(nid, type="doc", label=name, kind=sub, date=None)
            col[nid] = e["column"]
            if e["gap"]:
                gid = f"gap{e['id']}"
                g.node(gid, type="gap", label="?", kind=e["gap"], date=None)
                col[gid] = e["column"] - 1
                g.edge(gid, nid, "gap", label="no record" if e["gap"] == "no record of how" else "not collected")
            for x in e["in"]:
                g.edge(f"e{x['from']}", nid, "route", label=x["label"], weak=x["weak"])
        box, routes, w, h = _layout(g, col)
        head = f"Strand {k + 1}" + (f": {', '.join(s['people'])}" if s["people"] else "")
        body.append(f'<text class="adhoc-trace-strand" x="{PAD}" y="{y0 + 16:.0f}">{esc(head)}</text>')
        y0 += 18
        labels, said = [], set()
        for e in g.edges:
            d, (mx, my, _) = _path(g, box, routes[e["id"]])
            fan = (e["from"], e["label"])           # a fan-out says "via X" once, on its first branch
            show = fan not in said
            said.add(fan)
            cls = "adhoc-graph-edge kind-" + e["kind"] + (" derived" if e.get("weak") or e["kind"] == "gap" else "")
            body.append(f'<path class="{cls}" transform="translate(0,{y0:.0f})" d="{d}" marker-end="url(#arr)">'
                        f'<title>{esc(e["label"])}</title></path>')
            if show:
                labels.append((mx, my + y0, _trim(e["label"], 26), e["kind"]))
        for nid, (x, y, bw, bh) in box.items():
            if nid not in g.nodes:
                continue
            n = g.nodes[nid]
            y += y0
            if n["type"] == "gap":
                body.append(f'<g class="adhoc-graph-node adhoc-trace-gap" transform="translate({x:.0f},{y:.0f})">'
                            f'<title>{esc(n["kind"])}</title><rect width="{bw:.0f}" height="{bh}" rx="15"/>'
                            f'<text x="8" y="14" class="adhoc-graph-name">?</text>'
                            f'<text x="8" y="26" class="adhoc-graph-sub">{esc(_trim(n["kind"], int((bw - 14) / SUB_W)))}</text></g>')
                continue
            e = next(ev for ev in s["events"] if f"e{ev['id']}" == nid)
            loc = e["appearances"][0]["loc"]
            sequence.append(loc)
            tip = "\n".join([n["label"], n["kind"], *(f"#{o['n']} {o['how']}: {o['route']['text']}" for o in e["appearances"]),
                             "", e["appearances"][0]["excerpt"]])
            body.append(f'<g class="adhoc-graph-node type-doc{" adhoc-trace-gapped" if e["gap"] else ""}" '
                        f'data-locator="{esc(loc)}" transform="translate({x:.0f},{y:.0f})"><title>{esc(tip)}</title>'
                        f'<rect width="{bw:.0f}" height="{bh}" rx="4"/>'
                        f'<text x="8" y="14" class="adhoc-graph-name">{esc(_trim(n["label"], int((bw - 14) / CHAR_W)))}</text>'
                        f'<text x="8" y="26" class="adhoc-graph-sub">{esc(_trim(n["kind"], int((bw - 14) / SUB_W)))}</text></g>')
        for mx, my, text, kind in labels:
            lw = 8 + len(text) * 6.1
            body.append(f'<g class="adhoc-graph-label{" derived" if kind == "gap" else ""}" '
                        f'transform="translate({mx - lw / 2:.0f},{my - 8:.0f})"><rect width="{lw:.0f}" height="15" rx="3"/>'
                        f'<text x="4" y="11">{esc(text)}</text></g>')
            w = max(w, mx + lw / 2 + PAD)
        width = max(width, w)
        y0 += h + 10
        if k < len(strands) - 1:
            body.append(f'<line class="adhoc-trace-rule" x1="{PAD}" x2="{{W}}" y1="{y0 - 6:.0f}" y2="{y0 - 6:.0f}"/>')
    svg = (f'<div class="adhoc-graph" data-sequence><svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
           f'height="{y0:.0f}" viewBox="0 0 {width:.0f} {y0:.0f}">'
           '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="adhoc-graph-arrow"/></marker></defs>')
    out.append(svg + "\n".join(body).replace("{W}", f"{width - PAD:.0f}") + "</svg></div>")
    if not strands:
        out.append('<p class="adhoc-query">Nothing to show: the trace found no appearances.</p>')
    files = {"index.html": base64.b64encode("\n".join(out).encode()).decode()}
    return files, sequence, {"strands": len(strands), "events": n_events, "gaps": n_gaps,
                             "width": round(width), "height": round(y0)}
