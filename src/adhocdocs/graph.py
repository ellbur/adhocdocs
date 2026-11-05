"""The matter as a graph: entities, the links between them, and the
documents they rest on.

Nodes are ``entity:<id>`` (the entity list: people, organizations,
contracts, events…), ``doc:<id>`` (documents), and, when links are shown
as facts (``facts=True``), ``link:<id>``. Edges have a ``kind``:

=============  ===========================================================
``link``       entity → entity: a recorded relation (``ann link``), with
               ``rel``, the ``link`` ID, its ``see`` (the evidence) and ``date``
``see``        entity or link → document: the words that define or show it
``mentions``   entity → document: its aliases appear there (``count``). Found
               by matching, not recorded: ``derived``
``co-mention`` entity — entity: named in the same documents (``count``,
               ``docs``). A lead, not a fact: ``derived``
``family``     document → document: an attachment and its parent
``reply``      email → the email it answers, from its threading headers
``quotes``     email → an earlier message in its thread whose text it holds
               (``containment``): ``derived``
``version``    document — document: near-duplicate texts, or one mostly inside
               the other (``similarity``, ``containment``): ``derived``
``copy``       document — document: the same text
``sent``,      person → email: an address among the entity's aliases is the
``received``   sender, or a recipient (``field``: to, cc, bcc)
``emailed``    person — person: emails between their addresses, one edge a pair,
               from whoever wrote more (``count``, ``sent`` one way,
               ``replied`` the other, ``docs``)
``fact``       entity → link node (``role`` from or to), when ``facts``
=============  ===========================================================

Recorded edges (``link``, ``see``) are someone's judgment, with an author.
Derived ones are computed from the text each time, and say so; never
present a co-mention as a relationship.

Everything is plain data (:meth:`Graph.to_json`), so code can load it,
walk it, or hand it to a library. :func:`load` builds it; :meth:`Graph.around`,
:meth:`Graph.paths` and :meth:`Graph.subgraph` query it;
:func:`diagram_html` draws it for a viewer pane (``view graph``).
"""

import base64
import html
from collections import deque

from . import entities as entmod
from .annotations import Annotations
from .matter import Matter

DERIVED = ("mentions", "co-mention", "quotes", "version")
DOC_KINDS = ("family", "reply", "quotes", "version", "copy")

# columns of a diagram laid out by kind, left to right
KIND_COLUMN = {"organization": 0, "person": 1, "event": 2, "contract": 2, "identifier": 2, "thing": 2, "term": 2,
               "place": 2, "other": 2}


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []

    # -- building ---------------------------------------------------------------------------

    def node(self, nid: str, **attrs) -> dict:
        n = self.nodes.get(nid)
        if n is None:
            n = self.nodes[nid] = {"id": nid, **attrs}
        else:
            n.update({k: v for k, v in attrs.items() if v is not None})
        return n

    def edge(self, a: str, b: str, kind: str, **attrs) -> dict:
        e = {"id": f"x{len(self.edges)}", "from": a, "to": b, "kind": kind, **attrs}
        if kind in DERIVED:
            e["derived"] = True
        self.edges.append(e)
        return e

    # -- reading ------------------------------------------------------------------------------

    def incident(self, nid: str, kinds=None) -> list[dict]:
        return [e for e in self.edges if nid in (e["from"], e["to"]) and (kinds is None or e["kind"] in kinds)]

    def other(self, e: dict, nid: str) -> str:
        return e["to"] if e["from"] == nid else e["from"]

    def around(self, roots, depth=1, kinds=None) -> "Graph":
        """The nodes within ``depth`` hops of ``roots`` (over edges of ``kinds``), and the edges among them."""
        dist = {r: 0 for r in roots}
        todo = deque(roots)
        while todo:
            n = todo.popleft()
            if dist[n] >= depth:
                continue
            for e in self.incident(n, kinds):
                o = self.other(e, n)
                if o not in dist:
                    dist[o] = dist[n] + 1
                    todo.append(o)
        g = self.subgraph(dist, kinds)
        for n, d in dist.items():
            g.nodes[n]["depth"] = d
        return g

    def subgraph(self, ids, kinds=None) -> "Graph":
        ids = set(ids)
        g = Graph()
        for i in ids:
            if i in self.nodes:
                g.nodes[i] = dict(self.nodes[i])
        g.edges = [dict(e) for e in self.edges if e["from"] in ids and e["to"] in ids
                   and (kinds is None or e["kind"] in kinds)]
        return g

    def paths(self, a: str, b: str, max_len=4, kinds=None, limit=10, avoid_docs=False) -> list[list[dict]]:
        """The shortest paths from ``a`` to ``b`` (at most ``max_len`` hops,
        at most ``limit`` of them), each a list of steps ``{"from", "edge",
        "to"}``. Edges are walked either way. ``avoid_docs``: don't pass
        through documents (only entities, and links as facts)."""
        if a == b:
            return []
        adj: dict[str, list[dict]] = {}
        for e in self.edges:
            if kinds is None or e["kind"] in kinds:
                adj.setdefault(e["from"], []).append(e)
                adj.setdefault(e["to"], []).append(e)
        # breadth first, keeping every predecessor on a shortest path
        dist, preds = {a: 0}, {a: []}
        frontier = [a]
        while frontier and b not in dist:
            nxt = []
            for n in frontier:
                if dist[n] >= max_len:
                    continue
                if avoid_docs and n != a and self.nodes.get(n, {}).get("type") == "doc":
                    continue
                for e in adj.get(n, []):
                    o = self.other(e, n)
                    if o not in dist:
                        dist[o] = dist[n] + 1
                        preds[o] = [(n, e)]
                        nxt.append(o)
                    elif dist[o] == dist[n] + 1:
                        preds[o].append((n, e))
            frontier = nxt
        if b not in dist:
            return []
        out = []

        def walk(n, tail):
            if len(out) >= limit:
                return
            if n == a:
                out.append(tail)
                return
            for p, e in preds[n]:
                walk(p, [{"from": p, "edge": e, "to": n}] + tail)
        walk(b, [])
        # recorded relations first, then fewer derived hops
        out.sort(key=lambda path: sum(1 for s in path if s["edge"].get("derived")))
        return out

    def to_json(self) -> dict:
        return {"nodes": list(self.nodes.values()), "edges": self.edges}

    def to_dot(self) -> str:
        q = lambda s: '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines = ["digraph matter {", "  rankdir=LR; node [shape=box, style=rounded, fontname=Helvetica];"]
        for n in self.nodes.values():
            shape = {"doc": "note", "link": "ellipse"}.get(n["type"], "box")
            lines.append(f"  {q(n['id'])} [label={q(n['label'])}, shape={shape}];")
        for e in self.edges:
            style = ", style=dashed" if e.get("derived") else ""
            lines.append(f"  {q(e['from'])} -> {q(e['to'])} [label={q(edge_label(e))}{style}];")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def to_mermaid(self) -> str:
        ids = {nid: f"n{i}" for i, nid in enumerate(self.nodes)}
        esc = lambda s: str(s).replace('"', "'")
        lines = ["flowchart LR"]
        for nid, n in self.nodes.items():
            shape = ('["', '"]') if n["type"] != "doc" else ('[/"', '"/]')
            lines.append(f"  {ids[nid]}{shape[0]}{esc(n['label'])}{shape[1]}")
        for e in self.edges:
            arrow = "-.->" if e.get("derived") else "-->"
            lines.append(f'  {ids[e["from"]]} {arrow}|"{esc(edge_label(e))}"| {ids[e["to"]]}')
        return "\n".join(lines) + "\n"


def _emails(n: int) -> str:
    return "1 email" if n == 1 else f"{n} emails"


def _docs(n: int) -> str:
    return "1 document" if n == 1 else f"{n} documents"


def edge_label(e: dict) -> str:
    if e["kind"] == "link":
        return e["rel"]
    if e["kind"] in ("mentions", "co-mention"):
        return f"{e['kind']} ×{e.get('count', 1)}"
    if e["kind"] == "emailed":
        return f"emailed {e['sent']}" + (f" ↔ {e['replied']}" if e.get("replied") else "")
    if e["kind"] == "fact":
        return e.get("role", "")
    return e["kind"]


# -- loading the matter's graph ---------------------------------------------------------------------

def _doc_rows(m: Matter) -> dict:
    from .index import open_index
    try:
        fx = open_index(m, "fields")
        fx.update()
        return fx.rows()
    except Exception:          # no fields index yet: names come from metadata
        return {}


def doc_label(m: Matter, rows: dict, doc: str) -> str:
    r = rows.get(doc) or {}
    label = r.get("subject") or r.get("name")
    return str(label or doc.removeprefix("sha256:")[:12])


def load(m: Matter, mentions=False, cooccur=False, facts=False, facet=None, families=True,
         min_cooccur=2, ann: Annotations | None = None) -> Graph:
    """The matter's graph. Always: entities, links, and the documents in their
    ``see``. ``mentions``: also every document an entity is named in.
    ``cooccur``: entities named together in at least ``min_cooccur``
    documents. ``facts``: each link as a node of its own, joined to its two
    entities and its evidence, so documents point at the facts they support.
    ``facet``: only entities and links filed under it (and links between
    entities filed under it)."""
    ann = ann or Annotations.load(m)
    state = m.state
    rows = _doc_rows(m)
    g = Graph()

    ents = ann.entities
    links = ann.links
    if facet is not None:
        if facet not in ann.facets:
            raise KeyError(f"no facet {facet!r}" + (f"; facets: {', '.join(ann.facets)}" if ann.facets else ""))
        keep_e = {i for i in ents if ann.in_facet(facet, {"entity": i})}
        keep_l = {i for i, ln in links.items() if ann.in_facet(facet, {"link": i})
                  or (ln["from"] in keep_e and ln["to"] in keep_e)}
        for ln in (links[i] for i in keep_l):
            keep_e |= {ln["from"], ln["to"]}
        ents = {i: e for i, e in ents.items() if i in keep_e}
        links = {i: ln for i, ln in links.items() if i in keep_l}

    def live(doc):
        try:
            d = state.follow(doc)
        except Exception:
            return None
        return d if d in state.documents and d not in state.withdrawn else None

    def doc_node(doc):
        d = live(doc)
        if d is None:
            return None
        r = rows.get(d) or {}
        g.node(f"doc:{d}", type="doc", label=doc_label(m, rows, d), doc=d, date=(r.get("date") or "")[:10] or None,
               kind=r.get("kind"), loc=f"doc:{d}")
        return f"doc:{d}"

    for e in ents.values():
        g.node(f"entity:{e['entity']}", type="entity", label=e["name"], entity=e["entity"], kind=e["kind"], aliases=e["aliases"],
               summary=e.get("summary"), date=e.get("date"), by=e["by"],
               facets=ann.facets_of({"entity": e["entity"]}), see=e.get("see") or [])
    for e in ents.values():
        for t in e.get("see") or []:
            d = doc_node(t["doc"])
            if d:
                g.edge(f"entity:{e['entity']}", d, "see", loc=t)
    for ln in links.values():
        a, b = f"entity:{ln['from']}", f"entity:{ln['to']}"
        if a not in g.nodes or b not in g.nodes:
            continue
        attrs = {"rel": ln["rel"], "link": ln["link"], "see": ln.get("see") or [], "date": ln.get("date"),
                 "summary": ln.get("summary"), "by": ln["by"], "facets": ann.facets_of({"link": ln["link"]})}
        if facts:
            lid = f"link:{ln['link']}"
            g.node(lid, type="link", label=f"{ents[ln['from']]['name']} {ln['rel']} {ents[ln['to']]['name']}", **attrs)
            g.edge(a, lid, "fact", role="from")
            g.edge(lid, b, "fact", role="to")
            src = lid
        else:
            g.edge(a, b, "link", **attrs)
            src = None
        for t in ln.get("see") or []:
            d = doc_node(t["doc"])
            if d and src:
                g.edge(src, d, "see", loc=t)

    if mentions or cooccur:
        found = entmod.mentions(m, list(ents.values()))
        per: dict[tuple, list] = {}
        for x in found:
            per.setdefault((x["entity"], x["loc"]["doc"]), []).append(x["loc"])
        by_doc: dict[str, set] = {}
        for (eid, doc), locs in per.items():
            by_doc.setdefault(doc, set()).add(eid)
            if mentions:
                d = doc_node(doc)
                if d:
                    g.edge(f"entity:{eid}", d, "mentions", count=len(locs), loc=locs[0])
        if cooccur:
            pairs: dict[tuple, list] = {}
            for doc, es in by_doc.items():
                es = sorted(es)
                for i, a in enumerate(es):
                    for b in es[i + 1:]:
                        pairs.setdefault((a, b), []).append(doc)
            for (a, b), docs in pairs.items():
                if len(docs) >= min_cooccur:
                    g.edge(f"entity:{a}", f"entity:{b}", "co-mention", count=len(docs), docs=docs[:50])

    if families:
        docs_here = [n["doc"] for n in g.nodes.values() if n["type"] == "doc"]
        parent = {c: p for p, cs in state.children.items() for c in cs}
        for d in docs_here:
            p = parent.get(d)
            if p and f"doc:{p}" in g.nodes:
                g.edge(f"doc:{p}", f"doc:{d}", "family")
    return g


def resolve(m: Matter, g: Graph, ref: str, ann: Annotations | None = None) -> str:
    """A node ID from an entity (ID, name or alias), a link ID, or a document (ID or prefix, ``doc:`` optional)."""
    if ref in g.nodes:
        return ref
    if ref.startswith("l_") and f"link:{ref}" in g.nodes:
        return f"link:{ref}"
    try:
        e = entmod.resolve(m, ref, list((ann or Annotations.load(m)).entities.values()))
        return f"entity:{e['entity']}"
    except KeyError as err:
        if "could be any of" in str(err):
            raise
    from . import locators
    try:
        loc = locators.parse("doc:" + ref.removeprefix("doc:"), m.objects)
        d = m.state.follow(loc["doc"])
        if f"doc:{d}" not in g.nodes:
            g.node(f"doc:{d}", type="doc", label=doc_label(m, _doc_rows(m), d), doc=d, loc=f"doc:{d}")
        return f"doc:{d}"
    except Exception:
        pass
    rows = _doc_rows(m)
    named = [d for d, r in rows.items() if ref in (r.get("name"), r.get("subject")) and d in m.state.documents
             and d not in m.state.withdrawn]
    if len(named) == 1:
        d = named[0]
        if f"doc:{d}" not in g.nodes:
            g.node(f"doc:{d}", type="doc", label=doc_label(m, rows, d), doc=d, loc=f"doc:{d}")
        return f"doc:{d}"
    if named:
        raise KeyError(f"{len(named)} documents are called {ref!r}; give a document ID")
    raise KeyError(f"no entity, link or document {ref!r}")


def describe_step(g: Graph, s: dict) -> dict:
    """A path step for people: who, how, and on what evidence."""
    e = s["edge"]
    name = lambda n: g.nodes[n]["label"]
    forward = e["from"] == s["from"]
    out = {"from": name(s["from"]), "to": name(s["to"]), "kind": e["kind"]}
    if e["kind"] == "link":
        out["says"] = f"{name(e['from'])} {e['rel']} {name(e['to'])}"
        out["link"] = e["link"]
        if e.get("see"):
            out["evidence"] = e["see"]
        else:
            out["evidence"] = []
            out["unsupported"] = True
    elif e["kind"] == "mentions":
        out["says"] = f"{name(e['from'])} is named in {name(e['to'])}"
        out["evidence"] = [e["loc"]]
    elif e["kind"] == "co-mention":
        out["says"] = f"{name(e['from'])} and {name(e['to'])} are named together in {_docs(e['count'])} (a lead, not a relation)"
        out["docs"] = e.get("docs", [])
    elif e["kind"] == "see":
        out["says"] = f"{name(e['from'])} is shown by {name(e['to'])}"
        out["evidence"] = [e["loc"]]
    elif e["kind"] == "family":
        out["says"] = f"{name(e['to'])} is attached to {name(e['from'])}"
    elif e["kind"] == "reply":
        out["says"] = f"{name(e['from'])} replies to {name(e['to'])}"
    elif e["kind"] == "quotes":
        out["says"] = f"{name(e['from'])} quotes {name(e['to'])} (its text is {e['containment']:.0%} inside)"
    elif e["kind"] == "version":
        out["says"] = (f"{name(e['from'])} and {name(e['to'])} are versions of one text "
                       f"(similarity {e['similarity']:.0%}, the shorter {e['containment']:.0%} inside the longer)")
    elif e["kind"] == "copy":
        out["says"] = f"{name(e['from'])} and {name(e['to'])} have the same text"
    elif e["kind"] in ("sent", "received"):
        out["says"] = f"{name(e['from'])} {'sent' if e['kind'] == 'sent' else 'received'} {name(e['to'])}" + (
            f" ({e['field']})" if e.get("field") and e["kind"] == "received" else "")
    elif e["kind"] == "emailed":
        out["says"] = (f"{name(e['from'])} emailed {name(e['to'])} {_emails(e['sent'])}"
                       + (f", and {name(e['to'])} wrote back {_emails(e['replied'])}" if e.get("replied") else "")
                       + " (from the headers)")
        out["docs"] = e.get("docs", [])
    else:
        out["says"] = f"{name(e['from'])} {edge_label(e)} {name(e['to'])}"
    if e.get("derived"):
        out["derived"] = True
    if not forward:
        out["walked"] = "backwards"
    return out


def unsupported(ann: Annotations, facet=None, all_entities=False) -> list[dict]:
    """Links and events that rest on nothing: no ``see``. The facts that
    still need a document, or a word to the user that there is none.
    People and organizations are shown by their mentions; with
    ``all_entities``, every entity without a ``see`` is listed too."""
    out = []
    names = {i: e["name"] for i, e in ann.entities.items()}
    for ln in ann.links.values():
        if not ln.get("see") and ann.in_facet(facet, {"link": ln["link"]}):
            out.append({"link": ln["link"], "says": f"{names.get(ln['from'])} {ln['rel']} {names.get(ln['to'])}", "by": ln["by"]})
    for e in ann.entities.values():
        if not e.get("see") and ann.in_facet(facet, {"entity": e["entity"]}) and (all_entities or e["kind"] == "event"):
            out.append({"entity": e["entity"], "name": e["name"], "kind": e["kind"], "by": e["by"]})
    return out


# -- the diagram ----------------------------------------------------------------------------------
# A layered drawing: nodes in columns (by kind, or by hops from the center),
# an edge that skips columns passes through a waypoint in each column it
# crosses, so it runs between boxes rather than through them, and each
# column is ordered to cross fewer edges (barycenters, a few sweeps).

NODE_H, ROW_GAP, WAY_H, COL_GAP, PAD = 32, 14, 8, 150, 24
MIN_W, MAX_W, CHAR_W, SUB_W = 110, 290, 7.2, 5.9


def _columns(g: Graph, layout: str) -> dict[str, int]:
    if layout == "depth" and any("depth" in n for n in g.nodes.values()):
        return {i: n.get("depth", 0) for i, n in g.nodes.items()}
    facts = any(n["type"] == "link" for n in g.nodes.values())
    col = {}
    for i, n in g.nodes.items():
        if n["type"] == "doc":
            col[i] = 9
        elif n["type"] == "link":
            col[i] = 5
        elif facts:
            col[i] = 0                          # entities | the facts about them | the documents they rest on
        else:
            col[i] = KIND_COLUMN.get(n.get("kind"), 2)
    used = sorted(set(col.values()))            # close up empty columns
    return {i: used.index(c) for i, c in col.items()}


def _width(n: dict) -> float:
    text = max(len(n["label"]) * CHAR_W, len(_sub(n)) * SUB_W)
    return max(MIN_W, min(MAX_W + (100 if n["type"] == "link" else 0), 18 + text))


def _sub(n: dict) -> str:
    if n["type"] == "entity":
        return " · ".join(x for x in (n.get("kind"), n.get("date")) if x)
    if n["type"] == "link":
        return "fact" + (f" · {n['date']}" if n.get("date") else "") + ("" if n.get("see") else " · no evidence")
    return " · ".join(str(x) for x in (n.get("kind") or "document", n.get("date")) if x)


def _layout(g: Graph, col: dict[str, int]):
    """Positions: ``{node or waypoint: (x, y, w, h)}``, and for each edge its
    route (the nodes and waypoints it passes through, in column order)."""
    cols: dict[int, list[str]] = {}
    order = sorted(g.nodes, key=lambda i: (g.nodes[i]["type"] != "entity", g.nodes[i].get("kind") or "",
                                           g.nodes[i].get("date") or "", g.nodes[i]["label"].lower()))
    for i in order:
        cols.setdefault(col[i], []).append(i)
    routes, nbrs = {}, {i: [] for i in g.nodes}
    for e in g.edges:
        a, b = e["from"], e["to"]
        if a not in col or b not in col:
            continue
        ca, cb = col[a], col[b]
        step = 1 if cb >= ca else -1
        route = [a]
        for c in range(ca + step, cb, step):
            w = f"~{e['id']}:{c}"
            cols.setdefault(c, []).append(w)
            nbrs[w] = []
            route.append(w)
        route.append(b)
        routes[e["id"]] = route
        for x, y in zip(route, route[1:]):
            nbrs[x].append(y)
            nbrs[y].append(x)
    for sweep in range(8):
        pos = {i: k for c in cols.values() for k, i in enumerate(c)}
        for c in (sorted(cols) if sweep % 2 == 0 else sorted(cols, reverse=True)):
            def bary(i, c=c):
                ps = [pos[o] for o in nbrs[i] if (col[o] if o in col else _wcol(o)) != c]
                return sum(ps) / len(ps) if ps else pos[i]
            cols[c].sort(key=bary)
            pos.update({i: k for k, i in enumerate(cols[c])})
    widths = {c: max((_width(g.nodes[i]) for i in ids if i in g.nodes), default=40) for c, ids in cols.items()}
    heights = {c: sum((NODE_H + ROW_GAP) if i in g.nodes else (WAY_H + 4) for i in ids) for c, ids in cols.items()}
    tallest = max(heights.values(), default=NODE_H)
    box, x = {}, PAD
    for c in sorted(cols):
        y = PAD + (tallest - heights[c]) / 2
        for i in cols[c]:
            if i in g.nodes:
                box[i] = (x, y, _width(g.nodes[i]), NODE_H)
                y += NODE_H + ROW_GAP
            else:
                box[i] = (x, y, widths[c], WAY_H)
                y += WAY_H + 4
        x += widths[c] + COL_GAP
    return box, routes, x - COL_GAP + PAD, tallest + PAD * 2


def _wcol(w: str) -> int:
    return int(w.rsplit(":", 1)[1])


def _trim(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:max(1, n - 1)] + "…"


def _node_loc(n: dict) -> str | None:
    """Where clicking a node goes: a document, or the words that show an entity or fact."""
    from . import locators
    if n["type"] == "doc":
        return n["loc"]
    see = n.get("see") or []
    return locators.format(see[0]) if see else None


def _path(g, box, route) -> tuple[str, tuple[float, float]]:
    """An SVG path through a route, and where its label goes (the middle of its first gap)."""
    pts = []
    for k, i in enumerate(route):
        x, y, w, h = box[i]
        cy = y + h / 2
        if i in g.nodes:
            nxt = route[k + 1] if k + 1 < len(route) else route[k - 1]
            right = box[nxt][0] > x
            pts.append(((x + w) if right else x, cy, (x + w) if right else x, cy))
        else:
            pts.append((x, cy, x + w, cy))            # a waypoint: straight across its column
    # pts: (x_in, y, x_out, y) per stop, entering at x_in and leaving at x_out
    if len(route) == 2 and box[route[0]][0] == box[route[1]][0]:
        # one column: out past its widest box and back, so the arc and its label clear every box
        (x1, y1, _, _), (x2, y2, _, _) = pts
        left = box[route[0]][0]
        right = max(bx + bw for bx, _, bw, _ in box.values() if bx == left)
        bend = 64 + min(90, abs(y2 - y1) / 4)
        return (f"M{x1:.0f},{y1:.0f} C{right + bend:.0f},{y1:.0f} {right + bend:.0f},{y2:.0f} {x2 + 2:.0f},{y2:.0f}",
                (right + bend * 0.75, (y1 + y2) / 2, "middle"))             # on the arc, at its farthest out
    first = pts[0]
    d = [f"M{first[2]:.0f},{first[1]:.0f}"]
    label = None
    for (ax, ay, axo, _), (bx, by, bxo, _) in zip(pts, pts[1:]):
        sx, ex = axo, bx
        if bx < ax:                                    # leftwards: leave from the left, arrive on the right
            sx, ex = ax, bxo
        mid = (sx + ex) / 2
        if d[-1].startswith("M"):
            d[-1] = f"M{sx:.0f},{ay:.0f}"
        d.append(f"C{mid:.0f},{ay:.0f} {mid:.0f},{by:.0f} {ex:.0f},{by:.0f}")
        if label is None:
            label = (mid, (ay + by) / 2, "middle")
        if (bx, bxo) != (ex, ex) and bxo != bx:        # cross a waypoint's column
            d.append(f"L{(bxo if ex == bx else bx):.0f},{by:.0f}")
    return " ".join(d), label


def diagram_html(m: Matter, g: Graph, title: str = "Relationships", layout: str = "kind",
                 note: str | None = None) -> tuple[dict, list[str], dict]:
    """(files, sequence, summary): an SVG diagram of the graph for a viewer
    pane. Nodes and links with evidence are ``data-locator`` elements, so a
    click previews the evidence in the linked document pane and j/k steps
    through them. Links with no evidence are red and dashed; edges found by
    matching text are dashed and grey."""
    from . import locators
    col = _columns(g, layout)
    box, routes, width, height = _layout(g, col)
    esc = html.escape
    out = [f"<h2>{esc(title)}</h2>"]
    n_ent = sum(1 for n in g.nodes.values() if n["type"] == "entity")
    n_doc = sum(1 for n in g.nodes.values() if n["type"] == "doc")
    n_link = sum(1 for e in g.edges if e["kind"] == "link") + sum(1 for n in g.nodes.values() if n["type"] == "link")
    n_derived = sum(1 for e in g.edges if e.get("derived"))
    n_bare = sum(1 for e in g.edges if e["kind"] == "link" and not e.get("see")) \
        + sum(1 for n in g.nodes.values() if n["type"] == "link" and not n.get("see"))
    parts = [f"{n_ent} entities", f"{n_link} links"] + ([f"{n_doc} documents"] if n_doc else []) \
        + ([f"{n_bare} without evidence (red)"] if n_bare else []) \
        + ([f"{n_derived} found by matching text (dashed)"] if n_derived else [])
    out.append(f'<p class="adhoc-query">{" · ".join(parts)}' + (f" · {esc(note)}" if note else "")
               + " · click a box or a label to see its evidence</p>")
    svg_at = len(out)
    out.append("")                      # the <svg> tag, once the labels say how wide it is
    out.append('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
               'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="adhoc-graph-arrow"/></marker></defs>')
    sequence, labels = [], []
    for e in g.edges:
        if e["id"] not in routes:
            continue
        d, (mx, my, anchor) = _path(g, box, routes[e["id"]])
        bare = (e["kind"] == "link" and not e.get("see"))
        cls = f"adhoc-graph-edge kind-{e['kind']}" + (" derived" if e.get("derived") else "") + (" unsupported" if bare else "")
        marker = ' marker-end="url(#arr)"' if e["kind"] in ("link", "see", "family", "reply", "quotes", "sent", "received") \
            or e.get("role") == "to" else ""
        tip = esc(_edge_tip(g, e))
        out.append(f'<path class="{cls}" d="{d}"{marker}><title>{tip}</title></path>')
        if e["kind"] in ("link", "co-mention", "mentions", "emailed", "reply", "quotes", "version", "copy"):
            loc = locators.format(e["see"][0]) if e.get("see") else (locators.format(e["loc"]) if e.get("loc") else (
                f"doc:{e['docs'][0]}" if e.get("docs") else None))
            text = _trim(edge_label(e), 32)
            if anchor == "start":           # beside an arc in one column: clear of the boxes it joins
                mx += (8 + len(text) * 6.1) / 2
            labels.append([mx, my, text, tip, loc, cls.replace("adhoc-graph-edge", "").strip()])
    for i, (x, y, w, h) in box.items():
        if i not in g.nodes:
            continue
        n = g.nodes[i]
        loc = _node_loc(n)
        if loc:
            sequence.append(loc)
        cls = f"adhoc-graph-node type-{n['type']} kind-{esc(str(n.get('kind') or ''))}" + (
            " unsupported" if n["type"] == "link" and not n.get("see") else "")
        attrs = f' data-locator="{esc(loc)}"' if loc else ""
        chars = int((w - 14) / CHAR_W)
        out.append(f'<g class="{cls}"{attrs} transform="translate({x:.0f},{y:.0f})"><title>{esc(_node_tip(n))}</title>'
                   f'<rect width="{w:.0f}" height="{h}" rx="{4 if n["type"] != "link" else 15}"/>'
                   f'<text x="8" y="14" class="adhoc-graph-name">{esc(_trim(n["label"], chars))}</text>'
                   f'<text x="8" y="26" class="adhoc-graph-sub">{esc(_trim(_sub(n), int((w - 14) / SUB_W)))}</text></g>')
    # labels: no two in the same gap on top of each other
    labels.sort(key=lambda l: (round(l[0] / 40), l[1]))
    for k, lab in enumerate(labels):
        for prev in labels[:k]:
            if abs(prev[0] - lab[0]) < 90 and abs(prev[1] - lab[1]) < 17:
                lab[1] = prev[1] + 17
    for mx, my, text, *_ in labels:
        width = max(width, mx + (8 + len(text) * 6.1) / 2 + PAD)
        height = max(height, my + 12)
    out[svg_at] = (f'<div class="adhoc-graph" data-sequence><svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
                   f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">')
    for mx, my, text, tip, loc, cls in labels:
        w = 8 + len(text) * 6.1
        attrs = f' data-locator="{esc(loc)}"' if loc else ""
        if loc:
            sequence.append(loc)
        out.append(f'<g class="adhoc-graph-label {cls}"{attrs} transform="translate({mx - w / 2:.0f},{my - 8:.0f})">'
                   f'<title>{tip}</title><rect width="{w:.0f}" height="15" rx="3"/><text x="4" y="11">{esc(text)}</text></g>')
    out.append("</svg></div>")
    if not g.nodes:
        out.append('<p class="adhoc-query">Nothing to show: no entities or links here yet.</p>')
    files = {"index.html": base64.b64encode("\n".join(out).encode()).decode()}
    summary = {"nodes": len(g.nodes), "edges": len(g.edges), "entities": n_ent, "links": n_link, "documents": n_doc,
               "unsupported": n_bare, "derived": n_derived, "columns": len(set(col.values())),
               "width": round(width), "height": round(height)}
    return files, list(dict.fromkeys(sequence)), summary


def _node_tip(n: dict) -> str:
    bits = [n["label"]]
    if n["type"] == "entity":
        bits.append(n.get("kind") or "")
        if n.get("summary"):
            bits.append(n["summary"])
        if n.get("facets"):
            bits.append("issues: " + ", ".join(n["facets"]))
    elif n["type"] == "link":
        if n.get("summary"):
            bits.append(n["summary"])
        k = len(n.get("see") or [])
        bits.append(("1 piece of evidence" if k == 1 else f"{k} pieces of evidence") if k else "no evidence recorded")
    else:
        bits.append(" · ".join(str(x) for x in (n.get("date"), n.get("kind")) if x))
    return "\n".join(b for b in bits if b)


def _edge_tip(g: Graph, e: dict) -> str:
    a, b = g.nodes[e["from"]]["label"], g.nodes[e["to"]]["label"]
    if e["kind"] == "link":
        t = f"{a} {e['rel']} {b}"
        if e.get("date"):
            t += f" ({e['date']})"
        if e.get("summary"):
            t += "\n" + e["summary"]
        t += "\n" + (f"evidence: {len(e['see'])}" if e.get("see") else "no evidence recorded")
        return t
    if e["kind"] == "co-mention":
        return f"{a} and {b} are named together in {_docs(e['count'])}. Found by matching, not a recorded relation."
    if e["kind"] == "mentions":
        return f"{a} is named {e['count']}× in {b}"
    if e["kind"] == "emailed":
        return f"{a} emailed {b} {_emails(e['sent'])}" + (f"; {b} wrote back {_emails(e['replied'])}" if e.get("replied") else "") \
            + ". From the headers; click for the first email."
    return f"{a} → {b} ({e['kind']})"



# -- an entity's page ------------------------------------------------------------------------------

def dossier(m: Matter, ref: str, mentions_limit: int = 15) -> str:
    """A literate page (docs/PAGES.md) about one entity: what it is, how it is
    connected, on what evidence, in date order, and where it is named. The
    evidence is quoted by reference, so every quotation is checked and
    opens the document; links with no evidence say so. Summaries are the
    paraphrase of whoever wrote them, attributed. Mentions are found by
    matching names, and are labeled as such."""
    import re
    from . import locators
    ann = Annotations.load(m)
    e = entmod.resolve(m, ref, list(ann.entities.values()))
    ents = ann.entities
    rows = _doc_rows(m)
    refs: list[dict] = []
    seen: dict[str, str] = {}
    who = lambda by: "Claude" if by == "claude" else "the user" if str(by).startswith("user:") else str(by)

    def quote(t: dict) -> str:
        """A block quotation of a text range, or a link to a whole document."""
        doc = m.state.follow(t["doc"])
        name = doc_label(m, rows, doc)
        sel = t.get("select") or {}
        if not ("char" in sel and t.get("quote") and t.get("layer")):
            return f"[{name}](loc:{locators.format({'doc': doc, **({'select': sel} if sel else {})})})"
        key = locators.format({k: t[k] for k in ("doc", "layer", "select")})
        if key not in seen:
            seen[key] = f"e{len(seen) + 1}"
            refs.append({"id": seen[key], **{k: t[k] for k in ("doc", "layer", "select", "quote")}})
        return f"\n::q{{#{seen[key]}}}\n"

    title = e["name"]
    kind = e["kind"] + (f", {e['date']}" if e.get("date") else "")
    out = [f"# {title}", "", f"*{kind}*" + (f" · aliases: {', '.join(a for a in e['aliases'] if a != e['name'])}"
                                            if len(e["aliases"]) > 1 else ""), ""]
    if e.get("summary"):
        out += [f"{e['summary']} *(summary by {who(e['set_by'].get('summary', e['by']))})*", ""]
    facets = ann.facets_of({"entity": e["entity"]})
    if facets:
        out += ["Issues: " + ", ".join(facets), ""]
    if e.get("see"):
        out += ["## What shows it", ""]
        for t in e["see"]:
            out.append(quote(t))
        out.append("")

    links = [ln for ln in ann.links.values() if e["entity"] in (ln["from"], ln["to"])]
    if links:
        out += ["## Connections", ""]
        links.sort(key=lambda ln: (ln["rel"], str(ln.get("date") or "")))
        for ln in links:
            other = ents.get(ln["to"] if ln["from"] == e["entity"] else ln["from"], {"name": "?"})
            sentence = f"{ents[ln['from']]['name']} **{ln['rel']}** {ents[ln['to']]['name']}"
            line = f"- {sentence}" + (f" ({ln['date']})" if ln.get("date") else "")
            if ln.get("summary"):
                line += f". {ln['summary']} *({who(ln['set_by'].get('summary', ln['by']))})*"
            out.append(line)
            if ln.get("see"):
                for t in ln["see"]:
                    q = quote(t)
                    out.append(("  " + q.strip()) if q.startswith("\n") else f"  Evidence: {q}")
                    out.append("")
            else:
                out.append("  *No document recorded for this link.*")
                out.append("")
            del other

    dated = [(str(ln["date"]), f"{ents[ln['from']]['name']} {ln['rel']} {ents[ln['to']]['name']}") for ln in links if ln.get("date")]
    if e.get("date"):
        dated.append((str(e["date"]), e["name"]))
    for ln in links:
        o = ents.get(ln["to"] if ln["from"] == e["entity"] else ln["from"])
        if o and o.get("date") and o["kind"] == "event":
            dated.append((str(o["date"]), o["name"]))
    dated = sorted(set(dated))
    if len(dated) > 1:
        out += ["## In date order", ""] + [f"- {d}: {what}" for d, what in dated] + [""]

    found = entmod.mentions(m, [e])
    per: dict[str, list] = {}
    for x in found:
        per.setdefault(x["loc"]["doc"], []).append(x["loc"])
    if per:
        out += ["## Where it is named", "",
                f"Named {len(found)} times in {len(per)} documents (found by matching its names in the text). "
                f"The {min(mentions_limit, len(per))} with the most:", ""]
        top = sorted(per.items(), key=lambda kv: (-len(kv[1]), (rows.get(kv[0]) or {}).get("date") or ""))[:mentions_limit]
        for doc, locs in top:
            r = rows.get(doc) or {}
            when = (r.get("date") or "")[:10]
            out.append(f"- [{doc_label(m, rows, doc)}](loc:{locators.format(locs[0])})" + (f", {when}" if when else "")
                       + f": {len(locs)}×")
        out.append("")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).rstrip() + "\n"
    if refs:
        from .literate import format_refs
        text += "\n" + format_refs(refs)
    return text


# -- an entity's timeline ------------------------------------------------------------------------

def timeline_list(m: Matter, refs, facet=None, lanes="other", doc_dates=False,
                  ann: Annotations | None = None) -> tuple[dict, list[dict], dict]:
    """(header, items, summary): a list file (docs/LISTS.md) of the dated links of
    the entities ``refs`` name, for ``view timeline``. One item a link, at
    its first piece of evidence, with fields ``date``, ``event`` (the link
    as a sentence), ``rel``, ``link``, ``other`` and ``evidence`` (how
    many). A link with no date of its own takes the date of an event at
    either end (preferring the other end). An entity's own date (an event, with evidence) is an item
    too.

    Undated links go in the timeline's Undated list, rather than at their
    evidence's date: the email that shows Voss works for Harrowgate doesn't
    say when he started. ``doc_dates`` places them at that date anyway,
    marked approximate (``date_from: document``). A link with no evidence
    has nowhere to point, so it is left out and counted in the summary
    (``graph unsupported`` lists them). ``lanes``: ``other`` (the entity at
    the other end), ``rel`` (the relation), or ``none``."""
    from . import lists, locators
    ann = ann or Annotations.load(m)
    ents = ann.entities
    roots = [entmod.resolve(m, r, list(ents.values())) for r in refs]
    ids = {e["entity"] for e in roots}
    if facet is not None and facet not in ann.facets:
        raise KeyError(f"no facet {facet!r}" + (f"; facets: {', '.join(ann.facets)}" if ann.facets else ""))
    state = m.state

    def evidence(see):
        """The live targets among ``see``, each followed to its current document."""
        live = []
        for t in see or []:
            try:
                d = state.follow(t["doc"])
            except Exception:
                continue
            if d in state.documents and d not in state.withdrawn:
                live.append({**t, "doc": d})
        return live

    def loc(t):
        return locators.format({k: t[k] for k in ("doc", "layer", "select") if t.get(k)})

    items, no_evidence, undated = [], [], 0
    for e in roots:
        if e.get("date") and evidence(e.get("see")):
            ev = evidence(e["see"])
            items.append({"loc": loc(ev[0]), "group": e["name"] if lanes == "other" else e["kind"] if lanes == "rel" else "",
                          "fields": {"date": str(e["date"]), "event": e["name"], "entity": e["entity"], "evidence": len(ev)}})
    for ln in ann.links.values():
        if not ({ln["from"], ln["to"]} & ids):
            continue
        if facet is not None and not (ann.in_facet(facet, {"link": ln["link"]})
                                      or (ann.in_facet(facet, {"entity": ln["from"]}) and ann.in_facet(facet, {"entity": ln["to"]}))):
            continue
        a, b = ents[ln["from"]], ents[ln["to"]]
        says = f"{a['name']} {ln['rel']} {b['name']}"
        ev = evidence(ln.get("see"))
        if not ev:
            no_evidence.append({"link": ln["link"], "says": says})
            continue
        other = b if ln["from"] in ids else a
        when = ln.get("date") or next((x["date"] for x in (other, a, b) if x["kind"] == "event" and x.get("date")), None)
        f = {"event": says, "rel": ln["rel"], "link": ln["link"], "other": other["name"], "evidence": len(ev)}
        if when:
            f["date"] = str(when)
        elif doc_dates:
            f["date_from"] = "document"          # no "date": the timeline takes the evidence's
        else:
            f["undated"] = True                  # and not to be guessed from the document's date
            undated += 1
        if ln.get("summary"):
            f["summary"] = ln["summary"]
        group = {"other": other["name"], "rel": ln["rel"], "none": ""}[lanes]
        items.append({"loc": loc(ev[0]), "group": group, "fields": f})
    names = ", ".join(e["name"] for e in roots)
    header, _ = lists.new(f"Timeline: {names}" + (f" · {facet}" if facet else ""), [],
                          columns=["date", "event", "other", "evidence"])
    header["entities"] = sorted(ids)
    if facet:
        header["facet"] = facet
    summary = {"entities": [{"entity": e["entity"], "name": e["name"]} for e in roots], "items": len(items),
               "undated": undated, "no_evidence": no_evidence}
    return header, items, summary


# -- documents and correspondence ------------------------------------------------------------------

def doc_relations(m: Matter) -> dict[str, list[dict]]:
    """How documents relate (:func:`adhocdocs.analysis.doc_links`), as the
    near-duplicate index keeps them."""
    from .index import open_index
    idx = open_index(m, "neardup")
    if idx.behind():
        idx.update()
    return idx.links()


def add_doc_links(m: Matter, g: Graph, hops: int = 1, kinds=DOC_KINDS) -> Graph:
    """Add the relations between documents (replies, quotations, versions,
    copies, families) for the documents in ``g``, ``hops`` steps out: each
    step adds the documents related to the ones already there."""
    rel = doc_relations(m) if set(kinds) - {"family"} else {}
    rows = _doc_rows(m)
    state = m.state
    parent = {c: p for p, cs in state.children.items() for c in cs}
    children = state.children
    have = {(e["from"], e["to"], e["kind"]) for e in g.edges}

    def node(d):
        if f"doc:{d}" not in g.nodes:
            r = rows.get(d) or {}
            g.node(f"doc:{d}", type="doc", label=doc_label(m, rows, d), doc=d, date=(r.get("date") or "")[:10] or None,
                   kind=r.get("kind"), loc=f"doc:{d}")
        return f"doc:{d}"

    def edge(a, b, kind, **attrs):
        if (a, b, kind) in have or (kind in ("version", "copy") and (b, a, kind) in have):
            return
        have.add((a, b, kind))
        g.edge(a, b, kind, **attrs)

    live = set(state.live_documents())
    frontier = [n["doc"] for n in g.nodes.values() if n["type"] == "doc"]
    for _ in range(max(0, hops)):
        nxt = []
        for d in frontier:
            out = []
            if "family" in kinds:
                if parent.get(d) in live:
                    out.append((parent[d], d, "family", {}))
                out += [(d, c, "family", {}) for c in children.get(d, []) if c in live]
            for x in rel.get(d, []):
                o = x["doc"]
                if o not in live:
                    continue
                more = {k: x[k] for k in ("similarity", "containment") if k in x}
                kind = {"replies-to": "reply", "replied-by": "reply", "quotes": "quotes", "quoted-by": "quotes",
                        "version": "version", "copy": "copy"}[x["rel"]]
                if kind not in kinds:
                    continue
                a, b = (o, d) if x["rel"] in ("replied-by", "quoted-by") else (d, o)
                out.append((a, b, kind, more))
            for a, b, kind, more in out:
                new = [x for x in (a, b) if f"doc:{x}" not in g.nodes]
                edge(node(a), node(b), kind, **more)
                nxt += new
        frontier = nxt
    return g


def _addresses(e: dict) -> set[str]:
    return {a.lower() for a in e["aliases"] if "@" in a and " " not in a.strip()}


def add_correspondence(m: Matter, g: Graph, docs=True) -> Graph:
    """Who emailed whom, from the headers. A person entity is matched to
    emails by the addresses among its aliases (``ann add-people`` puts them
    there). Adds ``emailed`` edges between entities (with the count and the
    emails), and, with ``docs``, ``sent`` and ``received`` edges to each email."""
    state = m.state
    rows = _doc_rows(m)
    owner: dict[str, str] = {}
    for n in g.nodes.values():
        if n["type"] == "entity":
            e = {"aliases": n.get("aliases") or []}
            for a in _addresses(e):
                owner.setdefault(a, n["id"])
    if not owner:
        return g
    pairs: dict[tuple, list] = {}
    for d in state.live_documents():
        r = rows.get(d)
        if not r or r.get("kind") != "email" or d not in state.metadata:
            continue
        md = m.objects.get_object(state.metadata[d])
        senders = [owner[a.lower()] for a in md.get("from", []) if a.lower() in owner]
        recips = [(owner[a.lower()], f) for f in ("to", "cc", "bcc") for a in md.get(f, []) if a.lower() in owner]
        if not senders and not recips:
            continue
        if docs:
            dn = f"doc:{d}"
            if dn not in g.nodes:
                g.node(dn, type="doc", label=doc_label(m, rows, d), doc=d, date=(r.get("date") or "")[:10] or None,
                       kind="email", loc=dn)
            for s in dict.fromkeys(senders):
                g.edge(s, dn, "sent", loc=dn)
            for p, f in dict.fromkeys(recips):
                g.edge(p, dn, "received", field=f, loc=dn)
        for s in dict.fromkeys(senders):
            for p, _ in dict.fromkeys(recips):
                if p != s:
                    pairs.setdefault((s, p), []).append(d)
    # one edge per pair, from whoever wrote more, with the counts both ways
    for a, b in {tuple(sorted(k)) for k in pairs}:
        ab, ba = pairs.get((a, b), []), pairs.get((b, a), [])
        if len(ba) > len(ab):
            a, b, ab, ba = b, a, ba, ab
        docs = list(dict.fromkeys(ab + ba))
        g.edge(a, b, "emailed", count=len(docs), sent=len(ab), replied=len(ba), docs=docs[:50])
    return g


def regrain(m: Matter, g: Graph, grain: str, ann: Annotations | None = None) -> Graph:
    """``g`` with its documents gathered into metadocuments at ``grain``
    (docs/METADOCS.md): each thread, family, item however many copies, text
    however many drafts, or bundle becomes one node (``unit:<ref>``), so a
    diagram shows "the thread" with the facts resting on it rather than
    eleven emails. Edges between documents of one unit go; parallel edges
    become one, with ``merged`` (how many) and ``locs`` (each one's
    evidence). At ``bundle``, a document in no bundle stays itself, and one
    in several goes with the first by name."""
    from . import metadocs
    if grain == "doc":
        return g
    u = metadocs.Units(m, ann)
    to = {}
    for nid, n in g.nodes.items():
        if n.get("type") != "doc":
            continue
        if grain == "bundle":
            bs = sorted(u.ann.bundles_of(n["doc"]), key=lambda b: b["name"].casefold())
            if bs:
                to[nid] = "unit:bundle:" + bs[0]["bundle"]
        elif metadocs.is_grain(grain):
            to[nid] = "unit:" + u.unit(n["doc"], grain)
        else:
            raise ValueError(f"no grain {grain!r}")
    out = Graph()
    for nid, n in g.nodes.items():
        if nid not in to:
            out.nodes[nid] = dict(n)
            continue
        uid = to[nid]
        if uid not in out.nodes:
            ref = uid.removeprefix("unit:")
            d = u.describe(ref)
            rep = n["doc"] if grain == "bundle" else ref.partition(":")[2]
            label = d["name"] + (f" ({d['documents']})" if d["documents"] > 1 else "")
            out.node(uid, type="doc", label=label, doc=rep, loc=f"doc:{rep}", unit=ref, grain=grain,
                     documents=d["documents"], date=(d.get("first") or "")[:10] or None, members=[])
        out.nodes[uid]["members"].append(nid)
    seen = {}
    for e in g.edges:
        a, b = to.get(e["from"], e["from"]), to.get(e["to"], e["to"])
        if a == b and (e["from"] in to or e["to"] in to):
            continue                        # inside one unit
        key = (a, b, e["kind"], e.get("link"), e.get("rel"))
        if key in seen:
            x = seen[key]
            x["merged"] = x.get("merged", 1) + 1
            if e.get("loc"):
                x.setdefault("locs", [x["loc"]] if x.get("loc") else []).append(e["loc"])
            if e.get("docs"):
                x["docs"] = list(dict.fromkeys([*x.get("docs", []), *e["docs"]]))[:50]
            if isinstance(e.get("count"), int) and isinstance(x.get("count"), int):
                x["count"] += e["count"]
            continue
        x = {**e, "from": a, "to": b}
        seen[key] = x
        out.edges.append(x)
    for i, e in enumerate(out.edges):
        e["id"] = f"x{i}"
    return out
