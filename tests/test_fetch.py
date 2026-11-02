"""Originals on demand for a limited copy, and its cache (adhocdocs.fetch)."""

import threading

import pytest

from adhocdocs import annapi, fetch, hub as hubmod, invite, peer, pool as poolmod, raw
from adhocdocs.matter import Matter
from test_hub import hub  # noqa: F401  (a fixture)
from test_limited import originals_here, pdf_doc, text_of
from test_peer import ALICE, BOB, serve
from test_sync import text_doc


@pytest.fixture
def pair(tmp_path):
    """a: full, serving, with three PDFs; b: a limited copy of it."""
    a = Matter.create(tmp_path / "a")
    docs = [pdf_doc(a, f"d{i}.pdf", f"Document number {i} " * 50) for i in range(3)]
    srv, addr = serve(a)
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b", copy="limited")
    b = Matter(tmp_path / "b")
    yield Matter(a.root), b, docs, srv, addr
    srv.stop()


def content(m, doc):
    return Matter(m.root).state.documents[doc]


def test_reading_an_original_fetches_it(pair):
    a, b, docs, srv, addr = pair
    assert originals_here(b) == set()
    h = content(b, docs[0])
    data = Matter(b.root).objects.get_bytes(h)          # any reader: through the store
    assert data == a.objects.get_bytes(h)
    assert originals_here(b) == {docs[0]}
    assert fetch.read_ledger(b)["entries"][h]["how"] == "fetched"
    assert raw.size(Matter(b.root), content(b, docs[1])) > 0     # raw, say
    assert originals_here(b) == {docs[0], docs[1]}
    # a full copy never fetches through the store: it has everything
    assert Matter(a.root).objects.fetcher is not None and not fetch.limited(a)


def test_through_a_hub(tmp_path, hub):
    h, srv, addr = hub
    a = Matter.create(tmp_path / "a")
    d = pdf_doc(a, "d.pdf", "Hello from the hub")
    hubmod.push(a, h.enroll("alice")["code"], ALICE)
    invite.join(invite.create(Matter(a.root), ALICE)["code"], tmp_path / "b", copy="limited")
    b = Matter(tmp_path / "b")
    c = content(b, d)
    assert Matter(b.root).objects.get_bytes(c) == Matter(a.root).objects.get_bytes(c)


def test_the_cache_lets_go_of_the_least_recently_used(pair):
    a, b, docs, srv, addr = pair
    size = Matter(a.root).objects.path(content(a, docs[0])).stat().st_size
    fetch.set_limit(b, 2.5 * size / (1 << 30))         # room for two
    for d in docs:
        Matter(b.root).objects.get_bytes(content(b, d))
    assert originals_here(b) == {docs[1], docs[2]}
    # used again, an original goes to the back of the line
    Matter(b.root).objects.get_bytes(content(b, docs[0]))
    assert originals_here(b) == {docs[2], docs[0]}
    fetch.touch(b, content(b, docs[2]))
    Matter(b.root).objects.get_bytes(content(b, docs[1]))
    assert originals_here(b) == {docs[2], docs[1]}


def test_pinned_originals_stay(pair):
    a, b, docs, srv, addr = pair
    size = Matter(a.root).objects.path(content(a, docs[0])).stat().st_size
    fetch.set_limit(b, 1.5 * size / (1 << 30))         # room for one
    r = fetch.pin(b, [docs[0]])
    assert r["pinned"] == 1 and r["fetched"] == 1
    Matter(b.root).objects.get_bytes(content(b, docs[1]))
    assert docs[0] in originals_here(b)                 # over the limit, but kept
    Matter(b.root).objects.get_bytes(content(b, docs[2]))
    assert originals_here(b) == {docs[0], docs[2]}
    fetch.pin(b, [docs[0]], unpin=True)
    assert originals_here(b) == {docs[2]}               # unpinned, it goes first: used longest ago


def test_what_was_ingested_here_stays_until_a_full_member_has_it(pair):
    a, b, docs, srv, addr = pair
    fetch.set_limit(b, 1e-9)                            # no room at all
    mine = pdf_doc(Matter(b.root), "bob.pdf", "Bob's notes " * 50)
    fetch.evict(Matter(b.root))
    assert mine in originals_here(b)                    # only b has it
    peer.sync_with(Matter(b.root), addr, a.origin)
    assert mine in originals_here(a)
    assert mine not in originals_here(b)                # a full member has it now: it may go
    assert Matter(b.root).objects.get_bytes(content(b, mine))      # and comes back when wanted


def test_a_text_layers_bytes_are_never_let_go_of(tmp_path):
    """A text file's layer text is its original's bytes: a limited copy keeps
    all text, so it keeps those."""
    a = Matter.create(tmp_path / "a")
    d = text_doc(a, "t.txt", "Plain text, all of it.")
    srv, addr = serve(a)
    try:
        invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b", copy="limited")
        b = Matter(tmp_path / "b")
        fetch.set_limit(b, 1e-9)
        with fetch.Ledger(b) as led:                    # as if fetched
            led.entries[content(b, d)] = {"how": "fetched", "size": 10, "used": 0}
        fetch.evict(Matter(b.root))
        assert text_of(b, d) == "Plain text, all of it."
    finally:
        srv.stop()


def test_going_limited_lets_go_of_what_a_full_member_holds(pair, tmp_path):
    """c starts full (all originals), then goes limited: on its next sync
    it learns a holds them, and the cache can let them go."""
    a, b, docs, srv, addr = pair
    invite.join(invite.create(Matter(a.root), ALICE, [addr])["code"], tmp_path / "c")
    c = Matter(tmp_path / "c")
    assert originals_here(c) == set(docs)
    fetch.set_limit(c, 1e-9)
    poolmod.set_copy(c, "user:carol", "limited")
    assert originals_here(c) == set(docs)                # nothing known held yet
    peer.sync_with(Matter(c.root), addr, a.origin)
    assert originals_here(c) == set()
    assert not (c.sync_dir / "confirm-held").exists()


def test_when_no_full_member_can_be_reached(pair):
    a, b, docs, srv, addr = pair
    srv.stop()
    h = content(b, docs[0])
    with pytest.raises(KeyError):
        Matter(b.root).objects.get_bytes(h)
    with pytest.raises(fetch.Unavailable, match="limited copy.*adhoc fetch"):
        raw.size(Matter(b.root), h)                     # a reader says why, and what to do
    r = fetch.fetch(Matter(b.root), [h])
    assert r["unavailable"][0]["content"] == h
    # tried again only after a while, so a reader isn't held up each time
    assert h in fetch.read_ledger(b)["failed"]
    srv2 = peer.Server([Matter(a.root)], "127.0.0.1", int(addr.rsplit(":", 1)[1]))
    srv2.start()
    try:
        assert Matter(b.root).objects.path(h) is None             # still waiting out the retry
        assert fetch.fetch(Matter(b.root), [h])["fetched"] == [h]  # asked for directly, it's fetched
    finally:
        srv2.stop()


def test_a_session_asks_the_host(pair, tmp_path, monkeypatch):
    """In a session the store is read-only and there's no network to the
    members: the annotation API fetches on the host."""
    a, b, docs, srv, addr = pair
    sock = tmp_path / "ann.sock"
    t = threading.Thread(target=annapi.serve, args=(Matter(b.root), sock), daemon=True)
    t.start()
    import time
    for _ in range(50):
        if sock.exists():
            break
        time.sleep(0.1)
    main = threading.main_thread()
    monkeypatch.setattr(fetch, "_on_host", lambda m: threading.current_thread() is not main)
    monkeypatch.setenv("ADHOC_ANN_SOCKET", str(sock))
    h = content(b, docs[0])
    assert Matter(b.root).objects.get_bytes(h) == Matter(a.root).objects.get_bytes(h)
    assert fetch.read_ledger(b)["entries"][h]["how"] == "fetched"


def test_fetch_from_a_list(pair, tmp_path):
    from adhocdocs import lists
    a, b, docs, srv, addr = pair
    path = tmp_path / "some.jsonl"
    lists.write(path, *lists.new("some", [f"doc:{d}" for d in docs[:2]]))
    import subprocess, sys
    r = subprocess.run([sys.executable, "-m", "adhocdocs.cli", "adhoc", "--matter", str(b.root), "fetch", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.fail(r.stderr)
    assert originals_here(b) == set(docs[:2])
    assert set(fetch.read_ledger(b)["pinned"]) == {content(b, d) for d in docs[:2]}
