"""Word .docx, read from the XML so tracked changes are handled exactly.

``native`` has tracked changes accepted (insertions kept, deletions
dropped). When the file has any, ``markup`` (both, with ``insertion`` and
``deletion`` segments) and ``original`` (before the changes) are made too.
Each paragraph is one line; table rows are lines with tab-separated cells.
Footnotes, comments, headers and footers are not extracted yet.
"""

import io

import docx
from docx.oxml.ns import qn

from . import Extraction, TextBuilder, text_presentation
from .simple import col_name

W_P, W_TBL, W_TR, W_TC = qn("w:p"), qn("w:tbl"), qn("w:tr"), qn("w:tc")
W_T, W_DELTEXT, W_TAB, W_BR = qn("w:t"), qn("w:delText"), qn("w:tab"), qn("w:br")
W_INS, W_DEL = qn("w:ins"), qn("w:del")
W_R, W_RPR = qn("w:r"), qn("w:rPr")
W_VAL = qn("w:val")


def _run_style(r) -> frozenset:
    """A run's direct formatting, and what its character style's name says."""
    out = set()
    rpr = r.find(W_RPR)
    if rpr is None:
        return frozenset()
    def on(tag):
        el = rpr.find(qn(tag))
        return el is not None and el.get(W_VAL) not in ("0", "false", "none")
    if on("w:b"):
        out.add("b")
    if on("w:i"):
        out.add("i")
    if on("w:u"):
        out.add("u")
    if on("w:strike") or on("w:dstrike"):
        out.add("s")
    va = rpr.find(qn("w:vertAlign"))
    if va is not None and va.get(W_VAL) in ("superscript", "subscript"):
        out.add("sup" if va.get(W_VAL) == "superscript" else "sub")
    rs = rpr.find(qn("w:rStyle"))
    name = (rs.get(W_VAL) or "").lower() if rs is not None else ""
    if "strong" in name or "bold" in name:
        out.add("b")
    if "emphasis" in name or "italic" in name:
        out.add("i")
    return frozenset(out)


def _runs_text(p, mode: str, b: TextBuilder):
    """Append a paragraph's text. ``mode`` is native, markup or original."""
    def walk(el, change, style=frozenset()):
        for child in el:
            tag = child.tag
            if tag == W_INS:
                walk(child, "insertion", style)
            elif tag == W_DEL:
                walk(child, "deletion", style)
            elif tag == W_R:
                walk(child, change, _run_style(child))
            elif tag in (W_T, W_DELTEXT, W_TAB, W_BR):
                keep = (mode == "markup"
                        or (mode == "native" and change != "deletion")
                        or (mode == "original" and change != "insertion"))
                if not keep:
                    continue
                if tag in (W_T, W_DELTEXT):
                    s = child.text or ""
                else:
                    s = "\t" if tag == W_TAB else "\n"
                start, end = b.add(s)
                if mode == "markup" and change and end > start:
                    b.segment(start, end, change)
                if style and end > start:
                    b.styles.append((start, end, style))
            elif tag in (W_P, W_TBL):
                continue
            else:
                walk(child, change, style)
    walk(p, None)


def _style(p) -> str:
    ppr = p.find(qn("w:pPr"))
    st = ppr.find(qn("w:pStyle")) if ppr is not None else None
    return (st.get(qn("w:val")) or "") if st is not None else ""


def _build(body, mode: str) -> TextBuilder:
    b = TextBuilder()
    first = True

    def newline():
        nonlocal first
        if not first:
            b.add("\n")
        first = False

    for el in body:
        if el.tag == W_P:
            newline()
            s = b.length
            _runs_text(el, mode, b)
            style = _style(el).lower()
            kind = "heading" if style.startswith(("heading", "title")) else "list-item" if "list" in style else "paragraph"
            b.segment(s, b.length, kind)
        elif el.tag == W_TBL:
            ts = None
            for r, tr in enumerate(el.iter(W_TR)):
                newline()
                rs = b.length
                ts = rs if ts is None else ts
                for c, tc in enumerate(tr.iter(W_TC)):
                    if c:
                        b.add("\t")
                    cs = b.length
                    for i, p in enumerate(tc.iter(W_P)):
                        if i:
                            b.add(" ")
                        _runs_text(p, mode, b)
                    b.segment(cs, b.length, "cell", ref=f"{col_name(c)}{r + 1}")
                b.segment(rs, b.length, "row")
            if ts is not None:
                b.segment(ts, b.length, "table")
    return b


def extract_docx(data: bytes) -> Extraction:
    d = docx.Document(io.BytesIO(data))
    body = d.element.body
    has_changes = body.find(f".//{W_INS}") is not None or body.find(f".//{W_DEL}") is not None
    layers = []
    for mode in ("native", "markup", "original") if has_changes else ("native",):
        layer = _build(body, mode).layer(mode, primary=mode == "native")
        layer.presentation = text_presentation(layer.text, layer.segments)
        layers.append(layer)
    cp = d.core_properties
    date = cp.created or cp.modified
    meta = {k: v for k, v in {
        "title": cp.title or None,
        "author": cp.author or None,
        "date": date.isoformat() if date else None,
        "date_source": ("created" if cp.created else "modified") if date else None,
        "created": cp.created.isoformat() if cp.created else None,
        "modified": cp.modified.isoformat() if cp.modified else None,
        "tracked_changes": has_changes or None,
    }.items() if v}
    return Extraction(kind="docx", layers=layers, metadata=meta)
