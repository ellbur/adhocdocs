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
``confirm-carried`` ``tag`` and ``target``, or ``note``: a carried-over tag or
                   note has been looked at and stands; its mark goes
``define-entity``  ``entity`` (new ID), ``name``, and optional ``kind``,
                   ``aliases``, ``summary``, ``recap``, ``see`` (targets),
                   ``date`` (an event's: ``YYYY-MM-DD``, ``YYYY-MM`` or ``YYYY``)
``edit-entity``    ``entity`` and the fields that change; ``add_aliases``,
                   ``remove_aliases``
``delete-entity``  ``entity``; links to and from it go with it
``define-link``    ``link`` (new ID), ``from`` and ``to`` (entity IDs), ``rel`` (what
                   ``from`` is or did to ``to``: "works for", "signed", "paid"),
                   optional ``summary``, ``see`` (the evidence), ``date``
``edit-link``      ``link`` and the fields that change (``rel``, ``summary``,
                   ``see``, ``date``)
``delete-link``    ``link``
``define-facet``   ``facet`` (a name: an issue, a question, a line of work), optional
                   ``description``, ``color``
``edit-facet``     ``facet`` and the fields that change
``file``/``unfile`` ``facet``, ``item``: one of ``{"entity": ID}``, ``{"link": ID}``,
                   ``{"note": ID}``, ``{"bundle": ID}`` or ``{"tag": NAME}`` (a tag
                   definition, so every application of it)
``define-bundle``  ``bundle`` (new ID), ``name``, ``parts`` (targets: documents or
                   ranges in them), optional ``summary``, ``from`` (the
                   metadocument it was made from, e.g. ``thread:<doc>``)
``edit-bundle``    ``bundle`` and what changes: ``name``, ``summary``,
                   ``add_parts``, ``remove_parts``
``delete-bundle``  ``bundle``; tags, notes and filings on it go with it
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

Links join two entities with a relation and its evidence, so the entity
list is a graph (GRAPH.md, :mod:`adhocdocs.graph`). They follow the
entities' rules, field by field. Facets are named issues; filing an entity,
link, note or tag under one lets the viewer and the tools show only what
bears on the issue at hand. Filings are like tags: Claude can't unfile what
the user filed, and filing never changes the thing filed, so Claude can
file the user's notes.

A bundle is a recorded metadocument (METADOCS.md, :mod:`adhocdocs.metadocs`):
documents, or parts of them, treated as one thing. A tag or note's
``target`` can be ``{"bundle": ID}``: on the bundle as a whole, not on
each part. For filters a document has the tags of the bundles it is in.
Privilege calls can't be on a bundle (a production withholds and redacts
documents): tag the parts. Claude can't edit or delete a bundle the user
made or edited, or delete one with the user's tags or notes on it.

Authorship comes from the write path, never from the request: the viewer
writes as ``user:<id>``, and the annotation API (:mod:`adhocdocs.annapi`),
the only write path from the container, writes as ``claude``. The rules in
:meth:`Annotations.check` keep Claude from changing the user's work.

A ``tag``, ``note`` or ``reply`` may carry ``carried``: it was copied from a
document that a replacement superseded (:func:`carry_over`). ``carried``
says from where (``from``, the old target), whose it was (``by``, ``at``,
``event`` or ``note``) and why (``reason``), plus ``unplaced`` for a note on
a passage the replacement doesn't have. The mark stays until someone
confirms it (``confirm-carried``) or edits the note.
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
    """Identity of a target: document, layer and selector (not the quote), or bundle."""
    return json.dumps({k: target[k] for k in ("doc", "layer", "select", "bundle") if k in target}, sort_keys=True)


def bundle_of(target) -> str | None:
    """The bundle a target is, if it is one."""
    return target.get("bundle") if isinstance(target, dict) and isinstance(target.get("bundle"), str) else None


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
        self.links: dict[str, dict] = {}         # link ID -> link between two entities
        self.link_alias: dict[str, str] = {}     # merged link ID -> the link it joined
        self.facets: dict[str, dict] = {}        # facet name -> definition
        self.filed: dict[str, dict] = {}         # facet -> item key -> filing
        self.bundles: dict[str, dict] = {}       # bundle ID -> a recorded metadocument
        self.bundle_alias: dict[str, str] = {}   # merged bundle ID -> the bundle it joined

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
                               **({k: ev[k] for k in ("tag", "note", "entity", "name", "link", "facet") if k in ev})})

    def _reconcile(self, ev: dict) -> list[dict]:
        """The events ``ev`` replays as, given the state so far: usually just
        ``ev``. Raises Refused for an event of no effect worth reporting."""
        op = ev["op"]
        if ev.get("entity") in self.entity_alias:
            ev = {**ev, "entity": self.entity_alias[ev["entity"]]}
        for k in ("from", "to"):
            if op == "define-link" and ev.get(k) in self.entity_alias:
                ev = {**ev, k: self.entity_alias[ev[k]]}
        if ev.get("link") in self.link_alias:
            ev = {**ev, "link": self.link_alias[ev["link"]]}
        if ev.get("bundle") in self.bundle_alias and op != "define-bundle":
            ev = {**ev, "bundle": self.bundle_alias[ev["bundle"]]}
        if bundle_of(ev.get("target")) in self.bundle_alias:
            ev = {**ev, "target": {"bundle": self.bundle_alias[ev["target"]["bundle"]]}}
        if op in ("file", "unfile") and isinstance(ev.get("item"), dict):
            item = ev["item"]
            alias = {"entity": self.entity_alias, "link": self.link_alias, "bundle": self.bundle_alias}
            for k, table in alias.items():
                if item.get(k) in table:
                    ev = {**ev, "item": {k: table[item[k]]}}
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
        if op == "confirm-carried" and self.carried_item(ev) is None:
            return []                   # confirmed twice, or gone
        if op == "delete-entity" and ev["entity"] not in self.entities:
            return []
        if op == "edit-entity" and ev.get("remove_aliases") and ev["entity"] in self.entities:
            have = self.entities[ev["entity"]]["alias_by"]
            gone = [a for a in ev["remove_aliases"] if a in have]
            if gone != ev["remove_aliases"]:
                ev = {**ev, "remove_aliases": gone}
        if op == "delete-link" and ev["link"] not in self.links:
            return []
        if op == "delete-bundle" and ev["bundle"] not in self.bundles:
            return []
        if op == "define-bundle":
            same = self.bundle_named(ev.get("name", ""))
            if same:
                # the same name, on two installations: one bundle, with both's parts
                self.bundle_alias[ev["bundle"]] = same["bundle"]
                self._conflict(ev, "bundle-merged", f"bundle {ev['name']!r} was also made as {same['bundle']} by "
                                                    f"{same['by']}; the two are now one, with the parts of both")
                have = {target_key(p) for p in same["parts"]}
                more = [p for p in ev.get("parts", []) if target_key(p) not in have]
                edit = {"id": ev["id"], "at": ev["at"], "author": ev["author"], "op": "edit-bundle",
                        "bundle": same["bundle"], **({"add_parts": more} if more else {}),
                        **({"summary": ev["summary"]} if ev.get("summary") and not same.get("summary") else {}),
                        **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {})}
                return [edit] if len(edit) > 5 + bool(ev.get("requested_by")) else []
        if op == "define-facet":
            d = self.facets.get(ev["facet"])
            if d is not None:
                if all(ev.get(k) == d.get(k) for k in ("description", "color")):
                    return []
                self._conflict(ev, "defined-twice", f"facet {ev['facet']!r} was already defined by {d['by']}; "
                                                    "this definition wasn't used")
                return []
        if op == "file" and item_key(ev.get("item")) in self.filed.get(ev.get("facet"), {}):
            return []                   # filed twice
        if op == "unfile" and item_key(ev.get("item")) not in self.filed.get(ev.get("facet"), {}):
            return []                   # unfiled twice, or the item is gone
        if op == "define-link":
            same = self.link_between(ev.get("from"), ev.get("rel", ""), ev.get("to"))
            if same:
                # the same link, made on two installations: they become one
                self.link_alias[ev["link"]] = same["link"]
                self._conflict(ev, "link-merged", f"{ev['rel']!r} was also added as {same['link']} by "
                                                  f"{same['by']}; the two are now one link")
                edit = {"id": ev["id"], "at": ev["at"], "author": ev["author"], "op": "edit-link", "link": same["link"],
                        **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {})}
                added = {f: ev[f] for f in ("summary", "see", "date") if ev.get(f) and not same.get(f)}
                return [{**edit, **added}] if added else []
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
                **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {}),
                **({"carried": ev["carried"]} if ev.get("carried") else {})}
        elif op == "untag":
            self.tags.get(target_key(ev["target"]), {}).pop(ev["tag"], None)
        elif op in ("note", "reply"):
            parent = self.notes.get(ev.get("parent")) if op == "reply" else None
            self.notes[ev["note"]] = {
                "note": ev["note"], "target": parent["target"] if parent else ev["target"],
                "parent": ev.get("parent"), "body": ev["body"], "by": ev["author"], "at": ev["at"],
                **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {}),
                **({"carried": ev["carried"]} if ev.get("carried") else {})}
        elif op == "edit-note":
            n = self.notes.get(ev["note"])
            if n:
                n.update(body=ev["body"], edited_at=ev["at"], edited_by=ev["author"])
                n.pop("carried", None)          # edited: it says what its editor means now
        elif op == "confirm-carried":
            item = self.carried_item(ev)
            if item:
                item.pop("carried", None)
                item["confirmed_by"], item["confirmed_at"] = ev["author"], ev["at"]
        elif op == "delete-note":
            self.notes.pop(ev["note"], None)
            self._unfile_all(f"note:{ev['note']}")
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
            self._unfile_all(f"entity:{ev['entity']}")
            for lid in [lid for lid, ln in self.links.items() if ev["entity"] in (ln["from"], ln["to"])]:
                self.links.pop(lid)
                self._unfile_all(f"link:{lid}")
        elif op == "define-link":
            ln = {"link": ev["link"], "from": ev["from"], "rel": " ".join(ev["rel"].split()), "to": ev["to"],
                  "summary": None, "see": [], "date": None, "by": ev["author"], "at": ev["at"], "set_by": {}, "set_at": {},
                  **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {})}
            for f in LINK_FIELDS:
                if f in ev:
                    if f != "rel":
                        ln[f] = ev[f]
                    ln["set_by"][f], ln["set_at"][f] = ev["author"], ev["at"]
            self.links[ev["link"]] = ln
        elif op == "edit-link":
            ln = self.links.get(ev["link"])
            if ln:
                for f in LINK_FIELDS:
                    if f in ev:
                        ln[f] = " ".join(ev[f].split()) if f == "rel" else ev[f]
                        ln["set_by"][f], ln["set_at"][f] = ev["author"], ev["at"]
                ln["edited_at"], ln["edited_by"] = ev["at"], ev["author"]
        elif op == "delete-link":
            self.links.pop(ev["link"], None)
            self._unfile_all(f"link:{ev['link']}")
        elif op == "define-bundle":
            self.bundles[ev["bundle"]] = {
                "bundle": ev["bundle"], "name": " ".join(ev["name"].split()), "parts": list(ev["parts"]),
                "summary": ev.get("summary"), "from": ev.get("from"), "by": ev["author"], "at": ev["at"],
                **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {})}
        elif op == "edit-bundle":
            b = self.bundles.get(ev["bundle"])
            if b:
                if "name" in ev:
                    b["name"] = " ".join(ev["name"].split())
                if "summary" in ev:
                    b["summary"] = ev["summary"]
                gone = {target_key(p) for p in ev.get("remove_parts", [])}
                b["parts"] = [p for p in b["parts"] if target_key(p) not in gone]
                have = {target_key(p) for p in b["parts"]}
                b["parts"] += [p for p in ev.get("add_parts", []) if target_key(p) not in have]
                b["edited_at"], b["edited_by"] = ev["at"], ev["author"]
        elif op == "delete-bundle":
            self.bundles.pop(ev["bundle"], None)
            self._unfile_all(f"bundle:{ev['bundle']}")
            self.tags.pop(target_key({"bundle": ev["bundle"]}), None)
            for nid in [nid for nid, n in self.notes.items() if bundle_of(n["target"]) == ev["bundle"]]:
                self.notes.pop(nid)
                self._unfile_all(f"note:{nid}")
        elif op == "define-facet":
            self.facets[ev["facet"]] = {k: ev[k] for k in ("facet", "description", "color") if k in ev} \
                | {"by": ev["author"], "at": ev["at"]}
        elif op == "edit-facet":
            d = self.facets.setdefault(ev["facet"], {"facet": ev["facet"], "by": ev["author"]})
            d.update({k: ev[k] for k in ("description", "color") if k in ev})
        elif op == "file":
            self.filed.setdefault(ev["facet"], {})[item_key(ev["item"])] = {
                "facet": ev["facet"], "item": ev["item"], "by": ev["author"], "at": ev["at"],
                **({"requested_by": ev["requested_by"]} if ev.get("requested_by") else {})}
        elif op == "unfile":
            self.filed.get(ev["facet"], {}).pop(item_key(ev["item"]), None)
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

    def link_between(self, frm, rel: str, to) -> dict | None:
        """The link from ``frm`` to ``to`` with this relation (folded), if there is one."""
        from .entities import fold_text
        f = " ".join(fold_text(rel or "").split())
        for ln in self.links.values():
            if ln["from"] == frm and ln["to"] == to and " ".join(fold_text(ln["rel"]).split()) == f:
                return ln
        return None

    def bundle_named(self, name: str) -> dict | None:
        f = " ".join(str(name).casefold().split())
        return next((b for b in self.bundles.values() if " ".join(b["name"].casefold().split()) == f), None)

    def find_bundle(self, ref: str) -> str:
        """A bundle's ID from its ID, an ID prefix or its name."""
        ref = str(ref).strip()
        ref = self.bundle_alias.get(ref, ref)
        if ref in self.bundles:
            return ref
        hits = [b for b in self.bundles.values()
                if " ".join(b["name"].casefold().split()) == " ".join(ref.casefold().split())] or \
            [b for b in self.bundles.values() if len(ref) >= 4 and b["bundle"].startswith(ref)] or \
            [b for b in self.bundles.values() if ref and b["name"].casefold().startswith(ref.casefold())] or \
            [b for b in self.bundles.values() if len(ref) >= 3 and ref.casefold() in b["name"].casefold()]
        if len(hits) == 1:
            return hits[0]["bundle"]
        raise Refused(f"no bundle {ref!r}" if not hits else f"bundle {ref!r} could be {', '.join(b['name'] for b in hits)}")

    def bundles_of(self, doc: str) -> list[dict]:
        """The bundles that hold a document, or part of it."""
        return [b for b in self.bundles.values() if any(p.get("doc") == doc for p in b["parts"])]

    def _unfile_all(self, key: str):
        for per in self.filed.values():
            per.pop(key, None)

    def item_exists(self, item) -> bool:
        k = item_key(item)
        kind, _, ref = k.partition(":")
        return bool(ref) and ref in {"entity": self.entities, "link": self.links, "note": self.notes,
                                     "tag": self.definitions, "bundle": self.bundles}.get(kind, {})

    def facets_of(self, item) -> list[str]:
        """The facets an item is filed under. A reply is filed where its thread
        is, and a tag application where its tag is."""
        if isinstance(item, dict) and "note" in item:
            n, seen = self.notes.get(item["note"]), set()
            while n and n.get("parent") in self.notes and n["note"] not in seen:
                seen.add(n["note"])
                n = self.notes[n["parent"]]
            keys = {f"note:{item['note']}"} | ({f"note:{n['note']}"} if n else set())
        else:
            keys = {item_key(item)}
        return sorted(f for f, per in self.filed.items() if keys & set(per))

    def in_facet(self, facet: str | None, item) -> bool:
        return facet is None or facet in self.facets_of(item)

    # -- the rules --------------------------------------------------------------------

    def check(self, ev: dict, replaying=False):
        """Refuse a write the rules don't allow. Claude can't change or remove
        what the user made; the user can change anything.

        ``replaying``: the event is already in a log (see :meth:`replay`). A
        privilege call by Claude is then kept, as a flag: productions act only
        on a person's (produce.py), and the replay reports it."""
        op, author = ev["op"], ev["author"]
        if bundle_of(ev.get("target")) is not None:
            b = self.bundles.get(ev["target"]["bundle"])
            if b is None:
                raise Refused(f"no bundle {ev['target']['bundle']}")
            if op == "tag" and is_privilege_call(ev["tag"]):
                raise Refused(f"a production withholds and redacts documents, not bundles: apply {ev['tag']!r} "
                              f"to the parts of {b['name']!r} (ann tag … bundle:{b['bundle']} --parts)")
        if op in ("define-bundle", "edit-bundle", "delete-bundle"):
            self._check_bundle(ev, author)
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
        elif op == "confirm-carried":
            item = self.carried_item(ev)
            if item is None:
                raise Refused("nothing carried over to confirm there" if ev.get("note") or ev.get("tag")
                              else "confirm-carried needs a note, or a tag and target")
            if author == CLAUDE and (is_user(item["by"]) or is_user(item["carried"].get("by", ""))):
                raise Refused("that was the user's: Claude can't confirm it for them")
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
        if op in ("define-link", "edit-link"):
            self._check_link(ev, author)
        elif op == "delete-link":
            ln = self.links.get(ev["link"])
            if ln is None:
                raise Refused(f"no link {ev['link']}")
            if author == CLAUDE and (is_user(ln["by"]) or any(is_user(b) for b in ln["set_by"].values())):
                raise Refused(f"link {ln['link']} has the user's work in it; Claude can't delete it")
        elif op == "define-facet":
            if not str(ev["facet"]).strip():
                raise Refused("a facet needs a name")
            if ev["facet"] in self.facets:
                raise Refused(f"facet {ev['facet']!r} is already defined; use edit-facet")
        elif op == "edit-facet":
            d = self.facets.get(ev["facet"])
            if d is None:
                raise Refused(f"no facet {ev['facet']!r}")
            if author == CLAUDE and is_user(d["by"]):
                raise Refused(f"facet {ev['facet']!r} was defined by {d['by']}; Claude can't edit it")
        elif op in ("file", "unfile"):
            if ev["facet"] not in self.facets:
                import difflib
                near = difflib.get_close_matches(ev["facet"].lower(), list(self.facets), n=3, cutoff=0.7)
                raise Refused(f"no facet {ev['facet']!r} is defined" + (f" (did you mean {', '.join(near)}?)" if near else "")
                              + "; define it first with define-facet")
            if item_key(ev.get("item")) == "?":
                raise Refused('an item is {"entity": ID}, {"link": ID}, {"note": ID}, {"bundle": ID} or {"tag": NAME}')
            if op == "file" and not self.item_exists(ev["item"]):
                raise Refused(f"no {item_key(ev['item']).replace(':', ' ', 1)}")
            if op == "unfile":
                f = self.filed.get(ev["facet"], {}).get(item_key(ev["item"]))
                if f is None:
                    raise Refused(f"that isn't filed under {ev['facet']!r}")
                if author == CLAUDE and is_user(f["by"]):
                    raise Refused(f"it was filed under {ev['facet']!r} by {f['by']}; Claude can't unfile it")
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
                if op == "delete-entity" and any(is_user(ln["by"]) or any(is_user(b) for b in ln["set_by"].values())
                                                 for ln in self.links.values() if e["entity"] in (ln["from"], ln["to"])):
                    raise Refused(f"entity {e['entity']} ({e['name']}) has the user's links; Claude can't delete it")
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

    def _check_bundle(self, ev: dict, author: str):
        op = ev["op"]
        if op == "define-bundle":
            if not str(ev.get("name", "")).strip():
                raise Refused("a bundle needs a name")
            same = self.bundle_named(ev["name"])
            if same:
                raise Refused(f"there is already a bundle {same['name']!r} ({same['bundle']}); "
                              "add to it with edit-bundle, or give this one another name")
            if not ev.get("parts"):
                raise Refused("a bundle needs parts: documents, or ranges in them")
            return
        b = self.bundles.get(ev["bundle"])
        if b is None:
            raise Refused(f"no bundle {ev['bundle']}")
        if "name" in ev:
            if not str(ev["name"]).strip():
                raise Refused("a bundle needs a name")
            same = self.bundle_named(ev["name"])
            if same and same["bundle"] != b["bundle"]:
                raise Refused(f"there is already a bundle {same['name']!r}")
        users = is_user(b["by"]) or is_user(b.get("edited_by", ""))
        if author == CLAUDE and users:
            raise Refused(f"bundle {b['name']!r} is the user's ({b.get('edited_by') if is_user(b.get('edited_by', '')) else b['by']}); "
                          f"Claude can't {'delete' if op == 'delete-bundle' else 'change'} it")
        if op == "delete-bundle" and author == CLAUDE:
            key = target_key({"bundle": b["bundle"]})
            if any(is_user(a["by"]) for a in self.tags.get(key, {}).values()) or \
                    any(is_user(n["by"]) for n in self.notes.values() if bundle_of(n["target"]) == b["bundle"]):
                raise Refused(f"bundle {b['name']!r} has the user's tags or notes on it; Claude can't delete it")
        if op == "edit-bundle":
            have = {target_key(p) for p in b["parts"]}
            for p in ev.get("remove_parts", []):
                if target_key(p) not in have:
                    raise Refused(f"that isn't a part of {b['name']!r}: {locators.format(p)}")
            gone = {target_key(p) for p in ev.get("remove_parts", [])}
            left = (have - gone) | {target_key(p) for p in ev.get("add_parts", [])}
            if not left:
                raise Refused(f"that would leave {b['name']!r} with no parts; delete it instead")

    def _check_link(self, ev: dict, author: str):
        if ev["op"] == "define-link":
            for k in ("from", "to"):
                if ev.get(k) not in self.entities:
                    raise Refused(f"no entity {ev.get(k)!r} to link {k}")
            if ev["from"] == ev["to"]:
                raise Refused("a link joins two different entities")
            same = self.link_between(ev["from"], ev["rel"], ev["to"])
            if same:
                raise Refused(f"{self.entities[ev['from']]['name']} {ev['rel']} {self.entities[ev['to']]['name']} "
                              f"is already link {same['link']}; edit it with edit-link")
            ln = None
        else:
            ln = self.links.get(ev["link"])
            if ln is None:
                raise Refused(f"no link {ev['link']}")
        if "rel" in ev and not str(ev["rel"]).strip():
            raise Refused("a link needs a relation (rel): what one entity is or did to the other")
        if ev.get("date") is not None and not ENTITY_DATE.fullmatch(str(ev["date"])):
            raise Refused(f"a link's date is YYYY-MM-DD, YYYY-MM or YYYY, not {ev['date']!r}")
        if ln is not None and author == CLAUDE:
            for f in LINK_FIELDS:
                if f in ev and is_user(ln["set_by"].get(f, "")) and ev[f] != ln[f]:
                    raise Refused(f"the {f} of link {ln['link']} was set by {ln['set_by'][f]}; Claude can't change it")

    def group_conflicts(self, ev: dict) -> list[dict]:
        """Other tags of the same exclusive group on the target, to remove first."""
        group = self.definitions.get(ev["tag"], {}).get("group")
        if not group:
            return []
        on = self.tags.get(target_key(ev["target"]), {})
        return [a for t, a in on.items()
                if t != ev["tag"] and self.definitions.get(t, {}).get("group") == group]

    # -- queries ------------------------------------------------------------------------

    def carried_item(self, ev: dict) -> dict | None:
        """The carried-over tag application or note a confirm-carried names."""
        if ev.get("note"):
            item = self.notes.get(ev["note"])
        elif ev.get("tag") and ev.get("target"):
            item = self.tags.get(target_key(ev["target"]), {}).get(ev["tag"])
        else:
            return None
        return item if item and item.get("carried") else None

    def applications(self, parts=False) -> list[dict]:
        """Every tag application. With ``parts``, one on a bundle is one on
        each of its parts instead (with ``via``: the bundle), for code that
        acts on documents."""
        out = [a for per in self.tags.values() for a in per.values()]
        if not parts:
            return out
        flat = []
        for a in out:
            b = bundle_of(a["target"])
            if b is None:
                flat.append(a)
            elif b in self.bundles:
                flat += [{**a, "target": p, "via": {"bundle": b}} for p in self.bundles[b]["parts"]]
        return flat

    def doc_tags(self) -> dict[str, set[str]]:
        """Document ID -> tags on the document, anywhere in it, or on a
        bundle it (or part of it) is in."""
        out: dict[str, set[str]] = {}
        for a in self.applications():
            b = bundle_of(a["target"])
            if b is None:
                out.setdefault(a["target"]["doc"], set()).add(a["tag"])
            elif b in self.bundles:
                for p in self.bundles[b]["parts"]:
                    out.setdefault(p["doc"], set()).add(a["tag"])
        return out


ENTITY_FIELDS = ("name", "kind", "summary", "recap", "see", "date")
LINK_FIELDS = ("rel", "summary", "see", "date")
ITEM_KINDS = ("entity", "link", "note", "tag", "bundle")


def item_key(item) -> str:
    """``kind:ref`` for a filed item (``{"entity": ID}`` and so on), or ``?``."""
    if not isinstance(item, dict) or len(item) != 1:
        return "?"
    (k, v), = item.items()
    return f"{k}:{v}" if k in ITEM_KINDS and isinstance(v, str) and v else "?"


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
    ranges get their layer and quote fingerprint from the store. A bundle
    (``{"bundle": ID}`` or ``bundle:ID``) is checked when it's written."""
    if isinstance(loc, str) and loc.startswith("bundle:"):
        loc = {"bundle": loc[7:].strip()}
    if isinstance(loc, dict) and "bundle" in loc:
        if set(loc) != {"bundle"} or not isinstance(loc["bundle"], str) or not loc["bundle"]:
            raise Refused('a bundle target is {"bundle": ID} and nothing else')
        return {"bundle": loc["bundle"]}
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
    "confirm-carried": ((), ("tag", "target", "note")),
    "define-entity": (("name",), ("kind", "aliases", "summary", "recap", "see", "date")),
    "edit-entity": (("entity",), ("name", "kind", "summary", "recap", "see", "date", "add_aliases", "remove_aliases")),
    "delete-entity": (("entity",), ()),
    "define-link": (("from", "rel", "to"), ("summary", "see", "date")),
    "edit-link": (("link",), ("rel", "summary", "see", "date")),
    "delete-link": (("link",), ()),
    "define-facet": (("facet",), ("description", "color")),
    "edit-facet": (("facet",), ("description", "color")),
    "file": (("facet", "item"), ()),
    "unfile": (("facet", "item"), ()),
    "define-bundle": (("name", "parts"), ("summary", "from")),
    "edit-bundle": (("bundle",), ("name", "summary", "add_parts", "remove_parts")),
    "delete-bundle": (("bundle",), ()),
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
    for k in ("aliases", "add_aliases", "remove_aliases", "see", "parts"):
        if k in ev and not isinstance(ev[k], list):
            raise Refused(f"{k} must be a list")
    for k in ("aliases", "add_aliases", "remove_aliases"):
        if k in ev:
            ev[k] = [" ".join(str(a).split()) for a in ev[k] if str(a).strip()]
    if "see" in ev:
        ev["see"] = [normalize_target(m, t) for t in ev["see"]]
    for k in ("parts", "add_parts", "remove_parts"):
        if k in ev:
            if not isinstance(ev[k], list):
                raise Refused(f"{k} must be a list")
            ts = [normalize_target(m, t) for t in ev[k]]
            if any("bundle" in t for t in ts):
                raise Refused("a bundle's parts are documents or ranges in them, not other bundles")
            ev[k] = list({target_key(t): t for t in ts}.values())
    if op == "define-bundle":
        ev["bundle"] = "b_" + new_event_id()[3:]
        ev["name"] = " ".join(str(ev["name"]).split())
    if op == "define-entity":
        ev["entity"] = "e_" + new_event_id()[3:]
    if op in ("note", "reply"):
        ev["note"] = "n_" + new_event_id()[3:]
    if op == "define-link":
        ev["link"] = "l_" + new_event_id()[3:]
        ev["rel"] = " ".join(str(ev["rel"]).split())
    if op in ("define-facet", "edit-facet", "file", "unfile"):
        ev["facet"] = " ".join(str(ev["facet"]).split())
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
        for k in ("from", "to") if op == "define-link" else ():
            ev[k] = ann.entity_alias.get(ev[k], ev[k])
        if ev.get("link") in ann.link_alias:
            ev["link"] = ann.link_alias[ev["link"]]
        if op in ("edit-bundle", "delete-bundle") or bundle_of(ev.get("target")):
            # a bundle by ID, ID prefix or name
            ref = ev["bundle"] if op in ("edit-bundle", "delete-bundle") else ev["target"]["bundle"]
            bid = ann.find_bundle(ref)
            if op in ("edit-bundle", "delete-bundle"):
                ev["bundle"] = bid
            else:
                ev["target"] = {"bundle": bid}
        if op in ("file", "unfile") and isinstance(ev["item"], dict):
            if "bundle" in ev["item"]:
                ev["item"] = {"bundle": ann.find_bundle(ev["item"]["bundle"])}
            for k, table in (("entity", ann.entity_alias), ("link", ann.link_alias)):
                if ev["item"].get(k) in table:
                    ev["item"] = {k: table[ev["item"][k]]}
        if op == "file" and item_key(ev["item"]) in ann.filed.get(ev["facet"], {}):
            return []    # already filed there
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
    """Problems in an entity's or link's summary or recap by Claude: no typed
    quotations (point at the words with ``see`` instead)."""
    out = []
    for f in ("summary", "recap"):
        if req.get(f):
            out += [{**p, "field": f} for p in note_check(m, req[f])]
    return out


# -- carrying annotations over to a replacement ------------------------------------------------


def carry_over(m: Matter, pairs: list[tuple[str, str]], person: str, reason: str) -> dict:
    """Copy the tags and notes on each superseded document (``old``) to its
    replacement (``new``), each marked ``carried``: the replacement may
    differ (a redaction, a correction), so they are judgments about the old
    document until someone looks and confirms them.

    Called on the host, after the supersede events, by the person who made
    the replacement (``person``, ``user:<id>``). A person's tag or note is
    carried as theirs, the person doing it, since only an installation's
    own people write from it; ``carried.by`` keeps whose it was. Claude's
    are carried as Claude's, so a flag stays a flag. A range moves where
    its quote is found in the replacement (as references follow a
    supersede: LOCATORS.md). A tagged range that can't be found isn't
    carried; a note on one goes on the whole document, ``unplaced``.
    Returns ``{"carried": [...], "not_carried": [...]}``."""
    from . import pool as poolmod
    if not is_user(person):
        raise Refused(f"carrying over is a person's: {person!r} isn't user:<id>")
    try:
        poolmod.claim(m, person)
    except poolmod.PoolError as e:
        raise Refused(str(e))
    carried, skipped = [], []
    with _locked(m):
        ann = Annotations.load(m)
        written = []

        def emit(ev):
            ann.check({**ev, "at": now(), "origin": m.origin})
            ann.apply({**ev, "id": "pending", "at": now()})
            written.append(m.annotation_log.append(**ev))
            return written[-1]

        for old, new in pairs:
            def place(target):
                """The old target in the replacement, or None."""
                if not target.get("select"):
                    return {"doc": new}
                try:
                    t = normalize_target(m, {k: target[k] for k in ("doc", "layer", "select", "quote") if k in target})
                except (Refused, locators.LocatorError, KeyError, ValueError):
                    return None
                return t if t.get("doc") == new else None

            mark = lambda x, **more: {"from": x["target"], "by": x["by"], "at": x["at"], "reason": reason, **more}
            for app in sorted((a for a in ann.applications() if a["target"].get("doc") == old), key=lambda a: a["at"]):
                target = place(app["target"])
                if target is None:
                    skipped.append({"tag": app["tag"], "from": app["target"], "by": app["by"],
                                    "why": "the tagged passage isn't in the replacement"})
                    continue
                if app["tag"] in ann.tags.get(target_key(target), {}):
                    continue                            # already there
                author = CLAUDE if app["by"] == CLAUDE else person
                ev = {"op": "tag", "tag": app["tag"], "target": target, "author": author,
                      "carried": mark(app, event=app["event"])}
                others = ann.group_conflicts(ev)
                if others:
                    skipped.append({"tag": app["tag"], "from": app["target"], "by": app["by"],
                                    "why": f"the replacement has {others[0]['tag']!r}, from the same group"})
                    continue
                try:
                    ev = emit(ev)
                except Refused as e:
                    skipped.append({"tag": app["tag"], "from": app["target"], "by": app["by"], "why": str(e)})
                    continue
                carried.append({"tag": app["tag"], "target": target, "event": ev["id"]})
            # notes, parents before replies, so threads stay threads
            new_id = {}
            already = {n["carried"].get("note") for n in ann.notes.values() if n.get("carried")}
            for n in sorted((n for n in ann.notes.values() if (n["target"] or {}).get("doc") == old),
                            key=lambda n: (n["at"], n["note"])):
                if n["note"] in already:
                    continue
                target = place(n["target"])
                more = {"note": n["note"]}
                if target is None:
                    target, more["unplaced"] = {"doc": new}, True
                author = CLAUDE if n["by"] == CLAUDE else person
                ev = {"op": "note", "target": target, "body": n["body"], "author": author, "note": "n_" + new_event_id()[3:],
                      "carried": mark(n, **more)}
                if n.get("parent") in new_id:
                    ev.update(op="reply", parent=new_id[n["parent"]])
                try:
                    written_ev = emit(ev)
                except Refused as e:
                    skipped.append({"note": n["note"], "from": n["target"], "by": n["by"], "why": str(e)})
                    continue
                new_id[n["note"]] = written_ev["note"]
                carried.append({"note": written_ev["note"], "target": target, "from_note": n["note"],
                                **({"unplaced": True} if more.get("unplaced") else {})})
            # bundles: the replacement joins each bundle the old document is in
            for b in ann.bundles_of(old):
                adds = [t for t in (place(p) for p in b["parts"] if p.get("doc") == old) if t is not None]
                have = {target_key(p) for p in b["parts"]}
                adds = [t for t in {target_key(t): t for t in adds}.values() if target_key(t) not in have]
                if not adds:
                    continue
                users = is_user(b["by"]) or is_user(b.get("edited_by", ""))
                try:
                    emit({"op": "edit-bundle", "bundle": b["bundle"], "add_parts": adds,
                          "author": person if users else CLAUDE})
                except Refused as e:
                    skipped.append({"bundle": b["bundle"], "why": str(e)})
                    continue
                carried.append({"bundle": b["bundle"], "added": adds})
        save_replay(m, ann)
    return {"carried": carried, "not_carried": skipped}
