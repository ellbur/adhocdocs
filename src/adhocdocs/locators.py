"""Locators: names for a document or a place inside one.

String form::

    doc:<doc-id>[@<layer-id>][#<selector>]

with selectors ``page=14``, ``page=14-16``, ``char=1200,1340``,
``page=3&xywh=0.1,0.2,0.5,0.05`` and ``sheet=Q3&cells=B2:D10``. Hash
prefixes of 8 or more hex digits are accepted on input and expanded.

JSON form (durable; used in pages, notes, search results)::

    {"doc": ..., "layer": ..., "select": {"char": [s, e]},
     "quote": {"exact": ..., "prefix": ..., "suffix": ...}}

Offsets are Unicode code points into the layer text, half-open.
"""

import json

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
        elif key in ("sheet", "cells", "label", "pl"):
            sel[key] = val
        else:
            raise LocatorError(f"unknown selector: {key}")
    return sel


def _format_selector(sel: dict) -> str:
    parts = []
    for key, val in sel.items():
        if isinstance(val, list):
            sep = "-" if key == "page" else ","
            parts.append(f"{key}={sep.join(str(v) for v in val)}")
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
