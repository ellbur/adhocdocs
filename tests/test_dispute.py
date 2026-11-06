"""A dispute (docs/DISPUTES.md) over the story corpus: "the shipment was never
received" against "it arrived", who holds which, and the tensions."""

import base64

import pytest

from adhocdocs import dispute, lists, query
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter
from corpus_files import story

NEVER = '"never received" OR "not received"'
ARRIVED = ('((delivered OR delivery OR tendered OR received OR arrived OR arriving) AND ("dock 4" OR "february 24" OR "2/24")) '
           'OR (pallet AND (q-3 OR q3 OR quarantined OR unlabeled))')


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    m = Matter.create(tmp_path_factory.mktemp("dispute") / "m")
    for cust in ("muller-z", "okafor-d", "voss-r", "kettering-t", "delacroix-m"):
        ingest_paths(m, story(cust), cust, cust)
    return Matter(m.root)


def named(m, name):
    return [d for d in m.state.live_documents()
            if m.objects.get_object(m.state.metadata[d]).get("name") == name]


@pytest.fixture(scope="module")
def sides(m):
    # the PO and the supply agreement say where to deliver: terms, not claims
    boiler = named(m, "PO-BMS-26-0112.pdf") + named(m, "master-supply-agreement-2025-11-03.docx")
    return [dispute.side("never received", query=NEVER, exclude=boiler),
            dispute.side("it arrived", query=ARRIVED, exclude=boiler)]


@pytest.fixture(scope="module")
def r(m, sides):
    return dispute.dispute(m, sides)


def find(r, text, **kw):
    out = [s for s in r["statements"] if text in " ".join(s["text"].split())
           and all(s.get(k) == v for k, v in kw.items())]
    assert out, text
    return out[0]


def test_stances_and_speakers(r):
    s = find(r, "the shipment was never received.", name="RE: INV-2026/0347 past due", copy_of=None)
    assert (s["speaker_name"], s["stance"], s["camp"]) == ("Zoë Müller", "asserts", "never received")
    # a denial of one side is the other's camp
    s = find(r, "No pallet bearing purchase order PO-BMS-26-0112 was received at Dock 4")
    assert (s["speaker_name"], s["stance"], s["camp"]) == ("Marcus Delacroix-Hale", "denies", "never received")
    # someone else's words, contradicted
    s = find(r, "assertion that “the shipment was never received” is contradicted", copy_of=None)
    assert (s["speaker_name"], s["stance"], s["camp"]) == ("Evelyn Stroud", "rejects", "it arrived")
    assert s["attributed"]["name"] == "Bellwether"
    s = find(r, "Zoë Müller, wrote on March 9", name="memo-collection-bellwether-2026-03-16.pdf")
    assert (s["stance"], s["attributed"]["name"]) == ("rejects", "Zoë Müller")    # "Our records say otherwise"
    # reported, not held
    s = find(r, "customer says “never received”")
    assert (s["speaker_name"], s["stance"], s["camp"], s["attributed"]["name"]) == ("Raymond Voss", "reports", None, "customer")
    # a condition isn't a claim, and a quoted part is its author's words passed on
    assert find(r, "If payment is not received", copy_of=None)["stance"] == "supposes"
    q = find(r, "They say it was delivered on 2/24")
    assert (q["stance"], q["speaker_name"], q["camp"]) == ("quotes", "Zoë Müller", None)
    # a transcript's answers are the witness's, hedges and all
    s = find(r, "As far as I knew, the shipment was never received.")
    assert (s["speaker_name"], s["stance"], s["camp"]) == ("Thomas Kettering", "qualifies", "never received")
    assert find(r, "The shipment was never received against that PO, in our system")["speaker_name"] == "Thomas Kettering"
    assert find(r, "Did anyone open the Q-3 pallet")["stance"] == "asks"


def test_the_tensions(r):
    by = lambda kind: [t for t in r["tensions"] if t["kind"] == kind]
    both = {(t["person"], t["name"]) for t in by("both")}
    assert ("Thomas Kettering", "RE: Dock 4 check — PO-BMS-26-0112?") in both          # "never received … but … Q-3"
    assert ("Zoë Müller", "RE: Proof of delivery — PRO 0047-2219-6 / INV-2026/0347") in both
    [sw] = [t for t in by("switch") if t["person"] == "Marcus Delacroix-Hale"]
    assert (sw["from"], sw["to"], sw["date"][:10]) == ("it arrived", "never received", "2026-04-02")
    # Müller said "never received" on 3/9, Kettering told her about the Q-3 pallet on 3/10, she said it again on 3/17
    t = next(t for t in by("despite") if t["person"] == "Zoë Müller" and t["relation"] == "colleague")
    st = {s["id"]: s for s in r["statements"]}
    assert t["camp"] == "never received" and t["date"].startswith("2026-03-17")
    assert st[t["against"]]["speaker_name"] == "Thomas Kettering" and "Q-3" in st[t["against"]]["text"]
    assert t["role"] == "received" and "on that side since 2026-03-09" in t["text"]
    # tensions come in order: both, switches, then held despite a colleague before anyone else
    kinds = [x["kind"] for x in r["tensions"]]
    assert kinds == sorted(kinds, key=["both", "switch", "despite"].index)


def test_copies_say_nothing_new(r):
    copies = [s for s in r["statements"] if s["copy_of"]]
    assert copies and all(c["copy_of"] < c["appearance"] for c in copies)
    positions = {s["id"] for s in dispute._positions(r["statements"])}
    assert not positions & {c["id"] for c in copies}
    stroud = next(p for p in r["people"] if p["person"] == "Evelyn Stroud")
    assert list(stroud["camps"]) == ["it arrived"] and stroud["org"] == "pellhamstroud.example"
    muller = next(p for p in r["people"] if p["person"] == "Zoë Müller")
    assert muller["camps"]["never received"]["first"].startswith("2026-03-09")
    assert {a["by"] for a in muller["attributed"]} >= {"Raymond Voss"}


def test_judging_a_stance_and_the_list(m, r, sides, tmp_path):
    s = find(r, "Delivered “Dock 4”")                      # a portal's status line, which Okafor only holds
    assert s["stance"] == "asserts"
    head, items = dispute.to_list(r, sides)
    path = tmp_path / "d.list.jsonl"
    lists.write(path, head, items)
    h2, i2 = lists.read(path)
    assert h2["dispute"]["sides"][0]["query"] == NEVER and len(i2) == len(items)
    assert sum(1 + len(i.get("children", [])) for i in items) == len(r["statements"])
    r2, terms = dispute.again(m, h2, [(s["loc"], "reports")])
    s2 = next(x for x in r2["statements"] if x["loc"] == s["loc"])
    assert (s2["stance"], s2["camp"], s2["judged"]) == ("reports", None, True)
    assert terms["stances"] == [(s["loc"], "reports")]
    with pytest.raises(dispute.DisputeError):
        dispute.dispute(m, sides, [(s["loc"], "agrees")])
    with pytest.raises(dispute.DisputeError):
        dispute.dispute(m, [sides[0], sides[0]])


def test_the_dag(m, r):
    files, sequence, summary = dispute.dag_html(m, r, "Never received vs it arrived")
    page = base64.b64decode(files["index.html"]).decode()
    assert summary["despite_edges"] >= 2 and summary["tensions"]["both"] >= 2
    assert "adhoc-dispute-c0" in page and "adhoc-dispute-c1" in page and "adhoc-dispute-both" in page
    assert "kind-despite" in page and "still: never received" in page
    assert page.count("data-locator=") == len(sequence) == summary["events"]


def test_sentences_and_queries():
    t = "Ms. Müller called at 2:47 p.m. on Feb. 24. She said no. Q. Did it arrive? A. No. It was never received."
    got = [t[a:b] for a, b in dispute._sentences(t, 0, len(t), transcript=True)]
    assert got == ["Ms. Müller called at 2:47 p.m. on Feb. 24.", "She said no.", "Q. Did it arrive?",
                   "A. No.", "It was never received."]
    node = query.parse('((delivered OR delivery) AND "dock 4") OR (pallet AND q-3)')
    s = "I went through the Dock 4 log for 2/19."
    assert dispute._query_hits(node, s, 0, len(s)) is None              # "Dock 4" alone isn't the claim
    s = "José put the pallet in Q-3, near the delivery bay."
    assert [s[a:b] for a, b in dispute._query_hits(node, s, 0, len(s))] == ["pallet", "Q-3"]
    assert dispute._query_hits(query.parse("pallet NOT q-3"), s, 0, len(s)) is None
