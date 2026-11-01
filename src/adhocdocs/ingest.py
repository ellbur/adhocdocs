"""Ingestion: originals into the object store, with layers, metadata and
children, every step recorded in the provenance log.

Runs on the host, outside the container (the container sees the store
read-only). Re-ingesting the same file with the same identity facts is a
no-op, because the document ID is the same; re-running an ingest that was
interrupted finishes it. Mailboxes (mbox, PST) on disk are streamed, so
they needn't fit in memory (see :func:`ingest_file`).
"""

import os
import sys
from pathlib import Path

from .extract import SNIFF, STREAMED, Extraction, extract, extract_file, file_kind, unextracted_kind
from .matter import Matter
from .objects import object_id


def store_layers(m: Matter, content: str, ext: Extraction, log="store", objects=None):
    objects = objects or m.objects
    state = m.state
    known = {l.id for l in state.layers.get(content, [])}
    ids = []
    for layer in ext.layers:
        obj = {"type": "layer", "v": 1, "content": content, "view": layer.view,
               "text": objects.put_text(layer.text)}
        if layer.derived_from is not None:
            obj["derived_from"] = ids[layer.derived_from]
            obj["offset_map"] = objects.put_jsonl(layer.offset_map)
        if layer.segments:
            obj["segments"] = objects.put_jsonl(layer.segments)
        if layer.geometry:
            obj["geometry"] = objects.put_jsonl(layer.geometry)
        if layer.presentation:
            obj["presentation"] = objects.put_text(layer.presentation)
        lid = objects.put_object(obj)
        ids.append(lid)
        if lid not in known:
            ev = m.logs[log].append(op="add-layer", layer=lid, content=content, text=obj["text"], view=layer.view)
            state.apply({"feed": log, **ev})
        if layer.primary and content not in state.primary:
            ev = m.logs[log].append(op="set-primary", content=content, layer=lid)
            state.apply(ev)


def ingest_bytes(m: Matter, data: bytes, name: str | None, identity: dict, report=None) -> str:
    content = m.objects.put_bytes(data)
    return _ingest(m, content, name, identity, report,
                   lambda: extract(name, data), lambda: unextracted_kind(name, data))


def ingest_file(m: Matter, path, name: str | None, identity: dict, report=None) -> str:
    """Ingest a file on disk. An mbox or PST is never read into memory: it
    is copied into the store in blocks, then read from there a message at
    a time, so it can be any size. Anything else is read whole."""
    kind = file_kind(path, name)
    if kind not in STREAMED:
        return ingest_bytes(m, Path(path).read_bytes(), name, identity, report)
    content = m.objects.put_file(path)
    stored = m.objects.path(content)
    with open(stored, "rb") as f:
        head = f.read(SNIFF)
    return _ingest(m, content, name, identity, report,
                   lambda: extract_file(stored, kind), lambda: unextracted_kind(name, head))


def _ingest(m: Matter, content: str, name, identity: dict, report, run_extract, failed_kind) -> str:
    """A document's metadata is recorded last, after its children, so a
    document without it is unfinished: an ingest that was interrupted (in
    the middle of a large mailbox, say) picks up where it stopped when run
    again. Children already in carry on being skipped, quickly."""
    state = m.state
    docobj = {"type": "document", "v": 1, "content": content, **identity}
    doc = object_id(docobj)
    live = doc in state.documents and doc not in state.withdrawn
    if live and doc in state.metadata:
        return doc
    if not live:
        m.objects.put_object(docobj)
        ev = m.logs["store"].append(op="add-document", doc=doc, content=content,
                                    **{k: identity[k] for k in ("parent",) if k in identity})
        state.apply(ev)

    try:
        ext = run_extract()
    except Exception as e:  # an unreadable original is still in the matter, just unextracted
        print(f"warning: could not extract {name}: {e}", file=sys.stderr)
        ext = Extraction(kind=failed_kind())
    if content not in state.layers:
        store_layers(m, content, ext)

    n, error = 0, None
    children = iter(ext.children)
    try:
        while True:
            try:
                child = next(children)
            except StopIteration:
                break
            except Exception as e:      # the container broke part-way: keep what came out of it
                error = f"stopped after {n} members: {e}"
                print(f"warning: could not finish extracting {name}: {e}", file=sys.stderr)
                break
            ingest_bytes(m, child.data, child.name, {"parent": doc, "part": child.part}, report)
            n += 1
    finally:
        if hasattr(children, "close"):
            children.close()

    meta = {"type": "metadata", "v": 1, "doc": doc, "kind": ext.kind, **ext.metadata}
    if error:
        meta["extraction_error"] = error
    if name:
        meta["name"] = os.path.basename(name)
    ev = m.logs["store"].append(op="set-metadata", doc=doc, metadata=m.objects.put_object(meta))
    state.apply(ev)
    if report:
        report(doc, name, ext, n)
    return doc


def ingest_paths(m: Matter, paths, custodian: str | None, collection: str, report=None) -> list[str]:
    docs = []
    for p in paths:
        p = Path(p)
        files = sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p]
        base = p if p.is_dir() else p.parent
        for f in files:
            if any(part.startswith(".") for part in f.relative_to(base).parts):
                continue
            identity = {"source": {"collection": collection, "path": str(f.relative_to(base))}}
            if custodian:
                identity["custodian"] = f"cust:{custodian}"
            docs.append(ingest_file(m, f, f.name, identity, report))
    return docs


MANIFEST_KEYS = {"name", "custodian", "paths"}


def load_manifest(path) -> list[dict]:
    """Read an ingest manifest: a TOML file of ``[[collection]]`` tables,
    each ingested as ``adhoc ingest --collection NAME --custodian CUSTODIAN
    PATHS...`` would be::

        [[collection]]
        name = "smith-laptop-2026-03"   # required
        custodian = "smith-j"           # optional
        paths = ["smith-files"]         # files or directories, relative to the manifest

    Everything is checked, and every path must exist, before anything is
    ingested. Returns the collections with absolute paths.
    """
    import tomllib
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"cannot read manifest {path}: {e.strerror}")
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: {e}")
    extra = set(data) - {"collection"}
    if extra:
        raise ValueError(f"{path}: unknown top-level key(s) {', '.join(sorted(extra))}; expected [[collection]]")
    colls = data.get("collection")
    if not isinstance(colls, list) or not colls:
        raise ValueError(f"{path}: no [[collection]] entries")
    out = []
    for i, c in enumerate(colls, 1):
        where = f"{path}: collection {i}"
        extra = set(c) - MANIFEST_KEYS
        if extra:
            raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(extra))}")
        name = c.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{where}: needs a name")
        custodian = c.get("custodian")
        if custodian is not None and (not isinstance(custodian, str) or not custodian):
            raise ValueError(f"{where} ({name}): custodian must be a non-empty string")
        paths = c.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
            raise ValueError(f"{where} ({name}): paths must be a non-empty list of strings")
        resolved = [(path.parent / p).resolve() for p in paths]
        for p, r in zip(paths, resolved):
            if not r.exists():
                raise ValueError(f"{where} ({name}): {p} does not exist (looked for {r})")
        out.append({"name": name, "custodian": custodian, "paths": resolved})
    return out


def ingest_manifest(m: Matter, path, report=None) -> list[str]:
    docs = []
    for c in load_manifest(path):
        docs += ingest_paths(m, c["paths"], c["custodian"], c["name"], report)
    return docs
