import json
import threading
import time

import pytest

from adhocdocs import annapi, literate, locators
from adhocdocs.annotations import Annotations, Refused, current_tags, write
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter
from adhocdocs.search import Options, Searcher

USER = "user:owen"
EMAIL = b"""From: Zoe <zoe@example.com>
Subject: RE: invoice

Dana, the shipment was never received.
"""


@pytest.fixture
def m(tmp_path):
    m = Matter.create(tmp_path / "m")
    m.doc = ingest_bytes(m, EMAIL, "a.eml", {"custodian": "cust:a", "source": {"collection": "c", "path": "a.eml"}})
    m.dupe = ingest_bytes(m, EMAIL, "a.eml", {"custodian": "cust:b", "source": {"collection": "c", "path": "a.eml"}})
    return m


def fresh(m):
    return Matter(m.root)


def range_loc(m, phrase="never received"):
    state = fresh(m).state
    layer = state.primary_layer(state.documents[m.doc])
    text = m.objects.get_text(layer.text)
    s = text.index(phrase)
    return {"doc": m.doc, "layer": layer.id, "select": {"char": [s, s + len(phrase)]}}


def test_tags_need_definitions_and_hotkeys_are_unique(m):
    with pytest.raises(Refused, match="define it first"):
        write(m, "claude", {"op": "tag", "tag": "hot", "target": {"doc": m.doc}})
    write(m, USER, {"op": "define-tag", "tag": "hot", "hotkey": "h"})
    with pytest.raises(Refused, match="hotkey"):
        write(m, "claude", {"op": "define-tag", "tag": "hotter", "hotkey": "h"})
    write(m, "claude", {"op": "tag", "tag": "hot", "target": {"doc": m.doc}})
    assert write(m, "claude", {"op": "tag", "tag": "hot", "target": {"doc": m.doc}}) == []   # already there


def test_claude_cannot_change_the_users_work(m):
    write(m, USER, {"op": "define-tag", "tag": "privileged"})
    write(m, USER, {"op": "tag", "tag": "privileged", "target": {"doc": m.doc}})
    with pytest.raises(Refused, match="Claude can't remove"):
        write(m, "claude", {"op": "untag", "tag": "privileged", "target": {"doc": m.doc}})
    with pytest.raises(Refused, match="Claude can't edit"):
        write(m, "claude", {"op": "edit-tag", "tag": "privileged", "color": "blue"})
    [ev] = write(m, USER, {"op": "note", "target": {"doc": m.doc}, "body": "mine"})
    with pytest.raises(Refused, match="can't change"):
        write(m, "claude", {"op": "edit-note", "note": ev["note"], "body": "changed"})
    with pytest.raises(Refused, match="can't change"):
        write(m, "claude", {"op": "delete-note", "note": ev["note"]})
    # Claude can reply, and the user can change Claude's work
    [reply] = write(m, "claude", {"op": "reply", "parent": ev["note"], "body": "a reply"})
    write(m, USER, {"op": "edit-note", "note": reply["note"], "body": "edited by the user"})
    a = Annotations.load(m)
    assert a.notes[reply["note"]]["body"] == "edited by the user"
    assert a.notes[reply["note"]]["target"] == {"doc": m.doc}


def test_exclusive_groups(m):
    for t in ("responsive", "not-responsive"):
        write(m, USER, {"op": "define-tag", "tag": t, "group": "resp"})
    write(m, "claude", {"op": "tag", "tag": "responsive", "target": {"doc": m.doc}})
    evs = write(m, "claude", {"op": "tag", "tag": "not-responsive", "target": {"doc": m.doc}})
    assert [(e["op"], e["tag"]) for e in evs] == [("untag", "responsive"), ("tag", "not-responsive")]
    # but not over the user's choice
    write(m, USER, {"op": "tag", "tag": "responsive", "target": {"doc": m.dupe}})
    with pytest.raises(Refused):
        write(m, "claude", {"op": "tag", "tag": "not-responsive", "target": {"doc": m.dupe}})
    assert current_tags(m)[m.dupe] == {"responsive"}


def test_targets_are_checked_and_fingerprinted(m):
    write(m, USER, {"op": "define-tag", "tag": "hot"})
    [ev] = write(m, "claude", {"op": "tag", "tag": "hot", "target": range_loc(m)})
    assert ev["target"]["quote"]["exact"] == "never received"
    bad = {**range_loc(m), "quote": {"exact": "something else"}}
    with pytest.raises(Refused, match="broken"):
        write(m, "claude", {"op": "tag", "tag": "hot", "target": bad})
    # tags are per copy: the duplicate isn't tagged
    assert current_tags(m) == {m.doc: {"hot"}}


def test_search_tag_filter_includes_ranges(m):
    write(m, USER, {"op": "define-tag", "tag": "hot"})
    write(m, "claude", {"op": "tag", "tag": "hot", "target": range_loc(m)})
    s = Searcher(fresh(m), Options())
    _, items = s.keyword("tag:hot")
    assert [i["loc"] for i in items] == ["doc:" + m.doc]
    _, items = Searcher(fresh(m), Options(tags=["-hot"])).keyword("shipment")
    assert [i["loc"] for i in items] == ["doc:" + m.dupe]


def test_note_check(m):
    ref = {**locators.char_locator(m.doc, *_layer_and_text(m), *_span(m, "never received")), "id": "nr"}
    body = f'Zoe wrote that :q{{#nr}}, and later "this is a long typed quotation here".\n\n```refs\n{json.dumps(ref)}\n```\n'
    results = literate.check(m, body)
    assert {"id": "nr", "status": "exact"} in results
    assert any(r.get("lint") == "unbacked-quote" for r in results)
    assert literate.check(m, "See :q{#missing}.") == [{"lint": "missing-ref", "id": "missing"}]


def _layer_and_text(m):
    state = fresh(m).state
    layer = state.primary_layer(state.documents[m.doc])
    return layer.id, m.objects.get_text(layer.text)


def _span(m, phrase):
    _, text = _layer_and_text(m)
    s = text.index(phrase)
    return s, s + len(phrase)


def test_api_stamps_claude(m, monkeypatch):
    sock = m.root / "run" / "a.sock"
    monkeypatch.setenv("ADHOC_ANN_SOCKET", str(sock))
    with pytest.raises(annapi.Unavailable):
        annapi.request(m, {"op": "define-tag", "tag": "hot"})
    threading.Thread(target=annapi.serve, args=(m, sock), daemon=True).start()
    for _ in range(50):
        if sock.exists():
            break
        time.sleep(0.05)
    out = annapi.request(m, {"op": "define-tag", "tag": "hot", "author": USER})
    assert out["events"][0]["author"] == "claude"
    out = annapi.request(m, {"op": "tag", "tag": "hot", "target": {"doc": m.doc}, "requested_by": "user"})
    assert out["events"][0]["requested_by"] == "user"
    out = annapi.request(m, {"op": "note", "target": {"doc": m.doc}, "body": 'He said "the goods never came to our dock"'})
    assert any(c.get("lint") == "unbacked-quote" for c in out["check"])
    write(m, USER, {"op": "define-tag", "tag": "mine"})
    write(m, USER, {"op": "tag", "tag": "mine", "target": {"doc": m.doc}})
    with pytest.raises(Refused):
        annapi.request(m, {"op": "untag", "tag": "mine", "target": {"doc": m.doc}})
