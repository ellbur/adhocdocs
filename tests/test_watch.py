"""Syncing as soon as something changes (adhocdocs.watch)."""

import time

from adhocdocs import annotations, invite, peer, watch
from adhocdocs.matter import Matter
from test_peer import ALICE, BOB, serve
from test_sync import text_doc


def eventually(check, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


def defined(m, tag):
    return tag in annotations.Annotations.load(Matter(m.root)).definitions


def test_changes_travel_within_seconds_both_ways(tmp_path):
    a = Matter.create(tmp_path / "a")
    text_doc(a, "a.txt", "hello")
    srv, addr = serve(a)
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
    b = Matter(tmp_path / "b")             # a laptop: it doesn't listen anywhere
    wa = watch.Watcher(Matter(a.root), every=600, debounce=0.1, poll=0.1, wait=5, log=lambda r: None)
    wb = watch.Watcher(b, every=600, debounce=0.1, poll=0.1, wait=5, log=lambda r: None)
    wa.start(), wb.start()
    try:
        time.sleep(0.5)
        annotations.write(Matter(b.root), BOB, {"op": "define-tag", "tag": "from-b"})      # pushed
        assert eventually(lambda: defined(a, "from-b"))
        annotations.write(Matter(a.root), ALICE, {"op": "define-tag", "tag": "from-a"})    # b hears by long poll
        t = time.time()
        assert eventually(lambda: defined(b, "from-a"))
        assert time.time() - t < 5
    finally:
        wa.stop(), wb.stop()
        srv.stop()


def test_a_member_passes_on_what_it_receives(tmp_path):
    """c can only reach b, and a can only reach b: b's watcher carries changes."""
    a = Matter.create(tmp_path / "a")
    text_doc(a, "a.txt", "hello")
    srv_a, addr_a = serve(a)
    invite.join(invite.create(a, ALICE, [addr_a])["code"], tmp_path / "b")
    b = Matter(tmp_path / "b")
    srv_b, addr_b = serve(b)
    invite.join(invite.create(b, BOB, [addr_b])["code"], tmp_path / "c")
    c = Matter(tmp_path / "c")
    peer.sync_with(b, addr_a, a.origin)
    srv_a.stop()                            # from here on, a is off; b and c carry on
    wb = watch.Watcher(b, every=600, debounce=0.1, poll=0.1, wait=5, log=lambda r: None)
    wc = watch.Watcher(c, every=600, debounce=0.1, poll=0.1, wait=5, log=lambda r: None)
    wb.start(), wc.start()
    try:
        annotations.write(Matter(c.root), "user:carol", {"op": "define-tag", "tag": "from-c"})
        assert eventually(lambda: defined(b, "from-c"))
    finally:
        wb.stop(), wc.stop()
        srv_b.stop()


def test_one_watcher_per_matter(tmp_path):
    import threading
    from adhocdocs import pool
    a = Matter.create(tmp_path / "a")
    pool.found(a, ALICE, "Alice")
    t = threading.Thread(target=watch.run, args=(Matter(a.root), 600), daemon=True)
    t.start()
    time.sleep(0.5)
    assert watch.run(Matter(a.root)) == 0         # returns at once: the first has it
    assert t.is_alive()


def test_members_find_each_other_on_the_local_network(tmp_path):
    """a serves but tells no one where (no address in the pool); b's watcher
    hears a's announcement, checks it's a member, and syncs with it."""
    import socket as sk
    from adhocdocs import lan, pool
    s = sk.socket(sk.AF_INET, sk.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    lan_port = s.getsockname()[1]
    s.close()
    a = Matter.create(tmp_path / "a")
    text_doc(a, "a.txt", "hello")
    srv, addr = serve(a)
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
    pool.set_addresses(Matter(a.root), ALICE, [])                  # now nobody knows where a is
    b = Matter(tmp_path / "b")
    peer.sync_with(b, addr, a.origin)
    assert pool.Pool.load(Matter(b.root)).members[a.origin]["addresses"] == []
    annotations.write(Matter(a.root), ALICE, {"op": "define-tag", "tag": "found-you"})
    wb = watch.Watcher(b, every=600, debounce=0.1, poll=0.1, wait=5, log=lambda r: None, lan_port=lan_port)
    wb.start()
    ann = lan.Announcer(srv, target=("127.0.0.1", lan_port), every=0.5).start()
    try:
        assert eventually(lambda: defined(b, "found-you"))
        assert lan.found(Matter(b.root)) == {a.origin: addr}
        # a stranger's announcements (another matter's tag) are ignored
        other = Matter.create(tmp_path / "other")
        pool.found(other, ALICE, "x")
        with sk.socket(sk.AF_INET, sk.SOCK_DGRAM) as u:
            u.sendto(lan.packet(Matter(other.root), 1), ("127.0.0.1", lan_port))
        time.sleep(0.5)
        assert set(lan.found(Matter(b.root))) == {a.origin}
    finally:
        ann.stop()
        wb.stop()
        srv.stop()


def test_the_tag_names_neither_matter_nor_installation(tmp_path):
    from adhocdocs import lan, pool
    a = Matter.create(tmp_path / "a")
    pool.found(a, ALICE, "Alice")
    p = lan.packet(Matter(a.root), 7480).decode()
    assert a.id not in p and a.origin not in p and a.id.lower() not in p
    t = time.time()
    assert lan.tag(Matter(a.root), t) != lan.tag(Matter(a.root), t + lan.WINDOW)
