"""Splitting merged PDFs (split.py): the evidence for each candidate, the
splits at each level, adjusting one, and keeping several as bundles."""

import json
import shutil
import threading
import time
from pathlib import Path

import pymupdf
import pytest

from adhocdocs import split
from adhocdocs.annotations import Annotations
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter
from adhocdocs.split import Page

CORPUS = Path(__file__).parent.parent / "corpus"
# Seven documents of the story, merged into one PDF: (file, its pages)
MERGED = [("voss-r/demand-letter-2026-03-25.pdf", 2), ("voss-r/memo-collection-bellwether-2026-03-16.pdf", 1),
          ("muller-z/PO-BMS-26-0112.pdf", 2), ("voss-r/INV-2026-0347.pdf", 1),
          ("delacroix-m/notice-of-deposition-kettering-2026-04-14.pdf", 2),
          ("voss-r/memo-collection-bellwether-2026-03-18-rev.pdf", 2),
          ("delacroix-m/deposition-kettering-2026-04-28.pdf", 8)]
STARTS = [3, 4, 6, 7, 9, 11]          # where each one after the first begins


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    m = Matter.create(tmp_path_factory.mktemp("split") / "m")
    out = pymupdf.open()
    for f, _ in MERGED:
        out.insert_pdf(pymupdf.open(CORPUS / f))
    meta = lambda p: {"custodian": "cust:a", "source": {"collection": "c", "path": p}}
    m.merged = ingest_bytes(m, out.tobytes(), "scan-batch.pdf", meta("scan-batch.pdf"))
    m.single = ingest_bytes(m, (CORPUS / "public/usc-title9-arbitration-2023.pdf").read_bytes(), "usc9.pdf", meta("usc9.pdf"))
    return m.root, m.merged, m.single


@pytest.fixture
def m(built, tmp_path):
    root, merged, single = built
    shutil.copytree(root, tmp_path / "m")
    m = Matter(tmp_path / "m")
    m.merged, m.single = merged, single
    return m


def test_merged_pdf(m):
    ps = split.pages(m, m.merged)
    assert len(ps) == sum(n for _, n in MERGED)
    assert (ps[0].number, ps[0].total) == (1, 2) and ps[2].number == 1        # "Page 1 of 2"; "… — Internal — Page 1"
    bs = split.boundaries(ps)
    assert split.starts_at(bs, "likely") == STARTS                            # the likely split is the seven documents
    assert set(split.starts_at(bs, "sure")) < set(STARTS)                      # coarser
    by = {b["page"]: b for b in bs}
    assert any("start again at 1" in r for r in by[3]["for"]) and any("the last (2 of 2)" in r for r in by[3]["for"])
    assert any("carry on" in r for r in by[2]["against"]) and by[2]["strength"] < 0.1
    assert any("caption" in r for r in by[7]["for"])
    assert any("runs on" in r for r in by[10]["against"])

    rows = split.pieces(ps, STARTS)
    assert [r["pages"] for r in rows] == [[1, 2], [3, 3], [4, 5], [6, 6], [7, 8], [9, 10], [11, 18]]
    assert rows[0]["kind"] == "letter" and rows[0]["label"].startswith("Letter: Invoice INV-2026/0347")
    assert rows[2]["label"] == "PURCHASE ORDER PO-BMS-26-0112" and rows[3]["label"] == "INVOICE"
    assert rows[6]["label"] == "AMERICAN ARBITRATION ASSOCIATION"             # without the transcript's line numbers
    assert "numbering" not in rows[0]
    assert split.pieces(ps, [3])[0].get("numbering") is None
    assert split.pieces(ps, [4])[0]["numbering"] == "its pages say 2, it has 3"       # a cut that disagrees


def test_one_document_is_left_alone(m):
    """A statute with page numbers on every other page, and a running header."""
    bs = split.boundaries(split.pages(m, m.single))
    assert split.starts_at(bs, "likely") == []
    found = split.find(m)
    assert [f["doc"] for f in found] == [m.merged] and found[0]["pieces"] == 7 and found[0]["starts"] == STARTS


def pg(n, lines, head=0, foot=0, number=None, total=None, ocr=None):
    p = Page(n, lines, lines[:head], lines[len(lines) - foot:] if foot else [])
    p.number, p.total = (number, total) if number else split._number(p)
    p.ocr = ocr
    return p


def strength(*ps):
    return split.score(list(ps), len(ps) - 1)


def test_evidence():
    body = ["We write about the invoice.", "It remains unpaid."]
    letter = ["Harrowgate Fastener Supply Co.", "1180 Marcourt Road", "March 25, 2026", "Dear Mr. Kettering:", *body]
    signed = [*body, "Sincerely,", "/s/ Raymond Voss", "cc: file"]
    # page numbers
    assert strength(pg(1, [*body, "Page 1 of 2"], foot=1), pg(2, [*body, "Page 2 of 2"], foot=1))["strength"] < 0.05
    s = strength(pg(1, [*body, "Page 2 of 2"], foot=1), pg(2, [*body, "Page 1 of 3"], foot=1))
    assert s["level"] == "sure" and any("different page count" in r for r in s["for"])
    assert strength(pg(1, body), pg(2, ["2", *body], head=1))["strength"] < 0.3          # a letter's second page
    # openings and closings
    s = strength(pg(1, signed), pg(2, letter))
    assert s["level"] == "sure" and any("salutation" in r for r in s["for"]) and any("closing" in r for r in s["for"])
    email = ["From: Raymond Voss", "Sent: Monday, March 16, 2026", "To: Ops", "Subject: Pallet", *body]
    assert any("email" in r for r in strength(pg(1, body), pg(2, email))["for"])
    # a sentence running on
    s = strength(pg(1, ["The pallet was delivered to the", "receiving dock and signed for by"]),
                 pg(2, ["the night supervisor, who noted", "no damage."]))
    assert s["strength"] < 0.05 and any("runs on" in r for r in s["against"])
    # the same letterhead as a page that began something, two pages back
    s = strength(pg(1, letter), pg(2, ["two", "more", "lines here."]), pg(3, letter))
    assert any("same letterhead" in r for r in s["for"])
    # a page with no text before
    assert any("no text" in r for r in strength(pg(1, body), pg(2, []), pg(3, body))["for"])
    # scanned after native
    assert any("scanned" in r for r in strength(pg(1, body, ocr=False), pg(2, body, ocr=True))["for"])


def test_choosing_and_adjusting(m):
    bs = split.boundaries(split.pages(m, m.merged))
    assert split.chosen(bs, "likely", cut=[12], join=[6]) == [3, 4, 7, 9, 11, 12]
    assert split.chosen(bs, at=[5]) == [5]
    over = split.passed_over(bs, split.chosen(bs, "sure"))
    assert {o["page"] for o in over} == set(STARTS) - set(split.chosen(bs, "sure"))


@pytest.fixture
def api(m, monkeypatch):
    from adhocdocs import annapi
    sock = m.root / "run" / "a.sock"
    monkeypatch.setenv("ADHOC_ANN_SOCKET", str(sock))
    monkeypatch.setenv("ADHOC_MATTER", str(m.root))
    threading.Thread(target=annapi.serve, args=(m, sock), daemon=True).start()
    for _ in range(50):
        if sock.exists():
            break
        time.sleep(0.05)
    return m


def test_cli_records_several_splits(api, tmp_path, capsys):
    from adhocdocs import cli
    m = api

    def run(*argv):
        capsys.readouterr()
        cli.meta(["--matter", str(m.root), *argv])
        return [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]

    rows = run("split", m.merged[:16], "--list", str(tmp_path / "p.list.jsonl"))
    head, pieces = rows[0], rows[1:8]
    assert head["pieces"] == 7 and head["at_each_level"]["likely"] == 7 and head["split"] == "likely"
    assert pieces[1]["loc"] == f"doc:{m.merged}#page=3" and pieces[2]["loc"] == f"doc:{m.merged}#page=4-5"
    assert pieces[1]["starts"]["level"] == "sure" and rows[-1]["next"].startswith("view list")

    rows = run("split", m.merged, "--record", "letters")
    assert rows[-1]["recorded"] == "letters" and len(rows[-1]["bundles"]) == 7
    ann = Annotations.load(m)
    b = ann.bundles[rows[-1]["bundles"][2]["bundle"]]
    assert [(p["doc"], p["select"]) for p in b["parts"]] == [(m.merged, {"page": [4, 5]})] and b["split"]["piece"] == 3
    assert b["name"] == "scan-batch.pdf · letters 3/7 · PURCHASE ORDER PO-BMS-26-0112" and b["from"] == f"split:{m.merged}"

    # a second, coarser split beside it
    run("split", m.merged, "--level", "sure", "--record", "batches")
    splits = {r["split"]: r for r in run("splits", m.merged)}
    assert set(splits) == {"letters", "batches"} and splits["letters"]["pieces"] == 7
    assert splits["batches"]["pieces"] < 7

    with pytest.raises(ValueError, match="already has a split named 'letters'"):
        split_again(m)
    rows = run("split", m.merged, "--at", "3,11", "--record", "letters", "--replace")
    assert rows[-1]["replaced"] == 7 and len(rows[-1]["bundles"]) == 3
    assert {r["split"]: r["pieces"] for r in run("splits", m.merged)}["letters"] == 3


def split_again(m):
    from adhocdocs import cli
    cli._split(m, type("A", (), {"cmd": "split", "find": False, "doc": m.merged, "level": "likely", "at": None,
                                 "cut": [], "join": [], "list_file": None, "record": "letters", "replace": False,
                                 "requested_by": None})())
