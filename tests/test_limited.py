"""Limited copies (docs/SYNC.md): an installation that holds everything but the
originals, which a full member always holds."""

import pytest

from adhocdocs import annotations, hub as hubmod, invite, peer, pool as poolmod
from adhocdocs.matter import Matter
from test_hub import hub  # noqa: F401  (a fixture)
from test_peer import ALICE, BOB, CAROL, serve
from test_sync import picture, text_doc


def originals_here(m) -> set:
    m = Matter(m.root)
    store = peer.store_only(m)
    return {d for d, c in m.state.documents.items() if store.has(c)}


def text_of(m, doc) -> str:
    m = Matter(m.root)
    return m.objects.get_text(m.state.primary_layer(m.state.documents[doc]).text)


def pdf_doc(m, name, words):
    """A document whose original isn't its text (as most aren't)."""
    import pymupdf
    from adhocdocs.ingest import ingest_bytes
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), words)
    return ingest_bytes(m, d.tobytes(), name, {"custodian": "cust:x", "source": {"collection": "c", "path": name}})


@pytest.fixture
def a(tmp_path):
    m = Matter.create(tmp_path / "a")
    m.doc = pdf_doc(m, "dock.pdf", "The pallet was never received at Dock 4.")
    annotations.write(m, ALICE, {"op": "define-tag", "tag": "hot"})
    fresh = Matter(m.root)
    fresh.doc = m.doc
    return fresh


# -- the pool's rule ------------------------------------------------------------------------------


def test_someone_must_hold_every_original(a, tmp_path):
    poolmod.found(a, ALICE, "Alice's laptop")
    with pytest.raises(poolmod.PoolError, match="no other member"):
        poolmod.set_copy(Matter(a.root), ALICE, "limited")
    # written anyway (modified code, or two at once): the replay doesn't count it
    Matter(a.root).pool_log.append(op="set-copy", member=a.origin, copy="limited", author=ALICE)
    p = poolmod.Pool.load(Matter(a.root))
    assert p.holds_all(a.origin) and p.full_copies() == [a.origin]
    assert any(x["op"] == "set-copy" and "no other member" in x["reason"] for x in p.ignored)
    assert peer.holds_all(Matter(a.root))


def test_two_going_limited_at_once_leaves_one_full(tmp_path):
    """Each saw the other full; the replay keeps whichever comes first."""
    events = []
    (oa, ca), (ob, cb) = [(m.origin, m.identity.cert_path().read_text(encoding="ascii"))
                          for m in (Matter.create(tmp_path / "x"), Matter.create(tmp_path / "y"))]
    events.append({"id": "1", "at": "1", "op": "add-member", "origin": oa, "member": oa, "cert": ca, "name": "a"})
    events.append({"id": "2", "at": "2", "op": "add-member", "origin": oa, "member": ob, "cert": cb, "name": "b"})
    events.append({"id": "3", "at": "3", "op": "set-copy", "origin": ob, "member": ob, "copy": "limited"})
    events.append({"id": "4", "at": "4", "op": "set-copy", "origin": oa, "member": oa, "copy": "limited"})
    p = poolmod.Pool.replay(oa, events)
    assert p.full_copies() == [oa] and not p.holds_all(ob)


def test_a_hub_is_never_limited(a, hub):
    h, srv, addr = hub
    hubmod.push(a, h.enroll("alice")["code"], ALICE)
    (replica,) = h.matters()
    p = poolmod.Pool.load(Matter(a.root))
    assert p.holds_all(replica.origin)
    replica.pool_log.append(op="set-copy", member=replica.origin, copy="limited", author="user:admin")
    assert poolmod.Pool.load(replica).holds_all(replica.origin)


def test_a_limited_copy_needs_a_full_one_it_can_reach(a, tmp_path):
    """Alice's laptop doesn't listen: a limited copy would have nowhere to
    fetch originals from."""
    poolmod.found(a, ALICE, "Alice's laptop")
    srv, addr = serve(a)
    try:
        code = invite.create(Matter(a.root), ALICE, [addr])["code"]
        poolmod.set_addresses(Matter(a.root), ALICE, [])
        with pytest.raises(invite.InviteError, match="no other member that does says where it listens"):
            invite.join(code, tmp_path / "b", copy="limited")
    finally:
        srv.stop()


# -- syncing ----------------------------------------------------------------------------------------


def test_a_limited_copy_gets_everything_but_the_originals(a, tmp_path):
    srv_a, addr_a = serve(a)
    try:
        invite.join(invite.create(a, ALICE, [addr_a])["code"], tmp_path / "b", copy="limited")
        b = Matter(tmp_path / "b")
        assert not peer.holds_all(b)
        assert picture(b) == picture(a)                   # every event, layer, tag
        assert text_of(b, a.doc) == text_of(a, a.doc)     # and the text, to search and index
        assert originals_here(b) == set()
        assert peer.missing_originals(b) == [Matter(a.root).state.documents[a.doc]]
        # what b ingests, it holds, and it goes to a with its original
        d = pdf_doc(b, "bob.pdf", "Bob's notes")
        r = peer.sync_with(Matter(b.root), addr_a, a.origin)
        assert r["pushed"] and "refused" not in r and "later" not in r
        assert d in originals_here(a) and d in originals_here(b)
        # a's new documents come without theirs
        d2 = pdf_doc(Matter(a.root), "more.pdf", "More")
        peer.sync_with(Matter(b.root), addr_a, a.origin)
        assert d2 in Matter(b.root).state.documents and d2 not in originals_here(b)
        assert picture(b) == picture(a)
    finally:
        srv_a.stop()


def test_a_text_file_keeps_its_text_when_limited(tmp_path):
    """A text file's layer text can be its original's very bytes: it still comes."""
    a = Matter.create(tmp_path / "a")
    doc = text_doc(a, "dock.txt", "The pallet was never received at Dock 4.")
    srv, addr = serve(a)
    try:
        invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b", copy="limited")
        assert text_of(Matter(tmp_path / "b"), doc).startswith("The pallet")
    finally:
        srv.stop()


def test_a_full_member_doesnt_take_events_without_their_originals(a, tmp_path):
    """b is limited and serves; c is full and reaches only b at first: it
    gets b's own documents now, and a's when it reaches a."""
    srv_a, addr_a = serve(a)
    invite.join(invite.create(a, ALICE, [addr_a])["code"], tmp_path / "b", copy="limited")
    b = Matter(tmp_path / "b")
    srv_b, addr_b = serve(b)
    try:
        mine = pdf_doc(b, "bob.pdf", "Bob's notes")
        invite.join(invite.create(Matter(b.root), BOB, [addr_b])["code"], tmp_path / "c")
        c = Matter(tmp_path / "c")
        assert peer.holds_all(c)
        assert mine in originals_here(c)
        assert a.doc not in Matter(c.root).state.documents      # not from b: it lacks the original
        r = peer.sync_with(Matter(c.root), addr_b, b.origin)
        assert any(x["log"] == f"store/{a.origin}" for x in r["later"]) and "refused" not in r
        peer.sync_with(Matter(b.root), addr_a, a.origin)          # a hears of c
        peer.sync_with(Matter(c.root), addr_a, a.origin)
        assert a.doc in originals_here(c)
        assert picture(c) == picture(a)
    finally:
        srv_a.stop(), srv_b.stop()


def test_a_limited_copy_doesnt_push_what_it_cant_back(a, tmp_path):
    """b (limited) syncs with c (full, serving), which hasn't heard from a:
    b's own events go, a's wait until c has them from someone with the
    originals."""
    srv_a, addr_a = serve(a)
    invite.join(invite.create(a, ALICE, [addr_a])["code"], tmp_path / "b", copy="limited")
    b = Matter(tmp_path / "b")
    # c joins through a before a has anything new; then a adds a document
    invite.join(invite.create(Matter(a.root), ALICE, [addr_a])["code"], tmp_path / "c")
    c = Matter(tmp_path / "c")
    srv_c, addr_c = serve(c)
    try:
        poolmod.set_addresses(Matter(c.root), CAROL, [addr_c])
        peer.sync_with(Matter(c.root), addr_a, a.origin)
        late = pdf_doc(Matter(a.root), "late.pdf", "Late arrival")
        peer.sync_with(Matter(b.root), addr_a, a.origin)
        assert late in Matter(b.root).state.documents
        mine = pdf_doc(Matter(b.root), "bob.pdf", "Bob's notes")
        r = peer.sync_with(Matter(b.root), addr_c, c.origin)
        assert "refused" not in r and any(x["log"] == f"store/{a.origin}" for x in r["later"])
        assert mine in originals_here(c) and late not in Matter(c.root).state.documents
        peer.sync_with(Matter(c.root), addr_a, a.origin)
        assert late in originals_here(c)
    finally:
        srv_a.stop(), srv_c.stop()


def test_through_a_hub(a, hub, tmp_path):
    """The usual shape: a hub holds everything; Bob's laptop is limited."""
    h, srv, addr = hub
    hubmod.push(a, h.enroll("alice")["code"], ALICE)
    invite.join(invite.create(Matter(a.root), ALICE)["code"], tmp_path / "b", copy="limited")
    b = Matter(tmp_path / "b")
    assert picture(b) == picture(a) and originals_here(b) == set()
    mine = pdf_doc(b, "bob.pdf", "Bob's notes")
    r = peer.sync_all(Matter(b.root))
    assert not any(x.get("refused") or x.get("failed") for x in r)
    peer.sync_all(Matter(a.root))
    assert mine in originals_here(a)
    # becoming full, b fetches what it lacks
    poolmod.set_copy(Matter(b.root), BOB, "full")
    r = peer.sync_all(Matter(b.root))
    assert any(x.get("filled") for x in r)
    assert originals_here(b) == set(Matter(a.root).state.documents)
    assert peer.missing_originals(Matter(b.root)) == []
    assert not (b.sync_dir / "was-limited").exists()


def test_the_only_full_copy_cant_be_removed_from_under_limited_ones(a, tmp_path):
    srv, addr = serve(a)
    try:
        invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b", copy="limited")
        b = Matter(tmp_path / "b")
        with pytest.raises(poolmod.PoolError, match="only member that holds every original"):
            poolmod.remove_member(b, BOB, a.origin)
    finally:
        srv.stop()
