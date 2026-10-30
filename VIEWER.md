# Document Viewer

*Status: draft.*

The viewer shows documents to the user. Under the design principles:

- Its displays are for **viewing, navigating, pointing and simple input**.
  The Claude Code prompt remains the main way to get things done
  (Principle 1).
- Everything it shows and everything the user points at or enters can be
  read by Claude. Claude can drive it from code, and can give it displays of
  its own (Principles 1 and 3).
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
4. **Custom displays from Claude.** For a particular task, Claude can show a
   display suited to it, such as a grouped list or a checklist.
5. **No content scripts.** No script supplied by a document, or written by
   Claude, ever runs in the viewer.

## Overview

The viewer is a **native desktop application, written in Rust, that runs on
the user's machine, outside the container**. It has two rendering engines:

| Engine | Used for |
|---|---|
| **MuPDF** | paginated documents: PDF, scans, TIFF and other images |
| **`wry` web view** | non-paginated documents (email, .docx, HTML), literate pages, lists and results, and displays built by Claude |

```
   container                              user's machine
 ┌──────────────────────┐            ┌──────────────────────────────────────────┐
 │ Claude Code          │            │ viewer (native, Rust)                    │
 │  view CLI ───────────┼── API ────▶│  ├ scope check (set by connect mechanism) │
 │  Claude's services ◀─┼── relay ───┤  ├ MuPDF panes      (paginated docs)      │
 │                      │            │  └ web view panes  (static HTML only)    │
 └──────────────────────┘            │        reads the object store (scoped)   │
                                     └──────────────────────────────────────────┘
```

All behavior lives in two places: the viewer's own native code, and code in
the container. The web views only display static HTML and collect input.

Any GUI toolkit that can host `wry` web views alongside a custom canvas for
MuPDF pages is acceptable. The choice can be made when implementation starts.

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
  locators and pane content (static HTML), and returns state. It has no way
  to open arbitrary files or paths, and no way to change the scope.
- **No network.** The viewer has no network access other than that API.
- **One instance per session,** launched by the connect operation. Claude
  can't launch the viewer.

How this fits into making the container feel seamless is still part of that
open question in [DESIGN.md](DESIGN.md).

## Rendering paginated documents: MuPDF

Recommended, through Rust bindings (the `mupdf` crate).

- It is fast, and widely used in lightweight viewers for that reason.
- It covers PDF, XPS, EPUB, CBZ, and images including multi-page TIFF.
- **License:** AGPL, or commercial from Artifex. Using it makes the viewer
  AGPL, which is fine while the project is open source.

Alternatives:

- **PDFium** (`pdfium-render` crate): BSD/Apache, PDF only.
- **Poppler** (used by KDE's Okular): GPL, generally slower.

Rendering sits behind a small internal interface (render a page at a scale,
get a page's size), so the library can be swapped.

## Display server and panes

One application, the **display server**, provides every display. It has
windows, each window is split into **panes**, and each pane has a kind:

| Pane kind | Engine | Shows | HTML produced by |
|---|---|---|---|
| `document` | MuPDF or web view | a document, in page mode or flow mode (below) | ingestion (presentations) |
| `page` | web view | a rendered literate page ([PAGES.md](PAGES.md)) | `page render` |
| `list` | web view | a list of documents (default display, to be designed) | the list tool |
| `results` | web view | search results with snippets (default display, to be designed) | the search tool |
| `custom` | web view | a display built by Claude | Claude, in the container |

- **Layout:** panes tile within a window (split vertically, split
  horizontally, or grid). Extra top-level windows can go on other monitors.
- **One mechanism for every web pane.** Every web pane is static HTML
  produced in the container and marked up with the declarative attributes
  described under [Thin panes](#thin-panes). The default list and results
  panes have no special powers. They are examples of what Claude can produce,
  and Claude can replace them for a particular task (Principle 4).
- **Independence:** pane kinds share only the shell (windows, layout and the
  state API) and the locator format.
- **State is held by the server.** The API reads and changes it.

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

- The layer's **presentation** is shown: a sanitized HTML rendering made at
  ingestion, in which every run of text carries its layer offset
  ([TEXTLAYERS.md](TEXTLAYERS.md)):

  ```html
  <span data-o="1200">the shipment was never received</span>
  ```

- **Selections:** the viewer's own code maps a selection to offsets by
  reading `data-o` at its start and end. To highlight, it finds the spans that
  cover a range.
- **Email:** a header panel, then the body. Attachments are listed as links
  to their child documents.
- **.docx:** flowing paragraphs. Tracked changes are visible in the
  `markup` view.
- **Selecting in flow mode** gives `char` locators. There are no page or
  region locators, because flowing content has no stable pages.

### Text mode

Text mode shows the layer text itself, laid out with its segments (headers,
quoted replies, line numbers). It is available for any document that has a
layer, maps to offsets exactly, and is the fallback for formats without a
presentation.

### Spreadsheets

`.xlsx` and `.csv` are shown as a grid built from `cell` segments. Selecting
cells gives a `sheet` plus `cells` locator.

## Thin panes

### Why panes are thin

Claude acts when the user prompts it. It is not watching the GUI, and it
can't react to events as they happen. A display that depends on Claude
responding to clicks would feel broken. So even displays that Claude builds
stay **thin**: they display things and collect simple input. Logic stays in
the container, and the Claude Code prompt remains the main way to interact.

Rapid interactions that don't need Claude, such as opening a document,
stepping through a sequence, or pointing at an item, are handled immediately
by the viewer's native code. Anything that needs thought goes through the
prompt.

### No scripts

No web pane runs scripts from its content. That includes document
presentations, literate pages, the default list and results panes, and
Claude's custom panes.

- **Sanitized on arrival:** the viewer removes `<script>`, event-handler
  attributes, `javascript:` URLs, iframes, objects, embeds and external
  resources from every page it is given, whoever produced it.
- **Blocked by policy as well:** every web view is served with a Content
  Security Policy of `script-src 'none'` and no network sources.
- **The viewer's own code is the only exception.** It needs to read
  selections and handle clicks on declarative attributes. It does this with
  its own code, injected by the viewer through the web view's
  initialization-script mechanism, in an isolated context where the platform
  engine allows it. That code is part of the viewer and never comes from
  content. (Whether this works within `script-src 'none'` on each platform
  engine needs to be checked during implementation.)

So a hostile document can't run code, even if its text ends up in a pane
that Claude built.

### Declarative attributes

Web panes describe behavior with attributes, and the viewer's native code
carries it out.

| Markup | Behavior |
|---|---|
| `<a href="loc:<locator>">` | opens the locator in a document pane |
| `data-locator="<locator>"` on any element | makes the element pointable: clicking selects it (the pane's selection becomes that locator), double-clicking or Enter opens it, and the mark key adds it to marks |
| `data-sequence` on a list | its `data-locator` items become the next/previous-document sequence of the linked document pane |
| `<span data-text="<locator>"></span>` | the viewer fills in the document text at the locator, inserted as text, never as HTML |
| `<span data-snippet="<locator>" data-context="80"></span>` | the viewer fills in a snippet around the locator, with the range highlighted |
| `data-status="<locator>"` | the viewer adds a resolution marker (exact, re-anchored, broken) |

`data-text` and `data-snippet` work like literate quotation (Principle 5).
The text shown comes from the document, filled in by the viewer, and is not
copied into the HTML by Claude.

### Input

Plain HTML form controls collect input: checkboxes, radio buttons, select
menus and text fields. They don't need scripts.

- **Form values are pane state.** The viewer records them as the user
  changes them, and `view state` reports them. Claude reads them when the
  user next prompts it. For example, the user ticks the privileged emails in a
  checklist pane, then tells Claude to "log the ones I ticked".
- **Submitting to a service:** a form with `action="svc:<name>"` sends its
  values, when submitted, to a service Claude runs in the container. The
  service replies with new static HTML for the pane. This is how a pane gets
  an immediate response without Claude in the loop, for example "next page of
  results" or "regroup by sender". The service is ordinary code in the
  container, and the viewer relays only to services registered for the
  session.

Services are optional. A pane with no service is purely display plus
recorded input.

### Producing a custom pane

Claude writes static HTML (plus CSS and images) in the container and sends
it:

```
view pane --new --title "Smith emails by week" ./triage/index.html
view pane --update <pane-id> ./triage/index.html
view service <name> <command|port>
```

Example of a checklist pane:

```html
<h2>Week of March 2</h2>
<form action="svc:triage">
  <ol data-sequence>
    <li data-locator="doc:sha256:3f9a…">
      <input type="checkbox" name="priv" value="doc:sha256:3f9a…">
      Smith → Jones, Mar 3:
      <span data-snippet="doc:sha256:3f9a…@sha256:8b21…#char=1200,1231" data-context="60"></span>
    </li>
    …
  </ol>
  <button name="op" value="regroup-by-recipient">Group by recipient</button>
</form>
```

## Controlling the viewer from code

Inside the container, the `view` CLI is a thin wrapper over the API. All
output is JSON.

```
view open <locator> [--pane <id>|--new-pane|--window <id>] [--layer <view>] [--mode page|flow|text]
view highlight <locator> [--label <text>] [--pane <id>]
view clear-highlights [--pane <id>]
view sequence <list-of-locators> [--pane <id>]
view pane --new|--update <id> <html-file> [--title <t>]
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
- `sequence` gives a document pane an ordered list to move through with the
  next- and previous-document keys. The viewer opens and renders the first
  pages of the next few documents ahead of time.
- If the viewer isn't running, the command says so. Claude can tell the user,
  but it can't launch the viewer itself.

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
      "sequence": {"position": 7, "length": 40, "from": "p3"},
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
    {
      "id": "p3",
      "kind": "custom",
      "title": "Smith emails by week",
      "selection": {"doc": "sha256:3f9a…"},
      "form": {"priv": ["doc:sha256:3f9a…", "doc:sha256:c802…"]}
    }
  ]
}
```

- `selection` is a locator in the full JSON form. It can be dropped straight
  into a page's `refs` block, a tag or a note.
- `visible` lets Claude answer "what am I looking at?". In page mode it is a
  page range, and in flow mode it is a `char` range.
- `form` holds the current input values of a web pane.

## What the user can do

- **Navigate:** scroll, go to the next or previous page or document, zoom,
  and follow links to parent documents, attachments and locators.
- **Switch:** choose another layer view (`native`, `ocr`, `markup`,
  `transcript`), or switch between page or flow mode and text mode.
- **Point:** select text, a region (in page mode), cells, or any pointable
  item in a web pane.
- **Mark** the current selection. Marks are a scratch list for pointing at
  several things at once ("these three passages"). Claude can read them, and
  the user or Claude can clear them. They don't persist beyond the session
  state.
- **Tag and annotate:** apply tags by hotkey or from a palette, and write,
  edit or reply to notes ([ANNOTATIONS.md](ANNOTATIONS.md)). This is the
  one kind of editing the viewer allows.
- **Fill in** form controls in panes that have them.
- **Arrange panes and windows.**

Apart from tags and notes, the default panes have no search, filter or edit
controls. The user asks Claude for those. A pane Claude builds may have simple input
controls when the task calls for them. It still stays thin (see
[Thin panes](#thin-panes)).

Exporting a document or opening it in another application is not possible
from the viewer. That is an isolation operation (Principle 2), handled by the
connect mechanism.

The keyboard comes first. Next and previous page, next and previous
document, mark, tag hotkeys, and moving focus between panes all have
single-key bindings.

## Safety

- **No content scripts anywhere** (see [No scripts](#no-scripts)).
- **Presentations are sanitized twice:** once at ingestion, inside the
  container with no network, and again by the viewer when they are shown,
  like every other web pane.
- **Remote resources are never fetched.** A remote image in an email is
  replaced with a visible placeholder showing the URL, since its presence may
  itself be evidence. Inline `cid:` images are served from the email's
  attachment documents.
- **Text from documents is inserted as text.** `data-text` and
  `data-snippet` never insert HTML.
- **No document code runs:** PDF JavaScript, form actions and embedded files
  are never run or opened. External URLs in documents are shown, not
  followed.
- **Everything is checked against the session scope.** That includes every
  locator in every attribute, link and API call.

## What else the viewer shows

- **A status bar:** a short form of the document ID, custodian, the view in
  use, the position (page *n* of *m* in page mode), the printed page label,
  the Bates number, and the position in the sequence.
- **Tags and notes** on the document, and notes anchored to ranges, as
  margin markers. The user can create, edit and delete them directly (see
  [ANNOTATIONS.md](ANNOTATIONS.md)).
- **Family:** the parent document and attachments, as links.
- **Duplicates:** other documents with the same content hash, as links.

## History

The server keeps a log of navigation: each `open`, and each place the user
went, as a locator with a timestamp and whether it came from the user, from
Claude, or from a pane. `view history` answers requests like "go back to the
email I was reading before". The log is session state, separate from the
append-only audit log.

## Decided

- The viewer runs on the host, outside the container.
- Two engines: MuPDF for paginated documents, and `wry` for everything else.
- Any GUI toolkit that can host both engines is acceptable.
- No content scripts in any web pane. Panes are static HTML plus declarative
  attributes. Logic stays in the container.
- Tags and notes can be edited directly in the viewer
  ([ANNOTATIONS.md](ANNOTATIONS.md)).

## Decisions to confirm

- **Form values as pane state** that Claude reads at the next prompt, plus
  optional `svc:` services for immediate responses.
- **Default list and results panes built on the same mechanism** as custom
  panes.
