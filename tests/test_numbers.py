"""Reserving Bates and exhibit numbers across installations (SYNC.md,
adhocdocs.numbers)."""

import json
import threading
import time

import pytest

from adhocdocs import annapi, annotations, hub as hubmod, invite, lists, numbers, peer, produce, sync
from adhocdocs.matter import Matter
from test_sync import exchange, install, text_doc

ALICE, BOB = "user:alice", "user:bob"


def a_list(m, tmp_path, name, docs):
    p = tmp_path / f"{name}.list.jsonl"
    lists.write(p, *lists.new(name, ["doc:" + d for d in docs]))
    return p


@pytest.fixture
def two(tmp_path):
    a = Matter.create(tmp_path / "a")
    docs = [text_doc(a, f"d{i}.txt", f"document {i}\n" * 3) for i in range(3)]
    b = install(tmp_path, "b", a.id)
    exchange(a, b)
    return Matter(a.root), Matter(b.root), docs


def test_the_next_production_starts_after_anothers_reservation(two, tmp_path):
    a, b, docs = two
    ra = produce.produce(a, a_list(a, tmp_path, "pa", docs[:2]), "vol001", "HFS")
    exchange(a, b)
    rb = produce.produce(b, a_list(b, tmp_path, "pb", docs[2:]), "vol002", "HFS")
    assert int(rb["begin"][3:]) == int(ra["end"][3:]) + 1
    exchange(a, b)
    ann = annotations.Annotations.load(Matter(a.root))
    assert [r["name"] for r in ann.ranges("bates:HFS")] == ["vol001", "vol002"]
    assert {r["origin"] for r in ann.ranges("bates:HFS")} == {a.origin, b.origin}
    assert sync.conflicts(Matter(a.root)) == []


def test_reserving_the_same_numbers_at_once_is_a_conflict(two, tmp_path):
    a, b, docs = two
    produce.produce(a, a_list(a, tmp_path, "pa", docs[:1]), "vol-a", "HFS")
    produce.produce(b, a_list(b, tmp_path, "pb", docs[1:2]), "vol-b", "HFS")      # hasn't heard of a's
    exchange(a, b)
    for m in (a, b):
        c = [x for x in sync.conflicts(Matter(m.root)) if x["kind"] == "numbers-overlap"]
        assert len(c) == 1 and "reserved already" in c[0]["reason"]
        ann = annotations.Annotations.load(Matter(m.root))
        assert len(ann.ranges("bates:HFS")) == 1                   # the earlier stands


def test_a_failed_production_gives_its_numbers_back(two, tmp_path, monkeypatch):
    a, b, docs = two
    monkeypatch.setattr(produce, "_load_files", lambda *x: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        produce.produce(a, a_list(a, tmp_path, "pa", docs), "vol001", "HFS")
    ann = annotations.Annotations.load(Matter(a.root))
    assert ann.ranges("bates:HFS") == [] and ann.next_number("bates:HFS") == 1
    assert not (a.workspace / "productions/vol001").exists()


def test_productions_from_before_reservations_are_reserved_when_the_series_is_used(two, tmp_path):
    a, b, docs = two
    produce.produce(a, a_list(a, tmp_path, "pa", docs[:2]), "vol001", "HFS")
    # as if made before reservations: none in the logs
    log = a.annotation_log.log_for(a.origin)
    keep = [e for e in log.events() if e["op"] != "reserve-numbers"]
    from adhocdocs.signing import sign
    prev, out = None, []
    for e in keep:
        e = sign({k: v for k, v in e.items() if k not in ("prev", "sig")}, a.identity.key, prev)
        out.append(e)
        prev = e
    log.path.write_text("".join(json.dumps(e) + "\n" for e in out), encoding="utf-8")
    r = produce.produce(Matter(a.root), a_list(a, tmp_path, "pc", docs[2:]), "vol002", "HFS")
    exchange(a, b)
    ann = annotations.Annotations.load(Matter(b.root))
    assert [x["name"] for x in ann.ranges("bates:HFS")] == ["vol001", "vol002"]
    assert ann.next_number("bates:HFS") == int(r["end"][3:]) + 1


def test_exhibits_reserve_too(two, tmp_path):
    from adhocdocs import exhibits
    a, b, docs = two
    exhibits.exhibits(a, a_list(a, tmp_path, "xa", docs[:2]), "dep-a")
    exchange(a, b)
    r = exhibits.exhibits(b, a_list(b, tmp_path, "xb", docs[2:]), "dep-b")
    assert r["first"] == "Exhibit 3"


def test_through_a_hub_two_installations_dont_clash(tmp_path, monkeypatch):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    h = hubmod.Hub.init(tmp_path / "hub", "Firm hub", [f"127.0.0.1:{port}"])
    srv = peer.Server(h.matters(), "127.0.0.1", port, hub=h)
    srv.start()
    try:
        a = Matter.create(tmp_path / "a")
        docs = [text_doc(a, f"d{i}.txt", f"document {i}") for i in range(2)]
        hubmod.push(Matter(a.root), h.enroll("alice")["code"], ALICE)
        invite.join(invite.create(Matter(a.root), ALICE)["code"], tmp_path / "b")
        b = Matter(tmp_path / "b")
        monkeypatch.setenv("ADHOC_USER", "alice")
        ra = produce.produce(Matter(a.root), a_list(a, tmp_path, "pa", docs[:1]), "vol-a", "HFS")
        # b hasn't synced since a's production; reserving syncs with the hub first
        monkeypatch.setenv("ADHOC_USER", "bob")
        rb = produce.produce(b, a_list(b, tmp_path, "pb", docs[1:]), "vol-b", "HFS")
        assert int(rb["begin"][3:]) == int(ra["end"][3:]) + 1
        assert sync.conflicts(Matter(b.root)) == []
    finally:
        srv.stop()


def test_a_session_reserves_through_the_api(two, tmp_path, monkeypatch):
    a, b, docs = two
    sock = a.root / "run" / "a.sock"
    sock.parent.mkdir(exist_ok=True)
    monkeypatch.setenv("ADHOC_ANN_SOCKET", str(sock))
    monkeypatch.setattr(numbers, "_in_session", lambda m: True)
    threading.Thread(target=annapi.serve, args=(a, sock), daemon=True).start()
    for _ in range(50):
        if sock.exists():
            break
        time.sleep(0.05)
    produce.produce(a, a_list(a, tmp_path, "pa", docs[:1]), "vol001", "HFS")
    (r,) = annotations.Annotations.load(Matter(a.root)).ranges("bates:HFS")
    assert r["by"] == "claude" and r["name"] == "vol001"
