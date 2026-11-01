"""Tags and notes: an append-only event log, replayed into current state.

Each line of ``annotations/logs/<origin>.jsonl`` (one log per installation,
read merged: see :mod:`adhocdocs.matter`) is an event with ``id``, ``at``,
``author`` (``claude`` or ``user:<id>``), optional ``requested_by``, and
``op``:

=================  =========================================================
``define-tag``     ``tag``, and optional ``color``, ``group``,
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
``reserve-numbers`` ``series`` (``bates:<prefix>`` or ``exhibit:<prefix>``), ``first``,
                   ``last``, optional ``name`` (the production or set): a range taken
                   before it's used, so two installations don't number the same
``release-numbers`` ``reservation`` (the reserve event's ID): given back unused, by
                   whoever reserved them, from the same installation
``use-numbers``    ``reservation``: the numbers are on documents now, and can't be
                   given back
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


MAX_RESERVATION = 1_000_000

# What a feed adds to an event as it reads it; not part of the event.
_READ_TAGS = ("feed", "log", "origin")


def _bare(ev: dict) -> dict:
    return {k: v for k, v in ev.items() if k not in _READ_TAGS}


class Annotations:
    def __init__(self):
        self.definitions: dict[str, dict] = {}   # tag -> definition (+ "by")
        self.tags: dict[str, dict] = {}          # target key -> tag -> application
        self.notes: dict[str, dict] = {}         # note ID -> note
        self.entities: dict[str, dict] = {}      # entity ID -> entity
        self.entity_alias: dict[str, str] = {}   # merged entity ID -> the entity it joined
        self.conflicts: list[dict] = []          # events that read differently on replay
        self.replaced: dict[str, list] = {}      # event ID -> the events it replays as
        self.last_id: str | None = None
        self.hotkeys: dict[str, str] = {}        # tag -> key, this installation's own (hotkeys())
        self.pool = None                          # who writes for whom (adhocdocs.pool), once there's a pool
        self.reservations: dict[str, dict] = {}  # reserve event ID -> number range (adhocdocs.numbers)

    @classmethod
    def load(cls, m: Matter) -> "Annotations":
        a = cls()
        a.pool = _pool(m)
        for ev in m.annotation_log.read():
            a.replay(ev)
        a.hotkeys = hotkeys(m)
        for t, d in a.definitions.items():
            d.pop("hotkey", None)
            if t in a.hotkeys:
                d["hotkey"] = a.hotkeys[t]
        return a

    # -- replay: the rules again, for events from every installation -------------------

    def replay(self, ev: dict):
        """Apply an event from the logs, holding it to the rules again.

        Each event was checked where it was written, against what that
        installation had. Events written concurrently on two installations
        can conflict once merged (the same tag defined twice, a hotkey taken
        twice, a note edited after someone else deleted it), and a log that
        didn't come through the write path (an installation running
        modified code) may break the rules outright. Replaying in the merge
        order that every installation shares, each event is resolved the
        same way everywhere: applied, applied in part, or of no effect.
        Anything other than plain application is recorded in ``conflicts``
        (when someone should look) and ``replaced`` (what the event
        replays as, for the viewer: see :func:`save_replay`)."""
        self.last_id = ev["id"]
        raw = ev
        from .signing import has_hidden
        if has_hidden(ev):
            from .purge import reveal
            shown = reveal(ev)          # a purge hid what it said about a purged document
            if shown is None:
                self.replaced[ev["id"]] = []
                return
            ev = shown
        try:
            if self.pool is not None and not self.pool.writes_for(ev.get("origin"), ev.get("author")):
                raise Refused(f"{ev.get('author')} doesn't write from that installation ({ev.get('origin')}); "
                              "another installation is theirs")
            out = self._reconcile(ev)
            for e in out:
                self.check(e, replaying=True)
        except Refused as e:
            self._conflict(ev, "refused", str(e))
            out = []
        if out != [raw]:
            self.replaced[ev["id"]] = [_bare(e) for e in out]
        for e in out:
            self.apply(e)

    def _conflict(self, ev: dict, kind: str, reason: str):
        self.conflicts.append({"event": ev["id"], "origin": ev.get("origin"), "at": ev.get("at"),
                               "author": ev.get("author"), "op": ev["op"], "kind": kind, "reason": reason,
                               **({k: ev[k] for k in ("tag", "note", "entity", "name") if k in ev})})

    def _reconcile(self, ev: dict) -> list[dict]:
        """The events ``ev`` replays as, given the state so far: usually just
        ``ev``. Raises Refused for an event of no effect worth reporting."""
        op = ev["op"]
        if ev.get("entity") in self.entity_alias:
            ev = {**ev, "entity": self.entity_alias[ev["entity"]]}
        if op in ("define-tag", "edit-tag") and "hotkey" in ev:
            # hotkeys are each installation's own (hotkeys()); in events from
            # before that they're ignored, and replay as if not there
            ev = {k: v for k, v in ev.items() if k != "hotkey"}
            if op == "edit-tag" and not any(k in ev for k in ("color", "group", "description")):
                return []
        if op == "define-tag":
            d = self.definitions.get(ev["tag"])
            fields = ("color", "group", "description")
            if d is not None:
                if all(ev.get(k) == d.get(k) for k in fields):
                    return []           # defined the same way twice: nothing to see
                theirs = ", ".join(f"{k} {ev[k]!r}" for k in fields if k in ev and ev[k] != d.get(k))
                self._conflict(ev, "defined-twice", f"tag {ev['tag']!r} was already defined by {d['by']}; "
                                                    f"this definition's {theirs or 'fields'} weren't used")
                return []
        if op == "untag" and ev["tag"] not in self.tags.get(target_key(ev["target"]), {}):
            return []                   # untagged twice
        if op == "tag" and ev["author"] == CLAUDE and is_privilege_call(ev["tag"]):
            self._conflict(ev, "privilege-call-by-claude", f"Claude applied {ev['tag']!r}; kept as a flag, "
                                                           "since only a person's privilege call acts in a production")
        if op == "tag" and ev["tag"] in self.definitions:
            # A group's other tags on the target were untagged where this was
            # written, by events of their own. One applied concurrently elsewhere
            # wasn't, so it goes now: the later tag wins.
            others = self.group_conflicts(ev)
            if others:
                if ev["author"] == CLAUDE and any(is_user(o["by"]) for o in others):
                    raise Refused(f"{ev['tag']!r} would replace {', '.join(o['tag'] for o in others)}, "
                                  f"applied by {others[0]['by']}; Claude can't remove it")
                self._conflict(ev, "group-replaced", f"{ev['tag']!r} replaced {', '.join(o['tag'] for o in others)}, "
                                                     "tagged concurrently on another installation")
                return [{**ev, "op": "untag", "tag": o["tag"], "target": o["target"], "id": ev["id"]} for o in others] + [ev]
        if op == "reserve-numbers":
            try:
                self.check(ev, replaying=True)
            except Refused as e:
                # reserved on two installations at once: the earlier stands
                self._conflict(ev, "numbers-overlap", str(e))
                return []
        if op == "reply" and ev.get("parent") not in self.notes and ev.get("target"):
            self._conflict(ev, "parent-deleted", f"replied to note {ev['parent']}, which was deleted; kept as a note")
            return [ev]
        if op == "delete-note" and ev["note"] not in self.notes:
            return []                   # deleted twice
        if op == "delete-entity" and ev["entity"] not in self.entities:
            return []
        if op == "edit-entity" and ev.get("remove_aliases") and ev["entity"] in self.entities:
            have = self.entities[ev["entity"]]["alias_by"]
            gone = [a for a in ev["remove_aliases"] if a in have]
            if gone != ev["remove_aliases"]:
                ev = {**ev, "remove_aliases": gone}
        if op == "define-entity":
            same = self.entity_named(ev["name"], ev.get("kind"))
            if same:
                # Two installations added the same entity. They become one: the
                # later's ID names the earlier, and it adds what the earlier lacks.
                self.entity_alias[ev["entity"]] = same["entity"]
                self._conflict(ev, "entity-merged", f"{ev['name']!r} was also added as {same['entity']} by "
                                                    f"{same['by']}; the two are now one entity")
                edit = {"id": ev["id"], "at": ev["at"], "author": ev["author"], "op": "edit-entity",
                        "entity": same["entity"], "add_aliases": [ev["name"], *ev.get("aliases", [])],
                        **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {})}
                for f in ("summary", "recap", "see", "date"):
                    if ev.get(f) and not same.get(f):
                        edit[f] = ev[f]
                return [edit]
        return [ev]

    def apply(self, ev: dict):
        op = ev["op"]
        self.last_id = ev["id"]
        if op == "define-tag":
            self.definitions[ev["tag"]] = {k: ev[k] for k in ("tag", "color", "group", "description") if k in ev} \
                | {"by": ev["author"], "at": ev["at"]}
        elif op == "edit-tag":
            d = self.definitions.setdefault(ev["tag"], {"tag": ev["tag"], "by": ev["author"]})
            d.update({k: ev[k] for k in ("color", "group", "description") if k in ev})
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
        elif op == "reserve-numbers":
            self.reservations[ev["id"]] = {"reservation": ev["id"], "series": ev["series"], "first": ev["first"],
                                           "last": ev["last"], "name": ev.get("name"), "by": ev["author"],
                                           "at": ev["at"], "origin": ev.get("origin")}
        elif op == "release-numbers":
            r = self.reservations.get(ev["reservation"])
            if r:
                r["released"] = ev["at"]
        elif op == "use-numbers":
            r = self.reservations.get(ev["reservation"])
            if r:
                r["used"] = ev["at"]

    def ranges(self, series: str) -> list[dict]:
        """The reservations in a series that stand (not released)."""
        return sorted((r for r in self.reservations.values() if r["series"] == series and not r.get("released")),
                      key=lambda r: r["first"])

    def next_number(self, series: str) -> int:
        return max((r["last"] for r in self.ranges(series)), default=0) + 1

    def entity_named(self, name: str, kind: str | None) -> dict | None:
        from .entities import fold_text
        f = " ".join(fold_text(name).split())
        for e in self.entities.values():
            if " ".join(fold_text(e["name"]).split()) == f and (e["kind"] == (kind or "other")):
                return e
        return None

    # -- the rules --------------------------------------------------------------------

    def check(self, ev: dict, replaying=False):
        """Refuse a write the rules don't allow. Claude can't change or remove
        what the user made; the user can change anything.

        ``replaying``: the event is already in a log (see :meth:`replay`). A
        privilege call by Claude is then kept, as a flag: productions act only
        on a person's (produce.py), and the replay reports it."""
        op, author = ev["op"], ev["author"]
        if op == "define-tag":
            if ev["tag"] in self.definitions:
                raise Refused(f"tag {ev['tag']!r} is already defined; use edit-tag")
        elif op == "edit-tag":
            d = self.definitions.get(ev["tag"])
            if d is None:
                raise Refused(f"no tag {ev['tag']!r}")
            if author == CLAUDE and is_user(d["by"]):
                raise Refused(f"tag {ev['tag']!r} was defined by {d['by']}; Claude can't edit it")
        elif op in ("tag", "untag"):
            if op == "tag" and author == CLAUDE and is_privilege_call(ev["tag"]) and not replaying:
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
            if ev["parent"] not in self.notes and not (replaying and ev.get("target")):
                raise Refused(f"no note {ev['parent']}")
        elif op in ("edit-note", "delete-note"):
            n = self.notes.get(ev["note"])
            if n is None:
                raise Refused(f"no note {ev['note']}")
            if author == CLAUDE and is_user(n["by"]):
                raise Refused(f"note {ev['note']} was written by {n['by']}; Claude can't change it")
        if op == "reserve-numbers":
            f, l = ev.get("first"), ev.get("last")
            if not isinstance(ev.get("series"), str) or not ev["series"].startswith(("bates:", "exhibit:")):
                raise Refused("a series is bates:<prefix> or exhibit:<prefix>")
            if not (isinstance(f, int) and isinstance(l, int) and 1 <= f <= l):
                raise Refused(f"not a range of numbers: {f!r} to {l!r}")
            if l - f >= MAX_RESERVATION:
                raise Refused(f"at most {MAX_RESERVATION:,} numbers at once")
            if f > self.next_number(ev["series"]) + MAX_RESERVATION:
                raise Refused(f"{f} is too far past the next free number ({self.next_number(ev['series'])})")
            clash = next((r for r in self.ranges(ev["series"]) if r["first"] <= l and f <= r["last"]), None)
            if clash:
                raise Refused(f"{ev['series'].split(':', 1)[1]} {clash['first']}–{clash['last']} "
                              f"{'is' if clash['first'] == clash['last'] else 'are'} reserved already"
                              + (f" (for {clash['name']}, by {clash['by']})" if clash.get("name") else f" (by {clash['by']})")
                              + f"; the next free number is {self.next_number(ev['series'])}")
        elif op in ("release-numbers", "use-numbers"):
            r = self.reservations.get(ev.get("reservation"))
            if r is None or r.get("released"):
                raise Refused(f"no reservation {ev.get('reservation')}")
            # only whoever reserved them, from where they reserved them
            if author != r["by"] or (ev.get("origin") and r.get("origin") and ev["origin"] != r["origin"]):
                raise Refused(f"that reservation is {r['by']}'s, made on another installation or by someone else")
            if op == "release-numbers" and r.get("used"):
                raise Refused("those numbers are on documents already; they can't be given back")
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
    "reserve-numbers": (("series", "first", "last"), ("name",)),
    "release-numbers": (("reservation",), ()),
    "use-numbers": (("reservation",), ()),
}


@contextmanager
def _locked(m: Matter):
    path = m.root / "annotations" / ".lock"
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

    from . import pool as poolmod
    try:
        poolmod.claim(m, author)
    except poolmod.PoolError as e:
        raise Refused(str(e))
    with _locked(m):
        ann = Annotations.load(m)
        if ev.get("entity") in ann.entity_alias:     # merged with one added on another installation
            ev["entity"] = ann.entity_alias[ev["entity"]]
        pending = []
        if op == "tag":
            if ev["tag"] in ann.tags.get(target_key(ev["target"]), {}):
                return []    # already tagged: nothing to record
            for other in ann.group_conflicts({**ev}):
                pending.append({"op": "untag", "tag": other["tag"], "target": other["target"], "author": author,
                                **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {})})
        if op == "reply" and ev["parent"] in ann.notes:
            # so a reply keeps its place if another installation deletes the parent
            ev["target"] = ann.notes[ev["parent"]]["target"]
        key = ev.pop("hotkey", None) if op in ("define-tag", "edit-tag") else None
        if key is not None:
            key = str(key)
            taken = next((t for t, k in ann.hotkeys.items() if k == key and t != ev["tag"]), None)
            if taken:
                raise Refused(f"hotkey {key!r} is already in use (by {taken!r})")
            if op == "edit-tag" and ev["tag"] not in ann.definitions:
                raise Refused(f"no tag {ev['tag']!r}")
        if not (op == "edit-tag" and key is not None and not any(k in ev for k in ("color", "group", "description"))):
            pending.append(ev)      # (a new hotkey alone is this installation's business: no event)
        for p in pending:
            ann.check({**p, "at": now(), "origin": m.origin})     # writing makes the origin anyway
            ann.apply({**p, "id": "pending", "at": now()})
        written = [m.annotation_log.append(**p) for p in pending]
        if key is not None:
            save_hotkeys(m, {**ann.hotkeys, ev["tag"]: key})
        save_replay(m, ann)
        return written


def hotkeys_path(m: Matter):
    return m.root / "annotations" / "hotkeys.json"


def hotkeys(m: Matter) -> dict[str, str]:
    """This installation's tag hotkeys, ``{tag: key}``. Keyboard shortcuts
    are each installation's own, and aren't synced: they live in
    ``annotations/hotkeys.json``, beside the logs, which sync never copies.

    Before that, hotkeys were part of tag definitions. The first time, the
    ones in this installation's own events (``define-tag``/``edit-tag``,
    in the logs of its origin and of the matter it was migrated from) are
    taken as its own. Others' are not."""
    p = hotkeys_path(m)
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("keys", {})
    except FileNotFoundError:
        pass
    mine = {o for o in (m.config.get("legacy_origin"),
                        m.identity.origin if (m.sync_dir / "origin.json").exists() else None) if o}
    keys: dict[str, str] = {}
    for ev in m.annotation_log.read():
        if ev.get("origin") in mine and ev["op"] in ("define-tag", "edit-tag") and ev.get("hotkey") \
                and ev["hotkey"] not in keys.values():
            keys[ev["tag"]] = ev["hotkey"]
    try:
        save_hotkeys(m, keys)
    except OSError:
        pass                # read-only here (a session): the host writes it
    return keys


def save_hotkeys(m: Matter, keys: dict):
    p = hotkeys_path(m)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"v": 1, "keys": keys}, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(p)


def _pool(m: Matter):
    """The pool, if the matter has one (who writes for whom)."""
    if not m.config.get("founder"):
        return None
    from .pool import Pool
    return Pool.load(m)


def replay_path(m: Matter):
    return m.root / "annotations" / "replay.json"


def save_replay(m: Matter, ann: "Annotations | None" = None) -> dict:
    """Write ``annotations/replay.json``: the events that replay as something
    other than themselves (``replace``: event ID -> the events it stands
    for, none if it had no effect), and the conflicts. The viewer replays
    the logs with these substitutions, so the rules live in one place.

    Host writers keep it current: the annotation write path, and anything
    that brings in another installation's logs. Events written through the
    write path never change it (they're checked, and sort last), so it only
    changes when logs arrive. Written only when it differs."""
    if ann is None:
        ann = Annotations.load(m)
    data = {"v": 1, "replace": ann.replaced, "conflicts": ann.conflicts}
    path = replay_path(m)
    text = json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return data
    except FileNotFoundError:
        if not ann.replaced and not ann.conflicts:
            return data
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return data


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
