"""Word .docx, read from the XML so tracked changes are handled exactly.

``native`` has tracked changes accepted (insertions kept, deletions
dropped). When the file has any, ``markup`` (both, with ``insertion`` and
``deletion`` segments) and ``original`` (before the changes) are made too.
Each paragraph is one line; table rows are lines with tab-separated cells.

The body comes first, as a ``body`` segment. After it, each separated by a
blank line, come what Word shows outside the body, so it can be searched
and quoted too: footnotes, endnotes, comments, then headers and footers.

- **Text boxes and shapes** (``w:txbxContent``, in a drawing or in VML)
  are read where they're anchored: each box's paragraphs are lines after
  the paragraph (or table) holding its anchor, as a ``text-box`` segment.
  A box saved twice, as a drawing and as a VML fallback for older Word
  (``mc:AlternateContent``), is read once, from the first choice.
- **Footnote and endnote marks** are in the text where Word shows them: the
  number at the reference in the body (a ``sup`` style run, and a
  ``note-ref`` segment with the note's ``id``), and again at the start of
  the note (a ``note-mark`` segment). Numbers follow each section's number
  format, start and restart rule (``w:footnotePr``/``w:endnotePr`` in the
  section, else in the settings; Word's defaults are 1, 2, 3 for footnotes
  and i, ii, iii for endnotes, continuous), counting the references in that
  view in order, so the ``original`` view numbers notes as they were.
  Restarting each section is exact. Restarting each page uses the page
  breaks Word recorded the last time it laid the document out
  (``w:lastRenderedPageBreak``), with explicit page breaks and section
  breaks that start a new page; a file no Word has laid out has only the
  explicit ones, so its numbers can run on where Word's restart. A custom
  mark (``w:customMarkFollows``) is the reference's own text and gets no
  number. Each note is a ``footnote`` or ``endnote`` segment with its
  ``id`` and ``mark``, in the order referenced; notes no view references
  are left out.
- **Comments** don't put text in the body. Each is a ``comment`` segment
  with its ``id``, ``author``, ``initials`` and ``date`` (attributes, not
  text: Word shows them beside the comment, not in it), and the commented
  range of the body is a ``comment-anchor`` segment with the same ``id``.
  Threads (``commentsExtended.xml``) are kept together: each thread's
  first comment, in the order of the anchors, then its replies, each with
  ``reply_to`` (the comment it answers) and ``thread`` (the first
  comment's ``id``). A resolved thread's first comment has ``resolved``.
- **Headers and footers:** each distinct part once, in the order sections
  reference them, as a ``header-footer`` segment with ``part`` (``header``
  or ``footer``) and ``type`` (``default``, ``first`` or ``even``). Empty
  ones are left out. A field's text is the result Word last saved for it,
  so a page number in a footer is whatever page it was last computed on.

Note, comment and header-footer segments have a ``title`` ("Footnote 1",
"Comment — J. Smith, 2026-03-02", "Reply — …", "Header") for displays,
which isn't in the text.
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
W_ID, W_TYPE = qn("w:id"), qn("w:type")
W_FNREF, W_ENREF = qn("w:footnoteReference"), qn("w:endnoteReference")
W_FNMARK, W_ENMARK = qn("w:footnoteRef"), qn("w:endnoteRef")
W_CUSTOM = qn("w:customMarkFollows")
W_CMT_START, W_CMT_END, W_CMT_REF = qn("w:commentRangeStart"), qn("w:commentRangeEnd"), qn("w:commentReference")
W_SDT, W_SDTCONTENT = qn("w:sdt"), qn("w:sdtContent")
W_TXBX = qn("w:txbxContent")
W_PAGEBREAK = qn("w:lastRenderedPageBreak")
W_PPR, W_SECTPR = qn("w:pPr"), qn("w:sectPr")
R_ID = qn("r:id")
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
MC_ALT, MC_CHOICE, MC_FALLBACK = MC + "AlternateContent", MC + "Choice", MC + "Fallback"
W14_PARAID = "{http://schemas.microsoft.com/office/word/2010/wordml}paraId"
W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"

NOTE_DEFAULTS = {"footnote": "decimal", "endnote": "lowerRoman"}


def _roman(n: int) -> str:
    out = ""
    for v, r in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                 (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out, n = out + r, n - v
    return out


def _letters(n: int) -> str:
    # Word's letter numbering repeats the letter: a … z, aa … zz, aaa …
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)


SYMBOLS = "*†‡§"


def number_format(n: int, fmt: str) -> str:
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt == "lowerLetter":
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n).upper()
    if fmt == "chicago":
        return SYMBOLS[(n - 1) % 4] * ((n - 1) // 4 + 1)
    return str(n)


class Notes:
    """What a view's walk of the document finds outside the run text: note
    references in order, with their marks, comment anchors, and text boxes
    waiting to be written after the paragraph that holds them.

    ``sections``: per section, in order, ``{kind: (format, start, restart,
    starts a new page)}``."""

    def __init__(self, sections: list):
        self.sections, self.section = sections, 0
        self.count = {"footnote": 0, "endnote": 0}
        self.base = {k: self.props(k)[1] for k in self.count}
        self.refs = {"footnote": [], "endnote": []}      # (id, mark) in order
        self.marks = {}                                   # (kind, id) -> mark
        self.custom = set()                               # (kind, id) with a custom mark
        self.current = None                               # (kind, id) of the note being written
        self.in_body = True                               # page breaks only count in the body
        self.anchor_open, self.anchors = {}, []           # comment id -> start; (id, start, end)
        self.comment_order = []
        self.boxes = []                                   # text boxes (txbxContent) to write

    def props(self, kind: str):
        return self.sections[min(self.section, len(self.sections) - 1)][kind]

    def _restart(self, kind):
        self.count[kind], self.base[kind] = 0, self.props(kind)[1]

    def next_section(self):
        self.section += 1
        for kind in self.count:
            _, _, restart, new_page = self.props(kind)
            if restart == "eachSect" or (restart == "eachPage" and new_page):
                self._restart(kind)

    def page_break(self):
        if not self.in_body:
            return
        for kind in self.count:
            if self.props(kind)[2] == "eachPage":
                self._restart(kind)

    def reference(self, kind: str, nid: str, custom: bool) -> str:
        if (kind, nid) in self.marks:                       # referenced twice (rare): one note
            return "" if (kind, nid) in self.custom else self.marks[(kind, nid)]
        if custom:
            self.custom.add((kind, nid))
            self.marks[(kind, nid)] = ""
            self.refs[kind].append((nid, ""))
            return ""
        n = self.base[kind] + self.count[kind]
        self.count[kind] += 1
        mark = number_format(n, self.props(kind)[0])
        self.marks[(kind, nid)] = mark
        self.refs[kind].append((nid, mark))
        return mark

    def comment(self, cid: str):
        if cid not in self.comment_order:
            self.comment_order.append(cid)


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


def _runs_text(p, mode: str, b: TextBuilder, notes: Notes):
    """Append a paragraph's text. ``mode`` is native, markup or original."""
    def kept(change):
        return (mode == "markup"
                or (mode == "native" and change != "deletion")
                or (mode == "original" and change != "insertion"))

    def put(s, change, style, kind=None, **extra):
        start, end = b.add(s)
        if mode == "markup" and change and end > start:
            b.segment(start, end, change)
        if style and end > start:
            b.styles.append((start, end, style))
        if kind and end > start:
            b.segment(start, end, kind, **extra)

    def walk(el, change, style=frozenset()):
        for child in el:
            tag = child.tag
            if tag in (W_FNREF, W_ENREF):
                if kept(change):
                    kind = "footnote" if tag == W_FNREF else "endnote"
                    nid = child.get(W_ID)
                    custom = child.get(W_CUSTOM) in ("1", "true", "on")
                    put(notes.reference(kind, nid, custom), change, style | {"sup"}, "note-ref", note=kind, id=nid)
            elif tag in (W_FNMARK, W_ENMARK):
                if notes.current and kept(change):
                    kind, nid = notes.current
                    put(notes.marks.get(notes.current, ""), change, style | {"sup"}, "note-mark", note=kind, id=nid)
            elif tag == W_TXBX:
                if kept(change):
                    notes.boxes.append(child)
            elif tag == MC_ALT:
                pick = child.find(MC_CHOICE)
                pick = pick if pick is not None else child.find(MC_FALLBACK)
                if pick is not None:
                    walk(pick, change, style)
            elif tag == W_PAGEBREAK:
                notes.page_break()
            elif tag == W_CMT_START:
                notes.anchor_open[child.get(W_ID)] = b.length
                notes.comment(child.get(W_ID))
            elif tag == W_CMT_END:
                cid = child.get(W_ID)
                if cid in notes.anchor_open:
                    notes.anchors.append((cid, notes.anchor_open.pop(cid), b.length))
            elif tag == W_CMT_REF:
                notes.comment(child.get(W_ID))
            elif tag == W_INS:
                walk(child, "insertion", style)
            elif tag == W_DEL:
                walk(child, "deletion", style)
            elif tag == W_R:
                walk(child, change, _run_style(child))
            elif tag in (W_T, W_DELTEXT, W_TAB, W_BR):
                if not kept(change):
                    continue
                if tag == W_BR and child.get(W_TYPE) == "page":
                    notes.page_break()
                if tag in (W_T, W_DELTEXT):
                    s = child.text or ""
                else:
                    s = "\t" if tag == W_TAB else "\n"
                put(s, change, style)
            elif tag in (W_P, W_TBL):
                continue
            else:
                walk(child, change, style)
    walk(p, None)


def _style(p) -> str:
    ppr = p.find(qn("w:pPr"))
    st = ppr.find(qn("w:pStyle")) if ppr is not None else None
    return (st.get(qn("w:val")) or "") if st is not None else ""


def _blocks(container):
    """Block-level children (paragraphs, tables), looking inside content
    controls, with comment range marks between them."""
    for el in container:
        if el.tag == W_SDT:
            content = el.find(W_SDTCONTENT)
            if content is not None:
                yield from _blocks(content)
        else:
            yield el


def _own(el, tag):
    """Descendants of ``el`` with ``tag`` that aren't in a text box or the
    fallback copy of one (a nested table's rows and cells are its own)."""
    for x in el.iter(tag):
        a = x.getparent()
        while a is not None and a is not el and a.tag not in (W_TXBX, MC_FALLBACK):
            a = a.getparent()
        if a is el:
            yield x


def _write_boxes(mode: str, b: TextBuilder, notes: Notes):
    """The text boxes anchored in what was just written, each on lines of
    its own (boxes in them come after them, the same way)."""
    boxes, notes.boxes = notes.boxes, []
    in_body, notes.in_body = notes.in_body, False
    for box in boxes:
        if not _has_text(box):
            continue
        start = b.length + 1
        _write_blocks(box, mode, b, notes, first=False)
        b.segment(start, b.length, "text-box")
    notes.in_body = in_body


def _write_blocks(container, mode: str, b: TextBuilder, notes: Notes, first=True, body=False):
    def newline():
        nonlocal first
        if not first:
            b.add("\n")
        first = False

    for el in _blocks(container):
        if el.tag == W_P:
            newline()
            s = b.length
            _runs_text(el, mode, b, notes)
            style = _style(el).lower()
            kind = "heading" if style.startswith(("heading", "title")) else "list-item" if "list" in style else "paragraph"
            b.segment(s, b.length, kind)
            _write_boxes(mode, b, notes)
            ppr = el.find(W_PPR)
            if body and ppr is not None and ppr.find(W_SECTPR) is not None:
                notes.next_section()
        elif el.tag == W_TBL:
            ts = None
            for r, tr in enumerate(_own(el, W_TR)):
                newline()
                rs = b.length
                ts = rs if ts is None else ts
                for c, tc in enumerate(_own(tr, W_TC)):
                    if c:
                        b.add("\t")
                    cs = b.length
                    for i, p in enumerate(_own(tc, W_P)):
                        if i:
                            b.add(" ")
                        _runs_text(p, mode, b, notes)
                    b.segment(cs, b.length, "cell", ref=f"{col_name(c)}{r + 1}")
                b.segment(rs, b.length, "row")
            if ts is not None:
                b.segment(ts, b.length, "table")
            _write_boxes(mode, b, notes)
        elif el.tag in (W_CMT_START, W_CMT_END):
            _runs_text([el], mode, b, notes)


def _has_text(el) -> bool:
    return any((t.text or "").strip() for t in el.iter(W_T, W_DELTEXT))


def _build(doc, mode: str) -> TextBuilder:
    b = TextBuilder()
    notes = Notes(doc.sections)
    _write_blocks(doc.body, mode, b, notes, body=True)
    b.segment(0, b.length, "body")
    notes.in_body = False

    def section(kind, el, **extra):
        b.add("\n\n")
        start = b.length
        _write_blocks(el, mode, b, notes)
        b.segment(start, b.length, kind, **extra)

    for kind in ("footnote", "endnote"):
        for nid, mark in notes.refs[kind]:
            el = doc.notes[kind].get(nid)
            if el is None:
                continue
            notes.current = (kind, nid)
            title = f"{kind.capitalize()} {mark}".rstrip()
            section(kind, el, id=nid, mark=mark, title=title)
            notes.current = None
    order = notes.comment_order + [c for c in doc.comments if c not in notes.comment_order]
    replies = {}
    for cid in doc.comments:                    # in the file's order, which is the order written
        if cid in doc.reply_to:
            replies.setdefault(doc.reply_to[cid], []).append(cid)
    written = set()

    def comment(cid, root):
        if cid in written:
            return
        written.add(cid)
        el = doc.comments[cid]
        info = {k: el.get(qn(f"w:{k}")) for k in ("author", "initials", "date")}
        info = {k: v for k, v in info.items() if v}
        who = ", ".join(x for x in (info.get("author"), (info.get("date") or "")[:10]) if x)
        extra = {"reply_to": doc.reply_to[cid], "thread": root} if cid != root else {}
        resolved = cid == root and cid in doc.resolved
        if resolved:
            extra["resolved"] = True
        title = ("Reply" if cid != root else "Comment") + (f" — {who}" if who else "") + (" (resolved)" if resolved else "")
        section("comment", el, id=cid, title=title, **info, **extra)
        for r in replies.get(cid, []):
            comment(r, root)

    for cid in order:
        if cid in doc.comments and cid not in doc.reply_to:
            comment(cid, cid)
    for cid in doc.comments:                    # replies whose thread is missing
        comment(cid, cid)
    for cid, start, end in notes.anchors:
        if cid in doc.comments:
            b.segment(start, end, "comment-anchor", id=cid)
    for part, typ, el in doc.headers_footers:
        title = part.capitalize() + {"default": "", "first": " (first page)", "even": " (even pages)"}.get(typ, f" ({typ})")
        section("header-footer", el, part=part, type=typ, title=title)
    return b


def _note_pr(where, kind: str) -> dict:
    """What a w:footnotePr or w:endnotePr in ``where`` says, if anything."""
    pr = where.find(qn(f"w:{kind}Pr")) if where is not None else None
    if pr is None:
        return {}
    out = {}
    fmt, start, restart = pr.find(qn("w:numFmt")), pr.find(qn("w:numStart")), pr.find(qn("w:numRestart"))
    if fmt is not None and fmt.get(W_VAL):
        out["fmt"] = fmt.get(W_VAL)
    if start is not None and (start.get(W_VAL) or "").isdigit():
        out["start"] = int(start.get(W_VAL))
    if restart is not None and restart.get(W_VAL):
        out["restart"] = restart.get(W_VAL)
    return out


class _Doc:
    """The parts of a .docx the text comes from."""

    def __init__(self, d):
        from docx.oxml import parse_xml
        self.body = d.element.body
        rels = {r.rId: r for r in d.part.rels.values() if not r.is_external}

        def part(name):
            for r in rels.values():
                if r.reltype.rsplit("/", 1)[-1] == name:
                    return parse_xml(r.target_part.blob)
            return None

        self.notes = {}
        for kind in ("footnote", "endnote"):
            root = part(kind + "s")
            self.notes[kind] = {} if root is None else {
                n.get(W_ID): n for n in root.findall(qn(f"w:{kind}"))
                if n.get(W_TYPE) in (None, "normal")}
        root = part("comments")
        self.comments = {} if root is None else {c.get(W_ID): c for c in root.findall(qn("w:comment"))}

        # threads: commentsExtended names each comment by its last paragraph's paraId
        self.reply_to, self.resolved = {}, set()
        ext = part("commentsExtended")
        if ext is not None:
            by_para = {}
            for cid, c in self.comments.items():
                ps = c.findall(W_P)
                if ps and ps[-1].get(W14_PARAID):
                    by_para[ps[-1].get(W14_PARAID)] = cid
            for ex in ext.findall(W15 + "commentEx"):
                cid = by_para.get(ex.get(W15 + "paraId"))
                parent = by_para.get(ex.get(W15 + "paraIdParent"))
                if cid is None:
                    continue
                if parent is not None and parent != cid:
                    self.reply_to[cid] = parent
                if ex.get(W15 + "done") in ("1", "true"):
                    self.resolved.add(cid)
            for cid in list(self.reply_to):     # a loop of replies would never be written
                seen, c = {cid}, self.reply_to[cid]
                while c in self.reply_to and c not in seen:
                    seen.add(c)
                    c = self.reply_to[c]
                if c in seen:
                    del self.reply_to[cid]

        # notes' number format, start and restart, per section: the
        # section's own, else the settings', else Word's defaults
        settings = part("settings")
        doc_default = {}
        for kind in ("footnote", "endnote"):
            d = {"fmt": NOTE_DEFAULTS[kind], "start": 1, "restart": "continuous"}
            d.update(_note_pr(settings, kind))
            doc_default[kind] = d
        sects = [sp for sp in self.body.iter(W_SECTPR)
                 if sp.getparent() is self.body
                 or (sp.getparent().tag == W_PPR and sp.getparent().getparent().tag == W_P)]
        self.sections = []
        for sp in sects or [None]:
            typ = sp.find(W_TYPE) if sp is not None else None
            new_page = (typ.get(W_VAL) if typ is not None else "nextPage") != "continuous"
            sec = {}
            for kind in ("footnote", "endnote"):
                d = {**doc_default[kind], **_note_pr(sp, kind)}
                sec[kind] = (d["fmt"], d["start"], d["restart"], new_page)
            self.sections.append(sec)

        self.headers_footers, seen = [], set()
        for sp in self.body.iter(qn("w:sectPr")):
            for ref in sp:
                kind = {qn("w:headerReference"): "header", qn("w:footerReference"): "footer"}.get(ref.tag)
                rid = ref.get(R_ID)
                if not kind or rid in seen or rid not in rels:
                    continue
                seen.add(rid)
                el = parse_xml(rels[rid].target_part.blob)
                if _has_text(el):
                    self.headers_footers.append((kind, ref.get(W_TYPE) or "default", el))

    def parts(self):
        yield self.body
        for kind in self.notes:
            yield from self.notes[kind].values()
        yield from self.comments.values()
        for _, _, el in self.headers_footers:
            yield el


def extract_docx(data: bytes) -> Extraction:
    d = docx.Document(io.BytesIO(data))
    doc = _Doc(d)
    has_changes = any(el.find(f".//{W_INS}") is not None or el.find(f".//{W_DEL}") is not None
                      for el in doc.parts())
    layers = []
    for mode in ("native", "markup", "original") if has_changes else ("native",):
        layer = _build(doc, mode).layer(mode, primary=mode == "native")
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
        "footnotes": len(doc.notes["footnote"]) or None,
        "endnotes": len(doc.notes["endnote"]) or None,
        "comments": len(doc.comments) or None,
    }.items() if v}
    return Extraction(kind="docx", layers=layers, metadata=meta)
