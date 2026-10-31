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
2. **Many documents at once:** panes side by side.
3. **Exact mapping to locators:** anything shown can be selected as a
   locator, and any locator can be shown and highlighted.
4. **Custom displays from Claude:** a grouped list or a checklist, for the
   task at hand.
5. **No content scripts:** no script from a document, or written by Claude,
   ever runs.

## How it is built

A native Rust program on the host, outside the container. Its window is one
`wry` web view running the viewer's own code ([`shell/`](viewer/shell),
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
| `document` | a document in page, flow (its presentation) or text mode, with layer and mode switches, tag chips, a side panel of range tags and notes, and family and duplicate links |
| `list` | a list file ([LISTS.md](LISTS.md)), rendered by `view list` with a linked preview pane |
| `custom` | static HTML from Claude, a file or a bundle; literate pages (`page render`) are shown this way |

Panes are shown as tabs, one at a time or tiled (`split-v`, `split-h`,
`grid`).

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
reads them at the next prompt ("log the ones I ticked").

## State

`view state` reports the layout, panes, and per pane: the document, layer
and view, mode, what's visible (pages or a char range), the sequence
position, the **selection as a JSON locator with its quote fingerprint**
(ready for a `refs` block, tag or note), marks, highlights, form values, and
a list pane's selected rows. `view history` lists where the user went and
whether the user, Claude or a pane went there.

## Not built yet

- `svc:` services (a form submitted to code in the container that answers
  with new HTML). Form values are recorded, but submitting does nothing.
- Multiple top-level windows (panes tile in one window).
- The spreadsheet grid view (sheets show as text), and selecting cells.
- In the status bar: printed page labels and Bates numbers.
- A visible placeholder with the URL for remote images in email (they are
  removed), and `cid:` inline images.
- Shading low-confidence OCR words exists; a marker on literate-page quotes
  that opens the page image does not.
- The 100 ms target hasn't been measured. Pages render on demand at the
  pane's width, and the next documents' metadata is prefetched, but page
  images are not.
- Builds and tests have been on Linux (WebKitGTK) only.

## Decided

- Host-side, native, Rust; MuPDF for paginated documents; one `wry` web view
  for the shell and all HTML.
- No content scripts: static HTML plus declarative attributes, sanitized and
  served with `script-src 'none'`.
- Tags and notes editable in the viewer, written as the user by a host-only
  command.
- Form values as pane state read at the next prompt; `svc:` services later.
- Default list panes built on the same mechanism as Claude's panes.
