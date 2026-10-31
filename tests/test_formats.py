import io
import mailbox
import zipfile
from pathlib import Path

import openpyxl

from adhocdocs.extract import extract, kind_for
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter
from adhocdocs.search import Options, Searcher

CORPUS = Path(__file__).parent.parent / "corpus"
EML = CORPUS / "muller-z" / "2026-03-09_RE_INV-2026-0347_past-due.eml"
DOCX = CORPUS / "muller-z" / "master-supply-agreement-2025-11-03.docx"


def xlsx_bytes():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Q1"
    ws.append(["Invoice", "Amount", "Note"])
    ws.append(["INV-2026/0347", 48612.5, "never received"])
    ws2 = wb.create_sheet("Q2")
    ws2.append(["nothing", None, "here"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx():
    e = extract("book.xlsx", xlsx_bytes())
    layer = e.layers[0]
    assert e.kind == "spreadsheet" and e.metadata["sheets"] == 2
    assert layer.text == "Invoice\tAmount\tNote\nINV-2026/0347\t48612.5\tnever received\fnothing\t\there"
    cells = {s["ref"]: layer.text[s["start"]:s["end"]] for s in layer.segments if s["kind"] == "cell"}
    assert cells["C2"] == "never received" and cells["B2"] == "48612.5"
    assert [s["name"] for s in layer.segments if s["kind"] == "sheet"] == ["Q1", "Q2"]


def test_sniffing_zips():
    assert kind_for(None, DOCX.read_bytes()) == "docx"
    assert kind_for(None, xlsx_bytes()) == "xlsx"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "hello")
    assert kind_for(None, buf.getvalue()) == "zip"


def test_zip_and_mbox_become_families(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mail/one.eml", EML.read_bytes())
        z.writestr("book.xlsx", xlsx_bytes())
        z.writestr("__MACOSX/._junk", b"x")
        z.writestr("empty/", b"")
    box_path = tmp_path / "box.mbox"
    box = mailbox.mbox(box_path)
    box.add(EML.read_bytes())
    box.add(EML.read_bytes().replace(b"nothing to pay yet", b"nothing to see"))
    box.close()
    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as z:          # a mailbox inside an archive
        z.writestr("export/box.mbox", box_path.read_bytes())

    m = Matter.create(tmp_path / "m")
    top = ingest_bytes(m, buf.getvalue(), "evidence.zip", {"custodian": "cust:a", "source": {"collection": "c", "path": "evidence.zip"}})
    nested = ingest_bytes(m, buf2.getvalue(), "export.zip", {"custodian": "cust:a", "source": {"collection": "c", "path": "export.zip"}})
    m = Matter(m.root)
    kids = m.state.children[top]
    assert len(kids) == 2
    box_doc = m.state.children[nested][0]
    assert len(m.state.children[box_doc]) == 2

    s = Searcher(m, Options())
    _, items = s.finish(*s.keyword('"never received"'))
    assert len(items) == 4                      # the email in the zip, the workbook, both mailbox messages
    _, items = s.finish(*s.keyword('"nothing to see"'))
    assert len(items) == 1
    header, _ = s.finish(*s.keyword("anything"))
    assert header["coverage"]["empty"] == 3     # the two zips and the mailbox: extracted, no text of their own
