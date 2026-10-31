"""Ingestion: originals into the object store, with layers, metadata and
children, every step recorded in the provenance log.

Runs on the host, outside the container (the container sees the store
read-only). Re-ingesting the same file with the same identity facts is a
no-op, because the document ID is the same.
"""

import os
import sys
from pathlib import Path

from .extract import Extraction, extract
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
    state = m.state
    content = m.objects.put_bytes(data)
    docobj = {"type": "document", "v": 1, "content": content, **identity}
    doc = object_id(docobj)
    if doc in state.documents and doc not in state.withdrawn:
        return doc
    m.objects.put_object(docobj)
    ev = m.logs["store"].append(op="add-document", doc=doc, content=content,
                                **{k: identity[k] for k in ("parent",) if k in identity})
    state.apply(ev)

    try:
        ext = extract(name, data)
    except Exception as e:  # an unreadable original is still in the matter, just unextracted
        print(f"warning: could not extract {name}: {e}", file=sys.stderr)
        ext = Extraction(kind="other")
    if content not in state.layers:
        store_layers(m, content, ext)

    meta = {"type": "metadata", "v": 1, "doc": doc, "kind": ext.kind, **ext.metadata}
    if name:
        meta["name"] = os.path.basename(name)
    ev = m.logs["store"].append(op="set-metadata", doc=doc, metadata=m.objects.put_object(meta))
    state.apply(ev)
    if report:
        report(doc, name, ext)

    for child in ext.children:
        ingest_bytes(m, child.data, child.name, {"parent": doc, "part": child.part}, report)
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
            docs.append(ingest_bytes(m, f.read_bytes(), f.name, identity, report))
    return docs
