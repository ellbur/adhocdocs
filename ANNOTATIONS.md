# Tags and Notes

*Status: designed. Only reading tags is built
([`annotations.py`](src/adhocdocs/annotations.py)), for `search --tag` and
`tag:`.*

**Tags** and **notes** record judgments about documents ("hot",
"privileged", "contradicts Jones's testimony"). Both the user and Claude
make them, and each records who made it.

## Editing in the GUI: an exception to Principle 1

Review means tagging many documents fast, often one key per document, and a
note is the user's own words. Relaying either through Claude would be too
slow and could change them. So the viewer lets the user create, edit and
delete tags and notes directly. Everything else stays programmable.

## What an annotation is

- **Target:** a document ID or a locator. Tags are **per copy**: tagging one
  custodian's copy doesn't tag the duplicates. Propagating to duplicates or
  a family is an explicit operation.
- **Tag definitions** form the matter's vocabulary: name, optional
  description (the review criterion), color, optional hotkey, optional group
  for mutually exclusive choices (responsive / not responsive).
- **Notes** are Markdown and can reply to other notes, which makes threads
  anchored in documents. **Claude's notes follow Principle 5**: quotations
  are literate references, checked like pages.

## Storage: an append-only log

`annotations/log.jsonl`, one JSON event per line, with `id`, `at`, `author`
and `op`: `tag`, `untag`, `note`, `edit-note`, `delete-note`, `reply`,
`define-tag` or `edit-tag`. The current state is a replay. That gives no
write conflicts between the viewer and Claude, full history, a change feed
("what did the user tag since I last looked?"), and plain data.

## Authorship doesn't depend on Claude

- Writes from the viewer are stamped `user:<id>` by the viewer.
- Writes from the container go through an API that stamps them `claude`.
  The container mounts the log **read-only**, so Claude can't write an
  event claiming to be the user's.
- When the user asks Claude to tag, the event is still `claude`'s, with
  `requested_by`.
- The API refuses Claude edits or deletions of the user's annotations; the
  user can change anything.

## In the viewer

Keyboard first: tag hotkeys (on the document, or on the selection); a
one-key type-ahead tag palette that can also define tags; a note key that
opens a plain text box on the selection or document; margin markers by tag
color, distinguishing the user's from Claude's; a palette command to apply a
tag to family or duplicates. These are native UI, not web content.

## CLI (to build)

`ann tags|notes|find|log` read the log directly (`find --tag hot --tag
-privileged`, `log --since … --author user`). `ann tag|untag|note|define-tag`
write through the API. All output JSON.

## Decided

- GUI editing of tags and notes, as an exception to Principle 1.
- Authorship stamped by the write path; the container is read-only.
- Claude can't edit or delete the user's annotations.
- Tags per copy, with explicit propagation.
- Claude's notes follow Principle 5.
