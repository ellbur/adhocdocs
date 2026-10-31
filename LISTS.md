# Document Lists

*Status: built. List files and the `list` CLI are in
[`lists.py`](src/adhocdocs/lists.py) (the format is in its docstring), and
`search --list` writes them. `view list` shows one in the viewer
([`view.py`](src/adhocdocs/view.py)).*

A **list** is an ordered set of documents or locators with columns. The
**list display** shows one in a viewer pane and is the usual way to browse
quickly.

- **No sort, filter or search controls** (Principle 1). The user asks
  Claude, and Claude makes a new list.
- **Pointing, browsing and tagging only** in the display.
- **Lists are plain files** (`*.list.jsonl`) in the workspace, versioned with
  git. A list given a name and kept is a **saved set**. Order is the file's
  order; sorting means writing a new list.

## Key sets

Asked for the documents about something, a user rarely wants all of them:
most repeat the others. A **key set** is the few documents that explain it,
with every other candidate accounted for. It's a list file with a
convention ([`sets.py`](src/adhocdocs/sets.py) has the details):

- **Items are representatives**, each with a `why` field: what it
  establishes.
- **Children are the documents each covers**, with `covered` (why they add
  nothing: `duplicate`, `same-text`, `same-message`,
  `quoted-in-later-email`, `near-duplicate`, or the judgment `restates`)
  and `covered_by`.
- **The header says what the set answers** (`question`) and where its
  candidates came from (`sources`, with each search's query and coverage).

`list condense` makes one from candidate lists, folding what can be found
mechanically. `list cover`, `list set` and `list uncover` record judgments,
and condensing again with new candidates keeps them.

- **Nothing is dropped.** A covered document stays in the file under its
  representative, so "these 4 of 23" can be checked, and the list display
  shows it indented under the row that covers it.
- **Not a tag.** Tags are judgments across the whole matter (hot,
  privileged, responsive). Whether a document is the best evidence of one
  point is about that point, and a tag per point would swamp the
  vocabulary.
- **Not a page.** A page explains in prose and quotes; a key set records
  what was considered and why the rest was set aside. A page can name the
  set it draws on.
- **Versions aren't folded by default.** The near-duplicate threshold (0.9)
  is high, because a memo and its revision may differ in exactly what
  matters.
- **Attachments aren't folded under their email.** The attachment is
  usually the evidence, and the email the wrapper; they are separate
  documents, and family is already a column (`family`).

**Points** make "enough" checkable. The header lists what the set must
establish (`list point pod.list.jsonl delivered "The pallet reached Dock
4"`), each representative names the points it supports (`list set …
points=delivered,signed`), and `list points` reports each point's
documents, the points none support, and the representatives that support
none. A covered document's points count for its representative, so
folding a document never leaves a point unsupported.

**Finding sets later.** `list ls` lists the workspace's list files, newest
first, with their titles, questions, sizes, how much each covers and which
points are unsupported. `page docs memo.md --list memo.list.jsonl` makes a
list of the documents a page cites, in the order it cites them, for when
the explanation came first.

## Columns

A column is either a list field (from the item's `fields`, e.g. `score`,
`snippet`, a reason Claude chose the item) or a document field looked up at
render time from the `fields` index: `id`, `custodian`, `name`, `kind`,
`date`, `from`, `to`, `cc`, `subject`, `pages`, `family`, `dupes`, plus the
live columns `tags` and `notes`.

## The list display

`view list <file>` renders the file to static HTML for a web pane
([VIEWER.md](VIEWER.md)), with a new linked preview pane unless given one
(`--preview`).

- **Linked preview pane.** Moving the selection (arrows, `j`/`k`) opens the
  row in a linked document pane at once; the viewer prefetches the next few.
  The sequence comes from the list file, not the HTML, so it spans pages.
- **Selection and tagging.** Click, shift- and ctrl-click select rows; `view
  state` reports them as locators. Tag hotkeys act on every selected row:
  look, press a tag key, press down.
- **Live columns** (`tags`, `notes`) are filled in by the viewer from the
  annotation log, so they update the moment the user tags something.
  Visited rows are dimmed from navigation history.
- **Large lists** are split into HTML pages of about 500 rows in one bundle.
  Selection, sequence and preview work across pages because they use the
  file.

`view state` reports a list pane's file path, position, length, selection
and visible rows, so Claude can work on exactly what the user sees ("make a
new list of the ones I've selected, sorted by recipient").

## Decided

- Lists as files in the workspace, versioned with git.
- A list pane linked to a preview pane as the standard browsing layout.
- Paged HTML bundles for large lists. If they're too slow, the pane can
  become native without changing the list format.
- Multi-selection in web panes, with tag hotkeys acting on every selected
  row.
- Key sets as a convention on list files, not a new format, so the list
  display, filtering and tagging work on them unchanged.
- Points are checked only for support, not for whether the support is
  good: that is the reader's judgment, made easy by each document's `why`.

## The timeline display

`view timeline <file>` shows the same list file by date
([`timeline.py`](src/adhocdocs/timeline.py)), in a pane that works like the
list display: a linked preview pane, selecting, tagging, and `j`/`k` or
`]`/`[` stepping through the items in date order.

- **Lanes:** horizontal bands, by the list's groups (so `analyze threads
  --list` gives a lane per thread), custodian, sender or kind.
- **Chronologies are lists too.** An item's `fields` can carry its own `date`
  (a day, or just `YYYY-MM` or `YYYY`, drawn dashed) and `event`, the label
  shown instead of the document's name. The locator points at the evidence
  for the entry, so a chronology is checked like any other quotation.
- **Breaks:** long empty stretches are cut out of the axis and shown as a
  hatched break labeled with their length, so documents years apart don't
  spread over a mile of screen. The automatic scale is the finest that fits
  without cutting the axis into many pieces.
- **No controls** here either: to zoom, filter or regroup, the user asks
  Claude, which renders it again (`--scale`, `--lanes`, a different list).

