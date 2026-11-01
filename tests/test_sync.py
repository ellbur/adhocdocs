"""Syncing a matter across installations (SYNC.md).

Step 1, with no network: every installation appends only to its own logs,
and installations that hold the same logs agree on the matter, however and
in whatever order the logs reached them. Logs are exchanged here by copying
files, which is all a sync has to do at the storage level.
"""

import json
import shutil
from pathlib import Path

import pytest

from adhocdocs import annotations
from adhocdocs.index import open_index
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter, new_event_id
from adhocdocs.origin import is_origin, origin_id
from adhocdocs.search import Options, Searcher


def text_doc(m, name, body, custodian="x"):
    return ingest_bytes(m, body.encode(), name, {"custodian": f"cust:{custodian}",
                                                 "source": {"collection": "c", "path": name}})


SYNCED = ("store/logs", "annotations/logs")


def copy_logs(src: Matter, dst: Matter, origins=None):
    """What a sync does: bring dst's copy of each of src's logs up to src's
    (a log only grows, and has one writer, so the longer copy is the newer),
    and the objects they name."""
    for d in SYNCED:
        for p in sorted((src.root / d).glob("*.jsonl")):
            if origins is not None and p.stem not in origins:
                continue
            q = dst.root / d / p.name
            if not q.exists() or q.stat().st_size < p.stat().st_size:
                q.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, q)
    for p in (src.root / "store/objects").rglob("*"):
        q = dst.root / "store/objects" / p.relative_to(src.root / "store/objects")
        if p.is_file() and not q.exists():
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, q)


def exchange(a: Matter, b: Matter):
    copy_logs(a, b)
    copy_logs(b, a)


def install(tmp_path, name, matter_id) -> Matter:
    """Another installation of the same matter, on its own disk."""
    return Matter.create(tmp_path / name, matter_id=matter_id)


def picture(m: Matter) -> dict:
    """Everything a replica should agree on."""
    m = Matter(m.root)
    s = m.state
    ann = annotations.Annotations.load(m)
    return {
        "documents": s.documents, "withdrawn": s.withdrawn, "metadata": s.metadata,
        "children": {k: sorted(v) for k, v in s.children.items()},
        "layers": {c: sorted(l.id for l in ls) for c, ls in s.layers.items()},
        "primary": s.primary,
        "tags": {k: sorted(v) for k, v in ann.tags.items()},
        "definitions": sorted(ann.definitions),
        "notes": {k: n["body"] for k, n in ann.notes.items()},
        "events": [e["id"] for e in m.changes()] + [e["id"] for e in m.annotation_log.read()],
    }


def found(m: Matter, q: str) -> set[str]:
    s = Searcher(Matter(m.root), Options())
    _, items = s.finish(*s.keyword(q))
    return {i["loc"][4:] for i in items}


# -- identity -------------------------------------------------------------------------


def test_new_matter_has_an_id_and_writes_as_its_origin(tmp_path):
    m = Matter.create(tmp_path / "m")
    assert m.id.startswith("mat_")
    assert not (m.root / "sync").exists()          # made on first write, not first read
    doc = text_doc(m, "a.txt", "hello")
    assert is_origin(m.origin) and origin_id(m.identity.public()) == m.origin
    assert (m.root / "store/logs" / f"{m.origin}.jsonl").exists()
    ev = next(e for e in m.changes() if e["op"] == "add-document")
    assert ev["doc"] == doc and ev["origin"] == m.origin and ev["log"] == f"store/{m.origin}"
    assert Matter(m.root).origin == m.origin       # stable across opens


def test_a_copied_matter_gets_its_own_origin(tmp_path):
    a = Matter.create(tmp_path / "a")
    text_doc(a, "a.txt", "hello")
    shutil.copytree(a.root, tmp_path / "b")
    b = Matter(tmp_path / "b")
    assert b.id == a.id
    text_doc(b, "b.txt", "world")
    assert b.origin != a.origin
    assert json.loads((b.root / "sync/origin.json").read_text(encoding="utf-8"))["previous"] == a.origin
    # the copy didn't touch the original's log
    assert sorted(p.stem for p in (b.root / "store/logs").glob("*.jsonl")) == sorted([a.origin, b.origin])
    assert Matter(a.root).logs["store"].log_for(a.origin).count() == \
        Matter(b.root).logs["store"].log_for(a.origin).count()


# -- the clock ------------------------------------------------------------------------


def test_event_ids_sort_after_every_event_seen(tmp_path, monkeypatch):
    import adhocdocs.matter as matter_mod
    monkeypatch.setattr(matter_mod, "_last_ulid", [0, 0])   # this test's far future stays in this test
    m = Matter.create(tmp_path / "m")
    future = "ev_" + "Z" * 10 + "0" * 16       # from a machine whose clock is far ahead
    other = m.root / "store/logs" / ("o_" + "1" * 26 + ".jsonl")
    other.write_text(json.dumps({"id": future, "at": "x", "op": "withdraw", "doc": "d"}) + "\n", encoding="utf-8")
    ev = m.logs["store"].append(op="withdraw", doc="e")
    assert ev["id"] > future
    assert [e["id"] for e in m.changes()][-1] == ev["id"]
    assert new_event_id(after=ev["id"]) > ev["id"]


# -- migration ------------------------------------------------------------------------


def legacy_matter(tmp_path) -> tuple[Path, dict]:
    """A matter in the layout before per-origin logs: one log per feed."""
    m = Matter.create(tmp_path / "old")
    doc = text_doc(m, "a.txt", "the pallet was never received")
    m.annotation_log.append(op="define-tag", tag="hot", author="user:o")
    m.annotation_log.append(op="tag", tag="hot", target={"doc": doc}, author="user:o")
    ix = open_index(m, "keyword")
    ix.update()
    before = picture(m)
    last = m.logs["store"].last_id()
    # undo the new layout
    for d in SYNCED:
        logs = list((m.root / d).glob("*.jsonl"))
        assert len(logs) == 1
        logs[0].replace(m.root / d.replace("/logs", "/log.jsonl"))
        (m.root / d).rmdir()
    (m.root / "matter.toml").unlink()
    shutil.rmtree(m.root / "sync")
    with ix.db:
        ix._set_cursor({"store": last, "workspace": None})
    ix.db.close()
    return m.root, before


def test_migration_moves_the_logs_and_keeps_the_state(tmp_path):
    root, before = legacy_matter(tmp_path)
    m = Matter(root)
    assert m.id and not (root / "store/log.jsonl").exists() and not (root / "annotations/log.jsonl").exists()
    assert m.config["legacy_origin"] == m.origin
    assert (root / "store/logs" / f"{m.origin}.jsonl").exists()
    assert picture(m) == before
    # an index's old cursor still means "read everything up to here"
    ix = open_index(m, "keyword")
    assert ix.cursor() == {f"store/{m.origin}": m.logs["store"].last_id()}
    assert not ix.behind() and ix.update() == 0
    assert Matter(root).id == m.id                  # migrates once


def test_unmigrated_matter_reads_where_it_cant_be_written(tmp_path, monkeypatch):
    root, before = legacy_matter(tmp_path)
    import adhocdocs.matter as matter_mod
    monkeypatch.setattr(matter_mod.os, "access", lambda p, mode: False)    # as in a session
    m = Matter(root)
    assert (root / "store/log.jsonl").exists() and m.id is None
    assert picture(m)["documents"] == before["documents"]
    assert {e["log"] for e in m.logs["store"].read()} == {"store/legacy"}


# -- convergence ------------------------------------------------------------------------


def test_two_installations_converge(tmp_path):
    a = Matter.create(tmp_path / "a")
    b = install(tmp_path, "b", a.id)
    da = text_doc(a, "a.txt", "the pallet was never received at dock four")
    db_ = text_doc(b, "b.txt", "the invoice is past due")
    same_a = text_doc(a, "same.txt", "one file, ingested on both machines")
    same_b = text_doc(b, "same.txt", "one file, ingested on both machines")
    assert same_a == same_b
    annotations.write(a, "user:alice", {"op": "define-tag", "tag": "hot", "hotkey": "h"})
    exchange(a, b)
    annotations.write(b, "user:bob", {"op": "tag", "tag": "hot", "target": f"doc:{db_}"})
    annotations.write(a, "user:alice", {"op": "tag", "tag": "hot", "target": f"doc:{da}"})
    annotations.write(a, "claude", {"op": "note", "target": f"doc:{da}", "body": "Check the dock log."})
    exchange(a, b)
    pa, pb = picture(a), picture(b)
    assert pa == pb
    assert set(pa["documents"]) == {da, db_, same_a}
    assert len(pa["tags"]) == 2
    assert len({e["origin"] for e in Matter(a.root).changes() if "origin" in e}) == 2


def test_logs_arriving_by_way_of_a_third_installation(tmp_path):
    """A's changes reach C through B: C never sees A directly."""
    a = Matter.create(tmp_path / "a")
    b, c = install(tmp_path, "b", a.id), install(tmp_path, "c", a.id)
    text_doc(a, "a.txt", "from A")
    exchange(a, b)
    text_doc(b, "b.txt", "from B")
    exchange(b, c)
    text_doc(c, "c.txt", "from C")
    exchange(b, c)
    exchange(a, b)
    assert picture(a) == picture(b) == picture(c)
    assert len(Matter(c.root).state.live_documents()) == 3


def test_replay_order_doesnt_depend_on_arrival_order(tmp_path):
    a = Matter.create(tmp_path / "a")
    b, c, d = (install(tmp_path, n, a.id) for n in "bcd")
    doc = text_doc(a, "a.txt", "the pallet")
    exchange(a, b)
    b.logs["store"].append(op="withdraw", doc=doc, reason="clawback")
    exchange(a, b)
    a.logs["store"].append(op="add-document", doc=doc, content=a.state.documents[doc])   # restored
    # c hears from b first, d from a first
    copy_logs(b, c), copy_logs(a, c)
    copy_logs(a, d), copy_logs(b, d)
    assert picture(c) == picture(d) == picture(a)
    assert doc in Matter(c.root).state.live_documents()


def test_indexes_agree_whatever_order_events_arrive_in(tmp_path):
    """An index applies each event once as it arrives; a withdraw that
    arrives before the document it withdraws must still win."""
    a = Matter.create(tmp_path / "a")
    b, c = install(tmp_path, "b", a.id), install(tmp_path, "c", a.id)
    doc = text_doc(a, "a.txt", "the pallet was never received")
    keep = text_doc(a, "k.txt", "the pallet was weighed")
    open_index(a, "keyword").update()
    assert found(a, "pallet") == {doc, keep}
    exchange(a, b)
    b.logs["store"].append(op="withdraw", doc=doc, reason="clawback")
    # c indexes b's withdraw before it has a's logs at all
    copy_logs(b, c, origins={b.origin})
    open_index(c, "keyword").update()
    open_index(c, "fields").update()
    copy_logs(a, c)
    open_index(c, "keyword").update()
    open_index(c, "fields").update()
    assert found(c, "pallet") == {keep}
    assert set(open_index(c, "fields").rows()) == {keep}
    # a has already indexed the document, and hears of the withdraw later
    exchange(a, b)
    open_index(a, "keyword").update()
    assert found(a, "pallet") == {keep}
    for m in (a, c):
        assert open_index(Matter(m.root), "keyword").verify() == {"name": "keyword", "missing": 0, "extra": 0,
                                                                   "repaired": False}


def test_ann_log_cursor_catches_late_events(tmp_path):
    a = Matter.create(tmp_path / "a")
    b = install(tmp_path, "b", a.id)
    annotations.write(b, "user:bob", {"op": "define-tag", "tag": "cold"})     # written first, arrives later
    annotations.write(a, "user:alice", {"op": "define-tag", "tag": "hot"})
    seen = Matter(a.root).annotation_log.cursor()
    last = Matter(a.root).annotation_log.last_id()
    exchange(a, b)
    late = list(Matter(a.root).annotation_log.read(seen))
    assert [e["tag"] for e in late] == ["cold"]
    # an event ID as the cursor misses it: it sorts before what was read
    assert list(Matter(a.root).annotation_log.read(last)) == []


@pytest.mark.parametrize("n", [3])
def test_many_writers_one_order(tmp_path, n):
    """Everyone tags and untags concurrently; after exchanging, all agree."""
    first = Matter.create(tmp_path / "m0")
    ms = [first] + [install(tmp_path, f"m{i}", first.id) for i in range(1, n)]
    doc = text_doc(first, "a.txt", "shared")
    annotations.write(first, "user:u0", {"op": "define-tag", "tag": "hot"})
    for m in ms[1:]:
        exchange(first, m)
    for i, m in enumerate(ms):
        annotations.write(m, f"user:u{i}", {"op": "tag", "tag": "hot", "target": f"doc:{doc}"})
        if i % 2:
            annotations.write(m, f"user:u{i}", {"op": "untag", "tag": "hot", "target": f"doc:{doc}"})
    for _ in range(2):
        for x in ms:
            for y in ms:
                if x is not y:
                    copy_logs(x, y)
    pics = [picture(m) for m in ms]
    assert all(p == pics[0] for p in pics)
