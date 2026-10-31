import pytest

from adhocdocs import locators, query as Q, textnorm
from adhocdocs.extract import extract
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter, new_event_id
from adhocdocs.objects import canonical, object_id


def test_canonical_is_sorted_compact_utf8():
    assert canonical({"b": 1, "a": "Zoë"}) == '{"a":"Zoë","b":1}'.encode()
    with pytest.raises(TypeError):
        canonical({"x": 0.5})


def test_event_ids_sort_in_order():
    ids = [new_event_id() for _ in range(1000)]
    assert ids == sorted(ids) and len(set(ids)) == 1000


def test_tokenize_folds_and_keeps_offsets():
    text = "Zoë Müller’s ﬁle: ship-\nment “never received”"
    toks = textnorm.tokenize(text)
    assert [t for t, _, _ in toks] == ["zoe", "muller", "s", "file", "shipment", "never", "received"]
    t, s, e = toks[4]
    assert text[s:e] == "ship-\nment"
    t, s, e = toks[3]
    assert text[s:e] == "ﬁle"


def test_hyphen_not_joined_mid_line():
    assert textnorm.words("44-B/7 well-known") == ["44", "b", "7", "well", "known"]


def test_query_parse():
    assert Q.to_text(Q.parse('pallet "never received" OR crate')) == '(pallet AND ("never received" OR crate))'
    assert Q.to_text(Q.parse("-tag:privileged kind:email")) == "(NOT tag:privileged AND kind:email)"
    assert Q.to_text(Q.parse("pallet w/5 received")) == "(pallet w/5 received)"
    assert Q.to_text(Q.parse("pallet OR crate w/5 received")) == "((pallet OR crate) w/5 received)"
    with pytest.raises(Q.QueryError):
        Q.parse("(a b) w/5 c")
    assert Q.to_text(Q.parse("INV-2026/0347")) == '"inv 2026 0347"'
    with pytest.raises(Q.QueryError):
        Q.parse("(pallet")


def test_proximity_spans():
    toks = textnorm.words("the pallet was never received at dock four")
    assert Q.spans(Q.parse("pallet w/3 received"), toks) == [(1, 5)]
    assert Q.spans(Q.parse("pallet w/2 received"), toks) == []
    assert Q.spans(Q.parse("received pre/3 pallet"), toks) == []
    assert Q.spans(Q.parse("received w/3 pallet"), toks) == [(1, 5)]
    assert Q.spans(Q.parse("rece*"), toks) == [(4, 5)]


def test_locator_string_round_trip():
    s = "doc:sha256:" + "a" * 64 + "@sha256:" + "b" * 64 + "#char=10,20"
    loc = locators.parse(s)
    assert loc["select"] == {"char": [10, 20]}
    assert locators.format(loc) == s
    assert locators.parse("doc:sha256:" + "a" * 64 + "#page=3-4")["select"] == {"page": [3, 4]}


EMAIL = b"""From: Zoe <zoe@example.com>
To: Dana <dana@example.com>
Subject: RE: invoice
Date: Mon, 09 Mar 2026 10:58:00 -0400
Message-ID: <x@example.com>

Dana, the shipment was never received.

On Mon, Mar 9, 2026, Dana wrote:
> Please pay.
"""


@pytest.fixture
def matter(tmp_path):
    return Matter.create(tmp_path / "m")


def test_email_layer(matter):
    ext = extract("a.eml", EMAIL)
    layer = ext.layers[0]
    assert layer.text.startswith("From: Zoe <zoe@example.com>\nTo:")
    kinds = {s["kind"] for s in layer.segments}
    assert {"header", "body", "quoted"} <= kinds
    quoted = next(s for s in layer.segments if s["kind"] == "quoted")
    assert layer.text[quoted["start"]:].startswith("On Mon")
    assert ext.metadata["from"] == ["zoe@example.com"]
    assert ext.metadata["date"].startswith("2026-03-09")


def test_ingest_is_idempotent_and_ids_check(matter):
    ident = {"custodian": "cust:x", "source": {"collection": "c", "path": "a.eml"}}
    doc = ingest_bytes(matter, EMAIL, "a.eml", ident)
    n = sum(1 for _ in matter.changes())
    assert ingest_bytes(Matter(matter.root), EMAIL, "a.eml", ident) == doc
    assert sum(1 for _ in Matter(matter.root).changes()) == n
    obj = matter.objects.get_object(doc)
    assert object_id(obj) == doc
    # a different custodian is a different document with the same content
    doc2 = ingest_bytes(matter, EMAIL, "a.eml", {**ident, "custodian": "cust:y"})
    assert doc2 != doc and matter.state.documents[doc2] == matter.state.documents[doc]


def test_resolve_exact_reanchored_broken(matter):
    doc = ingest_bytes(matter, EMAIL, "a.eml", {"source": {"collection": "c", "path": "a.eml"}})
    state = matter.state
    layer = state.primary_layer(state.documents[doc])
    text = matter.objects.get_text(layer.text)
    s = text.index("never received")
    loc = locators.char_locator(doc, layer.id, text, s, s + 14)
    assert locators.resolve(matter, loc)["status"] == "exact"

    # the layer is gone (say, re-extracted): found again by its quote
    moved = {**loc, "layer": "sha256:" + "0" * 64}
    r = locators.resolve(matter, moved)
    assert r["status"] == "re-anchored" and r["loc"]["select"]["char"] == [s, s + 14]

    # a quote that isn't there is broken, never shown as other text
    bad = {**moved, "quote": {"exact": "was delivered"}}
    assert locators.resolve(matter, bad)["status"] == "broken"
    wrong = {**loc, "quote": {"exact": "something else"}}
    assert locators.resolve(matter, wrong)["status"] == "broken"

    matter.logs["store"].append(op="withdraw", doc=doc, reason="clawback")
    assert locators.resolve(Matter(matter.root), loc)["status"] == "broken"
