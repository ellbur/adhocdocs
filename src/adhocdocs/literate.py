"""Markdown that quotes documents by reference (Principle 5).

Used by literate pages and by Claude's notes. Quotations are directives
naming entries in a fenced ``refs`` block at the end of the text:

=====================  ==================================================
``:q{#id}``            inline quotation plus citation
``::q{#id}``           (own line) block quotation plus citation
``:cite{#id}``         citation only
``[text](ref:id)``     link that opens the viewer at the reference
=====================  ==================================================

Options: ``cite=none|footnote``, ``layout=lines``, ``signal="see"``.

The ``refs`` block holds one JSON object per line: a locator plus an ``id``
(lowercase letters, digits, hyphens). A quoted reference needs ``layer``
and ``quote``; an elided one has ``parts`` (each a ``select`` and
``quote``, same layer, in order, not overlapping). ``alter`` lists
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
DIRECTIVE = re.compile(r"(?P<colons>:{1,2})(?P<name>q|cite)\{#(?P<id>[a-z0-9-]+)(?P<opts>[^}]*)\}")
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
        kind = "cite" if m.group("name") == "cite" else ("block" if block else "q")
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

    @property
    def quoted(self) -> bool:
        return bool(self.parts)

    def result(self) -> dict:
        r = {"id": self.id, "status": self.status}
        if self.error:
            r["error"] = self.error
        if self.new:
            r["new"] = self.new
        if self.low_confidence:
            r["low_confidence"] = True
        return r


def _parts_of(ref: dict) -> list[dict]:
    if "parts" in ref:
        return [{"doc": ref["doc"], "layer": ref.get("layer"), **p} for p in ref["parts"]]
    if "char" in (ref.get("select") or {}) or "quote" in ref:
        return [ref]
    return []


def resolve(m, ref: dict) -> Resolved:
    rid = ref.get("id", "?")
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
    res.low_confidence = _low_confidence(m, layer, parts)
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


def _low_confidence(m, layer, parts) -> bool:
    obj = m.objects.get_object(layer.id)
    if not obj.get("geometry"):
        return False
    for g in m.objects.get_jsonl(obj["geometry"]):
        if "conf" in g and g["conf"] < LOW_CONFIDENCE and any(g["start"] < p.end and p.start < g["end"] for p in parts):
            return True
    return False


# -- quotation pieces ------------------------------------------------------------------------

def normalize(s: str, layout: str, inline: bool) -> str:
    """The display rules, and nothing else."""
    if layout != "lines":
        s = re.sub(r"\s+", " ", s)
    if inline:
        s = s.replace("“", "‘").replace("”", "’").replace('"', "'")
    return s


def pieces(r: Resolved, layout="flow", inline=True) -> list[dict]:
    """The quotation as pieces: ``{"text", "start", "end", "em"}`` for runs
    of document text (with their layer range), ``{"mark"}`` for what the
    renderer adds ([T], [sic], …). Reading the text pieces back, with the
    marks removed and joins undone, gives the source."""
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
            _runs(out, r.text, pos, s, emph, layout, inline)
            if mark:
                out.append({"mark": mark})
            pos = max(pos, e)
        _runs(out, r.text, pos, p.end, emph, layout, inline)
    if trail:
        out.append({"mark": " " + trail})
    return out


def _runs(out, text, s, e, emph, layout, inline):
    """Text pieces for [s, e), split where emphasis starts or stops."""
    cuts = sorted({s, e, *[x for a, b in emph for x in (a, b) if s < x < e]})
    for a, b in zip(cuts, cuts[1:]):
        if b > a:
            em = any(x <= a and b <= y for x, y in emph)
            out.append({"text": normalize(text[a:b], layout, inline), "start": a, "end": b, "em": em})


def plain(ps: list[dict]) -> str:
    return "".join(p.get("text", p.get("mark", "")) for p in ps)


# -- checking ----------------------------------------------------------------------------------

def check(m, text: str) -> list[dict]:
    """One dict per reference (``id``, ``status``) and per lint (``lint``)."""
    prose, refs, out, _ = split(text)
    used = {d.id: d for d in directives(prose)}
    ids = [r["id"] for r in refs]
    for i in sorted({i for i in ids if ids.count(i) > 1}):
        out.append({"lint": "duplicate-ref", "id": i})
    for ref in refs:
        r = resolve(m, ref)
        res = r.result()
        d = used.get(ref["id"])
        if d and d.kind in ("q", "block") and not r.quoted and r.status != "broken":
            res.update(status="broken", error="quoted, but the reference has no quoted text (layer, select.char, quote)")
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


def problems(results: list[dict]) -> list[dict]:
    """The results that need attention."""
    return [r for r in results if "lint" in r or r.get("status") != "exact" or r.get("low_confidence")]
