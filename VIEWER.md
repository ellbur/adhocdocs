# Document Viewer

*Status: draft.*

The viewer shows documents to the user. Under the design principles:

- Its built-in displays are for **viewing, navigating and pointing**. They
  have no search box, filters or editing controls (Principle 1).
- Everything it shows and everything the user points at can be read by
  Claude. Claude can drive it from code, and can add displays of its own
  (Principles 1 and 3).
- What it can reach is limited by the session's scope, which is set outside
  Claude (Principle 2).

## Goals

1. **Fast enough for rapid browsing.** Review often means flipping through
   hundreds of documents quickly. Moving to the next document or page should
   feel instant. As a working target, the first page should be visible within
   100 ms of the user pressing a key.
2. **Many documents at once.** The user can see several documents side by
   side, in one window or across several windows and monitors.
3. **Exact mapping to locators.** Anything shown can be selected as a
   locator, and any locator can be shown and highlighted.
4. **Custom displays from Claude.** For a particular search task, Claude can
   build a navigation display suited to that task and show it in the viewer.

## Overview

The viewer is a **native desktop application, written in Rust, that runs on
the user's machine, outside the container**. It has two rendering engines:

| Engine | Used for |
|---|---|
| **MuPDF** | paginated documents: PDF, scans, TIFF and other images |
| **an embedded web view** | non-paginated documents (email, .docx, HTML), literate pages, lists and results, and displays built by Claude |

```
   container                              user's machine
 ┌──────────────────────┐            ┌──────────────────────────────────────────┐
 │ Claude Code          │            │ viewer (native, Rust)                    │
 │  view CLI ───────────┼── API ────▶│  ├ scope check (set by connect mechanism) │
 │  Claude's services ◀─┼── relay ───┤  ├ MuPDF panes      (paginated docs)      │
 │                      │            │  └ web view panes  (flowing docs, pages, │
 └──────────────────────┘            │                     custom displays)     │
                                     │        reads the object store (scoped)   │
                                     └──────────────────────────────────────────┘
```

## Where it runs and how isolation holds

The viewer runs on the host, so its display is native and fast. Isolation
still holds, because the viewer isn't Claude, and everything it enforces is
enforced by the viewer itself:

- **Scope comes from outside.** The viewer learns which documents are
  connected from the same mechanism that connects them to the container,
  never from Claude. It refuses any locator outside that scope, from the API
  and from every pane.
- **A narrow API.** Claude, inside the container, reaches the viewer through
  an API endpoint that the connect mechanism provides. The API accepts
  locators and pane content, and returns state. It has no way to open
  arbitrary files or paths, and no way to change the scope.
- **No network.** The viewer has no network access other than that API.
  Every web view runs with all outside network access blocked.
- **One instance per session,** launched by the connect operation. Claude
  can't launch the viewer.

How this fits into making the container feel seamless is still part of that
open question in [DESIGN.md](DESIGN.md).

## Rendering libraries

### Paginated documents: MuPDF

Recommended, through Rust bindings (the `mupdf` crate).

- It is fast, and widely used in lightweight viewers for that reason.
- It covers PDF, XPS, EPUB, CBZ, and images including multi-page TIFF, so
  scans and image productions need nothing extra.
- **License:** AGPL, or commercial from Artifex. Using it makes the viewer
  AGPL, which is fine while the project is open source.

Alternatives:

- **PDFium** (`pdfium-render` crate): BSD/Apache, PDF only.
- **Poppler** (used by KDE's Okular): GPL, generally slower.

Rendering sits behind a small internal interface (render a page at a scale,
get a page's size), so the library can be swapped.

### Everything else: an embedded web view

| Option | Notes |
|---|---|
| `wry` (Tauri's web view) | Uses the system engine: WebKitGTK on Linux, WKWebView on macOS, WebView2 on Windows. Light. Behavior differs a little across platforms. |
| CEF (Chromium Embedded) | The same engine everywhere. Heavy. |
| Servo | Embeddable and written in Rust. Still maturing. |

Recommended to start: **`wry`**.

The web view also affects the choice of GUI toolkit, which is still open:

| Toolkit | Notes |
|---|---|
| GTK4 | Embeds WebKitGTK as an ordinary widget, so it fits the web view most naturally on Linux. |
| egui | A GPU canvas, which is good for MuPDF pages. Web views have to be placed as child windows over the canvas, which is workable but awkward. |
| Qt (`cxx-qt`) | Fits KDE. Has QtWebEngine. Rust integration is less direct. |

## Display server and panes

One application, the **display server**, provides every display. It has
windows, each window is split into **panes**, and each pane has a kind:

| Pane kind | Engine | Shows |
|---|---|---|
| `document` | MuPDF or web view | a document, in page mode or flow mode (below) |
| `page` | web view | a rendered literate page ([PAGES.md](PAGES.md)) |
| `list` | web view | a list of documents (default display, to be designed) |
| `results` | web view | search results with snippets (default display, to be designed) |
| `custom` | web view | a display built by Claude (see [Custom panes](#custom-panes)) |

- **Layout:** panes tile within a window (split vertically, split
  horizontally, or grid). Extra top-level windows can go on other monitors.
- **Independence:** pane kinds share only the shell (windows, layout and the
  state API) and the locator format. A pane kind never calls another pane
  kind's code. When the user clicks a search result, the results pane asks
  the server to open a locator, and the server gives it to a document pane.
- **Default list and results panes are built like custom panes.** They are
  ordinary web pages that use the same bridge as Claude's custom panes. This
  means the defaults are examples of what Claude can build, and Claude can
  replace them for a particular task (Principle 4).
- **State is held by the server.** Panes report what the user does to the
  server. The API reads and changes that state.

## Showing documents

Everything shown must map back to **layer offsets**. Otherwise a selection
couldn't become a locator, and a locator couldn't be highlighted.

### Page mode (MuPDF)

Used for content that has its own pages: PDF, scans, TIFF, images, and
transcripts.

- Pages are drawn by MuPDF, with an invisible text overlay built from the
  layer's **geometry** (not MuPDF's own text extraction). This keeps
  selections on our offsets, and keeps highlights consistent with the quote
  checker.
- For OCR layers, low-confidence words can be shaded.
- Selecting by dragging a box gives a `page` plus `xywh` locator.

### Flow mode (web view)

Used for content without pages: email, .docx and HTML. These are shown as
flowing text, not converted to pages.

- The layer's **presentation** is shown: an HTML rendering made at
  ingestion, in which every run of text carries its layer offset
  ([TEXTLAYERS.md](TEXTLAYERS.md)):

  ```html
  <span data-o="1200">the shipment was never received</span>
  ```

- **Selections:** the viewer converts a DOM selection to offsets by reading
  `data-o` at the start and end nodes. To highlight, it finds the spans that
  cover a range.
- **Email:** a header panel, then the body. Attachments are listed as links
  to their child documents.
- **.docx:** shown as flowing paragraphs. Tracked changes are visible in the
  `markup` view.
- **Selecting in flow mode** gives `char` locators. There are no page or
  region locators, because flowing content has no stable pages.

### Text mode

Text mode shows the layer text itself, laid out with its segments (headers,
quoted replies, line numbers). It is available for any document that has a
layer, and it maps to offsets exactly. It is also what the viewer falls back
to for formats without a presentation.

### Spreadsheets

`.xlsx` and `.csv` are shown as a grid in a web view, built from `cell`
segments. Selecting cells gives a `sheet` plus `cells` locator.

## Custom panes

Claude can build a display for the task at hand. Some examples:

- a triage board for the emails in a date range, grouped by sender
- a side-by-side checker that steps through a privilege log and shows each
  entry next to its document
- a timeline in which each event can be clicked through to its source

A custom pane is a small web bundle (HTML, JS, CSS and data) that Claude
writes inside the container and sends to the viewer:

```
view pane --new --title "Smith emails by week" ./triage/
view pane --update <pane-id> ./triage/
```

The viewer serves the bundle from a private scheme (`pane://<id>/`) in its
own web view.

### The bridge

A custom pane can't reach the network or the file system. Its only
connection to anything is a `window.adhoc` object that the viewer injects:

| Call | Does |
|---|---|
| `adhoc.open(locator, opts)` | opens a locator in a document pane |
| `adhoc.highlight(locator)` | highlights in a document pane |
| `adhoc.sequence(locators)` | sets the next/previous-document sequence of a document pane |
| `adhoc.state()` | reads viewer state |
| `adhoc.setSelection(locator \| null)` | reports what the user is pointing at in this pane, so that Claude can read it |
| `adhoc.text(locator)` | returns the text at a locator, read by the viewer from the object store |
| `adhoc.snippet(locator, {context})` | returns text around a locator, with the range marked, for showing snippets |
| `adhoc.call(name, payload)` | sends a request to a service Claude runs in the container (below), and returns its reply |

- Every call that takes a locator is checked against the session scope.
- `text` and `snippet` let a pane show real document text without that text
  being copied into the bundle by Claude. They work like literate quotation
  (Principle 5).

### Services in the container

Some displays need live computation, such as running a new search when the
user picks a category. Claude can run a **service** in the container, which
is any program that answers requests. It registers the service with
`view service <name> <command or port>`. `adhoc.call(name, …)` is relayed by
the viewer, over the API connection, to that service, and to nothing else.
The service has only the container's access, so isolation doesn't change.

### Principle 1 and custom panes

Principle 1 is about the default displays: they leave searching and
filtering to Claude. A custom pane is different. Claude builds it because the
user asked for a particular workflow, which is the point of Principle 3. A
custom pane may therefore have whatever controls the task needs, including a
search box, if that is what the user wants.

## Controlling the viewer from code

Inside the container, the `view` CLI is a thin wrapper over the API. All
output is JSON.

```
view open <locator> [--pane <id>|--new-pane|--window <id>] [--layer <view>] [--mode page|flow|text]
view highlight <locator> [--label <text>] [--pane <id>]
view clear-highlights [--pane <id>]
view sequence <list-of-locators> [--pane <id>]
view pane --new|--update <id> <bundle-dir> [--title <t>]
view service <name> <command|port>
view close <pane-id>
view layout <single|split-v|split-h|grid> [--window <id>]
view state
view selection
view history [--limit N]
```

- `open` scrolls to the locator and highlights it briefly.
- `highlight` marks places Claude wants the user to see. Highlights stay
  until they are cleared, and are drawn differently from user selections.
- If the viewer isn't running, the command says so. Claude can tell the user,
  but it can't launch the viewer itself.

### Sequences

`view sequence` gives a document pane an ordered list of documents to move
through with the next- and previous-document keys. The viewer opens and
renders the first pages of the next few documents ahead of time. List,
results and custom panes set sequences through the bridge.

### State

`view state` returns:

```json
{
  "running": true,
  "focused": "p1",
  "windows": [{"id": "w1", "layout": "split-v", "panes": ["p1", "p2", "p3"]}],
  "panes": [
    {
      "id": "p1",
      "kind": "document",
      "mode": "page",
      "doc": "sha256:3f9a…",
      "layer": "sha256:8b21…",
      "view": "native",
      "visible": {"page": [14, 15]},
      "sequence": {"position": 7, "length": 40},
      "selection": {
        "doc": "sha256:3f9a…",
        "layer": "sha256:8b21…",
        "select": {"char": [1200, 1231]},
        "quote": {"exact": "the shipment was never received", "prefix": "…", "suffix": "…"}
      },
      "marks": [ … ],
      "highlights": [ … ]
    },
    { "id": "p2", "kind": "document", "mode": "flow", "doc": "sha256:77c0…", "visible": {"char": [0, 2400]}, "selection": null },
    { "id": "p3", "kind": "custom", "title": "Smith emails by week", "selection": { "doc": "sha256:a1b2…" } }
  ]
}
```

- `selection` is a locator in the full JSON form. It can be dropped straight
  into a page's `refs` block, a tag or a note.
- `visible` lets Claude answer "what am I looking at?". In page mode it is a
  page range, and in flow mode it is a `char` range.

## What the user can do

In the built-in panes, these are all viewing, navigating or pointing:

- **Navigate:** scroll, go to the next or previous page or document, zoom,
  and follow links to parent documents, attachments and linked locators.
- **Switch:** choose another layer view (`native`, `ocr`, `markup`,
  `transcript`), or switch between page or flow mode and text mode.
- **Select:** text, a region (in page mode) or cells.
- **Mark** the current selection. Marks are a scratch list for pointing at
  several things at once ("these three passages"). Claude can read them, and
  the user or Claude can clear them. They don't persist beyond the session
  state.
- **Arrange panes and windows.**

What the user cannot do in the built-in panes:

- **Search, filter, tag, annotate or edit.** The user asks Claude, or Claude
  builds a custom pane.
- **Export a document or open it in another application.** That is an
  isolation operation (Principle 2), handled by the connect mechanism.

The keyboard comes first: next and previous page, next and previous
document, and moving focus between panes all have single-key bindings.

## Safety

Two kinds of untrusted content run in the viewer: **documents**, which may
be hostile, and **custom panes**, which are written by Claude and so can't be
relied on for isolation (Principle 2). Each web view runs at one of three
privilege levels:

| Content | Scripts | Bridge | Network |
|---|---|---|---|
| Document presentations | none | none | none |
| Literate pages | the viewer's own renderer only | read-only (`open`, `text`) | none |
| List, results and custom panes | yes | full (scope-checked) | none |

- **Presentations are sanitized** when they are made at ingestion, inside the
  container, with no network. Scripts, forms, iframes, event handlers and
  external styles are removed. When the viewer shows them, it applies a
  Content Security Policy that blocks everything, plus a web view
  configuration with JavaScript disabled.
- **Remote images** in an email are never fetched. They are replaced with a
  visible placeholder showing the URL, since their presence may itself be
  evidence. Inline `cid:` images are served from the email's attachment
  documents.
- **Document text inside custom panes:** a custom pane that inserts document
  text as HTML could let a hostile document run script with that pane's
  bridge. The bridge limits the damage: everything it can do stays inside the
  session scope. Even so, the bridge's helper library inserts text safely by
  default, and services that Claude exposes through `adhoc.call` should be
  narrow and read-only.
- **No document code runs:** PDF JavaScript, form actions and embedded files
  are never run or opened. External URLs in documents are shown, not
  followed.

## What else the viewer shows

- **A status bar:** a short form of the document ID, custodian, the view in
  use, the position (page *n* of *m* in page mode), the printed page label,
  the Bates number, and the position in the sequence.
- **Tags and notes** on the document, and notes anchored to ranges, as
  margin markers. They are read-only here.
- **Family:** the parent document and attachments, as links.
- **Duplicates:** other documents with the same content hash, as links.

## History

The server keeps a log of navigation: each `open`, and each place the user
went, as a locator with a timestamp and whether it came from the user, from
Claude, or from a custom pane. `view history` answers requests like "go back
to the email I was reading before". The log is session state, separate from
the append-only audit log.

## Decisions to confirm

- **Two engines:** MuPDF for paginated documents, and a web view for
  everything else, including custom panes.
- **`wry` as the web view** to start with. CEF is the fallback if
  differences between platforms become a problem.
- **The GUI toolkit** is still open. GTK4 fits the web view best on Linux.
- **Default list and results panes built as web panes** on the same bridge
  Claude uses.
- **Custom panes may have their own controls,** such as a search box. This is
  how Principle 1 is read for displays Claude builds.
- **Services relayed through the viewer** to the container, as the only way a
  custom pane can do live computation.
- **No authoring of notes or tags in the built-in panes,** including the
  user's own. The user dictates them to Claude.
