"""The entity layer: known entities, and where their names appear.

Known entities are kept in the annotation log (``define-entity``,
``edit-entity``, ``delete-entity``; see :mod:`adhocdocs.annotations` for the
rules), so they have authors and history like tags and notes. This module
finds an entity's **aliases** in text. The viewer does the same in
``viewer/shell/entities.js``, live, as the list changes; both follow these
rules, and ``tests/entity_match_cases.json`` holds them to it:

- Text and alias are compared character by character after folding
  (compatibility forms, diacritics and case: "Ibanez" finds "Ibáñez"). A
  character whose fold isn't a single character is only lowercased.
- Whitespace in an alias matches any run of whitespace, line and page
  breaks included.
- A match starts and ends at a word boundary: the characters beside it are
  not letters, digits or ``_``.
- An alias with a capital letter in it only matches text with a capital
  letter in it: "Brown" finds "BROWN" and "Brown", not "brown".
- Leftmost first, then longest; matches don't overlap. Where entities share
  an alias, the match names all of them.

Suggestions (detected spans that aren't known entities) come from the
``entities`` index (:mod:`adhocdocs.detect`).
"""

import json
import re
import unicodedata
from functools import lru_cache

from . import locators
from .matter import Matter

KINDS = ("person", "organization", "term", "identifier", "contract", "event", "place", "thing", "other")


@lru_cache(maxsize=8192)
def fold(c: str) -> str:
    f = "".join(d for d in unicodedata.normalize("NFKD", c) if unicodedata.category(d) != "Mn").lower()
    if len(f) == 1:
        return f
    lo = c.lower()
    return lo if len(lo) == 1 else c


def fold_text(text: str) -> str:
    return "".join(fold(c) for c in text)


def _word(c: str) -> bool:
    return c.isalnum() or c == "_"


def _pattern(alias: str) -> re.Pattern | None:
    words = alias.split()
    if not words:
        return None
    return re.compile(r"\s+".join(re.escape(fold_text(w)) for w in words))


def find(text: str, entities: list[dict], folded: str | None = None) -> list[dict]:
    """Mentions of known entities in ``text``: ``[{start, end, entities}]``,
    in order, ``entities`` being IDs. Each entity is a dict with ``entity``
    and ``aliases``."""
    folded = fold_text(text) if folded is None else folded
    cands = {}                                     # (start, end) -> [entity IDs]
    for ent in entities:
        for alias in ent.get("aliases") or []:
            pat = _pattern(alias)
            if pat is None:
                continue
            capital = any(c.isupper() for c in alias)
            for m in pat.finditer(folded):
                s, e = m.span()
                if (s > 0 and _word(text[s - 1])) or (e < len(text) and _word(text[e])):
                    continue
                if capital and not any(c.isupper() for c in text[s:e]):
                    continue
                ids = cands.setdefault((s, e), [])
                if ent["entity"] not in ids:
                    ids.append(ent["entity"])
    out, last = [], 0
    for (s, e) in sorted(cands, key=lambda se: (se[0], -se[1])):
        if s < last:
            continue
        out.append({"start": s, "end": e, "entities": cands[(s, e)]})
        last = e
    return out


# -- the matter's entities --------------------------------------------------------------------

def known(m: Matter) -> list[dict]:
    from .annotations import Annotations
    return list(Annotations.load(m).entities.values())


def resolve(m: Matter, ref: str, entities: list[dict] | None = None) -> dict:
    """An entity by ID, or by name or alias (folded) if only one has it."""
    entities = known(m) if entities is None else entities
    for e in entities:
        if e["entity"] == ref:
            return e
    f = " ".join(fold_text(ref).split())
    hits = [e for e in entities if any(" ".join(fold_text(a).split()) == f for a in [e["name"], *e["aliases"]])]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise KeyError(f"{ref!r} could be any of " + ", ".join(f"{e['entity']} ({e['name']}, {e['kind']})" for e in hits))
    raise KeyError(f"no entity {ref!r}")


def _texts(m: Matter, docs=None):
    """(doc, layer, text) for each live document's primary layer."""
    state = m.state
    seen = {}
    for d in docs or state.live_documents():
        layer = state.primary_layer(state.documents[d])
        if layer is None:
            continue
        if layer.text not in seen:
            seen[layer.text] = m.objects.get_text(layer.text)
        yield d, layer, seen[layer.text]


def mentions(m: Matter, entities: list[dict], docs=None) -> list[dict]:
    """Every mention of these entities in the documents' primary layers, as
    ``{entity, loc}`` with a durable locator (quote included)."""
    out = []
    for d, layer, text in _texts(m, docs):
        for x in find(text, entities):
            loc = locators.char_locator(d, layer.id, text, x["start"], x["end"])
            for e in x["entities"]:
                out.append({"entity": e, "loc": loc})
    return out


def detected(m: Matter, text_hash: str) -> list[dict]:
    """The entities index's detections for one text, or [] if not indexed yet."""
    p = m.indexes_dir / "entities" / "texts" / (text_hash.removeprefix("sha256:") + ".json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))["detections"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return []


def candidates(m: Matter) -> dict:
    """key -> {kind, name, docs, ...}: every detected candidate across the matter."""
    p = m.indexes_dir / "entities" / "candidates.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))["candidates"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return {}


def known_keys(entities: list[dict]) -> set[str]:
    """Detection keys the known entities already cover, by their aliases."""
    from .detect import _fold
    out = set()
    for e in entities:
        for a in [e["name"], *e["aliases"]]:
            f = _fold(a)
            out |= {f"person:{f}", f"organization:{_fold(re.sub(r'[.,]', '', a))}", f"term:{f}",
                    f"identifier:{a.upper().replace('/', '-')}"}
    return out


def layer(m: Matter, doc: str, suggestions=True) -> dict:
    """A document's entity layer, as the viewer shows it: the known
    entities' mentions, and (with ``suggestions``) the detections that no
    known mention overlaps and no known entity already names."""
    state = m.state
    doc = state.follow(doc)
    lay = state.primary_layer(state.documents[doc])
    if lay is None:
        return {"doc": doc, "layer": None, "known": [], "suggested": []}
    text = m.objects.get_text(lay.text)
    ents = known(m)
    found = find(text, ents)
    out = {"doc": doc, "layer": lay.id,
           "known": [{**x, "loc": locators.char_locator(doc, lay.id, text, x["start"], x["end"])} for x in found]}
    if suggestions:
        taken = [(x["start"], x["end"]) for x in found]
        have = known_keys(ents)
        cands = candidates(m)
        sug = []
        for d in detected(m, lay.text):
            if d["key"] in have or any(d["start"] < e and s < d["end"] for s, e in taken):
                continue
            c = cands.get(d["key"], {})
            sug.append({**d, "docs": c.get("docs", 1),
                        "loc": locators.char_locator(doc, lay.id, text, d["start"], d["end"])})
        out["suggested"] = sug
    return out


# -- people who aren't entities yet ----------------------------------------------------------------
# The cast from email headers and names in text (adhocdocs.analysis.people)
# holds people nobody has added: a correspondent who appears only in From
# and To lines is never underlined in a document, and the graph can't say
# who emailed whom without knowing their addresses. These match the cast to
# the entity list, and make define-entity requests for the people that
# matter, with their names, the forms the text uses and their addresses as
# aliases.

GROUP_WORDS = {"everyone", "all", "staff", "team", "list", "group", "department", "dept", "resources", "office",
               "desk", "support", "accounts", "payable", "receivable", "sales", "service", "services", "notifications"}
ORG_WORDS = {"inc", "llc", "ltd", "co", "corp", "corporation", "company", "association", "network", "group", "suites",
             "hotel", "airways", "airlines", "bank", "monthly", "weekly", "magazine", "news", "club", "league", "soccer",
             "society", "institute", "council", "foundation", "university", "college", "school", "analytics", "cloud",
             "benefits", "goods", "supply", "partners", "associates", "llp", "pc", "pllc"}
BULK_LOCAL = {"noreply", "no-reply", "donotreply", "do-not-reply", "news", "newsletter", "info", "hello", "marketing",
              "updates", "notifications", "notify", "alerts", "mailer", "bounce", "promo", "deals", "offers"}


def looks_like(p: dict) -> str:
    """What a cast member seems to be: ``person``, ``group`` (a mailing list
    or a team's address), ``organization``, or ``bulk`` (a newsletter or
    notification sender: it sends and is never written to)."""
    words = {w.lower().strip(".,()") for w in p["name"].split()}
    if p.get("addresses") and p.get("sent") and not p.get("received") and not p.get("roles"):
        return "bulk"
    locals_ = {a.split("@", 1)[0].lower() for a in p.get("addresses") or []}
    domains = p.get("domains") or []
    if locals_ & BULK_LOCAL or any(d.split(".", 1)[0] in ("news", "mail", "email", "mailer", "e") for d in domains) \
            and not p.get("received"):
        return "bulk"
    if words & GROUP_WORDS:
        return "group"
    if words & ORG_WORDS:
        return "organization"
    if p.get("addresses") and not p.get("sent") and not p.get("roles") and len(words) != 2:
        return "group"
    return "person"


def _folded_names(e: dict) -> set[str]:
    return {" ".join(fold_text(a).split()) for a in [e["name"], *e["aliases"]]}


def person_aliases(p: dict) -> list[str]:
    """The ways a cast member is written: display names, the forms the text
    uses ("T. Kettering", "Mr. Kettering"), and email addresses."""
    out, seen = [], {" ".join(fold_text(p["name"]).split())}
    for a in [*p.get("names", []), *p.get("written_as", []), *p.get("addresses", [])]:
        a = " ".join(str(a).split())
        f = " ".join(fold_text(a).split())
        if len(a) > 2 and f not in seen:        # "MR. KETTERING" is "Mr. Kettering": matching folds case
            seen.add(f)
            out.append(a)
    return out


def match_person(p: dict, ents: list[dict]) -> dict | None:
    """The known entity a cast member is: by an address among its aliases,
    else by a name (or a form the text uses) it is called, if only one has it."""
    addrs = {a.lower() for a in p.get("addresses") or []}
    for e in ents:
        if addrs & {a.lower() for a in e["aliases"]}:
            return e
    names = {" ".join(fold_text(n).split()) for n in [p["name"], *p.get("names", [])]}
    hits = [e for e in ents if e["kind"] in ("person", "other", "organization") and names & _folded_names(e)]
    return hits[0] if len(hits) == 1 else None


def person_summary(p: dict) -> str:
    """A line of what the headers and text say about a cast member: counts
    and roles, not a description (Claude or the user writes that)."""
    bits = []
    if p.get("roles"):
        bits.append("Called " + ", ".join(p["roles"][:3]) + " in the documents")
    if p.get("addresses"):
        when = ""
        if p.get("first") and p.get("last"):
            when = f", {str(p['first'])[:10]} to {str(p['last'])[:10]}"
        bits.append(f"Email at {', '.join(p['domains'][:2])}: {p['sent']} sent, {p['received']} received{when}")
    if p.get("mentioned_in"):
        bits.append(f"named in {p['mentioned_in']} documents")
    return "; ".join(bits) + "." if bits else ""


def people(m: Matter, ents: list[dict] | None = None) -> list[dict]:
    """The cast, each with the entity it matches (or None), the aliases that
    entity lacks, and what it looks like (person, group, bulk sender)."""
    from .analysis import people as cast
    ents = known(m) if ents is None else ents
    out = []
    for p in cast(m):
        e = match_person(p, ents)
        aliases = person_aliases(p)
        have = _folded_names(e) if e else set()
        out.append({**p, "looks_like": looks_like(p),
                    "entity": {"entity": e["entity"], "name": e["name"]} if e else None,
                    "missing_aliases": [a for a in aliases if " ".join(fold_text(a).split()) not in have] if e else aliases})
    return out
