"""Analysis over the matter: email threads, near-duplicates, and redlines.

Threads come from Message-ID, In-Reply-To and References (copies of one
message from several custodians are one node; a missing parent still links
its replies). Near-duplicates come from MinHash signatures of each text's
word 5-shingles, compared by estimated Jaccard similarity. A redline diffs
two documents' normalized tokens and gives every change as locators into
both, so it can be quoted, tagged or shown.
"""

import difflib
import html
from collections import defaultdict

import numpy as np

from . import locators, textnorm
from .index import open_index
from .index.neardup import similarity
from .matter import Matter


def _ready(m: Matter, name: str):
    idx = open_index(m, name)
    idx.check_settings()
    if idx.behind():
        idx.update()
    return idx


class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


# -- threads -------------------------------------------------------------------------------

def threads(m: Matter, by_subject=False) -> list[dict]:
    """Every thread of two or more messages, oldest message first. With
    ``by_subject``, emails with no threading headers at all join the thread
    whose normalized subject they share."""
    rows = [r for r in _ready(m, "threads").rows() if r["doc"] in set(m.state.live_documents())]
    fields = _ready(m, "fields").rows()
    node_of = {r["doc"]: (r["message_id"] or "doc:" + r["doc"]) for r in rows}
    u = _Union()
    parent_of = {}
    for r in rows:
        node = node_of[r["doc"]]
        u.find(node)
        parent = r["in_reply_to"] or (r["refs"][-1] if r["refs"] else None)
        if parent and parent != node:
            parent_of[node] = parent
            u.union(node, parent)
        for ref in r["refs"]:
            u.union(node, ref)
    if by_subject:
        roots_by_subject = {}
        for r in rows:
            if r["subject"] and (r["in_reply_to"] or r["refs"] or r["message_id"]):
                roots_by_subject.setdefault(r["subject"], node_of[r["doc"]])
        for r in rows:
            if not (r["in_reply_to"] or r["refs"]) and r["subject"] in roots_by_subject:
                u.union(node_of[r["doc"]], roots_by_subject[r["subject"]])

    groups = defaultdict(list)
    for r in rows:
        groups[u.find(node_of[r["doc"]])].append(r)
    out = []
    for members in groups.values():
        nodes = {}
        for r in members:
            nodes.setdefault(node_of[r["doc"]], []).append(r)
        if len(nodes) < 2:
            continue

        def depth(node, seen=()):
            p = parent_of.get(node)
            return 0 if p is None or p not in nodes or node in seen else 1 + depth(p, (*seen, node))

        messages = []
        for node, copies in nodes.items():
            first = min(copies, key=lambda r: r["doc"])
            messages.append({
                "loc": "doc:" + first["doc"], "message_id": first["message_id"],
                "date": first["date"], "subject": (fields.get(first["doc"]) or {}).get("subject"),
                "from": (fields.get(first["doc"]) or {}).get("from"),
                "depth": depth(node), "in_reply_to": parent_of.get(node),
                "copies": sorted("doc:" + c["doc"] for c in copies if c is not first),
                "custodians": sorted({(fields.get(c["doc"]) or {}).get("custodian") or "" for c in copies} - {""}),
            })
        messages.sort(key=lambda x: x["date"] or "")
        missing = sorted({p for n, p in parent_of.items() if n in nodes and p not in nodes})
        out.append({"subject": messages[0]["subject"], "messages": messages, "size": len(messages),
                    "first": messages[0]["date"], "last": messages[-1]["date"],
                    "custodians": sorted({c for x in messages for c in x["custodians"]}),
                    "missing_parents": missing})
    out.sort(key=lambda t: t["first"] or "")
    return out


# -- near-duplicates -------------------------------------------------------------------------

def _text_docs(m: Matter):
    """primary text hash -> live documents."""
    state = m.state
    out = defaultdict(list)
    for d in state.live_documents():
        layer = state.primary_layer(state.documents[d])
        if layer:
            out[layer.text].append(d)
    return out


def containment(j: float, n_a: int, n_b: int) -> float:
    """How much of the smaller shingle set is in the larger, from the
    Jaccard estimate and the set sizes: |A∩B| = J/(1+J)·(|A|+|B|)."""
    return min(1.0, j / (1 + j) * (n_a + n_b) / min(n_a, n_b))


def near(m: Matter, doc: str, threshold=0.5) -> dict:
    """Documents whose primary text is similar to this one's, best first:
    ``similarity`` estimates Jaccard similarity of their word 5-shingles,
    and ``containment`` how much of the shorter text is in the longer, which
    finds earlier and later versions. Either at ``threshold`` counts.
    Exact duplicates (same content) are listed separately."""
    state = m.state
    content = state.documents[doc]
    layer = state.primary_layer(content)
    if layer is None:
        raise ValueError("document has no text")
    sigs = _ready(m, "neardup").signatures()
    if layer.text not in sigs:
        raise ValueError("document's text is not in the near-duplicate index")
    mine, my_n = sigs[layer.text]
    by_text = _text_docs(m)
    out = []
    for h, (sig, n) in sigs.items():
        if h == layer.text or not n or not my_n:
            continue
        j = similarity(mine, sig)
        c = containment(j, my_n, n)
        if j >= threshold or c >= threshold:
            for d in by_text.get(h, []):
                out.append({"loc": "doc:" + d, "similarity": round(j, 3), "containment": round(c, 3)})
    out.sort(key=lambda x: (-max(x["similarity"], x["containment"])))
    dupes = ["doc:" + d for d in state.live_documents() if d != doc and state.documents[d] == content]
    same_text = ["doc:" + d for d in by_text.get(layer.text, []) if d != doc and state.documents[d] != content]
    return {"doc": "doc:" + doc, "exact_duplicates": dupes, "same_text": same_text, "near": out}


BANDS, ROWS = 32, 4


def groups(m: Matter, threshold=0.8) -> list[dict]:
    """Clusters of texts whose similarity is at least ``threshold``. Candidate
    pairs come from LSH banding, then each is checked against its signature."""
    sigs = {h: s for h, (s, n) in _ready(m, "neardup").signatures().items() if n}
    by_text = _text_docs(m)
    sigs = {h: s for h, s in sigs.items() if h in by_text}
    buckets = defaultdict(list)
    for h, s in sigs.items():
        for b in range(BANDS):
            buckets[(b, s[b * ROWS:(b + 1) * ROWS].tobytes())].append(h)
    u = _Union()
    checked = set()
    for members in buckets.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                pair = (min(a, b), max(a, b))
                if pair in checked:
                    continue
                checked.add(pair)
                if similarity(sigs[a], sigs[b]) >= threshold:
                    u.union(a, b)
    clusters = defaultdict(list)
    for h in sigs:
        clusters[u.find(h)].append(h)
    out = []
    for texts in clusters.values():
        if len(texts) < 2:
            continue
        texts.sort(key=lambda h: -len(by_text[h]))
        first = sigs[texts[0]]
        out.append({"size": len(texts), "texts": [
            {"text": h, "similarity_to_first": round(similarity(first, sigs[h]), 3),
             "docs": ["doc:" + d for d in by_text[h]]} for h in texts]})
    out.sort(key=lambda g: -g["size"])
    return out


# -- redline -------------------------------------------------------------------------------------

def _primary(m: Matter, doc: str):
    state = m.state
    layer = state.primary_layer(state.documents[doc])
    if layer is None:
        raise ValueError(f"{doc} has no text")
    return layer, m.objects.get_text(layer.text)


def redline(m: Matter, a: str, b: str) -> tuple[dict, list[dict]]:
    """(header, changes) between two documents' primary texts. Tokens are
    compared after search normalization, so line breaks, hyphenation and
    case don't count as changes. Each change has a locator into A (what was
    there) and into B (what replaced it); either can be empty."""
    la, ta = _primary(m, a)
    lb, tb = _primary(m, b)
    xa, xb = textnorm.tokenize(ta), textnorm.tokenize(tb)
    sm = difflib.SequenceMatcher(None, [t for t, _, _ in xa], [t for t, _, _ in xb], autojunk=False)

    def span(toks, text, doc, layer, i, j, other_pos):
        if i < j:
            s, e = toks[i][1], toks[j - 1][2]
            return locators.char_locator(doc, layer.id, text, s, e)
        # an empty side: where the change happened
        p = toks[other_pos][1] if other_pos < len(toks) else len(text)
        return {"doc": doc, "layer": layer.id, "select": {"char": [p, p]}}

    changes = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        changes.append({"op": op, "a": span(xa, ta, a, la, i1, i2, i1), "b": span(xb, tb, b, lb, j1, j2, j1)})
    header = {"a": "doc:" + a, "b": "doc:" + b, "similarity": round(sm.ratio(), 4), "changes": len(changes),
              "words": {"a": len(xa), "b": len(xb)}}
    return header, changes


def redline_html(m: Matter, a: str, b: str) -> str:
    """Static HTML for a viewer pane: B's text with insertions marked and A's
    deleted text struck through. Every run is a data-text span, so the
    viewer fills it from the documents."""
    la, ta = _primary(m, a)
    lb, tb = _primary(m, b)
    xa, xb = textnorm.tokenize(ta), textnorm.tokenize(tb)
    sm = difflib.SequenceMatcher(None, [t for t, _, _ in xa], [t for t, _, _ in xb], autojunk=False)

    def run(text, doc, layer, s, e, cls):
        loc = locators.format({"doc": doc, "layer": layer.id, "select": {"char": [s, e]}})
        inner = html.escape(text[s:e])
        tag = {"ins": "ins", "del": "del"}.get(cls, "span")
        return f'<{tag}><span data-text="{loc}" data-normalize="lines">{inner}</span></{tag}>'

    out = []
    pos_b = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        sb = xb[j1][1] if j1 < len(xb) else len(tb)
        eb = xb[j2 - 1][2] if j2 > j1 else sb
        if sb > pos_b:                       # text between tokens (spaces, punctuation) stays as in B
            out.append(run(tb, b, lb, pos_b, sb, "same"))
        if op in ("delete", "replace") and i2 > i1:
            out.append(run(ta, a, la, xa[i1][1], xa[i2 - 1][2], "del"))
        if j2 > j1:
            out.append(run(tb, b, lb, sb, eb, "ins" if op in ("insert", "replace") else "same"))
        pos_b = max(pos_b, eb)
    if pos_b < len(tb):
        out.append(run(tb, b, lb, pos_b, len(tb), "same"))
    style = ("<style>body{font:14px/1.5 system-ui} .redline{white-space:pre-wrap} ins{background:#dfd;text-decoration:none}"
             " del{background:#fdd;color:#900}</style>")
    return f'{style}<h2>Redline</h2><p><a href="loc:doc:{a}">A</a> → <a href="loc:doc:{b}">B</a> · ' \
           f'{len([o for o in sm.get_opcodes() if o[0] != "equal"])} changes</p><div class="redline">{"".join(out)}</div>'
