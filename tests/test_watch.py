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
