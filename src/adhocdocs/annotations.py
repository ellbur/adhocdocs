"""Tags and notes: an append-only event log, replayed into current state.

Each line of ``annotations/log.jsonl`` is an event with ``id``, ``at``,
``author`` (``claude`` or ``user:<id>``), optional ``requested_by``, and
``op``:

=================  =========================================================
``define-tag``     ``tag``, and optional ``color``, ``hotkey``, ``group``,
                   ``description``
``edit-tag``       ``tag`` and the fields that change
``tag``/``untag``  ``tag``, ``target`` (a JSON locator)
``note``           ``note`` (new ID), ``target``, ``body`` (Markdown)
``reply``          ``note`` (new ID), ``parent`` (a note ID), ``body``
``edit-note``      ``note``, ``body``
``delete-note``    ``note``
``define-entity``  ``entity`` (new ID), ``name``, and optional ``kind``,
                   ``aliases``, ``summary``, ``recap``, ``see`` (targets),
                   ``date`` (an event's: ``YYYY-MM-DD``, ``YYYY-MM`` or ``YYYY``)
``edit-entity``    ``entity`` and the fields that change; ``add_aliases``,
                   ``remove_aliases``
``delete-entity``  ``entity``
=================  =========================================================

Entities are the entity layer's known entities (ENTITIES.md). Each field
remembers who set it last: Claude can't change a field, remove an alias or
delete an entity the user set, made or added.

Authorship comes from the write path, never from the request: the viewer
writes as ``user:<id>``, and the annotation API (:mod:`adhocdocs.annapi`),
the only write path from the container, writes as ``claude``. The rules in
:meth:`Annotations.check` keep Claude from changing the user's work.
"""

import json
import re
from contextlib import contextmanager

from . import literate, locators
from .matter import Matter, new_event_id, now
from .portable import locked

CLAUDE = "claude"

# Privilege calls are a lawyer's. Claude can't apply the tags that withhold
# (``privileged``) or redact (``redact…``) in a production, even when asked;
# it flags documents and passages for review with PRIVILEGE_CHECK instead.
PRIVILEGE_CHECK = "check-privilege"
PRIVILEGE_CHECK_DESCRIPTION = ("Check for possible privilege: flagged for a lawyer's review. "
                               "Not a privilege call; the note gives the reason.")


def is_privilege_call(tag: str) -> bool:
    return tag == "privileged" or tag.startswith("redact")


class Refused(Exception):
    """A write the rules don't allow."""


def target_key(target: dict) -> str:
    """Identity of a target: document, layer and selector (not the quote)."""
    return json.dumps({k: target[k] for k in ("doc", "layer", "select") if k in target}, sort_keys=True)


def is_user(author: str) -> bool:
    return author.startswith("user:")


class Annotations:
    def __init__(self):
        self.definitions: dict[str, dict] = {}   # tag -> definition (+ "by")
        self.tags: dict[str, dict] = {}          # target key -> tag -> application
        self.notes: dict[str, dict] = {}         # note ID -> note
        self.entities: dict[str, dict] = {}      # entity ID -> entity
        self.last_id: str | None = None

    @classmethod
    def load(cls, m: Matter) -> "Annotations":
        a = cls()
        for ev in m.annotation_log.read():
            a.apply(ev)
        return a

    def apply(self, ev: dict):
        op = ev["op"]
        self.last_id = ev["id"]
        if op == "define-tag":
            self.definitions[ev["tag"]] = {k: ev[k] for k in ("tag", "color", "hotkey", "group", "description") if k in ev} \
                | {"by": ev["author"], "at": ev["at"]}
        elif op == "edit-tag":
            d = self.definitions.setdefault(ev["tag"], {"tag": ev["tag"], "by": ev["author"]})
            d.update({k: ev[k] for k in ("color", "hotkey", "group", "description") if k in ev})
        elif op == "tag":
            self.tags.setdefault(target_key(ev["target"]), {})[ev["tag"]] = {
                "tag": ev["tag"], "target": ev["target"], "by": ev["author"], "at": ev["at"], "event": ev["id"],
                **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {})}
        elif op == "untag":
            self.tags.get(target_key(ev["target"]), {}).pop(ev["tag"], None)
        elif op in ("note", "reply"):
            parent = self.notes.get(ev.get("parent")) if op == "reply" else None
            self.notes[ev["note"]] = {
                "note": ev["note"], "target": parent["target"] if parent else ev["target"],
                "parent": ev.get("parent"), "body": ev["body"], "by": ev["author"], "at": ev["at"],
                **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {})}
        elif op == "edit-note":
            n = self.notes.get(ev["note"])
            if n:
                n.update(body=ev["body"], edited_at=ev["at"], edited_by=ev["author"])
        elif op == "delete-note":
            self.notes.pop(ev["note"], None)
        elif op == "define-entity":
            e = {"entity": ev["entity"], "name": ev["name"], "kind": ev.get("kind") or "other", "aliases": [],
                 "alias_by": {}, "summary": None, "recap": None, "see": [], "date": None, "by": ev["author"], "at": ev["at"],
                 "set_by": {}, "set_at": {}}
            self.entities[ev["entity"]] = e
            for f in ENTITY_FIELDS:
                if f in ev and f != "name":
                    e[f] = ev[f]
            for f in ENTITY_FIELDS:
                if f in ev:                     # a field nobody set is anyone's to set
                    e["set_by"][f], e["set_at"][f] = ev["author"], ev["at"]
            for a in dict.fromkeys([ev["name"], *ev.get("aliases", [])]):
                if a.strip():
                    _add_alias(e, a, ev["author"])
            e["aliases"] = list(e["alias_by"])
        elif op == "edit-entity":
            e = self.entities.get(ev["entity"])
            if e:
                for f in ENTITY_FIELDS:
                    if f in ev:
                        e[f] = ev[f]
                        e["set_by"][f], e["set_at"][f] = ev["author"], ev["at"]
                for a in ev.get("remove_aliases", []):
                    e["alias_by"].pop(a, None)
                for a in ([ev["name"]] if "name" in ev else []) + ev.get("add_aliases", []):
                    if a.strip():
                        _add_alias(e, a, ev["author"])
                e["aliases"] = list(e["alias_by"])
                e["edited_at"], e["edited_by"] = ev["at"], ev["author"]
                if ev.get("requested_by"):
                    e["requested_by"] = ev["requested_by"]
        elif op == "delete-entity":
            self.entities.pop(ev["entity"], None)

    def entity_named(self, name: str, kind: str | None) -> dict | None:
        from .entities import fold_text
        f = " ".join(fold_text(name).split())
        for e in self.entities.values():
            if " ".join(fold_text(e["name"]).split()) == f and (e["kind"] == (kind or "other")):
                return e
        return None

    # -- the rules --------------------------------------------------------------------

    def check(self, ev: dict):
        """Refuse a write the rules don't allow. Claude can't change or remove
        what the user made; the user can change anything."""
        op, author = ev["op"], ev["author"]
        if op == "define-tag":
            if ev["tag"] in self.definitions:
                raise Refused(f"tag {ev['tag']!r} is already defined; use edit-tag")
            if ev.get("hotkey") and any(d.get("hotkey") == ev["hotkey"] for d in self.definitions.values()):
                raise Refused(f"hotkey {ev['hotkey']!r} is already in use")
        elif op == "edit-tag":
            d = self.definitions.get(ev["tag"])
            if d is None:
                raise Refused(f"no tag {ev['tag']!r}")
            if author == CLAUDE and is_user(d["by"]):
                raise Refused(f"tag {ev['tag']!r} was defined by {d['by']}; Claude can't edit it")
            if ev.get("hotkey") and any(t != ev["tag"] and o.get("hotkey") == ev["hotkey"] for t, o in self.definitions.items()):
                raise Refused(f"hotkey {ev['hotkey']!r} is already in use")
        elif op in ("tag", "untag"):
            if op == "tag" and author == CLAUDE and is_privilege_call(ev["tag"]):
                raise Refused(f"privilege calls are a lawyer's: Claude can't apply {ev['tag']!r}, even when asked. "
                              "Flag it for review instead: ann flag-privilege <locator> --basis \"…\"")
            if ev["tag"] not in self.definitions:
                near = [t for t in self.definitions if t.lower() == ev["tag"].lower() or ev["tag"].lower() in t.lower()]
                raise Refused(f"no tag {ev['tag']!r} is defined" + (f" (did you mean {', '.join(near)}?)" if near else "")
                              + "; define it first with define-tag")
            current = self.tags.get(target_key(ev["target"]), {}).get(ev["tag"])
            if op == "untag":
                if current is None:
                    raise Refused(f"{ev['tag']!r} is not on that target")
                if author == CLAUDE and is_user(current["by"]):
                    raise Refused(f"{ev['tag']!r} was applied by {current['by']}; Claude can't remove it")
        elif op == "reply":
            if ev["parent"] not in self.notes:
                raise Refused(f"no note {ev['parent']}")
        elif op in ("edit-note", "delete-note"):
            n = self.notes.get(ev["note"])
            if n is None:
                raise Refused(f"no note {ev['note']}")
            if author == CLAUDE and is_user(n["by"]):
                raise Refused(f"note {ev['note']} was written by {n['by']}; Claude can't change it")
        if op in ("define-entity", "edit-entity") and ev.get("date") is not None and not ENTITY_DATE.fullmatch(str(ev["date"])):
            raise Refused(f"an entity's date is YYYY-MM-DD, YYYY-MM or YYYY, not {ev['date']!r}")
        if op == "define-entity":
            if not ev["name"].strip():
                raise Refused("an entity needs a name")
            same = self.entity_named(ev["name"], ev.get("kind"))
            if same:
                raise Refused(f"{ev['name']!r} is already entity {same['entity']} ({same['kind']}); "
                              "edit it with edit-entity, or give this one a name that tells them apart")
        elif op in ("edit-entity", "delete-entity"):
            e = self.entities.get(ev["entity"])
            if e is None:
                raise Refused(f"no entity {ev['entity']}")
            if author == CLAUDE:
                if op == "delete-entity" and (is_user(e["by"]) or any(is_user(b) for b in e["set_by"].values())
                                              or any(is_user(b) for b in e["alias_by"].values())):
                    raise Refused(f"entity {e['entity']} ({e['name']}) has the user's work in it; Claude can't delete it")
                for f in ENTITY_FIELDS:
                    if f in ev and is_user(e["set_by"].get(f, "")) and ev[f] != e[f]:
                        raise Refused(f"the {f} of {e['name']} was set by {e['set_by'][f]}; Claude can't change it"
                                      + ("; use recap for what you told the user" if f == "summary" else ""))
                for a in ev.get("remove_aliases", []):
                    if is_user(e["alias_by"].get(a, "")):
                        raise Refused(f"alias {a!r} of {e['name']} was added by {e['alias_by'][a]}; Claude can't remove it")
            for a in ev.get("remove_aliases", []):
                if a not in e["alias_by"]:
                    raise Refused(f"{a!r} is not an alias of {e['name']}")
                if a == ev.get("name", e["name"]):
                    raise Refused(f"{a!r} is the entity's name; rename it first")

    def group_conflicts(self, ev: dict) -> list[dict]:
        """Other tags of the same exclusive group on the target, to remove first."""
        group = self.definitions.get(ev["tag"], {}).get("group")
        if not group:
            return []
        on = self.tags.get(target_key(ev["target"]), {})
        return [a for t, a in on.items()
                if t != ev["tag"] and self.definitions.get(t, {}).get("group") == group]

    # -- queries ------------------------------------------------------------------------

    def applications(self) -> list[dict]:
        return [a for per in self.tags.values() for a in per.values()]

    def doc_tags(self) -> dict[str, set[str]]:
        """Document ID -> tags on the document or anywhere in it."""
        out: dict[str, set[str]] = {}
        for a in self.applications():
            out.setdefault(a["target"]["doc"], set()).add(a["tag"])
        return out


ENTITY_FIELDS = ("name", "kind", "summary", "recap", "see", "date")


# an event's date: a day, a month or a year (DATES.md)
ENTITY_DATE = re.compile(r"\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?")


def _add_alias(e: dict, alias: str, author: str):
    alias = " ".join(alias.split())
    if alias not in e["alias_by"]:
        e["alias_by"][alias] = author


def current_tags(m: Matter) -> dict[str, set[str]]:
    return Annotations.load(m).doc_tags()


def normalize_target(m: Matter, loc) -> dict:
    """A checked, durable target: broken locators are refused, and text
    ranges get their layer and quote fingerprint from the store."""
    r = locators.resolve(m, loc)
    if r["status"] == "broken":
        raise Refused(f"locator is broken: {r.get('error')}")
    t = {k: v for k, v in r["loc"].items() if k in ("doc", "layer", "select", "quote")}
    sel = t.get("select") or {}
    if not sel:
        return {"doc": t["doc"]}
    if "char" in sel and "quote" not in t:
        layer = m.state.layer(t["layer"])
        t["quote"] = locators.make_quote(m.objects.get_text(layer.text), *sel["char"])
    return t


# op -> (required fields, optional fields) in a write request
OPS = {
    "define-tag": (("tag",), ("color", "hotkey", "group", "description")),
    "edit-tag": (("tag",), ("color", "hotkey", "group", "description")),
    "tag": (("tag", "target"), ()),
    "untag": (("tag", "target"), ()),
    "note": (("target", "body"), ()),
    "reply": (("parent", "body"), ()),
    "edit-note": (("note", "body"), ()),
    "delete-note": (("note",), ()),
    "define-entity": (("name",), ("kind", "aliases", "summary", "recap", "see", "date")),
    "edit-entity": (("entity",), ("name", "kind", "summary", "recap", "see", "date", "add_aliases", "remove_aliases")),
    "delete-entity": (("entity",), ()),
}


@contextmanager
def _locked(m: Matter):
    path = m.annotation_log.path.parent / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f, locked(f):
        yield


def write(m: Matter, author: str, req: dict) -> list[dict]:
    """Validate a request, stamp it with ``author`` and append it. Returns the
    events written (more than one when an exclusive group tag replaces
    another). The viewer and the annotation API call this; nothing else
    should."""
    op = req.get("op")
    if op not in OPS:
        raise Refused(f"unknown op {op!r}")
    required, optional = OPS[op]
    for k in required:
        if req.get(k) in (None, ""):
            raise Refused(f"{op} needs {k}")
    ev = {"op": op, **{k: req[k] for k in required + optional if req.get(k) is not None}}
    if "target" in ev:
        ev["target"] = normalize_target(m, ev["target"])
    for k in ("aliases", "add_aliases", "remove_aliases", "see"):
        if k in ev and not isinstance(ev[k], list):
            raise Refused(f"{k} must be a list")
    for k in ("aliases", "add_aliases", "remove_aliases"):
        if k in ev:
            ev[k] = [" ".join(str(a).split()) for a in ev[k] if str(a).strip()]
    if "see" in ev:
        ev["see"] = [normalize_target(m, t) for t in ev["see"]]
    if op == "define-entity":
        ev["entity"] = "e_" + new_event_id()[3:]
    if op in ("note", "reply"):
        ev["note"] = "n_" + new_event_id()[3:]
    ev["author"] = author
    if req.get("requested_by"):
        ev["requested_by"] = str(req["requested_by"])

    with _locked(m):
        ann = Annotations.load(m)
        pending = []
        if op == "tag":
            if ev["tag"] in ann.tags.get(target_key(ev["target"]), {}):
                return []    # already tagged: nothing to record
            for other in ann.group_conflicts({**ev}):
                pending.append({"op": "untag", "tag": other["tag"], "target": other["target"], "author": author,
                                **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {})})
        pending.append(ev)
        for p in pending:
            ann.check({**p, "at": now()})
            ann.apply({**p, "id": "pending", "at": now()})
        return [m.annotation_log.append(**p) for p in pending]


def note_check(m: Matter, body: str) -> list[dict]:
    """Problems in a note by Claude: quotations must be references (Principle 5)."""
    return literate.problems(literate.check(m, body))


def entity_check(m: Matter, req: dict) -> list[dict]:
    """Problems in an entity's summary or recap by Claude: no typed quotations
    (point at the words with ``see`` instead)."""
    out = []
    for f in ("summary", "recap"):
        if req.get(f):
            out += [{**p, "field": f} for p in note_check(m, req[f])]
    return out
