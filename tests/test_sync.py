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

from adhocdocs import annotations, sync
from adhocdocs.annotations import CLAUDE, Refused
from adhocdocs.index import open_index
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter, new_event_id
from adhocdocs.origin import is_origin, origin_id
from adhocdocs.search import Options, Searcher


def text_doc(m, name, body, custodian="x"):
    return ingest_bytes(m, body.encode(), name, {"custodian": f"cust:{custodian}",
                                                 "source": {"collection": "c", "path": name}})


SYNCED = ("store/logs", "annotations/logs", "work/logs", "pool/logs")


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
    sync.refresh(Matter(dst.root))


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
        "entities": {k: (e["name"], sorted(e["aliases"]), e["summary"]) for k, e in ann.entities.items()},
        "conflicts": ann.conflicts,
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
    shutil.rmtree(m.root / "work")
    for d in ("store/logs", "annotations/logs"):
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


# -- step 2: conflicts, resolved the same way everywhere ----------------------------------


def pair(tmp_path):
    a = Matter.create(tmp_path / "a")
    b = install(tmp_path, "b", a.id)
    doc = text_doc(a, "a.txt", "the pallet")
    exchange(a, b)
    return a, b, doc


def write(m, author, **req):
    return annotations.write(Matter(m.root), author, req)


def state(m):
    return annotations.Annotations.load(Matter(m.root))


def kinds(m):
    return [c["kind"] for c in state(m).conflicts]


def test_a_tag_defined_on_two_installations(tmp_path):
    a, b, doc = pair(tmp_path)
    write(a, "user:alice", op="define-tag", tag="hot", color="red", hotkey="h")
    write(b, "user:bob", op="define-tag", tag="hot", color="orange")
    write(b, "user:bob", op="tag", tag="hot", target=f"doc:{doc}")
    exchange(a, b)
    s = state(a)
    assert s.definitions["hot"]["by"] == "user:alice" and s.definitions["hot"]["color"] == "red"
    assert kinds(a) == ["defined-twice"] and "orange" in s.conflicts[0]["reason"]
    assert [t["tag"] for t in s.applications()] == ["hot"]       # bob's tag still counts
    assert picture(a) == picture(b)


def test_the_same_definition_twice_is_no_conflict(tmp_path):
    a, b, _ = pair(tmp_path)
    write(a, "user:alice", op="define-tag", tag="hot", hotkey="h")
    write(b, "user:bob", op="define-tag", tag="hot", hotkey="h")
    exchange(a, b)
    assert kinds(a) == kinds(b) == []


def test_hotkeys_are_each_installations_own(tmp_path):
    a, b, _ = pair(tmp_path)
    write(a, "user:alice", op="define-tag", tag="hot", hotkey="h")
    write(b, "user:bob", op="define-tag", tag="helpful", hotkey="h")
    exchange(a, b)
    sa, sb = state(a), state(b)
    assert sa.definitions["hot"]["hotkey"] == "h" and "hotkey" not in sa.definitions["helpful"]
    assert sb.definitions["helpful"]["hotkey"] == "h" and "hotkey" not in sb.definitions["hot"]
    assert kinds(a) == kinds(b) == []
    # no event carries a hotkey, and changing one writes nothing to sync
    assert not any("hotkey" in e for e in Matter(a.root).annotation_log.read())
    n = Matter(b.root).annotation_log.log_for(b.origin).count()
    write(b, "user:bob", op="edit-tag", tag="hot", hotkey="j")
    assert Matter(b.root).annotation_log.log_for(b.origin).count() == n
    assert state(b).definitions["hot"]["hotkey"] == "j"
    with pytest.raises(Refused, match="in use"):
        write(b, "user:bob", op="edit-tag", tag="helpful", hotkey="j")


def test_hotkeys_from_before_stay_with_the_installation_that_set_them(tmp_path):
    """Matters from before kept hotkeys in tag definitions: each installation
    keeps those it set, and doesn't take on others'."""
    a, b, _ = pair(tmp_path)
    a.annotation_log.append(op="define-tag", tag="hot", hotkey="h", author="user:alice")
    b.annotation_log.append(op="define-tag", tag="cold", hotkey="c", author="user:bob")
    for m in (a, b):
        annotations.hotkeys_path(m).unlink(missing_ok=True)
    exchange(a, b)
    assert annotations.hotkeys(Matter(a.root)) == {"hot": "h"}
    assert state(b).definitions["cold"]["hotkey"] == "c" and "hotkey" not in state(b).definitions["hot"]


def test_exclusive_groups_hold_across_installations(tmp_path):
    a, b, doc = pair(tmp_path)
    for t in ("responsive", "nonresponsive"):
        write(a, "user:alice", op="define-tag", tag=t, group="resp")
    exchange(a, b)
    write(a, "user:alice", op="tag", tag="responsive", target=f"doc:{doc}")
    write(b, "user:bob", op="tag", tag="nonresponsive", target=f"doc:{doc}")      # later
    exchange(a, b)
    for m in (a, b):
        assert [t["tag"] for t in state(m).applications()] == ["nonresponsive"]
    assert kinds(a) == ["group-replaced"]
    # the viewer follows: the replay file says what bob's tag stands for
    replay = json.loads((a.root / "annotations/replay.json").read_text(encoding="utf-8"))
    (instead,) = replay["replace"].values()
    assert [(e["op"], e["tag"]) for e in instead] == [("untag", "responsive"), ("tag", "nonresponsive")]
    assert picture(a) == picture(b)


def test_claude_cant_replace_a_persons_group_tag_from_elsewhere(tmp_path):
    a, b, doc = pair(tmp_path)
    for t in ("responsive", "nonresponsive"):
        write(a, "user:alice", op="define-tag", tag=t, group="resp")
    exchange(a, b)
    write(a, "user:alice", op="tag", tag="responsive", target=f"doc:{doc}")
    write(b, CLAUDE, op="tag", tag="nonresponsive", target=f"doc:{doc}")
    exchange(a, b)
    assert [(t["tag"], t["by"]) for t in state(a).applications()] == [("responsive", "user:alice")]
    assert kinds(a) == ["refused"]


def test_claude_cant_untag_what_a_person_retagged_meanwhile(tmp_path):
    a, b, doc = pair(tmp_path)
    write(a, "user:alice", op="define-tag", tag="hot")
    write(a, CLAUDE, op="tag", tag="hot", target=f"doc:{doc}")
    exchange(a, b)
    write(a, "user:alice", op="untag", tag="hot", target=f"doc:{doc}")
    write(a, "user:alice", op="tag", tag="hot", target=f"doc:{doc}")           # now alice's
    write(b, CLAUDE, op="untag", tag="hot", target=f"doc:{doc}")               # b saw only Claude's
    exchange(a, b)
    (app,) = state(b).applications()
    assert app["by"] == "user:alice"
    assert [c["kind"] for c in state(b).conflicts] == ["refused"]
    assert "Claude can't remove it" in state(b).conflicts[0]["reason"]


def test_untagging_twice_is_quiet(tmp_path):
    a, b, doc = pair(tmp_path)
    write(a, "user:alice", op="define-tag", tag="hot")
    write(a, "user:alice", op="tag", tag="hot", target=f"doc:{doc}")
    exchange(a, b)
    write(a, "user:alice", op="untag", tag="hot", target=f"doc:{doc}")
    write(b, "user:bob", op="untag", tag="hot", target=f"doc:{doc}")
    exchange(a, b)
    assert state(a).applications() == [] and kinds(a) == []


def test_notes_edited_and_deleted_concurrently(tmp_path):
    a, b, doc = pair(tmp_path)
    (n,) = write(a, "user:alice", op="note", target=f"doc:{doc}", body="first")
    exchange(a, b)
    write(a, "user:alice", op="delete-note", note=n["note"])
    write(b, "user:bob", op="edit-note", note=n["note"], body="edited")
    (r,) = write(b, "user:bob", op="reply", parent=n["note"], body="a reply")
    exchange(a, b)
    s = state(a)
    assert n["note"] not in s.notes
    assert s.notes[r["note"]]["target"] == {"doc": doc}            # the reply keeps its place
    assert kinds(a) == ["refused", "parent-deleted"]
    assert picture(a) == picture(b)


def test_one_entity_added_on_two_installations(tmp_path):
    a, b, doc = pair(tmp_path)
    (ea,) = write(a, "user:alice", op="define-entity", name="Dana Okafor", kind="person")
    (eb,) = write(b, CLAUDE, op="define-entity", name="Dana Okafor", kind="person", aliases=["D. Okafor"],
                  summary="Bellwether's AP clerk")
    write(b, CLAUDE, op="edit-entity", entity=eb["entity"], add_aliases=["Dana"])
    exchange(a, b)
    for m in (a, b):
        s = state(m)
        assert list(s.entities) == [ea["entity"]]
        e = s.entities[ea["entity"]]
        assert set(e["aliases"]) == {"Dana Okafor", "D. Okafor", "Dana"} and e["summary"] == "Bellwether's AP clerk"
    assert kinds(a) == ["entity-merged"]
    # an edit naming either ID lands on the one entity
    write(b, "user:bob", op="edit-entity", entity=eb["entity"], recap="asked about the invoice")
    exchange(a, b)
    assert state(a).entities[ea["entity"]]["recap"] == "asked about the invoice"
    assert picture(a) == picture(b)


def forge(m: Matter, *events):
    """A log written without the write path (modified code, a hand edit)."""
    p = m.root / "annotations/logs" / ("o_" + "F" * 26 + ".jsonl")
    with open(p, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps({"id": new_event_id(), "at": "2026-11-01T00:00:00Z", **ev}) + "\n")


def test_rules_hold_against_a_log_that_broke_them(tmp_path):
    a, _, doc = pair(tmp_path)
    write(a, "user:alice", op="define-tag", tag="hot")
    write(a, "user:alice", op="define-tag", tag="privileged")
    write(a, "user:alice", op="tag", tag="hot", target=f"doc:{doc}")
    (n,) = write(a, "user:alice", op="note", target=f"doc:{doc}", body="mine")
    forge(a, {"op": "untag", "tag": "hot", "target": {"doc": doc}, "author": CLAUDE},
          {"op": "edit-note", "note": n["note"], "body": "changed", "author": CLAUDE},
          {"op": "tag", "tag": "undefined-tag", "target": {"doc": doc}, "author": "user:alice"},
          {"op": "tag", "tag": "privileged", "target": {"doc": doc}, "author": CLAUDE})
    s = state(a)
    assert {t["tag"] for t in s.applications()} == {"hot", "privileged"}
    assert s.notes[n["note"]]["body"] == "mine"
    assert [c["kind"] for c in s.conflicts] == ["refused", "refused", "refused", "privilege-call-by-claude"]
    # the write path still refuses outright
    other = text_doc(a, "b.txt", "another")
    with pytest.raises(Refused):
        write(a, CLAUDE, op="tag", tag="privileged", target=f"doc:{other}")


def test_sync_conflicts_command(tmp_path, capsys):
    from adhocdocs.cli import adhoc
    a, b, _ = pair(tmp_path)
    write(a, "user:alice", op="define-tag", tag="hot", color="red")
    write(b, "user:bob", op="define-tag", tag="hot", color="blue")
    copy_logs(b, a)
    (a.root / "annotations/replay.json").unlink()
    adhoc(["--matter", str(a.root), "sync", "conflicts"])
    (row,) = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    assert row["kind"] == "defined-twice" and row["origin"] == b.origin and row["tag"] == "hot"
    assert (a.root / "annotations/replay.json").exists()
    adhoc(["--matter", str(a.root), "sync", "status"])
    st = json.loads(capsys.readouterr().out)
    assert st["matter"] == a.id and st["origin"] == a.origin
    assert {o["origin"]: o["annotations"] for o in st["origins"]} == {a.origin: 1, b.origin: 1}


def test_replay_file_is_only_written_when_there_is_something_to_say(tmp_path):
    a, b, doc = pair(tmp_path)
    write(a, "user:alice", op="define-tag", tag="hot")
    write(a, "user:alice", op="tag", tag="hot", target=f"doc:{doc}")
    exchange(a, b)
    assert not (a.root / "annotations/replay.json").exists()
