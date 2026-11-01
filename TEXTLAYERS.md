# Text Layers

*Status: implemented for PDF (native and OCR), images, email, .docx, .xlsx,
CSV, HTML and plain text, Outlook .msg, and for zip, mbox and PST containers, in [`extract/`](src/adhocdocs/extract) and
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
- Outlook messages (in a PST, or a .msg file) are converted to RFC 822
  email and extracted as email, so threading, people and productions treat
  them like any other ([`extract/mapi.py`](src/adhocdocs/extract/mapi.py)
  says what the conversion keeps). A PST's messages are child documents
  whose `part` is the folder path and position (`Inbox/Vendors/3`); a .msg
  is itself an `email`. The converted bytes are stored as the child's
  content and are the same each time, so re-ingesting finds the same
  documents. PSTs are read with libpff, through `libpff-python`.
- Containers of any size: every original is put in the store before it
  is read, and a zip, mbox or PST is then read from its stored copy a
  member at a time (`extract_file`). That holds however it arrived, so a
  PST in a zip, or an mbox attached to an email, is streamed like one
  given as a file. A zip's members are streamed into the store without
  being held in memory; a mailbox message is held while it is extracted,
  with its attachments. So what must fit in memory is one message (or
  one non-container file that something extracts, such as a PDF), never a
  container, and never a file nothing can read (kind `other`). A zip member
  that can't be read (a bad CRC, an unsupported compression method) is
  skipped and counted (`unreadable`); the rest of the archive goes in.
- A document's metadata is recorded after its children, so one without
  metadata is unfinished, and re-running an interrupted ingest carries on
  from where it stopped. A container that breaks part-way keeps the
  members read before the break, and its metadata says where
  (`extraction_error`).
- Spreadsheets: one page per sheet (form feeds), so `page=` selects a sheet,
  and `sheet=…&cells=B2:D10` a rectangle of cells;
  cells hold the values Excel last calculated, not formulas.
- A PDF whose pages are mostly lines numbered 1–25 is a transcript. Its
  layer gets `line` segments, and ingestion adds a derived `transcript` view
  without line numbers and page labels, made primary, so phrases read
  across lines.

- A .docx's text outside the body comes after it, each piece separated by
  a blank line: footnotes, endnotes, comments, then headers and footers,
  each a segment of its own kind (`footnote`, `endnote`, `comment`,
  `header-footer`) and the body a `body` segment. So a privilege legend in
  a header, or a comment saying what the author really thought, is found
  by search and can be quoted, and `search --in body` leaves them out.
  Note numbers are in the text where Word shows them; a comment's author
  and date are segment attributes, since they aren't the comment's words.
  [`extract/docx.py`](src/adhocdocs/extract/docx.py) has the details.
- GPL dependencies are fine: the project is open source, and will stay so
  for the foreseeable future (as with MuPDF, see [VIEWER.md](VIEWER.md)).

## Not built yet

Sanitized presentations of the original HTML (presentations are currently
generated from the layer text), and derived views other than OCR made in
the workspace.

.docx, still: text boxes and shapes (`w:txbxContent`), replies to comments
shown as threads (they come through as comments of their own), note
numbering that restarts each section or page, and footnote marks shown as
superscript in the flow presentation (rich quotes have them). Documents
ingested before notes and comments were extracted keep their old layers;
nothing extracts them again yet.

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
