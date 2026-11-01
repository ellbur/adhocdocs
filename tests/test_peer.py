"""Sync over the network (SYNC.md step 4): signed logs, the pool, the
protocol between members, and invitations. Real TLS on localhost."""

import json
import shutil

import pytest

from adhocdocs import annotations, invite, peer, pool as poolmod, work
from adhocdocs.matter import Matter
from adhocdocs.signing import BadLog, check_chain, event_hash
from adhocdocs.origin import cert_origin
from adhocdocs.signing import public_key
from test_sync import picture, text_doc

ALICE, BOB, CAROL = "user:alice", "user:bob", "user:carol"


def serve(*ms):
    srv = peer.Server(list(ms), "127.0.0.1", 0)
    srv.start()
    return srv, f"127.0.0.1:{srv.address[1]}"


@pytest.fixture
def a(tmp_path):
    m = Matter.create(tmp_path / "a")
    m.doc = text_doc(m, "dock.txt", "The pallet was never received at Dock 4.")
    annotations.write(m, ALICE, {"op": "define-tag", "tag": "hot", "hotkey": "h"})
    annotations.write(m, ALICE, {"op": "tag", "tag": "hot", "target": f"doc:{m.doc}"})
    fresh = Matter(m.root)
    fresh.doc = m.doc
    return fresh


@pytest.fixture
def served(a):
    srv, addr = serve(a)
    yield srv, addr
    srv.stop()


def key_of(m):
    return public_key(m.identity.public())


# -- signing ------------------------------------------------------------------------------------


def test_every_event_is_signed_and_chained(a):
    for feed in (a.logs["store"], a.annotation_log):
        events = list(feed.log_for(a.origin).events())
        check_chain(None, events, key_of(a))
    events = list(a.annotation_log.log_for(a.origin).events())
    tampered = [events[0], {**events[1], "tag": "cold"}]
    with pytest.raises(BadLog, match="isn't signed"):
        check_chain(None, tampered, key_of(a))
    with pytest.raises(BadLog, match="follow"):
        check_chain(None, events[1:], key_of(a))


def test_founding_seals_events_written_before_signing(tmp_path):
    m = Matter.create(tmp_path / "m")
    text_doc(m, "a.txt", "hello")
    log = m.logs["store"].log_for(m.origin)
    # as a log from before signing: no prev, no sig
    stripped = [{k: v for k, v in e.items() if k not in ("prev", "sig")} for e in log.events()]
    log.path.write_text("".join(json.dumps(e) + "\n" for e in stripped), encoding="utf-8")
    ids = [e["id"] for e in stripped]
    poolmod.found(m, ALICE, "Alice's laptop", ["127.0.0.1:1"])
    events = list(log.events())
    assert [e["id"] for e in events] == ids
    check_chain(None, events, key_of(m))
    with pytest.raises(poolmod.PoolError):
        poolmod.seal(m)            # shared now: no rewriting


# -- the pool -------------------------------------------------------------------------------------


def test_the_founder_adds_itself_and_others_by_their_certificates(a, tmp_path):
    poolmod.found(a, ALICE, "Alice's laptop", ["127.0.0.1:1"])
    p = poolmod.Pool.load(a)
    assert p.founder == a.origin and [x["name"] for x in p.active()] == ["Alice's laptop"]
    b = Matter.create(tmp_path / "b", matter_id=a.id)
    cert = b.identity.cert_path().read_text(encoding="ascii")
    poolmod.add_member(a, ALICE, cert, "Bob's desktop")
    p = poolmod.Pool.load(a)
    assert p.is_member(b.origin) and cert_origin(p.cert(b.origin))[0] == b.origin
    poolmod.remove_member(a, ALICE, b.origin)
    p = poolmod.Pool.load(a)
    assert not p.is_member(b.origin) and p.cert(b.origin)      # its old events still check


def test_a_non_member_cant_change_membership(a, tmp_path):
    poolmod.found(a, ALICE, "Alice's laptop", ["127.0.0.1:1"])
    x = Matter.create(tmp_path / "x", matter_id=a.id)
    # x writes membership events into its own pool log, and they reach a
    x.pool_log.append(op="add-member", member=x.origin, cert=x.identity.cert_path().read_text(encoding="ascii"),
                      name="intruder", role="member", addresses=[], author="user:mallory")
    shutil.copy(x.pool_log.log_for(x.origin).path, a.pool_log.dir)
    p = poolmod.Pool.load(Matter(a.root))
    assert not p.is_member(x.origin) and p.ignored and p.ignored[0]["reason"].endswith("wasn't a member")


# -- invitations and the protocol --------------------------------------------------------------------


def test_join_with_an_invitation_and_sync(a, served, tmp_path):
    _, addr = served
    inv = invite.create(a, ALICE, [addr], name="Alice's laptop")
    assert inv["code"].startswith("adhoc1.")
    r = invite.join(inv["code"], tmp_path / "b", name="Bob's desktop")
    b = Matter(tmp_path / "b")
    assert r["id"] == a.id and b.id == a.id
    assert r["pulled"]
    assert picture(a) == picture(b)
    assert {x["name"] for x in poolmod.Pool.load(b).active()} == {"Alice's laptop", "Bob's desktop"}
    # Bob added by the inviting member, in Alice's name
    added = [e for e in Matter(a.root).pool_log.read() if e.get("member") == b.origin]
    assert added[0]["author"] == ALICE and added[0]["origin"] == a.origin
    # the code works once
    with pytest.raises(invite.InviteError):
        invite.join(inv["code"], tmp_path / "c")


def test_changes_flow_both_ways(a, served, tmp_path):
    _, addr = served
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
    b = Matter(tmp_path / "b")
    # Bob adds a document, tags, and publishes a page; Alice tags too
    d = text_doc(b, "bob.txt", "Bob's notes on the invoice.")
    annotations.write(b, BOB, {"op": "tag", "tag": "hot", "target": f"doc:{d}"})
    (b.workspace / "memo.md").write_text("# Memo\n\nBob's memo.\n", encoding="utf-8")
    work.publish(b, BOB, "memo.md")
    annotations.write(a, ALICE, {"op": "define-tag", "tag": "cold"})
    r = peer.sync_with(b, addr, a.origin)
    assert "store/" + b.origin in r["pushed"] and "annotations/" + a.origin in r["pulled"]
    assert picture(a) == picture(b)
    # the server laid out what arrived
    assert (a.root / "work/memo.md").read_text(encoding="utf-8") == "# Memo\n\nBob's memo.\n"
    assert Matter(a.root).objects.get_text(Matter(a.root).state.documents[d])
    # nothing more to do the second time
    r = peer.sync_with(b, addr, a.origin)
    assert r["pulled"] == {} and r["pushed"] == {}


def test_three_members_without_a_direct_connection(a, served, tmp_path):
    """Carol joins through Bob; Alice and Carol never connect, yet agree."""
    _, addr_a = served
    invite.join(invite.create(a, ALICE, [addr_a])["code"], tmp_path / "b")
    b = Matter(tmp_path / "b")
    srv_b, addr_b = serve(b)
    try:
        invite.join(invite.create(b, BOB, [addr_b])["code"], tmp_path / "c")
        c = Matter(tmp_path / "c")
        text_doc(c, "carol.txt", "Carol's file.")
        annotations.write(c, CAROL, {"op": "note", "target": f"doc:{a.doc}", "body": "Carol was here."})
        peer.sync_with(c, addr_b, b.origin)          # Carol -> Bob
        peer.sync_with(b, addr_a, a.origin)          # Bob -> Alice
        assert picture(a) == picture(b) == picture(c)
        # and Alice knows Carol is a member, with her certificate, though they never met
        assert poolmod.Pool.load(Matter(a.root)).is_member(c.origin)
    finally:
        srv_b.stop()


def test_a_server_talks_only_to_members(a, served, tmp_path):
    _, addr = served
    invite.create(a, ALICE, [addr])
    x = Matter.create(tmp_path / "x", matter_id=a.id)
    from adhocdocs.matter import write_toml
    write_toml(x.root / "matter.toml", {**x.config, "founder": Matter(a.root).config["founder"]})   # knows both
    x = Matter(x.root)
    with pytest.raises(peer.SyncError, match="403"):
        peer.Peer(x, addr, a.origin).json("GET", "vector")
    # and the client won't talk to a server that isn't the origin it expects
    with pytest.raises(peer.SyncError, match="not "):
        peer.Peer(x, addr, x.origin).json("GET", "vector")


def test_a_removed_member_is_refused(a, served, tmp_path):
    _, addr = served
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
    b = Matter(tmp_path / "b")
    poolmod.remove_member(a, ALICE, b.origin)
    with pytest.raises(peer.SyncError, match="403"):
        peer.sync_with(b, addr, a.origin)


def test_a_tampered_log_is_refused(a, served, tmp_path):
    _, addr = served
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
    b = Matter(tmp_path / "b")
    annotations.write(b, BOB, {"op": "define-tag", "tag": "warm"})
    log = b.annotation_log.log_for(b.origin)
    lines = log.path.read_text(encoding="utf-8").splitlines()
    ev = json.loads(lines[-1])
    ev["tag"] = "privileged"           # changed after signing
    lines[-1] = json.dumps(ev)
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = peer.sync_with(b, addr, a.origin)
    assert [x["log"] for x in r["refused"]] == [f"annotations/{b.origin}"] and "isn't signed" in r["refused"][0]["error"]
    assert f"store/{b.origin}" not in r.get("refused", [])   # the other logs still sync
    assert "warm" not in annotations.Annotations.load(Matter(a.root)).definitions


def test_a_forked_log_is_refused(a, served, tmp_path):
    """A copy of an installation that kept writing as the same origin."""
    _, addr = served
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
    b = Matter(tmp_path / "b")
    annotations.write(a, ALICE, {"op": "define-tag", "tag": "x1"})
    peer.sync_with(b, addr, a.origin)
    # b's copy of a's log gets a different next event, signed with a's key
    from adhocdocs.signing import sign
    log = b.annotation_log.log_for(a.origin)
    last = log.last_event()
    forged = sign({"id": "ev_ZZZZZZZZZZZZZZZZZZZZZZZZZZ", "at": "x", "op": "define-tag", "tag": "x2", "author": ALICE},
                  a.identity.key, last)
    with open(log.path, "a", encoding="utf-8") as f:
        f.write(json.dumps(forged) + "\n")
    annotations.write(a, ALICE, {"op": "define-tag", "tag": "x3"})
    r = peer.sync_with(b, addr, a.origin)
    assert [x["log"] for x in r["refused"]] == [f"annotations/{a.origin}"] and "forked" in r["refused"][0]["error"]
    # longer on one side: caught by the chain
    annotations.write(a, ALICE, {"op": "define-tag", "tag": "x4"})
    r = peer.sync_with(b, addr, a.origin)
    assert "forked" in r["refused"][0]["error"]


def test_large_objects_stream(a, served, tmp_path, monkeypatch):
    _, addr = served
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
    b = Matter(tmp_path / "b")
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (3 << 20) + b"end")
    from adhocdocs.ingest import ingest_paths
    ingest_paths(b, [big], "b", "c")
    peer.sync_with(b, addr, a.origin)
    content = Matter(b.root).objects.put_file(big)
    assert Matter(a.root).objects.path(content).stat().st_size == (3 << 20) + 3


def test_one_server_serves_several_matters(a, tmp_path):
    other = Matter.create(tmp_path / "other")
    text_doc(other, "o.txt", "another case entirely")
    srv, addr = serve(a, other)
    try:
        invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
        invite.join(invite.create(other, ALICE, [addr])["code"], tmp_path / "o2")
        assert picture(Matter(tmp_path / "b"))["documents"] == picture(a)["documents"]
        assert picture(Matter(tmp_path / "o2"))["documents"] == picture(other)["documents"]
    finally:
        srv.stop()


def test_a_session_sees_the_pool_but_cant_change_it(a, tmp_path, monkeypatch):
    from adhocdocs import container
    poolmod.found(a, ALICE, "Alice's laptop", ["127.0.0.1:1"])
    monkeypatch.setenv("ADHOC_POOL_FOUNDER", Matter(a.root).config["founder"])      # as the launcher sets it
    st = container._sync_status(a.root / "pool" / "logs")
    assert [x["name"] for x in st["members"]] == ["Alice's laptop"]
    args = " ".join(container.session_args(Matter(a.root), "s", tmp_path / "sock"))
    assert f"{a.root}/pool:/matter/pool:ro" in args and f"{a.root}/work:/matter/work:ro" in args
    assert f"ADHOC_POOL_FOUNDER={a.origin}" in args
    assert "/sync" not in args.replace("/matter/", "")         # keys and invitations stay on the host


# -- people and installations ---------------------------------------------------------------------


def test_a_member_cant_write_as_someone_who_works_elsewhere(a, served, tmp_path):
    _, addr = served
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
    b = Matter(tmp_path / "b")
    # alice wrote on a before the pool: founding made her a's
    assert poolmod.Pool.load(Matter(a.root)).users[ALICE] == [a.origin]
    with pytest.raises(annotations.Refused, match="another installation"):
        annotations.write(b, ALICE, {"op": "define-tag", "tag": "mine"})
    # Bob, unclaimed until now, is claimed by the first installation he writes on
    annotations.write(b, BOB, {"op": "define-tag", "tag": "bobs"})
    peer.sync_with(b, addr, a.origin)
    assert poolmod.Pool.load(Matter(a.root)).users[BOB] == [b.origin]
    with pytest.raises(annotations.Refused):
        annotations.write(Matter(a.root), BOB, {"op": "define-tag", "tag": "not-bobs"})


def test_events_written_as_someone_else_dont_count(a, served, tmp_path):
    """b's code is modified to write as Alice anyway: on replay it has no effect."""
    _, addr = served
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
    b = Matter(tmp_path / "b")
    b.annotation_log.append(op="tag", tag="hot", target={"doc": a.doc}, author=ALICE)      # around the write path
    b.annotation_log.append(op="define-tag", tag="forged", author=ALICE)
    (b.workspace / "memo.md").write_text("# Not Alice's\n", encoding="utf-8")
    from adhocdocs import work as workmod
    b.work_log.append(op="publish", name="memo.md", publication=workmod._store(b).put_object(
        {"type": "publication", "v": 1, "files": {"memo.md": workmod._store(b).put_bytes(b"# Not Alice's\n")}}),
        revises=[], kind="page", author=ALICE)
    peer.sync_with(b, addr, a.origin)
    ann = annotations.Annotations.load(Matter(a.root))
    assert "forged" not in ann.definitions
    assert [c["reason"] for c in ann.conflicts if "doesn't write from" in c["reason"]]
    assert not (a.root / "work/memo.md").exists()


def test_vouching_for_another_installation_of_the_same_person(a, served, tmp_path):
    _, addr = served
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "desk")
    desk = Matter(tmp_path / "desk")
    poolmod.vouch(a, ALICE, desk.origin)
    peer.sync_with(desk, addr, a.origin)
    annotations.write(Matter(desk.root), ALICE, {"op": "define-tag", "tag": "from-the-desk"})
    peer.sync_with(desk, addr, a.origin)
    assert "from-the-desk" in annotations.Annotations.load(Matter(a.root)).definitions


def test_an_invitation_for_the_same_person(a, served, tmp_path):
    _, addr = served
    invite.join(invite.create(a, ALICE, [addr], same_person=True)["code"], tmp_path / "desk")
    desk = Matter(tmp_path / "desk")
    assert sorted(poolmod.Pool.load(desk).users[ALICE]) == sorted([a.origin, desk.origin])
    annotations.write(desk, ALICE, {"op": "define-tag", "tag": "desk"})      # allowed
