"""PDFs and images: native text with word geometry, OCR when a page has no text.

Words on a line are joined with single spaces (the PDF has no reliable
record of spacing); lines are joined with ``\\n`` and pages with ``\\f``.
Line-end hyphens and OCR errors are kept as they are.
"""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pymupdf

from . import Extraction, Layer, TextBuilder
from ..portable import tesseract

OCR_DPI = 300
# A page with fewer native characters than this, and an image on it, is OCRed.
MIN_PAGE_CHARS = 20
TRANSCRIPT_LINES = 25


def _box(rect, w, h):
    # Integers per mille-ish would avoid floats, but geometry isn't a hashed
    # object, so round to 4 places and keep it readable.
    x0, y0, x1, y1 = rect
    return [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)]


def _add_lines(b: TextBuilder, lines, page_no: int):
    """``lines`` is a list of lists of (word, box, conf)."""
    for i, words in enumerate(lines):
        if i:
            b.add("\n")
        for j, (word, box, conf) in enumerate(words):
            if j:
                b.add(" ")
            s, e = b.add(word)
            g = {"start": s, "end": e, "page": page_no, "box": box}
            if conf is not None:
                g["conf"] = conf
            b.geometry.append(g)


def native_lines(page) -> list:
    w, h = page.rect.width, page.rect.height
    lines, prev = [], None
    for x0, y0, x1, y1, word, *_ in page.get_text("words", sort=False):
        # Words in stream order are on one line while they move right and
        # overlap vertically, even across text blocks (transcript line
        # numbers, table cells).
        if prev is None or x0 < prev[2] - 1 or min(y1, prev[3]) - max(y0, prev[1]) < 0.5 * min(y1 - y0, prev[3] - prev[1]):
            lines.append([])
        lines[-1].append((word, _box((x0, y0, x1, y1), w, h), None))
        prev = (x0, y0, x1, y1)
    return lines


def tessdata() -> list[str]:
    """Use the models directory's tessdata if it has one, else tesseract's own."""
    d = Path(os.environ.get("ADHOC_MODELS", Path.home() / ".cache" / "adhocdocs" / "models")) / "tessdata"
    return ["--tessdata-dir", str(d)] if (d / "eng.traineddata").exists() else []


def ocr_png(png: bytes, psm: int = 3) -> list:
    """Words from tesseract, grouped into lines: (word, box, conf)."""
    r = subprocess.run([tesseract(), "stdin", "stdout", *tessdata(), "-l", "eng", "--psm", str(psm), "-c", "tessedit_create_tsv=1"],
                       input=png, capture_output=True)
    if r.returncode:
        raise RuntimeError("tesseract failed: " + r.stderr.decode().strip().splitlines()[0])
    out = r.stdout.decode()
    rows = [r.split("\t") for r in out.splitlines()[1:]]
    page_w = page_h = None
    lines, key = [], None
    for r in rows:
        if len(r) < 12:
            continue
        level, _, block, par, line, _, left, top, width, height, conf, text = r[:12]
        if level == "1":
            page_w, page_h = int(width), int(height)
        if level != "5" or not text.strip():
            continue
        if (block, par, line) != key:
            lines.append([])
            key = (block, par, line)
        x0, y0 = int(left), int(top)
        box = _box((x0, y0, x0 + int(width), y0 + int(height)), page_w, page_h)
        lines[-1].append((text, box, round(max(0.0, float(conf)) / 100, 3)))
    return lines


def ocr_page(page, dpi: int = OCR_DPI, psm: int = 3) -> list:
    return ocr_png(page.get_pixmap(dpi=dpi).tobytes("png"), psm)


def ocr_layer(data: bytes, kind: str, name: str | None, dpi: int = OCR_DPI, psm: int = 3) -> Layer:
    """OCR every page, whatever text the file already has: for re-reading a
    scan whose existing text, or first OCR, is poor."""
    if kind == "image":
        pix = pymupdf.Pixmap(data)
        if pix.alpha or pix.n > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        doc = pymupdf.open("pdf", pymupdf.open(stream=pix.tobytes("png"), filetype="png").convert_to_pdf())
    else:
        doc = pymupdf.open(stream=data, filetype="pdf")
    b = TextBuilder()
    for i, page in enumerate(doc, 1):
        if i > 1:
            b.raw("\f")
        _add_lines(b, ocr_page(page, dpi, psm), i)
    mark_transcript(b)
    return b.layer("ocr")


PAGE_LABEL = re.compile(r"^\s*(?:Page\s+)?(\d{1,5})\s*$", re.I)
NUMBERED = re.compile(r"^\s*(\d{1,2})(?=\s|$)")


def mark_transcript(b: TextBuilder):
    """Find printed page labels and, if the document looks like a transcript
    (pages of lines numbered 1-25), give each numbered line a ``line``
    segment labeled ``page:line``."""
    text = b.text
    pages, pos = [], 0
    for ptext in text.split("\f"):
        pages.append((pos, ptext))
        pos += len(ptext) + 1
    numbered_pages = 0
    marks = []
    for pno, (pstart, ptext) in enumerate(pages, 1):
        lines, lpos = [], pstart
        for line in ptext.split("\n"):
            lines.append((lpos, line))
            lpos += len(line) + 1
        label = label_at = None
        for lstart, line in lines[:2] + lines[-2:]:
            m = PAGE_LABEL.match(line)
            if m:
                label, label_at = m.group(1), lstart
                b.segment(lstart, lstart + len(line), "page-label", label=label)
                break
        nums = []
        for lstart, line in lines:
            m = NUMBERED.match(line)
            if m and 1 <= int(m.group(1)) <= TRANSCRIPT_LINES and lstart != label_at:
                nums.append((int(m.group(1)), lstart, len(line)))
        if len(nums) >= 10 and [n for n, _, _ in nums] == sorted(n for n, _, _ in nums):
            numbered_pages += 1
            marks.append((label or str(pno), nums))
    if numbered_pages and numbered_pages >= len(pages) // 2:
        for label, nums in marks:
            for n, lstart, llen in nums:
                b.segment(lstart, lstart + llen, "line", label=f"{label}:{n}")
        return True
    return False


def transcript_view(src: Layer, src_index: int) -> Layer:
    """The testimony without margin line numbers or page labels, so a phrase
    reads straight across lines and pages. Line segments keep their
    ``page:line`` labels; the offset map leads back to the source layer
    (and through it to the page geometry)."""
    text = src.text
    drops = []
    for seg in src.segments:
        if seg["kind"] == "line":
            m = re.match(r"\s*\d{1,2}[ \t]*", text[seg["start"]:seg["end"]])
            drops.append((seg["start"], seg["start"] + m.end()))
        elif seg["kind"] == "page-label":
            end = seg["end"] + 1 if text[seg["end"]:seg["end"] + 1] == "\n" else seg["end"]
            drops.append((seg["start"], end))
    out, runs, pos, n = [], [], 0, 0
    for a, b in sorted(drops) + [(len(text), len(text))]:
        if a > pos:
            out.append(text[pos:a])
            runs.append({"start": n, "end": n + a - pos, "source_start": pos})
            n += a - pos
        pos = max(pos, b)

    def mapped(off):
        for r in runs:
            if r["source_start"] <= off <= r["source_start"] + r["end"] - r["start"]:
                return r["start"] + off - r["source_start"]
        return None

    segments = []
    for seg in src.segments:
        if seg["kind"] == "line":
            start = next(b for a, b in drops if a == seg["start"])
            s, e = mapped(start), mapped(seg["end"])
            if s is not None and e is not None:
                segments.append({**seg, "start": s, "end": e})
    return Layer("transcript", "".join(out), segments, primary=True, derived_from=src_index, offset_map=runs)


def _pdf_date(s: str | None) -> str | None:
    m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?", s or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)).date().isoformat()
    except ValueError:
        return None


def extract_pdf(data: bytes) -> Extraction:
    doc = pymupdf.open(stream=data, filetype="pdf")
    native, ocr = TextBuilder(), TextBuilder()
    needs_ocr = False
    page_lines = []
    for i, page in enumerate(doc, 1):
        lines = native_lines(page)
        page_lines.append(lines)
        chars = sum(len(w) for l in lines for w, _, _ in l)
        if chars < MIN_PAGE_CHARS and page.get_images():
            needs_ocr = True
    for i, lines in enumerate(page_lines, 1):
        if i > 1:
            native.raw("\f")
        _add_lines(native, lines, i)
    is_transcript = mark_transcript(native)
    layers = [native.layer("native", primary=not needs_ocr)]
    if needs_ocr:
        for i, page in enumerate(doc, 1):
            if i > 1:
                ocr.raw("\f")
            lines = page_lines[i - 1]
            # Pages with native text keep it; only textless pages are OCRed.
            if sum(len(w) for l in lines for w, _, _ in l) < MIN_PAGE_CHARS and page.get_images():
                lines = ocr_page(page)
            _add_lines(ocr, lines, i)
        is_transcript = mark_transcript(ocr)
        layers.append(ocr.layer("ocr", primary=True))
    if is_transcript:
        layers[-1].primary = False
        layers.append(transcript_view(layers[-1], len(layers) - 1))
    meta = {k: v for k, v in {
        "title": (doc.metadata or {}).get("title") or None,
        "author": (doc.metadata or {}).get("author") or None,
        "date": _pdf_date((doc.metadata or {}).get("creationDate")),
        "pages": doc.page_count,
    }.items() if v}
    if is_transcript:
        meta["transcript"] = True
    return Extraction(kind="pdf", layers=layers, metadata=meta)


def extract_image(data: bytes) -> Extraction:
    pix = pymupdf.Pixmap(data)
    if pix.alpha or pix.n > 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    b = TextBuilder()
    _add_lines(b, ocr_png(pix.tobytes("png")), 1)
    return Extraction(kind="image", layers=[b.layer("ocr", primary=True)], metadata={"pages": 1})
