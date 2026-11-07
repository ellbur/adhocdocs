"""What a claim was checked against: search references (`page ref search`,
:checked), and the small tool fixes that came out of watching a fresh Claude
summarize the story corpus (layers with no text, list items by locator)."""

import json
import os
from pathlib import Path

import pytest

from adhocdocs import cli, lists, literate, locators, pages, sets
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter

CORPUS = Path(__file__).parent.parent / "corpus"
FILES = [CORPUS / "voss-r" / "memo-collection-bellwether-2026-03-16.pdf",
         CORPUS / "muller-z" / "2026-03-09_RE_INV-2026-0347_past-due.eml",
         CORPUS / "okafor-d" / "2026-03-09_RE_INV-2026-0347_past-due.eml",
         CORPUS / "kettering-t" / "2026-03-10_RE_dock-4-check-PO-BMS-26-0112.eml"]


@pytest.fixture
def m(tmp_path):
    m = Matter.create(tmp_path / "m")
    ingest_paths(m, FILES, "x", "c")
    return Matter(m.root)


def hit(m, phrase):
    from adhocdocs.search import Options, Searcher
    s = Searcher(m, Options(by="hit"))
    _, items = s.finish(*s.keyword(f'"{phrase}"'))
    return locators.format(items[0]["loc"])


def page_with(tmp_path, body):
    p = tmp_path / "page.md"
    p.write_text("---\ntitle: T\n---\n" + body + "\n", encoding="utf-8", newline="\n")
    return p


def test_a_claim_and_what_it_was_checked_against(m, tmp_path):
    p = page_with(tmp_path, "Bellwether says it never received the shipment :checked{#nr}.\n\n"
                            "Nothing mentions a forklift :checked{#fl}.")
    against = hit(m, "may have been sitting in Q-3")
    r = pages.add_search_ref(m, p, "nr", '"never received"', against=[(against, "the pallet was there")], read="all")
    assert r["documents"] == 4 and r["against"] == 1 and r["coverage"]["searched"] == 4
    assert pages.add_search_ref(m, p, "fl", "forklift", read="all")["documents"] == 0
    with pytest.raises(pages.PageError):
        pages.add_search_ref(m, p, "nr", "x")                  # exists: --replace
    html_doc, results = pages.render(pages.Page(m, p))
    assert {x["id"]: x["status"] for x in results if "id" in x} == {"nr": "exact", "fl": "exact"}
    # a badge at the claim, and after the paragraph what it was checked against
    assert "checked: 4 documents, read all 4 (by the author's account): 1 against" in html_doc
    assert "checked: 0 documents, nothing found" in html_doc
    assert html_doc.index("adhoc-check-badge") < html_doc.index("</p>") < html_doc.index("<details")
    # the two copies of one email are one row, and the one judged against comes first
    block = html_doc[html_doc.index('id="adhoc-check-nr"'):]
    block = block[:block.index("</details>")]
    assert block.count("<li") == 3 and "(2 copies)" in block
    assert block.index("adhoc-against") < block.index("(2 copies)") and "against: the pallet was there" in block
    assert "over 4 of 4 documents" in block and "(3 once copies are folded)" in block


def test_a_check_is_rerun_and_reports_new_documents(m, tmp_path):
    p = page_with(tmp_path, "She said so :checked{#nr}.")
    pages.add_search_ref(m, p, "nr", '"never received"', read=2)
    assert "read 2 of 4 (by the author's account)" in pages.render(pages.Page(m, p))[0]
    ingest_paths(m, [CORPUS / "okafor-d" / "call-notes-bellwether-2026-03.txt"], "y", "c")
    m2 = Matter(m.root)
    res = {x["id"]: x for x in pages.Page(m2, p).check() if "id" in x}
    assert res["nr"]["status"] == "re-anchored" and "1 more" in res["nr"]["error"]
    assert "finds other documents now" in pages.render(pages.Page(m2, p))[0]
    pages.update_refs(m2, p)
    assert {x["id"]: x["status"] for x in pages.Page(m2, p).check() if "id" in x} == {"nr": "exact"}


def test_a_search_is_shown_only_as_a_check(m, tmp_path):
    p = page_with(tmp_path, "Quoted :q{#nr}, cited :cite{#nr}, linked [here](ref:nr).")
    pages.add_search_ref(m, p, "nr", '"never received"')
    res = {x["id"]: x for x in pages.Page(m, p).check() if "id" in x}
    assert res["nr"]["status"] == "broken" and ":checked" in res["nr"]["error"]
    p.write_text(p.read_text().replace(":q{#nr}", "x"))
    html_doc, results = pages.render(pages.Page(m, p))
    assert "search [&quot;never received&quot;] (4 documents)" in html_doc and 'href="#adhoc-check-nr"' in html_doc
    with pytest.raises(ValueError):          # a check must be exhaustive, so it is a keyword search
        literate.run_search(m, {"search": "x", "index": "semantic"})


def test_export_records_the_checks(m, tmp_path):
    p = page_with(tmp_path, "She said so :checked{#nr}.")
    pages.add_search_ref(m, p, "nr", '"never received"', read="all", note="every statement of non-receipt")
    for fmt in ("html", "docx"):
        out = pages.export(pages.Page(m, p), fmt, tmp_path / f"o.{fmt}")
        prov = json.loads(Path(out["provenance"]).read_text())
        [c] = prov["checks"]
        assert c["query"] == '"never received"' and len(c["documents"]) == 4 and c["read"] == "all"
    import docx
    text = "\n".join(x.text for x in docx.Document(tmp_path / "o.docx").paragraphs)
    assert "[checked: 4 documents, read all 4 (by the author's account): none against" in text and "<a" not in text


def test_the_documents_behind_a_point(m, tmp_path):
    cand = tmp_path / "c.list.jsonl"
    lists.write(cand, *lists.new("c", ["doc:" + d for d in m.state.live_documents()]))
    ks = m.workspace / "lists" / "k.set.list.jsonl"
    ks.parent.mkdir(parents=True, exist_ok=True)
    header, items, _ = sets.condense(m, [(str(cand), *lists.read(cand))], "Key", "What happened?", 0.9, 0.9)
    sets.set_point(header, "denied", "Bellwether denied receipt")
    first = lists.doc_of(items[0])
    sets.set_fields(items, first, {"why": "the first denial", "points": "denied"}, header)
    lists.write(ks, header, items)
    p = page_with(tmp_path, "Bellwether denied it :listed{#den}, and the whole set :listed{#all}.")
    assert pages.add_list_ref(m, p, "den", str(ks), point="denied")["documents"] == 1
    assert pages.add_list_ref(m, p, "all", str(ks))["list"] == "lists/k.set.list.jsonl"      # kept relative to the workspace
    with pytest.raises(pages.PageError):
        pages.add_list_ref(m, p, "x", str(ks), point="nope")
    html_doc, _ = pages.render(pages.Page(m, p))
    assert "Key: Bellwether denied receipt — 1 document" in html_doc and "1 document behind this" in html_doc and "the first denial" in html_doc
    assert html_doc.count('class="adhoc-check adhoc-list"') == 2


def test_list_items_by_locator_or_position(m, tmp_path):
    a, b = hit(m, "never received"), hit(m, "may have been sitting in Q-3")
    f = tmp_path / "t.list.jsonl"
    header, items = lists.new("t", [a, b, json.dumps(locators.parse(b))])   # the last is b, spelled as JSON
    sets.set_fields(items, "@1", {"event": "first"})
    key = cli._item_key(items, b)
    assert key == b
    sets.set_fields(items, key, {"event": "second"})
    assert [(i.get("fields") or {}).get("event") for i in items] == ["first", "second", None]
    with pytest.raises(ValueError):
        sets.set_fields(items, "@9", {"x": "y"})


def test_layers_points_to_parts_and_says_when_ocr_cannot_run(tmp_path, capsys, monkeypatch):
    m = Matter.create(tmp_path / "m2")
    ingest_paths(m, [CORPUS / "voss-r" / "2026-03-06_INV-2026-0347_reminder.eml",
                     CORPUS / "voss-r" / "memo-collection-bellwether-2026-03-16.pdf"], "x", "c")
    m = Matter(m.root)
    monkeypatch.setenv("ADHOC_MATTER", str(m.root))
    email = next(d for d in m.state.live_documents() if m.state.children.get(d))
    cli.layers([email])
    out = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert out[-1] == {"parts": 1, "see": f"meta show family:{email}"}
    pdf = next(d for d in m.state.live_documents()
               if m.objects.get_object(m.state.metadata[d]).get("name") == "memo-collection-bellwether-2026-03-16.pdf")
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(SystemExit):
        cli.layers(["ocr", pdf])
    assert "OCR needs tesseract" in capsys.readouterr().err


def test_an_absence_is_one_search_with_its_near_misses(m, tmp_path):
    p = page_with(tmp_path, "No photograph of the pallet is in the collection :checked{#photo}. "
                            "Nor any forklift record :checked{#fl}.")
    pages.add_search_ref(m, p, "photo", "pallet", read="all", absent=True)
    near = hit(m, "may have been sitting in Q-3")
    pages.add_search_ref(m, p, "fl", "forklift", read="all", absent=True, against=[(near, "a pallet, but no photo")])
    html_doc, _ = pages.render(pages.Page(m, p))
    assert "checked: 2 documents, read all 2 (by the author's account): none is it" in html_doc
    assert "checked: 0 documents, nothing found: 1 may be it, found otherwise" in html_doc and "may be it: a pallet, but no photo" in html_doc


def test_adding_what_a_condense_missed(m, tmp_path):
    docs = sorted(m.state.live_documents())
    header, items = lists.new("k", ["doc:" + docs[0]])
    assert sets.add(items, ["doc:" + docs[1]]) == ["doc:" + docs[1]]
    with pytest.raises(ValueError):
        sets.add(items, ["doc:" + docs[0]])
    f = tmp_path / "k.list.jsonl"
    lists.write(f, header, items)
    cli.list_(["--matter", str(m.root), "add", str(f), docs[2][:20]])
    assert [lists.doc_of(i) for i in lists.read(f)[1]] == docs[:3]


def test_what_was_read_comes_from_the_read_log(m, tmp_path, monkeypatch, capsys):
    from adhocdocs import readlog
    monkeypatch.setenv("ADHOC_MATTER", str(m.root))
    p = page_with(tmp_path, "She said so :checked{#nr}.")
    pages.add_search_ref(m, p, "nr", '"never received"', read="all")
    # the hit's own words aren't reading it; a passage around it, or the whole document, is
    cli.text([hit(m, "never received")])
    assert "(by the author's account)" not in pages.render(pages.Page(m, p))[0]
    assert "0 of 4 read (the author says all)" in pages.render(pages.Page(m, p))[0]
    kett = lists.doc_of({"loc": hit(m, "may have been sitting in Q-3")})
    cli.text(["doc:" + kett])
    capsys.readouterr()
    html_doc = pages.render(pages.Page(m, p))[0]
    assert "1 of 4 read (the author says all)" in html_doc and html_doc.count('class="adhoc-read"') == 1
    # a copy read is read: the 3/9 email is in two mailboxes
    past_due = [d for d in m.state.live_documents()
                if m.objects.get_object(m.state.metadata[d]).get("name") == "2026-03-09_RE_INV-2026-0347_past-due.eml"]
    cli.text(["doc:" + past_due[0]])
    assert "3 of 4 read" in pages.render(pages.Page(m, p))[0]
    assert len(readlog.path(m).read_text().splitlines()) == 3        # one line per read


def test_proposed_against_and_judging_them(m, tmp_path):
    p = page_with(tmp_path, "Bellwether never received it :checked{#nr}.")
    r = pages.add_search_ref(m, p, "nr", '"never received"', read="all")
    [prop] = r["proposed_not_judged"]                       # Voss's memo: Müller's words, rejected
    assert prop["stance"] == "rejects" and prop["speaker"] == "Raymond Voss"
    assert {x["id"]: x.get("unjudged") for x in pages.Page(m, p).check() if "id" in x} == {"nr": 1}
    html_doc = pages.render(pages.Page(m, p))[0]
    assert "1 proposed as against, not judged" in html_doc and "adhoc-check-pending" in html_doc
    out = pages.judge_ref(m, p, "nr", prop["loc"], "not", "Voss quotes her to answer her; it is our side's memo")
    assert out["proposed_not_judged"] == 0
    html_doc = pages.render(pages.Page(m, p))[0]
    assert "none against" in html_doc and "judged not against: Voss quotes her" in html_doc
    pages.judge_ref(m, p, "nr", prop["loc"], "against", "changed my mind")       # a later judgment replaces it
    ref = pages.Page(m, p).refs["nr"]
    assert len(ref["against"]) == 1 and "not_against" not in ref
    with pytest.raises(pages.PageError):
        pages.judge_ref(m, p, "nr", prop["loc"], "against", "")
