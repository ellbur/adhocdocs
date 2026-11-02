# Document Viewer

*Status: a first version is built: [`viewer/`](viewer) (Rust) and the `view`
CLI ([`view.py`](src/adhocdocs/view.py); `view --help` has usage and keys).
See [Not built yet](#not-built-yet).*

The viewer shows documents to the user. Its displays are for viewing,
navigating, pointing and simple input (Principle 1). Everything it shows or
the user points at can be read by Claude, which can drive it from code
(Principle 3). What it can reach is set outside Claude (Principle 2).

## Goals

1. **Fast:** first page visible within 100 ms of a keypress, for flipping
   through hundreds of documents.
2. **Many documents at once:** related panes side by side, the rest in tabs.
3. **Exact mapping to locators:** anything shown can be selected as a
   locator, and any locator can be shown and highlighted.
4. **Custom displays from Claude:** a grouped list or a checklist, for the
   task at hand.
5. **No content scripts:** no script from a document, or written by Claude,
   ever runs.

## How it is built

A native Rust program on the host, outside the container. Each of its
windows is one `wry` web view running the viewer's own code ([`shell/`](viewer/shell),
compiled into the binary). Everything it loads comes from an `adhoc:`
protocol served by the program itself, never from the network.

- **Paginated documents** (PDF, scans, images) are rendered by **MuPDF**, in
  Rust, and shown as page images. Over each page sits an invisible text layer
  built from the layer's geometry (not MuPDF's own text), so selecting text
  gives our offsets and highlights agree with the quote checker. Derived
  layers (`transcript`) use their source's geometry through the offset map,
  so page mode works in them too.
- **Everything else** (presentations, literate pages, lists, Claude's panes)
  is static HTML in iframes. It is sanitized on arrival (scripts, handlers,
  `javascript:` URLs, frames, objects and remote resources removed) and
  served with `script-src 'none'` and no network sources. The viewer's own
  code reaches into these frames to read selections and carry out their
  declarative attributes.

*Why not a native canvas beside web views*, as first sketched: mixing the two
is fragile across platforms. MuPDF still does the rendering, so it stays
native. *Why not sandboxed iframes:* WebKit then refuses to run the viewer's
own event listeners on the frame, so sanitizing plus the CSP header are the
two layers.

MuPDF is AGPL, which makes the viewer AGPL. That's fine while the project is
open source.

## Isolation

- **Scope comes from outside:** the viewer is started for one matter (`adhoc
  view`, part of connecting it) and refuses any document or layer that
  isn't live in it, from the API and from every pane.
- **A narrow API:** HTTP over a Unix socket that the connect mechanism mounts
  into the container (`ADHOC_VIEW_SOCKET`). It accepts locators and static
  HTML, and returns state. It can't open paths, start the viewer, or change
  the scope.
- **The user's tags and notes** are written by the viewer through a host-only
  command (`adhoc annotate-as-user`), stamped `user:<id>`
  ([ANNOTATIONS.md](ANNOTATIONS.md)).

## Panes

| Pane | Shows |
|---|---|
| `document` | a document in page, flow (its presentation), text, grid (spreadsheets) or hex mode (the original's bytes), with layer and mode switches, tag chips, a side panel of range tags and notes, and family and duplicate links |
| `list` | a list file ([LISTS.md](LISTS.md)), rendered by `view list` with a linked preview pane |
| `timeline` | a list file by date, in lanes, rendered by `view timeline`, linked the same way |
| `custom` | static HTML from Claude, a file or a bundle; literate pages (`page render`) are shown this way |
| `home` | the overview: what the matter holds and what state it's in (below) |

**The overview** is the landing page: the first window opens on it, the
⌂ Overview tab or `H` brings it back, and Claude shows it with `view home`.
It isn't where work starts (that is Claude's prompt); it shows what this is,
what can be done with it, and how far along it is:

- **Documents** in summary, since there may be thousands: counts by kind
  and custodian, the date range, attachments, documents with no text yet
  (which searches can't reach), and the latest additions, which open on a
  click.
- **Entities**: the entity list ([ENTITIES.md](ENTITIES.md)) by kind,
  and how many names the entities index found that aren't on it yet, the
  most mentioned first.
- **Email correspondents**, from the From, To and Cc lines.
- **Work product** in the workspace: pages (`*.md`, with their quotation
  counts), lists and key sets (`*.list.jsonl`), productions and exhibit
  sets.
- **Published** work: pages and lists published from any installation of
  the matter (`work/`, [SYNC.md](SYNC.md)), and whether any are forked.
- **Tags** with how often each is used and by whom, and the latest notes.

Each section that is empty says what it would hold and offers a request
to make of Claude ("Write a chronology of the key events, quoting the
documents"); filled sections offer follow-ups, and the top invites
questions about the documents in general. Clicking a suggestion copies it
for pasting into Claude's prompt: the viewer still starts nothing.
Documents can't be added by Claude, so an empty matter points to `adhoc
ingest` instead. The data comes from `/api/overview` (built in
[`overview.rs`](viewer/src/overview.rs)) and is read again every few
seconds while the overview is showing.

**Tabs and splits:** a window's panes are arranged in a tree of tabs and
splits. Columns are spent only on views that are read together; anything
else gets a tab, so width isn't used up as panes pile on:

- A new, unrelated view (a page, a list, a document Claude opens in a new
  pane) gets a **tab of its own** at the top of the window.
- A view opened **from** another (a reference clicked in a page, a document
  from the overview, a list's preview) goes **beside** it, in a split. That
  side is itself tabbed: the next reference opens a new tab there, and one
  already showing that document is reused. A view on that side opens its own
  references among its neighbors, not in a third column.
- Closing panes folds away what they leave: a split with one side left
  becomes that side, and its tabs join the tabs around it.

For example, after Claude shows a page, the user opens two of its
references, Claude shows a second page, and the user opens one of its
references:

```
tabs(split(page, tabs(doc, doc)), split(page, tabs(doc)))
```

A tab holding a split is named after its first pane, with a count of the
others. Each tab has a close button (×, closing all the panes under it),
and the divider of a split can be dragged. `view state` reports the tree
(`windows[].tree`: a pane ID, `{tabs, active}` or `{split, children,
sizes}`). The top-level tabs can also be tiled instead of shown one at a
time (`view layout split-v`, `split-h` or `grid`; `tabs` goes back).

**Windows:** there can be several, each with its own panes and layout, for
example a list on one screen and documents on another. The user moves the
focused pane to a new window with `W`. Claude can open a pane in a new
window (`--new-window`) or a given one (`--window`), and move or close
windows (`view window`).
- **Routing:** pane IDs are unique across windows (`p1` in the first,
  `w2-p1` in the second). A command naming a pane goes to its window, and
  other commands go to the window the user last used.
- **Links across windows:** a list can be linked to a document pane in
  another window. The host relays opening, stepping and following between
  them, and items opened in one window show as visited in the others.
- **One process:** each window runs its own copy of the shell, but all of
  them share the page renderer and its cache. `view state` merges every
  window. `view history` is kept by the host, so it survives a window
  closing.
- **Closing:** a window takes its panes with it, and closing the last
  window ends the viewer.

## Thin panes

Claude acts when prompted; it can't react to clicks. So panes, even ones
Claude builds, display things and collect simple input, and anything that
needs no thought (open, step, point) is handled by the viewer at once.

**Declarative attributes** in pane HTML, carried out by the viewer:

| Markup | Behavior |
|---|---|
| `<a href="loc:…">` | open the locator, in the linked document pane if there is one |
| `data-locator="…"` | pointable: click selects (shift/ctrl for several), double-click or Enter opens, tag keys apply; a linked document pane follows |
| `data-sequence` | its items become the linked pane's next/previous sequence (`view list` passes the whole list's sequence, across HTML pages) |
| `data-text="…"` | the text at the locator, inserted as text, never HTML (`data-normalize="inline\|block\|lines"`) |
| `data-snippet="…"` | a snippet around the locator, with it marked |
| `data-status="…"` | marked if the locator is broken |
| `data-tags="…"`, `data-notes="…"` | live tags and note counts |

**Input:** form controls' values are pane state in `view state`, so Claude
reads them at the next prompt ("log the ones I ticked"). A form with
`action="svc:NAME"` is answered at once by a service Claude runs in the
container (`view serve NAME -- cmd`). The service gets the form values and
the pane's selection as JSON and prints the pane's new HTML. It listens on
a socket in the directory the session shares with the host. The viewer
connects only to `svc-*.sock` there, and sanitizes the reply like any
pane.

**Spreadsheets** open in grid mode: one sheet at a time, with sheet tabs,
built from the layer text (sheets are pages, rows lines, cells
tab-separated), so it shows exactly what search and locators see: the
values Excel last calculated. Click, drag or Shift+arrows select a
rectangle, and row and column headers select whole rows and columns. A
selection is a `sheet=…&cells=B2:D10` locator whose quote is the cells'
text, so tags, notes and marks work on it as on any range. Search hits
(char ranges) light up the cells they fall in. Only the first 3,000 rows of
a sheet are drawn, unless a locator points further down; text mode and
search reach the rest.

## Highlights and stepping

`view highlight` marks a place in a document pane until it's cleared, with
a label from Claude saying what it is. A pane with highlights gets a bar
under its header:

```
Highlights  ‹ Prev  3 of 7  Next ›   §5 Delivery / risk of loss: …
```

- **Stepping:** the buttons, or `]` and `[` when the pane has no
  sequence, go through the highlights in the order they come in the
  document, not the order Claude made them. Before the first step the bar
  shows the count, and Next goes to the first.
- **The current one** is drawn darker, with an edge, and stays that way, so
  adjacent highlights are told apart. Clicking a highlight makes it the
  current one.
- **Labels** show in the bar for the current highlight, as a tooltip on
  hovering any highlight, and in the side panel, which lists every
  highlight by label; clicking one there goes to it.
- A pane with a **sequence** (`view sequence`, or a list's preview) shows
  Prev/Next buttons for it too. Then `]` and `[` step through the
  sequence, and the highlights are stepped with their own buttons.

The buttons have tooltips naming the keys, since the keys are easy to
miss. `view state` marks the highlight the user is on `"current": true`.

## The entity layer

Known entities' mentions are underlined and suggestions dotted, in page,
flow and text modes. Hovering one shows a card (summary, recap, links,
evidence), and clicking one selects it. `e` adds the selection to the
entity list or edits the entity, and `E` (or the header counter) shows all,
known only, or none. The viewer matches aliases itself
([`entities.js`](viewer/shell/entities.js)) so the list's changes show at
once, and reads suggestions from the `entities` index's files. See
[ENTITIES.md](ENTITIES.md).

## Dates

A strip under the header says when the document is dated and where that
came from (sent, attached, created, modified). Dates in the text have a
dashed underline. Hovering either one shows a date card: the document's
thread, how many documents are near the date, and the nearest events
before and after, from chronologies, dated entities, key sets, pages'
citations and hot tags. The card's data comes from `/api/around`
([`chrono.rs`](viewer/src/chrono.rs)). See [DATES.md](DATES.md).

## State

`view state` reports the layout, panes, and per pane: the document, layer
and view, mode, what's visible (pages or a char range), the sequence
position, the **selection as a JSON locator with its quote fingerprint**
(ready for a `refs` block, tag or note) and the `entity` or `date` it is, if any, marks, highlights, form values, and
a list pane's selected rows. `view history` lists where the user went and
whether the user, Claude or a pane went there.

## Not built yet

- A marker on literate-page quotes that opens the page image (shading
  low-confidence OCR words exists).
- The 100 ms target is met most of the time, not always. `view state`
  reports each document pane's `open_ms`: keypress to first page shown.
  Stepping through 15 corpus PDFs (release build, Xvfb with software
  rendering) gave a median of 51 ms and a worst case of 204 ms, with 4 of
  15 over 100 ms. The slow ones are the first document of a sequence and
  very long documents. Page images come before the text layer over them, a
  cache holds the last 64 rendered pages, and each document's first page is
  prefetched before the user moves to it. `ADHOC_VIEWER_DEBUG=1` logs each
  request's time.
- Builds and tests have been on Linux (WebKitGTK) only.

## Decided

- Host-side, native, Rust; MuPDF for paginated documents; one `wry` web view
  for the shell and all HTML.
- No content scripts: static HTML plus declarative attributes, sanitized and
  served with `script-src 'none'`.
- Tags and notes editable in the viewer, written as the user by a host-only
  command.
- Form values as pane state read at the next prompt; `svc:` services for
  immediate answers.
- Default list panes built on the same mechanism as Claude's panes.
