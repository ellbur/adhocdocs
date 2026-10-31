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
  .eml with attachments, .docx, CSV, HTML and text: `adhoc ingest`,
  [TEXTLAYERS.md](TEXTLAYERS.md). Originals are never changed; text is a
  derived layer. *Proposed:* archives (zip, PST/mbox), `.msg`, `.xlsx`.
- **Metadata extraction.** *Built* for email headers and file properties.
  *Proposed:* richer fields, cast of characters.

## Integrity

These partly belong under Principle 2: Claude must not be able to undermine
them.

- **Read-only originals.** *Designed*: the store is mounted read-only
  ([CONTAINER.md](CONTAINER.md)).
- **Hashing and provenance.** *Built*: content-addressed store, and every
  change recorded in the provenance log; exact duplicates share a content
  hash.
- **Audit log.** *Proposed.* To defend work product it must be written from
  outside Claude's reach.

## Viewing

- **Document viewer.** *Designed*: [VIEWER.md](VIEWER.md).
- **List display.** *Designed*: [LISTS.md](LISTS.md). Search results are a
  preset of it.
- **Timeline / chronology display.** *Proposed.*

## Search and indexing

- **Indexes and search.** *Built*: `idx`, `search`, [INDEXING.md](INDEXING.md).
  Fields, keyword (Boolean, proximity, exhaustive), semantic (local model),
  hybrid, and grep.

## Annotation

- **Tags and notes.** *Designed*: [ANNOTATIONS.md](ANNOTATIONS.md). Search
  already reads tags.
- **Saved sets.** *Built*, as list files: `list`, [LISTS.md](LISTS.md).

## Writing

- **Literate pages.** *Designed*: [PAGES.md](PAGES.md).
- **Quote checking.** *Built* as `text --json <locator>`, which resolves a
  locator and reports exact, re-anchored or broken. Pages will run it on
  every reference.

## Analysis, review and output

All *proposed*: email threading; near-duplicate and version detection with
a redline; an entity directory; redaction and privilege logs (redactions are
derived documents); export and production (Bates stamping, exhibits,
provenance of every quotation).
