"""Containers too large for memory: a zip, mbox or PST is copied into the
store in blocks and read from there a member at a time, however it
arrived, and an interrupted ingest finishes when run again."""

import mailbox
import os
import zipfile
from pathlib import Path

import pytest

import adhocdocs.ingest as ingest
from adhocdocs.extract import extract_file, file_kind
from adhocdocs.ingest import ingest_bytes, ingest_paths
from adhocdocs.matter import Matter
from adhocdocs.objects import ObjectStore, sha256

CORPUS = Path(__file__).parent.parent / "corpus"
PST = CORPUS / "kettering-t" / "kettering-t-mailbox-2026-04.pst"
EML = CORPUS / "muller-z" / "2026-03-09_RE_INV-2026-0347_past-due.eml"
N = 40


@pytest.fixture
def box(tmp_path):
    path = tmp_path / "in" / "export.mbox"
    path.parent.mkdir()
    b = mailbox.mbox(path)
    for i in range(N):
        b.add(EML.read_bytes().replace(b"nothing to pay yet", f"message number {i}".encode()))
    b.close()
    return path


def ident(path):
    return {"custodian": "cust:a", "source": {"collection": "c", "path": path.name}}


def family(m, doc):
    m = Matter(m.root)
    return {m.state.documents[c]: c for c in m.state.children[doc]}


def test_put_file(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    f = tmp_path / "f.bin"
    f.write_bytes(b"x" * 3_000_001)
    h = store.put_file(f, chunk=1 << 16)
    assert h == sha256(f.read_bytes()) and store.get_bytes(h) == f.read_bytes()
    mtime = store.path(h).stat().st_mtime_ns
    assert store.put_file(f) == h and store.path(h).stat().st_mtime_ns == mtime     # already in: not copied again
    assert not list((tmp_path / "objects").rglob("*.tmp"))


def test_mbox_on_disk_is_streamed_and_matches_in_memory(tmp_path, box):
    whole = Matter.create(tmp_path / "whole")
    in_memory = ingest_bytes(whole, box.read_bytes(), box.name, ident(box))
    m = Matter.create(tmp_path / "m")
    [doc] = _streamed(m, box)
    assert doc == in_memory
    assert family(m, doc) == family(whole, in_memory)       # same messages, same IDs
    meta = Matter(m.root).objects.get_object(Matter(m.root).state.metadata[doc])
    assert meta["kind"] == "mailbox" and meta["members"] == N


def _streamed(m, path):
    """Ingest, failing if the mailbox is read whole: no read_bytes of it,
    nothing that big stored in one piece."""
    real_read, real_put = Path.read_bytes, ObjectStore.put_bytes

    def read_bytes(self):
        assert self.suffix not in (".mbox", ".pst", ".zip"), f"{self} read into memory"
        return real_read(self)

    def put_bytes(self, data):
        assert not data.startswith((b"From ", b"!BDN", b"PK\x03\x04")), "a whole container stored from memory"
        return real_put(self, data)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_bytes", read_bytes)
        mp.setattr(ObjectStore, "put_bytes", put_bytes)
        return ingest_paths(m, [path], "a", "c")


def test_pst_on_disk_is_streamed_and_matches_in_memory(tmp_path):
    whole = Matter.create(tmp_path / "whole")
    in_memory = ingest_bytes(whole, PST.read_bytes(), PST.name, ident(PST))
    m = Matter.create(tmp_path / "m")
    [doc] = _streamed(m, PST)
    assert doc == in_memory
    assert family(m, doc) == family(whole, in_memory)
    a = Matter(m.root)
    b = Matter(whole.root)
    assert a.objects.get_object(a.state.metadata[doc]) == b.objects.get_object(b.state.metadata[in_memory])


def test_extract_file_children_come_one_at_a_time(box):
    assert file_kind(box, None) == "mbox" and file_kind(PST, None) == "pst"
    ext = extract_file(box, "mbox")
    assert ext.metadata == {}                       # filled in at the end
    first = next(iter(ext.children))
    assert first.part == "1" and b"message number 0" in first.data
    ext.children.close()
    assert ext.metadata == {"members": 1}


def test_interrupted_ingest_finishes_when_run_again(tmp_path, box):
    m = Matter.create(tmp_path / "m")
    real = ingest.ingest_bytes
    calls = []

    def dies(*a, **k):
        calls.append(1)
        if len(calls) > 15:
            raise KeyboardInterrupt
        return real(*a, **k)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ingest, "ingest_bytes", dies)
        with pytest.raises(KeyboardInterrupt):
            ingest_paths(m, [box], "a", "c")
    m = Matter(m.root)
    [doc] = m.state.documents.keys() - set().union(*m.state.children.values())
    assert doc not in m.state.metadata                       # unfinished
    assert len(m.state.children[doc]) == 15

    [again] = ingest_paths(m, [box], "a", "c")
    assert again == doc
    m = Matter(m.root)
    assert len(m.state.children[doc]) == N                   # each message once
    adds = [e["doc"] for e in m.logs["store"].read() if e["op"] == "add-document"]
    assert len(adds) == len(set(adds)) == N + 1
    assert m.objects.get_object(m.state.metadata[doc])["members"] == N
    n = sum(1 for _ in m.changes())
    ingest_paths(m, [box], "a", "c")                         # and now it's done: a no-op
    assert sum(1 for _ in Matter(m.root).changes()) == n


def test_mailbox_that_breaks_part_way(tmp_path, box, monkeypatch):
    """What came out before the break is kept, and the break is recorded."""
    from adhocdocs.extract import archive
    real = archive.extract_mbox_file

    def breaks(path):
        ext = real(path)
        inner = ext.children

        def children():
            for i, c in enumerate(inner):
                if i == 5:
                    raise OSError("bad sector")
                yield c
        ext.children = children()
        return ext
    monkeypatch.setattr(archive, "extract_mbox_file", breaks)
    m = Matter.create(tmp_path / "m")
    [doc] = ingest_paths(m, [box], "a", "c")
    m = Matter(m.root)
    assert len(m.state.children[doc]) == 5
    assert "bad sector" in m.objects.get_object(m.state.metadata[doc])["extraction_error"]


# Containers inside containers: zips on disk, and mailboxes that arrive
# inside a zip or as an attachment. All are stored first and read from the
# store a member at a time.

def _zip(path, members, compression=zipfile.ZIP_DEFLATED):
    path.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(path, "w", compression) as z:
        for name, data in members:
            z.writestr(name, data)
    return path


def _docs_by_part(m, doc):
    """The family under doc as {part path: content}, recursively."""
    out = {}

    def walk(d, prefix):
        for c in m.state.children.get(d, []):
            obj = m.objects.get_object(c)
            out[prefix + obj["part"]] = m.state.documents[c]
            walk(c, prefix + obj["part"] + ">")
    walk(doc, "")
    return out


def test_zip_on_disk_is_streamed_and_matches_in_memory(tmp_path, box):
    z = _zip(tmp_path / "in" / "export.zip", [("mail/export.mbox", box.read_bytes()), ("outlook/k.pst", PST.read_bytes()),
                                              ("loose/one.eml", EML.read_bytes()), ("empty/", b"")])
    whole = Matter.create(tmp_path / "whole")
    in_memory = ingest_bytes(whole, z.read_bytes(), z.name, ident(z))

    m = Matter.create(tmp_path / "m")

    def no_read(self, *a, **k):
        raise AssertionError("a zip member read into memory")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(zipfile.ZipFile, "read", no_read)
        [doc] = _streamed(m, z)
    assert doc == in_memory
    a, b = _docs_by_part(Matter(m.root), doc), _docs_by_part(Matter(whole.root), in_memory)
    assert a == b
    assert sum(1 for p in a if p.startswith("mail/export.mbox>")) == N
    assert sum(1 for p in a if p.startswith("outlook/k.pst>") and p.count(">") == 1) == 5


def test_mailbox_attached_to_an_email_is_read_from_the_store(tmp_path, box, monkeypatch):
    from email.message import EmailMessage

    from adhocdocs.extract import archive, pst
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "a@x.example", "b@x.example", "the mailboxes you asked for"
    msg.set_content("attached")
    msg.add_attachment(PST.read_bytes(), maintype="application", subtype="vnd.ms-outlook", filename="k.pst")
    msg.add_attachment(box.read_bytes(), maintype="application", subtype="mbox", filename="export.mbox")

    def whole(_):
        raise AssertionError("a mailbox extracted from memory")
    monkeypatch.setattr(pst, "extract_pst", whole)
    monkeypatch.setattr(archive, "extract_mbox", whole)
    m = Matter.create(tmp_path / "m")
    doc = ingest_bytes(m, msg.as_bytes(), "send.eml", {"source": {"collection": "c", "path": "send.eml"}})
    m = Matter(m.root)
    [k, x] = m.state.children[doc]
    assert len(m.state.children[k]) == 5 and len(m.state.children[x]) == N

    # the same messages as the PST ingested on its own
    alone = Matter.create(tmp_path / "alone")
    [p] = ingest_paths(alone, [PST], None, "c")
    assert set(_docs_by_part(m, k).values()) == set(_docs_by_part(Matter(alone.root), p).values())


def test_unreadable_zip_member_loses_only_itself(tmp_path):
    z = _zip(tmp_path / "in" / "x.zip", [("a.txt", b"first " * 1000), ("b.txt", b"second " * 1000)])
    raw = bytearray(z.read_bytes())
    i = raw.index(b"a.txt") + len("a.txt") + 10      # into a.txt's compressed data
    raw[i] ^= 0xFF
    z.write_bytes(bytes(raw))
    m = Matter.create(tmp_path / "m")
    [doc] = ingest_paths(m, [z], None, "c")
    m = Matter(m.root)
    [kid] = m.state.children[doc]
    assert m.objects.get_object(kid)["part"] == "b.txt"
    meta = m.objects.get_object(m.state.metadata[doc])
    assert meta["unreadable"] == 1 and meta["members"] == 1
    assert not list((m.store_dir / "objects").glob("*.tmp"))


def test_zip_kind_from_a_large_file(tmp_path):
    """A zip's directory is at its end, past the first bytes: file_kind
    reads it there. Office files stay office files."""
    import io

    from adhocdocs.extract import kind_for
    big = _zip(tmp_path / "big", [("filler.bin", os.urandom(200_000)), ("a.txt", b"x")], zipfile.ZIP_STORED)
    assert file_kind(big, None) == "zip" == kind_for(None, big.read_bytes())
    doc = io.BytesIO()
    with zipfile.ZipFile(doc, "w") as d:
        d.writestr("filler.bin", os.urandom(200_000))
        d.writestr("word/document.xml", "<w/>")
    (tmp_path / "doc").write_bytes(doc.getvalue())
    assert file_kind(tmp_path / "doc", None) == "docx"


def test_file_nothing_can_extract_is_not_read(tmp_path):
    f = tmp_path / "in" / "blob.dat"
    f.parent.mkdir()
    f.write_bytes(b"\x00\xff" * 100_000)

    def no_read(self):
        raise AssertionError(f"{self} read into memory")
    m = Matter.create(tmp_path / "m")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_bytes", no_read)
        [doc] = ingest_paths(m, [f], None, "c")
    assert m.objects.get_object(m.state.metadata[doc])["kind"] == "other"
