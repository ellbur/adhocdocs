# Document Lists

*Status: draft.*

A **list** is an ordered set of documents (or locators) with columns of
information about each. The **list display** shows one in a viewer pane, and
is the usual way to browse a set of documents quickly.

Under the design principles:

- **No sort, filter or search controls** (Principle 1). To get a different
  list, the user asks Claude, and Claude makes it with the list tools.
- **Pointing, browsing and tagging only.** The user can select rows, step
  through them, open them, and tag them (tagging being the exception for
  annotations; see [ANNOTATIONS.md](ANNOTATIONS.md)).
- **Lists are plain files** that Claude makes, transforms and reads with
  code (Principles 3 and 4).

## List files

A list is a JSON Lines file, by convention named `*.list.jsonl`. The first
line is a header, and each following line is an item.

```json
{"type": "list", "v": 1, "title": "Smith emails, March 2026", "columns": ["date", "from", "to", "subject", "tags"], "made_by": "claude", "query": "custodian:smith-j date:2026-03 kind:email"}
{"loc": "doc:sha256:3f9a…", "group": "Week of Mar 2"}
{"loc": "doc:sha256:c802…", "group": "Week of Mar 2", "fields": {"why": "mentions the pallet"}}
{"loc": "doc:sha256:a1b2…", "group": "Week of Mar 9", "children": [{"loc": "doc:sha256:77e0…"}]}
```

Header fields:

- `title`: shown at the top of the pane.
- `columns`: which columns to show, in order (see [Columns](#columns)).
- `query` and `made_by`: optional notes on how the list was made, kept for
  the record and shown in the pane's header.

Item fields:

- `loc`: a locator. Usually a whole document, but it can be a range, for
  example "every passage where X is mentioned".
- `group`: optional section heading. Groups appear in the order they first
  occur. Items in a group stay in file order.
- `fields`: optional values specific to this list, shown in columns of the
  same name. Examples are a reason Claude chose the item, a score, or a
  privilege-log description.
- `children`: optional nested items, shown indented under their parent. This
  is mainly for families, so attachments appear under their email.

**Order is the file's order.** Sorting means making a new list (or
rewriting this one) in a different order.

Lists are work product, like pages. They live in the workspace and are
versioned with git. A list given a name and kept is what [TOOLS.md](TOOLS.md)
calls a **saved set**.

## Columns

A column is either:

- a **document field**, looked up when the list is rendered, or
- a **list field**, taken from the item's `fields`.

Document fields come from the document object, from extracted metadata, and
from annotations:

| Column | Source |
|---|---|
| `id` | short document ID |
| `custodian`, `source` | the document object |
| `name` | file name from the source path |
| `kind` | email, pdf, docx, spreadsheet, image… |
| `date` | the document's primary date (sent date for email, otherwise the best available) |
| `from`, `to`, `cc`, `subject` | email headers |
| `pages` | page count, for paginated documents |
| `bates` | Bates range, if produced |
| `family` | the parent's short name, or the attachment count |
| `dupes` | the number of exact duplicates |
| `tags` | the current tags (live; see below) |
| `notes` | the number of notes (live) |
| `snippet` | text at the item's locator (filled in by the viewer, and only meaningful when `loc` is a range) |

Which fields exist depends on metadata extraction, which is still to be
designed. The table is the starting set that list rendering expects.

## Making and changing lists

The `list` CLI works on list files. Every command reads and writes plain
JSON Lines, so Claude can use the commands, or write code that does the same
thing directly.

```
list new <out> --title <t> [--columns ...] < locators   make a list from locators, one per line
list sort <in> <out> --by date [--desc]                  re-order by a document field
list group <in> <out> --by from|custodian|week|...       set groups from a field
list filter <in> <out> --tag hot --tag -privileged       keep items matching tag criteria
list families <in> <out>                                 nest attachments under their parents
list cat <in>...                                         print items as JSON Lines
```

Lists are usually made from search results, tags or other lists. The search
tools will produce list files directly.

## The list display

```
view list <file> [--pane <id>|--new-pane] [--preview <doc-pane-id>|--preview-new]
```

`view list` renders the list file to static HTML in the container and sends
it to the viewer as a web pane ([VIEWER.md](VIEWER.md)). The pane has no
content scripts. Everything interactive is either declarative markup handled
by the viewer's native code, or a keyboard binding.

### The preview pane

A list pane is usually **linked to a document pane**, which shows the
selected row.

- Moving the selection, with the up/down keys or `j`/`k`, opens the selected
  document in the preview pane immediately. This is the main way to browse
  quickly.
- The viewer prefetches the next few documents in list order (see
  [Sequences](VIEWER.md#sequences)).
- Next- and previous-document keys in the preview pane move the list
  selection too, so the two stay in step.
- Enter, or a double-click, opens the selected row in a new document pane
  when the user wants to keep it visible.

The sequence comes from the **list file**, not from the rendered HTML, so it
covers the whole list even when the display is split into pages (below).

### Selection and marks

- **Clicking** selects a row. **Shift- or Ctrl-clicking** extends the
  selection. `view state` reports the pane's selection as a list of
  locators.
- The **mark** key adds the selected rows to marks.
- **Tag hotkeys** and the tag palette act on all selected rows. Combined
  with the preview pane, this gives the usual review loop: look, press a tag
  key, press down.

### What's shown

- The title, and the query or note on how the list was made, if present.
- Group headings, with item counts.
- Rows with the chosen columns. Children are indented under their parent.
- **Live columns:** `tags` and `notes` are filled in by the viewer, not baked
  into the HTML. They update immediately when the user tags something. Rows
  are marked using the `data-tags` and `data-notes` attributes (see below).
- **Visited rows** (ones the user has opened in this session) are dimmed.
  The viewer does this from its navigation history, without anything in the
  HTML.
- The position, such as "row 37 of 412", and the number of rows selected.

### Large lists

The rendered HTML is split into pages of, say, 500 rows. The pages are files
in one bundle, linked with ordinary relative links, so no service is needed.
The selection, the sequence and the preview pane work across page
boundaries, because they use the list file. Moving down from the last row of
a page goes to the next page.

## Additions to the viewer

The list display needs a few things from the viewer, which apply to every
web pane:

- **Bundles:** `view pane` takes a directory as well as a single file.
  Relative links between files in the bundle navigate within the pane.
- **Multi-selection:** elements with `data-locator` can be selected as a
  group, and a pane's `selection` can be a list of locators.
- **Live annotation attributes:** `data-tags="<locator>"` and
  `data-notes="<locator>"` are filled in, and kept up to date, by the viewer
  from the annotation log.
- **Linked panes:** a web pane can be linked to a document pane, which then
  follows its selection.
- **Sequences from a file:** a linked pane can take its sequence from a list
  file, so it isn't limited to the rows in the current HTML page.

## State

A list pane's entry in `view state`:

```json
{
  "id": "p3",
  "kind": "list",
  "file": "lists/smith-march.list.jsonl",
  "title": "Smith emails, March 2026",
  "position": 37,
  "length": 412,
  "selection": [{"doc": "sha256:3f9a…"}, {"doc": "sha256:c802…"}],
  "preview": "p1",
  "visible": [30, 58]
}
```

`file` is the list's path in the container, so Claude can work with exactly
what the user is looking at. For example: "make a new list of the ones
I've selected, sorted by recipient".

## Decisions to confirm

- **Lists as files in the workspace,** versioned with git, rather than in
  the object store or the annotation log.
- **A list pane linked to a preview pane** as the standard browsing layout.
- **Paged HTML bundles** for large lists, rather than a native list widget.
  If web rendering turns out too slow for very large lists, the list pane
  could become native without changing the list format.
- **Multi-selection** in web panes, with tag hotkeys acting on every
  selected row.
