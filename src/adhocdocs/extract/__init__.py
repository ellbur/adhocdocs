"""Extractors: turn an original's bytes into text layers, metadata and
children (attachments, archive members).

Each extractor returns an :class:`Extraction`. The layer text it builds
follows TEXTLAYERS.md: NFC, ``\\n`` line endings, form feeds only as page
breaks, otherwise literal.
"""

import html
import unicodedata
from collections.abc import Callable, Iterable
from typing import BinaryIO
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
    # Formatting runs from the original ((start, end, {"b", "i", …})). Not
    # stored: quoting recomputes them from the original (see adhocdocs.quoting).
    styles: list = field(default_factory=list)


@dataclass
class Child:
    part: str
    name: str | None
    data: bytes | None = None
    # Or, for a member too large to hold (a zip member read from disk): a
    # call opening it as a binary file, to be read while the parent's
    # children are being iterated.
    open: Callable[[], BinaryIO] | None = None


@dataclass
class Extraction:
    kind: str
    layers: list[Layer] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    # A list, or for a container read from disk (extract_file) a generator
    # yielding one child at a time; metadata is complete once it is used up.
    children: Iterable[Child] = field(default_factory=list)


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
        self.styles: list = []

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
        return Layer(view, self.text, segs, self.geometry, presentation, primary, styles=self.styles)


# Segments for what a document shows outside its body (a .docx's notes,
# comments, headers and footers), set apart when displayed
NOTE_KINDS = ("footnote", "endnote", "comment", "header-footer")


# Segments shown raised: footnote and endnote numbers, in the body and at their notes
RAISED_KINDS = ("note-ref", "note-mark")


def _raised_spans(line: str, pos: int, raised: list) -> str:
    """A line as spans carrying their layer offsets, the raised ranges in <sup>."""
    end = pos + len(line)
    cuts = sorted({pos, end, *[x for a, b in raised for x in (a, b) if pos < x < end]})
    out = []
    for a, b in zip(cuts, cuts[1:]):
        span = f'<span data-o="{a}">{html.escape(line[a - pos:b - pos])}</span>'
        out.append(f"<sup>{span}</sup>" if any(x <= a and b <= y for x, y in raised) else span)
    return "".join(out)


def text_presentation(text: str, segments: list) -> str:
    """A plain HTML rendering of layer text, every line a span carrying its
    layer offset, for flow-mode display of content without pages."""
    kinds_at = []
    for seg in segments:
        if seg["kind"] in ("header", "quoted", "signature", "heading", *NOTE_KINDS):
            kinds_at.append(seg)
    raised = [(seg["start"], seg["end"]) for seg in segments if seg["kind"] in RAISED_KINDS]
    titles = {seg["start"]: seg["title"] for seg in segments if seg["kind"] in NOTE_KINDS and seg.get("title")}
    out = ['<article class="adhoc-flow">']
    pos = 0
    for line in text.split("\n"):
        classes = sorted({s["kind"] for s in kinds_at if s["start"] <= pos < s["end"] or (s["start"] == pos and s["end"] == pos)})
        cls = f' class="{" ".join(classes)}"' if classes else ""
        if pos in titles:     # shown by CSS (::before), so it isn't selected or quoted as the document's text
            cls += f' data-title="{html.escape(titles[pos])}"'
        if line:
            out.append(f"<p{cls}>{_raised_spans(line, pos, raised)}</p>")
        else:
            out.append(f"<p{cls}><br></p>")
        pos += len(line) + 1
    out.append("</article>")
    return "\n".join(out)


def _zip_kind(data) -> str:
    """Office files are zips: tell them apart by what's inside. ``data`` is
    the bytes, or a path (only the zip's directory is read)."""
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data) if isinstance(data, bytes) else data) as z:
            names = set(z.namelist())
    except (zipfile.BadZipFile, OSError):
        return "other"
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return "zip"


def kind_for(name: str | None, data: bytes, zip_kind=_zip_kind) -> str:
    n = (name or "").lower()
    if data.startswith(b"%PDF") or n.endswith(".pdf"):
        return "pdf"
    if n.endswith(".eml"):
        return "email"
    if n.endswith((".pst", ".ost")) or data.startswith(b"!BDN"):
        return "pst"
    if n.endswith(".msg") or (data.startswith(b"\xd0\xcf\x11\xe0") and "__substg1.0_".encode("utf-16-le") in data):
        return "msg"
    if n.endswith(".docx"):
        return "docx"
    if n.endswith((".xlsx", ".xlsm")):
        return "xlsx"
    if data.startswith(b"PK\x03\x04"):
        return zip_kind(data)
    if n.endswith((".mbox", ".mbx")) or (data.startswith(b"From ") and b"\nFrom: " in data[:4000]):
        return "mbox"
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


# kind_for's names for formats whose extractors report another kind
_EXTRACTED_KIND = {"xlsx": "spreadsheet", "csv": "spreadsheet", "zip": "archive", "mbox": "mailbox",
                   "pst": "mailbox", "msg": "email"}


def unextracted_kind(name: str | None, data: bytes) -> str:
    """The kind to record when extraction failed: what the original is (a
    PDF stays a PDF, so it can be OCRed later), as extraction would have
    named it."""
    kind = kind_for(name, data)
    return _EXTRACTED_KIND.get(kind, kind)


# Containers extract_file reads in place, a member at a time
STREAMED = ("mbox", "pst", "zip")
SNIFF = 65536


def file_kind(path, name: str | None) -> str:
    """kind_for, from a file's first bytes (and a zip's directory): the
    same answer as kind_for on the whole file for every kind in STREAMED.
    For the rest, read the file and ask kind_for."""
    with open(path, "rb") as f:
        return kind_for(name, f.read(SNIFF), zip_kind=lambda _: _zip_kind(path))


def unextracted_file_kind(path, name: str | None) -> str:
    """unextracted_kind, for a file too large to read."""
    kind = file_kind(path, name)
    return _EXTRACTED_KIND.get(kind, kind)


def extract_file(path, kind: str) -> Extraction:
    """Extract a container on disk (``kind`` in STREAMED) without reading it
    into memory: ``children`` is a generator, whose zip members come with
    ``open`` rather than ``data``."""
    if kind == "zip":
        from .archive import extract_zip_file
        return extract_zip_file(path)
    if kind == "mbox":
        from .archive import extract_mbox_file
        return extract_mbox_file(path)
    if kind == "pst":
        from .pst import extract_pst_file
        return extract_pst_file(path)
    raise ValueError(f"{kind} is not read from disk")


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
    if kind == "xlsx":
        from .simple import extract_xlsx
        return extract_xlsx(data)
    if kind == "zip":
        from .archive import extract_zip
        return extract_zip(data)
    if kind == "mbox":
        from .archive import extract_mbox
        return extract_mbox(data)
    if kind == "pst":
        from .pst import extract_pst
        return extract_pst(data)
    if kind == "msg":
        from .msg import extract_msg
        return extract_msg(data)
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
