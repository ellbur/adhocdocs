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
    recs = [json.loads(l) for l in (out / "production.jsonl").read_text(encoding="utf-8").splitlines()][1:]
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
    assert len((out / "vol001.opt").read_text(encoding="utf-8").splitlines()) == r["pages"]
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
    page.write_text(":cite{#d}\n\n" + literate.format_refs([{"id": "d", "doc": doc, "select": {"page": [1, 2]}}]), encoding="utf-8", newline="\n")
    assert "HFS000124–25" in pages.render(pages.Page(m, page))[0]


def test_withhold_redact_and_privilege_log(m, tmp_path):
    from adhocdocs.annotations import write
    user = "user:owen"
    write(m, user, {"op": "define-tag", "tag": "privileged", "description": "Attorney-client communication"})
    write(m, user, {"op": "define-tag", "tag": "redact-privilege"})
    memo = named(m, "memo-collection-bellwether-2026-03-16.pdf")[0]
    email = named(m, "2026-03-06_INV-2026-0347_reminder.eml")[0]
    letter = named(m, "demand-letter-2026-03-25.pdf")[0]

    def span(doc, phrase):
        st = m.state
        layer = st.primary_layer(st.documents[doc])
        text = m.objects.get_text(layer.text)
        s = text.index(phrase)
        return {"doc": doc, "layer": layer.id, "select": {"char": [s, s + len(phrase)]}}

    write(m, user, {"op": "tag", "tag": "privileged", "target": {"doc": memo}})
    write(m, user, {"op": "note", "target": {"doc": memo}, "body": "Prepared at counsel's request."})
    write(m, user, {"op": "tag", "tag": "redact-privilege", "target": span(letter, "TCF-889104")})
    write(m, user, {"op": "tag", "tag": "redact-privilege", "target": span(email, "friendly reminder")})
    p = make_list(m, tmp_path, ["memo-collection-bellwether-2026-03-16.pdf", "demand-letter-2026-03-25.pdf",
                                "2026-03-06_INV-2026-0347_reminder.eml"])
    r = produce.produce(Matter(m.root), p, "vol001", "HFS")
    out = Path(r["path"])
    recs = {x["doc"]: x for x in map(json.loads, (out / "production.jsonl").read_text(encoding="utf-8").splitlines()[1:])}

    slip = pymupdf.open(out / recs[memo]["file"])
    assert slip.page_count == 1 and "withheld" in slip[0].get_text() and "MEMORANDUM" not in slip[0].get_text()
    letter_text = "".join(pg.get_text() for pg in pymupdf.open(out / recs[letter]["file"]))
    assert "TCF-889104" not in letter_text and "REDACTED" in letter_text and "Bellwether" in letter_text
    email_text = "".join(pg.get_text() for pg in pymupdf.open(out / recs[email]["file"]))
    assert "friendly reminder" not in email_text and "[REDACTED – Privilege]" in email_text

    log = [json.loads(l) for l in (out / "privlog.jsonl").read_text(encoding="utf-8").splitlines()]
    by = {row["treatment"]: row for row in log}
    assert len(log) == 3 and by["withheld"]["privilege"] == "privileged"
    assert "Attorney-client" in by["withheld"]["basis"] and "counsel's request" in by["withheld"]["basis"]
    assert {row["privilege"] for row in log if row["treatment"] == "redacted"} == {"privilege"}
    assert (out / "privlog.csv").read_text(encoding="utf-8").startswith("bates,date,author")


def test_unplaceable_redaction_stops_production(m, tmp_path):
    from adhocdocs.annotations import write
    letter = named(m, "demand-letter-2026-03-25.pdf")[0]
    write(m, "user:owen", {"op": "define-tag", "tag": "redact"})
    # a whole page region is fine; a char range in a layer without geometry is not placeable
    write(m, "user:owen", {"op": "tag", "tag": "redact", "target": {"doc": letter, "select": {"page": 2, "xywh": [0.1, 0.1, 0.5, 0.2]}}})
    p = make_list(m, tmp_path, ["demand-letter-2026-03-25.pdf"])
    produce.produce(Matter(m.root), p, "vol001", "HFS")
    import adhocdocs.produce as P
    with pytest.raises(P.ProductionError, match="can't place"):
        P.pages_for(Matter(m.root), letter, [{"tag": "redact", "target": {"doc": letter, "layer": "sha256:" + "0" * 64, "select": {"char": [0, 5]}}}])


def test_privilege_calls_are_a_persons_and_claude_flags(m, tmp_path):
    from adhocdocs.annotations import CLAUDE, PRIVILEGE_CHECK, Refused, write
    user = "user:owen"
    write(m, user, {"op": "define-tag", "tag": "privileged"})
    memo = named(m, "memo-collection-bellwether-2026-03-16.pdf")[0]
    email = named(m, "2026-03-06_INV-2026-0347_reminder.eml")[0]
    letter = named(m, "demand-letter-2026-03-25.pdf")[0]
    # Claude can't make the call, even when asked
    with pytest.raises(Refused, match="privilege calls are a lawyer's"):
        write(m, CLAUDE, {"op": "tag", "tag": "privileged", "target": {"doc": memo}, "requested_by": "user"})
    # ...so it flags, with a basis (what ann flag-privilege writes)
    write(m, CLAUDE, {"op": "define-tag", "tag": PRIVILEGE_CHECK})
    for doc, basis in ((memo, "Collection strategy prepared for counsel"), (email, "Mentions outside counsel")):
        write(m, CLAUDE, {"op": "tag", "tag": PRIVILEGE_CHECK, "target": {"doc": doc}})
        write(m, CLAUDE, {"op": "note", "target": {"doc": doc}, "body": basis})
    write(m, user, {"op": "tag", "tag": "privileged", "target": {"doc": memo}})      # the lawyer withholds one
    # a privileged tag Claude applied before it was refused is a flag, not a decision
    m.annotation_log.append(op="tag", tag="privileged", target={"doc": letter}, author=CLAUDE)

    p = make_list(m, tmp_path, ["memo-collection-bellwether-2026-03-16.pdf", "demand-letter-2026-03-25.pdf",
                                "2026-03-06_INV-2026-0347_reminder.eml"])
    r = produce.produce(Matter(m.root), p, "vol001", "HFS")
    out = Path(r["path"])
    recs = {x["doc"]: x for x in map(json.loads, (out / "production.jsonl").read_text(encoding="utf-8").splitlines()[1:])}
    assert recs[memo].get("withheld") == "privileged"
    assert "withheld" not in recs[letter] and "withheld" not in recs[email]
    assert [row["bates"] for row in map(json.loads, (out / "privlog.jsonl").read_text(encoding="utf-8").splitlines())] == [recs[memo]["begin"]]

    check = {row["bates"].split("–")[0]: row for row in map(json.loads, (out / "privilege-check.jsonl").read_text(encoding="utf-8").splitlines())}
    assert check[recs[memo]["begin"]]["status"] == "withheld (privileged)"
    assert check[recs[memo]["begin"]]["basis"] == "Collection strategy prepared for counsel"
    assert check[recs[email]["begin"]]["status"] == "open: produced"
    assert "only a person's tag acts on" in check[recs[letter]["begin"]]["basis"]
    assert r["privilege_check"]["open"] == 2 and "review privilege-check.csv" in r["warning"]
