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

