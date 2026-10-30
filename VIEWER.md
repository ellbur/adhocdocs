# Document Viewer

*Status: draft.*

The viewer shows documents to the user. Under the design principles:

- It is for **viewing, navigating and pointing**. It has no search box,
  filters or editing controls (Principle 1).
- Everything it shows and everything the user points at can be read by
  Claude, and Claude can drive it from code (Principles 1 and 3).
- It runs inside the container and never reaches outside it (Principle 2).

## Architecture

The viewer is a small **display server** that runs in the container, plus a
**browser UI** that connects to it.

```
 Claude / scripts ──CLI / HTTP API──▶ display server ◀──WebSocket──▶ browser UI ── user
                                            │
                                      object store
                                (content, layers, presentations)
```

- **The server owns all state:** which documents are open, what is
  highlighted, and what the user has selected. The browser UI is a view of
  that state, and it reports what the user does back to the server.
- **Claude never talks to the browser.** It uses the CLI or the server's
  HTTP API, which both take and return locators and JSON.
- **The server listens only on localhost.** How the user's browser reaches it
  is part of the open "seamless containers" question in
  [DESIGN.md](DESIGN.md).
- **More than one browser tab:** each mirrors the same state. If the user
  selects something in any tab, that becomes the selection.

### One display server, several kinds of pane

The document list, search results and rendered pages are displays too. They
need the same things the viewer needs: localhost serving, state that Claude
can read, and a selection expressed as locators. So they all run in the same
display server, each as a different **pane kind**:

| Pane kind | Spec |
|---|---|
| `document` | this file |
| `list` | to be designed |
| `results` | to be designed |
| `page` | rendered literate page ([PAGES.md](PAGES.md)) |

Pane kinds are independent modules. They share only the shell (layout and the
state API) and the locator format. A pane kind never calls another pane
kind's code. When the user clicks a search result, the results pane asks the
server to open a locator, and the server gives it to a document pane.

## Controlling the viewer from code

The `view` CLI is a thin wrapper over the HTTP API. All output is JSON.

```
view open <locator> [--pane <id>|--new-pane] [--layer <view>]
view highlight <locator> [--label <text>] [--pane <id>]
view clear-highlights [--pane <id>]
view close <pane-id>
view layout <single|split-v|split-h|grid>
view state                      # everything below, as one JSON object
view selection                  # just the selection(s)
view history [--limit N]        # what the user and Claude have navigated to
```

- `open` scrolls to the locator and highlights it briefly. A whole-document
  locator opens at page 1.
- `highlight` marks one or more places that Claude wants the user to see. For
  example, Claude might highlight every hit of a phrase in the open document.
  The marks stay until they are cleared, and are drawn differently from user
  selections.
- If no browser is connected, `open` still updates the state, and reports
  `{"connected": false, "url": "…"}` so Claude can tell the user where to
  look.

### State

`view state` returns:

```json
{
  "connected": true,
  "focused": "p1",
  "layout": "split-v",
  "panes": [
    {
      "id": "p1",
      "kind": "document",
      "doc": "sha256:3f9a…",
      "layer": "sha256:8b21…",
      "view": "native",
      "visible": {"page": [14, 15]},
      "selection": {
        "doc": "sha256:3f9a…",
        "layer": "sha256:8b21…",
        "select": {"char": [1200, 1231]},
        "quote": {"exact": "the shipment was never received", "prefix": "…", "suffix": "…"}
      },
      "marks": [ … ],
      "highlights": [ … ]
    },
    { "id": "p2", "kind": "document", "doc": "sha256:77c0…", "visible": {"page": [2, 2]}, "selection": null }
  ]
}
```

- `selection` is a locator in the full JSON form, with its quote fingerprint.
  A selection can be dropped straight into a page's `refs` block, a tag, or a
  note.
- `visible` lets Claude answer questions like "what am I looking at?"
  without the user selecting anything.

## What the user can do

These are all viewing, navigating or pointing:

- **Navigate:** scroll, go to the next or previous page, zoom, and follow
  links to parent documents, attachments and linked locators.
- **Switch view:** choose another layer view (`native`, `ocr`, `markup`,
  `transcript`) or the plain-text mode (see [Rendering](#rendering)).
- **Select text.** The selection is a `char` locator.
- **Select a region** by dragging a box on a page. The selection is a `page`
  plus `xywh` locator. This is useful for signatures, stamps, handwriting and
  images.
- **Select cells** in a spreadsheet. The selection is a `sheet` plus `cells`
  locator.
- **Mark** the current selection. Marks are a scratch list for pointing at
  several things at once ("these three passages"). They are shown in the
  pane, they can be read by Claude, and they are cleared by the user or by
  Claude. Marks are not tags. They don't persist beyond the session state, and
  making something durable is done by asking Claude.
- **Arrange panes**, for example side by side to compare two documents.

What the user cannot do in the viewer:

- **Search, filter, tag, annotate or edit.** The user asks Claude for these.
- **Download or open a document in another application.** Getting files out
  of the container is an isolation operation (Principle 2), handled outside
  Claude and outside the viewer.

## Rendering

Everything the viewer shows must map back to **layer offsets**. Otherwise a
selection couldn't become a locator, and a locator couldn't be highlighted.
Each document can be shown in two modes:

1. **Original mode:** as close to how the document looks as possible.
2. **Text mode:** the layer text itself, laid out with its segments (headers,
   quoted replies, cells, line numbers). It maps to offsets exactly, and it is
   always available for any document that has a layer.

### Original mode by format

| Format | Rendering | How offsets map |
|---|---|---|
| PDF | Page images drawn by PDF.js | An invisible text overlay built from the layer's **geometry**, not the PDF's own text, so selections land on our offsets |
| Scans, TIFF, images | The page image | Overlay from OCR geometry. Low-confidence words can be shaded on request |
| Email | Header panel, then the body | A presentation (below). Attachments are listed as links to their child documents |
| .docx | Flowing HTML | A presentation. Tracked changes are shown in the `markup` view |
| .xlsx / .csv | A grid for each sheet | `cell` segments |
| HTML | Sanitized HTML | A presentation |
| Transcripts | The PDF page, or text mode with line numbers in the margin | Geometry, or `line` segments |
| Anything else | Metadata, plus text mode | Exact |

### Presentations

Formats without fixed pages (email, .docx, HTML) need an HTML rendering whose
text maps back to the layer. The extractor produces a **presentation**
alongside the layer: an HTML file in which every run of text is wrapped in an
element carrying its layer offsets:

```html
<span data-o="1200">the shipment was never received</span>
```

- A presentation is stored in the object store and recorded on the layer as
  `"presentation": "sha256:…"`. Because it is part of the layer object, it is
  part of the layer's identity. It is optional: without one, the viewer uses
  text mode.
- The viewer converts a DOM selection to offsets by reading `data-o` from the
  start and end nodes. It converts offsets to a highlight by finding the
  spans that cover the range.

## Safety of rendering

Documents are untrusted. Rendering them must not run their code, and must
not contact anything outside the container. A remote image in an email would
otherwise tell the sender that the email was opened, and from where.

- **Sanitizing:** HTML in presentations is sanitized when it is produced, and
  again when it is served. Scripts, forms, iframes, event handlers and
  external stylesheets are removed.
- **No outside requests:** the UI is served with a strict Content Security
  Policy, `default-src 'self'`. Nothing is loaded from outside the server.
- **Blocked images:** remote images are replaced with a visible placeholder
  showing the URL. The fact that the email contained a remote image may itself
  be evidence. `cid:` images are served from the email's attachment
  documents.
- **No macros or embedded code:** these never run. PDF JavaScript is
  disabled in PDF.js.
- **The server doesn't proxy anything** and doesn't follow links. Clicking an
  external URL in a document shows the URL. It doesn't open it.

## What else the viewer shows

- **A status bar:** a short form of the document ID, custodian, the view in
  use, page *n* of *m*, the printed page label, and the Bates number, if any.
- **Tags and notes** on the document, and notes anchored to ranges, as
  margin markers. They are read-only here.
- **Family:** the parent document and attachments, as links.
- **Duplicates:** other documents with the same content hash, as links.

## History

The server keeps a log of navigation: each `open`, and each place the user
went, as a locator with a timestamp and whether it came from the user or from
Claude. `view history` gives Claude what it needs for requests like "go back
to the email I was reading before", or "which of these did I already look
at?". The log is session state, separate from the append-only audit log,
which records reads independently (see [TOOLS.md](TOOLS.md)).

## Decisions to confirm

- **One display server for all panes,** rather than a separate program for
  each display. The shared parts are the shell and the state API. Pane kinds
  stay independent and communicate only through locators.
- **A browser-based UI,** rather than a native app. It needs only a browser
  on the user's side, and it lets us use PDF.js. How the browser reaches the
  container is still open.
- **Marks as a scratch list,** separate from tags. They make it easy to point
  at several things at once without creating anything durable.
- **No authoring of notes or tags in the viewer,** including the user's own.
  The user dictates them to Claude, and each is recorded with the user as its
  author.
- **Presentations as part of layer identity.** A better presentation means a
  new layer ID. If its text is the same, existing locators are re-anchored
  automatically through the matching text hash.
