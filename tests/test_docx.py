"""Word .docx: what's outside the body (footnotes, endnotes, comments,
headers and footers), and tracked changes in it."""

import io
import zipfile

from adhocdocs.extract import extract
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter
from adhocdocs.search import Options, Searcher

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
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


def make_docx(body: str, sect: str = "", footnotes=(), endnotes=(), comments=(), parts=(), settings="") -> bytes:
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
    if settings:
        add("settings", "settings.xml", f'<w:settings {W}>{settings}</w:settings>', "settings+xml")
    for name, tag, xml in parts:
        rid = add("header" if tag == "hdr" else "footer", name, f"<w:{tag} {W}>{xml}</w:{tag}>",
                  "header+xml" if tag == "hdr" else "footer+xml")
        sect = sect.replace(f'"{name}"', f'"{rid}"')
    files["word/document.xml"] = f'<w:document {W}><w:body>{body}<w:sectPr>{sect}</w:sectPr></w:body></w:document>'
    files["word/_rels/document.xml.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i}" Type="{REL}{k}" Target="{t}"/>' for i, (k, t) in enumerate(rels, 1))
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
