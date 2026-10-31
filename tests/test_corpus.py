"""End to end over corpus/: ingest, index, search, checked against the
known facts in corpus/SOURCES.md. Semantic search runs only with
ADHOC_TEST_SEMANTIC=1, since embedding the corpus takes minutes on a CPU."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from adhocdocs import locators
from adhocdocs.extract.pdf import tessdata
from adhocdocs.index import open_index
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter
from adhocdocs.search import Options, Searcher

CORPUS = Path(__file__).parent.parent / "corpus"


def have_ocr():
    if not shutil.which("tesseract"):
        return False
    langs = subprocess.run(["tesseract", *tessdata(), "--list-langs"], capture_output=True, text=True).stdout
    return "eng" in langs.split()


needs_ocr = pytest.mark.skipif(not have_ocr(), reason="tesseract with English data not installed")


@pytest.fixture(scope="session")
def matter(tmp_path_factory):
    m = Matter.create(tmp_path_factory.mktemp("corpus") / "matter")
    for d in sorted(CORPUS.iterdir()):
        if d.is_dir() and d.name != "tools":
            ingest_paths(m, [d], d.name, f"{d.name}-2026")
    return Matter(m.root)


def search(m, q, **kw):
    s = Searcher(Matter(m.root), Options(**kw))
    header, items = s.finish(*s.keyword(q))
    return header, items


def files(m, items):
    """custodian/path of each result; attachments as parent-path/part."""
    rows = open_index(m, "fields").rows()
    return sorted(f"{rows[i['loc'][4:]]['custodian']}/{rows[i['loc'][4:]]['path']}" for i in items)


def test_phrase_across_line_breaks_and_quotes(matter):
    header, items = search(matter, '"the shipment was never received"')
    assert header["exhaustive"] and header["coverage"]["pending"] == 0
    got = files(matter, items)
    for f in ["muller-z/2026-03-09_RE_INV-2026-0347_past-due.eml",
              "okafor-d/2026-03-09_RE_INV-2026-0347_past-due.eml",
              "okafor-d/2026-03-12_proof-of-delivery.eml",
              "muller-z/2026-03-17_RE_proof-of-delivery.eml",
              "kettering-t/2026-03-10_RE_dock-4-check-PO-BMS-26-0112.eml",
              "muller-z/dispute-timeline-notes.txt",
              "okafor-d/call-notes-bellwether-2026-03.txt",
              "voss-r/memo-collection-bellwether-2026-03-16.pdf",
              "voss-r/memo-collection-bellwether-2026-03-18-rev.pdf",
              "delacroix-m/deposition-kettering-2026-04-28.pdf",
              "voss-r/demand-letter-2026-03-25.pdf"]:
        assert f in got
    # plus the demand letter attached to two emails
    assert len(got) == 13


def test_hits_are_checked_locators(matter):
    _, items = search(matter, '"never received"')
    assert len(items) == 19
    for it in items:
        for h in it["hits"]:
            r = locators.resolve(matter, h["loc"])
            assert r["status"] == "exact"
            assert " ".join(r["text"].split()).lower() == "never received"


def test_transcript_page_line(matter):
    _, items = search(matter, '"the shipment was never received"', where="name:deposition-kettering", by="hit")
    assert [h["pl"] for h in items] == ["6:12-6:13", "7:17"]


def test_quoted_segments(matter):
    _, items = search(matter, '"nothing to pay yet"', in_kinds=["quoted"])
    assert files(matter, items) == ["muller-z/2026-03-17_RE_proof-of-delivery.eml",
                                    "okafor-d/2026-03-12_proof-of-delivery.eml"]


def test_dehyphenation(matter):
    for q in ("initials", '"payment disputes"'):
        got = files(matter, search(matter, q)[1])
        assert "voss-r/memo-collection-bellwether-2026-03-16.pdf" in got
        assert "voss-r/memo-collection-bellwether-2026-03-18-rev.pdf" in got


def test_near_duplicate_differences(matter):
    for q in ("unclaimed", "Pruitt", '"March 24"'):
        got = [f for f in files(matter, search(matter, q)[1]) if "memo-collection" in f]
        assert got == ["voss-r/memo-collection-bellwether-2026-03-18-rev.pdf"], q


def test_accent_folding(matter):
    got = files(matter, search(matter, '"zoe muller"')[1])
    assert "okafor-d/call-notes-bellwether-2026-03.txt" in got
    assert len(got) >= 19


def test_fields_and_negation(matter):
    _, items = search(matter, "from:zoe.muller@bellwethermarine.example date:2026-03")
    assert len(items) == 3 and all(i["doc"]["kind"] == "email" for i in items)
    _, items = search(matter, "kind:email -from:bellwethermarine")
    assert items and all("bellwethermarine" not in a for i in items for a in i["doc"].get("from", []))


def test_exact_duplicates_share_content(matter):
    rows = open_index(matter, "fields").rows()
    invoices = [r for r in rows.values() if r["name"] == "INV-2026-0347.pdf"]
    assert len({r["doc"] for r in invoices}) == 3          # two custodians' copies and an attachment
    assert len({r["content"] for r in invoices}) == 1


def test_grep_is_literal(matter):
    s = Searcher(matter, Options())
    _, items = s.finish(*s.grep(r"PN 44-B/7"))
    assert all("PN 44-B/7" in h["loc"]["quote"]["exact"] for i in items for h in i["hits"])
    assert len(items) >= 10


@needs_ocr
def test_ocr(matter):
    got = files(matter, search(matter, "TCF-889104")[1])
    assert "okafor-d/pod-TCF-889104.pdf" in got and "voss-r/tcfl-claim-letter-2026-04-10.pdf" in got
    got = files(matter, search(matter, "RDR-0224-02 Q-3")[1])
    assert "kettering-t/receiving-discrepancy-report-RDR-0224-02.pdf" in got


def test_withdraw_removes_from_index(tmp_path):
    m = Matter.create(tmp_path / "m")
    ingest_paths(m, [CORPUS / "muller-z" / "dispute-timeline-notes.txt"], "muller-z", "c")
    _, items = search(m, "timeline")
    assert len(items) == 1
    doc = items[0]["loc"][4:]
    m.logs["store"].append(op="withdraw", doc=doc, reason="clawback")
    header, items = search(m, "timeline")
    assert items == [] and header["coverage"]["documents"] == 0
    assert open_index(Matter(m.root), "keyword").indexed() == set()


@pytest.mark.skipif(not os.environ.get("ADHOC_TEST_SEMANTIC"), reason="set ADHOC_TEST_SEMANTIC=1")
def test_semantic(matter):
    open_index(matter, "semantic").update()
    s = Searcher(Matter(matter.root), Options(limit=5))
    header, items = s.finish(*s.semantic("the receiving dock put an unlabeled pallet in quarantine"))
    assert not header["exhaustive"] and header["coverage"]["pending"] == 0
    assert any("Q3" in i["doc"].get("name", "") or "dock" in i["doc"].get("name", "") for i in items)


def test_transcript_and_label_aliases(matter):
    dep = next(d for d, r in open_index(matter, "fields").rows().items() if r["name"] == "deposition-kettering-2026-04-28.pdf")
    r = locators.resolve(matter, f"tr:{dep}#pl=6:12,6:13")
    assert r["status"] == "exact" and r["text"].startswith("the pallet in Q-3") and r["text"].endswith("against that PO.")
    assert locators.resolve(matter, f"doc:{dep}#label=6")["loc"]["select"] == {"page": 6}
    with pytest.raises(locators.LocatorError):
        locators.resolve(matter, f"doc:{dep}#pl=99:1")


@needs_ocr
def test_reocr_into_the_workspace(tmp_path):
    from adhocdocs.cli import layers
    m = Matter.create(tmp_path / "m")
    ingest_paths(m, [CORPUS / "public" / "txsd-latshaw-drilling-v-saia-motor-freight-2013.pdf"], None, "public")
    doc = Matter(m.root).state.live_documents()[0]
    layers(["--matter", str(m.root), "ocr", doc, "--dpi", "300"])
    m = Matter(m.root)
    views = {l.view: l.feed for l in m.state.layers[m.state.documents[doc]]}
    assert views == {"native": "store", "ocr": "workspace"}
    assert not (m.store_dir / "objects").joinpath("x").exists()          # nothing written to the store
    found = lambda layers: search(m, '"united states district court"', layers=layers)[1]
    assert found("native") == [] and len(found("ocr")) == 1
