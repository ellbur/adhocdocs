"""Metadocuments: content at a scale other than the document.

A **document** is what ingestion decided was one: an email, an attachment,
a file. It is the atom of the store, and every locator names one (or a
place in one). A **metadocument** is any other boundary drawn around
content: an email with its attachments, a whole thread, every copy and
draft of a memo, the part of a reply above the quoted text, or a set of
documents someone chose to treat as one. There are endlessly many, so they
are not listed or indexed. They are worked out when asked for, from the
documents, and indexes stay on documents (an index is for speed, not for
saying what the data is).

Two kinds:

- **Derived** at a *grain*, by a rule (:data:`GRAINS`). A unit is named
  ``<grain>:<doc>``: the unit at that grain that holds the document, so
  any member names it (``thread:3fa2…`` is "the thread 3fa2… is in"). Its
  canonical name uses a representative (the family's top item, the
  thread's first message, the earliest copy or draft).
- **Recorded**: a *bundle*, named and kept in the annotation log, whose
  parts are documents or ranges in them (``bundle:b_…``). Tags and notes
  can be on a bundle as one thing (adhocdocs.annotations).

:func:`members` turns any of these references into locators, and
:func:`regroup` re-reads document-level search results as results on
units, so a message quoted in 40 later replies is one result, not 41.
"""

from collections import defaultdict

from . import locators, textnorm
from .matter import Matter

GRAINS = {
    "doc": "the document itself, as ingested",
    "family": "a document with its parent and attachments: the top-level item and everything under it",
    "thread": "an email conversation: every message in the thread, and their copies",
    "copies": "one item, however many copies: exact duplicates, the same text in another format, "
              "the same message from several custodians",
    "versions": "every draft, revision and copy of a text (near-duplicates, estimated)",
    "new": "what a document adds: a message's own text without the text it quotes, a revision's changes "
           "over the version before it; copies count once, and quoted or kept text counts for the "
           "document that wrote it",
}
_BREAK = __import__("re").compile(r"\n[>|\s]*\n|:[ \t]*\n")
PREFIXES = tuple(f"{g}:" for g in GRAINS if g != "doc") + ("bundle:",)
# Grains that combine: "thread+family" is everything reachable from a document
# through its thread or its family (a thread with its attachments, and the
# attachments' other threads), "thread+family+copies" adds every copy, and so
# on. Written in this order, whatever order they were asked for in.
COMPOSABLE = ("thread", "family", "copies", "versions")


class MetaError(ValueError):
    pass


def composite(grain: str) -> tuple[str, ...] | None:
    """The grains a combined grain joins ("family+thread" -> ("thread", "family")), or None."""
    parts = grain.split("+")
    if len(parts) < 2 or len(set(parts)) != len(parts) or not all(x in COMPOSABLE for x in parts):
        return None
    return tuple(g for g in COMPOSABLE if g in parts)


def canonical_grain(grain: str) -> str:
    """A grain's own name: a combined one with its parts in their usual order."""
    c = composite(grain)
    return "+".join(c) if c else grain


def is_grain(grain: str) -> bool:
    return grain in GRAINS or composite(grain) is not None


def is_ref(s) -> bool:
    if not isinstance(s, str) or ":" not in s:
        return False
    if s.startswith(PREFIXES):
        return True
    return composite(s.partition(":")[0]) is not None


class _Union:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


class Units:
    """The units documents belong to, worked out once and cached."""

    def __init__(self, m: Matter, ann=None):
        self.m = m
        self.state = m.state
        self.live = set(self.state.live_documents())
        self.parent = {c: p for p, cs in self.state.children.items() for c in cs}
        self._ann = ann
        self._fields = self._threads = self._links = None
        self._groups = {}
        self._texts = {}
        self._previous = {}

    # -- what is known about documents ---------------------------------------------------

    @property
    def fields(self) -> dict:
        if self._fields is None:
            from .analysis import _ready
            self._fields = _ready(self.m, "fields").rows()
        return self._fields

    @property
    def ann(self):
        if self._ann is None:
            from .annotations import Annotations
            self._ann = Annotations.load(self.m)
        return self._ann

    @property
    def links(self) -> dict:
        if self._links is None:
            try:
                from .graph import doc_relations
                self._links = doc_relations(self.m)
            except Exception:
                self._links = {}
        return self._links

    @property
    def threads(self) -> dict:
        """doc -> (thread number, thread)"""
        if self._threads is None:
            from .analysis import threads
            self._threads = {}
            try:
                ts = threads(self.m)
            except Exception:
                ts = []
            for k, t in enumerate(ts):
                for msg in t["messages"]:
                    for loc in [msg["loc"], *msg["copies"]]:
                        self._threads[loc.removeprefix("doc:")] = (k, t)
        return self._threads

    def date(self, doc) -> str | None:
        return (self.fields.get(doc) or {}).get("date")

    def name(self, doc) -> str:
        row = self.fields.get(doc) or {}
        return row.get("subject") or row.get("name") or doc[:12]

    def _order(self, doc):
        return (self.date(doc) is None, self.date(doc) or "", doc)

    # -- grains ----------------------------------------------------------------------------

    def family_root(self, doc) -> str:
        seen = set()
        while doc in self.parent and doc not in seen:
            seen.add(doc)
            doc = self.parent[doc]
        return doc

    def family(self, root) -> list[str]:
        out, todo = [], [root]
        while todo:
            d = todo.pop(0)
            if d in out:
                continue
            out.append(d)
            todo += self.state.children.get(d, [])
        return [d for d in out if d in self.live]

    def _grouped(self, grain) -> dict:
        """doc -> representative, for copies and versions."""
        if grain in self._groups:
            return self._groups[grain]
        u = _Union()
        by_content = defaultdict(list)
        for d in self.live:
            by_content[self.state.documents[d]].append(d)
        for ds in by_content.values():
            for d in ds[1:]:
                u.union(ds[0], d)
        rels = ("copy",) if grain == "copies" else ("copy", "version")
        for d, xs in self.links.items():
            for x in xs:
                if x["rel"] in rels and d in self.live and x["doc"] in self.live:
                    u.union(d, x["doc"])
        for _, t in {k: t for k, t in self.threads.values()}.items():
            for msg in t["messages"]:
                for c in msg["copies"]:
                    u.union(msg["loc"][4:], c[4:])
        members = defaultdict(list)
        for d in self.live:
            members[u.find(d)].append(d)
        rep = {}
        for ds in members.values():
            r = min(ds, key=self._order)
            for d in ds:
                rep[d] = r
        self._groups[grain] = rep
        self._groups[grain + "/members"] = {r: sorted([d for d in ds], key=self._order)
                                            for ds in members.values() for r in [rep[ds[0]]]}
        return rep

    def _joined(self, grains: tuple[str, ...]) -> dict:
        """doc -> representative, for a combined grain: the documents joined
        through any of ``grains``, step after step, until nothing new joins
        (so an attachment brings its email, the email its thread, and the
        thread's other emails their attachments)."""
        key = "+".join(grains)
        if key in self._groups:
            return self._groups[key]
        u = _Union()
        for d in self.live:
            u.find(d)
        for g in grains:
            if g == "family":
                for d in self.live:
                    u.union(d, self.family_root(d))
            elif g == "thread":
                for d in self.live:
                    t = self.threads.get(d)
                    if t:
                        u.union(d, t[1]["messages"][0]["loc"][4:])
            else:
                rep = self._grouped(g)
                for d in self.live:
                    u.union(d, rep.get(d, d))
        members = defaultdict(list)
        for d in self.live:
            members[u.find(d)].append(d)
        rep = {}
        for ds in members.values():
            r = min(ds, key=self._order)
            for d in ds:
                rep[d] = r
        self._groups[key] = rep
        self._groups[key + "/members"] = {rep[ds[0]]: sorted(ds, key=self._order) for ds in members.values()}
        return rep

    def unit(self, doc: str, grain: str) -> str:
        """The unit at ``grain`` that holds ``doc``, by its canonical name."""
        doc = self.state.follow(doc)
        if grain == "doc":
            return "doc:" + doc
        if grain == "family":
            return "family:" + self.family_root(doc)
        if grain == "thread":
            t = self.threads.get(doc)
            return "thread:" + (t[1]["messages"][0]["loc"][4:] if t else doc)
        if grain in ("copies", "new"):
            return f"{grain}:" + self._grouped("copies").get(doc, doc)
        if grain == "versions":
            return "versions:" + self._grouped("versions").get(doc, doc)
        if composite(grain):
            key = canonical_grain(grain)
            return f"{key}:" + self._joined(composite(grain)).get(doc, doc)
        raise MetaError(f"no grain {grain!r}: {', '.join(GRAINS)}, or several of {', '.join(COMPOSABLE)} joined by +")

    def docs(self, ref: str) -> list[str]:
        """The documents a reference covers (whole or in part), oldest first."""
        grain, _, seed = ref.partition(":")
        if grain == "bundle":
            b = self.bundle(seed)
            return list(dict.fromkeys(self.state.follow(p["doc"]) for p in b["parts"]))
        if composite(grain):
            doc = self._doc(seed)
            key = canonical_grain(grain)
            r = self._joined(composite(grain)).get(doc, doc)
            return self._groups[key + "/members"].get(r, [doc])
        if grain == "doc" or grain not in GRAINS:
            if grain != "doc":
                raise MetaError(f"not a metadocument: {ref} (use {', '.join(PREFIXES)}, or grains joined by +)")
            return [self._doc(seed)]
        doc = self._doc(seed)
        if grain == "family":
            return self.family(self.family_root(doc))
        if grain == "thread":
            t = self.threads.get(doc)
            if not t:
                return [doc]
            out = []
            for msg in t[1]["messages"]:
                out += [msg["loc"][4:], *(c[4:] for c in msg["copies"])]
            return [d for d in out if d in self.live]
        g = "versions" if grain == "versions" else "copies"
        rep = self._grouped(g).get(doc, doc)
        return self._groups[g + "/members"].get(rep, [doc])

    def _doc(self, s: str) -> str:
        loc = locators.parse("doc:" + s.removeprefix("doc:"), self.m.objects)
        doc = self.state.follow(loc["doc"])
        if doc not in self.state.documents:
            raise MetaError(f"no document {s}")
        return doc

    def bundle(self, ref: str) -> dict:
        """A recorded bundle by ID, ID prefix, name or the start of its name."""
        from .annotations import Refused
        try:
            return self.ann.bundles[self.ann.find_bundle(ref)]
        except Refused as e:
            raise MetaError(str(e))

    # -- the parts of a unit -----------------------------------------------------------------

    def text(self, doc):
        """(layer, text, segments) of a document's primary layer, or None."""
        if doc not in self._texts:
            layer = self.state.primary_layer(self.state.documents[doc])
            if layer is None:
                self._texts[doc] = None
            else:
                obj = self.m.objects.get_object(layer.id)
                self._texts[doc] = (layer, self.m.objects.get_text(layer.text),
                                    self.m.objects.get_jsonl(obj.get("segments")))
        return self._texts[doc]

    def own_ranges(self, doc) -> list[tuple[int, int]] | None:
        """The char ranges of a document's own text: everything but its
        quoted segments. None if it has no text."""
        t = self.text(doc)
        if t is None:
            return None
        _, text, segs = t
        quoted = sorted((g["start"], g["end"]) for g in segs if g["kind"] == "quoted")
        out, pos = [], 0
        for s, e in quoted:
            if s > pos and text[pos:s].strip():
                out.append((pos, s))
            pos = max(pos, e)
        if pos < len(text) and text[pos:].strip():
            out.append((pos, len(text)))
        return out

    # -- what a revision adds over the version before it --------------------------------------

    def previous_version(self, doc) -> str | None:
        """The version a document revises: the nearest earlier one among its
        versions (near-duplicates or one inside the other, estimated) with
        different text. Copies don't count. None if it is the first."""
        cache = self._previous
        if doc not in cache:
            copies = set(self.docs("copies:" + doc))
            here = self._order(doc)
            earlier = [d for d in self.docs("versions:" + doc) if d not in copies and self._order(d) < here
                       and self.text(d) is not None]
            mine = self.text(doc)
            earlier = [d for d in earlier if mine is not None and self.text(d)[1] != mine[1]]
            cache[doc] = earlier[-1] if earlier else None
        return cache[doc]

    def added_ranges(self, doc) -> list[tuple[int, int]] | None:
        """The char ranges of what a document adds over the version before it:
        words inserted or changed, compared as `analyze redline` compares them
        (line breaks, hyphenation and case aside). None if it revises nothing."""
        prev = self.previous_version(doc)
        if prev is None:
            return None
        import difflib
        _, ta, _ = self.text(prev)
        _, tb, _ = self.text(doc)
        xa, xb = self._words(prev), self._words(doc)
        sm = difflib.SequenceMatcher(None, [w for w, _, _ in xa], [w for w, _, _ in xb], autojunk=False)
        out: list[list[int]] = []
        for op, _, _, j1, j2 in sm.get_opcodes():
            if op in ("insert", "replace") and j2 > j1:
                s, e = xb[j1][1], xb[j2 - 1][2]
                if out and not tb[out[-1][1]:s].strip(" \t\n\f.,;:"):      # runs apart by spacing or punctuation join
                    out[-1][1] = e
                else:
                    out.append([s, e])
        return [(s, e) for s, e in out]

    def new_ranges(self, doc) -> list[tuple[int, int]] | None:
        """What a document says that isn't said before it: a message's text
        without what it quotes; a revision's changes over the version before;
        else all of it. None if it has no text."""
        own = self.own_ranges(doc)
        t = self.text(doc)
        if own is None or t is None:
            return own
        quotes = any(g["kind"] == "quoted" for g in t[2])
        if not quotes:
            added = self.added_ranges(doc)
            if added is not None:
                return added
        return own

    def version_origin(self, doc, s, e) -> tuple[str, int, int] | None:
        """Where words at ``doc`` [s, e) that a revision kept were first
        written: the earliest version before it that has them, with their
        range there. None if they are the revision's own."""
        if any(a <= s and e <= b for a, b in (self.new_ranges(doc) or [])):
            return None
        want = [w for w, a, b in self._words(doc) if a < e and s < b]
        if not want:
            return None
        found = None
        d = self.previous_version(doc)
        while d is not None:
            ws = self._words(d)
            words = [w for w, _, _ in ws]
            hit = next((i for i in range(len(words) - len(want) + 1) if words[i:i + len(want)] == want), None)
            if hit is None:
                break
            found = (d, ws[hit][1], ws[hit + len(want) - 1][2])
            if any(a <= found[1] and found[2] <= b for a, b in (self.new_ranges(d) or [])):
                break                       # that version wrote them
            d = self.previous_version(d)
        return found

    def members(self, ref: str) -> list[dict]:
        """The parts of a unit or bundle as locators (dicts): whole
        documents, or for ``new`` and bundles, ranges in them."""
        grain, _, seed = ref.partition(":")
        if grain == "bundle":
            return [dict(p) for p in self.bundle(seed)["parts"]]
        if grain == "new":
            doc = self.unit(self._doc(seed), "new")[4:]
            ranges = self.new_ranges(doc)
            t = self.text(doc)
            if ranges == [] and self.previous_version(doc):
                return []                   # a revision that adds nothing
            if not ranges or t is None or ranges == [(0, len(t[1]))]:
                return [{"doc": doc}]
            return [locators.char_locator(doc, t[0].id, t[1], s, e) for s, e in ranges]
        return [{"doc": d} for d in self.docs(ref)]

    def describe(self, ref: str) -> dict:
        """What a reference names: its canonical name, grain, documents and
        a label to show."""
        grain = ref.partition(":")[0]
        if grain == "bundle":
            b = self.bundle(ref.partition(":")[2])
            docs = self.docs("bundle:" + b["bundle"])
            out = {"ref": "bundle:" + b["bundle"], "grain": "bundle", "name": b["name"],
                   "summary": b.get("summary"), "by": b["by"], "parts": len(b["parts"])}
        else:
            if not is_grain(grain):
                raise MetaError(f"not a metadocument: {ref}")
            grain = canonical_grain(grain)
            canon = self.unit(self._doc(ref.partition(":")[2]), grain)
            docs = self.docs(canon)
            rep = canon.partition(":")[2]
            out = {"ref": canon, "grain": grain, "name": self.name(rep)}
            if grain == "new":
                out["parts"] = len(self.members(canon))
                out["copies"] = len(self.docs("copies:" + rep))
                prev = self.previous_version(rep)
                t = self.text(rep)
                if prev and t is not None and not any(g["kind"] == "quoted" for g in t[2]):
                    out["over"] = "doc:" + prev         # what it adds over this version
        dates = sorted(d[:10] for d in (self.date(x) for x in docs) if d)
        out.update({"documents": len(docs), "first": dates[0] if dates else None,
                    "last": dates[-1] if dates else None})
        return out

    def units_of(self, doc: str) -> list[dict]:
        """Every unit a document is in, at each grain, and the bundles that
        hold it or part of it."""
        doc = self._doc(doc)
        out = []
        sizes = {}
        for g in GRAINS:
            if g == "doc":
                continue
            ref = self.unit(doc, g)
            n = sizes[g] = len(self.docs(ref))
            if n > 1 or g == "new":
                out.append(self.describe(ref))
        # a thread with its attachments, when that is more than either alone
        ref = self.unit(doc, "thread+family")
        if len(self.docs(ref)) > max(sizes["thread"], sizes["family"]):
            out.insert(1, self.describe(ref))
        for b in self.ann.bundles.values():
            if any(self.state.follow(p["doc"]) == doc for p in b["parts"]):
                out.append(self.describe("bundle:" + b["bundle"]))
        return out

    # -- quoted text, back to the message that wrote it ------------------------------------

    def _words(self, doc):
        key = ("w", doc)
        if key not in self._texts:
            t = self.text(doc)
            self._texts[key] = textnorm.tokenize(t[1]) if t else []
        return self._texts[key]

    def origin(self, doc: str, s: int, e: int, context: int = 6) -> tuple[str, int, int, bool] | None:
        """Where quoted text at ``doc`` [s, e) came from: an earlier message
        (in the thread, or one ``doc`` quotes) with the same words, matched
        with up to ``context`` words either side from the same quoted
        passage, so a single word isn't matched just anywhere. Prefers the
        message that has them in its own text (it wrote them); otherwise the
        earliest that quotes them (the one that wrote them isn't in the
        matter). (doc, start, end, wrote_it), or None."""
        t = self.text(doc)
        if t is None:
            return None
        toks = self._words(doc)
        seg = next(((g["start"], g["end"]) for g in t[2] if g["kind"] == "quoted" and g["start"] <= s and e <= g["end"]),
                   (s, e))
        idx = [k for k, (_, a, b) in enumerate(toks) if a < e and s < b]
        if not idx:
            return None
        inseg = [k for k, (_, a, b) in enumerate(toks) if seg[0] <= a and b <= seg[1]]
        lo = max(inseg[0] if inseg else idx[0], idx[0] - context)
        hi = min(inseg[-1] if inseg else idx[-1], idx[-1] + context)
        # context stays in the hit's paragraph: not across a blank quoted line,
        # nor back into an "On …, X wrote:" line the original never had
        text = t[1]
        brk = lambda k: bool(_BREAK.search(text[toks[k - 1][2]:toks[k][1]]))
        for k in range(idx[0], lo, -1):
            if brk(k):
                lo = k
                break
        for k in range(idx[-1] + 1, hi + 1):
            if brk(k):
                hi = k - 1
                break
        want = [w for w, _, _ in toks[lo:hi + 1]]
        off, n_hit = idx[0] - lo, len(idx)
        cands = []
        th = self.threads.get(doc)
        if th:
            for msg in th[1]["messages"]:
                cands += [msg["loc"][4:], *(c[4:] for c in msg["copies"])]
        cands += [x["doc"] for x in self.links.get(doc, []) if x["rel"] in ("quotes", "replies-to")]
        mine = self._order(doc)
        copies = set(self.docs("copies:" + doc))
        quoted_at = None
        for c in sorted(dict.fromkeys(cands), key=self._order):
            if c in copies or c not in self.live or self._order(c) > mine:
                continue
            ctoks = self._words(c)
            words = [w for w, _, _ in ctoks]
            own = self.own_ranges(c) or []
            n = len(want)
            for i in range(len(words) - n + 1):
                if words[i] == want[0] and words[i:i + n] == want:
                    a, b = ctoks[i + off][1], ctoks[i + off + n_hit - 1][2]
                    if any(x <= a and b <= y for x, y in own):
                        return c, a, b, True
                    if quoted_at is None:
                        quoted_at = (c, a, b, False)
        return quoted_at


def members(m: Matter, ref: str, ann=None) -> list[dict]:
    return Units(m, ann).members(ref)


def describe(m: Matter, ref: str, ann=None) -> dict:
    return Units(m, ann).describe(ref)


def regroup(m: Matter, items: list[dict], grain: str, units: Units | None = None) -> list[dict]:
    """Document-level search results (adhocdocs.search) as results on units
    at ``grain``, in the order their first document ranked.

    Each unit: ``unit`` (its canonical name), ``grain``, ``name``, ``score``
    (its best document's), ``docs`` (the documents with hits, each with how
    many) and ``hits``. At ``new``, a hit in quoted text counts for the
    message that wrote it (``quoted_in`` lists where else it was found);
    quoted text whose writer isn't in the matter stays with the message
    that quotes it, marked ``quoted``; and copies' identical hits count
    once."""
    u = units or Units(m)
    if grain == "doc":
        return items
    if grain == "bundle":
        return _by_bundle(u, items)
    out: dict[str, dict] = {}

    def slot(ref, score):
        if ref not in out:
            rep = ref.partition(":")[2]
            out[ref] = {"unit": ref, "grain": grain, "name": u.name(rep), "date": u.date(rep), "score": score,
                        "docs": {}, "hits": [], "_seen": set()}
            if grain != "new":
                out[ref]["size"] = len(u.docs(ref))
        elif score is not None and (out[ref]["score"] is None or score > out[ref]["score"]):
            out[ref]["score"] = score
        return out[ref]

    def add_hit(x, doc, h, **more):
        x["docs"].setdefault(doc, 0)
        if grain == "new":
            k = (tuple(textnorm.words(h["loc"]["quote"]["exact"])), h.get("snippet", {}).get("text"))
            if k in x["_seen"]:
                return          # the same words at the same place in a copy
            x["_seen"].add(k)
        x["docs"][doc] += 1
        x["hits"].append({**h, **more})

    for it in items:
        doc = it["loc"].removeprefix("doc:")
        if grain != "new":
            x = slot(u.unit(doc, grain), it.get("score"))
            for h in it["hits"]:
                add_hit(x, doc, h)
            if not it["hits"]:
                x["docs"].setdefault(doc, 0)
            continue
        own_unit = u.unit(doc, "new")
        for h in it["hits"] or [None]:
            if h is None:
                slot(own_unit, it.get("score"))["docs"].setdefault(doc, 0)
                continue
            if "quoted" in h.get("in", []):
                s, e = h["loc"]["select"]["char"]
                o = u.origin(doc, s, e)
                if o is not None:
                    src, a, b, wrote = o
                    x = slot(u.unit(src, "new"), it.get("score"))
                    x.setdefault("quoted_in", [])
                    if "doc:" + doc not in x["quoted_in"]:
                        x["quoted_in"].append("doc:" + doc)
                    layer, text, _ = u.text(src)
                    from .search import snippet
                    hh = {"loc": locators.char_locator(src, layer.id, text, a, b),
                          "in": ["body"] if wrote else ["body", "quoted"], "snippet": snippet(text, a, b)}
                    add_hit(x, src, hh, **({} if wrote else {"quoted": True}))
                    continue
                add_hit(slot(own_unit, it.get("score")), doc, h, quoted=True)
                continue
            sel = (h.get("loc") or {}).get("select") or {}
            if "char" in sel and u.previous_version(doc) is not None:
                o = u.version_origin(doc, *sel["char"])
                if o is not None:                   # words a revision kept: they count for the version that wrote them
                    src, a, b = o
                    x = slot(u.unit(src, "new"), it.get("score"))
                    x.setdefault("kept_in", [])
                    if "doc:" + doc not in x["kept_in"]:
                        x["kept_in"].append("doc:" + doc)
                    layer, text, _ = u.text(src)
                    from .search import snippet
                    add_hit(x, src, {"loc": locators.char_locator(src, layer.id, text, a, b), "in": ["body"],
                                     "snippet": snippet(text, a, b)})
                    continue
            add_hit(slot(own_unit, it.get("score")), doc, h)
    rows = []
    for x in out.values():
        x.pop("_seen")
        x["docs"] = [{"loc": "doc:" + d, "name": u.name(d), "date": u.date(d), "hits": n} for d, n in x["docs"].items()]
        if grain == "new":
            x["copies"] = len(u.docs("copies:" + x["unit"][4:]))
        rows.append(x)
    return rows


def _by_bundle(u: Units, items: list[dict]) -> list[dict]:
    """Results by bundle: each bundle with the hits in its parts. Documents
    in no bundle stay as they are."""
    bundles = list(u.ann.bundles.values())
    out = {}
    for it in items:
        doc = it["loc"].removeprefix("doc:")
        placed = False
        for b in bundles:
            parts = [p for p in b["parts"] if u.state.follow(p["doc"]) == doc]
            if not parts:
                continue
            whole = any(not p.get("select") for p in parts)
            ranges = [tuple(p["select"]["char"]) for p in parts if "char" in (p.get("select") or {})]
            hits = [h for h in it["hits"] if whole or any(a <= h["loc"]["select"]["char"][0] < c for a, c in ranges)]
            if it["hits"] and not hits:
                continue
            placed = True
            ref = "bundle:" + b["bundle"]
            x = out.setdefault(ref, {"unit": ref, "grain": "bundle", "name": b["name"], "score": it.get("score"),
                                     "docs": [], "hits": [], "size": len(b["parts"])})
            x["docs"].append({"loc": "doc:" + doc, "name": u.name(doc), "date": u.date(doc), "hits": len(hits)})
            x["hits"] += hits
        if not placed:
            out["doc:" + doc] = ({"unit": "doc:" + doc, "grain": "doc", "name": u.name(doc), "date": u.date(doc),
                          "score": it.get("score"), "docs": [{"loc": it["loc"], "name": u.name(doc),
                                                               "date": u.date(doc), "hits": len(it["hits"])}],
                          "hits": it["hits"]})
    return list(out.values())


def to_list(header: dict, units: list[dict]) -> list[dict]:
    """Results by unit as a list file (LISTS.md): an item per unit (its
    representative document, or for a bundle its first part), with the
    documents that had hits as children."""
    head = {"type": "list", "v": 1, "title": f"{header['query']} (by {header['grain']})",
            "columns": ["name", "date", "grain", "documents", "snippet"], "made_by": "search",
            "query": header["query"], "grain": header["grain"],
            "search": {k: header[k] for k in ("index", "parsed", "as_of", "layers", "exhaustive", "coverage") if k in header}}
    rows = [head]
    for x in units:
        first = x["hits"][0] if x["hits"] else None
        loc = x["docs"][0]["loc"] if x["unit"].startswith(("bundle:", "doc:")) else "doc:" + x["unit"].partition(":")[2]
        row = {"loc": loc, "fields": {"unit": x["unit"], "grain": x["grain"], "name": x["name"],
                                      "documents": len(x["docs"])}}
        if first:
            row["fields"]["snippet"] = first["snippet"]["text"]
        if x.get("quoted_in"):
            row["fields"]["quoted_in"] = len(x["quoted_in"])
        row["children"] = [{"loc": d["loc"], "fields": {"hits": d["hits"]}} for d in x["docs"] if d["loc"] != loc]
        rows.append(row)
    return rows


# -- as list files, for a list pane (view meta) -----------------------------------------------------

def _pages(sel: dict) -> str | None:
    p = sel.get("page")
    if p is None:
        return None
    return f"p. {p}" if isinstance(p, int) else f"pp. {p[0]}–{p[1]}"


def part_items(u: Units, ref: str, group: str | None = None) -> list[dict]:
    """A metadocument's parts as list items, in the order that reads best at
    its grain: a thread's messages in reply order, indented by depth, with
    their copies under them; a family from its top item down; copies and
    versions oldest first; what a message adds, passage by passage; a
    bundle's parts as recorded."""
    grain = ref.partition(":")[0]
    items: list[dict] = []

    def item(loc, **fields):
        it = {"loc": loc if isinstance(loc, str) else locators.format(loc),
              "fields": {k: v for k, v in fields.items() if v is not None}}
        if group:
            it["group"] = group
        return it

    if grain == "thread":
        t = u.threads.get(ref.partition(":")[2])
        if t:
            for msg in t[1]["messages"]:
                if msg["loc"][4:] not in u.live:
                    continue
                it = item(msg["loc"], part="\u00a0\u00a0" * msg["depth"] + ("↳ " if msg["depth"] else "") + u.name(msg["loc"][4:]))
                kids = [{"loc": c, "fields": {"part": "copy"}} for c in msg["copies"] if c[4:] in u.live]
                if kids:
                    it["children"] = kids
                items.append(it)
            return items
    if grain == "family":
        root = u.family_root(u._doc(ref.partition(":")[2]))

        def walk(d, depth):
            if d not in u.live:
                return
            items.append(item("doc:" + d, part="\u00a0\u00a0" * depth + ("↳ " if depth else "") + u.name(d)))
            for c in u.state.children.get(d, []):
                walk(c, depth + 1)
        walk(root, 0)
        return items
    if grain == "new":
        for k, loc in enumerate(u.members(ref), 1):
            items.append(item(loc, part=f"passage {k}" if loc.get("select") else "all of it"))
        return items
    if grain == "bundle":
        b = u.bundle(ref.partition(":")[2])
        for k, p in enumerate(b["parts"], 1):
            d = u.state.follow(p["doc"])
            sel = p.get("select") or {}
            what = _pages(sel) or ("passage" if sel else "whole document")
            items.append(item({**p, "doc": d}, part=f"{k}. {what} of {u.name(d)}" if sel else f"{k}. {u.name(d)}"))
        return items
    joined = composite(grain)
    if joined and "family" in joined:
        # each top-level item (an email, a loose file) in date order, its attachments under it
        docs = u.docs(ref)
        here = set(docs)
        roots = sorted({u.family_root(d) if u.family_root(d) in here else d for d in docs}, key=u._order)

        def walk(d, depth):
            if d not in here:
                return
            here.discard(d)
            items.append(item("doc:" + d, part="\u00a0\u00a0" * depth + ("↳ " if depth else "") + u.name(d)))
            for c in u.state.children.get(d, []):
                walk(c, depth + 1)
        for r in roots:
            walk(r, 0)
        for d in sorted(here, key=u._order):                 # anything not under a root in the unit
            items.append(item("doc:" + d, part=u.name(d)))
        return items
    docs = sorted(u.docs(ref), key=u._order)                 # copies, versions: oldest first
    for d in docs:
        items.append(item("doc:" + d, part=u.name(d)))
    return items


def list_of(m: Matter, ref: str, ann=None) -> tuple[dict, list[dict]]:
    """(header, items): a list file of a metadocument's parts (view list)."""
    u = Units(m, ann)
    d = u.describe(ref)
    title = f"{d['name']} ({d['grain']})"
    header = {"type": "list", "v": 1, "title": title, "made_by": "view meta", "query": d["ref"],
              "columns": ["part", "date", "from", "custodian", "kind", "tags"]}
    if d["grain"] in ("new", "bundle"):
        header["columns"] = ["part", "snippet", "date", "custodian", "tags"]
    return header, part_items(u, d["ref"])


def around_list(m: Matter, doc: str, ann=None) -> tuple[dict, list[dict]]:
    """(header, items): every metadocument a document is in, a group each,
    with its parts: its thread, family, copies, versions, and the bundles
    (and splits) that hold it."""
    u = Units(m, ann)
    doc = u._doc(doc)
    items = []
    for d in u.units_of(doc):
        items += part_items(u, d["ref"], group=f"{d['grain']}: {d['name']}")    # the pane adds how many rows
    header = {"type": "list", "v": 1, "title": f"Around {u.name(doc)}", "made_by": "view meta --of",
              "query": f"what {u.name(doc)} is part of", "columns": ["part", "date", "from", "custodian", "kind", "tags"]}
    return header, items
