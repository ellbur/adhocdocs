"""Links between entities, facets, and the matter as a graph (GRAPH.md)."""

import base64

import pytest

from adhocdocs import graph
from adhocdocs.annotations import Annotations, Refused, write
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter

USER = "user:owen"

MSA = """MASTER SUPPLY AGREEMENT
Harrowgate Fastener Supply Co. ("Seller") and Bellwether Marine Systems, Inc. ("Buyer").
By: /s/ Raymond Voss
Raymond Voss, Controller
"""
POD = "Delivery receipt. Received at Dock 4 by Rita Ames, Bellwether receiving. Carrier: Kettering Freight.\n"
NOTE = "Voss chased the invoice again. Ames says the pallet never arrived. Harrowgate disagrees.\n"


@pytest.fixture
def m(tmp_path):
    m = Matter.create(tmp_path / "m")
    meta = lambda p: {"custodian": "cust:a", "source": {"collection": "c", "path": p}}
    m.msa = ingest_bytes(m, MSA.encode(), "msa.txt", meta("msa.txt"))
    m.pod = ingest_bytes(m, POD.encode(), "pod.txt", meta("pod.txt"))
    m.note = ingest_bytes(m, NOTE.encode(), "note.txt", meta("note.txt"))
    docs = (m.msa, m.pod, m.note)
    m = Matter(m.root)
    m.msa, m.pod, m.note = docs
    return m


def ent(m, name, kind, author="claude", **kw):
    return write(m, author, {"op": "define-entity", "name": name, "kind": kind, **kw})[0]["entity"]


def link(m, a, rel, b, author="claude", **kw):
    return write(m, author, {"op": "define-link", "from": a, "rel": rel, "to": b, **kw})[0]["link"]


@pytest.fixture
def cast(m):
    c = {
        "voss": ent(m, "Raymond Voss", "person", aliases=["Voss"]),
        "ames": ent(m, "Rita Ames", "person", aliases=["Ames"]),
        "harrowgate": ent(m, "Harrowgate Fastener Supply Co.", "organization", aliases=["Harrowgate"]),
        "bellwether": ent(m, "Bellwether Marine Systems, Inc.", "organization", aliases=["Bellwether"]),
        "msa": ent(m, "Master Supply Agreement", "contract", see=[{"doc": m.msa}]),
        "delivery": ent(m, "Dock 4 delivery", "event", date="2026-03-09"),
    }
    c["works"] = link(m, c["voss"], "works for", c["harrowgate"])
    c["signed"] = link(m, c["voss"], "signed", c["msa"], see=[{"doc": m.msa}])
    c["party"] = link(m, c["bellwether"], "party to", c["msa"], see=[{"doc": m.msa}])
    c["received"] = link(m, c["ames"], "signed for", c["delivery"], see=[{"doc": m.pod}], date="2026-03-09")
    c["ames_at"] = link(m, c["ames"], "works for", c["bellwether"], see=[{"doc": m.pod}])
    return c


# -- links ---------------------------------------------------------------------------------------

def test_links_join_entities(m, cast):
    a = Annotations.load(m)
    ln = a.links[cast["received"]]
    assert (ln["from"], ln["rel"], ln["to"], ln["date"]) == (cast["ames"], "signed for", cast["delivery"], "2026-03-09")
    assert ln["see"] == [{"doc": m.pod}] and ln["by"] == "claude"


def test_link_rules(m, cast):
    with pytest.raises(Refused, match="already link"):
        link(m, cast["voss"], "Works  For", cast["harrowgate"])            # folded: the same link
    with pytest.raises(Refused, match="no entity"):
        link(m, cast["voss"], "knows", "e_nobody")
    with pytest.raises(Refused, match="two different"):
        link(m, cast["voss"], "is", cast["voss"])
    with pytest.raises(Refused, match="relation"):
        link(m, cast["voss"], "  ", cast["ames"])
    with pytest.raises(Refused, match="date"):
        link(m, cast["voss"], "met", cast["ames"], date="March 9")
    # the user's field is the user's; Claude can fill in what's blank
    write(m, USER, {"op": "edit-link", "link": cast["works"], "summary": "Controller, per the signature block."})
    with pytest.raises(Refused, match="Claude can't change"):
        write(m, "claude", {"op": "edit-link", "link": cast["works"], "summary": "something else"})
    write(m, "claude", {"op": "edit-link", "link": cast["works"], "see": [{"doc": m.msa}]})
    with pytest.raises(Refused, match="user's work"):
        write(m, "claude", {"op": "delete-link", "link": cast["works"]})
    write(m, "claude", {"op": "delete-link", "link": cast["party"]})
    assert cast["party"] not in Annotations.load(m).links


def test_deleting_an_entity_takes_its_links(m, cast):
    user_link = link(m, cast["voss"], "emailed", cast["ames"], author=USER)
    with pytest.raises(Refused, match="user's links"):
        write(m, "claude", {"op": "delete-entity", "entity": cast["ames"]})
    write(m, USER, {"op": "delete-entity", "entity": cast["ames"]})
    a = Annotations.load(m)
    assert not {cast["received"], cast["ames_at"], user_link} & set(a.links)
    assert cast["signed"] in a.links


def test_the_same_link_from_two_installations_becomes_one(m, cast):
    """Replayed in merge order, a second define-link of the same relation joins the first."""
    a = Annotations.load(m)
    dup = {"id": "ev_zzzz", "at": "2026-11-03T00:00:00Z", "author": "claude", "op": "define-link", "link": "l_other",
           "from": cast["voss"], "rel": "works for", "to": cast["harrowgate"], "see": [{"doc": m.msa}], "summary": "s"}
    a.replay(dup)
    assert "l_other" not in a.links and a.link_alias["l_other"] == cast["works"]
    assert a.links[cast["works"]]["see"] == [{"doc": m.msa}]            # it adds what the first lacked
    assert a.conflicts[-1]["kind"] == "link-merged"
    a.replay({"id": "ev_zzzz1", "at": "t", "author": "claude", "op": "edit-link", "link": "l_other", "date": "2025"})
    assert a.links[cast["works"]]["date"] == "2025"


# -- facets ---------------------------------------------------------------------------------------

def test_facets_and_filing(m, cast):
    write(m, USER, {"op": "define-facet", "facet": "delivery", "description": "Were the goods delivered?"})
    with pytest.raises(Refused, match="already defined"):
        write(m, "claude", {"op": "define-facet", "facet": "delivery"})
    with pytest.raises(Refused, match="Claude can't edit"):
        write(m, "claude", {"op": "edit-facet", "facet": "delivery", "description": "x"})
    with pytest.raises(Refused, match="no facet 'deliveries'.*did you mean delivery"):
        write(m, "claude", {"op": "file", "facet": "deliveries", "item": {"entity": cast["ames"]}})
    with pytest.raises(Refused, match="an item is"):
        write(m, "claude", {"op": "file", "facet": "delivery", "item": {"document": m.pod}})
    with pytest.raises(Refused, match="no link"):
        write(m, "claude", {"op": "file", "facet": "delivery", "item": {"link": "l_nope"}})

    note = write(m, USER, {"op": "note", "target": {"doc": m.pod}, "body": "Ames's signature?"})[0]["note"]
    reply = write(m, "claude", {"op": "reply", "parent": note, "body": "It matches the MSA's style."})[0]["note"]
    write(m, "claude", {"op": "define-tag", "tag": "pod"})
    write(m, "claude", {"op": "file", "facet": "delivery", "item": {"note": note}})       # Claude may file the user's note
    write(m, USER, {"op": "file", "facet": "delivery", "item": {"link": cast["received"]}})
    write(m, "claude", {"op": "file", "facet": "delivery", "item": {"tag": "pod"}})
    assert write(m, "claude", {"op": "file", "facet": "delivery", "item": {"tag": "pod"}}) == []    # twice: nothing new

    a = Annotations.load(m)
    assert a.facets_of({"note": reply}) == ["delivery"]           # a reply is filed with its thread
    assert a.in_facet("delivery", {"tag": "pod"}) and not a.in_facet("delivery", {"entity": cast["voss"]})
    assert a.in_facet(None, {"entity": cast["voss"]})

    with pytest.raises(Refused, match="Claude can't unfile"):
        write(m, "claude", {"op": "unfile", "facet": "delivery", "item": {"link": cast["received"]}})
    write(m, "claude", {"op": "unfile", "facet": "delivery", "item": {"tag": "pod"}})
    write(m, USER, {"op": "delete-note", "note": note})
    a = Annotations.load(m)
    assert set(a.filed["delivery"]) == {f"link:{cast['received']}"}


# -- the graph ---------------------------------------------------------------------------------------

def test_graph_holds_entities_links_and_evidence(m, cast):
    g = graph.load(m)
    assert {n["label"] for n in g.nodes.values() if n["type"] == "entity"} >= {"Raymond Voss", "Rita Ames"}
    links = [e for e in g.edges if e["kind"] == "link"]
    assert len(links) == 5 and not any(e.get("derived") for e in links)
    sees = {(e["from"], e["to"]) for e in g.edges if e["kind"] == "see"}
    assert (f"entity:{cast['msa']}", f"doc:{m.msa}") in sees
    assert g.nodes[f"doc:{m.pod}"]["label"] == "pod.txt"


def test_paths_say_how_two_things_connect(m, cast):
    g = graph.load(m)
    paths = g.paths(f"entity:{cast['voss']}", f"entity:{cast['ames']}", max_len=4)
    assert paths and len(paths[0]) == 3          # Voss signed the MSA, Bellwether is party to it, Ames works for Bellwether
    steps = [graph.describe_step(g, s) for s in paths[0]]
    assert steps[0]["says"] == "Raymond Voss signed Master Supply Agreement" and steps[0]["evidence"] == [{"doc": m.msa}]
    assert steps[-1]["walked"] == "backwards"
    assert g.paths(f"entity:{cast['voss']}", f"entity:{cast['ames']}", max_len=2) == []
    # through documents only when asked
    gd = graph.load(m, facts=True)
    assert all(not s["to"].startswith("doc:") for p in gd.paths(f"entity:{cast['voss']}", f"entity:{cast['ames']}", 8, avoid_docs=True)
               for s in p[:-1])


def test_derived_edges_are_marked(m, cast):
    g = graph.load(m, mentions=True, cooccur=True, min_cooccur=1)
    co = [e for e in g.edges if e["kind"] == "co-mention"]
    pair = {f"entity:{cast['voss']}", f"entity:{cast['ames']}"}
    assert any({e["from"], e["to"]} == pair and e["derived"] and e["docs"] == [m.note] for e in co)
    mentions = [e for e in g.edges if e["kind"] == "mentions"]
    assert all(e["derived"] for e in mentions)
    assert any(e["from"] == f"entity:{cast['ames']}" and e["to"] == f"doc:{m.pod}" for e in mentions)
    path = g.paths(f"entity:{cast['voss']}", f"entity:{cast['ames']}", 1)[0]
    assert "a lead, not a relation" in graph.describe_step(g, path[0])["says"]


def test_facts_as_nodes(m, cast):
    g = graph.load(m, facts=True)
    fact = g.nodes[f"link:{cast['received']}"]
    assert fact["label"] == "Rita Ames signed for Dock 4 delivery"
    assert any(e["from"] == f"link:{cast['received']}" and e["to"] == f"doc:{m.pod}" and e["kind"] == "see" for e in g.edges)


def test_facet_narrows_the_graph(m, cast):
    write(m, USER, {"op": "define-facet", "facet": "delivery"})
    write(m, "claude", {"op": "file", "facet": "delivery", "item": {"link": cast["received"]}})
    g = graph.load(m, facet="delivery")
    assert {n["label"] for n in g.nodes.values() if n["type"] == "entity"} == {"Rita Ames", "Dock 4 delivery"}
    with pytest.raises(KeyError, match="no facet"):
        graph.load(m, facet="damages")


def test_around_and_resolve(m, cast):
    g = graph.load(m)
    voss = graph.resolve(m, g, "Voss")
    assert voss == f"entity:{cast['voss']}"
    near = g.around([voss], 1)
    assert {n["label"] for n in near.nodes.values()} == {"Raymond Voss", "Harrowgate Fastener Supply Co.", "Master Supply Agreement"}
    assert near.nodes[voss]["depth"] == 0
    assert graph.resolve(m, g, "pod.txt") == f"doc:{m.pod}"
    assert graph.resolve(m, g, m.pod[7:19]) == f"doc:{m.pod}"
    with pytest.raises(KeyError):
        graph.resolve(m, g, "nobody at all")


def test_unsupported_lists_what_rests_on_nothing(m, cast):
    rows = graph.unsupported(Annotations.load(m))
    assert {"link": cast["works"], "says": "Raymond Voss works for Harrowgate Fastener Supply Co.", "by": "claude"} in rows
    assert not any(r.get("link") == cast["signed"] for r in rows)


def test_exports(m, cast):
    g = graph.load(m)
    dot, mer = g.to_dot(), g.to_mermaid()
    assert '"Raymond Voss"' in dot and "works for" in dot and dot.startswith("digraph")
    assert mer.startswith("flowchart LR") and "|\"signed for\"|" in mer
    js = g.to_json()
    assert set(js) == {"nodes", "edges"}


def test_diagram(m, cast):
    g = graph.load(m)
    files, seq, summary = graph.diagram_html(m, g, "Who did what")
    page = base64.b64decode(files["index.html"]).decode()
    assert "<svg" in page and "Who did what" in page and "data-sequence" in page
    assert f'data-locator="doc:{m.pod}"' in page                  # "signed for" previews the receipt
    assert "unsupported" in page                                   # Voss works for Harrowgate: no evidence
    assert summary["links"] == 5 and summary["unsupported"] == 1
    assert seq and all(s.startswith("doc:") for s in seq)
    # an edge that skips a column passes between the boxes, not through them
    g2 = g.subgraph([f"entity:{cast['bellwether']}", f"entity:{cast['msa']}", f"entity:{cast['voss']}"])
    box, routes, _, _ = graph._layout(g2, graph._columns(g2, "kind"))
    party = next(e for e in g2.edges if e.get("rel") == "party to")
    assert len(routes[party["id"]]) == 3                           # through a waypoint in the people column
    empty = base64.b64decode(graph.diagram_html(m, graph.Graph(), "x")[0]["index.html"]).decode()
    assert "Nothing to show" in empty


# -- the CLI, through the annotation API ----------------------------------------------------------------

def test_cli(m, cast, monkeypatch, capsys):
    import json
    import threading
    import time

    from adhocdocs import annapi, cli
    sock = m.root / "run" / "a.sock"
    monkeypatch.setenv("ADHOC_ANN_SOCKET", str(sock))
    monkeypatch.setenv("ADHOC_MATTER", str(m.root))
    threading.Thread(target=annapi.serve, args=(m, sock), daemon=True).start()
    for _ in range(50):
        if sock.exists():
            break
        time.sleep(0.05)

    def run(fn, *argv):
        capsys.readouterr()
        fn(list(argv))
        return [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]

    out = run(cli.ann, "define-facet", "delivery", "--description", "Were the goods delivered?")
    assert out[0]["events"][0]["author"] == "claude"
    out = run(cli.ann, "link", "Rita Ames", "received", "Master Supply Agreement", "--see", "doc:" + m.pod, "--facet", "delivery")
    lid = out[0]["events"][0]["link"]
    assert out[0]["events"][1]["op"] == "file"
    rows = run(cli.ann, "links", "--entity", "Ames")
    assert {r["says"] for r in rows} >= {"Rita Ames received Master Supply Agreement", "Rita Ames signed for Dock 4 delivery"}
    assert run(cli.ann, "links", "--facet", "delivery")[0]["link"] == lid
    run(cli.ann, "edit-link", lid[:12], "--rel", "acknowledged")
    run(cli.ann, "file", "delivery", "--entity", "Ames")
    facet = run(cli.ann, "facet", "delivery")
    assert facet[0]["facet"] == "delivery" and {r["item"] for r in facet[1:]} == {"link", "entity"}
    assert run(cli.ann, "facets")[0]["filed"] == {"link": 1, "entity": 1}
    assert [e["name"] for e in run(cli.ann, "entities", "--facet", "delivery")] == ["Rita Ames"]
    row = run(cli.ann, "entity", "Ames")[0]
    assert row["facets"] == ["delivery"] and any(x["rel"] == "acknowledged" for x in row["links"])

    path = run(cli.graph, "path", "Voss", "Ames")
    assert path[0]["hops"] == 2 and path[0]["steps"][0]["says"] == "Raymond Voss signed Master Supply Agreement"
    ev = run(cli.graph, "evidence", "pod.txt")
    assert ev[0]["label"] == "pod.txt" and any(r.get("rel") == "signed for" for r in ev[1:])
    assert any(r.get("link") == cast["works"] for r in run(cli.graph, "unsupported"))
    shown = run(cli.graph, "show", "Ames")
    assert shown[0]["label"] == "Rita Ames" and any(r["edge"] == "link" for r in shown[1:])
    capsys.readouterr()
    cli.graph(["export", "--format", "mermaid", "--around", "Ames"])
    assert "flowchart LR" in capsys.readouterr().out
    run(cli.ann, "unfile", "delivery", "--entity", "Ames")
    run(cli.ann, "delete-link", lid)
    assert not any(r["link"] == lid for r in run(cli.ann, "links"))


def test_entity_page(m, cast):
    from adhocdocs import literate, locators
    text = m.objects.get_text(m.state.primary_layer(m.state.documents[m.pod]).text)
    s = text.index("Received at Dock 4 by Rita Ames")
    loc = locators.char_locator(m.pod, m.state.primary_layer(m.state.documents[m.pod]).id, text, s, s + 31)
    write(m, "claude", {"op": "edit-link", "link": cast["received"], "see": [loc]})
    page = graph.dossier(m, "Ames")
    assert page.startswith("# Rita Ames\n")
    assert "Rita Ames **signed for** Dock 4 delivery (2026-03-09)" in page
    assert "::q{#e1}" in page and "```refs" in page                 # the evidence, quoted by reference
    assert "[pod.txt](loc:doc:" in page                             # a whole document is a link
    assert "## In date order" not in page or "2026-03-09" in page
    assert "found by matching its names" in page
    assert literate.problems(literate.check(m, page)) == []


# -- an entity's timeline ------------------------------------------------------------------------

def test_entity_timeline(m, cast, tmp_path):
    from adhocdocs import lists, timeline
    write(m, "claude", {"op": "edit-link", "link": cast["ames_at"], "summary": "she signs as Bellwether receiving"})
    header, items, s = graph.timeline_list(m, ["Ames"])
    by = {it["fields"]["link"]: it for it in items}
    assert set(by) == {cast["received"], cast["ames_at"]} and s["no_evidence"] == []
    signed = by[cast["received"]]
    assert signed["loc"] == "doc:" + m.pod and signed["group"] == "Dock 4 delivery"
    assert signed["fields"]["date"] == "2026-03-09" and signed["fields"]["event"] == "Rita Ames signed for Dock 4 delivery"
    works = by[cast["ames_at"]]["fields"]
    assert works["undated"] and "date" not in works and s["undated"] == 1       # not guessed from the receipt

    path = tmp_path / "t.list.jsonl"
    lists.write(path, header, items)
    files, seq, t = timeline.timeline_html(m, path)
    out = base64.b64decode(files["index.html"]).decode()
    assert t["dated"] == 1 and t["undated"] == 1 and t["lanes"] == "group" and t["lane_names"] == ["Dock 4 delivery"]
    assert "Undated (1)" in out and "Rita Ames works for Bellwether" in out
    assert "she signs as Bellwether receiving" in out                          # the link's summary in its tooltip

    # --doc-dates: at the evidence's date (here, the receipt has none: still undated), lanes by relation
    _, items, s = graph.timeline_list(m, ["Ames"], doc_dates=True, lanes="rel")
    assert s["undated"] == 0 and {it["group"] for it in items} == {"signed for", "works for"}
    assert next(it for it in items if it["fields"]["link"] == cast["ames_at"])["fields"]["date_from"] == "document"
    _, _, t = timeline.timeline_html(m, (header, items))
    assert t["dated"] == 1 and t["undated"] == 1

    # a link with no evidence has nowhere to point: left out, and said
    _, items, s = graph.timeline_list(m, ["Voss"])
    assert [it["fields"]["link"] for it in items] == [cast["signed"]]
    assert s["no_evidence"] == [{"link": cast["works"], "says": "Raymond Voss works for Harrowgate Fastener Supply Co."}]

    # an event's own date and evidence, and links to a dated event take its date
    write(m, "claude", {"op": "edit-entity", "entity": cast["delivery"], "see": [{"doc": m.pod}]})
    rec = link(m, cast["bellwether"], "received", cast["delivery"], see=[{"doc": m.note}])
    _, items, _ = graph.timeline_list(m, ["Dock 4 delivery"])
    dates = {it["fields"].get("event"): it["fields"].get("date") for it in items}
    assert dates["Dock 4 delivery"] == "2026-03-09"
    assert dates["Bellwether Marine Systems, Inc. received Dock 4 delivery"] == "2026-03-09"
    assert any(it["fields"].get("link") == rec for it in items)

    # a facet keeps what is filed under it
    write(m, "claude", {"op": "define-facet", "facet": "delivery"})
    write(m, "claude", {"op": "file", "facet": "delivery", "item": {"link": cast["received"]}})
    _, items, _ = graph.timeline_list(m, ["Ames"], facet="delivery")
    assert [it["fields"]["link"] for it in items] == [cast["received"]]
    with pytest.raises(KeyError, match="no facet"):
        graph.timeline_list(m, ["Ames"], facet="nope")


def test_entity_timeline_cli(m, cast, tmp_path, monkeypatch, capsys):
    import json
    from adhocdocs import cli, lists, view
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()
    cli.graph(["--matter", str(m.root), "timeline", "Ames"])
    r = json.loads(capsys.readouterr().out.splitlines()[0])
    assert r["written"] == "timeline-rita-ames.list.jsonl" and r["items"] == 2 and r["next"].startswith("view timeline")
    header, items = lists.read(tmp_path / r["written"])
    assert header["title"] == "Timeline: Rita Ames" and len(items) == 2

    sent = []
    monkeypatch.setattr(view, "command", lambda m, c: sent.append(c) or {"pane": "p1"})
    cli.view(["--matter", str(m.root), "timeline", "--entity", "Voss"])
    c = sent[-1]
    assert c["kind"] == "timeline" and c["title"] == "Timeline: Raymond Voss" and c["sequence"] == ["doc:" + m.msa]
    r = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert r["timeline"]["no_evidence"][0]["link"] == cast["works"]
