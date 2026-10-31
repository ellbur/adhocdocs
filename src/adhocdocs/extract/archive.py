"""Containers: zip archives and mbox mailboxes. Their members become child
documents (``part`` is the member's path, or the message's position), and
the container itself gets an empty layer: extracted, with no text of its own.
"""

import io
import mailbox
import tempfile
import zipfile
from pathlib import PurePosixPath

from . import Child, Extraction, Layer

MAX_MEMBERS = 100_000


def extract_zip(data: bytes) -> Extraction:
    children = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist()[:MAX_MEMBERS]:
            if info.is_dir() or info.flag_bits & 0x1:          # directories; encrypted members can't be read
                continue
            path = PurePosixPath(info.filename)
            if path.name.startswith(".") or "__MACOSX" in path.parts:
                continue
            children.append(Child(part=info.filename, name=path.name, data=z.read(info)))
    return Extraction(kind="archive", layers=[Layer("native", "", primary=True)],
                      metadata={"members": len(children)}, children=children)


def extract_mbox(data: bytes) -> Extraction:
    # mailbox wants a file; the bytes are already in memory, so this is brief.
    with tempfile.NamedTemporaryFile(suffix=".mbox") as f:
        f.write(data)
        f.flush()
        box = mailbox.mbox(f.name, create=False)
        children = [Child(part=str(i), name=f"message-{i}.eml", data=msg.as_bytes())
                    for i, msg in enumerate(box, 1)]
        box.close()
    return Extraction(kind="mailbox", layers=[Layer("native", "", primary=True)],
                      metadata={"members": len(children)}, children=children)
