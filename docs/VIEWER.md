# Document Viewer

*Status: a first version is built: [`viewer/`](../viewer) (Rust) and the `view`
CLI ([`view.py`](../src/adhocdocs/view.py); `view --help` has usage and keys).
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
windows is one `wry` web view running the viewer's own code ([`shell/`](../viewer/shell),
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
| `document` | a document in page, flow (its presentation), text, grid (spreadsheets) or hex mode (the original's bytes), with mode switches (and layer switches, when there's more than one layer), tag chips, a side panel of highlights, range tags and notes (those carried over from a document a replacement superseded are marked `↪`, with a confirm link: [RECEIVED.md](RECEIVED.md)), family and duplicate links, and a ⋯ menu for downloads and hex mode |
| `list` | a list file ([LISTS.md](LISTS.md)), rendered by `view list` with a linked preview pane |
| `timeline` | a list file by date, in lanes, rendered by `view timeline`, linked the same way |
| `graph` | an SVG diagram, linked the same way: entities and their links (`view graph`, [GRAPH.md](GRAPH.md)), or a fact's trace as a time DAG (`view trace`, [TRACE.md](TRACE.md)), or a dispute's (`view dispute`, [DISPUTES.md](DISPUTES.md)) |
| `custom` | static HTML from Claude, a file or a bundle; literate pages (`page render`) are shown this way |
| `home` | the overview: what the matter holds and what state it's in (below) |
| `files` | loose files: what the user handed Claude and what Claude handed back, which aren't discovery documents ([below](#loose-files)) |

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
[`overview.rs`](../viewer/src/overview.rs)) and is read again every few
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
| `<a href="loc:…">` | open the locator, in the linked document pane if there is one; `data-mode="page"` (or `flow`, `text`…) opens it in that mode, if the document has it |
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

**The header** names the document's kind and title, then its modes, by
what they show: *Pages*, *Formatted* (flow), *Extracted text* (text: the
layer text line by line, exactly what search, quotes and Claude read) and
*Cells* (grid). Hex mode is in the ⋯ menu, since few documents need it,
unless nothing else can show the document; `f` cycles the others.

**Cropping blank margins** (the ⋯ menu, one setting for every paginated
document) shows each page through a window onto its ink, so the text is
larger. The host finds the ink on a small grayscale rendering (about 1.5 ms
a page, kept while the viewer runs: `/api/margins/<doc>?from=&to=`); a row
or column needs two dark pixels to count, so specks don't hold a margin
open, and a blank page is kept whole. Each page is cropped to its own ink,
but all at one scale, so type is the same size throughout: the widest ink
fits the pane. The first four pages' margins come before layout; the
rest are guessed from them and set a batch at a time, the scroll moving
with any page above the view that changes height. The window isn't a
positioned element, so geometry, highlights, regions and selections work
on the whole page as before. Nothing is ever cut off: only blank margins
go.

**Received documents** ([RECEIVED.md](RECEIVED.md)) show their Bates range
and the producing party beside the title, and the status line gives each
page's Bates number, as for documents we produced.

## Downloads

The ⋯ menu saves the document to the user's Downloads folder
(`$XDG_DOWNLOAD_DIR`, else `~/Downloads`; `ADHOC_DOWNLOAD_DIR` overrides),
never overwriting a file there:

| Choice | What's saved |
|---|---|
| Original | the bytes as collected |
| PDF | a PDF as is; an image as a PDF page; anything else laid out by MuPDF from its presentation (or its text) as Letter pages |
| PDF with annotations | the same, with the pane's highlights and the document's ranged tags and notes as PDF highlight annotations (notes as their comments) and, on laid-out documents, numbered at the end; tags and notes on the whole document go on the first page |
| Text | the layer's text, page breaks as form feeds |

Only the user can ask for one: it is an IPC message from the shell
(`export`), carried out by the worker that owns MuPDF
([`export.rs`](../viewer/src/export.rs)); the API Claude reaches has no way
to write a file on the host. For pages the shell sends each annotation's
boxes from the layer geometry. For a laid-out document it sends the HTML,
each annotation's text colored with a near-black color that stands for
it (MuPDF's HTML layout paints no inline backgrounds), and the host puts
a highlight wherever that color landed. Annotations on another layer than
the one shown are left out, and the message says how many.

## Loose files

Not every file in the work is a discovery document. A client's notes, a
draft to mark up, a template, opposing counsel's letter: the user wants to
hand these to Claude, and Claude makes files for the user (a redline, a
spreadsheet, a letter). Chat tools take these by drag and drop onto the
chat window. Here the chat is a terminal, so the viewer takes them.

**What they are:** plain files in the workspace, in `workspace/files/`
(`/matter/workspace/files/` in the session):

- `uploads/`: what the user handed Claude;
- `from-claude/`: what Claude handed the user, with Claude's note on each.

They aren't ingested, have no document IDs, and aren't searched, quoted,
synced or tracked. Claude can read, change and delete them. The overview,
date cards and list catalog skip them, so a list file or page among them
isn't taken for work product. To make one part of the record, the user
ingests it on the host (`adhoc ingest`).

**Handing Claude files:**

- **Drop them anywhere** in a viewer window, files or folders. A veil says
  where they'll go. They're copied into `uploads/`, and the Files pane opens
  with them marked. The user then tells Claude what to do with them.
- **Drop them on Claude's terminal** (xterm.js, when the viewer is the
  terminal), and their paths are also typed at Claude's prompt, as a desktop
  terminal does with a dropped file:
  `/matter/workspace/files/uploads/draft.pdf `. Nothing is sent: the user
  finishes the sentence.
- **The ☰ menu** at the top left opens the Files pane, which has "Add
  files…" and "Add a folder…" (the system's file dialog).

The host takes the drop (wry's drag-and-drop handler, before the web view
sees it), so a drop works over any frame or the terminal, and files of any
size are copied as files: nothing goes through the web view. Each is
copied under a hidden name and renamed when whole, so Claude never sees
half a file, and never over a file already there (`name (2).ext`). A link
or text dragged within the viewer is left to the web view.

**Claude handing files back:** `view deliver FILE… --note "what it is"`
copies them into `from-claude/` (a file already there stays put) and opens
the Files pane with them marked. The user saves one with "Save to
Downloads" (a copy, never over a file there), the same rule as other
downloads: only the user writes outside the matter. `view files` opens
the pane. `view state` lists both folders' names.

**The Files pane** shows both folders, newest first, with each file's
size, age, Claude's note, and the path Claude sees ("Copy path" puts it on
the clipboard for the prompt). It has "Save to Downloads", "Remove" (click
twice), and "Show folder" (the file manager), and a warning that these
aren't discovery documents and that Claude can change them. It reads the
folders again every two seconds while it's showing.

## Light and dark

The ◐ button in the top bar sets the interface's theme (the system's,
light or dark) and the documents' theme (the interface's, light or dark)
apart, so dark chrome can frame light pages or the other way round. Both
are kept per viewer, in its local storage.

Dark documents cost the web view nothing: MuPDF's page images come with
each pixel's lightness inverted and its hue kept (what CSS `invert(1)
hue-rotate(180deg)` approximates), squeezed between the dark page color
and a soft white, once per rendered page (`/page/…?dark=1`, cached
apart). Presentations, text, grid and hex modes take dark colors from
CSS. Highlights are blended with `screen` on dark pages, `multiply` on
light ones. Only a presentation with colors of its own (an HTML email) is
inverted with a CSS filter.

**Sharp pages:** page images are rendered at the width they're shown, in
device pixels, and panes and pages sit on whole pixels, so the web view
never rescales them (rescaling cost text its antialiasing).

## Claude Code in the viewer

Claude Code needs a terminal. Started from one (`adhoc open`), it stays
there and the viewer only shows documents. Started without one, from the
launcher (`adhoc gui`) or with `adhoc open --terminal beside|window`, the
viewer is its terminal: it runs the session's `podman run -it …` on a
pseudo-terminal of its own ([`term.rs`](../viewer/src/term.rs)) and shows it
with [xterm.js](https://xtermjs.org), the terminal emulator VS Code uses
([`shell/term.js`](../viewer/shell/term.js), vendored under
[`shell/vendor/`](../viewer/shell/vendor)). It is an ordinary terminal, not a
reformatting of Claude Code's output: what Claude Code draws is what shows,
keys typed in it go to Claude Code (the viewer's own keys don't apply
there), and Ctrl+Shift+C and Ctrl+Shift+V copy and paste.

- **Beside the documents** (`--terminal beside`): the terminal is a column
  on the left of the viewer's window, with a splitter.
- **In a window of its own** (`--terminal window`): the documents keep the
  viewer's window, and Claude's `view` commands still go there.
- **Either way, while it runs:** ⇱ in the terminal's header moves it to a
  window of its own, and ⇲ puts it back. The new window is redrawn from the
  last 2 MB of output, kept in memory for this and for nothing else.
- **Closing** the terminal's window while Claude Code runs asks first, as it
  ends the session. When Claude Code exits, the viewer closes, as in a
  terminal. If the session fails, the window stays open with its output
  until it's closed, and `adhoc open` exits with the session's status.

The terminal relays nothing anywhere else: output goes to the screen, keys
to the program, and the audit log is written by the launcher and proxy
exactly as with a terminal of the system's.

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
([`entities.js`](../viewer/shell/entities.js)) so the list's changes show at
once, and reads suggestions from the `entities` index's files. See
[ENTITIES.md](ENTITIES.md).

## Dates

A strip under the header says when the document is dated and where that
came from (sent, attached, created, modified). Dates in the text have a
dashed underline. Hovering either one shows a date card: the document's
thread, how many documents are near the date, and the nearest events
before and after, from chronologies, dated entities, key sets, pages'
citations and hot tags. The card's data comes from `/api/around`
([`chrono.rs`](../viewer/src/chrono.rs)). See [DATES.md](DATES.md).

## On a limited copy

A [limited copy](SYNC.md#limited-copies) has every document's text but
fetches originals on demand. **The viewer never waits for one.** Opening a
document whose original isn't here shows its text at once, with "Fetching
the original… its text is here meanwhile" in the pane's head, and switches
to the original (pages, hex) when it arrives. If by then the user has
selected something or highlights are showing, it doesn't switch on its
own: the note offers "show it". If no member that holds the original can be
reached, the note says so and offers "try again". A sequence (stepping
through one, a list pane's, or `view sequence`) fetches the next three in
the background, so they open as pages straight away.

How: the viewer answers every request on one worker thread, so nothing
there may wait on the network. An original the store lacks is fetched on a
thread of its own (`adhoc fetch-original`), outside the matter's lock, and
a read answers "not here" meanwhile. `/api/fetch/<doc>` starts a fetch if
needed and says where it stands (`here`, `fetching`, `failed`, `absent`),
at once.

When a fetch ends, the viewer tells every window's shell. The fetching
thread sends the event loop a `Fetched` event, and the loop runs
`adhoc.fetched({content, state})` in each window, as it does for other
news from outside (`adhoc.elsewhere`, `adhoc.visited`). A pane waiting for
that original asks `/api/fetch` once more, and shows it. A pane starts
listening before it first asks, so a fetch that ends in between isn't
missed. If no word comes within 15 seconds, it asks anyway. An original
that couldn't be fetched isn't tried again for 30 seconds, except by
"try again" (`/api/fetch/<doc>?retry`).

## State

`view state` reports the layout, panes, and per pane: the document, layer
and view, mode, what's visible (pages or a char range), the sequence
position, the **selection as a JSON locator with its quote fingerprint**
(ready for a `refs` block, tag or note) and the `entity` or `date` it is, if any, marks, highlights, form values, and
a list pane's selected rows. `view history` lists where the user went and
whether the user, Claude or a pane went there.

## Checking it without a screen

The viewer can be built and driven where there is no display, such as a
cloud container or CI, so a change to it can be seen and not only
compiled. On Ubuntu 24.04:

```sh
apt-get install -y libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev xvfb xdotool imagemagick x11-utils
cd viewer && cargo build --release        # about 4 minutes the first time (MuPDF is built from source)
tools/headless-viewer.sh MATTER           # the viewer on a virtual screen, :99
```

`view` commands drive it as usual, and `view state` says what each pane
shows. `xdotool mousemove X Y click 1` clicks, and `import -window root
shot.png` takes a picture of the screen. Page images are drawn a moment
after a document opens, so wait for `view state` to show the page before
taking the picture. Rendering is in software, so it is slower than on a
desktop, but what it shows is the same.

## Not built yet

- Loose files aren't in the audit log: the viewer copies them in and out
  at the user's say-so, outside Claude's tool calls. Claude's reads and
  writes of them are recorded, like any tool call.
- Dropping files has been tried on Linux (WebKitGTK, a real XDND drag
  under Xvfb). wry's handler is the same on Windows and macOS, but untried.

- The 100 ms target is met most of the time, not always. `view state`
  reports each document pane's `open_ms`: keypress to first page shown.
  Stepping through the corpus's 33 text PDFs with `]` (release build,
  Xvfb with software rendering, a short pause on each) gives a median of
  about 40 ms and a worst case of about 165 ms, with 5 of 33 over 100 ms.
  Those are the densest pages (the Federal Register, a Supreme Court
  opinion, a statute); their image is already cached by then, so what is
  left is mostly the web view decoding and painting it, which is slower
  here than on a desktop. Before the fixes below, the worst case was
  764 ms (the 142-page Federal Rules) and the first document took 280 ms.

  The viewer answers requests one at a time (one worker owns MuPDF), so
  what matters most is what is asked for first:
  - a page's image before its words: the page view asks for word boxes
    only once the first page is showing (or after 1.5 s);
  - word boxes are sent as stored (one JSON object a line, joined into an
    array) rather than parsed and written out again: 688 ms to a few for
    the Federal Rules;
  - each document's first page is prefetched before the user moves to it,
    and then its words and text too, unless it is long (over 40 pages),
    whose words would hold up a page the user asks for meanwhile.

  A cache holds the last 64 rendered pages. `ADHOC_VIEWER_DEBUG=1` logs
  each request's time, and [`tools/viewer_bench.py`](../tools/viewer_bench.py)
  repeats the measurement on any matter.
- Builds and tests have been on Linux (WebKitGTK) only. The terminal and
  the launcher use portable-pty (ConPTY on Windows) and, for file dialogs
  outside Linux, rfd; neither has been tried on Windows or macOS yet, and
  `--terminal app` doesn't know macOS's Terminal yet.

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
