# Tags and Notes

*Status: built: the log and its rules
([`annotations.py`](src/adhocdocs/annotations.py) has the event format), the
annotation API ([`annapi.py`](src/adhocdocs/annapi.py)) and `ann` CLI for
Claude, and editing in the viewer for the user.*

**Tags** and **notes** record judgments about documents ("hot",
"privileged", "contradicts Jones's testimony"). Both the user and Claude
make them, and each records who made it.

## Editing in the GUI: an exception to Principle 1

Review means tagging many documents fast, often one key per document, and a
note is the user's own words. Relaying either through Claude would be too
slow and could change them. So the viewer lets the user create, edit and
delete tags and notes directly. Everything else stays programmable.

## What an annotation is

- **Target:** a document or a locator. Targets are checked when written
  (a broken locator is refused), and text ranges get their quote
  fingerprint from the store.
- **Tags are per copy.** Tagging one custodian's copy doesn't tag the
  duplicates. `ann tag --dupes` and `--family` apply a tag to them
  explicitly.
- **Tag definitions** form the matter's vocabulary: name, description (the
  review criterion), color, hotkey, and optional exclusive group. A tag must
  be defined before use, so a typo can't start a new tag, and hotkeys are
  unique. Applying a tag in a group replaces the group's other tag on that
  target, as recorded untag events.
- **Notes** are Markdown and can reply to other notes, making threads
  anchored in documents. **Claude's notes follow Principle 5**: quotations
  are references (`:q{#id}` plus a `refs` block, as in [PAGES.md](PAGES.md)).
  Every note Claude writes is checked by
  [`literate.py`](src/adhocdocs/literate.py), the same checker pages use. The
  check reports broken or missing references and quotations typed by hand,
  as warnings.
- **For filters** (`search tag:hot`, `--tag`, `ann find`), a document has a
  tag if it, or any range in it, does.

## Storage

`annotations/log.jsonl` is an append-only event log, and the current state
is a replay. That gives no write conflicts between the viewer and Claude,
full history ("who tagged this privileged, and when?"), a change feed (`ann
log --since <id> --author user`: "what did the user tag since I last
looked?"), and plain data. Writers hold `annotations/.lock` while they
validate and append, so the viewer must too.

## Authorship doesn't depend on Claude

- The viewer writes as `user:<id>`, through a host-only command (`adhoc
  annotate-as-user`) that applies the same rules.
- The container writes only through the **annotation API**: a server the
  launcher runs on the host (`adhoc ann-api`), on a Unix socket mounted into
  the container. It stamps every write `claude`, whatever the request says.
  The log is mounted read-only, so this is the only path.
- When the user asked for a change, Claude passes `--requested-by user`. The
  event is still Claude's; `requested_by` is context, not authority.
- **Claude can't change the user's work:** it can't untag the user's tags
  (including by a group replacement), edit or delete the user's notes, or
  edit the user's tag definitions. The user can change anything. These
  rules are enforced in the write path, not by the CLI.

Until the container exists, Claude runs on the host and could append to the
log directly. The guarantee only holds once the log is mounted read-only.

## In the viewer

Keyboard first: tag hotkeys (on the selection, else the document, or on the
selected rows of a list); `t` opens a type-ahead tag palette that can also
define a tag; `N` opens a note on the selection or document. A document pane
shows its tags as chips and lists range tags and note threads beside it,
with Claude's marked differently from the user's. These are the viewer's
own UI, not web content.

In the palette, Shift+Enter tags the document's whole family and Ctrl+Enter
tags it and its exact duplicates (`ann tag --family/--dupes` from the CLI).

## Decided

- GUI editing of tags and notes, as an exception to Principle 1.
- Authorship stamped by the write path; the container is read-only.
- Claude can't edit or delete the user's annotations.
- Tags per copy, with explicit propagation.
- Claude's notes follow Principle 5, checked and warned, not refused.
- Tags must be defined before use.
