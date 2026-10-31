import json
from pathlib import Path

import pymupdf
import pytest

from adhocdocs import lists, literate, locators, pages, produce
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter

CORPUS = Path(__file__).parent.parent / "corpus"


@pytest.fixture
def m(tmp_path):
    m = Matter.create(tmp_path / "m")
    ingest_paths(m, [CORPUS / "voss-r" / "2026-03-06_INV-2026-0347_reminder.eml",
                     CORPUS / "voss-r" / "demand-letter-2026-03-25.pdf",
                     CORPUS / "voss-r" / "memo-collection-bellwether-2026-03-16.pdf"], "voss-r", "v")
    return Matter(m.root)


def named(m, name):
    return [d for d in m.state.live_documents() if m.objects.get_object(m.state.metadata[d]).get("name") == name]


def make_list(m, tmp_path, names):
    p = tmp_path / "prod.list.jsonl"
    docs = [d for n in names for d in named(m, n)]
    lists.write(p, *lists.new("prod", ["doc:" + d for d in docs]))
    return p


def test_production(m, tmp_path):
    # the attachment is listed too, before its email: it must still follow the email
    p = make_list(m, tmp_path, ["INV-2026-0347.pdf", "2026-03-06_INV-2026-0347_reminder.eml", "demand-letter-2026-03-25.pdf"])
    r = produce.produce(m, p, "vol001", "HFS", legend="CONFIDENTIAL")
    out = Path(r["path"])
    recs = [json.loads(l) for l in (out / "production.jsonl").read_text().splitlines()][1:]
    names = [m.objects.get_object(m.state.metadata[x["doc"]])["name"] for x in recs]
    assert names == ["2026-03-06_INV-2026-0347_reminder.eml", "INV-2026-0347.pdf", "demand-letter-2026-03-25.pdf"]
    assert recs[0]["begin"] == "HFS000001" and recs[0]["page_map"] is None          # email rendered from text
    assert recs[1]["page_map"] == [1] and recs[2]["page_map"] == [1, 2]
    assert recs[0]["begattach"] == "HFS000001" and recs[1]["endattach"] == recs[1]["end"]
    assert recs[2]["begattach"] == recs[2]["begin"]
    pdf = pymupdf.open(out / recs[2]["file"])
    assert "HFS" in pdf[1].get_text() and "CONFIDENTIAL" in pdf[1].get_text()
    dat = (out / "vol001.dat").read_text(encoding="utf-8").splitlines()
    assert len(dat) == 4 and "þBEGBATESþ" in dat[0]
    assert len((out / "vol001.opt").read_text().splitlines()) == r["pages"]
    with pytest.raises(produce.ProductionError, match="already used"):
        produce.produce(m, p, "vol002", "HFS")


def test_bates_resolution_and_citation(m, tmp_path):
    p = make_list(m, tmp_path, ["demand-letter-2026-03-25.pdf"])
    produce.produce(m, p, "vol001", "HFS", start=124)
    hits = produce.resolve_bates(m, "bates:HFS000124-HFS000125")
    assert len(hits) == 1 and hits[0]["loc"]["select"] == {"page": [1, 2]}
    assert produce.resolve_bates(m, "bates:HFS000125")[0]["loc"]["select"] == {"page": 2}
    with pytest.raises(locators.LocatorError):
        produce.resolve_bates(m, "bates:HFS000999")
    # a page citing the produced pages gets Bates numbers
    doc = named(m, "demand-letter-2026-03-25.pdf")[0]
    page = tmp_path / "p.md"
    page.write_text(":cite{#d}\n\n" + literate.format_refs([{"id": "d", "doc": doc, "select": {"page": [1, 2]}}]))
    assert "HFS000124–25" in pages.render(pages.Page(m, page))[0]
