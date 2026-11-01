"""Regression tests for what an independent review of sync found: each
of these was shown to work against the code before it was fixed."""

import json
import os
import socket
import ssl
import time

import pytest

from adhocdocs import annotations, invite, peer, pool as poolmod, work
from adhocdocs.annotations import Refused
from adhocdocs.matter import Matter
from adhocdocs.signing import sign
from test_peer import ALICE, serve
from test_sync import text_doc


def just_after(event_id: str) -> str:
    """An ID right after another: one a writer could choose to backdate to."""
    from adhocdocs.matter import _decode_ulid, _encode_ulid
    ms, rand = _decode_ulid(event_id[3:])
    return "ev_" + _encode_ulid(ms, rand + 1)


@pytest.fixture
def pair(tmp_path):
    a = Matter.create(tmp_path / "a")
    text_doc(a, "a.txt", "hello")
    srv, addr = serve(a)
    invite.join(invite.create(a, ALICE, [addr])["code"], tmp_path / "b")
    yield Matter(a.root), Matter(tmp_path / "b"), srv, addr
    srv.stop()


def test_a_log_name_cant_be_a_path(pair, tmp_path):
    """A member of one matter can't read another's logs (or the workspace
    feed) by putting a path where an origin goes."""
    a, b, srv, addr = pair
    other = Matter.create(tmp_path / "other")
    annotations.write(other, ALICE, {"op": "define-tag", "tag": "secret"})
    srv.add(other)
    p = peer.Peer(b, addr, a.origin)
    for evil in (f"..%2F..%2F..%2Fother%2Fannotations%2Flogs%2F{other.origin}", "..%2F..%2Fworkspace%2F.adhoc%2Flog"):
        r = p.request("GET", f"events/annotations/{evil}?from=0")
        assert r.status == 404 and b"secret" not in r.read()
    p.close()
    with pytest.raises(ValueError):
        a.annotation_log.log_for("../x")


def test_publications_from_elsewhere_cant_write_outside_work(pair, tmp_path):
    a, b, srv, addr = pair
    store = work._store(b)
    h = store.put_bytes(b"pwned")
    for name, files in (("x.md", {"../../escaped.txt": h}), ("y.md", {"/tmp/abs-escaped.txt": h}),
                        ("../z.md", {"../z.md": h}), ("w.md", {"logs/" + b.origin + ".jsonl": h})):
        pub = store.put_object({"type": "publication", "v": 1, "files": files})
        b.work_log.append(op="publish", name=name, publication=pub, revises=[], kind="page", author="user:mallory")
    peer.sync_with(b, addr, a.origin)
    assert not (tmp_path / "escaped.txt").exists() and not (a.root / "escaped.txt").exists()
    assert not os.path.exists("/tmp/abs-escaped.txt") and not (tmp_path / "z.md").exists()
    assert work.listing(Matter(a.root))                               # listed, but laid out nowhere
    assert sorted(p.name for p in (a.root / "work").iterdir() if not p.name.startswith(".")) == ["logs"]
    with pytest.raises(Refused):
        work.checkout(Matter(a.root), "x.md")


def test_links_in_the_workspace_dont_publish_what_they_point_at(pair):
    a, b, srv, addr = pair
    key = next((a.root / "sync/keys").glob("*.pem"))
    d = a.workspace / "exhibits"
    d.mkdir()
    (d / "real.md").write_text("# Real\n", encoding="utf-8")
    (d / "key.md").symlink_to(key)
    (d / "sub").symlink_to(a.root / "sync")
    r = work.publish(a, ALICE, "exhibits")
    assert r["files"] == ["exhibits/real.md"]
    (a.workspace / "memo.md").symlink_to(key)
    with pytest.raises(Refused, match="link"):
        work.publish(a, ALICE, "memo.md")
    (a.workspace / "linked").symlink_to(a.root / "sync" / "keys")
    with pytest.raises(Refused):
        work.publish(a, ALICE, f"linked/{key.name}")
    assert not any(b"PRIVATE KEY" in p.read_bytes() for p in (a.root / "store/objects").rglob("*") if p.is_file())


def test_a_removed_member_cant_backdate_its_way_back(pair, tmp_path):
    a, b, srv, addr = pair
    poolmod.remove_member(a, ALICE, b.origin)
    # b hasn't heard: it adds a sock puppet, with an ID from before its removal
    c = Matter.create(tmp_path / "c", matter_id=a.id)
    added = next(e for e in a.pool_log.read() if e.get("member") == b.origin)
    log = b.pool_log.log_for(b.origin)
    ev = sign({"id": just_after(added["id"]), "at": "x", "op": "add-member", "member": c.origin,
               "cert": c.identity.cert_path().read_text(encoding="ascii"), "name": "puppet", "role": "member",
               "addresses": [], "author": "user:mallory"}, b.identity.key, log.last_event())
    with open(log.path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")
    # the events arrive anyway, by way of a member that kept them
    import shutil
    shutil.copy(log.path, a.pool_log.dir / log.path.name)
    p = poolmod.Pool.load(Matter(a.root))
    assert not p.is_member(c.origin) and not p.is_member(b.origin)
    assert any("after its installation was removed" in x["reason"] for x in p.ignored)
    # and sync won't take them in the first place
    (a.pool_log.dir / log.path.name).unlink()
    with pytest.raises(peer.SyncError, match="removed|403"):
        peer.append_remote(Matter(a.root), "pool", b.origin, 0, list(log.events()))


def test_ids_in_a_membership_log_only_go_up(pair):
    a, b, srv, addr = pair
    log = a.pool_log.log_for(a.origin)
    events = list(log.events())
    first = events[0]
    back = sign({"id": "ev_00000000000000000000000001", "at": "x", "op": "set-addresses", "member": a.origin,
                 "addresses": [], "author": ALICE}, a.identity.key, events[-1])
    from adhocdocs.signing import BadLog
    with pytest.raises(BadLog, match="dated before"):
        peer.append_remote(Matter(b.root), "pool", a.origin, len(events), [back])
    assert first


def _raw(addr, sni, request: bytes) -> bytes:
    host, port = peer.split_address(addr)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    s = ctx.wrap_socket(socket.create_connection((host, port), 5), server_hostname=sni)
    try:
        s.sendall(request)
    except (ssl.SSLError, ConnectionError):
        # The server may answer and close before taking the whole body (as it
        # should, for a stranger); its reply is still there to read.
        pass
    s.settimeout(5)
    data = b""
    try:
        while chunk := s.recv(65536):
            data += chunk
    except (TimeoutError, ssl.SSLError, ConnectionError):
        pass
    s.close()
    return data


def test_nothing_is_read_from_strangers(pair):
    a, b, srv, addr = pair
    key = peer.pool_key(a)
    path = f"/v1/{key}/events/annotations/{a.origin}"
    sni = peer.sni_name(key)
    for headers in ("Content-Length: -1", "Content-Length: 999999999999", ""):
        t = time.time()
        out = _raw(addr, sni, f"POST {path} HTTP/1.1\r\nHost: x\r\n{headers}\r\n\r\n".encode() + b"x" * 100000)
        assert out.startswith(b"HTTP/1.1 403") and time.time() - t < 4
    # a member must still say how long its body is, within limits
    p = peer.Peer(b, addr, a.origin)
    auth = peer.auth_header(b, "POST", path, b"", a.origin)
    out = _raw(addr, sni, f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Adhoc {auth}\r\n"
                          f"Content-Length: -1\r\n\r\n".encode())
    assert out.startswith(b"HTTP/1.1 413")
    p.close()


def test_malformed_membership_events_dont_break_the_pool(pair):
    a, b, srv, addr = pair
    log = a.pool_log
    log.append(op="add-member", member=None, cert=7, author=ALICE)
    log.append(op="set-addresses", member=a.origin, addresses=None, author=ALICE)
    p = poolmod.Pool.load(Matter(a.root))
    assert p.is_member(b.origin) and len(p.ignored) >= 1


def test_what_a_removed_member_wrote_afterwards_doesnt_count(tmp_path):
    """Bob is removed; what he writes afterwards reaches Carol (who hadn't
    heard), and through her, Alice. It doesn't count anywhere once the
    removal is known: not in the documents, tags, published work or search."""
    from test_sync import copy_logs, picture
    from adhocdocs import search
    a = Matter.create(tmp_path / "a")
    keep = text_doc(a, "a.txt", "the original pallet document")
    poolmod.found(a, ALICE, "Alice")
    b = Matter.create(tmp_path / "b", matter_id=a.id)
    c = Matter.create(tmp_path / "c", matter_id=a.id)
    for x in (b, c):
        cfg = x.config
        cfg["founder"] = a.origin
        from adhocdocs.matter import write_toml
        write_toml(x.root / "matter.toml", cfg)
        poolmod.add_member(a, ALICE, x.identity.cert_path().read_text(encoding="ascii"), x.root.name)
    for x in (b, c):
        copy_logs(a, x)
    before = text_doc(b, "before.txt", "bob's pallet note, before")
    copy_logs(b, a)
    poolmod.remove_member(a, ALICE, b.origin)
    # Bob, not having heard, carries on; Carol takes it in
    after = text_doc(b, "after.txt", "bob's pallet note, after")
    annotations.write(Matter(b.root), "user:bob", {"op": "define-tag", "tag": "bobs"})
    (b.workspace / "memo.md").write_text("# Bob's\n", encoding="utf-8")
    work.publish(Matter(b.root), "user:bob", "memo.md")
    copy_logs(b, c)
    copy_logs(c, a)            # raw copies: as if relayed before the removal was known
    copy_logs(a, c)
    for m in (a, c):
        m = Matter(m.root)
        docs = m.state.live_documents()
        assert before in docs and after not in docs and keep in docs
        assert "bobs" not in annotations.Annotations.load(m).definitions
        assert not (m.root / "work/memo.md").exists()
        from adhocdocs.index import open_index
        open_index(m, "keyword").update()
        s = search.Searcher(Matter(m.root), search.Options())
        _, items = s.finish(*s.keyword("pallet"))
        assert {i["loc"][4:] for i in items} == {keep, before}
        assert not open_index(m, "keyword").behind()
    assert picture(a) == picture(c)


def test_someone_elses_pool_under_a_matters_id_doesnt_take_its_place(tmp_path):
    """Someone enrolled at the hub, who isn't in a matter's pool but knows its
    ID, pushes a pool of their own under it first. The real one still gets
    its own replica."""
    import socket as sk
    from adhocdocs import hub as hubmod
    s = sk.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    h = hubmod.Hub.init(tmp_path / "hub", "Firm hub", [f"127.0.0.1:{port}"])
    srv = peer.Server(h.matters(), "127.0.0.1", port, hub=h)
    srv.start()
    try:
        a = Matter.create(tmp_path / "a")
        text_doc(a, "a.txt", "the real matter")
        squat = Matter.create(tmp_path / "squat", matter_id=a.id)
        text_doc(squat, "s.txt", "not the real one")
        hubmod.push(squat, h.enroll("mallory")["code"], "user:mallory")
        r = hubmod.push(Matter(a.root), h.enroll("alice")["code"], ALICE)
        assert r["pushed"]
        replicas = {m.config["founder"]: m for m in h.matters()}
        assert len(replicas) == 2
        real = replicas[Matter(a.root).config["founder"]]
        from adhocdocs import sync
        assert sync.vector(real) == sync.vector(Matter(a.root))
    finally:
        srv.stop()


# -- second review: who writes for whom, and removals -------------------------------------------


def _three(tmp_path):
    from test_sync import copy_logs
    from adhocdocs.matter import write_toml
    a = Matter.create(tmp_path / "a")
    poolmod.found(a, ALICE, "Alice")
    ms = []
    for n in ("bob", "mallory"):
        x = Matter.create(tmp_path / n, matter_id=a.id)
        write_toml(x.root / "matter.toml", {**x.config, "founder": a.origin})
        poolmod.add_member(a, ALICE, x.identity.cert_path().read_text(encoding="ascii"), n)
        ms.append(Matter(x.root))
    for x in ms:
        copy_logs(a, x)
    return Matter(a.root), ms[0], ms[1]


def test_a_backdated_claim_doesnt_take_a_person_over(tmp_path):
    from test_sync import copy_logs
    a, bob, mal = _three(tmp_path)
    annotations.write(bob, "user:bob", {"op": "define-tag", "tag": "bobs"})          # bob claims himself
    # mallory claims bob too, dated before bob's claim, around the write path
    log = mal.pool_log.log_for(mal.origin)
    own = next(e for e in a.pool_log.read() if e.get("member") == mal.origin)
    ev = sign({"id": just_after(own["id"]), "at": "x", "op": "bind-user", "user": "user:bob", "member": mal.origin,
               "author": "user:bob"}, mal.identity.key, log.last_event())
    with open(log.path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")
    for x in (bob, mal):
        copy_logs(x, a)
    a = Matter(a.root)
    assert "bobs" in annotations.Annotations.load(a).definitions          # bob's work still counts
    p = poolmod.Pool.load(a)
    assert sorted(p.users["user:bob"]) == sorted([bob.origin, mal.origin])
    from adhocdocs import sync
    (d,) = sync.disputes(a)                                                 # and it's plain to see
    assert d["kind"] == "person-disputed" and d["author"] == "user:bob"


def test_a_same_person_vouch_cant_be_used_by_another_member(tmp_path, monkeypatch):
    """Members see the vouch in the pool log, but only the invitation's secret
    makes its proof."""
    from test_sync import copy_logs
    a, bob, mal = _three(tmp_path)
    secret = "s3cret"
    k, pub = poolmod.vouch_key(secret)
    a.pool_log.append(op="bind-user", user=ALICE, vouch_key=pub, author=ALICE)
    copy_logs(a, mal)
    vouch = next(e for e in Matter(mal.root).pool_log.read() if e.get("vouch_key"))
    from adhocdocs.signing import b64
    # mallory re-adds herself, and makes up a proof
    mal.pool_log.append(op="add-member", member=mal.origin, cert=mal.identity.cert_path().read_text(encoding="ascii"),
                        name="again", role="member", addresses=[], author=ALICE, users=[ALICE])
    mal.pool_log.append(op="bind-user", user=ALICE, member=mal.origin, vouch_key=vouch["vouch_key"],
                        proof=b64(b"x" * 64), author=ALICE)
    copy_logs(mal, a)
    p = poolmod.Pool.load(Matter(a.root))
    assert mal.origin not in p.users.get(ALICE, [])
    assert any("added already" in x["reason"] for x in p.ignored)
    # with the secret, the proof checks
    poolmod.prove(bob, secret, ALICE)
    copy_logs(bob, a)
    assert bob.origin in poolmod.Pool.load(Matter(a.root)).users[ALICE]


def test_a_removal_cant_be_redone_with_another_cutoff(tmp_path):
    from test_sync import copy_logs
    a, bob, mal = _three(tmp_path)
    text_doc(bob, "b.txt", "bob's earlier work")
    copy_logs(bob, a)
    poolmod.remove_member(a, ALICE, bob.origin)
    copy_logs(a, mal)
    # mallory removes bob again, cutting all his work
    mal.pool_log.append(op="remove-member", member=bob.origin, upto={"pool": 0, "store": 0}, author="user:mallory")
    copy_logs(mal, a)
    from adhocdocs import pool as pm
    pm.save_cutoffs(Matter(a.root))
    docs = Matter(a.root).state.live_documents()
    assert any(Matter(a.root).objects.get_object(Matter(a.root).state.metadata[d])["name"] == "b.txt" for d in docs)


def test_an_installation_that_joined_as_the_same_person_can_vouch(tmp_path):
    a = Matter.create(tmp_path / "a")
    text_doc(a, "a.txt", "x")
    srv, addr = serve(a)
    try:
        invite.join(invite.create(a, ALICE, [addr], same_person=True)["code"], tmp_path / "desk")
        desk = Matter(tmp_path / "desk")
        invite.join(invite.create(Matter(a.root), ALICE, [addr])["code"], tmp_path / "tablet")
        tablet = Matter(tmp_path / "tablet")
        peer.sync_with(desk, addr, a.origin)
        poolmod.vouch(desk, ALICE, tablet.origin)          # the desk, not the laptop, vouches
        peer.sync_with(desk, addr, a.origin)
        p = poolmod.Pool.load(Matter(a.root))
        assert sorted(p.users[ALICE]) == sorted([a.origin, desk.origin, tablet.origin])
        assert p.disputed == {}
    finally:
        srv.stop()


def test_a_long_poll_always_ends(pair):
    a, b, srv, addr = pair
    p = peer.Peer(b, addr, a.origin, timeout=10)
    fp = p.json("GET", "wait?timeout=0")["fingerprint"]          # nothing will change meanwhile
    t = time.time()
    for bad in ("nan", "-5", "x"):
        assert p.json("GET", f"wait?timeout={bad}&seen={fp}")["fingerprint"] == fp
    assert time.time() - t < 3                                     # each answered at once
    p.close()


def test_the_viewer_hears_of_local_membership_changes(pair, tmp_path):
    """A vouch made here changes what counts: replay.json follows at once."""
    a, b, srv, addr = pair
    b.annotation_log.append(op="define-tag", tag="b-as-alice", author=ALICE)     # around the write path
    peer.sync_with(b, addr, a.origin)
    rp = json.loads((a.root / "annotations/replay.json").read_text(encoding="utf-8"))
    assert any(v == [] for v in rp["replace"].values())
    poolmod.vouch(Matter(a.root), ALICE, b.origin)
    rp = json.loads((a.root / "annotations/replay.json").read_text(encoding="utf-8"))
    assert all(v != [] for v in rp["replace"].values()) or rp["replace"] == {}
