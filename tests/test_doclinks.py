"""How documents relate (replies, quotations, versions, copies) and people
from email headers, in the graph (docs/GRAPH.md)."""

import json
from pathlib import Path

import pytest

from adhocdocs import analysis, entities, graph
from adhocdocs.annotations import write
from adhocdocs.index import open_index
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter
from corpus_files import story

CORPUS = Path(__file__).parent.parent / "corpus"


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    m = Matter.create(tmp_path_factory.mktemp("d") / "m")
    for cust in ("muller-z", "okafor-d"):
        ingest_paths(m, story(cust, "*.eml"), cust, cust)
    ingest_paths(m, story("voss-r", "memo-*.pdf") + [CORPUS / "voss-r" / "INV-2026-0347.pdf"], "voss-r", "v")
    ingest_paths(m, [CORPUS / "muller-z" / "INV-2026-0347.pdf"], "muller-z", "muller-z")
    return Matter(m.root)


def named(m, name, custodian=None):
    for d in m.state.live_documents():
        meta = m.objects.get_object(m.state.metadata[d])
        obj = m.objects.get_object(d)
        if meta.get("name") == name and (custodian is None or obj.get("custodian") == f"cust:{custodian}"):
            return d


def rels(links, d, rel):
    return {x["doc"] for x in links.get(d, []) if x["rel"] == rel}


def test_doc_links(m):
    links = analysis.doc_links(m)
    # replies from the headers, both ways
    past_due = next(t for t in analysis.threads(m) if "past due" in (t["subject"] or ""))
    first, second = (x["loc"][4:] for x in past_due["messages"][:2])
    assert first in rels(links, second, "replies-to") and second in rels(links, first, "replied-by")
    # versions: the memo and its revision, with how alike they are
    v1 = named(m, "memo-collection-bellwether-2026-03-16.pdf")
    v2 = named(m, "memo-collection-bellwether-2026-03-18-rev.pdf")
    assert v2 in rels(links, v1, "version")
    x = next(x for x in links[v1] if x["doc"] == v2)
    assert x["containment"] > 0.7
    # the same invoice from two custodians: a copy
    a, b = named(m, "INV-2026-0347.pdf", "voss-r"), named(m, "INV-2026-0347.pdf", "muller-z")
    assert b in rels(links, a, "copy")
    # no reply is also listed as a version of its parent
    for d, xs in links.items():
        replies = {x["doc"] for x in xs if x["rel"] in ("replies-to", "replied-by")}
        assert not replies & {x["doc"] for x in xs if x["rel"] == "version"}


def test_the_index_keeps_them_for_the_viewer(m):
    idx = open_index(m, "neardup")
    idx.update()
    data = json.loads(idx.links_path().read_text(encoding="utf-8"))
    assert data["v"] == 1 and data["as_of"] == m.cursor() and data["links"]
    assert idx.write_links() == data                    # unchanged store: not recomputed


def test_doc_links_in_the_graph(m):
    v1 = named(m, "memo-collection-bellwether-2026-03-16.pdf")
    v2 = named(m, "memo-collection-bellwether-2026-03-18-rev.pdf")
    g = graph.load(m)
    nid = graph.resolve(m, g, v1)
    graph.add_doc_links(m, g, 1)
    e = next(e for e in g.edges if e["kind"] == "version" and {e["from"], e["to"]} == {nid, f"doc:{v2}"})
    assert e["derived"] and "versions of one text" in graph.describe_step(g, {"from": nid, "edge": e, "to": f"doc:{v2}"})["says"]
    past_due = next(t for t in analysis.threads(m) if "past due" in (t["subject"] or ""))
    msgs = [x["loc"][4:] for x in past_due["messages"]]
    g = graph.load(m)
    a, c = graph.resolve(m, g, msgs[0]), graph.resolve(m, g, msgs[2])
    graph.add_doc_links(m, g, 2)
    path = g.paths(a, c, 3)[0]
    assert [s["edge"]["kind"] for s in path] == ["reply", "reply"] and not any(s["edge"].get("derived") for s in path)


def test_people_from_headers(m, monkeypatch):
    rows = entities.people(m)
    okafor = next(p for p in rows if p["name"] == "Dana Okafor")
    assert okafor["entity"] is None and okafor["looks_like"] == "person"
    assert "dana.okafor@harrowgatefastener.example" in okafor["missing_aliases"]
    assert entities.looks_like({"name": "Bellwether Everyone", "addresses": ["everyone@b.example"], "sent": 0, "received": 9}) == "group"
    assert entities.looks_like({"name": "Deals", "addresses": ["noreply@x.example"], "sent": 9, "received": 0}) == "bulk"
    assert entities.looks_like({"name": "Ohio Valley Association", "addresses": ["a@o.example"], "sent": 2, "received": 2}) == "organization"
    # case-only variants aren't separate aliases
    assert entities.person_aliases({"name": "Tom Kettering", "names": ["TOM KETTERING"], "written_as": ["T. Kettering"],
                                    "addresses": []}) == ["T. Kettering"]

    # known by name: the entity is matched, and lacks the address
    e = write(m, "claude", {"op": "define-entity", "name": "Dana Okafor", "kind": "person"})[0]["entity"]
    okafor = next(p for p in entities.people(m) if p["name"] == "Dana Okafor")
    assert okafor["entity"]["entity"] == e
    write(m, "claude", {"op": "edit-entity", "entity": e, "add_aliases": okafor["missing_aliases"]})
    z = write(m, "claude", {"op": "define-entity", "name": "Zoë Müller", "kind": "person",
                            "aliases": next(p for p in entities.people(m) if p["name"] == "Zoë Müller")["missing_aliases"]})[0]["entity"]
    assert next(p for p in entities.people(m) if p["name"] == "Dana Okafor")["missing_aliases"] == []

    # who emailed whom
    g = graph.load(m)
    graph.add_correspondence(m, g)
    emailed = [x for x in g.edges if x["kind"] == "emailed" and {x["from"], x["to"]} == {f"entity:{e}", f"entity:{z}"}]
    assert len(emailed) == 1 and emailed[0]["count"] == len(emailed[0]["docs"]) == emailed[0]["sent"] + emailed[0]["replied"]
    assert any(x["kind"] == "sent" and x["from"] == f"entity:{e}" for x in g.edges)
    assert "from the headers" in graph.describe_step(g, {"from": emailed[0]["from"], "edge": emailed[0], "to": emailed[0]["to"]})["says"]
    assert "sent" in entities.person_summary(okafor) and "received" in entities.person_summary(okafor)


def test_cli_people(m, monkeypatch, capsys):
    import threading
    import time

    from adhocdocs import annapi, cli
    sock = m.root / "run" / "p.sock"
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

    listed = run(cli.ann, "people", "--limit", "100")
    assert listed and all(p["entity"] is None and p["looks_like"] == "person" for p in listed)
    name = listed[0]["name"]
    added = run(cli.ann, "add-people", name)
    assert added[0]["added"] == name and any("@" in a for a in added[0]["aliases"])
    assert not any(p["name"] == name for p in run(cli.ann, "people", "--limit", "100"))
    shown = run(cli.graph, "show", name, "--email")
    assert shown[0]["label"] == name
    v1 = named(m, "memo-collection-bellwether-2026-03-16.pdf")
    rows = run(cli.graph, "show", v1, "--doc-links")
    assert any(r["edge"] == "version" and r.get("similarity") for r in rows[1:])
