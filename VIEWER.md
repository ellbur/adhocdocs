# Document Viewer

*Status: designed, not built.*

The viewer shows documents to the user. Its displays are for viewing,
navigating, pointing and simple input (Principle 1). Everything it shows or
the user points at can be read by Claude, which can drive it from code
(Principle 3). What it can reach is set outside Claude (Principle 2).

## Goals

1. **Fast:** first page visible within 100 ms of a keypress, for flipping
   through hundreds of documents.
2. **Many documents at once:** panes side by side, across windows and
   monitors.
3. **Exact mapping to locators:** anything shown can be selected as a
   locator, and any locator can be shown and highlighted.
4. **Custom displays from Claude:** a grouped list or a checklist, for the
   task at hand.
5. **No content scripts:** no script from a document, or written by Claude,
   ever runs.

## Overview

A **native Rust application on the host**, outside the container, with two
engines: **MuPDF** (`mupdf` crate) for paginated documents, and **`wry` web
views** for everything else (email, .docx, HTML, pages, lists, custom
panes). Any GUI toolkit that can host both is acceptable.

MuPDF is fast and covers PDF, XPS, EPUB and images including multi-page
TIFF. It is AGPL, which makes the viewer AGPL, fine while the project is
open source. The alternatives are PDFium (BSD/Apache, PDF only) and Poppler
(GPL, slower). Rendering sits behind a small interface so it can be
swapped.

## Isolation

- **Scope comes from outside:** the viewer learns which documents are
  connected from the connect mechanism, never from Claude, and refuses any
  locator outside it, from the API and from every pane.
- **A narrow API:** Claude reaches it through an endpoint the connect
  mechanism provides. It accepts locators and static HTML, returns state,
  and can't open paths or change scope.
- **No network** other than that API, and **one instance per session,**
  launched by the connect operation, never by Claude.

## Panes

One display server has windows, split into tiled panes:

| Pane | Engine | Shows |
|---|---|---|
| `document` | MuPDF or web view | a document in page, flow or text mode |
| `page` | web view | a rendered literate page ([PAGES.md](PAGES.md)) |
| `list` | web view | a list ([LISTS.md](LISTS.md)); search results are a preset of it |
| `custom` | web view | a display built by Claude |

Every web pane is static HTML made in the container plus the declarative
attributes below. The default list pane has no special powers, so Claude can
replace it for a task.

## Showing documents

Everything shown maps back to **layer offsets**.

- **Page mode (MuPDF):** PDFs, scans, images, transcripts. An invisible text
  overlay is built from the layer's geometry, not MuPDF's own text, so
  selections and highlights agree with the quote checker. Low-confidence OCR
  can be shaded. Dragging a box gives a `page` plus `xywh` locator.
- **Flow mode (web view):** email, .docx, HTML, shown from the layer's
  presentation, whose text spans carry `data-o` offsets. Selections give
  `char` locators.
- **Text mode:** the layer text with its segments; the fallback for any
  layer.
- **Spreadsheets:** a grid from `cell` segments; selections give `sheet` and
  `cells`.

## Thin panes

Claude acts when prompted; it can't react to clicks. So panes, even ones
Claude builds, **display things and collect simple input**. Logic stays in
the container. Rapid things that need no thought (open, step, point) are
handled by the viewer's native code.

**No scripts.** Every pane is sanitized on arrival (scripts, handlers,
`javascript:` URLs, iframes, objects, embeds, remote resources removed) and
served with `script-src 'none'`. The only code that runs is the viewer's
own, injected through the web view's init-script mechanism. (Whether that
works under `script-src 'none'` on each platform engine is to be checked.)

**Declarative attributes**, carried out by native code:

| Markup | Behavior |
|---|---|
| `<a href="loc:…">` | open the locator |
| `data-locator="…"` | pointable: click selects (shift/ctrl for several), Enter opens, mark and tag keys apply; a linked document pane follows |
| `data-sequence` | its items become the linked pane's next/previous sequence |
| `<span data-text="…">` | the viewer inserts the text at the locator, as text, never HTML |
| `<span data-snippet="…" data-context="80">` | a snippet with the range highlighted |
| `data-status="…"` | a resolution marker |
| `data-tags="…"`, `data-notes="…"` | live tags and note counts |

**Input:** plain form controls. Their values are pane state that `view
state` reports, so Claude reads them at the next prompt ("log the ones I
ticked"). A form with `action="svc:<name>"` submits to a service Claude
registered in the container, which replies with new HTML, for an immediate
response without Claude in the loop.

## The `view` CLI

Inside the container, a thin wrapper over the API, with JSON output:
`open <locator>`, `highlight`, `clear-highlights`, `sequence`, `pane
--new|--update <html|bundle>`, `list`, `service`, `close`, `layout`,
`state`, `selection`, `history`. If the viewer isn't running, the command
says so; Claude can't start it.

`view state` reports windows and panes. For each pane it gives the document,
layer and view, `visible` (pages or a char range), the sequence position,
the `selection` as a full JSON locator (ready for a `refs` block, tag or
note), marks, highlights and form values.

## What the user can do

Navigate, switch layer view or mode, select (text, region, cells, pointable
items), **mark** (a session scratch list for "these three passages"), tag
and annotate ([ANNOTATIONS.md](ANNOTATIONS.md)), fill in forms, and arrange
panes. There are no search, filter or edit controls, and no export or
open-elsewhere, which is an isolation operation. The keyboard comes first.

A status bar shows the short ID, custodian, view, position, printed page
label, Bates number and sequence position. Margin markers show tags and
notes, and there are links to family and duplicates. Navigation history is
logged (`view history`: "go back to the email I was reading").

## Safety

No content scripts anywhere. Presentations are sanitized twice (at ingestion
and on display). Remote resources are never fetched: a remote image becomes
a placeholder showing its URL, since its presence may be evidence. Document
text is always inserted as text. PDF JavaScript, form actions and embedded
files never run. Every locator in every attribute and call is checked
against scope.

## Decided

- Host-side, native, Rust; MuPDF plus `wry`; any toolkit hosting both.
- No content scripts; static HTML plus declarative attributes.
- Tags and notes editable in the viewer.

## Decisions to confirm

- Form values as pane state read at the next prompt, plus optional `svc:`
  services.
- Default list and results panes built on the same mechanism as custom
  panes.
