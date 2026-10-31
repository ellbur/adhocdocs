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
- **Audit log.** *Proposed.* To defend work product it must be written from
  outside Claude's reach.

## Viewing

- **Document viewer.** *Built*, a first version (`adhoc view` starts it,
  `view` drives it): [VIEWER.md](VIEWER.md).
- **List display.** *Built*, as list panes with a linked preview (`view
  list`): [LISTS.md](LISTS.md). Search results are shown the same way.
- **Timeline / chronology display.** *Proposed.*

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

*Proposed:* an entity directory (a cast of characters, with name variants
and addresses resolved to one person).

## Review and output

All *proposed*: redaction and privilege logs (redactions are derived
documents); export and production (Bates stamping, exhibits, provenance of
every quotation).
