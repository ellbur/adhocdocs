"""Publishing work product (SYNC.md, adhocdocs.work): the workspace is
private, and what's published is shared with the matter's other
installations."""

import json
import threading
import time

import pytest

from adhocdocs import annapi, literate, locators, pages, sync, work
from adhocdocs.annotations import CLAUDE, Refused
from adhocdocs.extract import Extraction, Layer
from adhocdocs.ingest import store_layers
from adhocdocs.matter import Matter
from test_sync import copy_logs, exchange, install, text_doc

USER = "user:alice"


@pytest.fixture
def a(tmp_path):
    m = Matter.create(tmp_path / "a")
    m.doc = text_doc(m, "dock.txt", "The pallet was never received at Dock 4.")
    return m


def page_quoting(m, name, phrase, layer=None, body="She wrote that :q{#q}.\n"):
    doc = m.doc
    s = m.state
    layer = layer or s.primary_layer(s.documents[doc]).id
    text = m.objects.get_text(s.layer(layer).text)
    i = text.index(phrase)
    loc = locators.char_locator(doc, layer, text, i, i + len(phrase))
    p = m.workspace / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body + "\n" + literate.format_refs([{"id": "q", **loc}]), encoding="utf-8", newline="\n")
    return p


def test_publish_lays_out_work_and_records_the_version(a):
    page_quoting(a, "memos/memo.md", "never received")
    r = work.publish(a, USER, "memos/memo.md")
    assert r["files"] == ["memos/memo.md"] and r["forks"] == []
    out = a.root / "work/memos/memo.md"
    assert out.read_text(encoding="utf-8") == (a.workspace / "memos/memo.md").read_text(encoding="utf-8")
    (row,) = work.listing(a)
    assert row["name"] == "memos/memo.md" and row["current"]["by"] == USER and row["current"]["kind"] == "page"
    assert work.bases(a)["memos/memo.md"]["version"] == r["version"]
    # publishing again unchanged adds nothing; a change revises the last version
    assert work.publish(a, USER, "memos/memo.md")["unchanged"]
    (a.workspace / "memos/memo.md").write_text("Revised.\n", encoding="utf-8")
    r2 = work.publish(a, USER, "memos/memo.md")
    assert [v["revises"] for v in work.history(a, "memos/memo.md")] == [[], [r["version"]]]
    assert out.read_text(encoding="utf-8") == "Revised.\n" and r2["forks"] == []


def test_a_published_page_renders_on_another_installation(a, tmp_path):
    p = page_quoting(a, "memo.md", "never received")
    work.publish(a, USER, "memo.md")
    b = install(tmp_path, "b", a.id)
    exchange(a, b)
    published = b.root / "work/memo.md"
    assert published.exists()
    html, results = pages.render(pages.Page(Matter(b.root), published))
    assert results == [{"id": "q", "status": "exact"}]
    assert "never received" in html


def test_a_workspace_layer_is_promoted_so_quotations_resolve_elsewhere(a, tmp_path):
    content = a.state.documents[a.doc]
    store_layers(a, content, Extraction(kind="text", layers=[Layer(view="ocr", text="The pallet was NEVER received.")]),
                 log="workspace", objects=a.workspace_objects)
    a = Matter(a.root)
    a.doc = next(iter(a.state.documents))
    ocr = next(l.id for l in a.state.layers[content] if l.view == "ocr")
    assert a.state.layer(ocr).feed == "workspace"
    page_quoting(a, "memo.md", "NEVER received", layer=ocr)
    r = work.publish(a, USER, "memo.md")
    assert r["promoted"] == [ocr]
    assert any(e["op"] == "add-layer" and e["layer"] == ocr and e.get("promoted") for e in Matter(a.root).logs["store"].read())
    b = install(tmp_path, "b", a.id)
    exchange(a, b)
    _, results = pages.render(pages.Page(Matter(b.root), b.root / "work/memo.md"))
    assert results == [{"id": "q", "status": "exact"}]


def test_a_pages_cite_style_goes_with_it(a):
    (a.workspace / "styles").mkdir()
    (a.workspace / "styles/firm.py").write_text("def cite(info):\n    return 'X'\n", encoding="utf-8")
    page_quoting(a, "memo.md", "never received", body="---\ncite-style: styles/firm.py\n---\nText :q{#q}.\n")
    assert work.publish(a, USER, "memo.md")["files"] == ["memo.md", "styles/firm.py"]
    assert (a.root / "work/styles/firm.py").exists()


def test_a_directory_publishes_whole(a):
    d = a.workspace / "productions/vol001"
    d.mkdir(parents=True)
    (d / "production.jsonl").write_text("{}\n", encoding="utf-8")
    (d / "HFS000001.pdf").write_bytes(b"%PDF-1.4 not really")
    (d / ".hidden").write_text("x", encoding="utf-8")
    r = work.publish(a, USER, "productions/vol001")
    assert r["files"] == ["productions/vol001/HFS000001.pdf", "productions/vol001/production.jsonl"]
    assert work.listing(a)[0]["current"]["kind"] == "files"


def test_only_work_product_in_the_workspace_publishes(a):
    for bad in ("../store/logs", "/etc/passwd", ".adhoc/log.jsonl", "a/.git/config", "missing.md"):
        with pytest.raises(Refused):
            work.publish(a, USER, bad)
    with pytest.raises(Refused):
        work.name_of(a, a.root / "annotations")


def test_someone_elses_page_must_be_checked_out_first(a, tmp_path):
    page_quoting(a, "memo.md", "never received")
    v1 = work.publish(a, USER, "memo.md")["version"]
    b = install(tmp_path, "b", a.id)
    exchange(a, b)
    (b.workspace / "memo.md").write_text("Bob's own memo.\n", encoding="utf-8")
    with pytest.raises(Refused, match="check it out first"):
        work.publish(b, "user:bob", "memo.md")
    # checking out won't clobber his unpublished file either
    with pytest.raises(Refused, match="changes that aren't published"):
        work.checkout(b, "memo.md")
    (b.workspace / "memo.md").unlink()
    assert work.checkout(b, "memo.md")["version"] == v1
    (b.workspace / "memo.md").write_text("Alice's memo, revised by Bob.\n", encoding="utf-8")
    r = work.publish(b, "user:bob", "memo.md")
    assert work.history(b, "memo.md")[-1]["revises"] == [v1] and r["forks"] == []


def test_revising_the_same_version_on_two_installations_forks(a, tmp_path):
    page_quoting(a, "memo.md", "never received")
    v1 = work.publish(a, USER, "memo.md")["version"]
    b = install(tmp_path, "b", a.id)
    exchange(a, b)
    work.checkout(b, "memo.md")
    (a.workspace / "memo.md").write_text("Alice's revision.\n", encoding="utf-8")
    (b.workspace / "memo.md").write_text("Bob's revision.\n", encoding="utf-8")
    va = work.publish(a, USER, "memo.md")["version"]
    vb = work.publish(b, "user:bob", "memo.md")["version"]
    exchange(a, b)
    for m in (a, b):
        (row,) = work.listing(Matter(m.root))
        assert row["current"]["version"] == vb and [f["version"] for f in row["forks"]] == [va]
        assert (m.root / "work/memo.md").read_text(encoding="utf-8") == "Bob's revision.\n"
        forks = list((m.root / "work/.forks/memo.md").rglob("memo.md"))
        assert [f.read_text(encoding="utf-8") for f in forks] == ["Alice's revision.\n"]
        assert [c["kind"] for c in sync.conflicts(Matter(m.root))] == ["fork"]
    # Alice merges the two and publishes; the fork is gone everywhere
    work.checkout(a, "memo.md", force=True)
    (a.workspace / "memo.md").write_text("Both revisions, merged.\n", encoding="utf-8")
    r = work.publish(a, USER, "memo.md", merge=True)
    assert sorted(work.history(a, "memo.md")[-1]["revises"]) == sorted([va, vb]) and r["forks"] == []
    exchange(a, b)
    assert not (b.root / "work/.forks").exists()
    assert sync.conflicts(Matter(b.root)) == []
    assert (b.root / "work/memo.md").read_text(encoding="utf-8") == "Both revisions, merged.\n"
    assert v1 in {r for v in work.history(b, "memo.md") for r in v["revises"]}


def test_retire_takes_it_out_of_work(a, tmp_path):
    page_quoting(a, "memo.md", "never received")
    work.publish(a, USER, "memo.md")
    b = install(tmp_path, "b", a.id)
    exchange(a, b)
    work.retire(b, "user:bob", "memo.md")
    exchange(a, b)
    assert not (a.root / "work/memo.md").exists() and work.listing(a) == []
    assert [v["op"] for v in work.history(a, "memo.md")] == ["publish", "retire"]
    # publishing it again starts it over
    work.publish(a, USER, "memo.md")
    assert (a.root / "work/memo.md").exists()


def test_claude_publishes_through_the_api(a, monkeypatch):
    sock = a.root / "run" / "a.sock"
    sock.parent.mkdir(exist_ok=True)
    monkeypatch.setenv("ADHOC_ANN_SOCKET", str(sock))
    threading.Thread(target=annapi.serve, args=(a, sock), daemon=True).start()
    for _ in range(50):
        if sock.exists():
            break
        time.sleep(0.05)
    page_quoting(a, "memo.md", "never received")
    out = annapi.request(a, {"name": "memo.md", "requested_by": "user", "author": USER}, "/publish")
    (row,) = work.listing(a)
    assert row["current"]["by"] == CLAUDE and row["current"]["requested_by"] == "user"
    with pytest.raises(Refused):
        annapi.request(a, {"name": "../annotations/logs"}, "/publish")
    annapi.request(a, {"name": "memo.md"}, "/retire")
    assert work.listing(a) == [] and out["version"]


def test_work_cli(a, capsys, monkeypatch):
    from adhocdocs.cli import adhoc, work as work_cli
    page_quoting(a, "memo.md", "never received")
    monkeypatch.chdir(a.workspace)
    adhoc(["--matter", str(a.root), "publish", "memo.md", "--user", "alice"])
    v = json.loads(capsys.readouterr().out)["version"]
    work_cli(["--matter", str(a.root), "ls"])
    assert json.loads(capsys.readouterr().out)["current"]["version"] == v
    work_cli(["--matter", str(a.root), "log", "memo.md"])
    assert json.loads(capsys.readouterr().out)["head"] is True


def test_work_syncs_with_the_vector(a, tmp_path):
    page_quoting(a, "memo.md", "never received")
    work.publish(a, USER, "memo.md")
    b = install(tmp_path, "b", a.id)
    copy_logs(a, b)
    assert sync.vector(Matter(b.root))["work"] == {a.origin: 1}
