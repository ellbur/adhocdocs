"""Plain text, HTML and CSV."""

import csv
import io
import re

from bs4 import BeautifulSoup, CData, NavigableString

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


def extract_structured(kind: str, data: bytes) -> Extraction:
    """JSON, JSON Lines or XML: the literal text, like any text file, plus
    what its syntax says (``valid``, its top-level ``shape`` or number of
    ``records``). ``ptr=`` locators name values in JSON (adhocdocs.structured)."""
    from .. import structured
    b = TextBuilder()
    b.add(decode(data))
    layer = b.layer("native", primary=True)
    meta = {"format": kind}
    try:
        if kind == "xml":
            from lxml import etree
            etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True))
        else:
            value = structured.loads(layer.text, kind)
            meta["shape"] = structured.type_of(value) if kind == "json" else "records"
            if kind == "ndjson":
                meta["records"] = len(value)
        meta["valid"] = True
    except Exception as e:              # still the file's text; just not well-formed
        meta["valid"] = False
        meta["syntax_error"] = str(e)[:200]
    return Extraction(kind=kind, layers=[layer], metadata=meta)


STYLE_TAGS = {"b": "b", "strong": "b", "i": "i", "em": "i", "cite": "i", "u": "u", "ins": "u",
              "s": "s", "strike": "s", "del": "s", "sup": "sup", "sub": "sub"}


def _string_style(node) -> frozenset:
    out = set()
    for p in node.parents:
        if p.name in STYLE_TAGS:
            out.add(STYLE_TAGS[p.name])
        css = (p.get("style") or "").replace(" ", "").lower()
        if "font-weight:bold" in css or re.search(r"font-weight:[6-9]00", css):
            out.add("b")
        if "font-style:italic" in css:
            out.add("i")
        if "underline" in css:
            out.add("u")
        if "line-through" in css:
            out.add("s")
    return frozenset(out)


def _html_styles(soup, text: str) -> list:
    """Formatting runs for ``text`` (as html_to_text makes it), by following
    each character back to the string it came from. Empty if that doesn't
    reproduce the text exactly."""
    strings = [x for x in soup.descendants if type(x) in (NavigableString, CData)]
    chars = []
    for i, x in enumerate(strings):
        if i:
            chars.append(("\n", None))
        chars += [(c, i) for c in str(x)]
    lines, line = [], []
    for c in chars:
        if c[0] == "\n":
            lines.append(line)
            line = []
        else:
            line.append(c)
    lines.append(line)
    out = []
    for ln in lines:
        a, z = 0, len(ln)
        while a < z and ln[a][0].isspace():
            a += 1
        while z > a and ln[z - 1][0].isspace():
            z -= 1
        ln = ln[a:z]
        if ln or (out and out[-1]):
            out.append(ln)
    flat = []
    for i, ln in enumerate(out):
        if i:
            flat.append(("\n", None))
        flat += ln
    while flat and flat[0][0] == "\n":
        flat.pop(0)
    while flat and flat[-1][0] == "\n":
        flat.pop()
    if "".join(c for c, _ in flat) != text:
        return []
    styles, cache = [], {}
    for pos, (_, origin) in enumerate(flat):
        st = frozenset() if origin is None else cache.setdefault(origin, _string_style(strings[origin]))
        if styles and styles[-1][2] == st and styles[-1][1] == pos:
            styles[-1][1] = pos + 1
        else:
            styles.append([pos, pos + 1, st])
    return [(a, e, st) for a, e, st in styles if st]


def _soup(markup: str):
    soup = BeautifulSoup(markup, "lxml")
    for tag in soup(["script", "style", "head", "noscript", "template"]):
        tag.decompose()
    return soup


def _lines(soup, mark: str | None = None) -> tuple[list[str], dict[int, list[str]]]:
    """The output lines, and for text with markers (``mark`` + key + ``mark``,
    each a string of its own), the keys before each output line."""
    out, before = [], {}
    pending = []
    for line in soup.get_text("\n").split("\n"):
        line = line.strip()
        if mark and line.startswith(mark) and line.endswith(mark) and len(line) > 1:
            pending.append(line[1:-1])
            continue
        if line or (out and out[-1]):
            if pending:
                before[len(out)], pending = pending, []
            out.append(line)
    if pending:
        before[len(out)] = pending
    return out, before


IMAGE_MARK = ""      # private use: never in a message's own text


def html_to_text(markup: str, b: TextBuilder, images: list | None = None):
    """Rendered text of HTML: one line per block element, scripts and styles
    dropped. Its formatting (bold, italic, …) goes to ``b.styles``.

    With ``images``, each ``<img src="cid:…">`` adds ``(offset, cid)`` to it:
    where the image was, at the start of the line after it. Images add no
    text."""
    soup = _soup(markup)
    out, _ = _lines(soup)
    text = "\n".join(out).strip("\n")
    start, end = b.add(text)
    exact = b.text[start:end] == text        # add() cleans; offsets only hold if it changed nothing
    if exact:
        b.styles += [(start + a, start + e, st) for a, e, st in _html_styles(soup, text)]
    if images is not None:
        marked = _soup(markup)
        cids = []
        for img in marked.find_all("img"):
            src = (img.get("src") or "").strip()
            if src[:4].lower() == "cid:" and src[4:]:
                img.replace_with(NavigableString(f"{IMAGE_MARK}{len(cids)}{IMAGE_MARK}"))
                cids.append(src[4:])
        if cids:
            lines, before = _lines(marked, IMAGE_MARK)
            offsets, pos = [], 0
            for line in lines:
                offsets.append(pos)
                pos += len(line) + 1
            offsets.append(pos)
            same = exact and lines == out
            for k, keys in sorted(before.items()):
                at = start + min(offsets[k], len(text)) if same else end
                images += [(at, cids[int(key)]) for key in keys]
    return start, end


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


def extract_xlsx(data: bytes) -> Extraction:
    """Each sheet a ``sheet`` segment; rows are lines and cells are separated
    by tabs, each cell with a ``cell`` segment and its A1 ``ref``. The text
    holds the values Excel last calculated, not formulas."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    b = TextBuilder()
    for n, ws in enumerate(wb.worksheets):
        if n:
            b.raw("\f")          # one sheet per page, so page= selects a sheet
        start = b.length
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            if r:
                b.add("\n")
            rs = b.length
            for c, value in enumerate(row):
                if c:
                    b.add("\t")
                text = "" if value is None else str(value)
                cs, ce = b.add(text.replace("\t", " ").replace("\n", " "))
                if ce > cs:
                    b.segment(cs, ce, "cell", ref=f"{col_name(c)}{r + 1}")
            b.segment(rs, b.length, "row")
        b.segment(start, b.length, "sheet", name=ws.title)
    props = wb.properties
    meta = {k: v for k, v in {"title": props.title, "author": props.creator,
                              "date": props.created.isoformat() if props.created else None,
                              "date_source": "created" if props.created else None,
                              "created": props.created.isoformat() if props.created else None,
                              "modified": props.modified.isoformat() if props.modified else None,
                              "sheets": len(wb.worksheets)}.items() if v}
    wb.close()
    return Extraction(kind="spreadsheet", layers=[b.layer("native", primary=True)], metadata=meta)
