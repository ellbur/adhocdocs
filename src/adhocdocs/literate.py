"""Markdown that quotes documents by reference (Principle 5).

Used by literate pages and by Claude's notes. Quotations are directives
naming entries in a fenced ``refs`` block at the end of the text:

=====================  ==================================================
``:q{#id}``            inline quotation plus citation
``::q{#id}``           (own line) block quotation plus citation
``:cite{#id}``         citation only
``[text](ref:id)``     link that opens the viewer at the reference
``:interp{#id}``       an interpretation's value, inline, marked as one
``::interp{#id}``      (own line) an interpretation as a table, tree,
                       text or picture, captioned as Claude's reading
``:checked{#id}``      a search reference: what a claim was checked
                       against, one line that opens to every document
                       the search finds (``page ref search``)
``:listed{#id}``       a list reference: a list file, or one point of a
                       key set, as one line that opens to its documents
                       and their whys (``page ref list``)
=====================  ==================================================

Options: ``cite=none|footnote``, ``layout=lines``, ``signal="see"``, and
``as=`` for how the quotation is shown:

``as=text``   the words (the default for text)
``as=rich``   the words with their formatting from the original: Word run
              properties, HTML tags (emails, web pages), or PDF fonts
              (approximate). The words are checked as for text.
``as=image``  (block only) a picture of the page: the lines a text quotation
              is on, or a region reference (the default for regions).
              Documents without pages (emails, Word files) are pictured as
              their text set as pages, captioned as such
``as=table``  (block only) spreadsheet cells as a table (the default for a
              cells reference in a block)
``as=hex``    a byte reference's bytes, as a hex dump in a block or as hex
              digits inline (the default, and only, way to quote bytes)

The ``refs`` block holds one JSON object per line: a locator plus an ``id``
(lowercase letters, digits, hyphens). A quoted reference needs ``layer``
and ``quote``; an elided one has ``parts`` (each a ``select`` and
``quote``, same layer, in order, not overlapping). A cells reference has
``layer``, ``select`` (``sheet``, ``cells``) and ``quote`` (the cells'
text); a region reference has ``select`` (``page``, ``xywh``); a byte
reference has ``select`` (``bytes``) and ``quote`` (``sha256``, ``hex``).
An interpretation reference has ``interp`` (its name), ``output`` (the
output's sha256, so a changed interpretation breaks the reference) and
optionally ``ptr`` (a JSON pointer into the output); it can only be shown
with ``:interp``/``::interp`` (``as=table|tree|value|text|image``), never
quoted. A metadocument reference (docs/METADOCS.md) has ``meta``
(``thread:<doc>``, ``family:<doc>``, ``bundle:<id>``…) and ``docs``, the
documents it held when cited; it can be cited (``:cite``) or linked, never
quoted, and when its documents change (a reply arrives, a bundle is
edited) it resolves re-anchored, with the new list, so the author looks
before ``page refs update`` takes it. ``alter`` lists
alterations, positioned by layer offsets inside the quoted parts:

``{"replace": [a, b], "with": "T"}``  ``{"insert": p, "text": "…"}``
``{"sic": p}``  ``{"emphasis": [a, b]}``  ``{"join": p}`` (drop a line-end
hyphen at p and the line break after it)  ``{"lead": "…"}``  ``{"trail": "…"}``
"""

import json
import re
from dataclasses import dataclass, field

from . import locators

REFS_BLOCK = re.compile(r"^```refs[ \t]*\n(.*?)^```[ \t]*\n?", re.S | re.M)
DIRECTIVE = re.compile(r"(?P<colons>:{1,2})(?P<name>q|cite|interp|checked|listed)\{#(?P<id>[a-z0-9-]+)(?P<opts>[^}]*)\}")
REF_LINK = re.compile(r"\]\(ref:(?P<id>[a-z0-9-]+)\)")
OPTION = re.compile(r'([a-z]+)=(?:"([^"]*)"|(\S+))')
QUOTED = re.compile(r"“[^”]*”|\"[^\"\n]*\"")
ID = re.compile(r"^[a-z0-9-]+$")
UNBACKED_WORDS = 5
LOW_CONFIDENCE = 0.6


@dataclass
class Directive:
    kind: str           # q, block, cite or link
    id: str
    opts: dict
    start: int          # span in the prose
    end: int


def directives(prose: str) -> list[Directive]:
    out = []
    for m in DIRECTIVE.finditer(prose):
        block = m.group("colons") == "::"
        line_start = prose.rfind("\n", 0, m.start()) + 1
        line_end = prose.find("\n", m.end())
        alone = prose[line_start:m.start()].strip() == "" and prose[m.end():line_end if line_end >= 0 else None].strip() == ""
        if block and not alone:
            block = False
        opts = {k: a if a else b for k, a, b in OPTION.findall(m.group("opts"))}
        name = m.group("name")
        kind = {"cite": "cite", "checked": "checked", "listed": "listed", "interp": "interp-block" if block else "interp"}.get(name) or (
            "block" if block else "q")
        out.append(Directive(kind, m.group("id"), opts, m.start(), m.end()))
    for m in REF_LINK.finditer(prose):
        out.append(Directive("link", m.group("id"), {}, m.start(), m.end()))
    return sorted(out, key=lambda d: d.start)


def split(text: str) -> tuple[str, list[dict], list[dict], int]:
    """(prose, refs, problems, line where the refs block starts)."""
    refs, problems = [], []
    m = REFS_BLOCK.search(text)
    if not m:
        return text, refs, problems, 0
    first = text.count("\n", 0, m.start()) + 2
    for n, line in enumerate(m.group(1).splitlines()):
        if not line.strip():
            continue
        try:
            ref = json.loads(line)
            if not isinstance(ref, dict) or "id" not in ref:
                raise ValueError("needs an id")
            if not ID.match(ref["id"]):
                raise ValueError(f"bad id {ref['id']!r}: use lowercase letters, digits and hyphens")
            ref["_line"] = first + n
            refs.append(ref)
        except ValueError as e:
            problems.append({"lint": "bad-ref", "line": first + n, "error": str(e)})
    return text[:m.start()] + text[m.end():], refs, problems, first


def format_refs(refs: list[dict]) -> str:
    lines = [json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, ensure_ascii=False) for r in refs]
    return "```refs\n" + "".join(l + "\n" for l in lines) + "```\n"


def replace_refs(text: str, refs: list[dict]) -> str:
    """The text with its refs block rewritten (or added at the end)."""
    block = format_refs(refs)
    m = REFS_BLOCK.search(text)
    if m:
        return text[:m.start()] + block + text[m.end():]
    return text.rstrip("\n") + "\n\n" + block


# -- resolving references ---------------------------------------------------------------

@dataclass
class Part:
    start: int
    end: int


@dataclass
class Resolved:
    id: str
    status: str                     # exact, re-anchored, broken
    doc: str | None = None
    layer: object = None            # LayerInfo
    text: str | None = None         # the whole layer text
    parts: list[Part] = field(default_factory=list)
    alter: list = field(default_factory=list)
    error: str | None = None
    new: dict | None = None         # the rewritten ref, when re-anchored
    low_confidence: bool = False
    low_confidence_pages: list = field(default_factory=list)    # the pages its low-confidence words are on
    cells: list | None = None       # a cells reference: rows of cell text
    select: dict | None = None      # a cells or region reference's selector
    region: dict | None = None      # a region reference: page and xywh
    span: list | None = None        # a cells reference: the char range it covers
    data: bytes | None = None       # a byte reference: the bytes
    interp: dict | None = None      # an interpretation reference: its record
    ptr: str | None = None          # … and the pointer into its output
    meta: dict | None = None        # a metadocument reference: what it names (metadocs.describe) and its docs
    search: dict | None = None      # a search reference: the search's header and its documents, run now
    listing: dict | None = None     # a list reference: the list file's header and items (a point's, if it names one)
    unjudged: int = 0               # a search reference: passages proposed as against the claim that no one judged

    @property
    def quoted(self) -> bool:
        return bool(self.parts)

    @property
    def kind(self) -> str:
        if self.meta is not None:
            return "meta"
        if self.search is not None:
            return "search"
        if self.listing is not None:
            return "list"
        if self.interp is not None:
            return "interp"
        if self.data is not None:
            return "bytes"
        return "text" if self.parts else "cells" if self.cells is not None else "region" if self.region else "document"

    def result(self) -> dict:
        r = {"id": self.id, "status": self.status}
        if self.error:
            r["error"] = self.error
        if self.new:
            r["new"] = self.new
        if self.low_confidence:
            r["low_confidence"] = True
        if self.unjudged:
            r["unjudged"] = self.unjudged
        return r


def _parts_of(ref: dict) -> list[dict]:
    if "parts" in ref:
        return [{"doc": ref["doc"], "layer": ref.get("layer"), **p} for p in ref["parts"]]
    if "char" in (ref.get("select") or {}) or "quote" in ref:
        return [ref]
    return []


def _resolve_selection(m, ref: dict) -> Resolved:
    """A cells or region reference: no parts, its selector names what is quoted."""
    rid, sel = ref.get("id", "?"), ref["select"]
    try:
        r = locators.resolve(m, {k: ref[k] for k in ("doc", "layer", "select", "quote") if k in ref})
    except (locators.LocatorError, KeyError, ValueError) as e:
        return Resolved(rid, "broken", error=str(e))
    if r["status"] == "broken":
        return Resolved(rid, "broken", error=r.get("error"))
    doc = r["loc"]["doc"]
    if "cells" in sel:
        res = Resolved(rid, r["status"], doc=doc, layer=m.state.layer(r["loc"]["layer"]), select=r["loc"]["select"],
                       cells=[row.split("\t") for row in r["text"].split("\n")])
        res.span = r.get("span")
        if r["status"] == "re-anchored":
            res.new = {**{k: v for k, v in ref.items() if not k.startswith("_")}, "doc": doc, "layer": r["loc"]["layer"],
                       "quote": r["loc"]["quote"]}
        return res
    res = Resolved(rid, r["status"], doc=doc, select=sel, region={"page": sel["page"], "xywh": sel["xywh"]})
    if r["status"] == "re-anchored":
        res.new = {**{k: v for k, v in ref.items() if not k.startswith("_")}, "doc": doc}
    return res


def _resolve_interp(m, ref: dict) -> Resolved:
    """An interpretation reference: the interpretation must exist, still
    hold (inputs live, files unchanged) and have the output it was given."""
    from . import interp
    rid = ref.get("id", "?")
    try:
        rec = interp.load(m, ref["interp"])
    except (interp.InterpError, KeyError) as e:
        return Resolved(rid, "broken", error=str(e))
    if ref.get("output") and ref["output"] != rec["output"]["sha256"]:
        return Resolved(rid, "broken", error=f"interpretation {rec['name']} was made again since this reference; "
                                              "check it still says this, then page ref add --interp --replace")
    held = interp.check(m, rec["name"], rerun=False)
    if not held["ok"]:
        return Resolved(rid, "broken", error="; ".join(held["problems"]))
    try:
        interp.value_of(m, rec, ref.get("ptr"))
    except (interp.InterpError, ValueError) as e:
        return Resolved(rid, "broken", error=str(e))
    return Resolved(rid, "exact", doc=rec["inputs"][0]["doc"], interp=rec, ptr=ref.get("ptr"))


def _resolve_bytes(m, ref: dict) -> Resolved:
    rid = ref.get("id", "?")
    try:
        r = locators.resolve(m, {k: ref[k] for k in ("doc", "select", "quote") if k in ref})
    except (locators.LocatorError, KeyError, ValueError) as e:
        return Resolved(rid, "broken", error=str(e))
    if r["status"] == "broken":
        return Resolved(rid, "broken", error=r.get("error"))
    res = Resolved(rid, r["status"], doc=r["loc"]["doc"], select=r["loc"]["select"], data=r.data)
    if r["status"] == "re-anchored":
        res.new = {**{k: v for k, v in ref.items() if not k.startswith("_")}, "doc": r["loc"]["doc"]}
    return res


def _resolve_meta(m, ref: dict) -> Resolved:
    """A metadocument reference: it must still name something, and if its
    documents changed it is re-anchored with the new list."""
    from . import metadocs
    rid = ref.get("id", "?")
    try:
        u = metadocs.Units(m)
        d = u.describe(ref["meta"])
        docs = u.docs(d["ref"])
    except (metadocs.MetaError, locators.LocatorError, KeyError, ValueError) as e:
        return Resolved(rid, "broken", error=str(e))
    if not docs:
        return Resolved(rid, "broken", error=f"{ref['meta']} names no documents now")
    res = Resolved(rid, "exact", doc=docs[0], meta={**d, "docs": docs})
    was = [locators.parse("doc:" + x.removeprefix("doc:"), m.objects)["doc"] for x in ref.get("docs") or []]
    if was and set(map(m.state.follow, was)) != set(docs):
        gone, added = len(set(was) - set(docs)), len(set(docs) - set(was))
        res.status = "re-anchored"
        res.error = (f"{d['ref']} has changed since it was cited: " + ", ".join(
            x for x in ([f"{added} more document{'s' * (added > 1)}"] if added else [])
            + ([f"{gone} gone"] if gone else [])) + "; look, then page refs update")
        res.new = {**{k: v for k, v in ref.items() if not k.startswith("_")}, "meta": d["ref"], "docs": docs}
    return res


def run_search(m, ref: dict) -> tuple[dict, list[dict]]:
    """A search reference's search, run now: (header, one item per document)."""
    from .search import Options, Searcher
    s = Searcher(m, Options(layers=ref.get("layers") or "primary", where=ref.get("where")))
    index = ref.get("index") or "keyword"
    if index != "keyword":
        raise ValueError(f"index {index}: a search reference re-runs its search, so it must be exhaustive (keyword)")
    return s.finish(*s.keyword(ref["search"]))


def _place(m, loc):
    p = locators.parse(loc, m.objects)
    return p["doc"], (p.get("select") or {}).get("char")


def _overlaps(a, b) -> bool:
    return a[0] == b[0] and (a[1] is None or b[1] is None or (a[1][0] < b[1][1] and b[1][0] < a[1][1]))


def judgments(m, ref: dict) -> list[dict]:
    """A search reference's proposed passages (statements that tools found
    going against the claim), each with how it was judged: "against", "not",
    or None (not yet). A judgment is an --against or --not-against at the
    same place (overlapping, in the same document)."""
    marks = [("against", x) for x in ref.get("against") or []] + [("not", x) for x in ref.get("not_against") or []]
    placed = []
    for kind, x in marks:
        try:
            placed.append((kind, x, _place(m, x["loc"])))
        except (locators.LocatorError, KeyError, ValueError):
            continue
    out = []
    for p in ref.get("proposed") or []:
        try:
            here = _place(m, p["loc"])
        except (locators.LocatorError, KeyError, ValueError):
            continue
        j = next(((kind, x) for kind, x, pl in placed if _overlaps(here, pl)), None)
        out.append({**p, "judged": j[0] if j else None, "why": (j[1].get("why") if j else None)})
    return out


def _resolve_search(m, ref: dict) -> Resolved:
    """A search reference: the search behind a claim, kept so the reader can
    re-run it, see every document it found, and see which ones the author
    judged to go against the claim. Re-anchored when it finds other
    documents now than when it was recorded."""
    rid = ref.get("id", "?")
    try:
        header, items = run_search(m, ref)
    except (locators.LocatorError, KeyError, ValueError) as e:
        return Resolved(rid, "broken", error=str(e))
    docs = [locators.parse(it["loc"], m.objects)["doc"] for it in items]
    res = Resolved(rid, "exact", doc=docs[0] if docs else None, search={"header": header, "items": items, "docs": docs})
    was = [locators.parse("doc:" + x.removeprefix("doc:"), m.objects)["doc"] for x in ref.get("docs") or []]
    if "docs" in ref and set(map(m.state.follow, was)) != set(docs):
        gone, added = len(set(was) - set(docs)), len(set(docs) - set(was))
        res.status = "re-anchored"
        res.error = (f"the search finds other documents now: " + ", ".join(
            x for x in ([f"{added} more"] if added else []) + ([f"{gone} no longer"] if gone else []))
            + "; read the new ones against the claim, then page refs update")
        res.new = {**{k: v for k, v in ref.items() if not k.startswith("_")}, "docs": docs}
    res.unjudged = sum(1 for j in judgments(m, ref) if not j["judged"])
    for a in [*(ref.get("against") or []), *(ref.get("not_against") or [])]:
        try:
            if locators.resolve(m, locators.parse(a["loc"], m.objects))["status"] == "broken":
                res.status, res.error = "broken", f"an 'against' locator no longer resolves: {a['loc'][:80]}"
        except (locators.LocatorError, KeyError, ValueError) as e:
            res.status, res.error = "broken", f"an 'against' entry is not a locator: {e}"
    return res


def list_path(m, ref: dict):
    from pathlib import Path
    p = Path(ref["list"])
    return p if p.is_absolute() else m.workspace / p


def _resolve_list(m, ref: dict) -> Resolved:
    """A list reference: a list file (a key set, a chronology), or one point
    of a key set: the documents behind a claim, beyond the one or two cited."""
    from . import lists
    rid = ref.get("id", "?")
    path = list_path(m, ref)
    try:
        header, items = lists.read(path)
    except (OSError, ValueError) as e:
        return Resolved(rid, "broken", error=f"list {ref['list']}: {e}")
    point = ref.get("point")
    if point:
        if point not in [p["id"] for p in header.get("points", [])]:
            return Resolved(rid, "broken", error=f"list {ref['list']} has no point {point!r}")
        items = [it for it in items if point in ((it.get("fields") or {}).get("points") or [])]
    docs = [locators.parse(it["loc"], m.objects)["doc"] for it in items]
    return Resolved(rid, "exact", doc=docs[0] if docs else None,
                    listing={"header": header, "items": items, "docs": docs, "point": point})


def resolve(m, ref: dict) -> Resolved:
    rid = ref.get("id", "?")
    sel = ref.get("select") or {}
    if "list" in ref:
        return _resolve_list(m, ref)
    if "search" in ref:
        return _resolve_search(m, ref)
    if "meta" in ref:
        return _resolve_meta(m, ref)
    if "interp" in ref:
        return _resolve_interp(m, ref)
    if "bytes" in sel:
        return _resolve_bytes(m, ref)
    if "parts" not in ref and ("cells" in sel or "xywh" in sel):
        return _resolve_selection(m, ref)
    try:
        base = locators.resolve(m, {k: ref[k] for k in ("doc", "layer") if k in ref} | (
            {"select": ref["select"]} if "select" in ref and "parts" not in ref and "quote" not in ref else {}))
    except (locators.LocatorError, KeyError, ValueError) as e:
        return Resolved(rid, "broken", error=str(e))
    if base["status"] == "broken":
        return Resolved(rid, "broken", error=base.get("error"))
    doc = base["loc"]["doc"]
    specs = _parts_of(ref)
    if not specs:
        return Resolved(rid, base["status"], doc=doc)
    status = base["status"]
    parts, new_parts, layer_id = [], [], None
    for p in specs:
        loc = {k: p[k] for k in ("doc", "layer", "select", "quote") if k in p}
        if not loc.get("layer") or not loc.get("quote") or "char" not in (loc.get("select") or {}):
            return Resolved(rid, "broken", doc=doc, error="a quoted reference needs layer, select.char and quote")
        r = locators.resolve(m, loc)
        if r["status"] == "broken":
            return Resolved(rid, "broken", doc=doc, error=r.get("error"))
        if r["status"] == "re-anchored":
            status = "re-anchored"
        if layer_id and r["loc"]["layer"] != layer_id:
            return Resolved(rid, "broken", doc=doc, error="parts resolve to different layers")
        layer_id = r["loc"]["layer"]
        s, e = r["loc"]["select"]["char"]
        parts.append(Part(s, e))
        new_parts.append({"select": {"char": [s, e]}, "quote": r["loc"].get("quote") or loc["quote"]})
    if any(b.start < a.end for a, b in zip(parts, parts[1:])):
        return Resolved(rid, "broken", doc=doc, error="parts overlap or are out of order")
    state = m.state
    layer = state.layer(layer_id)
    text = m.objects.get_text(layer.text)

    # Alterations move with their part when it is re-anchored.
    alter = []
    for a in ref.get("alter", []):
        a = dict(a)
        for key in ("replace", "insert", "sic", "emphasis", "join"):
            if key in a:
                pos = a[key]
                old = [(p["select"]["char"] if "select" in p else None) for p in specs]
                shift = 0
                for o, np in zip(old, parts):
                    lo = pos[0] if isinstance(pos, list) else pos
                    if o and o[0] <= lo <= o[1]:
                        shift = np.start - o[0]
                        break
                a[key] = [x + shift for x in pos] if isinstance(pos, list) else pos + shift
        alter.append(a)
    res = Resolved(rid, status, doc=doc, layer=layer, text=text, parts=parts, alter=alter)
    err = check_alterations(res)
    if err:
        res.status, res.error = "broken", err
        return res
    res.low_confidence_pages = _low_confidence(m, layer, parts)
    res.low_confidence = bool(res.low_confidence_pages)
    if status == "re-anchored":
        new = {k: v for k, v in ref.items() if not k.startswith("_")}
        new["doc"], new["layer"] = doc, layer_id
        if "parts" in ref:
            new["parts"] = new_parts
        else:
            new.update(new_parts[0])
        if alter:
            new["alter"] = alter
        res.new = new
    return res


def check_alterations(r: Resolved) -> str | None:
    def inside(a, b=None):
        b = a if b is None else b
        return any(p.start <= a and b <= p.end for p in r.parts)
    for a in r.alter:
        if "replace" in a or "emphasis" in a:
            lo, hi = a.get("replace") or a.get("emphasis")
            if not (lo < hi and inside(lo, hi)):
                return f"alteration {a} is outside the quotation"
        elif "insert" in a or "sic" in a:
            if not inside(a.get("insert", a.get("sic"))):
                return f"alteration {a} is outside the quotation"
        elif "join" in a:
            p = a["join"]
            if not inside(p) or r.text[p:p + 1] not in "-­‐" or "\n" not in r.text[p + 1:p + 3]:
                return f"join at {p} is not a line-end hyphen"
        elif not ("lead" in a or "trail" in a):
            return f"unknown alteration {a}"
    return None


def _low_confidence(m, layer, parts) -> list[int]:
    """The pages the quotation's low-confidence OCR words are on (none: it has none)."""
    obj = m.objects.get_object(layer.id)
    if not obj.get("geometry"):
        return []
    pages = set()
    for g in m.objects.get_jsonl(obj["geometry"]):
        if "conf" in g and g["conf"] < LOW_CONFIDENCE and any(g["start"] < p.end and p.start < g["end"] for p in parts):
            pages.add(g.get("page"))
    return sorted(x for x in pages if x is not None) or ([1] if pages else [])


# -- quotation pieces ------------------------------------------------------------------------

def normalize(s: str, layout: str, inline: bool) -> str:
    """The display rules, and nothing else."""
    if layout != "lines":
        s = re.sub(r"\s+", " ", s)
    if inline:
        s = s.replace("“", "‘").replace("”", "’").replace('"', "'")
    return s


def pieces(r: Resolved, layout="flow", inline=True, styles=()) -> list[dict]:
    """The quotation as pieces: ``{"text", "start", "end", "em", "style"}``
    for runs of document text (with their layer range, and formatting from
    ``styles`` runs), ``{"mark"}`` for what the renderer adds ([T], [sic],
    …). Reading the text pieces back, with the marks removed and joins
    undone, gives the source."""
    out = []
    lead = next((a["lead"] for a in r.alter if "lead" in a), None)
    trail = next((a["trail"] for a in r.alter if "trail" in a), None)
    if lead:
        out.append({"mark": lead + " "})
    emph = [a["emphasis"] for a in r.alter if "emphasis" in a]
    for i, p in enumerate(r.parts):
        if i:
            out.append({"mark": " … "})
        events = []
        for a in r.alter:
            if "replace" in a and p.start <= a["replace"][0] < p.end:
                events.append((a["replace"][0], a["replace"][1], f"[{a['with']}]"))
            elif "insert" in a and p.start <= a["insert"] <= p.end:
                events.append((a["insert"], a["insert"], f"[{a['text']}]"))
            elif "sic" in a and p.start <= a["sic"] <= p.end:
                events.append((a["sic"], a["sic"], " [sic]"))
            elif "join" in a and p.start <= a["join"] < p.end:
                j = a["join"] + 1
                while j < p.end and r.text[j] in " \t\n":
                    j += 1
                events.append((a["join"], j, ""))
        pos = p.start
        for s, e, mark in sorted(events):
            _runs(out, r.text, pos, s, emph, layout, inline, styles)
            if mark:
                out.append({"mark": mark})
            pos = max(pos, e)
        _runs(out, r.text, pos, p.end, emph, layout, inline, styles)
    if trail:
        out.append({"mark": " " + trail})
    return out


def _runs(out, text, s, e, emph, layout, inline, styles=()):
    """Text pieces for [s, e), split where emphasis or formatting starts or stops."""
    near = [st for st in styles if st[0] < e and s < st[1]]
    cuts = sorted({s, e, *[x for a, b in emph for x in (a, b) if s < x < e],
                   *[x for a, b, _ in near for x in (a, b) if s < x < e]})
    for a, b in zip(cuts, cuts[1:]):
        if b > a:
            em = any(x <= a and b <= y for x, y in emph)
            style = frozenset().union(*[st for x, y, st in near if x <= a and b <= y])
            out.append({"text": normalize(text[a:b], layout, inline), "start": a, "end": b, "em": em, "style": style})


def plain(ps: list[dict]) -> str:
    return "".join(p.get("text", p.get("mark", "")) for p in ps)


# -- checking ----------------------------------------------------------------------------------

def check(m, text: str) -> list[dict]:
    """One dict per reference (``id``, ``status``) and per lint (``lint``)."""
    prose, refs, out, _ = split(text)
    uses = {}
    for d in directives(prose):
        uses.setdefault(d.id, []).append(d)
    used = uses
    ids = [r["id"] for r in refs]
    for i in sorted({i for i in ids if ids.count(i) > 1}):
        out.append({"lint": "duplicate-ref", "id": i})
    for ref in refs:
        r = resolve(m, ref)
        res = r.result()
        if r.status != "broken":
            # every use of the reference must be one it can be shown as
            err = next((e for d in used.get(ref["id"], []) if d.kind in ("q", "block", "interp", "interp-block", "checked", "listed")
                        for e in [shown_as(m, d, r)[1]] if e), None)
            if err:
                res.update(status="broken", error=err)
        out.append(res)
    for i in sorted(set(used) - set(ids)):
        out.append({"lint": "missing-ref", "id": i})
    for i in sorted(set(ids) - set(used)):
        out.append({"lint": "unused-ref", "id": i})
    stripped = DIRECTIVE.sub("", REF_LINK.sub("]()", prose))
    in_code = False
    for n, line in enumerate(stripped.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if in_code:
            continue
        if line.lstrip().startswith(">"):
            out.append({"lint": "blockquote", "line": n, "text": line.strip()[:80]})
        for q in QUOTED.findall(re.sub(r"`[^`]*`", "", line)):
            if len(q.split()) >= UNBACKED_WORDS:
                out.append({"lint": "unbacked-quote", "line": n, "text": q[:80]})
    return out


def shown_as(m, d: Directive, r: Resolved) -> tuple[str, str | None]:
    """How a quotation or interpretation directive shows its reference
    (``as=``), or why it can't."""
    if d.kind == "checked":
        return "", None if r.kind == "search" else "not a search: :checked shows a search reference (page ref search)"
    if d.kind == "listed":
        return "", None if r.kind == "list" else "not a list: :listed shows a list reference (page ref list)"
    if r.kind == "list":
        return "", "a list: show its documents with :listed, or cite it"
    if r.kind == "search":
        return d.opts.get("as") or "", ("a search: show what it found with :checked, or cite it; "
                                        "to quote, quote a passage it found")
    if d.kind in ("interp", "interp-block"):
        if r.kind != "interp":
            return d.opts.get("as") or "", "not an interpretation: quote the document with :q, or make an interpretation (interp run)"
        from . import interp
        return interp.shown_as(r.interp, interp.value_of(m, r.interp, r.ptr), d.opts.get("as"), d.kind == "interp-block")
    if r.kind == "meta":
        return d.opts.get("as") or "", (f"{r.meta['ref']} is a metadocument: cite it (:cite) or link to it; "
                                        "to quote, quote a passage in one of its documents")
    if r.kind == "interp":
        return d.opts.get("as") or "", ("an interpretation is Claude's reading, not the document's content, so it "
                                        "can't be quoted; show it with :interp or ::interp")
    block = d.kind == "block"
    if r.kind == "bytes":
        want = d.opts.get("as") or "hex"
        return want, None if want == "hex" else f"as={want}: bytes are quoted as hex (as=hex)"
    want = d.opts.get("as") or {"region": "image", "cells": "table" if block else "text"}.get(r.kind, "text")
    if r.kind == "document":
        return want, "quoted, but the reference has no quoted text (layer, select.char, quote), cells or region"
    if want not in ("text", "rich", "image", "table"):
        return want, f"as={want}: use text, rich, image or table" + (" (hex is for byte references)" if want == "hex" else "")
    if want in ("image", "table") and not block:
        return want, f"as={want} is for block quotations (::q)"
    if r.kind == "region" and want != "image":
        return want, "a page region can only be quoted as an image"
    if r.kind == "region":
        from .quoting import paginated
        if not paginated(m, r.doc):
            return want, "a page region needs a document with pages of its own (PDF or scan); quote its text as an image instead"
    if want == "table" and r.kind != "cells":
        return want, "as=table needs a cells reference (sheet=…&cells=…)"
    if want == "rich" and r.kind != "text":
        return want, "as=rich needs a text reference"
    if want == "image" and r.kind == "cells":
        return want, "spreadsheet cells can't be quoted as an image; use as=table"
    if want == "image" and r.kind == "text":
        from .produce import geometry
        from .quoting import paginated
        if paginated(m, r.doc) and not geometry(m, r.layer.id):
            return want, "as=image needs word positions, and this layer has none; try as=rich"
    return want, None


def problems(results: list[dict]) -> list[dict]:
    """The results that need attention. A low-confidence OCR quotation is
    one to check against the page image, said so (``check``)."""
    out = []
    for r in results:
        if "lint" in r or r.get("status") != "exact":
            out.append(r)
        elif r.get("low_confidence"):
            out.append({**r, "check": "low-confidence OCR: compare the quotation with the page image "
                                      "(its [check page N] marker opens it)"})
        elif r.get("unjudged"):
            out.append({**r, "check": f"{r['unjudged']} passage(s) proposed as against the claim, not judged: "
                                      "read each, then page ref judge FILE ID LOC against|not --why \"…\""})
    return out


def summary(results: list[dict]) -> dict:
    """Counts for a one-line report: what's wrong, and what to check."""
    wrong = [r for r in results if "lint" in r or r.get("status") != "exact"]
    ocr = [r for r in results if not ("lint" in r or r.get("status") != "exact") and r.get("low_confidence")]
    open_ = [r for r in results if r.get("unjudged")]
    return {"problems": len(wrong), **({"check_ocr": [r["id"] for r in ocr]} if ocr else {}),
            **({"unjudged": {r["id"]: r["unjudged"] for r in open_}} if open_ else {})}
