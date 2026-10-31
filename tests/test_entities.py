"""The entity layer: the entity list and its rules, finding aliases in text
(the same in Python and the viewer), and detected suggestions."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from adhocdocs import detect, entities
from adhocdocs.annotations import Annotations, Refused, write
from adhocdocs.index import open_index
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter

USER = "user:owen"
ROOT = Path(__file__).parent.parent
CASES = json.loads((ROOT / "tests" / "entity_match_cases.json").read_text())["cases"]

CONTRACT = """MASTER SUPPLY AGREEMENT
This Master Supply Agreement (the “Agreement”) is made as of November 3, 2025 between Harrowgate Fastener Supply Co., an Ohio corporation (“Seller”), and Bellwether Marine Systems, Inc., a Virginia corporation (“Buyer”).
Seller will sell and Buyer will purchase the goods. Payment is due fifteen (15) days after the invoice date (“Net 15”).
“Delivery” means physical tender at Buyer’s receiving dock. Seller will invoice on PO-BMS-26-0112 and invoice INV-2026/0347.
By: /s/ Raymond Voss
Raymond Voss, Controller
"""


@pytest.fixture
def m(tmp_path):
    m = Matter.create(tmp_path / "m")
    doc = ingest_bytes(m, CONTRACT.encode(), "msa.txt", {"custodian": "cust:a", "source": {"collection": "c", "path": "msa.txt"}})
    other = ingest_bytes(m, b"Voss chased INV-2026-0347 again. Mr. Voss said Seller is late.\n", "note.txt",
                         {"custodian": "cust:a", "source": {"collection": "c", "path": "note.txt"}})
    m = Matter(m.root)
    m.doc, m.other = doc, other
    return m


def define(m, author="claude", **kw):
    return write(m, author, {"op": "define-entity", **kw})[0]["entity"]


# -- matching ----------------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=[c["text"][:30] for c in CASES])
def test_matching_cases(case):
    got = [[x["start"], x["end"], x["entities"]] for x in entities.find(case["text"], case["entities"])]
    assert got == case["expect"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_viewer_matches_the_same():
    """viewer/shell/entities.js gives the same answers as entities.find."""
    script = """
const {findEntityMentions} = require(process.argv[1]);
const cases = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")).cases;
console.log(JSON.stringify(cases.map((c) => findEntityMentions(Array.from(c.text), c.entities).map((x) => [x.start, x.end, x.entities]))));
"""
    out = subprocess.run(["node", "-e", script, str(ROOT / "viewer/shell/entities.js"), str(ROOT / "tests/entity_match_cases.json")],
                         capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [c["expect"] for c in CASES]


# -- the entity list ------------------------------------------------------------------------------

def test_define_and_find(m):
    e = define(m, name="Raymond Voss", kind="person", aliases=["Voss", "  R.  Voss "], summary="Harrowgate's controller.")
    ent = Annotations.load(m).entities[e]
    assert ent["aliases"] == ["Raymond Voss", "Voss", "R. Voss"]
    assert ent["by"] == "claude" and ent["summary"] == "Harrowgate's controller."
    assert entities.resolve(m, "voss")["entity"] == e                  # by alias, folded
    found = entities.mentions(m, [ent])
    assert {x["loc"]["quote"]["exact"] for x in found} == {"Raymond Voss", "Voss"}
    assert {x["loc"]["doc"] for x in found} == {m.doc, m.other}


def test_same_name_and_kind_refused(m):
    define(m, name="Raymond Voss", kind="person")
    with pytest.raises(Refused, match="already entity"):
        define(m, name="raymond  voss", kind="person")
    define(m, name="Raymond Voss", kind="event")                       # another kind is another thing


def test_claude_cant_change_the_users_work(m):
    e = define(m, author=USER, name="Marcus Delacroix-Hale", kind="person", summary="Their general counsel.")
    with pytest.raises(Refused, match="summary .* set by user:owen.*recap"):
        write(m, "claude", {"op": "edit-entity", "entity": e, "summary": "Bellwether's GC."})
    with pytest.raises(Refused, match="can't delete"):
        write(m, "claude", {"op": "delete-entity", "entity": e})
    with pytest.raises(Refused, match="can't remove"):
        write(m, "claude", {"op": "edit-entity", "entity": e, "remove_aliases": ["Marcus Delacroix-Hale"]})
    # but Claude can add what the user left: a recap, aliases, a place to see it
    write(m, "claude", {"op": "edit-entity", "entity": e, "recap": "You asked who signed for Buyer: he did.",
                        "add_aliases": ["Delacroix-Hale"]})
    ent = Annotations.load(m).entities[e]
    assert ent["recap"].startswith("You asked") and "Delacroix-Hale" in ent["aliases"]
    assert ent["set_by"]["recap"] == "claude" and ent["set_by"]["summary"] == USER
    # and the user can change anything, Claude's too
    write(m, USER, {"op": "edit-entity", "entity": e, "remove_aliases": ["Delacroix-Hale"], "recap": ""})
    write(m, USER, {"op": "delete-entity", "entity": e})
    assert e not in Annotations.load(m).entities


def test_user_marks_claude_describes(m):
    """The user adds a name with nothing else; the description is left for Claude."""
    e = define(m, author=USER, name="Seller")
    write(m, "claude", {"op": "edit-entity", "entity": e, "kind": "term", "summary": "Harrowgate, in the supply agreement."})
    ent = Annotations.load(m).entities[e]
    assert ent["kind"] == "term" and ent["summary"].startswith("Harrowgate")
    with pytest.raises(Refused, match="name"):
        write(m, "claude", {"op": "edit-entity", "entity": e, "name": "The Seller"})


def test_see_targets_are_checked(m):
    state = m.state
    layer = state.primary_layer(state.documents[m.doc])
    s = CONTRACT.index("By: /s/ Raymond Voss")
    e = define(m, name="Raymond Voss", kind="person",
               see=[{"doc": m.doc, "layer": layer.id, "select": {"char": [s, s + 20]}}])
    see = Annotations.load(m).entities[e]["see"]
    assert see[0]["quote"]["exact"] == "By: /s/ Raymond Voss"          # fingerprinted, like a tag's target
    with pytest.raises(Refused, match="broken"):
        define(m, name="X", see=[{"doc": m.doc, "layer": layer.id, "select": {"char": [5, 10**6]}}])


# -- detection --------------------------------------------------------------------------------------

def test_detects_terms_with_what_they_mean():
    ds = [d for d in detect.detect(CONTRACT) if d.kind == "term"]
    means = {d.form: d.means for d in ds if d.basis == ["definition"]}
    assert means["Seller"] == "Harrowgate Fastener Supply Co., an Ohio corporation"
    assert means["Buyer"] == "Bellwether Marine Systems, Inc., a Virginia corporation"
    assert means["Agreement"] == "Master Supply Agreement"
    assert means["Delivery"] == "physical tender at Buyer’s receiving dock"
    assert means["Net 15"] is None                                     # no name before it: the definition is shown instead
    net = next(d for d in ds if d.form == "Net 15")
    assert CONTRACT[net.defined[0]:net.defined[1]].startswith("Payment is due")
    uses = [d for d in ds if d.form == "Buyer" and d.basis == ["use"]]
    assert len(uses) == 2 and all(d.means.startswith("Bellwether") for d in uses)


def test_detects_organizations_numbers_and_people():
    ds = detect.detect(CONTRACT + "Copy to Pellham & Stroud LLP and BELLWETHER MARINE SYSTEMS, INC.\n")
    by_kind = {}
    for d in ds:
        by_kind.setdefault(d.kind, set()).add(d.form)
    assert by_kind["organization"] == {"Harrowgate Fastener Supply Co.", "Bellwether Marine Systems, Inc.",
                                      "Pellham & Stroud LLP", "BELLWETHER MARINE SYSTEMS, INC."}
    assert by_kind["identifier"] == {"PO-BMS-26-0112", "INV-2026/0347"}
    assert "Raymond Voss" in by_kind["person"]
    po = next(d for d in ds if d.form == "INV-2026/0347")
    assert po.key == "identifier:INV-2026-0347" and po.label == "invoice"
    keys = {d.key for d in ds if d.kind == "organization"}
    assert "organization:bellwether marine systems" in keys and len(keys) == 3      # capitals are the same company


@pytest.mark.parametrize("text, absent", [
    ("made between Harrowgate Fastener Supply Co., an Ohio corporation", "an Ohio corporation"),
    ("Receiving Supervisor of Respondent Bellwether Marine Systems, Inc.", "Receiving Supervisor of Respondent Bellwether Marine Systems, Inc."),
    ("per ISO-9001 and COVID-19 rules", "ISO-9001"),
    ("ask the brown dog", "brown"),
])
def test_detection_leaves_out(text, absent):
    assert absent not in {d.form for d in detect.detect(text)}


def test_detections_do_not_overlap():
    ds = detect.detect("ordered by Bellwether Marine Systems, Inc. (“Bellwether”) and Bellwether paid.")
    spans = sorted((d.start, d.end) for d in ds)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
    assert any(d.form == "Bellwether Marine Systems, Inc." for d in ds)  # the longer name wins over the term inside it


# -- the index and the layer -------------------------------------------------------------------------

def test_index_writes_files_for_the_viewer(m):
    ix = open_index(m, "entities")
    ix.update()
    state = m.state
    layer = state.primary_layer(state.documents[m.doc])
    side = json.loads((ix.dir / "texts" / (layer.text.removeprefix("sha256:") + ".json")).read_text())
    assert any(d["form"] == "Seller" for d in side["detections"])
    cands = entities.candidates(m)
    assert cands["identifier:INV-2026-0347"]["docs"] == 2               # both spellings, both documents
    assert cands["identifier:INV-2026-0347"]["labels"] == ["invoice"]


def test_layer_known_first_then_suggestions(m):
    open_index(m, "entities").update()
    e = define(m, name="Raymond Voss", kind="person", aliases=["Voss"])
    lay = entities.layer(m, m.doc)
    assert [x["loc"]["quote"]["exact"] for x in lay["known"]] == ["Raymond Voss", "Raymond Voss"]
    assert all(x["entities"] == [e] for x in lay["known"])
    sug = {x["form"] for x in lay["suggested"]}
    assert "Seller" in sug and "Raymond Voss" not in sug                # a known entity is no longer a suggestion
    keys = entities.known_keys(entities.known(m))
    assert "person:raymond voss" in keys


def test_typed_quotations_in_a_summary_are_warned(m):
    from adhocdocs.annotations import entity_check
    warn = entity_check(m, {"summary": 'Controller. He wrote "we will escalate this to counsel" in March.'})
    assert warn and warn[0]["lint"] == "unbacked-quote" and warn[0]["field"] == "summary"
    assert entity_check(m, {"summary": "Harrowgate's controller.", "recap": "You asked who he is."}) == []
