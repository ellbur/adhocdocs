"""Purging documents across installations (adhocdocs.purge)."""

import io
import json
import zipfile

import pytest

from adhocdocs import annotations, hub as hubmod, invite, peer, purge, search
from adhocdocs.annotations import CLAUDE, Refused
from adhocdocs.index import open_index
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter
from adhocdocs.signing import is_tombstone
from test_peer import ALICE, BOB, serve
from test_sync import picture, text_doc


def a_zip(m, name, files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, body in files.items():
            z.writestr(n, body)
    return ingest_bytes(m, buf.getvalue(), name, {"custodian": "cust:x", "source": {"collection": "c", "path": name}})


def found(m, q):
    s = search.Searcher(Matter(m.root), search.Options())
    _, items = s.finish(*s.keyword(q))
    return {i["loc"][4:] for i in items}


@pytest.fixture
def m(tmp_path):
    m = Matter.create(tmp_path / "a")
    m.bad = a_zip(m, "privileged.zip", {"memo.txt": "attorney advice about the pallet",
                                        "note.txt": "more privileged pallet talk"})
    m.keep = text_doc(m, "keep.txt", "an ordinary pallet document")
    annotations.write(m, ALICE, {"op": "define-tag", "tag": "hot"})
    annotations.write(m, ALICE, {"op": "tag", "tag": "hot", "target": f"doc:{m.bad}"})
    annotations.write(m, ALICE, {"op": "note", "target": f"doc:{m.bad}", "body": "says 'attorney advice'"})
    annotations.write(m, ALICE, {"op": "tag", "tag": "hot", "target": f"doc:{m.keep}"})
    open_index(m, "keyword").update()
    fresh = Matter(m.root)
    fresh.bad, fresh.keep = m.bad, m.keep
    return fresh


def objects(m):
    return {"sha256:" + p.parent.name + p.name for p in (m.root / "store/objects").rglob("*") if p.is_file()}


def test_a_purge_deletes_the_documents_and_what_names_them(m):
    kids = m.state.children[m.bad]
    assert len(kids) == 2 and found(m, "pallet") == {m.keep, *kids}
    before = objects(m)
    r = purge.purge(m, ALICE, [m.bad], "clawback: produced in error")
    assert r["documents"] == 3 and r["redacted_events"] == 2
    m2 = Matter(m.root)
    assert set(m2.state.live_documents()) == {m.keep}
    gone = before - objects(m2)
    assert m.bad in gone and all(k in gone for k in kids) and m2.state.documents[m.bad] in gone
    # tags and notes on it are tombstones; the rest stand
    events = list(m2.annotation_log.read())
    assert sum(is_tombstone(e) for e in events) == 2
    assert b"attorney advice" not in b"".join(p.read_bytes() for p in (m2.root / "annotations/logs").glob("*"))
    ann = annotations.Annotations.load(m2)
    assert [a["target"]["doc"] for a in ann.applications()] == [m.keep] and ann.notes == {}
    # out of the index, and its text with it
    open_index(m2, "keyword").update()
    assert found(m2, "pallet") == {m.keep} and found(m2, "attorney") == set()
    # ingesting the same file again doesn't bring it back
    again = a_zip(m2, "privileged.zip", {"memo.txt": "attorney advice about the pallet",
                                         "note.txt": "more privileged pallet talk"})
    assert again == m.bad and m.bad not in Matter(m.root).state.live_documents()


def test_only_a_person_purges_and_says_why(m):
    with pytest.raises(Refused, match="person"):
        purge.purge(m, CLAUDE, [m.bad], "because")
    with pytest.raises(Refused, match="why"):
        purge.purge(m, ALICE, [m.bad], "  ")


def test_a_shared_original_needs_all_its_documents(m):
    dup = ingest_bytes(m, b"an ordinary pallet document", "keep.txt",
                       {"custodian": "cust:y", "source": {"collection": "c", "path": "keep.txt"}})
    with pytest.raises(Refused, match="with-duplicates"):
        purge.purge(Matter(m.root), ALICE, [m.keep], "clawback")
    r = purge.purge(Matter(m.root), ALICE, [m.keep], "clawback", with_duplicates=True)
    assert r["documents"] == 2 and dup not in Matter(m.root).state.live_documents()


def test_text_another_document_has_stays(m):
    same = ingest_bytes(m, "\ufeffan ordinary pallet document".encode(), "copy.txt",
                        {"custodian": "cust:y", "source": {"collection": "c", "path": "copy.txt"}})   # another original
    s = Matter(m.root).state
    assert s.documents[same] != s.documents[m.keep]
    purge.purge(Matter(m.root), ALICE, [m.keep], "clawback")
    m2 = Matter(m.root)
    layer = m2.state.primary_layer(m2.state.documents[same])
    assert m2.objects.get_text(layer.text) == "an ordinary pallet document"


def test_a_purge_reaches_every_installation(m, tmp_path):
    srv, addr = serve(m)
    try:
        invite.join(invite.create(m, ALICE, [addr])["code"], tmp_path / "b")
        b = Matter(tmp_path / "b")
        (b.workspace / "draft.md").write_text(f"About doc:{m.bad} …", encoding="utf-8")
        assert m.bad in b.state.live_documents()
        purge.purge(Matter(m.root), ALICE, [m.bad], "protective order: case closed")
        r = peer.sync_with(b, addr, m.origin)
        assert "refused" not in r
        b2 = Matter(b.root)
        assert m.bad not in b2.state.live_documents() and m.bad not in objects(b2)
        assert sum(is_tombstone(e) for e in b2.annotation_log.read()) == 2
        assert picture(Matter(m.root)) == picture(b2)
        (n,) = purge.pending(b2)
        assert n["workspace_files"] == ["draft.md"] and n["reason"] == "protective order: case closed"
        purge.acknowledge(b2, n["purge"], BOB)
        assert purge.pending(Matter(b.root)) == []
        # the logs still check end to end, tombstones and all
        r = peer.sync_with(Matter(b.root), addr, m.origin)
        assert "refused" not in r
    finally:
        srv.stop()


def test_a_member_that_hasnt_heard_cant_bring_it_back(m, tmp_path):
    """b still has the document's objects and its tag events; it syncs again
    after the purge. Nothing comes back to a."""
    srv, addr = serve(m)
    try:
        invite.join(invite.create(m, ALICE, [addr])["code"], tmp_path / "b")
        b = Matter(tmp_path / "b")
        purge.purge(Matter(m.root), ALICE, [m.bad], "clawback")
        before = objects(Matter(m.root))
        text_doc(b, "new.txt", "something new")                  # b has something to push too
        peer.sync_with(b, addr, m.origin)
        assert m.bad not in objects(Matter(m.root)) and objects(Matter(m.root)) - before
        assert m.bad not in Matter(m.root).state.live_documents()
    finally:
        srv.stop()


@pytest.mark.parametrize("sealed", [True, False], ids=["sealed", "plain"])
def test_a_hub_purges_too_and_a_newcomer_never_gets_it(m, tmp_path, sealed):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    h = hubmod.Hub.init(tmp_path / "hub", "Firm hub", [f"127.0.0.1:{port}"], sealed=sealed)
    srv = peer.Server(h.matters(), "127.0.0.1", port, hub=h)
    srv.start()
    try:
        hubmod.push(m, h.enroll("alice")["code"], ALICE)
        from adhocdocs import seal
        name = seal.blind(seal.key(Matter(m.root)), m.bad) if sealed else m.bad
        (replica,) = h.matters()
        assert name in objects(replica)
        purge.purge(Matter(m.root), ALICE, [m.bad], "clawback")
        peer.sync_all(Matter(m.root))
        assert name not in objects(replica)
        if sealed:     # and the tag and note on it are tombstones there too
            logs = b"".join(p.read_bytes() for p in (replica.root / "annotations/logs").glob("*"))
            assert logs.count(b'"op": "redacted"') == 2
        invite.join(invite.create(Matter(m.root), ALICE)["code"], tmp_path / "c")
        c = Matter(tmp_path / "c")
        assert m.bad not in objects(c) and m.bad not in c.state.live_documents()
        assert set(c.state.live_documents()) == {m.keep}
    finally:
        srv.stop()


def test_purge_command(m, capsys):
    from adhocdocs.cli import adhoc
    adhoc(["--matter", str(m.root), "purge", m.bad, "--user", "alice", "--reason", "clawback", "--yes"])
    out = json.loads(capsys.readouterr().out)
    assert out["documents"] == 3
    adhoc(["--matter", str(m.root), "purges"])
    (n,) = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    assert n["by"] == ALICE and not n["acknowledged"]
