# Text Layers

*Status: implemented for PDF (native and OCR), images, email, .docx, .xlsx,
CSV, HTML and plain text, and for zip and mbox containers, in [`extract/`](src/adhocdocs/extract) and
[`ingest.py`](src/adhocdocs/ingest.py). Formats and per-format conventions
are in those docstrings.*

A **text layer** is one extracted text of a document's content, plus what
relates it back to the original. Search, quoting and highlighting all work
on layers. One content can have several: native text and OCR, two OCR
passes, a .docx with tracked changes accepted and with them shown.

## Goals

1. **Literal.** The text as it appears in the original. Cleanup belongs to
   search or display, never to the layer; literal quotation (Principle 5)
   depends on it.
2. **Plain.** An ordinary UTF-8 file Claude can `grep` or read directly.
3. **Hashed like documents.** A layer object names its content, view and
   part files (text, segments, geometry, presentation, offset map), and its
   ID is the object's hash. It never changes once written.
4. **Relatable to the original.** Geometry ties text ranges to page
   positions, so the viewer can highlight a quote.

## Rules that matter outside the code

- Text is NFC with `\n` line endings. **A form feed is the only page
  break**; extractors turn any form feed in the source into a space, so
  `split('\f')` gives pages.
- Offsets are code points, half-open. Python's `str` indexes the same way.
- **Empty is valid** (extracted, no text). No layer at all means not
  extracted.
- **Segments** (labeled ranges: `header`, `body`, `quoted`, `line` with a
  `page:line` label, `page-label`, `cell` with an A1 `ref`, …) are the one
  way to record structure, so format knowledge stays in extractors.
- **Extractor identity is not layer identity.** Identical output is the same
  layer; extraction runs belong in the provenance log.
- **The primary layer** is a pointer in the provenance log (`set-primary`),
  not part of any identity. Durable locators always name their layer, so
  changing it breaks nothing.
- **Derived layers** (e.g. `transcript`) record `derived_from` and an
  offset map of runs back to the source, so they need no geometry of their
  own.

## Choices made while building

- PDF words on a line are joined with single spaces (PDFs don't record
  spacing reliably). Lines are grouped by position on the page, not by the
  PDF's text blocks.
- A PDF page with almost no text and an image on it is OCRed; the document
  then gets an `ocr` layer as primary, alongside the (near-empty) `native`
  one.
- Zip members and mbox messages are child documents (`part` is the member
  path or message number), so families, custodians and duplicates work as
  for attachments. The container's own layer is empty: extracted, no text.
- Spreadsheets: one page per sheet (form feeds), so `page=` selects a sheet;
  cells hold the values Excel last calculated, not formulas.
- A PDF whose pages are mostly lines numbered 1–25 is a transcript. Its
  layer gets `line` segments, and ingestion adds a derived `transcript` view
  without line numbers and page labels, made primary, so phrases read
  across lines.

## Not built yet

`.msg` and PST (their Python readers are GPL; to decide), .docx footnotes/comments/headers/footers, sanitized
presentations of the original HTML (presentations are currently generated
from the layer text), and derived views other than OCR made in the
workspace.

*Built:* `layers ocr <doc> [--dpi] [--psm]` OCRs a document again into a
new layer in the workspace (the store is read-only in the session). It
joins the workspace change feed, so the indexes pick it up, and searches
reach it with `--layers ocr` or `--layers all`. Making it primary is a
host-side decision, recorded in the provenance log.

## Decisions to confirm

- **Form feeds as the only page signal,** rather than a separate page-start
  list. Plain tools keep working, at the cost of not matching extractor
  output byte for byte.
- **Email header block in the text,** so headers can be searched and
  quoted.
- **Accepted changes as the default .docx view,** with `markup` and
  `original` views whenever there are tracked changes.
