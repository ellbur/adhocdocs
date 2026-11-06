"""Tracing a fact (docs/TRACE.md) through the story corpus: where a passage
appears, its routes, who had it when, and the gaps."""

import pytest

from adhocdocs import lists, trace
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter
from corpus_files import story

PALLET = 'pallet AND (q-3 OR q3 OR quarantine)'
KETTERING = ("on 2/24 at 14:52 José logged an unlabeled pallet from Tri-County — no PO, no packing list — "
             "and put it in Q-3")


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    m = Matter.create(tmp_path_factory.mktemp("trace") / "m")
    for cust in ("muller-z", "okafor-d", "voss-r", "kettering-t", "delacroix-m"):
        ingest_paths(m, story(cust), cust, cust)
    return Matter(m.root)


def named(m, name):
    return [d for d in m.state.live_documents()
            if m.objects.get_object(m.state.metadata[d]).get("name") == name]


def by_name(result):
    return {p["person"]: p for p in result["people"]}


def test_a_quoted_passage_and_the_message_it_came_from(m):
    r = trace.trace(m, trace.seed_of(m, text="$48,612.50 is now past due (it was due 3/7). "
                                             "Could you check with AP on the status"), paraphrases=0)
    apps = r["appearances"]
    assert [a["how"] for a in apps] == ["verbatim"] * len(apps) and len(apps) == 4
    assert all(a["where"] == "quoted" for a in apps)
    # Müller's reply quotes Okafor's email, which nobody collected: the first appearance, and a gap
    assert apps[0]["route"] == {"kind": "quoted", "missing": True,
                                "text": "quoted from a message that isn't in the matter"}
    assert apps[1]["route"]["kind"] == "copy"           # the other custodian's copy of that reply
    assert {a["route"]["kind"] for a in apps[2:]} == {"quoted"} and all(a["route"]["from"] for a in apps[2:])
    assert [g["why"] for g in r["gaps"]] == ["it quotes a message that isn't in the matter"]
    people = by_name(r)
    assert people["Thomas Kettering"]["role"] == "received"
    assert people["Thomas Kettering"]["first"].startswith("2026-03-17")


def test_an_edited_passage_kept_from_a_draft(m):
    r = trace.trace(m, trace.seed_of(m, text="Zoë Müller, wrote on March 9 that the shipment was not received. "
                                             "Our records say otherwise: the proof of delivery shows the pallet "
                                             "was delivered to Dock 4 on February 24"), paraphrases=0)
    apps = r["appearances"]
    assert [a["name"] for a in apps] == ["memo-collection-bellwether-2026-03-16.pdf",
                                         "memo-collection-bellwether-2026-03-18-rev.pdf"]
    assert all(a["how"] == "edited" and 0.6 <= a["score"] < 0.97 for a in apps)
    assert apps[0]["route"]["kind"] == "earliest"
    assert apps[1]["route"]["kind"] == "draft" and apps[1]["route"]["from"] == 1
    assert "documentation says otherwise" in apps[1]["excerpt"]


def test_who_knew_about_the_pallet_in_q3_and_when(m):
    boiler = named(m, "PO-BMS-26-0112.pdf") + named(m, "master-supply-agreement-2025-11-03.docx")
    r = trace.trace(m, trace.seed_of(m, text=KETTERING), query=PALLET, exclude=boiler, paraphrases=0)
    apps = r["appearances"]
    assert not any(a["doc"] in boiler for a in apps)
    first = apps[0]
    assert first["name"] == "dock-receiving-log-2026-02.csv" and first["date"] == "2026-02-24"
    assert first["date_basis"] == "not before a date it mentions"
    told = next(a for a in apps if a["how"] == "verbatim")          # the seed, found word for word
    assert told["name"].startswith("RE: Dock 4 check") and told["route"]["kind"] == "knew"
    people = by_name(r)
    assert people["Zoë Müller"]["first"].startswith("2026-03-10") and people["Zoë Müller"]["role"] == "received"
    assert people["Marcus Delacroix-Hale"]["first"].startswith("2026-03-26")
    # an exhibit list naming the 3/2 email is dated only by that: noted, not taken as when he knew
    assert people["Marcus Delacroix-Hale"]["maybe_earlier"]["date_basis"] == "not before a date it mentions"
    assert people["Raymond Voss"]["first"].startswith("2026-04-03")
    assert people["Thomas Kettering"]["maybe_earlier"]["date"] == "2026-02-24"
    # custodians are people: kettering-t's files are Thomas Kettering's
    assert not any(p.startswith("custodian:") for p in people)
    assert r["gaps"] == []


def test_a_gap_where_nothing_shows_how_someone_knew(m):
    r = trace.trace(m, None, query='pallet AND unclaimed AND (march OR "3/4")', paraphrases=0)
    [gap] = r["gaps"]
    assert gap["who"] == ["Raymond Voss"] and gap["name"] == "memo-collection-bellwether-2026-03-18-rev.pdf"
    assert gap["why"] == "nothing in the matter shows how they had it"
    # Okafor's call notes say where it came from, but have no date to place them by
    notes = next(a for a in r["appearances"] if a["name"] == "call-notes-bellwether-2026-03.txt")
    assert notes["route"]["kind"] == "unplaced" and notes["date"] is None


def test_a_phrase_query(m):
    # a phrase is one hit; it is still all its words, not one word alone
    r = trace.trace(m, None, query='"never received"', paraphrases=0)
    names = {a["name"] for a in r["appearances"]}
    assert {"dispute-timeline-notes.txt", "call-notes-bellwether-2026-03.txt", "RE: INV-2026/0347 past due"} <= names
    assert all(a["hits"] for a in r["appearances"])


def test_include_and_exclude(m):
    base = trace.trace(m, None, query='pallet AND unclaimed AND (march OR "3/4")', paraphrases=0)
    memo = next(a for a in base["appearances"] if a["name"].endswith("-rev.pdf"))
    r = trace.trace(m, None, query='pallet AND unclaimed AND (march OR "3/4")', exclude=[memo["doc"]],
                    paraphrases=0)
    assert not any(a["doc"] == memo["doc"] for a in r["appearances"])
    # a paraphrase judged to be the fact joins as reworded
    [dt] = named(m, "dispute-timeline-notes.txt")
    text = m.objects.get_text(m.state.primary_layer(m.state.documents[dt]).text)
    s = text.index("BUT: unlabeled")
    loc = f"doc:{dt}#char={s},{s + 40}"
    r = trace.trace(m, trace.seed_of(m, text=KETTERING), include=[loc], paraphrases=0)
    inc = next(a for a in r["appearances"] if a["doc"] == dt)
    assert inc["how"] == "reworded" and inc["included"]
    with pytest.raises(trace.TraceError):
        trace.trace(m, None)


def test_seed_from_a_locator_and_the_list(m, tmp_path):
    [d] = [x for x in named(m, "2026-03-10_RE_dock-4-check-PO-BMS-26-0112.eml")]
    text = m.objects.get_text(m.state.primary_layer(m.state.documents[d]).text)
    s = text.index("on 2/24 at 14:52")
    seed = trace.seed_of(m, f"doc:{d}#char={s},{s + 80}")
    assert seed["doc"] == d and seed["text"].startswith("on 2/24 at 14:52")
    with pytest.raises(trace.TraceError):
        trace.seed_of(m, f"doc:{d}")                   # a whole document isn't a passage
    r = trace.trace(m, seed, query=PALLET, paraphrases=0)
    head, items = trace.to_list(r, query=PALLET)
    assert head["trace"] == {"seed": seed["loc"], "text": None, "query": PALLET, "include": [], "exclude": []}
    assert head["people"] == r["people"]
    assert any(i.get("children") for i in items)       # a document's other passages under its first
    path = tmp_path / "t.list.jsonl"
    lists.write(path, head, items)
    h2, i2 = lists.read(path)
    assert h2["trace"]["query"] == PALLET and len(i2) == len(items)


# -- the time DAG ------------------------------------------------------------------------------

def test_the_time_dag_keeps_unrelated_strands_apart(m):
    boiler = named(m, "PO-BMS-26-0112.pdf") + named(m, "master-supply-agreement-2025-11-03.docx")
    r = trace.trace(m, trace.seed_of(m, text=KETTERING), query=PALLET, exclude=boiler, paraphrases=0)
    strands = trace.dag(r)
    main, *rest = strands
    # the exhibit list, dated only by the email it names, joins nothing: a strand of its own
    assert any(s["events"][0]["appearances"][0]["name"] == "deposition-exhibits-kettering.csv" for s in rest)
    events = {e["id"]: e for e in main["events"]}
    # copies and a document's other passages are one event
    assert sum(len(e["appearances"]) for s in strands for e in s["events"]) == len(r["appearances"])
    assert len({e["appearances"][0]["doc"] for e in main["events"]}) == len(main["events"])
    # every edge runs forward a column at least, and only along a route
    for e in main["events"]:
        for x in e["in"]:
            assert events[x["from"]]["column"] < e["column"]
    roots = [e for e in main["events"] if not e["in"]]
    assert [e["appearances"][0]["name"] for e in roots] == ["dock-receiving-log-2026-02.csv"]
    told = next(e for e in main["events"] if e["appearances"][0]["name"].startswith("RE: Dock 4 check"))
    assert [x["label"] for x in told["in"]] == ["via Kettering"]
    files, sequence, summary = trace.dag_html(m, r, "Q-3")
    import base64
    page = base64.b64decode(files["index.html"]).decode()
    assert summary["strands"] == len(strands) and summary["gaps"] == 0
    assert page.count("data-locator=") == summary["events"] == len(sequence)
    assert "not the calendar" in page


def test_a_gap_is_a_question_mark_in_the_dag(m):
    r = trace.trace(m, None, query='pallet AND unclaimed AND (march OR "3/4")', paraphrases=0)
    [gapped] = [e for s in trace.dag(r) for e in s["events"] if e["gap"]]
    assert gapped["gap"] == "no record of how" and gapped["column"] == 1
    import base64
    files, _, summary = trace.dag_html(m, r, "unclaimed")
    page = base64.b64decode(files["index.html"]).decode()
    assert summary["gaps"] == 1 and "adhoc-trace-gap" in page and ">?<" in page


def test_short_names():
    assert trace._short("Thomas Kettering") == "Kettering"
    assert trace._short("Bellwether Accounts Payable") == "Bellwether Accounts Payable"
    assert trace._short("Tri-County Freight Lines Dispatch") == "Tri-County Freight Lines Dispatch"
    assert trace._short("ar@harrowgate.example") == "ar@harrowgate.example"
