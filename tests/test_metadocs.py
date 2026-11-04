"""Metadocuments (METADOCS.md): units at other grains, search results on
them, recorded bundles, and graphs of them."""

import json
import threading
import time
from pathlib import Path

import pytest

from adhocdocs import graph, metadocs, purge
from adhocdocs.annotations import Annotations, Refused, write
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter
from adhocdocs.search import Options, Searcher
from corpus_files import story

CORPUS = Path(__file__).parent.parent / "corpus"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    m = Matter.create(tmp_path_factory.mktemp("md") / "m")
    for cust in ("muller-z", "okafor-d"):
        ingest_paths(m, story(cust, "*.eml"), cust, cust)
    ingest_paths(m, story("voss-r", "memo-*.pdf") + [CORPUS / "voss-r" / "INV-2026-0347.pdf"], "voss-r", "v")
    ingest_paths(m, [CORPUS / "muller-z" / "INV-2026-0347.pdf"], "muller-z", "muller-z")
    return m.root


@pytest.fixture
def m(built, tmp_path):
    """A fresh copy for each test, so bundles written in one don't leak."""
    import shutil
    shutil.copytree(built, tmp_path / "m")
    return Matter(tmp_path / "m")


def named(m, name, custodian=None):
    for d in m.state.live_documents():
        meta = m.objects.get_object(m.state.metadata[d])
        obj = m.objects.get_object(d)
        if meta.get("name") == name and (custodian is None or obj.get("custodian") == f"cust:{custodian}"):
            return d


def past_due(m):
    from adhocdocs import analysis
    t = next(t for t in analysis.threads(m) if "past due" in (t["subject"] or ""))
    return [x["loc"][4:] for x in t["messages"]], t


def search(m, q):
    s = Searcher(m, Options())
    return s.keyword(q)[1]


# -- units at each grain ---------------------------------------------------------------------------

def test_units_at_each_grain(m):
    u = metadocs.Units(m)
    msgs, t = past_due(m)
    first = msgs[0]
    copy = t["messages"][0]["copies"][0][4:]
    # any member names the thread; its canonical name is the first message
    assert u.unit(msgs[-1], "thread") == u.unit(copy, "thread") == "thread:" + first
    assert set(u.docs("thread:" + msgs[-1])) == {*msgs, copy}
    # one message from two custodians is one item
    assert u.unit(copy, "copies") == u.unit(first, "copies")
    a, b = named(m, "INV-2026-0347.pdf", "voss-r"), named(m, "INV-2026-0347.pdf", "muller-z")
    assert set(u.docs("copies:" + a)) == {a, b}
    # the memo and its revision are versions of one text, not copies
    v1 = named(m, "memo-collection-bellwether-2026-03-16.pdf")
    v2 = named(m, "memo-collection-bellwether-2026-03-18-rev.pdf")
    assert u.unit(v2, "versions") == u.unit(v1, "versions") and u.unit(v2, "copies") != u.unit(v1, "copies")
    # an attachment's family is its email
    pod = named(m, "pod-TCF-889104.pdf")
    fam = u.docs("family:" + pod)
    assert pod in fam and len(fam) == 2 and fam[0] != pod
    with pytest.raises(metadocs.MetaError):
        u.unit(pod, "chapter")


def test_new_is_a_message_without_what_it_quotes(m):
    u = metadocs.Units(m)
    msgs, _ = past_due(m)
    last = msgs[-1]
    parts = u.members("new:" + last)
    layer, text, segs = u.text(last)
    quoted = [(g["start"], g["end"]) for g in segs if g["kind"] == "quoted"]
    assert quoted and parts and all("char" in p["select"] for p in parts)
    for p in parts:
        s, e = p["select"]["char"]
        assert not any(s < qe and qs < e for qs, qe in quoted)
    d = u.describe("new:" + last)
    assert d["ref"] == "new:" + last and d["grain"] == "new"


def test_search_by_what_each_message_adds(m):
    """Words written once and quoted down a thread are one result, not one per reply."""
    items = search(m, '"past due"')
    msgs, t = past_due(m)
    assert len(items) >= 4
    units = metadocs.regroup(m, items, "new")
    assert len(units) == 1
    x = units[0]
    assert x["unit"] == "new:" + msgs[0]
    assert {d[4:] for d in x["quoted_in"]} == set(msgs[1:])
    # the subject line is the message's own; the quoted line came from Dana's
    # original, which isn't in the matter: it stays with the first message to quote it
    assert any(not h.get("quoted") for h in x["hits"]) and any(h.get("quoted") for h in x["hits"])
    # the copy from the other custodian adds nothing
    assert x["copies"] == 2 and len({tuple(h["loc"]["select"]["char"]) for h in x["hits"]}) == len(x["hits"])


def test_quoted_text_counts_for_the_message_that_wrote_it(m):
    u = metadocs.Units(m)
    msgs, _ = past_due(m)
    second, third = msgs[1], msgs[2]
    _, text, _ = u.text(second)
    own = u.own_ranges(second)
    body = text[own[0][0]:own[0][1]].split("\n\n")
    phrase = next(p for p in body if len(p.split()) > 8 and ":" not in p.split()[0])
    words = " ".join(phrase.split()[2:6])
    units = metadocs.regroup(m, search(m, f'"{words}"'), "new")
    x = next(x for x in units if x["unit"] == "new:" + second)
    assert "doc:" + third in x.get("quoted_in", [])
    assert all(not h.get("quoted") for h in x["hits"])           # it wrote them
    assert not any(y["unit"] == "new:" + third for y in units)


def test_search_by_thread_and_family(m):
    items = search(m, "pallet")
    threads = metadocs.regroup(m, items, "thread")
    assert len(threads) <= len(items)
    assert sum(len(x["docs"]) for x in threads) == len(items)
    for x in threads:
        assert x["grain"] == "thread" and x["size"] >= len(x["docs"])
    header = {"query": "pallet", "grain": "thread"}
    rows = metadocs.to_list(header, threads)
    assert rows[0]["grain"] == "thread" and len(rows) == len(threads) + 1


# -- bundles ---------------------------------------------------------------------------------------

def bundle(m, name, parts, author="claude", **kw):
    return write(m, author, {"op": "define-bundle", "name": name, "parts": parts, **kw})[0]["bundle"]


def test_a_bundle_is_tagged_and_noted_as_one(m):
    msgs, _ = past_due(m)
    b = bundle(m, "The chase", [{"doc": d} for d in msgs], summary="Harrowgate chasing payment")
    write(m, "user:a", {"op": "define-tag", "tag": "hot"})
    ev = write(m, "claude", {"op": "tag", "tag": "hot", "target": "bundle:The chase"})
    assert ev[0]["target"] == {"bundle": b}
    write(m, "claude", {"op": "note", "target": {"bundle": b}, "body": "One story, three emails."})
    a = Annotations.load(m)
    assert len([x for x in a.applications() if x["tag"] == "hot"]) == 1               # once, on the bundle
    assert {x["target"]["doc"] for x in a.applications(parts=True)} == set(msgs)        # for code that acts on documents
    tags = a.doc_tags()
    assert all("hot" in tags[d] for d in msgs)
    assert a.bundles_of(msgs[0])[0]["name"] == "The chase"
    # a second bundle's tag is its own application, not the first's
    other = bundle(m, "Invoice", [{"doc": named(m, "INV-2026-0347.pdf", "voss-r")}])
    write(m, "claude", {"op": "tag", "tag": "hot", "target": {"bundle": other}})
    assert len([x for x in Annotations.load(m).applications() if x["tag"] == "hot"]) == 2


def test_bundle_parts_can_be_passages(m):
    msgs, _ = past_due(m)
    u = metadocs.Units(m)
    parts = u.members("new:" + msgs[-1])
    b = bundle(m, "What Dana added", parts)
    a = Annotations.load(m)
    assert a.bundles[b]["parts"][0]["quote"]["exact"]
    assert metadocs.Units(m, a).docs("bundle:" + b) == [msgs[-1]]


def test_bundle_rules(m):
    msgs, _ = past_due(m)
    write(m, "user:a", {"op": "define-tag", "tag": "privileged"})
    b = bundle(m, "Users pack", [{"doc": msgs[0]}], author="user:a")
    with pytest.raises(Refused, match="already a bundle"):
        bundle(m, "users  PACK", [{"doc": msgs[1]}])
    with pytest.raises(Refused, match="needs parts"):
        bundle(m, "Empty", [])
    with pytest.raises(Refused, match="the user's"):
        write(m, "claude", {"op": "edit-bundle", "bundle": b, "add_parts": [{"doc": msgs[1]}]})
    with pytest.raises(Refused, match="the user's"):
        write(m, "claude", {"op": "delete-bundle", "bundle": b})
    # productions act on documents: no privilege call on a bundle, even the user's
    with pytest.raises(Refused, match="not bundles"):
        write(m, "user:a", {"op": "tag", "tag": "privileged", "target": {"bundle": b}})
    with pytest.raises(Refused, match="no bundle"):
        write(m, "claude", {"op": "note", "target": {"bundle": "b_nope"}, "body": "x"})
    with pytest.raises(Refused, match="not other bundles"):
        bundle(m, "Nested", [{"bundle": b}])
    # the user can change anything
    write(m, "user:a", {"op": "edit-bundle", "bundle": "Users pack", "add_parts": [{"doc": msgs[1]}],
                        "remove_parts": [{"doc": msgs[0]}]})
    assert Annotations.load(m).bundles[b]["parts"] == [{"doc": msgs[1]}]
    with pytest.raises(Refused, match="no parts"):
        write(m, "user:a", {"op": "edit-bundle", "bundle": b, "remove_parts": [{"doc": msgs[1]}]})


def test_claude_cant_delete_a_bundle_with_the_users_notes(m):
    msgs, _ = past_due(m)
    b = bundle(m, "Mine", [{"doc": msgs[0]}])
    n = write(m, "user:a", {"op": "note", "target": {"bundle": b}, "body": "keep this"})[0]["note"]
    with pytest.raises(Refused, match="tags or notes"):
        write(m, "claude", {"op": "delete-bundle", "bundle": b})
    write(m, "user:a", {"op": "define-facet", "facet": "payment"})
    write(m, "claude", {"op": "file", "facet": "payment", "item": {"bundle": "Mine"}})
    assert Annotations.load(m).facets_of({"bundle": b}) == ["payment"]
    write(m, "user:a", {"op": "delete-bundle", "bundle": b})
    a = Annotations.load(m)
    assert b not in a.bundles and n not in a.notes and not a.filed["payment"]


def test_the_same_bundle_from_two_installations_becomes_one(m):
    msgs, _ = past_due(m)
    b = bundle(m, "Chase", [{"doc": msgs[0]}])
    a = Annotations.load(m)
    a.replay({"id": "ev_zzzz", "at": "t", "author": "claude", "op": "define-bundle", "bundle": "b_other",
              "name": "chase", "parts": [{"doc": msgs[0]}, {"doc": msgs[1]}]})
    assert "b_other" not in a.bundles and a.bundle_alias["b_other"] == b
    assert a.bundles[b]["parts"] == [{"doc": msgs[0]}, {"doc": msgs[1]}]
    assert a.conflicts[-1]["kind"] == "bundle-merged"
    a.replay({"id": "ev_zzzz1", "at": "t", "author": "claude", "op": "edit-bundle", "bundle": "b_other", "summary": "s"})
    assert a.bundles[b]["summary"] == "s"


def test_a_purge_hides_a_bundles_part(m):
    msgs, _ = past_due(m)
    ev = {"op": "define-bundle", "bundle": "b_1", "name": "x", "author": "claude",
          "parts": [{"doc": msgs[0], "select": {"char": [0, 5]}, "quote": {"exact": "From:"}}, {"doc": msgs[1]}]}
    out = purge.redact(ev, {msgs[0]})
    assert out["parts"][0]["doc"] == msgs[0] and out["parts"][0]["quote"] != {"exact": "From:"}
    assert out["parts"][1] == {"doc": msgs[1]} and purge.may_hide(out, {msgs[0]})
    assert purge.reveal(out)["parts"] == [{"doc": msgs[1]}]


def test_search_by_bundle(m):
    msgs, _ = past_due(m)
    bundle(m, "The chase", [{"doc": d} for d in msgs])
    items = search(m, "pallet")
    units = metadocs.regroup(m, items, "bundle")
    assert sum(len(x["docs"]) for x in units) == len(items)
    x = next(x for x in units if x["grain"] == "bundle")
    assert x["name"] == "The chase"


# -- graphs of metadocuments -----------------------------------------------------------------------

def test_graph_by_thread(m):
    msgs, _ = past_due(m)
    g = graph.load(m)
    graph.resolve(m, g, msgs[1])
    graph.add_doc_links(m, g, 2)
    assert sum(1 for n in g.nodes.values() if n.get("doc") in msgs) >= 3
    t = graph.regrain(m, g, "thread")
    units = [n for n in t.nodes.values() if n.get("unit") == "thread:" + msgs[0]]
    assert len(units) == 1 and units[0]["documents"] >= 3
    uid = units[0]["id"]
    # replies and quotations inside the thread are gone; its family edges remain
    assert not any(e["from"] == e["to"] == uid for e in t.edges)
    assert any(uid in (e["from"], e["to"]) and e["kind"] == "family" for e in t.edges)


# -- the CLI ---------------------------------------------------------------------------------------

def test_cli(m, monkeypatch, capsys):
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

    msgs, _ = past_due(m)
    rows = run(cli.search, '"past due"', "--grain", "new")
    assert rows[0]["grain"] == "new" and rows[0]["matches"]["units"] == 1 and rows[1]["unit"] == "new:" + msgs[0]
    shown = run(cli.meta, "show", "thread:" + msgs[-1][:16])
    assert shown[0]["ref"] == "thread:" + msgs[0] and len(shown) == 1 + shown[0]["documents"]
    assert any(r["grain"] == "thread" for r in run(cli.meta, "of", msgs[1]))
    parts = run(cli.text, "new:" + msgs[-1])
    assert parts and all(p["status"] == "exact" and "#char=" in p["loc"] for p in parts)

    made = run(cli.ann, "bundle", "The chase", "thread:" + msgs[1], "--summary", "Harrowgate chasing")
    assert made[0]["parts"] == 4
    run(cli.ann, "define-tag", "hot")
    run(cli.ann, "tag", "hot", "bundle:the chase")
    listed = run(cli.ann, "bundles")
    assert listed[0]["tags"] == ["hot"] and listed[0]["from"] == "thread:" + msgs[0]
    one = run(cli.ann, "bundles", "chase")[0]
    assert one["name"] == "The chase" and len(one["parts"]) == 4 and one["tags"][0]["tag"] == "hot"
    assert {r["loc"][4:] for r in run(cli.ann, "find", "--tag", "hot")} == {p["loc"][4:] for p in one["parts"]}
    # a metadocument as a target is each of its parts
    run(cli.ann, "define-tag", "responsive")
    ev = run(cli.ann, "tag", "responsive", "thread:" + msgs[0])[0]["events"]
    assert len(ev) == 4 and all("doc" in e["target"] for e in ev)
    run(cli.ann, "edit-bundle", "chase", "--remove", "doc:" + msgs[0])
    assert run(cli.ann, "bundles")[0]["parts"] == 3
    assert run(cli.ann, "bundles", "--doc", msgs[0]) == []
    capsys.readouterr()
    cli.graph(["show", msgs[1], "--doc-links", "--grain", "thread"])
    first = json.loads(capsys.readouterr().out.splitlines()[0])
    assert first["unit"] == "thread:" + msgs[0]


# -- citing a metadocument in a page --------------------------------------------------------------

def test_a_page_cites_a_thread_as_a_whole(m, tmp_path):
    from adhocdocs import literate, pages
    msgs, _ = past_due(m)
    page = tmp_path / "p.md"
    page.write_text("# Chase\n\nHarrowgate chased payment :cite{#chase}, as [the thread](ref:chase) shows.\n", encoding="utf-8")
    ref = pages.add_ref(m, page, "chase", ["thread:" + msgs[1]])
    assert ref["meta"] == "thread:" + msgs[0] and len(ref["docs"]) == 4
    p = pages.Page(m, page)
    html, results = pages.render(p, standalone=False)
    assert "the thread “RE: INV-2026/0347 past due” (4 messages, " in html
    assert f'href="loc:doc:{msgs[0]}"' in html and "opens its first document" in html
    assert not literate.problems(results)
    assert {d for d, _ in pages.cited_docs(p)} == set(ref["docs"])
    # quoting it is refused: a metadocument has no one text
    page.write_text(page.read_text(encoding="utf-8").replace(":cite{#chase}", ":q{#chase}"), encoding="utf-8")
    bad = [r for r in literate.check(m, page.read_text(encoding="utf-8")) if r.get("id") == "chase"]
    assert bad[0]["status"] == "broken" and "metadocument" in bad[0]["error"]


def test_a_cited_bundle_that_changed_is_re_anchored(m, tmp_path):
    from adhocdocs import literate, pages
    msgs, _ = past_due(m)
    b = bundle(m, "Pack", [{"doc": msgs[0]}])
    page = tmp_path / "p.md"
    page.write_text("See :cite{#pack}.\n", encoding="utf-8")
    pages.add_ref(m, page, "pack", ["bundle:Pack"])
    assert literate.check(m, page.read_text(encoding="utf-8"))[0]["status"] == "exact"
    write(m, "claude", {"op": "edit-bundle", "bundle": b, "add_parts": [{"doc": msgs[1]}]})
    r = literate.check(m, page.read_text(encoding="utf-8"))[0]
    assert r["status"] == "re-anchored" and "1 more document" in r["error"]
    assert pages.update_refs(m, page)[0]["new"]["docs"] == [msgs[0], msgs[1]]
    assert literate.check(m, page.read_text(encoding="utf-8"))[0]["status"] == "exact"


# -- in the viewer: a metadocument's parts in a list pane -------------------------------------------

def test_parts_as_lists(m):
    from adhocdocs import lists
    msgs, t = past_due(m)
    header, items = metadocs.list_of(m, "thread:" + msgs[-1])
    assert header["query"] == "thread:" + msgs[0] and header["title"].endswith("(thread)")
    assert [it["loc"] for it in items] == [x["loc"] for x in t["messages"]]                 # reply order
    assert items[0]["children"][0]["fields"]["part"] == "copy"                               # its copy under it
    assert items[1]["fields"]["part"].lstrip("\u00a0").startswith("↳")                              # indented by depth

    pod = named(m, "pod-TCF-889104.pdf")
    _, items = metadocs.list_of(m, "family:" + pod)
    assert items[1]["loc"] == "doc:" + pod and not items[0]["fields"]["part"].startswith("\u00a0") and items[1]["fields"]["part"].startswith("\u00a0")

    _, items = metadocs.list_of(m, "new:" + msgs[-1])
    assert all("char" in lists.locators.parse(it["loc"])["select"] for it in items)

    v1 = named(m, "memo-collection-bellwether-2026-03-16.pdf")
    write(m, "claude", {"op": "define-bundle", "name": "Memo pages", "parts": [{"doc": v1, "select": {"page": 1}}, {"doc": pod}]})
    header, items = metadocs.list_of(m, "bundle:Memo pages")
    assert [it["fields"]["part"] for it in items] == ["1. p. 1 of memo-collection-bellwether-2026-03-16.pdf",
                                                       "2. pod-TCF-889104.pdf"]
    header, items = metadocs.around_list(m, pod)
    groups = list(dict.fromkeys(it["group"] for it in items))
    assert any(g.startswith("family:") for g in groups) and any(g.startswith("bundle: Memo pages") for g in groups)


def test_view_meta_command(m, monkeypatch, capsys, tmp_path):
    from adhocdocs import cli, view
    sent = []
    monkeypatch.setattr(view, "command", lambda m, c: sent.append(c) or {"pane": "p1"})
    msgs, t = past_due(m)
    capsys.readouterr()
    cli.view(["--matter", str(m.root), "meta", "thread:" + msgs[1], "--list", str(tmp_path / "t.list.jsonl")])
    c = sent[-1]
    assert c["kind"] == "list" and c["preview_new"] and c["sequence"][0] == t["messages"][0]["loc"]
    assert len(c["sequence"]) == len(metadocs.Units(m).docs("thread:" + msgs[0]))           # copies are rows too
    r = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert r["meta"]["items"] == len(t["messages"]) and (tmp_path / "t.list.jsonl").exists()
    cli.view(["--matter", str(m.root), "meta", msgs[1], "--of"])
    assert sent[-1]["title"].startswith("Around ")
    with pytest.raises(SystemExit):
        cli.view(["--matter", str(m.root), "meta", msgs[1]])
    assert "is a document" in capsys.readouterr().err


def test_meta_pane_for_the_viewer(m, capsys):
    """The viewer's "list parts" links run `adhoc meta-pane`: the same pane as `view meta`, as files."""
    import base64
    from adhocdocs import cli
    msgs, t = past_due(m)
    capsys.readouterr()
    cli.adhoc(["--matter", str(m.root), "meta-pane", "doc:" + msgs[1], "--of"])
    r = json.loads(capsys.readouterr().out)
    assert r["title"].startswith("Around ") and t["messages"][0]["loc"] in r["sequence"]
    assert "thread:" in base64.b64decode(r["files"]["index.html"]).decode()
    cli.adhoc(["--matter", str(m.root), "meta-pane", "thread:" + msgs[1]])
    assert json.loads(capsys.readouterr().out)["title"].endswith("(thread)")
    with pytest.raises(SystemExit):
        cli.adhoc(["--matter", str(m.root), "meta-pane", "doc:" + msgs[1]])
    assert "is a document" in capsys.readouterr().err


# -- grains joined: a thread with its attachments ------------------------------------------------

def test_joined_grains(m):
    from adhocdocs import lists
    u = metadocs.Units(m)
    pod = named(m, "pod-TCF-889104.pdf")                    # an attachment of an email in the proof-of-delivery thread
    email = u.family_root(pod)
    thread = set(u.docs("thread:" + email))
    joined = u.docs("family+thread:" + pod)                 # named in either order, from any member
    assert pod not in thread and set(joined) == thread | {pod} | {d for t in thread for d in u.docs("family:" + t)}
    assert u.unit(pod, "family+thread") == u.unit(email, "thread+family") == "thread+family:" + joined[0]
    assert metadocs.is_ref("family+thread:" + pod) and not metadocs.is_ref("thread+thread:" + pod)
    assert metadocs.canonical_grain("versions+thread") == "thread+versions" and metadocs.composite("doc+thread") is None
    with pytest.raises(metadocs.MetaError):
        u.unit(pod, "thread+new")
    # copies join too: the other custodian's copy of each message is already in its thread; +copies adds the rest
    assert set(u.docs("thread+family+copies:" + pod)) >= set(joined)

    d = u.describe("thread+family:" + pod)
    assert d["grain"] == "thread+family" and d["documents"] == len(joined)
    _, items = metadocs.list_of(m, "thread+family:" + pod)
    at = [it["loc"] for it in items].index("doc:" + pod)
    assert items[at]["fields"]["part"].lstrip(" ").startswith("↳") and items[at - 1]["loc"] == "doc:" + email
    # what a document is part of includes it, when it is more than the thread or the family
    assert any(x["grain"] == "thread+family" for x in u.units_of(pod))


def test_search_and_tags_at_a_joined_grain(m, capsys):
    from adhocdocs import cli
    pod = named(m, "pod-TCF-889104.pdf")
    items = search(m, '"proof of delivery"')
    units = metadocs.regroup(m, items, "thread+family")
    assert units and all(x["unit"].startswith("thread+family:") for x in units)
    assert len(units) <= len(metadocs.regroup(m, items, "thread"))
    capsys.readouterr()
    cli.search(["--matter", str(m.root), '"proof of delivery"', "--grain", "family+thread"])
    head = json.loads(capsys.readouterr().out.splitlines()[0])
    assert head["grain"] == "thread+family"
    with pytest.raises(SystemExit):
        cli.search(["--matter", str(m.root), "x", "--grain", "thread+new"])
    assert "joined by +" in capsys.readouterr().err
    # a joined unit is a target like any metadocument: each of its documents
    targets = cli._targets(m, ["thread+family:" + pod])
    assert {t["doc"] for t in targets} == set(metadocs.Units(m).docs("thread+family:" + pod))


# -- what a revision adds over the version before it ----------------------------------------------

def test_new_for_a_revision(m):
    u = metadocs.Units(m)
    v1 = named(m, "memo-collection-bellwether-2026-03-16.pdf")
    v2 = named(m, "memo-collection-bellwether-2026-03-18-rev.pdf")
    assert u.previous_version(v2) == v1 and u.previous_version(v1) is None
    text = u.text(v2)[1]
    added = [text[a:b] for a, b in u.new_ranges(v2)]
    assert any("Carrier position" in x for x in added) and "REVISED" in added
    assert not any("Raymond Voss" in x for x in added)              # kept from the first version
    parts = u.members("new:" + v2)
    assert parts and all("char" in p["select"] for p in parts)
    d = u.describe("new:" + v2)
    assert d["over"] == "doc:" + v1
    assert u.members("new:" + v1) == [{"doc": v1}]                  # the first version: all of it is its own
    # kept words go back to the version that wrote them
    s = text.index("Raymond Voss")
    src, a, b = u.version_origin(v2, s, s + len("Raymond Voss"))
    assert src == v1 and u.text(v1)[1][a:b] == "Raymond Voss"
    s = text.index("Carrier position")
    assert u.version_origin(v2, s, s + 16) is None


def test_search_new_counts_kept_words_once(m):
    v1 = named(m, "memo-collection-bellwether-2026-03-16.pdf")
    v2 = named(m, "memo-collection-bellwether-2026-03-18-rev.pdf")
    units = metadocs.regroup(m, search(m, "Bellwether"), "new")
    first = next(x for x in units if x["unit"] == metadocs.Units(m).unit(v1, "new"))
    assert "doc:" + v2 in first.get("kept_in", [])
    units = metadocs.regroup(m, search(m, '"Carrier position"'), "new")
    assert [x["unit"] for x in units] == [metadocs.Units(m).unit(v2, "new")]   # only the revision says it
