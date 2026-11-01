"""Word .docx: what's outside the body (footnotes, endnotes, comments and
their threads, headers and footers, text boxes), note numbering, and
tracked changes in all of it."""

import io
import zipfile

from adhocdocs.extract import extract
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter
from adhocdocs.search import Options, Searcher

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ' \
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" ' \
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" ' \
    'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml" ' \
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" ' \
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" ' \
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" ' \
    'xmlns:v="urn:schemas-microsoft-com:vml" mc:Ignorable="w14 w15"'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
CT = "application/vnd.openxmlformats-officedocument.wordprocessingml."


def p(*runs) -> str:
    return "<w:p>" + "".join(runs) + "</w:p>"


def r(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def fn_ref(n, kind="footnote") -> str:
    return f'<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:{kind}Reference w:id="{n}"/></w:r>'


def note(n, text, kind="footnote") -> str:
    return (f'<w:{kind} w:id="{n}"><w:p><w:r><w:{kind}Ref/></w:r>{r(" " + text)}</w:p></w:{kind}>')


def make_docx(body: str, sect: str = "", footnotes=(), endnotes=(), comments=(), parts=(), settings="",
              comments_ext=()) -> bytes:
    """A .docx package from raw WordprocessingML. parts: (name, root tag,
    body xml) for headers and footers, referenced from ``sect`` by name."""
    rels = [("styles", "styles.xml")]
    types = [("/word/document.xml", CT + "document.main+xml"), ("/word/styles.xml", CT + "styles+xml")]
    files = {"word/styles.xml": f'<w:styles {W}/>'}

    def add(kind, name, xml, ctype):
        rid = f"rId{len(rels) + 1}"
        rels.append((kind, name))
        files["word/" + name] = xml
        types.append(("/word/" + name, CT + ctype))
        return rid

    sep = '<w:{0} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:{0}>'
    if footnotes:
        add("footnotes", "footnotes.xml", f'<w:footnotes {W}>{sep.format("footnote")}{"".join(footnotes)}</w:footnotes>', "footnotes+xml")
    if endnotes:
        add("endnotes", "endnotes.xml", f'<w:endnotes {W}>{sep.format("endnote")}{"".join(endnotes)}</w:endnotes>', "endnotes+xml")
    if comments:
        add("comments", "comments.xml", f'<w:comments {W}>{"".join(comments)}</w:comments>', "comments+xml")
    if comments_ext:
        rid = f"rId{len(rels) + 1}"
        rels.append(("http://schemas.microsoft.com/office/2011/relationships/commentsExtended", "commentsExtended.xml"))
        files["word/commentsExtended.xml"] = f'<w15:commentsEx {W}>{"".join(comments_ext)}</w15:commentsEx>'
        types.append(("/word/commentsExtended.xml", CT + "commentsExtended+xml"))
    if settings:
        add("settings", "settings.xml", f'<w:settings {W}>{settings}</w:settings>', "settings+xml")
    for name, tag, xml in parts:
        rid = add("header" if tag == "hdr" else "footer", name, f"<w:{tag} {W}>{xml}</w:{tag}>",
                  "header+xml" if tag == "hdr" else "footer+xml")
        sect = sect.replace(f'"{name}"', f'"{rid}"')
    files["word/document.xml"] = f'<w:document {W}><w:body>{body}<w:sectPr>{sect}</w:sectPr></w:body></w:document>'
    files["word/_rels/document.xml.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i}" Type="{k if k.startswith("http") else REL + k}" Target="{t}"/>'
                  for i, (k, t) in enumerate(rels, 1))
        + "</Relationships>")
    files["_rels/.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{REL}officeDocument" Target="word/document.xml"/></Relationships>')
    files["[Content_Types].xml"] = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(f'<Override PartName="{n}" ContentType="{c}"/>' for n, c in types) + "</Types>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, xml in files.items():
            z.writestr(name, xml)
    return buf.getvalue()


def segs(layer, kind):
    return [s for s in layer.segments if s["kind"] == kind]


def test_footnotes_numbered_in_order_of_reference():
    data = make_docx(p(r("Delivered late"), fn_ref(7), r(" and damaged"), fn_ref(3), r(".")),
                     footnotes=[note(3, "The pallet wrap said HFS."), note(7, "Per the bill of lading."),
                                note(9, "Never referenced.")])
    ext = extract("x.docx", data)
    layer = ext.layers[0]
    assert layer.text == ("Delivered late1 and damaged2.\n\n"
                          "1 Per the bill of lading.\n\n"
                          "2 The pallet wrap said HFS.")
    assert [(s["id"], s["mark"], layer.text[s["start"]:s["end"]]) for s in segs(layer, "footnote")] == [
        ("7", "1", "1 Per the bill of lading."), ("3", "2", "2 The pallet wrap said HFS.")]
    assert [(s["id"], layer.text[s["start"]:s["end"]]) for s in segs(layer, "note-ref")] == [("7", "1"), ("3", "2")]
    body, = segs(layer, "body")
    assert layer.text[body["start"]:body["end"]] == "Delivered late1 and damaged2."
    assert {layer.text[a:e] for a, e, st in layer.styles if "sup" in st} == {"1", "2"}
    assert ext.metadata["footnotes"] == 3


def test_endnotes_and_number_formats():
    data = make_docx(p(r("A"), fn_ref(1, "endnote"), r(" B"), fn_ref(2, "endnote"), r(" C"), fn_ref(1)),
                     endnotes=[note(1, "First.", "endnote"), note(2, "Second.", "endnote")],
                     footnotes=[note(1, "Starred.")],
                     settings='<w:footnotePr><w:numFmt w:val="chicago"/></w:footnotePr>'
                              '<w:endnotePr><w:numStart w:val="4"/></w:endnotePr>')
    layer = extract("x.docx", data).layers[0]
    assert layer.text == "Aiv Bv C*\n\n* Starred.\n\niv First.\n\nv Second."
    assert [s["title"] for s in segs(layer, "endnote")] == ["Endnote iv", "Endnote v"]


def test_custom_footnote_mark():
    data = make_docx(p(r("See"), '<w:r><w:footnoteReference w:customMarkFollows="1" w:id="1"/><w:t>†</w:t></w:r>', r(" now."),
                       fn_ref(2)),
                     footnotes=['<w:footnote w:id="1"><w:p>' + r("† Custom.") + "</w:p></w:footnote>", note(2, "Numbered.")])
    layer = extract("x.docx", data).layers[0]
    assert layer.text == "See† now.1\n\n† Custom.\n\n1 Numbered."


def test_comments_in_anchor_order_with_their_ranges():
    body = p(r("The "), '<w:commentRangeStart w:id="5"/>', r("goods arrived"), '<w:commentRangeEnd w:id="5"/>',
             '<w:r><w:commentReference w:id="5"/></w:r>', r(" on "), '<w:commentRangeStart w:id="2"/>', r("March 9"),
             '<w:commentRangeEnd w:id="2"/>', '<w:r><w:commentReference w:id="2"/></w:r>', r("."))
    comments = ['<w:comment w:id="2" w:author="Raymond Voss" w:initials="RV" w:date="2026-03-12T10:00:00Z">'
                + p(r("Wasn't it the 10th?")) + "</w:comment>",
                '<w:comment w:id="5" w:author="Thomas Kettering" w:date="2026-03-11T09:00:00Z">'
                + p('<w:r><w:annotationRef/></w:r>', r("Damaged, per the dock log.")) + "</w:comment>"]
    ext = extract("x.docx", make_docx(body, comments=comments))
    layer = ext.layers[0]
    assert layer.text == "The goods arrived on March 9.\n\nDamaged, per the dock log.\n\nWasn't it the 10th?"
    cs = segs(layer, "comment")
    assert [(c["id"], c["author"], layer.text[c["start"]:c["end"]]) for c in cs] == [
        ("5", "Thomas Kettering", "Damaged, per the dock log."), ("2", "Raymond Voss", "Wasn't it the 10th?")]
    assert cs[1]["initials"] == "RV" and cs[1]["title"] == "Comment — Raymond Voss, 2026-03-12"
    assert {a["id"]: layer.text[a["start"]:a["end"]] for a in segs(layer, "comment-anchor")} == {
        "5": "goods arrived", "2": "March 9"}
    assert ext.metadata["comments"] == 2


def test_headers_and_footers_once_each():
    sect = ('<w:headerReference w:type="default" r:id="h1.xml"/><w:headerReference w:type="first" r:id="h2.xml"/>'
            '<w:footerReference w:type="default" r:id="f1.xml"/><w:footerReference w:type="even" r:id="f2.xml"/>')
    body = (p(r("Section one.")) + f'<w:p><w:pPr><w:sectPr>{sect}</w:sectPr></w:pPr></w:p>' + p(r("Section two.")))
    data = make_docx(body, sect=sect, parts=[
        ("h1.xml", "hdr", p(r("PRIVILEGED &amp; CONFIDENTIAL"))), ("h2.xml", "hdr", p(r("Draft"))),
        ("f1.xml", "ftr", p(r("Page "), '<w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>')),
        ("f2.xml", "ftr", p())])
    layer = extract("x.docx", data).layers[0]
    assert layer.text == "Section one.\n\nSection two.\n\nPRIVILEGED & CONFIDENTIAL\n\nDraft\n\nPage 1"
    assert [(s["part"], s["type"], s["title"]) for s in segs(layer, "header-footer")] == [
        ("header", "default", "Header"), ("header", "first", "Header (first page)"), ("footer", "default", "Footer")]


def test_tracked_changes_in_footnotes_and_their_numbering():
    ins = ('<w:ins w:id="90" w:author="RV" w:date="2026-03-01T00:00:00Z">' + fn_ref(1) + "</w:ins>")
    data = make_docx(p(r("Shipped"), ins, r(" Monday"), fn_ref(2), r(".")),
                     footnotes=[note(1, "Added later."),
                                '<w:footnote w:id="2"><w:p><w:r><w:footnoteRef/></w:r>' + r(" Per ")
                                + '<w:del w:id="91" w:author="RV" w:date="2026-03-01T00:00:00Z"><w:r><w:delText>Jones</w:delText></w:r></w:del>'
                                + '<w:ins w:id="92" w:author="RV" w:date="2026-03-01T00:00:00Z">' + r("Smith") + "</w:ins></w:p></w:footnote>"])
    ext = extract("x.docx", data)
    views = {l.view: l.text for l in ext.layers}
    assert views["native"] == "Shipped1 Monday2.\n\n1 Added later.\n\n2 Per Smith"
    assert views["original"] == "Shipped Monday1.\n\n1 Per Jones"
    markup = next(l for l in ext.layers if l.view == "markup")
    assert markup.text == "Shipped1 Monday2.\n\n1 Added later.\n\n2 Per JonesSmith"
    assert {markup.text[s["start"]:s["end"]] for s in segs(markup, "deletion")} == {"Jones"}


def test_content_controls_are_read():
    data = make_docx('<w:sdt><w:sdtPr/><w:sdtContent>' + p(r("Inside a content control.")) + "</w:sdtContent></w:sdt>"
                     + p(r("After it.")))
    assert extract("x.docx", data).layers[0].text == "Inside a content control.\nAfter it."


def test_search_reaches_notes_and_can_leave_them_out(tmp_path):
    m = Matter.create(tmp_path / "m")
    data = make_docx(p(r("Delivered on time"), fn_ref(1), r(".")), footnotes=[note(1, "Except the HFS pallet.")])
    doc = ingest_bytes(m, data, "letter.docx", {"custodian": "cust:a", "source": {"collection": "c", "path": "letter.docx"}})
    m = Matter(m.root)
    s = Searcher(m, Options())
    _, items = s.finish(*s.keyword("HFS"))
    assert [i["loc"] for i in items] == ["doc:" + doc]
    assert [h["in"] for h in items[0]["hits"]] == [["footnote"]]
    _, items = Searcher(m, Options(in_kinds=["body"])).finish(*Searcher(m, Options(in_kinds=["body"])).keyword("HFS"))
    assert items == []


def text_box(*paras, vml_too=True) -> str:
    """A run holding a drawing text box, saved (as Word does) with a VML copy
    for older versions."""
    content = "<w:txbxContent>" + "".join(paras) + "</w:txbxContent>"
    choice = (f'<mc:Choice Requires="wps"><w:drawing><wp:anchor><a:graphic><a:graphicData><wps:wsp>'
              f"<wps:txbx>{content}</wps:txbx></wps:wsp></a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>")
    fallback = f"<mc:Fallback><w:pict><v:shape><v:textbox>{content}</v:textbox></v:shape></w:pict></mc:Fallback>"
    return f"<w:r><mc:AlternateContent>{choice}{fallback if vml_too else ''}</mc:AlternateContent></w:r>"


def test_text_boxes_after_their_paragraph_once():
    body = (p(r("Exhibit A shows the pallet."), text_box(p(r("Callout: HFS on the wrap")), p(r("second line"))))
            + p(r("Next paragraph.")))
    layer = extract("x.docx", make_docx(body)).layers[0]
    assert layer.text == "Exhibit A shows the pallet.\nCallout: HFS on the wrap\nsecond line\nNext paragraph."
    box, = segs(layer, "text-box")
    assert layer.text[box["start"]:box["end"]] == "Callout: HFS on the wrap\nsecond line"


def test_vml_only_text_boxes_and_boxes_in_tables():
    vml = '<w:r><w:pict><v:shape><v:textbox><w:txbxContent>' + p(r("Old Word box")) + "</w:txbxContent></v:textbox></v:shape></w:pict></w:r>"
    table = ("<w:tbl><w:tr><w:tc>" + p(r("Cell"), text_box(p(r("Box in a cell")))) + "</w:tc><w:tc>"
             + p(r("Other")) + "</w:tc></w:tr></w:tbl>")
    layer = extract("x.docx", make_docx(p(r("Para"), vml) + table)).layers[0]
    assert layer.text == "Para\nOld Word box\nCell\tOther\nBox in a cell"


def test_a_deleted_text_box_is_only_in_markup_and_original():
    deleted = '<w:del w:id="1" w:author="RV" w:date="2026-03-01T00:00:00Z">' + text_box(p(r("Removed box"))) + "</w:del>"
    views = {l.view: l.text for l in extract("x.docx", make_docx(p(r("Text"), deleted))).layers}
    assert views == {"native": "Text", "markup": "Text\nRemoved box", "original": "Text\nRemoved box"}


def comment(cid, author, text, para_id, date="2026-03-11T09:00:00Z") -> str:
    return (f'<w:comment w:id="{cid}" w:author="{author}" w:date="{date}">'
            f'<w:p w14:paraId="{para_id}">{r(text)}</w:p></w:comment>')


def test_comment_replies_follow_their_thread():
    anchor = lambda cid, text: (f'<w:commentRangeStart w:id="{cid}"/>{r(text)}<w:commentRangeEnd w:id="{cid}"/>'
                                f'<w:r><w:commentReference w:id="{cid}"/></w:r>')
    body = p(anchor(0, "first"), r(" "), anchor(3, "second"))
    comments = [comment(0, "Raymond Voss", "Is this right?", "0A"),
                comment(1, "Thomas Kettering", "Yes, per the dock log.", "1A"),
                comment(3, "Raymond Voss", "Separate point.", "3A"),
                comment(2, "Raymond Voss", "Thanks.", "2A")]
    ext = ['<w15:commentEx w15:paraId="0A" w15:done="1"/>', '<w15:commentEx w15:paraId="1A" w15:paraIdParent="0A"/>',
           '<w15:commentEx w15:paraId="3A"/>', '<w15:commentEx w15:paraId="2A" w15:paraIdParent="0A"/>']
    layer = extract("x.docx", make_docx(body, comments=comments, comments_ext=ext)).layers[0]
    assert layer.text == "first second\n\nIs this right?\n\nYes, per the dock log.\n\nThanks.\n\nSeparate point."
    cs = segs(layer, "comment")
    assert [(c["id"], c.get("reply_to"), c.get("thread"), c.get("resolved")) for c in cs] == [
        ("0", None, None, True), ("1", "0", "0", None), ("2", "0", "0", None), ("3", None, None, None)]
    assert [c["title"] for c in cs] == ["Comment — Raymond Voss, 2026-03-11 (resolved)", "Reply — Thomas Kettering, 2026-03-11",
                                        "Reply — Raymond Voss, 2026-03-11", "Comment — Raymond Voss, 2026-03-11"]


def section_break(pr="", typ=None) -> str:
    t = f'<w:type w:val="{typ}"/>' if typ else ""
    return f"<w:p><w:pPr><w:sectPr>{t}{pr}</w:sectPr></w:pPr></w:p>"


def test_footnotes_restart_each_section():
    restart = '<w:footnotePr><w:numRestart w:val="eachSect"/></w:footnotePr>'
    body = (p(r("A"), fn_ref(1), r(" B"), fn_ref(2)) + section_break(restart)
            + p(r("C"), fn_ref(3)) + section_break('<w:footnotePr><w:numFmt w:val="lowerLetter"/><w:numRestart w:val="eachSect"/></w:footnotePr>')
            + p(r("D"), fn_ref(4)))
    data = make_docx(body, sect=restart, footnotes=[note(i, f"Note {i}.") for i in range(1, 5)])
    layer = extract("x.docx", data).layers[0]
    body, = segs(layer, "body")
    assert layer.text[body["start"]:body["end"]] == "A1 B2\n\nCa\n\nD1"     # a sectPr describes the section it ends
    assert [s["mark"] for s in segs(layer, "footnote")] == ["1", "2", "a", "1"]


def test_footnotes_restart_each_page_where_word_broke_pages():
    each_page = '<w:footnotePr><w:numRestart w:val="eachPage"/></w:footnotePr>'
    body = (p(r("A"), fn_ref(1), r(" B"), fn_ref(2))
            + p('<w:r><w:lastRenderedPageBreak/><w:t>C</w:t></w:r>', fn_ref(3))
            + p(r("D"), fn_ref(4), '<w:r><w:br w:type="page"/></w:r>', r("E"), fn_ref(5))
            + p(r("F"), fn_ref(6), '<w:r><w:br/></w:r>', r("G"), fn_ref(7))
            + section_break(each_page) + p(r("H"), fn_ref(8))
            + section_break(each_page, "continuous") + p(r("I"), fn_ref(9)))
    data = make_docx(body, sect=each_page, settings=each_page,
                     footnotes=[note(i, f"Note {i}.") for i in range(1, 10)])
    layer = extract("x.docx", data).layers[0]
    # a section's sectPr ends it and says how it began: H's section began on the
    # same page (continuous), I's on a new one (nextPage, the default)
    assert [s["mark"] for s in segs(layer, "footnote")] == ["1", "2", "1", "2", "1", "2", "3", "4", "1"]


def test_note_numbers_are_raised_in_the_presentation():
    layer = extract("x.docx", make_docx(p(r("Late"), fn_ref(1), r(".")), footnotes=[note(1, "Per the log.")])).layers[0]
    assert '<span data-o="0">Late</span><sup><span data-o="4">1</span></sup><span data-o="5">.</span>' in layer.presentation
    assert '<sup><span data-o="8">1</span></sup><span data-o="9"> Per the log.</span>' in layer.presentation
    assert [(s["kind"], layer.text[s["start"]:s["end"]]) for s in layer.segments if s["kind"] in ("note-ref", "note-mark")] == [
        ("note-ref", "1"), ("note-mark", "1")]
