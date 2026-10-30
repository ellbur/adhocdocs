# Document Viewer

*Status: draft.*

The viewer shows documents to the user. Under the design principles:

- It is for **viewing, navigating and pointing**. It has no search box,
  filters or editing controls (Principle 1).
- Everything it shows and everything the user points at can be read by
  Claude, and Claude can drive it from code (Principles 1 and 3).
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

## Native application

Browser-based PDF display is generally too slow for rapid browsing, so the
viewer is a **native desktop application written in Rust**, built on a
native rendering library.

### Rendering library

**Recommended: MuPDF**, through Rust bindings (the `mupdf` crate).

- It is fast, and widely used in lightweight viewers for that reason.
- It covers PDF, XPS, EPUB, CBZ, and images including multi-page TIFF, so
  scans and productions in image form need nothing extra.
- It includes an HTML/CSS layout engine, which can be used to produce
  renditions of email (see [Renditions](#renditions)).
- It extracts structured text with positions, which the extractor can use
  for geometry.
- **License:** AGPL, or commercial from Artifex. Using it makes the viewer
  AGPL, which is fine while the project is open source. A commercial license
  is available later if needed.

Alternatives:

| Library | License | Notes |
|---|---|---|
| PDFium (`pdfium-render` crate) | BSD / Apache | Chrome's engine. Fast, and its permissive license avoids AGPL. Covers PDF only; images are handled separately. |
| Poppler (KDE's Okular uses it) | GPL | Mature and accurate. GTK-oriented bindings. Generally slower than MuPDF. |

The design keeps rendering behind a small internal interface: render a page
at a scale, and get a page's size. That way the library can be swapped.

### GUI toolkit

This is still to be decided. The main requirement is a fast custom canvas
for pages and overlays, plus ordinary list and grid widgets.

| Toolkit | Notes |
|---|---|
| egui | Immediate mode and GPU-drawn, so custom canvases are simple. Less native look and feel. |
| GTK4 (`gtk4-rs`) | Mature, native on Linux, usable on macOS and Windows. |
| Qt (`cxx-qt`) | Fits the KDE ecosystem. Rust integration is less direct. |
| Slint, iced | Declarative and Rust-first. Younger ecosystems. |

### Speed

- **Renditions are precomputed** at ingestion (below). At view time
  everything is a page, and no format conversion happens.
- **Caches:** rendered page bitmaps are cached at each zoom level.
- **Prefetch:** when the user is moving through a sequence of documents (see
  [Sequences](#sequences)), the viewer opens and renders the first pages of
  the next few documents ahead of time.
- **Keyboard first:** next and previous page, next and previous document,
  and moving focus between panes all have single-key bindings.

## Where it runs

The viewer is a native GUI, and the session runs in a container. That gives
two options:

**A. Viewer on the host (recommended).** The viewer runs on the user's
machine, outside the container. Claude controls it through an API endpoint
exposed into the container.

- Display is native and fast, with no forwarding layer.
- Isolation still holds, because the viewer is not Claude. The viewer gets
  the session's **scope** (which documents are connected) from the same
  outside mechanism that connects documents to the container, never from
  Claude. It refuses any locator outside that scope.
- The API is deliberately narrow. It accepts locators and returns state, and
  it has no way to open arbitrary files or paths.
- One viewer instance runs per session, launched by the connect operation.

**B. Viewer in the container,** displayed through Wayland/X11 socket sharing
or remote display.

- Everything stays inside the container boundary.
- This is fast on Linux with a shared Wayland socket. It is slow or awkward
  on macOS and Windows, where containers run in a VM, so it may defeat the
  purpose.

Either way, the viewer is part of the "seamless containers" question in
[DESIGN.md](DESIGN.md).

## Display server and panes

One application, the **display server**, provides every display. It has
windows, each window is split into **panes**, and each pane has a kind:

| Pane kind | Shows |
|---|---|
| `document` | a document (this file) |
| `list` | a list of documents (to be designed) |
| `results` | search results with snippets (to be designed) |
| `page` | a rendered literate page ([PAGES.md](PAGES.md)) |

- **Layout:** panes tile within a window (split vertically, split
  horizontally, or grid). Extra top-level windows can go on other monitors.
- **Independence:** pane kinds share only the shell (windows, layout and the
  state API) and the locator format. A pane kind never calls another pane
  kind's code. When the user clicks a search result, the results pane asks
  the server to open a locator, and the server gives it to a document pane.
- **State is held by the server.** Panes report what the user does to the
  server. The API reads and changes that state.

## Controlling the viewer from code

Inside the container, the `view` CLI is a thin wrapper over the API (HTTP
over a socket provided by the connect mechanism). All output is JSON.

```
view open <locator> [--pane <id>|--new-pane|--window <id>] [--layer <view>]
view highlight <locator> [--label <text>] [--pane <id>]
view clear-highlights [--pane <id>]
view sequence <list-of-locators> [--pane <id>]
view close <pane-id>
view layout <single|split-v|split-h|grid> [--window <id>]
view state                      # everything below, as one JSON object
view selection                  # just the selection(s)
view history [--limit N]        # what the user and Claude have navigated to
```

- `open` scrolls to the locator and highlights it briefly. A whole-document
  locator opens at page 1.
- `highlight` marks places Claude wants the user to see, for example every
  hit of a phrase in the open document. Highlights stay until they are
  cleared, and are drawn differently from user selections.
- If the viewer isn't running, the command says so. Claude can tell the user,
  but it can't launch the viewer itself, because launching belongs to the
  connect operation.

### Sequences

`view sequence` gives a pane an ordered list of documents to move through
with the next- and previous-document keys. The viewer prefetches ahead in the
sequence. A `list` or `results` pane can set the sequence of a document pane
it is linked to. Claude can set a sequence directly too, for example "show me
the 40 emails Smith sent that week, one at a time".

### State

`view state` returns:

```json
{
  "running": true,
  "focused": "p1",
  "windows": [{"id": "w1", "layout": "split-v", "panes": ["p1", "p2"]}],
  "panes": [
    {
      "id": "p1",
      "kind": "document",
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
    { "id": "p2", "kind": "document", "doc": "sha256:77c0…", "visible": {"page": [2, 2]}, "selection": null }
  ]
}
```

- `selection` is a locator in the full JSON form, with its quote fingerprint.
  It can be dropped straight into a page's `refs` block, a tag or a note.
- `visible` lets Claude answer "what am I looking at?" without the user
  selecting anything.

## What the user can do

These are all viewing, navigating or pointing:

- **Navigate:** scroll, go to the next or previous page or document, zoom,
  and follow links to parent documents, attachments and linked locators.
- **Switch view:** choose another layer view (`native`, `ocr`, `markup`,
  `transcript`) or text mode (below).
- **Select text.** The selection is a `char` locator.
- **Select a region** by dragging a box on a page. This is useful for
  signatures, stamps, handwriting and images. On content that has its own
  pages, the selection is a `page` plus `xywh` locator. On a rendition (see
  below), it is the `char` range of the text inside the box, because
  rendition pages aren't citable.
- **Select cells** in a spreadsheet. The selection is a `sheet` plus `cells`
  locator.
- **Mark** the current selection. Marks are a scratch list for pointing at
  several things at once ("these three passages"). They are shown in the
  pane, they can be read by Claude, and they are cleared by the user or by
  Claude. Marks are not tags: they don't persist beyond the session state.
- **Arrange panes and windows.**

What the user cannot do in the viewer:

- **Search, filter, tag, annotate or edit.** The user asks Claude for these.
- **Export a document or open it in another application.** Getting files out
  of the session's scope is an isolation operation (Principle 2), handled by
  the connect mechanism, not by the viewer.

## Rendering

Everything the viewer shows must map back to **layer offsets**. Otherwise a
selection couldn't become a locator, and a locator couldn't be highlighted.
The viewer shows each document in one of two modes:

1. **Page mode:** pages drawn by the rendering library, with an invisible
   text overlay built from the layer's **geometry** (not the library's own
   text extraction). This keeps selections on our offsets and keeps
   highlights consistent with the quote checker.
2. **Text mode:** the layer text itself, laid out with its segments (headers,
   quoted replies, line numbers). It maps to offsets exactly, and it is
   always available for any document that has a layer.

| Format | Page mode shows |
|---|---|
| PDF | the PDF |
| Scans, TIFF, images | the image, with an overlay from OCR geometry; low-confidence words can be shaded |
| Email, .docx, HTML | the layer's **rendition** |
| Transcripts | the PDF; text mode puts line numbers in the margin |
| .xlsx / .csv | no page mode; a native grid built from `cell` segments |
| Anything else | no page mode; metadata plus text mode |

### Renditions

Formats without fixed pages (email, .docx, HTML) get a **rendition** at
ingestion: a PDF produced from the content, with the layer's geometry
pointing at the rendition's pages. Some examples of how renditions can be
made:

- **Email:** a header panel, then the body, laid out by MuPDF's HTML engine
  or a similar tool. Remote images are replaced with a visible placeholder
  showing the URL.
- **.docx:** headless LibreOffice. Tracked changes are shown in the `markup`
  view's rendition.

How renditions work:

- **Part of the layer:** a rendition is stored in the object store and
  recorded on the layer as `"rendition": "sha256:…"`
  ([TEXTLAYERS.md](TEXTLAYERS.md)), so it is part of the layer's identity.
- **Not citable by page:** rendition pages depend on the renderer, so
  `page=` locators never refer to them. Selections on a rendition become
  `char` locators. To cite a .docx by page, make a PDF of it as a separate
  derived document (see [LOCATORS.md](LOCATORS.md)).
- **One code path:** because every page-mode display is a PDF or an image,
  the viewer has a single, fast page renderer and no HTML engine.

## Safety of rendering

Documents are untrusted. Rendering them must not run their code, and must
not contact anything outside the session. A remote image in an email would
otherwise tell the sender that the email was opened, and from where.

- **Renditions are made inside the container,** with no network access.
  Remote resources are never fetched. Their URLs are shown as placeholders,
  since the presence of a remote image may itself be evidence.
- **No scripting:** PDF JavaScript, form actions and embedded files are never
  run or opened. The viewer uses only the rendering library's page rendering.
- **No network:** the viewer has no network code apart from its API socket.
  Clicking an external URL in a document shows the URL and doesn't open it.

## What else the viewer shows

- **A status bar:** a short form of the document ID, custodian, the view in
  use, page *n* of *m*, the printed page label, the Bates number, and the
  position in the sequence.
- **Tags and notes** on the document, and notes anchored to ranges, as
  margin markers. They are read-only here.
- **Family:** the parent document and attachments, as links.
- **Duplicates:** other documents with the same content hash, as links.

## History

The server keeps a log of navigation: each `open`, and each place the user
went, as a locator with a timestamp and whether it came from the user or from
Claude. `view history` gives Claude what it needs for requests like "go back
to the email I was reading before", or "which of these did I already look
at?". The log is session state, separate from the append-only audit log.

## Decisions to confirm

- **Host-side viewer (option A)** with a narrow, scope-checked API, rather
  than a viewer inside the container.
- **MuPDF** for rendering, accepting AGPL for now. PDFium is the alternative
  if a permissive license matters.
- **The GUI toolkit** is still open.
- **Precomputed PDF renditions** for email, .docx and HTML, so the viewer has
  a single page renderer and no HTML engine.
- **Marks as a scratch list,** separate from tags.
- **No authoring of notes or tags in the viewer,** including the user's own.
  The user dictates them to Claude, and each is recorded with the user as its
  author.
