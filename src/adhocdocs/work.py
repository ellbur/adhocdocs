"""Published work product: pages, chronologies, key sets, lists and other
files made in the workspace, shared with the matter's other installations.

The workspace is private and isn't synced (SYNC.md). Work crosses over by
**publishing**: a snapshot of files, stored by hash in the store, recorded
as an event in the ``work`` feed (``work/logs/<origin>.jsonl``, one log per
installation like the others), and laid out in ``<matter>/work/`` on every
installation that has the event. ``work/`` is derived: rebuilt from the
feed whenever it changes, and mounted read-only in a session.

Events, besides ``id``, ``at`` and ``author`` (``claude`` or ``user:<id>``,
stamped by the write path) and optional ``requested_by``:

===========  ===============================================================
``publish``  ``name`` (the path in ``work/``, as in the workspace),
             ``publication`` (the hash of ``{"type": "publication", "v": 1,
             "files": {path: hash}}``), ``revises`` (the versions it
             revises), ``kind`` (``page``, ``list`` or ``files``)
``retire``   ``name``, ``revises``: takes it out of ``work/``
===========  ===============================================================

A **version** is a publish (or retire) event, named by its event ID. Each
names the versions it revises, so a name's versions form a history. The
**heads** are the versions nobody has revised. One head is current (the
latest in the merge order) and is what ``work/<name>`` holds. More than one
head is a **fork**: two installations revised the same version without
seeing each other's. The others are kept in ``work/.forks/<name>/<version>/``
until someone publishes a version that revises them all (``--merge``).
Nothing is merged silently: a page's prose is someone's judgment.

Publishing carries what the work needs. A page or list can quote a layer
made in the workspace (``layers ocr``), which other installations don't
have; publishing promotes such layers into the store, so the quotations
resolve everywhere. A page's ``cite-style`` file is published with it.

Which version a workspace file was checked out from or last published as is
kept in ``workspace/.adhoc/work.json``, so publishing it again revises that
version. Publishing over a name someone else published, without having
checked it out, is refused: check it out first, and merge.
"""

import json
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .annotations import Refused
from .matter import Matter, new_event_id
from .objects import HASH_PREFIX, ObjectStore, object_id
from .portable import locked

TEXT_SUFFIXES = (".md", ".jsonl", ".json", ".txt", ".csv", ".html", ".py", ".toml", ".yaml", ".yml")
HASH = re.compile(r"sha256:[0-9a-f]{64}")


# -- state ------------------------------------------------------------------------------


class Work:
    """The work feed replayed."""

    def __init__(self):
        self.versions: dict[str, dict] = {}      # version (event ID) -> version
        self.names: dict[str, list[str]] = {}    # name -> its versions, in merge order

    @classmethod
    def load(cls, m: Matter) -> "Work":
        w = cls()
        for ev in m.work_log.read():
            w.apply(ev)
        return w

    def apply(self, ev: dict):
        if ev["op"] not in ("publish", "retire"):
            return
        v = {"version": ev["id"], "name": ev["name"], "op": ev["op"], "revises": ev.get("revises", []),
             "by": ev["author"], "at": ev["at"], "origin": ev.get("origin"),
             **{k: ev[k] for k in ("publication", "kind", "requested_by") if k in ev}}
        self.versions[ev["id"]] = v
        self.names.setdefault(ev["name"], []).append(ev["id"])

    def heads(self, name: str) -> list[str]:
        vs = self.names.get(name, [])
        revised = {r for v in vs for r in self.versions[v]["revises"]}
        return [v for v in vs if v not in revised]

    def current(self, name: str) -> dict | None:
        """The version ``work/<name>`` holds: the latest head, unless that
        retired the name."""
        heads = self.heads(name)
        if not heads:
            return None
        v = self.versions[heads[-1]]
        return None if v["op"] == "retire" else v

    def forks(self, name: str) -> list[dict]:
        """Heads other than the current one, that still hold something."""
        heads = self.heads(name)
        return [self.versions[h] for h in heads[:-1] if self.versions[h]["op"] == "publish"]

    def summary(self, name: str) -> dict:
        cur = self.current(name)
        return {"name": name, "current": _public(cur) if cur else None,
                "forks": [_public(f) for f in self.forks(name)], "versions": len(self.names.get(name, []))}


def _public(v: dict) -> dict:
    return {k: v[k] for k in ("version", "by", "at", "origin", "kind", "revises", "requested_by") if k in v}


def files_of(m: Matter, v: dict) -> dict[str, str]:
    return m.objects.get_object(v["publication"])["files"] if v.get("publication") else {}


# -- the workspace side ------------------------------------------------------------------


def _bases_path(m: Matter) -> Path:
    return m.adhoc_dir / "work.json"


def bases(m: Matter) -> dict:
    """name -> {"version", "files": {path: hash}}: what the workspace's copy
    was checked out from, or last published as."""
    try:
        return json.loads(_bases_path(m).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _set_base(m: Matter, name: str, version: str, files: dict):
    b = bases(m)
    b[name] = {"version": version, "files": files}
    p = _bases_path(m)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(b, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(p)


def name_of(m: Matter, path) -> str:
    """The name of a path in the workspace: the path relative to it. A
    relative path is taken from the current directory if that's in the
    workspace, else from the workspace."""
    ws = m.workspace.resolve()
    p = Path(path)
    if not p.is_absolute():
        here = Path.cwd().resolve()
        p = (here if here == ws or ws in here.parents else ws) / p
    p = p.resolve()
    if ws not in p.parents:
        raise Refused(f"{path} is not in the workspace")
    return check_name(m, str(PurePosixPath(*p.relative_to(ws).parts)))


def check_name(m: Matter, name: str) -> str:
    """A name as given to the write path (from the API): relative, inside
    the workspace, and not hidden."""
    rel = PurePosixPath(name)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts or "\\" in name:
        raise Refused(f"{name!r} isn't a path in the workspace")
    if any(part.startswith(".") for part in rel.parts):
        raise Refused(f"{name} is hidden; only work product can be published")
    ws = m.workspace.resolve()
    if ws not in (ws / name).resolve().parents:
        raise Refused(f"{name} isn't in the workspace")
    return str(rel)


def _gather(m: Matter, name: str) -> dict[str, Path]:
    """The files a publication of ``name`` holds: the file, or every file in
    the directory, and a page's cite-style file."""
    ws = m.workspace.resolve()
    p = ws / name
    if p.is_dir():
        found = {str(PurePosixPath(*f.relative_to(ws).parts)): f for f in sorted(p.rglob("*"))
                 if f.is_file() and not any(x.startswith(".") for x in f.relative_to(ws).parts)}
        if not found:
            raise Refused(f"{name} is an empty directory")
        return found
    if not p.is_file():
        raise Refused(f"no file {name} in the workspace")
    found = {name: p}
    if p.suffix == ".md":
        style = _front_matter(p).get("cite-style")
        if isinstance(style, str) and style.endswith(".py"):
            sp = (p.parent / style).resolve()
            if ws not in sp.parents or not sp.is_file():
                raise Refused(f"{name}'s cite-style {style} isn't a file in the workspace")
            found[str(PurePosixPath(*sp.relative_to(ws).parts))] = sp
    return found


def _front_matter(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    import yaml
    try:
        meta = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _kind(name: str, files: dict) -> str:
    if len(files) == 1 or name.endswith(".md") or name.endswith(".list.jsonl"):
        if name.endswith(".md"):
            return "page"
        if name.endswith(".list.jsonl"):
            return "list"
    return "files"


# -- promoting workspace layers ------------------------------------------------------------


def _layer_refs(obj: dict) -> list[str]:
    return [obj[k] for k in ("text", "offset_map", "segments", "geometry", "presentation") if obj.get(k)]


def workspace_layers(m: Matter, paths) -> list[str]:
    """Layers the files name that only this workspace has."""
    state = m.state
    found = []
    for p in paths:
        if p.suffix not in TEXT_SUFFIXES:
            continue
        for h in dict.fromkeys(HASH.findall(p.read_text(encoding="utf-8", errors="replace"))):
            info = state.layer(h)
            if info is not None and info.feed == "workspace" and h not in found:
                found.append(h)
    return found


def _store(m: Matter) -> ObjectStore:
    """The store alone: what's written there must be there, not just
    readable through the workspace's objects."""
    return ObjectStore(m.store_dir / "objects")


def promote(m: Matter, layer_ids) -> list[str]:
    """Put workspace layers (and the layers they derive from) in the store,
    with ``add-layer`` events in this installation's store log."""
    state = m.state
    store = _store(m)
    store_layers = {l.id for ls in state.layers.values() for l in ls if l.feed == "store"}
    done = []

    def one(lid):
        if lid in store_layers or lid in done:
            return
        obj = m.workspace_objects.get_object(lid)
        if obj.get("derived_from"):
            one(obj["derived_from"])
        for h in _layer_refs(obj):
            if not store.has(h):
                store.put_bytes(m.workspace_objects.get_bytes(h))
        store.put_object(obj)
        ev = m.logs["store"].append(op="add-layer", layer=lid, content=obj["content"], text=obj["text"],
                                    view=obj["view"], promoted=True)
        state.apply(ev)
        done.append(lid)

    for lid in layer_ids:
        one(lid)
    return done


# -- writing ---------------------------------------------------------------------------------


@contextmanager
def _locked(m: Matter):
    d = m.root / "work"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".lock", "w", encoding="utf-8", newline="\n") as f, locked(f):
        yield


def publish(m: Matter, author: str, name: str, revises=None, merge=False, requested_by=None) -> dict:
    """Publish a workspace file or directory, by its name (path in the
    workspace: :func:`name_of`). Host only: the store and the work feed are
    written. The annotation API calls this for Claude."""
    name = check_name(m, name)
    found = _gather(m, name)
    with _locked(m):
        w = Work.load(m)
        heads = w.heads(name)
        base = bases(m).get(name)
        if merge:
            revises = heads
        elif revises is None:
            if base:
                revises = [base["version"]]
            elif heads and w.current(name):
                cur = w.current(name)
                raise Refused(f"{name} is already published (version {cur['version']}, by {cur['by']}); "
                              f"check it out first (work checkout {name}), make your changes there, and "
                              f"publish again, or publish under another name")
            else:
                revises = list(heads)      # a retired name starts again
        for r in revises:
            if r not in w.versions or w.versions[r]["name"] != name:
                raise Refused(f"{r} is not a version of {name}")
        promoted = promote(m, workspace_layers(m, found.values()))
        store = _store(m)
        files = {rel: store.put_file(p) for rel, p in found.items()}
        pub = {"type": "publication", "v": 1, "files": files}
        pid = object_id(pub)
        cur = w.current(name)
        if cur and cur.get("publication") == pid and sorted(revises) == sorted(heads) == [cur["version"]]:
            _set_base(m, name, cur["version"], files)
            return {"name": name, "version": cur["version"], "unchanged": True, "promoted": promoted}
        store.put_object(pub)
        ev = m.work_log.append(op="publish", name=name, publication=pid, revises=list(revises),
                               kind=_kind(name, files), author=author,
                               **({"requested_by": str(requested_by)} if requested_by else {}))
        _set_base(m, name, ev["id"], files)
    materialize(m)
    w = Work.load(m)
    return {"name": name, "version": ev["id"], "files": sorted(files), "promoted": promoted,
            "forks": [f["version"] for f in w.forks(name)]}


def retire(m: Matter, author: str, name: str, requested_by=None) -> dict:
    with _locked(m):
        w = Work.load(m)
        if not w.current(name):
            raise Refused(f"{name} isn't published")
        ev = m.work_log.append(op="retire", name=name, revises=w.heads(name), author=author,
                               **({"requested_by": str(requested_by)} if requested_by else {}))
    materialize(m)
    return {"name": name, "version": ev["id"], "retired": True}


# -- reading -------------------------------------------------------------------------------


def checkout(m: Matter, name: str, version: str | None = None, force=False) -> dict:
    """Copy a published version into the workspace, to work on. Works in a
    session (the workspace is writable there). Refuses to overwrite a
    workspace file with changes of its own unless ``force``."""
    w = Work.load(m)
    if version is None:
        v = w.current(name)
        if v is None:
            raise Refused(f"{name} isn't published")
    else:
        v = w.versions.get(version)
        if v is None or v["name"] != name or v["op"] != "publish":
            raise Refused(f"{version} is not a published version of {name}")
    files = files_of(m, v)
    base = bases(m).get(name, {}).get("files", {})
    ws = m.workspace
    for rel, h in files.items():
        p = ws / rel
        if p.exists() and not force:
            mine = _hash_file(p)
            if mine != h and mine != base.get(rel):
                raise Refused(f"{rel} in the workspace has changes that aren't published; publish them, move "
                              "the file, or check out with --force to replace it")
    for rel, h in files.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".checkout-tmp")
        with open(tmp, "wb") as f:
            f.write(m.objects.get_bytes(h))
        tmp.replace(p)
    _set_base(m, name, v["version"], files)
    return {"name": name, "version": v["version"], "files": sorted(files)}


def _hash_file(p: Path) -> str:
    import hashlib
    with open(p, "rb") as f:
        return HASH_PREFIX + hashlib.file_digest(f, "sha256").hexdigest()


def listing(m: Matter) -> list[dict]:
    w = Work.load(m)
    return [w.summary(n) for n in sorted(w.names) if w.current(n) or w.forks(n)]


def history(m: Matter, name: str) -> list[dict]:
    w = Work.load(m)
    heads = set(w.heads(name))
    return [{**_public(w.versions[v]), "op": w.versions[v]["op"], "head": v in heads,
             "files": sorted(files_of(m, w.versions[v]))} for v in w.names.get(name, [])]


def forks(m: Matter) -> list[dict]:
    """Every fork, for ``adhoc sync conflicts``."""
    w = Work.load(m)
    out = []
    for name in sorted(w.names):
        for f in w.forks(name):
            cur = w.current(name) or w.versions[w.heads(name)[-1]]
            out.append({"kind": "fork", "name": name, "event": f["version"], "origin": f["origin"], "author": f["by"],
                        "at": f["at"], "op": "publish",
                        "reason": f"{name} was revised on two installations: {cur['version']} by {cur['by']} is "
                                  f"current, and {f['version']} by {f['by']} is in work/.forks/; publish a version "
                                  "that merges them (work publish --merge)"})
    return out


# -- work/ on disk ----------------------------------------------------------------------------


def _short(version: str) -> str:
    return version.removeprefix("ev_")[-10:]


def materialize(m: Matter) -> dict:
    """Make ``work/`` match the feed: each current version's files at their
    names, each fork's under ``.forks/``. Only files this wrote before are
    ever removed. Host only."""
    root = m.root / "work"
    root.mkdir(parents=True, exist_ok=True)
    w = Work.load(m)
    want: dict[str, str] = {}
    for name in w.names:
        cur = w.current(name)
        if cur:
            want.update(files_of(m, cur))
        for f in w.forks(name):
            for rel, h in files_of(m, f).items():
                want[f".forks/{name}/{_short(f['version'])}/{rel}"] = h
    manifest = root / ".materialized.json"
    try:
        had = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        had = {}
    written = 0
    for rel, h in want.items():
        p = root / rel
        if had.get(rel) == h and p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp-" + new_event_id()[-6:])
        src = m.objects.path(h)
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o444)
        tmp.replace(p)
        written += 1
    removed = 0
    for rel in set(had) - set(want):
        p = root / rel
        if p.exists():
            p.unlink()
            removed += 1
            for d in p.parents:
                if d == root or any(d.iterdir()):
                    break
                d.rmdir()
    tmp = manifest.with_suffix(".tmp")
    tmp.write_text(json.dumps(want, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(manifest)
    return {"written": written, "removed": removed}
