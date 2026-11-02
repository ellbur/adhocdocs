"""Locators: names for a document or a place inside one.

String form::

    doc:<doc-id>[@<layer-id>][#<selector>]

with selectors ``page=14``, ``page=14-16``, ``char=1200,1340``,
``page=3&xywh=0.1,0.2,0.5,0.05``, ``sheet=Q3&cells=B2:D10``,
``bytes=0,16``, ``ptr=/items/3/amount``, ``xpath=/orders/order[2]/sku``
and ``sheet=Q3&col=Amount&row=7``. Hash prefixes of 8 or more hex
digits are accepted on input and expanded.

``bytes`` is a half-open range of the original's bytes, not of any layer
(see adhocdocs.raw). Its quote fingerprint is ``{"sha256": …}``, plus
``"hex"`` for short ranges. It is how a file no extractor reads is quoted.

``ptr`` is an RFC 6901 JSON Pointer (percent-encoded in the string form)
into a JSON or JSON Lines document. It is an alias: it resolves to the
``char`` range of the value's text in the layer (see adhocdocs.structured).

``xpath`` names an element or attribute of an XML document, by a small
subset of XPath (percent-encoded in the string form); it is an alias for
the ``char`` range of its text, like ``ptr``.

``col`` names a spreadsheet column (CSV too) by the text of its header
cell, ``row`` a row (``7``) or rows (``7-9``) by the sheet's own row
numbers, and ``header`` the header's row if it isn't the first. They are
an alias for ``cells``: ``col=Amount&row=7`` is ``cells=D7`` when D1 says
"Amount", and ``col`` alone is the column below its header. A header that
appears twice is an error, not a guess at which.

``cells`` is a rectangle (``B2:D10``, or one cell ``B2``) on a sheet named by
``sheet`` (percent-encoded in the string form; the first sheet if left
out). Its text is the rectangle's cells, tab-separated, one row per line,
and its quote fingerprint is that text alone, with no prefix or suffix.

JSON form (durable; used in pages, notes, search results)::

    {"doc": ..., "layer": ..., "select": {"char": [s, e]},
     "quote": {"exact": ..., "prefix": ..., "suffix": ...}}

Offsets are Unicode code points into the layer text, half-open.
"""

import json
import re
from urllib.parse import quote as _pct, unquote as _unpct

from .objects import HASH_PREFIX

QUOTE_CONTEXT = 32


class LocatorError(ValueError):
    pass


def _parse_selector(s: str) -> dict:
    sel = {}
    for part in s.split("&"):
        if not part:
            continue
        key, _, val = part.partition("=")
        if key == "page":
            if "-" in val:
                a, b = val.split("-")
                sel["page"] = [int(a), int(b)]
            else:
                sel["page"] = int(val)
        elif key == "char":
            a, b = val.split(",")
            sel["char"] = [int(a), int(b)]
        elif key == "xywh":
            sel["xywh"] = [float(x) for x in val.split(",")]
        elif key == "bytes":
            a, b = val.split(",")
            sel["bytes"] = [int(a), int(b)]
        elif key in ("sheet", "ptr", "xpath", "col"):
            sel[key] = _unpct(val)
        elif key == "row":
            a, _, b = val.partition("-")
            sel["row"] = [int(a), int(b)] if b else int(a)
        elif key == "header":
            sel["header"] = int(val)
        elif key in ("cells", "label", "pl"):
            sel[key] = val
        else:
            raise LocatorError(f"unknown selector: {key}")
    return sel


def _format_selector(sel: dict) -> str:
    parts = []
    for key, val in sel.items():
        if isinstance(val, list):
            sep = "-" if key in ("page", "row") else ","
            parts.append(f"{key}={sep.join(str(v) for v in val)}")
        elif key == "sheet":
            parts.append(f"sheet={_pct(val, safe='')}")
        elif key == "ptr":
            parts.append(f"ptr={_pct(val, safe='/~')}")
        elif key == "xpath":
            parts.append(f"xpath={_pct(val, safe='/@*[]')}")
        elif key == "col":
            parts.append(f"col={_pct(val, safe='')}")
        else:
            parts.append(f"{key}={val}")
    return "&".join(parts)


def _hash(s: str) -> str:
    return s if s.startswith(HASH_PREFIX) else HASH_PREFIX + s


def parse(s, store=None) -> dict:
    """A locator from its string or JSON form (or a dict). With ``store``,
    hash prefixes are expanded to full hashes."""
    if isinstance(s, dict):
        loc = dict(s)
    elif s.lstrip().startswith("{"):
        loc = json.loads(s)
    else:
        if s.startswith("tr:"):          # a transcript alias: the same as doc:
            s = "doc:" + s[3:]
        if not s.startswith("doc:"):
            raise LocatorError(f"not a locator: {s}")
        rest = s[4:]
        rest, _, selector = rest.partition("#")
        doc, _, layer = rest.partition("@")
        loc = {"doc": _hash(doc)}
        if layer:
            loc["layer"] = _hash(layer)
        if selector:
            loc["select"] = _parse_selector(selector)
    if store is not None:
        for key in ("doc", "layer"):
            if key in loc and len(loc[key]) < len(HASH_PREFIX) + 64:
                matches = store.expand(loc[key])
                if len(matches) != 1:
                    raise LocatorError(f"{loc[key]} matches {len(matches)} objects")
                loc[key] = matches[0]
    return loc


def format(loc: dict) -> str:
    s = "doc:" + loc["doc"]
    if loc.get("layer"):
        s += "@" + loc["layer"]
    if loc.get("select"):
        s += "#" + _format_selector(loc["select"])
    return s


def make_quote(text: str, start: int, end: int, context: int = QUOTE_CONTEXT) -> dict:
    return {
        "exact": text[start:end],
        "prefix": text[max(0, start - context):start],
        "suffix": text[end:end + context],
    }


def char_locator(doc: str, layer: str, text: str, start: int, end: int) -> dict:
    """A durable range locator, with its quote fingerprint taken from the text."""
    return {"doc": doc, "layer": layer, "select": {"char": [start, end]}, "quote": make_quote(text, start, end)}


def page_range(text: str, page: int | list) -> tuple[int, int]:
    """Char range of a page (1-based) or page range, from form feeds."""
    first, last = (page, page) if isinstance(page, int) else page
    starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\f"]
    if not 1 <= first <= last <= len(starts):
        raise LocatorError(f"page {page} out of range (1-{len(starts)})")
    end = starts[last] - 1 if last < len(starts) else len(text)
    return starts[first - 1], end


def page_of(text: str, offset: int) -> int:
    return text.count("\f", 0, offset) + 1


def _col(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def parse_cells(spec: str) -> tuple[int, int, int, int]:
    """``B2:D10`` -> (first col, first row, last col, last row), 0-based, inclusive."""
    ends = []
    for ref in spec.upper().split(":"):
        m = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", ref)
        if not m:
            raise LocatorError(f"not a cell reference: {ref!r}")
        ends.append((_col(m[1]), int(m[2]) - 1))
    if len(ends) == 1:
        ends *= 2
    if len(ends) != 2:
        raise LocatorError(f"not a cell range: {spec!r}")
    (c0, r0), (c1, r1) = ends
    return min(c0, c1), min(r0, r1), max(c0, c1), max(r0, r1)


def sheet_range(text: str, segments: list[dict], sheet: str | None) -> tuple[int, int]:
    """Char range of a sheet, by name (``sheet`` segments), or the first."""
    if sheet is None:
        return page_range(text, 1)
    for seg in segments:
        if seg["kind"] == "sheet" and seg.get("name") == sheet:
            return seg["start"], seg["end"]
    raise LocatorError(f"no sheet named {sheet!r}")


def cells_text(text: str, start: int, end: int, rect) -> tuple[str, tuple[int, int] | None]:
    """The rectangle's text (cells by tabs, rows by newlines) in the sheet
    at ``text[start:end]``, and the char span from its first to its last
    non-empty cell (None if all are empty), for highlighting."""
    c0, r0, c1, r1 = rect
    rows, span, pos = [], None, start
    for r, line in enumerate(text[start:end].split("\n")):
        if r > r1:
            break
        if r >= r0:
            cells, cpos = [], pos
            for c, cell in enumerate(line.split("\t")):
                if c0 <= c <= c1:
                    cells.append(cell)
                    if cell:
                        span = (cpos if span is None else span[0], cpos + len(cell))
                cpos += len(cell) + 1
            rows.append("\t".join(cells + [""] * (c1 - c0 + 1 - len(cells))))
        pos += len(line) + 1
    rows += ["\t" * (c1 - c0)] * (r1 - r0 + 1 - len(rows))
    return "\n".join(rows), span


class Resolution(dict):
    """``status`` is exact, re-anchored or broken; ``loc`` is the locator as
    it now resolves, ``text`` the text it selects (when it selects text)."""


def _find_quote(text: str, quote: dict) -> list[int]:
    exact = quote["exact"]
    if not exact:
        return []
    hits = []
    i = text.find(exact)
    while i >= 0:
        hits.append(i)
        i = text.find(exact, i + 1)
    if len(hits) > 1:
        pre, suf = quote.get("prefix", ""), quote.get("suffix", "")
        hits = [h for h in hits
                if text[max(0, h - len(pre)):h].endswith(pre) and text[h + len(exact):].startswith(suf)]
    return hits


def _segments(matter, layer) -> list[dict]:
    obj = matter.objects.get_object(layer.id)
    return matter.objects.get_jsonl(obj.get("segments"))


def expand_alias(matter, loc: dict, content: str) -> dict:
    """Turn ``pl=14:3,15:7`` (transcript page:line, through ``line``
    segments) or ``label=iv`` (a printed page label, through ``page-label``
    segments) into the canonical char or page selector."""
    state = matter.state
    sel = dict(loc["select"])
    layers = state.layers.get(content, [])
    if loc.get("layer"):
        layers = [l for l in layers if l.id == loc["layer"]]
    else:
        primary = state.primary_layer(content)
        layers = sorted(layers, key=lambda l: l is not primary)
    if "pl" in sel:
        first, _, last = sel.pop("pl").partition(",")
        last = last or first
        for layer in layers:
            lines = {s["label"]: s for s in _segments(matter, layer) if s["kind"] == "line"}
            if first in lines and last in lines:
                s, e = lines[first]["start"], lines[last]["end"]
                if s > e:
                    raise LocatorError(f"pl={first},{last} runs backwards")
                return {"doc": loc["doc"], "layer": layer.id, "select": {**sel, "char": [s, e]}}
        raise LocatorError(f"no transcript lines {first}–{last} in this document")
    label = sel.pop("label")
    for layer in layers:
        text = None
        for seg in _segments(matter, layer):
            if seg["kind"] == "page-label" and seg.get("label") == label:
                text = text or matter.objects.get_text(layer.text)
                return {**{k: v for k, v in loc.items() if k != "select"}, "select": {**sel, "page": page_of(text, seg["start"])}}
    raise LocatorError(f"no page labeled {label!r} in this document")


def resolve(matter, loc) -> Resolution:
    """Check a locator against the store, following the steps in
    LOCATORS.md: exact, re-anchored (same text hash, or the quote found once
    in the primary layer), or broken. Never falls back to whatever text is
    now at the old offsets."""
    loc = parse(loc, matter.objects)
    state = matter.state
    doc = loc["doc"]
    notes = []
    if doc not in state.documents or doc in state.withdrawn:
        new = state.follow(doc)
        if new != doc and new in state.documents and new not in state.withdrawn:
            notes.append(f"superseded by {new}")
            loc = {**loc, "doc": new}
            doc = new
        else:
            reason = state.withdrawn.get(doc)
            return Resolution(status="broken", loc=loc, error=f"document withdrawn ({reason})" if reason else "no such document")
    content = state.documents[doc]
    sel = loc.get("select") or {}
    if "pl" in sel or "label" in sel:
        loc = expand_alias(matter, loc, content)
        sel = loc["select"]
    if "bytes" in sel:
        return _resolve_bytes(matter, loc, content, notes)
    if "ptr" in sel or "xpath" in sel or "col" in sel:
        try:
            loc = expand_pointer(matter, loc, content) if "col" not in sel else expand_columns(matter, loc, content)
        except ValueError as e:
            return Resolution(status="broken", loc=loc, error=str(e))
        sel = loc["select"]
    if "cells" in sel:
        return _resolve_cells(matter, loc, content, notes)
    if "xywh" in sel:
        xywh = sel["xywh"]
        if "page" not in sel or not isinstance(sel["page"], int):
            return Resolution(status="broken", loc=loc, error="a region (xywh) needs one page")
        if len(xywh) != 4 or not all(0 <= v <= 1 for v in xywh) or xywh[2] <= 0 or xywh[3] <= 0 \
                or xywh[0] + xywh[2] > 1.0001 or xywh[1] + xywh[3] > 1.0001:
            return Resolution(status="broken", loc=loc, error="xywh is x, y, width, height as fractions of the page")
    if "char" not in sel and "page" not in sel:
        return Resolution(status="re-anchored" if notes else "exact", loc=loc, notes=notes)

    layers = state.layers.get(content, [])
    layer = next((l for l in layers if l.id == loc.get("layer")), None) if loc.get("layer") else state.primary_layer(content)
    status = "re-anchored" if notes else "exact"
    if layer is None and loc.get("layer"):
        # Another layer with the same text keeps the offsets valid.
        try:
            want_text = matter.objects.get_object(loc["layer"])["text"]
        except KeyError:
            want_text = None
        layer = next((l for l in layers if l.text == want_text), None)
        if layer is not None:
            status = "re-anchored"
            notes.append("same text in another layer")
    if layer is None:
        primary = state.primary_layer(content)
        quote = loc.get("quote")
        if primary is None or not quote:
            return Resolution(status="broken", loc=loc, error="layer missing and no quote to search for")
        text = matter.objects.get_text(primary.text)
        hits = _find_quote(text, quote)
        if len(hits) != 1:
            return Resolution(status="broken", loc=loc, error=f"quote found {len(hits)} times in primary layer")
        s = hits[0]
        new = char_locator(doc, primary.id, text, s, s + len(quote["exact"]))
        return Resolution(status="re-anchored", loc=new, text=quote["exact"], notes=notes + ["found by quote"])

    text = matter.objects.get_text(layer.text)
    loc = {**loc, "layer": layer.id}
    if "char" in sel:
        s, e = sel["char"]
        if not (0 <= s <= e <= len(text)):
            return Resolution(status="broken", loc=loc, error="offsets outside the layer")
        if "page" in sel:
            ps, pe = page_range(text, sel["page"])
            if not (ps <= s and e <= pe):
                return Resolution(status="broken", loc=loc, error="char range is not on the given page")
    else:
        s, e = page_range(text, sel["page"])
    selected = text[s:e]
    quote = loc.get("quote")
    if quote and quote.get("exact") != selected:
        return Resolution(status="broken", loc=loc, error="quote does not match the text at these offsets")
    return Resolution(status=status, loc=loc, text=selected, notes=notes)


def _resolve_cells(matter, loc, content, notes) -> Resolution:
    """A rectangle of cells: in the given layer, else the primary. Its quote,
    when it has one, must still match; ``span`` is the char range it covers."""
    state = matter.state
    sel = loc["select"]
    layer = state.layer(loc["layer"]) if loc.get("layer") else state.primary_layer(content)
    if layer is None or layer not in state.layers.get(content, []):
        return Resolution(status="broken", loc=loc, error="no such layer in this document")
    text = matter.objects.get_text(layer.text)
    try:
        s, e = sheet_range(text, _segments(matter, layer), sel.get("sheet"))
        selected, span = cells_text(text, s, e, parse_cells(sel["cells"]))
    except LocatorError as err:
        return Resolution(status="broken", loc=loc, error=str(err))
    quote = loc.get("quote")
    if quote and quote.get("exact") != selected:
        return Resolution(status="broken", loc=loc, error="quote does not match the cells")
    loc = {**loc, "layer": layer.id, "quote": {"exact": selected}}
    return Resolution(status="re-anchored" if notes else "exact", loc=loc, text=selected, notes=notes,
                      span=list(span) if span else None)


def _resolve_bytes(matter, loc, content, notes) -> Resolution:
    """A range of the original's bytes. ``hex`` is them (for ranges a hex
    dump would show), and the resolution's ``data`` attribute the bytes."""
    from . import raw
    sel = loc["select"]
    if len(sel["bytes"]) != 2 or set(sel) - {"bytes"}:
        return Resolution(status="broken", loc=loc, error="a byte range is bytes=start,end, alone")
    data, err = raw.check_bytes(matter, content, sel, loc.get("quote"))
    if err:
        return Resolution(status="broken", loc=loc, error=err)
    loc = {k: v for k, v in loc.items() if k != "layer"}
    loc["quote"] = raw.fingerprint(data)
    r = Resolution(status="re-anchored" if notes else "exact", loc=loc, notes=notes, size=len(data))
    if len(data) <= raw.DUMP_LIMIT:
        r["hex"] = data.hex()
    r.data = data
    return r


def pointer_kind(matter, doc: str) -> str | None:
    """``json``, ``ndjson`` or ``xml`` from the document's metadata (kind,
    or the format a text file was found to be), if it says."""
    h = matter.state.metadata.get(doc)
    meta = matter.objects.get_object(h) if h else {}
    for v in (meta.get("kind"), meta.get("format")):
        if v in ("json", "ndjson", "xml"):
            return v
    return None


def expand(matter, loc) -> dict:
    """A locator with its aliases (``pl``, ``label``, ``ptr``, ``xpath``,
    ``col``/``row``) turned into the canonical selector they name, for
    whatever reads only those (the viewer). Raises LocatorError."""
    loc = parse(loc, matter.objects)
    sel = loc.get("select") or {}
    state = matter.state
    content = state.documents.get(loc["doc"])
    if content is None:
        return loc
    if "pl" in sel or "label" in sel:
        loc = expand_alias(matter, loc, content)
    if "ptr" in loc.get("select", {}) or "xpath" in loc.get("select", {}):
        loc = expand_pointer(matter, loc, content)
    if "col" in loc.get("select", {}):
        loc = expand_columns(matter, loc, content)
    return loc


def expand_columns(matter, loc: dict, content: str) -> dict:
    """Turn ``col=`` (with ``row=``, ``header=``) into ``cells=``, by the
    header row's text, in the given layer or the primary one."""
    state = matter.state
    sel = dict(loc["select"])
    name, rows, header = sel.pop("col"), sel.pop("row", None), sel.pop("header", 1)
    if "cells" in sel or "char" in sel:
        raise LocatorError("col names cells by itself; don't combine it with cells or char")
    layer = state.layer(loc["layer"]) if loc.get("layer") else state.primary_layer(content)
    if layer is None or layer not in state.layers.get(content, []):
        raise LocatorError("no such layer in this document")
    text = matter.objects.get_text(layer.text)
    s, e = sheet_range(text, _segments(matter, layer), sel.get("sheet"))
    lines = text[s:e].split("\n")
    if not 1 <= header <= len(lines):
        raise LocatorError(f"no row {header} for the header (the sheet has {len(lines)})")
    heads = [h.strip() for h in lines[header - 1].split("\t")]
    found = [i for i, h in enumerate(heads) if h == name.strip()]
    if not found:
        shown = ", ".join(repr(h) for h in heads if h)[:300]
        raise LocatorError(f"no column headed {name!r} in row {header} (the headers: {shown})")
    if len(found) > 1:
        letters = ", ".join(_col_name(i) for i in found)
        raise LocatorError(f"{len(found)} columns are headed {name!r} ({letters}); name the cells instead")
    letter = _col_name(found[0])
    if rows is None:
        last = max((r for r, line in enumerate(lines, 1) if line.strip()), default=header)
        if last <= header:
            raise LocatorError(f"nothing below the header {name!r}")
        r0, r1 = header + 1, last
    else:
        r0, r1 = (rows, rows) if isinstance(rows, int) else rows
        if not 1 <= r0 <= r1:
            raise LocatorError(f"not a row range: {r0}-{r1}")
    return {**loc, "layer": layer.id, "select": {**sel, "cells": f"{letter}{r0}" + (f":{letter}{r1}" if r1 != r0 else "")}}


def _col_name(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def expand_pointer(matter, loc: dict, content: str) -> dict:
    """Turn ``ptr=`` into the char range of the value it names, in the
    given layer or the primary one."""
    from . import structured
    state = matter.state
    sel = dict(loc["select"])
    xml = "xpath" in sel
    ptr = sel.pop("xpath" if xml else "ptr")
    if "char" in sel or "page" in sel or ("ptr" in sel and xml):
        raise LocatorError(f"{'xpath' if xml else 'ptr'} names a value by itself; don't combine it with char or page")
    layer = state.layer(loc["layer"]) if loc.get("layer") else state.primary_layer(content)
    if layer is None or layer not in state.layers.get(content, []):
        raise LocatorError("no such layer in this document")
    text = matter.objects.get_text(layer.text)
    kind = pointer_kind(matter, loc["doc"]) or structured.sniff(text)
    if xml:
        if kind != "xml" and not text.lstrip("\ufeff \t\r\n").startswith("<"):
            raise LocatorError("xpath needs an XML document")
        s, e = structured.xpath_range(text, ptr)
        return {**loc, "layer": layer.id, "select": {**sel, "char": [s, e]}}
    if kind not in ("json", "ndjson"):
        raise LocatorError("ptr needs a JSON or JSON Lines document")
    s, e = structured.pointer_range(text, ptr, lines=kind == "ndjson")
    return {**loc, "layer": layer.id, "select": {**sel, "char": [s, e]}}
