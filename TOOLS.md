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
- **Entities.** *Built*: the entity layer, [ENTITIES.md](ENTITIES.md). A
  list of known people, organizations, defined terms, numbers and the like,
  with a summary and a recap of what Claude last said about each. The
  viewer marks their mentions, shows a card on hover, and selects one on a
  click. It also suggests names found by rules at indexing (`entities`
  index: people, organizations, defined terms with what they mean, document
  numbers). `ann entities`, `ann suggestions`, `ann define-entity`.
- **Saved sets.** *Built*, as list files: `list`, [LISTS.md](LISTS.md).
- **Key sets.** *Built*: the few documents that explain a point, with
  duplicates, quoted emails, near-duplicates and restatements folded under
  them (`list condense`, `cover`, `set`, `uncover`), and points that report
  what nothing supports yet (`list point`, `list points`). `list ls` finds
  them again; `page docs` makes one from a page's citations:
  [LISTS.md](LISTS.md#key-sets).

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
- **A cast of characters** from email headers and from names in document
  text. Addresses whose display names fold to the same name become one
  person. Names in text ("T. Kettering", "Mr. Kettering", "Tom Kettering")
  join a known person, and names with evidence that they are a person (a
  title, an initial, a role beside them, a signature, "By:") that fit no
  one become people of their own, with the roles found beside them and
  every mention as a locator ([`names.py`](src/adhocdocs/names.py) has the
  rules). Rules, not a model: each mention says why it was taken.

Organizations, defined terms and document numbers are detected for the
entity layer ([ENTITIES.md](ENTITIES.md)). *Proposed:* first names alone
("José") and initials ("TK") joined to the person they stand for.

## Review and output

- **Productions.** *Built:* `produce` writes Bates-stamped PDFs (originals'
  pages, or text rendered to pages), a production record, and Concordance
  and Opticon load files ([`produce.py`](src/adhocdocs/produce.py)).
  Families stay together. `bates:` locators resolve through production
  records, and pages cite produced documents by Bates number.
- **Withholding, redaction and privilege logs.** *Built*, from the user's
  tags. A document a person tagged `privileged` becomes a slip sheet.
  Ranges or regions a person tagged `redact…` are redacted, with the text
  beneath removed. Both go in the production's privilege log, with the
  basis taken from tag descriptions and the user's notes.
- **Check for possible privilege.** *Built.* Privilege calls are a
  lawyer's, so Claude can't apply `privileged` or `redact…` tags, even when
  asked. It flags documents and passages instead (`ann flag-privilege
  --basis`, the tag `check-privilege` plus a note giving the reason). Each
  production writes `privilege-check.csv`: every flag on a produced
  document, its basis, and whether a person withheld, redacted or produced
  it. Production goes ahead with flags open and says how many; it is the
  lawyer's list to review before serving, not a privilege log. A
  withhold or redact tag Claude applied earlier is reported as a flag and
  never acted on.
- **Exports of pages** with the provenance of every quotation. *Built*:
  [PAGES.md](PAGES.md).
- **Exhibits.** *Built* (`produce exhibits`): documents stamped as numbered
  exhibits, with a sticker (label, witness, date) on the first page and the
  label on every page. Produced documents are used as produced, so their
  Bates numbers show. The exhibit list comes as CSV, Word and a list file.
  Numbers run on across sets and are never reused. `exh:12#page=3`
  locators resolve to the original's pages, and pages can cite "Ex. 12 at
  3" (`cite-exhibits`). The viewer's status bar shows a page's exhibit.
- *Proposed:* native and TIFF productions.
