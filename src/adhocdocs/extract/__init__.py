"""Extractors: turn an original's bytes into text layers, metadata and
children (attachments, archive members).

Each extractor returns an :class:`Extraction`. The layer text it builds
follows TEXTLAYERS.md: NFC, ``\\n`` line endings, form feeds only as page
breaks, otherwise literal.
"""

import html
import unicodedata
from dataclasses import dataclass, field


@dataclass
class Layer:
    view: str
    text: str
    segments: list = field(default_factory=list)
    geometry: list = field(default_factory=list)
    presentation: str | None = None
    primary: bool = False
    derived_from: int | None = None   # index of the source layer in the same Extraction
    offset_map: list = field(default_factory=list)


@dataclass
class Child:
    part: str
    name: str | None
    data: bytes


@dataclass
class Extraction:
    kind: str
    layers: list[Layer] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    children: list[Child] = field(default_factory=list)


def clean(s: str) -> str:
    """Make extracted text fit the layer rules without changing its words."""
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\f", " ")
    return unicodedata.normalize("NFC", s)


class TextBuilder:
    """Accumulates layer text, handing back the code-point range of each piece."""

    def __init__(self):
        self.parts: list[str] = []
        self.length = 0
        self.segments: list[dict] = []
        self.geometry: list[dict] = []

    def add(self, s: str) -> tuple[int, int]:
        s = clean(s)
        start = self.length
        self.parts.append(s)
        self.length += len(s)
        return start, self.length

    def raw(self, s: str) -> tuple[int, int]:
        """Add structural characters (``\\f``) that ``add`` would clean away."""
        start = self.length
        self.parts.append(s)
        self.length += len(s)
        return start, self.length

    def segment(self, start: int, end: int, kind: str, **extra):
        self.segments.append({"start": start, "end": end, "kind": kind, **extra})

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def layer(self, view: str, presentation=None, primary=False) -> Layer:
        segs = sorted(self.segments, key=lambda s: (s["start"], -s["end"]))
        return Layer(view, self.text, segs, self.geometry, presentation, primary)


def text_presentation(text: str, segments: list) -> str:
    """A plain HTML rendering of layer text, every line a span carrying its
    layer offset, for flow-mode display of content without pages."""
    kinds_at = []
    for seg in segments:
        if seg["kind"] in ("header", "quoted", "signature", "heading"):
            kinds_at.append(seg)
    out = ['<article class="adhoc-flow">']
    pos = 0
    for line in text.split("\n"):
        classes = sorted({s["kind"] for s in kinds_at if s["start"] <= pos < s["end"] or (s["start"] == pos and s["end"] == pos)})
        cls = f' class="{" ".join(classes)}"' if classes else ""
        if line:
            out.append(f'<p{cls}><span data-o="{pos}">{html.escape(line)}</span></p>')
        else:
            out.append(f"<p{cls}><br></p>")
        pos += len(line) + 1
    out.append("</article>")
    return "\n".join(out)


def kind_for(name: str | None, data: bytes) -> str:
    n = (name or "").lower()
    if data.startswith(b"%PDF") or n.endswith(".pdf"):
        return "pdf"
    if n.endswith(".eml"):
        return "email"
    if n.endswith(".docx") or (data.startswith(b"PK") and b"word/" in data[:2000]):
        return "docx"
    if n.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp")):
        return "image"
    if n.endswith(".csv"):
        return "csv"
    if n.endswith((".html", ".htm")):
        return "html"
    if n.endswith((".txt", ".text", ".md")):
        return "text"
    head = data[:2000]
    if head.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        return "html"
    if b"\nSubject:" in head or head.startswith((b"From:", b"Received:", b"Return-Path:", b"Message-ID:", b"MIME-Version:")):
        return "email"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "other"


def extract(name: str | None, data: bytes) -> Extraction:
    kind = kind_for(name, data)
    if kind == "pdf":
        from .pdf import extract_pdf
        return extract_pdf(data)
    if kind == "image":
        from .pdf import extract_image
        return extract_image(data)
    if kind == "email":
        from .email import extract_email
        return extract_email(data)
    if kind == "docx":
        from .docx import extract_docx
        return extract_docx(data)
    if kind == "csv":
        from .simple import extract_csv
        return extract_csv(name, data)
    if kind == "html":
        from .simple import extract_html
        return extract_html(data)
    if kind == "text":
        from .simple import extract_text
        return extract_text(data)
    return Extraction(kind="other")
