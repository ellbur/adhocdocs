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
lines a text quotation is on, or a region reference's rectangle. Documents
without pages of their own (emails, Word files, web pages, text) have
their text set as pages first, with its formatting, and their words found
on those pages by reading them back in order. Those pictures show our
rendering, not the document's own layout, and say so.
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
    rendered: bool = False      # a picture of our rendering of the text, not of the document's own pages


# -- pages to take pictures of ----------------------------------------------------------------

def paginated(m, doc) -> bool:
    """Whether the document has pages of its own (PDFs and images)."""
    return _original(m, doc)[1].get("kind") in ("pdf", "image")


RENDER_CSS = """
body { font-family: sans-serif; font-size: 11pt; }
p { margin: 0; line-height: 1.35; white-space: pre-wrap; }
p.header { font-size: 10pt; color: #333; }
p.quoted { color: #555; }
p.heading { font-weight: bold; font-size: 13pt; }
p.footnote, p.endnote, p.comment, p.header-footer { font-size: 9.5pt; color: #333; }
"""
TAGS = (("b", "b"), ("i", "i"), ("u", "u"), ("s", "s"), ("sup", "sup"), ("sub", "sub"))
_rendered = {}


def _render_html(text: str, segments: list, styles: list) -> str:
    """The layer's text as HTML, a paragraph per line, with its formatting."""
    import html
    from .extract import NOTE_KINDS
    kinds = [s for s in segments if s["kind"] in ("header", "quoted", "heading", *NOTE_KINDS)]
    out, pos = [], 0
    for line in text.replace("\f", "\n").split("\n"):
        end = pos + len(line)
        cls = sorted({s["kind"] for s in kinds if s["start"] <= pos < s["end"] or (line == "" and s["start"] == pos)})
        cuts = sorted({pos, end, *[x for a, b, _ in styles for x in (a, b) if pos < x < end]})
        spans = []
        for a, b in zip(cuts, cuts[1:]):
            piece = html.escape(text[a:b])
            st = frozenset().union(*[st for x, y, st in styles if x <= a and b <= y])
            for key, tag in TAGS:
                if key in st:
                    piece = f"<{tag}>{piece}</{tag}>"
            spans.append(piece)
        out.append(f'<p class="{" ".join(cls)}">{"".join(spans) or "&nbsp;"}</p>')
        pos = end + 1
    return "\n".join(out)


def render_pages(m, doc, layer):
    """A text layer set as letter pages, with each of its words' boxes found
    by matching the words read back from the pages, in order, to the
    layer's. Returns (PDF, geometry)."""
    import io
    import re
    import pymupdf
    key = (doc, layer.id)
    if key in _rendered:
        return _rendered[key]
    text = m.objects.get_text(layer.text)
    obj = m.objects.get_object(layer.id)
    segments = m.objects.get_jsonl(obj["segments"]) if obj.get("segments") else []
    styles, _ = styles_for(m, doc, layer)
    story = pymupdf.Story(html=_render_html(text, segments, styles), user_css=RENDER_CSS)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    page_rect = pymupdf.paper_rect("letter")
    more = True
    while more:
        dev = writer.begin_page(page_rect)
        more, _ = story.place(page_rect + (54, 54, -54, -54))
        story.draw(dev)
        writer.end_page()
    writer.close()
    pdf = pymupdf.open("pdf", buf.getvalue())

    words = [(mt.start(), mt.end(), mt.group()) for mt in re.finditer(r"\S+", text)]
    shown = [(n, x0 / pg.rect.width, y0 / pg.rect.height, x1 / pg.rect.width, y1 / pg.rect.height, w)
             for n, pg in enumerate(pdf, 1) for x0, y0, x1, y1, w, *_ in pg.get_text("words", flags=0)]
    geo, j = [], 0
    for start, end, word in words:
        # the next rendered word that is this one, not far ahead (a glyph the font lacks can garble one)
        for k in range(j, min(j + 4, len(shown))):
            if shown[k][5] == word:
                n, x0, y0, x1, y1, _ = shown[k]
                geo.append({"start": start, "end": end, "page": n, "box": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]})
                j = k + 1
                break
    _rendered[key] = (pdf, geo)
    return pdf, geo


def _source(m, doc, layer_id=None):
    """(PDF, word boxes, layer text, rendered?) for taking pictures: the
    document's own pages when it has them, else its text set as pages."""
    from .produce import geometry, pages_for
    state = m.state
    layer = state.layer(layer_id) if layer_id else state.primary_layer(state.documents[doc])
    text = m.objects.get_text(layer.text) if layer else ""
    if paginated(m, doc):
        return pages_for(m, doc)[0], (geometry(m, layer.id) if layer else []), text, False
    pdf, geo = render_pages(m, doc, layer)
    return pdf, geo, text, True


def _picture(pdf, page_no: int, rect, highlight=()) -> bytes:
    import pymupdf
    page = pymupdf.open("pdf", pdf.tobytes())[page_no - 1]     # a copy: highlights mustn't stay
    w, h = page.rect.width, page.rect.height
    for x0, y0, x1, y1 in highlight:
        page.draw_rect(pymupdf.Rect(x0 * w, y0 * h, x1 * w, y1 * h), color=None, fill=(1, 0.85, 0), fill_opacity=0.35, overlay=True)
    clip = pymupdf.Rect(rect[0] * w, rect[1] * h, rect[2] * w, rect[3] * h)
    return page.get_pixmap(dpi=DPI, clip=clip).tobytes("png")


def crops(m, r, highlight=False) -> list[Crop]:
    """Pictures for a resolved reference: one per page a text quotation
    touches (its whole lines), or the region of a region reference."""
    if r.region:
        pdf, geo, text, rendered = _source(m, r.doc)
        x, y, w, h = r.region["xywh"]
        inside = [g for g in geo if g["page"] == r.region["page"]
                  and x <= (g["box"][0] + g["box"][2]) / 2 <= x + w and y <= (g["box"][1] + g["box"][3]) / 2 <= y + h]
        rect = [round(v, 4) for v in (x, y, x + w, y + h)]
        return [Crop(r.region["page"], rect, _picture(pdf, r.region["page"], rect),
                     " ".join(text[g["start"]:g["end"]] for g in inside), rendered)]
    pdf, geo, text, rendered = _source(m, r.doc, r.layer.id)
    quoted = [g for g in geo if any(g["start"] < p.end and p.start < g["end"] for p in r.parts)]
    out = []
    for page in sorted({g["page"] for g in quoted}):
        mine = [g for g in quoted if g["page"] == page]
        top, bottom = min(g["box"][1] for g in mine), max(g["box"][3] for g in mine)
        # whole lines: every word on the page whose middle is between the first line's top and the last line's bottom
        lines = [g for g in geo if g["page"] == page and top <= (g["box"][1] + g["box"][3]) / 2 <= bottom]
        # Pad, but stop short of the lines above and below. Word boxes span the
        # font's whole height and overlap between lines set close, so measure
        # from line middles: cut a little past halfway to the next line's.
        mid = lambda g: (g["box"][1] + g["box"][3]) / 2
        first, last = min(mid(g) for g in mine), max(mid(g) for g in mine)
        height = min(g["box"][3] - g["box"][1] for g in mine)
        above = [mid(g) for g in geo if g["page"] == page and mid(g) < first - height / 2]
        below = [mid(g) for g in geo if g["page"] == page and mid(g) > last + height / 2]
        cut_top = max(top - PAD_Y, first - 0.6 * (first - max(above))) if above else top - PAD_Y
        cut_bottom = min(bottom + PAD_Y, last + 0.6 * (min(below) - last)) if below else bottom + PAD_Y
        rect = [max(0.0, min(g["box"][0] for g in lines) - PAD_X), max(0.0, cut_top),
                min(1.0, max(g["box"][2] for g in lines) + PAD_X), min(1.0, cut_bottom)]
        marks = [g["box"] for g in mine] if highlight else ()
        out.append(Crop(page, [round(v, 4) for v in rect], _picture(pdf, page, rect, marks),
                        " ".join(text[g["start"]:g["end"]] for g in mine), rendered))
    return out
