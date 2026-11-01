"""The hub (SYNC.md step 5): pushing a matter to it, invitations through
it, and members syncing through it while each other's machines are off."""

import pytest

from adhocdocs import annotations, hub as hubmod, invite, peer, pool as poolmod, work
from adhocdocs.matter import Matter
from test_sync import picture, text_doc

ALICE, BOB = "user:alice", "user:bob"


@pytest.fixture
def hub(tmp_path):
    """A hub on localhost: its directory, server and address."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    addr = f"127.0.0.1:{port}"
    h = hubmod.Hub.init(tmp_path / "hub", "Firm hub", [addr])
    srv = peer.Server(h.matters(), "127.0.0.1", port, hub=h)
    srv.start()
    yield h, srv, addr
    srv.stop()


@pytest.fixture
def a(tmp_path):
    m = Matter.create(tmp_path / "a")
    text_doc(m, "dock.txt", "The pallet was never received at Dock 4.")
    annotations.write(m, ALICE, {"op": "define-tag", "tag": "hot"})
    return Matter(m.root)


def test_push_a_matter_to_the_hub(a, hub):
    h, srv, addr = hub
    code = h.enroll("alice")["code"]
    r = hubmod.push(a, code, ALICE)
    assert r["pushed"] and r["name"] == "Firm hub"
    (replica,) = h.matters()
    assert replica.id == a.id and picture(replica) == picture(a)
    roles = {x["name"]: x["role"] for x in poolmod.Pool.load(Matter(a.root)).active()}
    assert roles["Firm hub"] == "hub"
    assert poolmod.Pool.load(replica).is_member(replica.origin)
    # pushing again just syncs
    text_doc(a, "more.txt", "more")
    r = hubmod.push(Matter(a.root), code, ALICE)
    assert r["pushed"] and len(h.matters()) == 1


def test_join_through_the_hub_while_the_inviter_is_off(a, hub, tmp_path):
    """Alice's laptop never serves at all: Bob joins and syncs with her
    through the hub."""
    h, srv, addr = hub
    hubmod.push(a, h.enroll("alice")["code"], ALICE)
    inv = invite.create(Matter(a.root), ALICE)
    assert inv["through"] == "the hub" and inv["addresses"] == [addr]
    r = invite.join(inv["code"], tmp_path / "b", name="Bob's desktop")
    b = Matter(tmp_path / "b")
    assert picture(b) == picture(a)
    # Bob works, and syncs with every member he can reach: the hub
    d = text_doc(b, "bob.txt", "Bob's notes")
    annotations.write(b, BOB, {"op": "tag", "tag": "hot", "target": f"doc:{d}"})
    (b.workspace / "memo.md").write_text("# Memo\n", encoding="utf-8")
    work.publish(b, BOB, "memo.md")
    results = peer.sync_all(b)
    assert [x["name"] for x in results if "pushed" in x] == ["Firm hub"]
    assert any("skipped" in x for x in results)           # Alice doesn't listen anywhere
    # Alice, later, syncs with the hub and has it all
    peer.sync_all(Matter(a.root))
    assert picture(a) == picture(b)
    assert (a.root / "work/memo.md").exists()
    # Bob was added by the hub, on Alice's invitation
    added = next(e for e in Matter(a.root).pool_log.read() if e.get("member") == b.origin)
    assert added["author"] == ALICE and added["origin"] == h.matters()[0].origin


def test_only_enrolled_people_push(a, hub, tmp_path):
    h, srv, addr = hub
    code = h.enroll("alice")["code"]
    body = hubmod.parse(code)
    forged = hubmod.PREFIX + hubmod._b64({**body, "t": "guessed"})
    with pytest.raises(hubmod.HubError, match="403"):
        hubmod.push(a, forged, ALICE)
    assert h.matters() == []
    assert h.revoke("alice") == 1
    with pytest.raises(hubmod.HubError, match="403"):
        hubmod.push(Matter(a.root), code, ALICE)


def test_a_pusher_must_be_a_member(a, hub, tmp_path):
    """Someone with a copy of the matter, but not in its pool, can't put it on
    the hub as if it were theirs."""
    h, srv, addr = hub
    code = h.enroll("mallory")["code"]
    poolmod.found(a, ALICE, "Alice's laptop")
    x = Matter.create(tmp_path / "x", matter_id=a.id)
    cfg = x.config
    cfg["founder"] = a.config["founder"]
    from adhocdocs.matter import write_toml
    write_toml(x.root / "matter.toml", cfg)
    import shutil
    shutil.copytree(a.root / "pool", x.root / "pool", dirs_exist_ok=True)
    x = Matter(x.root)
    with pytest.raises(hubmod.HubError, match="isn't a member"):
        hubmod.push(x, code, "user:mallory")        # the client says so first
    # and the hub checks for itself
    req = {"token": hubmod.parse(code)["t"], "matter": x.id, "founder": x.config["founder"],
           "pool": {o: list(x.pool_log.log_for(o).events()) for o in x.pool_log.vector()},
           "cert": x.identity.cert_path().read_text(encoding="ascii")}
    with pytest.raises(peer.SyncError, match="403"):
        peer.Peer(x, addr, h.identity.origin, sni=peer.HUB_SNI, base="/hub/v1").json("POST", "adopt", req)
    assert h.matters() == []


def test_the_client_checks_it_reached_the_hub(a, hub, tmp_path):
    h, srv, addr = hub
    body = hubmod.parse(h.enroll("alice")["code"])
    wrong = hubmod.PREFIX + hubmod._b64({**body, "h": a.identity.origin})
    with pytest.raises(hubmod.HubError, match="not"):
        hubmod.push(a, wrong, ALICE)


def test_a_restarted_hub_serves_what_it_holds(a, tmp_path):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    addr = f"127.0.0.1:{port}"
    h = hubmod.Hub.init(tmp_path / "hub", "Firm hub", [addr])
    srv = peer.Server(h.matters(), "127.0.0.1", port, hub=h)
    srv.start()
    hubmod.push(a, h.enroll("alice")["code"], ALICE)
    srv.stop()
    h2 = hubmod.Hub(tmp_path / "hub")
    srv = peer.Server(h2.matters(), "127.0.0.1", port, hub=h2)
    srv.start()
    try:
        text_doc(a, "later.txt", "written while the hub was down")
        (r,) = [x for x in peer.sync_all(Matter(a.root)) if "pushed" in x]
        assert r["pushed"]
        assert picture(h2.matters()[0]) == picture(a)
    finally:
        srv.stop()
