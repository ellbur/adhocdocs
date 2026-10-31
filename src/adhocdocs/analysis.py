"""Analysis over the matter: email threads, near-duplicates, redlines, and
a cast of characters from email headers and names in text.

Threads come from Message-ID, In-Reply-To and References (copies of one
message from several custodians are one node; a missing parent still links
its replies). Near-duplicates come from MinHash signatures of each text's
word 5-shingles, compared by estimated Jaccard similarity. A redline diffs
two documents' normalized tokens and gives every change as locators into
both, so it can be quoted, tagged or shown.

People come from email headers and from the names index: names in text
with evidence that they are a person (a title, an initial, a role, a
signature, "By:"), joined to known people by surname and given name or
initial (Tom for Thomas). First names alone ("José") and initials ("TK")
are not counted; search for those. ``people --person X --list F`` writes
the documents that name someone, each mention a child row.
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


# -- people ---------------------------------------------------------------------------------------

def _name_key(name: str) -> str | None:
    """Folded "first last" for a display name, or None if it isn't a usable
    personal name (one word, or an address)."""
    if not name or "@" in name:
        return None
    if "," in name:                                    # "Müller, Zoë" -> "Zoë Müller"
        last, _, first = name.partition(",")
        name = f"{first} {last}"
    words = [w for w in textnorm.words(name)]
    return " ".join(words) if len(words) >= 2 else None


NICKNAMES = [set(g.split()) for g in """
    thomas tom tommy|william will bill billy liam|robert rob bob bobby robbie|richard rick dick rich|james jim jimmy
    jamie|michael mike mick|john jack johnny|joseph joe joey|charles charlie chuck|edward ed eddie ted ned
    |elizabeth liz beth betty eliza lisa|margaret maggie meg peggy|katherine kate katie kathy cathy|patricia pat
    patty trish|jennifer jen jenny|rebecca becky|susan sue suzy|deborah deb debbie|christopher chris|christine
    chris tina|daniel dan danny|anthony tony|andrew andy drew|benjamin ben|matthew matt|nicholas nick
    |alexander alex|alexandra alex|samuel sam|samantha sam|stephen steve|steven steve|timothy tim|gregory greg
    |jonathan jon|nathaniel nate nat|raymond ray|ronald ron|donald don|douglas doug|gerald gerry jerry
    |lawrence larry|leonard len lenny|kenneth ken|frederick fred|francis frank|henry hank harry|harold harry
    |david dave|peter pete|philip phil|zachary zach|joshua josh|jacob jake|abigail abby|victoria vicky tori
""".replace("\n", " ").split("|")]


def same_given(a: str, b: str) -> bool:
    """Whether two folded given names can be the same person's: equal, one a
    prefix of the other (Dan, Daniel), or nicknames (Tom, Thomas)."""
    if a == b or (len(min(a, b, key=len)) >= 3 and (a.startswith(b) or b.startswith(a))):
        return True
    return any(a in g and b in g for g in NICKNAMES)


def _parts(display: str):
    """(given, surname) folded, from a display name, or None."""
    from .names import key
    if not display or "@" in display:
        return None
    if "," in display:
        last, _, first = display.partition(",")
        display = f"{first} {last}"
    words = [key(w).strip(".") for w in display.split() if w.strip(".")]
    return (words[0], words[-1]) if len(words) >= 2 else None


def _display(form: str) -> str:
    """A mention as a name: without its title, and not in capitals."""
    from .names import TITLES
    words = form.split()
    while len(words) > 2 and words[0].rstrip(".").lower() in TITLES:
        words.pop(0)
    if len(words) == 2 and words[0].rstrip(".").lower() in TITLES and words[0].isupper():
        words[0] = words[0].capitalize()                 # MR. KETTERING -> Mr. Kettering; Mr. X keeps its title
    return " ".join("-".join(p.capitalize() for p in w.split("-")) if w.isupper() and len(w.rstrip(".")) > 1 else w
                    for w in words)


def people(m: Matter, mentions=False) -> list[dict]:
    """A cast of characters, from email headers and from names in document
    text (:mod:`adhocdocs.names`).

    From headers, each address is a person, and addresses whose display
    names fold to the same first and last name are merged. A mention in
    text joins a known person when its surname matches and its given name
    (or initial, or a nickname: Tom for Thomas) fits only one of them; a
    surname alone ("Mr. Kettering") joins the only person with it. Mentions
    with evidence that fit no one become new people ``source: text``;
    mentions that are only name-shaped ("Atlantic Gasket") never do.

    Each person has ``mentioned_in`` (documents whose text names them) and
    ``roles`` found beside their name. With ``mentions``, also every
    mention as a locator, with its basis."""
    from . import locators
    rows = _ready(m, "fields").rows()
    state = m.state
    meta = {}
    for d in state.live_documents():
        r = rows.get(d)
        if r and r.get("kind") == "email":
            meta[d] = m.objects.get_object(state.metadata[d])
    u = _Union()
    names = defaultdict(lambda: defaultdict(int))     # address -> display name -> count
    stats = defaultdict(lambda: {"sent": 0, "received": 0, "dates": []})
    for d, md in meta.items():
        for field in ("from", "to", "cc", "bcc"):
            addrs = md.get(field, [])
            shown = md.get(f"{field}_names", addrs)
            for addr, name in zip(addrs, shown):
                u.find(addr)
                if name and name != addr:
                    names[addr][name] += 1
                st = stats[addr]
                st["sent" if field == "from" else "received"] += 1
                if md.get("date"):
                    st["dates"].append(md["date"])
    by_name = {}
    for addr, ns in names.items():
        for n in ns:
            key = _name_key(n)
            if key:
                if key in by_name:
                    u.union(addr, by_name[key])
                else:
                    by_name[key] = addr
    groups = defaultdict(list)
    for addr in stats:
        groups[u.find(addr)].append(addr)
    out = []
    for addrs in groups.values():
        variants = defaultdict(int)
        for a in addrs:
            for n, c in names[a].items():
                variants[n] += c
        dates = sorted(x for a in addrs for x in stats[a]["dates"])
        out.append({
            "name": max(variants, key=variants.get) if variants else addrs[0],
            "source": ["email"],
            "names": sorted(variants, key=lambda n: -variants[n]),
            "addresses": sorted(addrs),
            "domains": sorted({a.rsplit("@", 1)[-1] for a in addrs}),
            "sent": sum(stats[a]["sent"] for a in addrs),
            "received": sum(stats[a]["received"] for a in addrs),
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
        })

    # -- names in text ---------------------------------------------------------------
    docs_of = defaultdict(list)                       # primary text -> live documents
    layer_of = {}
    for d in state.live_documents():
        layer = state.primary_layer(state.documents[d])
        if layer:
            docs_of[layer.text].append(d)
            layer_of[layer.text] = layer.id
    found = _ready(m, "names").mentions(set(docs_of))
    people_ = [{"given": set(), "initials": set(), "surname": None, "row": p} for p in out]
    by_surname = defaultdict(list)
    for p in people_:
        for n in p["row"]["names"]:
            parts = _parts(n)
            if parts:
                p["given"].add(parts[0])
                p["initials"].add(parts[0][0])
                p["surname"] = parts[1]
        if p["surname"]:
            by_surname[p["surname"]].append(p)

    def match(x):
        cands = by_surname.get(x["surname"], [])
        if x["given"]:
            c = [p for p in cands if any(same_given(x["given"], g) for g in p["given"])]
            if not c:
                c = [p for p in cands if not p["given"] and x["initial"] in p["initials"]]
        elif x["initial"]:
            c = [p for p in cands if x["initial"] in p["initials"]]
        else:
            c = cands
        return c[0] if len(c) == 1 else None

    def strength(x):
        return (x["basis"] == ["name"], not x["given"], not x["initial"])

    hits = defaultdict(list)                          # id(person) -> mentions
    for x in sorted(found, key=strength):
        p = match(x)
        if p is None:
            if x["basis"] == ["name"]:
                continue
            p = {"given": set(), "initials": set(), "surname": x["surname"], "row": None}
            people_.append(p)
            by_surname[x["surname"]].append(p)
        if x["given"]:
            p["given"].add(x["given"])
        if x["initial"]:
            p["initials"].add(x["initial"])
        hits[id(p)].append(x)

    for p in people_:
        xs = hits.get(id(p), [])
        if p["row"] is None:
            forms = defaultdict(int)
            for x in xs:
                forms[(bool(x["given"]), bool(x["initial"]), _display(x["form"]))] += 1
            best = max(forms, key=lambda k: (k[0], k[1], forms[k]))
            dates = sorted(d for x in xs for doc in docs_of[x["text_hash"]] if (d := (rows.get(doc) or {}).get("date")))
            p["row"] = {"name": best[2], "source": ["text"], "names": [], "addresses": [], "domains": [],
                        "sent": 0, "received": 0,
                        "first": dates[0] if dates else None, "last": dates[-1] if dates else None}
            basis = defaultdict(int)
            for x in xs:
                for b in x["basis"]:
                    basis[b] += 1
            p["row"]["basis"] = dict(sorted(basis.items(), key=lambda kv: -kv[1]))
            out.append(p["row"])
        elif xs:
            p["row"]["source"] = ["email", "text"]
        row = p["row"]
        written = defaultdict(int)
        roles = defaultdict(int)
        for x in xs:
            written[" ".join(x["form"].split())] += 1
            if x["role"]:
                roles[x["role"]] += 1
        row["written_as"] = sorted((w for w in written if w not in row["names"]), key=lambda w: -written[w])
        row["roles"] = sorted(roles, key=lambda r: -roles[r])
        row["mentioned_in"] = len({doc for x in xs for doc in docs_of[x["text_hash"]]})
        if mentions:
            texts = {}
            row["mentions"] = []
            for x in xs:
                t = texts.get(x["text_hash"]) or texts.setdefault(x["text_hash"], m.objects.get_text(x["text_hash"]))
                for doc in docs_of[x["text_hash"]]:
                    row["mentions"].append({"loc": locators.char_locator(doc, layer_of[x["text_hash"]], t, x["start"], x["end"]),
                                            "basis": x["basis"], **({"role": x["role"]} if x["role"] else {})})
    out.sort(key=lambda p: (-(p["sent"] + p["received"]), -p["mentioned_in"], p["name"]))
    return out
