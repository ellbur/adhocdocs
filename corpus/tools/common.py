"""Shared helpers for building the synthetic part of the adhocdocs test corpus.

Everything here aims at reproducible output: fixed timestamps, fixed MIME
boundaries, seeded noise. Fonts are the Liberation family (SIL OFL); set
ADHOC_FONT_DIR if they are not in /usr/share/fonts/liberation.
"""

from __future__ import annotations

import io
import os
import zipfile
from email import policy
from email.message import EmailMessage
from pathlib import Path

import numpy as np
import pymupdf

CORPUS = Path(__file__).resolve().parent.parent
FONT_DIR = Path(os.environ.get("ADHOC_FONT_DIR", "/usr/share/fonts/liberation"))

FONTS = {
    "serif": "LiberationSerif-Regular.ttf",
    "serif-bold": "LiberationSerif-Bold.ttf",
    "serif-italic": "LiberationSerif-Italic.ttf",
    "sans": "LiberationSans-Regular.ttf",
    "sans-bold": "LiberationSans-Bold.ttf",
    "mono": "LiberationMono-Regular.ttf",
}
_font_cache: dict[str, pymupdf.Font] = {}

LETTER = pymupdf.paper_rect("letter")  # 612 x 792


def font(name: str) -> pymupdf.Font:
    if name not in _font_cache:
        _font_cache[name] = pymupdf.Font(fontfile=str(FONT_DIR / FONTS[name]))
    return _font_cache[name]


def out(custodian: str, filename: str) -> Path:
    d = CORPUS / custodian
    d.mkdir(parents=True, exist_ok=True)
    return d / filename


# ---------------------------------------------------------------- PDF text


class PdfWriter:
    """Minimal flowing-text PDF writer with real (selectable) text."""

    def __init__(self, title: str, author: str, date: str, margin=72, footer=None):
        self.doc = pymupdf.open()
        self.margin = margin
        self.footer = footer  # callable(page_no) -> str
        self.title, self.author, self.date = title, author, date
        self.page = None
        self.y = 0.0
        self.hyphenations: list[str] = []
        self.new_page()

    @property
    def width(self):
        return LETTER.width - 2 * self.margin

    def new_page(self):
        if self.page is not None and self.footer:
            self._footer()
        self.page = self.doc.new_page(width=LETTER.width, height=LETTER.height)
        self.y = self.margin

    def _footer(self):
        n = self.doc.page_count
        text = self.footer(n)
        f = font("sans")
        w = f.text_length(text, 8)
        self.text_at((LETTER.width - w) / 2, LETTER.height - 40, text, "sans", 8)

    def text_at(self, x, y, text, fname="serif", size=11, color=(0, 0, 0)):
        self.page.insert_text((x, y), text, fontname=fname.replace("-", ""),
                              fontfile=str(FONT_DIR / FONTS[fname]), fontsize=size, color=color)

    def ensure(self, h):
        if self.y + h > LETTER.height - self.margin:
            self.new_page()

    def space(self, h):
        self.y += h

    def line(self, text, fname="serif", size=11, align="left", lead=1.35):
        self.ensure(size * lead)
        self.y += size
        x = self.margin
        if align != "left":
            w = font(fname).text_length(text, size)
            x = self.margin + (self.width - w) / (2 if align == "center" else 1)
        self.text_at(x, self.y, text, fname, size)
        self.y += size * (lead - 1)

    def para(self, text, fname="serif", size=11, lead=1.35, indent=0, hyph=None, after=6):
        for ln in wrap(text, font(fname), size, self.width - indent, hyph, self.hyphenations):
            self.ensure(size * lead)
            self.y += size
            self.text_at(self.margin + indent, self.y, ln, fname, size)
            self.y += size * (lead - 1)
        self.y += after

    def rule(self):
        self.ensure(8)
        self.page.draw_line((self.margin, self.y + 3), (LETTER.width - self.margin, self.y + 3), width=0.6)
        self.y += 8

    def table(self, rows, widths, fname="sans", size=9, bold_header=True):
        lead = size * 1.6
        for i, row in enumerate(rows):
            self.ensure(lead)
            x = self.margin
            fn = "sans-bold" if (i == 0 and bold_header) else fname
            for cell, w in zip(row, widths):
                if isinstance(w, tuple):  # right aligned
                    w = w[0]
                    tw = font(fn).text_length(cell, size)
                    self.text_at(x + w - tw - 4, self.y + size + 2, cell, fn, size)
                else:
                    self.text_at(x + 2, self.y + size + 2, cell, fn, size)
                x += w
            self.y += lead
            if i == 0:
                self.page.draw_line((self.margin, self.y), (self.margin + sum(w[0] if isinstance(w, tuple) else w for w in widths), self.y), width=0.5)

    def save(self, path: Path):
        if self.footer:
            self._footer()
        save_pdf(self.doc, path, self.title, self.author, self.date)


def wrap(text, f: pymupdf.Font, size, width, hyph=None, log=None):
    """Greedy word wrap. `hyph` maps a word to its allowed break ("ship-ment").

    Words that already contain a hyphen may also break after that hyphen.
    """
    hyph = hyph or {}
    words = text.split()
    lines, cur = [], ""
    i = 0
    while i < len(words):
        w = words[i]
        cand = (cur + " " + w) if cur else w
        if f.text_length(cand, size) <= width:
            cur = cand
            i += 1
            continue
        # try to break the word
        core = w.strip(".,;:()\u201c\u201d\u2019")
        if core.endswith("\u2019s"):
            core = core[:-2]
        split = None
        if core in hyph:
            parts = hyph[core].split("-")
            k0 = w.index(core)
            # longest head that still fits on the current line
            for j in range(len(parts) - 1, 0, -1):
                a = "".join(parts[:j])
                head = w[:k0] + a + "-"
                if cur and f.text_length(cur + " " + head, size) <= width:
                    split = (head, w[k0 + len(a):])
                    break
        elif "-" in w.strip("-") and not any(ch.isdigit() for ch in w):
            k = w.index("-")
            split = (w[: k + 1], w[k + 1:])
        if split and cur and f.text_length(cur + " " + split[0], size) <= width:
            lines.append(cur + " " + split[0])
            if log is not None:
                log.append(split[0] + split[1])
            words[i] = split[1]
            cur = ""
            continue
        lines.append(cur)
        cur = ""
    if cur:
        lines.append(cur)
    return lines


def save_pdf(doc, path: Path, title, author, date):
    """date: 'YYYYMMDDhhmmss'"""
    pdate = f"D:{date}-05'00'"
    doc.set_metadata({"title": title, "author": author, "creator": "adhocdocs corpus generator",
                      "producer": "PyMuPDF", "creationDate": pdate, "modDate": pdate,
                      "subject": "", "keywords": ""})
    doc.subset_fonts()
    doc.save(str(path), garbage=4, deflate=True, no_new_id=True)
    doc.close()


# ---------------------------------------------------------------- scans


def scan_pdf(src: pymupdf.Document, path: Path, seed: int, title: str, date: str,
             dpi=150, skew=0.6, noise=18, quality=60):
    """Rasterize each page of `src` into an image-only PDF that looks scanned."""
    rng = np.random.default_rng(seed)
    out_doc = pymupdf.open()
    for pno, page in enumerate(src):
        angle = skew * (1 if pno % 2 == 0 else -0.7)
        mat = pymupdf.Matrix(dpi / 72, dpi / 72).prerotate(angle)
        pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY, alpha=False)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width].astype(np.int16)
        # paper tone, sensor noise, toner speckles, a faint vertical streak
        a = a - 14
        a = a + rng.normal(0, noise, a.shape).astype(np.int16)
        speck = rng.random(a.shape) < 0.0008
        a[speck] = rng.integers(0, 90, speck.sum())
        col = rng.integers(int(pix.width * 0.6), int(pix.width * 0.9))
        a[:, col:col + 2] -= 25
        a = np.clip(a, 0, 255).astype(np.uint8)
        g = pymupdf.Pixmap(pymupdf.csGRAY, pix.width, pix.height, a.tobytes(), False)
        jpg = g.tobytes(output="jpg", jpg_quality=quality)
        p = out_doc.new_page(width=LETTER.width, height=LETTER.height)
        p.insert_image(p.rect, stream=jpg)
    save_pdf(out_doc, path, title, "", date)


# ---------------------------------------------------------------- docx


def save_docx_reproducibly(document, path: Path, when):
    """Save a python-docx Document with fixed core-property and zip timestamps."""
    cp = document.core_properties
    cp.created = when
    cp.modified = when
    cp.revision = 1
    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)
    zin = zipfile.ZipFile(buf)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in sorted(zin.infolist(), key=lambda i: (i.filename != "[Content_Types].xml", i.filename)):
            data = zin.read(info.filename)
            zi = zipfile.ZipInfo(info.filename, date_time=(when.year, when.month, when.day, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, data)


# ---------------------------------------------------------------- email

EML_POLICY = policy.default.clone(linesep="\r\n", max_line_length=78)


def email_msg(headers: dict, text: str | None = None, html: str | None = None,
              attachments=(), boundary_seed="b"):
    """Build an EmailMessage. attachments: (filename, bytes, maintype, subtype)."""
    m = EmailMessage(policy=EML_POLICY)
    for k, v in headers.items():
        if v is None:
            continue
        m[k] = v
    if text is not None:
        m.set_content(text, cte="quoted-printable")
        if html is not None:
            m.add_alternative(html, subtype="html", cte="quoted-printable")
    elif html is not None:
        m.set_content(html, subtype="html", cte="quoted-printable")
    for fn, data, mt, st in attachments:
        m.add_attachment(data, maintype=mt, subtype=st, filename=fn)
    n = 0
    for part in m.walk():
        if part.is_multipart():
            n += 1
            part.set_boundary(f"=_adhoc_{boundary_seed}_{n:02d}")
    return m


def write_eml(path: Path, msg: EmailMessage):
    path.write_bytes(msg.as_bytes(policy=EML_POLICY))


def quote(text: str) -> str:
    return "\n".join((">" + l) if l.startswith(">") else ("> " + l if l else ">") for l in text.strip("\n").split("\n"))
