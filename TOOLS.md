# Tools

These are the starting tools: the defaults. They do not limit what Claude can
use. Claude can write its own tools for specific situations, and per
[DESIGN.md](DESIGN.md) every default tool is meant to be composed with others
and called from code.

Items are marked **planned** (decided) or **proposed** (suggested, not yet
decided).

## Foundations

### Document IDs and locators — *planned, draft in [LOCATORS.md](LOCATORS.md)*
A shared, stable way to refer to a document and to a location inside it:
page, character range, Bates range, deposition page:line. Search snippets,
tags, notes, selections, literate-page references (Principle 5) and the viewer
all use it. This is most of the "plain, documented data" that Principle 4
says tools communicate through, which is why it comes first.

### Ingestion and text extraction — *proposed; layer format drafted in [TEXTLAYERS.md](TEXTLAYERS.md)*
- Extract text from every supported format, with OCR for scanned documents.
- Unpack containers (zip, PST/mbox, email attachments) and record
  parent/child relationships, e.g. an email and its attachments.
- Derived text is a new artifact linked to the original. The original is never
  changed.

### Metadata extraction — *proposed*
Email headers (from, to, cc, date, message-ID), file metadata, custodian,
source.

## Integrity

These probably belong partly under Principle 2: Claude should not be able to
undermine them.

### Read-only originals — *proposed*
Source documents cannot be changed from inside the container. Redactions,
conversions and OCR output are new derived documents.

### Hashing and provenance — *proposed*
Hash each original when it comes in and record where it came from. This also
gives exact-duplicate detection.

### Audit log — *proposed*
A record of which documents were read, which searches were run, and what was
produced. If the log is to be used to defend work product, it should be
written from outside Claude's reach.

## Viewing

### Document viewer — *planned*
- Formats: PDF, .eml, .docx, and other formats common in legal work.
  Proposed additions: .msg, .xlsx/.csv, images, HTML, plain text, deposition
  transcripts with page:line display; audio/video with transcripts later.
- Can be launched and pointed at a location from code (Principle 3).
- What the user has open, selected or highlighted can be read by Claude
  (Principle 1).

### Document list display — *planned*
Shows a list of documents. Has no search, filter or sort controls; the user
asks Claude for those (Principle 1). Selection can be read by Claude.

### Search results display — *planned*
Shows results with snippets and highlighting. Clicking a snippet opens the
document at that location. That is navigation, which Principle 1 allows.

### Timeline / chronology display — *proposed*
Events tied to source locators.

## Search and indexing

### Indices — *planned*
Several kinds, each usable on its own and from code:
- traditional keyword index
- embeddings
- others as needed

## Annotation

### Tags and notes — *planned*
Attached to documents or to locations within them. Both the user and Claude
can create them, and each records who made it.

### Saved sets / collections — *proposed*
Named, persistent lists of documents ("hot docs", "responsive to RFP 12").
The document list display shows these.

## Writing

### Pages — *planned, format drafted in [PAGES.md](PAGES.md)*
Pages that link to documents. Quotations in pages are literate references
(Principle 5). The renderer shows the text at each referenced range, and
quoted text can be clicked to open the viewer there.

### Quote and citation checking — *proposed*
Given a quote and a locator, confirm the text really appears there. Claude can
run this on its own output, and pages run it on every reference.

## Analysis

All *proposed*:
- **Email threading.**
- **Near-duplicate and version detection**, with a side-by-side redline of
  two versions.
- **Entities.** A cast-of-characters directory of people and organizations,
  with name variants and email addresses resolved to the same person.

## Review and output

All *proposed*:
- **Redaction and privilege log.** Redactions produce derived documents.
- **Export / production.** Bates stamping, exhibit preparation, production
  sets. Exporting a page resolves literate references into text and records
  where each quotation came from.
