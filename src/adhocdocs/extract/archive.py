"""Containers: zip archives and mbox mailboxes. Their members become child
documents (``part`` is the member's path, or the message's position), and
the container itself gets an empty layer: extracted, with no text of its own.

A zip or mbox on disk is read by :func:`extract_zip_file` or
:func:`extract_mbox_file`, whose children come one at a time, so neither
needs to fit in memory. Ingestion stores every container before reading
it, so this covers containers inside containers too (see ``adhocdocs.ingest``).
"""

import io
import mailbox
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from . import Child, Extraction, Layer

MAX_MEMBERS = 100_000


def _members(z: zipfile.ZipFile):
    for info in z.infolist()[:MAX_MEMBERS]:
        if info.is_dir() or info.flag_bits & 0x1:          # directories; encrypted members can't be read
            continue
        path = PurePosixPath(info.filename)
        if path.name.startswith(".") or "__MACOSX" in path.parts:
            continue
        yield info, path.name


def extract_zip(data: bytes) -> Extraction:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        children = [Child(part=info.filename, name=name, data=z.read(info)) for info, name in _members(z)]
    return Extraction(kind="archive", layers=[Layer("native", "", primary=True)],
                      metadata={"members": len(children)}, children=children)


def extract_zip_file(path) -> Extraction:
    """A zip on disk, of any size. Only its directory is read up front;
    each member comes with ``open``, to be streamed into the store, so
    neither the archive nor a member is ever held in memory."""
    z = zipfile.ZipFile(path)
    meta: dict = {}

    def children():
        n = 0
        try:
            for info, name in _members(z):
                n += 1
                yield Child(part=info.filename, name=name, open=lambda info=info: z.open(info))
        finally:
            meta["members"] = n
            z.close()
    return Extraction(kind="archive", layers=[Layer("native", "", primary=True)],
                      metadata=meta, children=children())


def extract_mbox(data: bytes) -> Extraction:
    """An mbox in memory (an attachment, a zip member): written to a
    temporary file for :mod:`mailbox`, and read from it in full."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "box.mbox"
        path.write_bytes(data)
        ext = extract_mbox_file(path)
        ext.children = list(ext.children)
    return ext


def extract_mbox_file(path) -> Extraction:
    """An mbox on disk, of any size. Its messages are read one at a time as
    ``children`` is iterated; only their offsets are held, never the
    mailbox. ``metadata`` is complete once the iteration ends."""
    box = mailbox.mbox(str(path), factory=None, create=False)
    meta: dict = {}

    def children():
        n = 0
        try:
            for msg in box:
                n += 1
                yield Child(part=str(n), name=f"message-{n}.eml", data=msg.as_bytes())
        finally:
            meta["members"] = n
            box.close()
    return Extraction(kind="mailbox", layers=[Layer("native", "", primary=True)],
                      metadata=meta, children=children())
