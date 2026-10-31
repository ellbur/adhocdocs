# Tools

The default tools. They don't limit Claude, which can write its own for a
task, and each is meant to be composed and called from code
([DESIGN.md](DESIGN.md)). Status: **built**, **designed** (decided, with a
design doc), or **proposed**.

## Foundations

- **Document IDs and locators.** *Built*: [LOCATORS.md](LOCATORS.md). The
  shared way to name a document or a place in one; most of the "plain data"
  of Principle 4.
- **Ingestion and text extraction.** *Built* for PDF (with OCR), images,
  .eml with attachments, .docx, .xlsx, CSV, HTML, text, and zip and mbox
  containers: `adhoc ingest`, [TEXTLAYERS.md](TEXTLAYERS.md). Originals are
  never changed; text is a derived layer. *Proposed:* PST and `.msg`.
- **Metadata extraction.** *Built* for email headers and file properties.
  *Proposed:* richer fields, cast of characters.

## Integrity

These partly belong under Principle 2: Claude must not be able to undermine
them.

- **Read-only originals.** *Built*: the store is mounted read-only in the
  session container ([CONTAINER.md](CONTAINER.md)).
- **The session container.** *Built* on Linux: `adhoc open <matter>` runs
  Claude Code with the matter's documents only and no network except an
  allowlist proxy ([CONTAINER.md](CONTAINER.md)).
- **Hashing and provenance.** *Built*: content-addressed store, and every
  change recorded in the provenance log; exact duplicates share a content
  hash.
- **Audit log.** *Built*: every session's prompts, tool calls, connections,
  start and end, written on the host outside Claude's reach (`adhoc audit`,
  [CONTAINER.md](CONTAINER.md#design)).

## Viewing

- **Document viewer.** *Built*, a first version (`adhoc view` starts it,
  `view` drives it): [VIEWER.md](VIEWER.md).
- **List display.** *Built*, as list panes with a linked preview (`view
  list`): [LISTS.md](LISTS.md). Search results are shown the same way.
- **Timeline / chronology display.** *Built*, as a pane (`view timeline`):
  a list's items by date, in lanes ([LISTS.md](LISTS.md#the-timeline-display)).

## Search and indexing

- **Indexes and search.** *Built*: `idx`, `search`, [INDEXING.md](INDEXING.md).
  Fields, keyword (Boolean, proximity, exhaustive), semantic (local model),
  hybrid, and grep.

## Annotation

- **Tags and notes.** *Built*: `ann` and the annotation API for Claude, and
  hotkeys, a tag palette and notes in the viewer for the user:
  [ANNOTATIONS.md](ANNOTATIONS.md).
- **Saved sets.** *Built*, as list files: `list`, [LISTS.md](LISTS.md).

## Writing

- **Literate pages.** *Built* (`page`: check, render, export to HTML,
  .docx and PDF; shown in the viewer as a pane): [PAGES.md](PAGES.md).
- **Quote checking.** *Built*: `text --json <locator>` resolves a locator
  and reports exact, re-anchored or broken, and `ann check` checks the
  references and quotations in a note, with the same checker `page check`
  uses.

## Analysis

*Built:* `analyze` ([`analysis.py`](src/adhocdocs/analysis.py) has the
details).
- **Email threading** from Message-ID, In-Reply-To and References. Copies
  from different custodians are one message, and missing parents are
  reported.
- **Near-duplicate and version detection** (MinHash, with similarity and
  containment).
- **A redline** of two documents as locators, or as HTML for a viewer pane.
- **A cast of characters** from email headers. Addresses whose display
  names fold to the same name become one person, with variants, counts,
  dates and, optionally, documents that mention them.

*Proposed:* people found in document text, not only email headers, and
organizations.

## Review and output

- **Productions.** *Built:* `produce` writes Bates-stamped PDFs (originals'
  pages, or text rendered to pages), a production record, and Concordance
  and Opticon load files ([`produce.py`](src/adhocdocs/produce.py)).
  Families stay together. `bates:` locators resolve through production
  records, and pages cite produced documents by Bates number.
- **Withholding, redaction and privilege logs.** *Built*, from tags. A
  document tagged `privileged` becomes a slip sheet. Ranges or regions
  tagged `redact…` are redacted, with the text beneath removed. Both go in
  the production's privilege log, with the basis taken from tag
  descriptions and the user's notes.
- **Exports of pages** with the provenance of every quotation. *Built*:
  [PAGES.md](PAGES.md).
- *Proposed:* exhibit stamping, and native and TIFF productions.
