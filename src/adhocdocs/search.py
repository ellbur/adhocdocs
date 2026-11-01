"""Search over the matter: keyword, semantic, hybrid, grep and similar.

Every hit is a durable locator whose quote is read from the layer, and is
checked against the layer before it is returned. Every result says what was
searched (``coverage``) and how far the index had got (``as_of``).
"""

import re
import sys
from dataclasses import dataclass, field

from . import locators, query as Q, textnorm
from .annotations import current_tags
from .index import Busy, open_index
from .matter import Matter
from .objects import sha256
from .portable import spawn

EMPTY_TEXT = sha256(b"")
SNIPPET_CONTEXT = 60
RRF_K = 60
STOPWORDS = set("a an and are as at be by for from has have in is it its of on or that the this to was were will with "
                "about any all what which who when where why how did do does".split())


@dataclass
class Options:
    layers: str = "primary"              # primary, all, or a view name
    in_kinds: list = field(default_factory=list)
    within: list | None = None           # locators (dicts)
    where: str | None = None
    tags: list = field(default_factory=list)   # "hot" or "-privileged"
    by: str = "doc"
    limit: int | None = None
    ignore_case: bool = False


class Searcher:
    def __init__(self, m: Matter, opts: Options):
        self.m = m
        self.opts = opts
        self.state = m.state
        self._fields = None
        self._tags = None
        self._segments = {}
        self._tok_cache = {}
        self.dropped = 0

    # -- catching up -----------------------------------------------------------------

    def ready(self, name: str):
        """Open an index, catching up first (fields, keyword) or in the
        background (semantic)."""
        idx = open_index(self.m, name)
        idx.check_settings()
        if idx.behind():
            if name == "semantic":
                start_background_update(self.m, name)
            else:
                idx.update()
        return idx

    # -- scope -------------------------------------------------------------------------

    @property
    def fields(self) -> dict:
        if self._fields is None:
            self._fields = self.ready("fields").rows()
        return self._fields

    @property
    def tags(self) -> dict:
        if self._tags is None:
            self._tags = current_tags(self.m)
        return self._tags

    def field_match(self, doc: str, f: Q.Field) -> bool:
        if f.name == "tag":
            return f.value in self.tags.get(doc, ())
        row = self.fields.get(doc)
        if row is None:
            return False
        v = f.value.lower()
        if f.name in ("from", "to", "cc", "bcc"):
            return any(a == v if "@" in v and not v.startswith("@") else v in a for a in row[f.name])
        if f.name == "custodian":
            return (row["custodian"] or "") == v.removeprefix("cust:")
        if f.name == "kind":
            return (row["kind"] or "") == v
        if f.name == "name":
            return v in (row["name"] or "").lower()
        if f.name == "date":
            d = row["date"] or ""
            if not d:
                return False
            lo, sep, hi = v.partition("..")
            if not sep:
                return d.startswith(lo)
            return (not lo or d[:len(lo)] >= lo) and (not hi or d[:len(hi)] <= hi)
        return False

    def scope(self) -> list[str]:
        docs = self.state.live_documents()
        if self.opts.within is not None:
            want = {self.state.follow(l["doc"]) for l in self.opts.within}
            docs = [d for d in docs if d in want]
        if self.opts.where:
            node = Q.parse(self.opts.where)
            docs = [d for d in docs if self.eval_doc(node, d, [])[0]]
        for t in self.opts.tags:
            neg = t.startswith("-")
            name = t.lstrip("-")
            docs = [d for d in docs if (name in self.tags.get(d, ())) != neg]
        return docs

    def doc_layers(self, doc: str):
        content = self.state.documents[doc]
        layers = self.state.layers.get(content, [])
        mode = self.opts.layers
        if mode == "primary":
            p = self.state.primary_layer(content)
            return [p] if p else []
        if mode == "all":
            return list(layers)
        return [l for l in layers if l.view == mode]

    def allowed(self, doc: str, layer) -> list[tuple[int, int]] | None:
        """Char ranges hits must fall in (None: anywhere)."""
        ranges = None
        if self.opts.in_kinds:
            ranges = [(s["start"], s["end"]) for s in self.segments(layer) if s["kind"] in self.opts.in_kinds]
        if self.opts.within is not None:
            rs = [tuple(l["select"]["char"]) for l in self.opts.within
                  if self.state.follow(l["doc"]) == doc and "char" in (l.get("select") or {})
                  and l.get("layer") in (None, layer.id)]
            whole = any(self.state.follow(l["doc"]) == doc and not l.get("select") for l in self.opts.within)
            if rs and not whole:
                ranges = rs if ranges is None else [(max(a, c), min(b, d)) for a, b in ranges for c, d in rs if max(a, c) < min(b, d)]
        return ranges

    def segments(self, layer) -> list[dict]:
        if layer.id not in self._segments:
            obj = self.m.objects.get_object(layer.id)
            self._segments[layer.id] = self.m.objects.get_jsonl(obj.get("segments"))
        return self._segments[layer.id]

    def segment_kinds(self, layer, s: int, e: int) -> list[str]:
        return sorted({g["kind"] for g in self.segments(layer) if g["start"] <= s and e <= g["end"]
                       and g["kind"] in ("header", "body", "quoted", "signature", "footnote", "comment",
                                         "header-footer", "table", "cell", "insertion", "deletion")})

    # -- keyword -------------------------------------------------------------------------

    def eval_doc(self, node, doc, layers, kw=None, cands=None):
        """(matches, [(layer, char spans)]) for one document."""
        if not layers:
            ok, _ = self._eval(node, doc, None, kw, cands)
            return ok, []
        result, hits = False, []
        for layer in layers:
            ok, sp = self._eval(node, doc, layer, kw, cands)
            if ok:
                result = True
                if sp:
                    hits.append((layer, sp))
        return result, hits

    def _eval(self, node, doc, layer, kw, cands):
        if isinstance(node, Q.All):
            return True, []
        if isinstance(node, Q.Field):
            return self.field_match(doc, node), []
        if isinstance(node, (Q.Words, Q.Near)):
            if layer is None or kw is None:
                return False, []
            if not may_match(node, layer.text, cands):
                return False, []
            toks, offs = self._tokens(kw, layer.text)
            out = []
            allowed = self.allowed(doc, layer)
            units = None
            if _needs_units(node):
                units = Q.Units(self.text(layer.text), offs, self.segments(layer))
            for i, j in Q.spans(node, toks, units):
                s, e = offs[2 * i], offs[2 * (j - 1) + 1]
                if allowed is None or any(a <= s and e <= b for a, b in allowed):
                    out.append((s, e, tuple(toks[i:j])))
            return bool(out), out
        if isinstance(node, Q.And):
            out = []
            for item in node.items:
                ok, sp = self._eval(item, doc, layer, kw, cands)
                if not ok:
                    return False, []
                out += sp
            return True, out
        if isinstance(node, Q.Or):
            res, out = False, []
            for item in node.items:
                ok, sp = self._eval(item, doc, layer, kw, cands)
                if ok:
                    res = True
                    out += sp
            return res, out
        if isinstance(node, Q.Not):
            ok, _ = self._eval(node.item, doc, layer, kw, cands)
            return not ok, []
        raise TypeError(node)

    def _tokens(self, kw, text_hash):
        if text_hash not in self._tok_cache:
            self._tok_cache[text_hash] = kw.tokens(text_hash) or ([], [])
        return self._tok_cache[text_hash]

    def keyword(self, q: str, node=None, docs=None):
        node = node or Q.parse(q)
        kw = self.ready("keyword")
        cands, scores = {}, {}
        for leaf, positive in Q.leaves(node):
            if leaf not in cands:
                found = kw.candidates(leaf.fts())
                cands[leaf] = found
                if positive:
                    for h, sc in found.items():
                        scores[h] = scores.get(h, 0.0) + sc
        items = []
        for doc in (docs if docs is not None else self.scope()):
            ok, hits = self.eval_doc(node, doc, self.doc_layers(doc), kw, cands)
            if not ok:
                continue
            score = max((scores.get(l.text, 0.0) for l, _ in hits), default=0.0)
            items.append(self.item(doc, float(f"{score:.4g}"), [self.hit(doc, l, s, e, toks) for l, sp in hits for s, e, toks in dedupe(sp)]))
        items.sort(key=lambda it: (-it["score"], it["doc"].get("date") or ""))
        header = self.header(q, node, "keyword", kw, exhaustive=True, coverage=self.coverage(kw))
        return header, items

    # -- hits and items ---------------------------------------------------------------------

    def text(self, text_hash):
        return self.m.objects.get_text(text_hash)

    def hit(self, doc, layer, s, e, toks=None, check=True):
        text = self.text(layer.text)
        if check and toks is not None and tuple(textnorm.words(text[s:e])) != toks:
            # the index and the layer disagree: never show it
            self.dropped += 1
            return None
        h = {"loc": locators.char_locator(doc, layer.id, text, s, e), "in": self.segment_kinds(layer, s, e),
             "snippet": snippet(text, s, e)}
        if "\f" in text:
            h["page"] = locators.page_of(text, s)
        lines = [g["label"] for g in self.segments(layer) if g["kind"] == "line" and g["start"] < e and s < g["end"]]
        if lines:
            h["pl"] = lines[0] if len(lines) == 1 else f"{lines[0]}-{lines[-1]}"
        if self.opts.layers != "primary":
            h["view"] = layer.view
        return h

    def doc_summary(self, doc):
        row = self.fields.get(doc) or {}
        out = {k: row.get(k) for k in ("name", "kind", "date", "custodian", "subject") if row.get(k)}
        if row.get("from"):
            out["from"] = row["from"]
        parent = self.parents.get(doc)
        if parent:                       # an attachment or archive member: its family is one hop away
            out["parent"] = "doc:" + parent
        return out

    @property
    def parents(self) -> dict:
        if not hasattr(self, "_parents"):
            self._parents = {c: p for p, cs in self.state.children.items() for c in cs}
        return self._parents

    def item(self, doc, score, hits):
        hits = [h for h in hits if h is not None]
        return {"loc": "doc:" + doc, "score": score, "doc": self.doc_summary(doc), "hits": hits}

    def coverage(self, idx) -> dict:
        have = idx.indexed() if idx is not None else None
        low = idx.low_confidence_layers() if hasattr(idx, "low_confidence_layers") else set()
        cov = {"documents": 0, "searched": 0, "unextracted": 0, "empty": 0, "low_confidence": 0, "pending": 0}
        for doc in self.scope():
            cov["documents"] += 1
            layers = self.doc_layers(doc)
            if not layers:
                cov["unextracted"] += 1
            elif all(self.is_empty(l.text) for l in layers):
                cov["empty"] += 1
            elif have is not None and not any(l.text in have for l in layers):
                cov["pending"] += 1
            else:
                cov["searched"] += 1
                if any(l.id in low for l in layers):
                    cov["low_confidence"] += 1
        return cov

    def is_empty(self, text_hash) -> bool:
        """No text at all, or only page breaks and spaces (a scan before OCR)."""
        if text_hash == EMPTY_TEXT:
            return True
        p = self.m.objects.path(text_hash)
        return p.stat().st_size < 4096 and not p.read_text(encoding="utf-8").strip()

    def header(self, q, node, index, idx, exhaustive, coverage, as_of=None):
        h = {"type": "search", "v": 1, "query": q, "parsed": Q.to_text(node) if node is not None else None,
             "index": index, "layers": self.opts.layers,
             "as_of": as_of if as_of is not None else (idx.cursor() if idx is not None else self.m.cursor()),
             "exhaustive": exhaustive, "coverage": coverage}
        if self.opts.in_kinds:
            h["in"] = self.opts.in_kinds
        if self.opts.where:
            h["where"] = self.opts.where
        if self.opts.tags:
            h["tags"] = self.opts.tags
        return h

    def finish(self, header, items):
        """Count, apply --by, and report dropped hits."""
        header["matches"] = {"documents": len(items), "hits": sum(len(i["hits"]) for i in items)}
        if self.dropped:
            header["dropped"] = self.dropped
            header["warning"] = "some hits did not match their layers and were dropped; run: idx verify"
        if self.opts.by == "hit":
            out = []
            for it in items:
                for h in it["hits"]:
                    out.append({**h, "score": it["score"], "doc": it["doc"]})
            items = out
        if self.opts.limit is not None:
            items = items[:self.opts.limit]
        return header, items

    # -- all ---------------------------------------------------------------------------------

    def all(self):
        """Every document in scope (--where, --tag, --within), matching no
        text: a listing, by date (undated last), then name."""
        items = [self.item(doc, 0.0, []) for doc in self.scope()]
        items.sort(key=lambda it: (it["doc"].get("date") is None, it["doc"].get("date") or "",
                                   it["doc"].get("name") or "", it["loc"]))
        header = self.header("all", None, "all", None, exhaustive=True, coverage=self.coverage(None))
        return header, items

    # -- grep -------------------------------------------------------------------------------

    def grep(self, pattern: str):
        rx = re.compile(pattern, re.M | (re.I if self.opts.ignore_case else 0))
        items = []
        for doc in self.scope():
            hits = []
            for layer in self.doc_layers(doc):
                text = self.text(layer.text)
                allowed = self.allowed(doc, layer)
                for mt in rx.finditer(text):
                    s, e = mt.span()
                    if e > s and (allowed is None or any(a <= s and e <= b for a, b in allowed)):
                        hits.append(self.hit(doc, layer, s, e))
            if hits:
                items.append(self.item(doc, len(hits), hits))
        items.sort(key=lambda it: (it["doc"].get("date") or "", it["loc"]))
        header = self.header(pattern, None, "grep", None, exhaustive=True, coverage=self.coverage(None))
        return header, items

    # -- semantic -------------------------------------------------------------------------

    def _text_docs(self, docs):
        """text hash -> [(doc, layer)] over the documents in scope."""
        out = {}
        for d in docs:
            for l in self.doc_layers(d):
                out.setdefault(l.text, []).append((d, l))
        return out

    def semantic(self, q: str, qvec=None, exclude_texts=(), limit=None):
        sem = self.ready("semantic")
        docs = self.scope()
        by_text = self._text_docs(docs)
        qvec = sem.embed_query(q) if qvec is None else qvec
        best = {}
        for score, _, th, s, e in sem.nearest(qvec, set(by_text) - set(exclude_texts)):
            for doc, layer in by_text[th]:
                allowed = self.allowed(doc, layer)
                if allowed is not None and not any(a < e and s < b for a, b in allowed):
                    continue
                it = best.get(doc)
                if it is None:
                    it = best[doc] = self.item(doc, round(score, 4), [])
                if len(it["hits"]) < 3:
                    h = self.hit(doc, layer, s, e)
                    h["score"] = round(score, 4)
                    it["hits"].append(h)
        items = sorted(best.values(), key=lambda it: -it["score"])
        limit = limit or self.opts.limit or 20
        items = items[:limit]
        cov = self.coverage(sem)
        header = self.header(q, None, "semantic", sem, exhaustive=False, coverage=cov)
        if cov["pending"]:
            # Not embedded yet: search those texts by keyword, so they aren't invisible.
            have = sem.indexed()
            pending = [d for d in docs if not any(l.text in have for l in self.doc_layers(d))]
            words = [w for w in textnorm.words(q) if w not in STOPWORDS]
            if words and pending:
                node = Q.Or(tuple(Q.Words((w,)) for w in words))
                _, extra = self.keyword(" OR ".join(words), node, docs=pending)
                for it in extra:
                    it["via"] = "keyword: not yet embedded"
                items += extra
                header["pending_searched_by_keyword"] = " OR ".join(words)
        return header, items

    def hybrid(self, q: str):
        node = Q.parse(q)
        terms = node.items if isinstance(node, Q.And) else (node,)
        if all(isinstance(t, Q.Words) and len(t.words) == 1 and not t.prefix for t in terms):
            # A question in plain English: any of its words, ranked.
            words = [w for w in textnorm.words(q) if w not in STOPWORDS] or textnorm.words(q)
            node = Q.Or(tuple(Q.Words((w,)) for w in words))
        kh, kitems = self.keyword(q, node)
        sh, sitems = self.semantic(q, limit=max(100, self.opts.limit or 0))
        fused = {}
        for ranked in (kitems, [i for i in sitems if "via" not in i]):
            for rank, it in enumerate(ranked):
                f = fused.setdefault(it["loc"], {**it, "score": 0.0, "hits": []})
                f["score"] += 1.0 / (RRF_K + rank + 1)
                f["hits"] += it["hits"][:3]
        items = sorted(fused.values(), key=lambda it: -it["score"])
        for it in items:
            it["score"] = round(it["score"], 5)
        items = items[:self.opts.limit or 20]
        header = self.header(q, node, "hybrid", None, exhaustive=False, coverage=sh["coverage"],
                             as_of={"keyword": kh["as_of"], "semantic": sh["as_of"]})
        return header, items

    def similar(self, loc):
        sem = self.ready("semantic")
        r = locators.resolve(self.m, loc)
        if r["status"] == "broken":
            raise locators.LocatorError(f"locator is broken: {r.get('error')}")
        doc = r["loc"]["doc"]
        content = self.state.documents[doc]
        if r.get("text"):
            qvec = sem.embed_passage(r["text"])
            exclude = ()
        else:
            # whole document: the mean of its primary text's passages
            import numpy as np
            layer = self.state.primary_layer(content)
            ids = [i for (i,) in sem.db.execute("SELECT id FROM passages WHERE text_hash=?", (layer.text,))] if layer else []
            if not ids:
                raise locators.LocatorError("document has no embedded text yet")
            v = sem.vectors()[ids].astype("float32").mean(axis=0)
            qvec = v / np.linalg.norm(v)
            exclude = [l.text for l in self.state.layers.get(content, [])]
        header, items = self.semantic(locators.format(r["loc"]), qvec=qvec, exclude_texts=exclude)
        items = [i for i in items if i["loc"] != "doc:" + doc and "via" not in i]
        header["index"] = "semantic (similar)"
        return header, items


def _needs_units(node) -> bool:
    if isinstance(node, Q.Near):
        return node.unit != "w" or _needs_units(node.left) or _needs_units(node.right)
    return isinstance(node, Q.Or) and any(_needs_units(i) for i in node.items)


def may_match(node, text_hash, cands) -> bool:
    """Whether FTS5's candidates leave a positional node possible in a text."""
    if isinstance(node, Q.Words):
        return text_hash in cands[node]
    if isinstance(node, Q.Near):
        return may_match(node.left, text_hash, cands) and may_match(node.right, text_hash, cands)
    return any(may_match(i, text_hash, cands) for i in node.items)


def dedupe(spans):
    seen, out = set(), []
    for s in sorted(spans):
        if s[:2] not in seen:
            seen.add(s[:2])
            out.append(s)
    return out


def snippet(text: str, s: int, e: int, context=SNIPPET_CONTEXT) -> dict:
    def squash(t):
        return re.sub(r"\s+", " ", t)
    a, b = max(0, s - context), min(len(text), e + context)
    pre, mid, post = squash(text[a:s]), squash(text[s:e]), squash(text[e:b])
    if a > 0:
        pre = "…" + pre.lstrip()
    if b < len(text):
        post = post.rstrip() + "…"
    if len(mid) > 300:
        mid = mid[:300] + "…"
    return {"text": pre + mid + post, "highlight": [[len(pre), len(pre) + len(mid)]]}


def start_background_update(m: Matter, name: str):
    idx = open_index(m, name)
    try:
        with idx.lock(wait=False):
            pass
    except Busy:
        return False
    spawn([sys.executable, "-m", "adhocdocs.cli", "idx", "--matter", str(m.root), "update", name], idx.dir / "update.log")
    return True


def to_list(header: dict, items: list, by: str) -> list[dict]:
    """Search results as a list file (LISTS.md)."""
    listing = header.get("index") == "all"
    head = {"type": "list", "v": 1, "title": header["query"],
            "columns": ["name", "date", "kind", "custodian"] if listing else ["name", "date", "kind", "score", "snippet"],
            "made_by": "search", "query": header["query"],
            "search": {k: header[k] for k in ("index", "parsed", "as_of", "layers", "exhaustive", "coverage") if k in header}}
    out = [head]
    if by == "hit":
        for it in items:
            out.append({"loc": it["loc"], "group": it["doc"].get("name") or it["loc"]["doc"],
                        "fields": {"score": it["score"], "snippet": it["snippet"]["text"]}})
    else:
        for it in items:
            row = {"loc": it["loc"], "fields": {} if listing else {"score": it["score"]}}
            if it["hits"]:
                row["fields"]["snippet"] = it["hits"][0]["snippet"]["text"]
                row["children"] = [{"loc": h["loc"], "fields": {"snippet": h["snippet"]["text"]}} for h in it["hits"]]
            out.append(row)
    return out
