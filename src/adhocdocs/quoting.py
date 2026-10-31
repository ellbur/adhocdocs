"""Quoting more than words: formatting, pictures of pages, and spreadsheet cells.

Formatting (``as=rich``) is recovered from the original at quote time, not
stored with the layer, so it works for documents ingested before it
existed. The words themselves are still the layer's, checked as for any
quotation; only how they are shown comes from here.

- **Word:** run properties (bold, italic, underline, strikethrough,
  superscript, subscript, and Strong/Emphasis character styles), found by
  extracting the original again. They are used only if that gives exactly
  the layer's text, so tracked-change views keep their own.
- **HTML** (emails, web pages): the tags around each piece of text, the same
  way.
- **PDF:** each word's font, matched by position. Fonts don't record
  intent, so this is approximate: bold and italic from font flags and
  names, superscript from the font flags. OCR layers have none.

Pictures (``as=image``) are crops of the page as MuPDF renders it: the full
lines a text quotation is on, or a region reference's rectangle.
"""

from dataclasses import dataclass

DPI = 150
PAD_X, PAD_Y = 0.01, 0.006       # around a crop, as fractions of the page


def _original(m, doc) -> tuple[bytes, dict]:
    state = m.state
    meta = m.objects.get_object(state.metadata[doc]) if doc in state.metadata else {}
    return m.objects.get_bytes(state.documents[doc]), meta


def styles_for(m, doc: str, layer) -> tuple[list, str | None]:
    """(runs, source): formatting runs ``(start, end, {"b", "i", …})`` over
    the layer's text, and what they came from (None if there's nothing)."""
    data, meta = _original(m, doc)
    kind = meta.get("kind")
    text = m.objects.get_text(layer.text)
    if kind in ("docx", "email", "html"):
        from .extract import extract
        try:
            ext = extract(meta.get("name"), data)
        except Exception:
            return [], None
        for l in ext.layers:
            if l.view == layer.view and l.text == text:
                return l.styles, {"docx": "Word run formatting", "email": "the email's HTML",
                                  "html": "the page's HTML"}[kind]
        return [], None
    if kind == "pdf" and layer.view == "native":
        return _pdf_styles(m, data, layer), "PDF fonts (approximate)"
    return [], None


def _pdf_styles(m, data: bytes, layer) -> list:
    import pymupdf
    geo = m.objects.get_jsonl(m.objects.get_object(layer.id).get("geometry")) if m.objects.get_object(layer.id).get("geometry") else []
    if not geo:
        return []
    d = pymupdf.open("pdf", data)
    spans = {}
    out = []
    for g in geo:
        if g["page"] not in spans:
            page = d[g["page"] - 1]
            w, h = page.rect.width, page.rect.height
            spans[g["page"]] = [(s["bbox"][0] / w, s["bbox"][1] / h, s["bbox"][2] / w, s["bbox"][3] / h, s["flags"], s["font"].lower())
                                for b in page.get_text("dict")["blocks"] for ln in b.get("lines", []) for s in ln["spans"]
                                if s["text"].strip()]
        x0, y0, x1, y1 = g["box"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for sx0, sy0, sx1, sy1, flags, font in spans[g["page"]]:
            if sx0 <= cx <= sx1 and sy0 <= cy <= sy1:
                st = set()
                if flags & 16 or any(w in font for w in ("bold", "black", "heavy", "semibold")):
                    st.add("b")
                if flags & 2 or "italic" in font or "oblique" in font:
                    st.add("i")
                if flags & 1:
                    st.add("sup")
                if st:
                    out.append((g["start"], g["end"], frozenset(st)))
                break
    return out


@dataclass
class Crop:
    page: int
    rect: list          # x0, y0, x1, y1 as fractions of the page
    png: bytes
    alt: str            # the words in it


def _words(m, doc, layer_id=None):
    from .produce import geometry
    state = m.state
    if layer_id is None:
        primary = state.primary_layer(state.documents[doc])
        layer_id = primary.id if primary else None
    if layer_id is None:
        return [], ""
    text = m.objects.get_text(state.layer(layer_id).text)
    return geometry(m, layer_id), text


def _render(m, doc, page_no: int, rect, highlight=()) -> bytes:
    import pymupdf
    from .produce import pages_for
    d, _ = pages_for(m, doc)
    page = d[page_no - 1]
    w, h = page.rect.width, page.rect.height
    for x0, y0, x1, y1 in highlight:
        page.draw_rect(pymupdf.Rect(x0 * w, y0 * h, x1 * w, y1 * h), color=None, fill=(1, 0.85, 0), fill_opacity=0.35, overlay=True)
    clip = pymupdf.Rect(rect[0] * w, rect[1] * h, rect[2] * w, rect[3] * h)
    return page.get_pixmap(dpi=DPI, clip=clip).tobytes("png")


def crops(m, r, highlight=False) -> list[Crop]:
    """Pictures for a resolved reference: one per page a text quotation
    touches (its whole lines), or the region of a region reference."""
    if r.region:
        x, y, w, h = r.region["xywh"]
        geo, text = _words(m, r.doc)
        inside = [g for g in geo if g["page"] == r.region["page"]
                  and x <= (g["box"][0] + g["box"][2]) / 2 <= x + w and y <= (g["box"][1] + g["box"][3]) / 2 <= y + h]
        rect = [round(v, 4) for v in (x, y, x + w, y + h)]
        return [Crop(r.region["page"], rect, _render(m, r.doc, r.region["page"], rect),
                     " ".join(text[g["start"]:g["end"]] for g in inside))]
    geo, text = _words(m, r.doc, r.layer.id)
    quoted = [g for g in geo if any(g["start"] < p.end and p.start < g["end"] for p in r.parts)]
    out = []
    for page in sorted({g["page"] for g in quoted}):
        mine = [g for g in quoted if g["page"] == page]
        top, bottom = min(g["box"][1] for g in mine), max(g["box"][3] for g in mine)
        # whole lines: every word on the page whose middle is between the first line's top and the last line's bottom
        lines = [g for g in geo if g["page"] == page and top <= (g["box"][1] + g["box"][3]) / 2 <= bottom]
        rect = [max(0.0, min(g["box"][0] for g in lines) - PAD_X), max(0.0, top - PAD_Y),
                min(1.0, max(g["box"][2] for g in lines) + PAD_X), min(1.0, bottom + PAD_Y)]
        marks = [g["box"] for g in mine] if highlight else ()
        out.append(Crop(page, [round(v, 4) for v in rect], _render(m, r.doc, page, rect, marks),
                        " ".join(text[g["start"]:g["end"]] for g in mine)))
    return out
