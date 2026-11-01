"""Indexes live in ``workspace/.adhoc/indexes/<name>/``, each with a
``manifest.json`` (kind, settings, cursor, updater). The default indexes
are ``fields``, ``keyword``, ``semantic``, ``threads``, ``neardup``, ``names``, ``entities`` and ``dates``. Any other directory with a
manifest is a custom index, updated by running its ``updater`` command (see
:class:`ExternalIndex`)."""

import json
import os
import subprocess

from ..matter import Matter
from .base import Busy, Index, NeedsRebuild
from .fields import FieldsIndex
from .keyword import KeywordIndex

DEFAULTS = ("fields", "keyword", "semantic", "threads", "neardup", "names", "entities", "dates")


def _kinds():
    from .dates import DatesIndex
    from .entities import EntitiesIndex
    from .names import NamesIndex
    from .neardup import NearDupIndex
    from .semantic import SemanticIndex
    from .threads import ThreadsIndex
    return {"fields": FieldsIndex, "keyword": KeywordIndex, "semantic": SemanticIndex,
            "threads": ThreadsIndex, "neardup": NearDupIndex, "names": NamesIndex,
            "entities": EntitiesIndex, "dates": DatesIndex}


class ExternalIndex(Index):
    """A custom index. Its updater gets change events as JSON Lines on stdin,
    with ``ADHOC_INDEX_DIR`` and ``ADHOC_MATTER`` set, applies them, and
    writes the new cursor into the manifest itself."""

    def __init__(self, m, name):
        super().__init__(m, name)
        self.kind = self.manifest().get("kind", "custom")

    def settings(self):
        return self.manifest().get("settings", {})

    def cursor(self):
        return self.manifest().get("cursor") or {"store": None, "workspace": None}

    def update(self, progress=None):
        events = list(self.m.changes(self.cursor()))
        if not events:
            return 0
        env = {**os.environ, "ADHOC_INDEX_DIR": str(self.dir), "ADHOC_MATTER": str(self.m.root)}
        data = "".join(json.dumps(e) + "\n" for e in events)
        subprocess.run(self.manifest()["updater"], shell=True, input=data, text=True, encoding="utf-8", env=env, check=True)
        return len(events)

    def status(self):
        return {"name": self.name, "kind": self.kind, "settings": self.settings(),
                "cursor": self.cursor(), "behind": self.behind()}


def open_index(m: Matter, name: str, create=True) -> Index:
    """The index called ``name``, created empty if it's a default one that doesn't exist yet."""
    d = m.indexes_dir / name
    kinds = _kinds()
    if (d / "manifest.json").exists():
        kind = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["kind"]
        idx = kinds[kind](m, name) if kind in kinds else ExternalIndex(m, name)
    elif name in kinds and create:
        idx = kinds[name](m, name)
        idx.create()
    else:
        raise KeyError(f"no index named {name}")
    return idx


def all_names(m: Matter) -> list[str]:
    names = list(DEFAULTS)
    if m.indexes_dir.is_dir():
        names += sorted(d.name for d in m.indexes_dir.iterdir() if (d / "manifest.json").exists() and d.name not in names)
    return names


__all__ = ["Busy", "NeedsRebuild", "open_index", "all_names", "DEFAULTS"]
