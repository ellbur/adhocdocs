"""Plain text, HTML and CSV."""

import csv
import io

from bs4 import BeautifulSoup

from . import Extraction, TextBuilder, text_presentation


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def extract_text(data: bytes) -> Extraction:
    b = TextBuilder()
    b.add(decode(data))
    return Extraction(kind="text", layers=[b.layer("native", primary=True)])


def html_to_text(markup: str, b: TextBuilder):
    """Rendered text of HTML: one line per block element, scripts and styles dropped."""
    soup = BeautifulSoup(markup, "lxml")
    for tag in soup(["script", "style", "head", "noscript", "template"]):
        tag.decompose()
    lines = [line.strip() for line in soup.get_text("\n").split("\n")]
    out = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    text = "\n".join(out).strip("\n")
    return b.add(text)


def extract_html(data: bytes) -> Extraction:
    markup = decode(data)
    soup = BeautifulSoup(markup, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else None
    b = TextBuilder()
    html_to_text(markup, b)
    layer = b.layer("native", primary=True)
    layer.presentation = text_presentation(layer.text, layer.segments)
    return Extraction(kind="html", layers=[layer], metadata={"title": title} if title else {})


def col_name(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def extract_csv(name: str | None, data: bytes) -> Extraction:
    """One sheet; rows are lines, cells separated by tabs. Tabs and line
    breaks inside a cell become spaces so the row/line rule holds."""
    b = TextBuilder()
    rows = list(csv.reader(io.StringIO(decode(data))))
    sheet = (name or "sheet").rsplit("/", 1)[-1]
    start = b.length
    for r, row in enumerate(rows):
        if r:
            b.add("\n")
        rs = b.length
        for c, cell in enumerate(row):
            if c:
                b.add("\t")
            cs, ce = b.add(cell.replace("\t", " ").replace("\n", " "))
            b.segment(cs, ce, "cell", ref=f"{col_name(c)}{r + 1}")
        b.segment(rs, b.length, "row")
    b.segment(start, b.length, "sheet", name=sheet)
    return Extraction(kind="spreadsheet", layers=[b.layer("native", primary=True)])
