# Tags and Notes

*Status: draft.*

**Tags** and **notes** record judgments about documents: "hot", "privileged",
"responsive to RFP 12", "this contradicts Jones's testimony". Both the user and
Claude make them, and every one records who made it.

## Editing in the GUI: an exception to Principle 1

Principle 1 would have the user dictate tags and notes to Claude. For this,
that is too limiting.

- Review means tagging many documents quickly, often with a single key per
  document. Going through the prompt for each one would be far too slow.
- A note is the user's own thinking, written in their own words. Having
  Claude relay it adds nothing, and risks changing it.

So the viewer lets the user **create, edit and delete tags and notes
directly**. This is a deliberate, limited exception, recorded in
[DESIGN.md](DESIGN.md). Everything else about annotations stays
programmable: Claude reads, writes and queries them from code (Principle 3).

## What an annotation is

### Tags

A tag is a named label applied to a target.

- **Target:** a document ID, or a locator for a range or region within one.
  Because document IDs are per copy ([LOCATORS.md](LOCATORS.md)), tagging
  one custodian's copy doesn't tag the others. Applying a tag to duplicates
  or to a whole family is a separate, explicit operation. Claude does it on
  request, or the user does it with a GUI command (below).
- **Tag definitions** make up the matter's tag vocabulary. Each has:
  - a name
  - an optional description, which states the review criterion
  - a color
  - an optional hotkey
  - an optional group, for mutually exclusive choices such as
    responsive / not responsive

  Both the user and Claude can define tags.

### Notes

A note is free text attached to a target, which is a document or a locator.

- **Body:** Markdown.
- **Notes by the user** can contain anything the user types.
- **Notes by Claude** follow Principle 5. Any quotation in them is a
  literate reference (`:q{#id}` with a `refs` block, as in
  [PAGES.md](PAGES.md)), not typed text. The note checker flags violations
  the same way the page checker does.
- **Replies:** a note can reply to another note. This makes discussion
  threads between the user and Claude, anchored to a place in a document.

## Storage: an append-only event log

Annotations are stored as an **append-only log of events**. The current set
of tags and notes is computed by replaying the log.

```json
{"id": "ev_01J…", "at": "2026-10-30T14:02:11Z", "author": "user:ellbur", "op": "tag", "tag": "hot", "target": {"doc": "sha256:3f9a…"}}
{"id": "ev_01J…", "at": "2026-10-30T14:02:40Z", "author": "claude", "op": "note", "note": "n_7c…", "target": {"doc": "sha256:3f9a…", "layer": "sha256:8b21…", "select": {"char": [1200, 1231]}, "quote": {…}}, "body": "…"}
{"id": "ev_01J…", "at": "2026-10-30T14:05:02Z", "author": "user:ellbur", "op": "edit-note", "note": "n_7c…", "body": "…"}
{"id": "ev_01J…", "at": "2026-10-30T14:06:13Z", "author": "user:ellbur", "op": "untag", "tag": "hot", "target": {"doc": "sha256:3f9a…"}}
```

Operations:

- `tag` and `untag`
- `note`, `edit-note` and `delete-note`
- `reply`
- `define-tag` and `edit-tag`

This design gives four things:

- **No conflicts.** The viewer and Claude can both write at the same time.
  Each just appends events.
- **Full history.** Nothing is overwritten. "Who tagged this privileged, and
  when?", and "what did the note say before it was edited?", can always be
  answered.
- **A change feed.** Claude can ask what changed since its last look, for
  example "what did the user tag since I last checked?". That covers the case
  where Claude can't react to GUI events as they happen.
- **Plain data.** Each event is a line of JSON, which is easy to process from
  code (Principles 3 and 4).

## Authorship doesn't depend on Claude

Whether an annotation was made by the user or by Claude matters. A tag
applied by a reviewer and a tag suggested by AI carry different weight. So
authorship is recorded by the mechanism, not reported by Claude, in the same
spirit as Principle 2:

- **Writes from the viewer** are stamped `user:<id>` by the viewer.
- **Writes from the container** go through the annotation API, which stamps
  them `claude`. The container has **read-only** access to the log, and can't
  append to it directly. So Claude can't write an event that claims to be the
  user's.
- **Requests from the user:** when the user asks Claude to tag something, the
  event is still authored by `claude`, with
  `"requested_by": "user:<id>"` to give the context.
- **Claude can't change the user's annotations.** The API refuses to let
  Claude edit or delete annotations authored by the user. The user can edit
  or delete anything, including Claude's.

The log lives with the matter's data on the host, next to the object store.
The connect mechanism makes it readable inside the container, and provides
the API endpoint for writes.

## Editing in the viewer

The editing controls stay minimal and keyboard-first:

- **Hotkeys** apply or remove the tag bound to them. On a document pane this
  acts on the whole document, and on a selection it acts on the selection.
  Combined with next-document, this makes rapid coding possible: press a key
  to tag, press a key to go on.
- **A tag palette** opens with a single key. It is a type-ahead list of the
  matter's defined tags, for tags without a hotkey. It can define a new tag
  too.
- **Note editor:** a key opens a note on the current selection, or on the
  document if nothing is selected. It is a plain text box, and saves on
  closing.
- **Margin markers** show tags and notes, with author and time. They are
  colored by tag and marked differently for the user's and Claude's.
  Clicking one opens it for editing or replying.
- **Apply to family or duplicates:** a palette command applies a tag to the
  document's family or to its exact duplicates.

These controls are native viewer UI, not web pane content, so the "no
scripts" rule ([VIEWER.md](VIEWER.md#no-scripts)) is unaffected.

## The API and CLI

Inside the container:

```
ann tags [--target <locator>]            list tags, by target or overall
ann notes [--target <locator>]           list notes
ann find --tag hot --tag -privileged     query by tags, with - for absent
ann log [--since <event-id|time>] [--author user|claude]
ann tag <tag> <locator>...               writes go through the API and are authored `claude`
ann untag <tag> <locator>...
ann note <locator> [--reply-to <note>] < body.md
ann define-tag <name> [--color] [--hotkey] [--group] [--description]
```

All output is JSON. Reads go directly to the log, which is mounted
read-only. Writes go through the API.

## Decisions to confirm

- **Editing tags and notes in the GUI** as an exception to Principle 1.
- **Authorship stamped by the write path**, with the container read-only, so
  Claude can't write as the user.
- **Claude can't edit or delete the user's annotations,** while the user can
  edit or delete Claude's.
- **Tags are per copy,** with propagation to duplicates or family done
  explicitly.
- **Claude's notes follow Principle 5,** so quotations in them are references.
