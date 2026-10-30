# Locator Format

*Status: draft.*

A **locator** names a document, or a place inside one. Every tool that refers
to document content uses locators:

- search results
- tags and notes
- viewer selections
- the viewer's "open at" argument
- literate-page references ([DESIGN.md](DESIGN.md), Principle 5)

It is the main piece of plain, documented data that tools exchange
(Principle 4).

Where an existing standard fits, this format borrows from it:

- **W3C Web Annotation selectors:** position, quote, and selectors that refine
  other selectors.
- **RFC 5147:** `char=` fragments for plain text.
- **PDF open parameters:** `page=`.
- **W3C Media Fragments:** `xywh=` and `t=`.

## Goals

1. **Stable.** A locator means the same thing tomorrow, after re-ingestion,
   and on another machine.
2. **Checkable.** A tool can tell whether a locator still points at the text
   it was made for, and it fails loudly when it doesn't.
3. **Plain.** It is readable in a terminal, easy to embed in a page, and easy
   to pass as a command-line argument.
4. **One format for everything.** Bates numbers, deposition page:line, and
   printed page labels are aliases that resolve to locators. They are not
   separate systems.

## Document identity

A document's ID is the **SHA-256 of its original bytes**, written
`sha256:<64 lowercase hex>`.

- **Verifiable:** anyone can re-hash the original to confirm which file is
  meant. This supports the integrity tools.
- **No central counter:** IDs don't depend on ingestion order and stay the
  same if documents are ingested again.
- **Duplicates:** byte-identical files share an ID. Where each copy came from
  (custodian, source path, date collected) is kept in separate provenance
  records, and one document can have many.
- **Attachments and archive members** are documents in their own right, each
  hashed from its decoded bytes. A family relation links them to their parent.
- **Short forms:** tools accept a unique prefix (at least 8 hex digits) as
  input, like git does, and always write the full hash. Displays may show the
  short form.

Human-facing identifiers are **aliases** that resolve to a document ID:
control numbers, Bates numbers, and file names.

## Text layers

Character positions only make sense relative to a particular extracted text.
A document can have several, such as native text, OCR, or a later and better
OCR. Each is a **text layer**.

- **Layer ID:** the SHA-256 of the layer's text, after normalization, encoded
  as UTF-8. Because the ID is a hash of the text, a locator that names a layer
  can check itself: if the layer hashes to its ID, the offsets are exact.
- **Normalization:** Unicode NFC and `\n` line endings, nothing else. Changes
  such as de-hyphenation or whitespace collapsing would make it harder to
  match the original, so they are not part of the layer. Search may normalize
  further, internally.
- **Offsets:** counted in Unicode code points, starting at 0. Ranges are
  half-open, `[start, end)`.
- **Page boundaries:** for documents with pages, the layer records the offset
  where each page starts. That lets page selectors and text selectors convert
  into each other.
- **Highlight boxes:** the extraction tool may record the bounding box of each
  character. That is what lets the viewer highlight a text range on a page.

## Selectors

A selector picks out part of a document. If a locator has no selector, it
means the whole document.

| Selector | String form | Meaning |
|---|---|---|
| page | `page=14` or `page=14-16` | Physical page index in the file, starting at 1. |
| char | `char=1200,1340` | Text range `[1200, 1340)` in the layer. |
| region | `page=3&xywh=0.10,0.20,0.50,0.05` | Rectangle on a page. Coordinates are fractions of page width and height, measured from the top left. |
| cells | `sheet=Q3&cells=B2:D10` | Spreadsheet cells, in A1 notation. |
| time | `t=95.0,120.5` | Seconds into audio or video. *(Later.)* |

Notes:

- **Pages:** only fixed-layout formats have pages: PDF, TIFF and other
  images. A .docx or .eml has no stable pages. To cite pages in one, cite a
  derived PDF rendering of it.
- **Page selectors are not printed page numbers.** `page=` is always the
  physical index. A printed label like "iv" or "Page 12 of 40" is an alias.
- **Refinement:** when a page selector and a char selector appear together,
  the char offsets are still offsets into the whole layer, not into the page.
  The page part acts as a hint and a consistency check.

## String form

```
doc:<doc-id>[@<layer-id>][#<selector>]
```

Examples:

```
doc:sha256:3f9a…c41e                                  whole document
doc:sha256:3f9a…c41e#page=14                          page 14
doc:sha256:3f9a…c41e@sha256:8b21…07fd#char=1200,1340  text range in a given layer
doc:sha256:3f9a…c41e#page=3&xywh=0.1,0.2,0.5,0.05     region on page 3
```

The string form is what the command line takes, what tools print, and what is
used for navigation. It does not carry a quote, so it cannot re-anchor itself
(see below).

**Layer omitted:** a `char=` locator without a layer means "the document's
current primary layer". That is fine for interactive use, but it isn't stable.
Tools that write durable locators always include the layer.

## JSON form

The full form, used in pages, tags, notes, and wherever durability matters:

```json
{
  "doc": "sha256:3f9a…c41e",
  "layer": "sha256:8b21…07fd",
  "select": { "char": [1200, 1340] },
  "quote": {
    "exact": "the shipment was never received",
    "prefix": "stated that ",
    "suffix": ", and that"
  }
}
```

Each selector from the table above becomes a key in `select`. Examples:

- `{"page": [14, 16]}`
- `{"page": 3, "xywh": [0.1, 0.2, 0.5, 0.05]}`
- `{"sheet": "Q3", "cells": "B2:D10"}`

`quote` is the **fingerprint** from Principle 5. It works like the W3C
TextQuoteSelector. The tool that creates the locator from the document text
fills it in. Claude never types it.

## Resolving and checking

Before showing or relying on a locator, a tool resolves it:

1. Find the document by `doc`. If it isn't there, that is an error.
2. Find the layer by `layer`.
   - If the layer is present, read the text at the offsets and compare it with
     `quote.exact`. If they match, the locator is **exact**. If they don't,
     that is an error, because the data is inconsistent.
   - If the layer is missing (for example, the document was re-extracted),
     search the current layer for `quote.exact`, using `prefix` and `suffix`
     to choose between multiple matches.
     - Exactly one match: the locator is **re-anchored**. The tool reports the
       new locator, and can rewrite the old one if asked.
     - No match, or more than one: the locator is **broken**. Show this
       clearly. Never fall back to showing whatever text is now at the old
       offsets.
3. A locator without a quote can only be exact or broken.

Every resolution reports its status: `exact`, `re-anchored`, or `broken`. The
literate-page renderer and the quote checker show that status to the user.

## Aliases

Aliases are input conveniences. A resolver turns each one into one or more
canonical locators. Tools store the canonical form and generate the citation
shown to the user (Bates, page:line, and so on) from it at display time.

| Alias | Example | Resolves via |
|---|---|---|
| Bates | `bates:ABC000123` or `bates:ABC000123-ABC000130` | Production load file or Bates-stamping record. |
| Transcript | `tr:<doc-id>#pl=14:3,15:7` | The transcript's page:line structure in its layer. |
| Control number | `ctl:SMITH-0042` | Ingestion records. |
| Printed page | `doc:<id>#label=iv` | PDF page labels, or a detected page footer. |

A Bates range can span several documents. In that case it resolves to a
*list* of locators, because a single locator always refers to one document.

## Multi-part quotations (sketch)

A literate quotation with elisions is an ordered list of locators in the same
document. The renderer joins the parts and inserts "…" between them.
Alterations, such as bracketed insertions or *[sic]*, are separate
annotations on the quotation, not changes to the locators. The exact structure
belongs to the pages spec.

## Decisions to confirm

- **Content IDs, not instance IDs.** With hash-based IDs, a tag on a document
  applies to every byte-identical copy. If tags need to apply to a single copy
  (e.g. "Smith's copy only"), we would add an instance ID on top.
- **Code-point offsets.** Python counts strings in code points, but JavaScript
  counts in UTF-16 units, so browser-side tools need a conversion.
- **Coordinates as page fractions,** rather than PDF points. Fractions work
  the same way for images and PDFs.
- **Hashes in the string form.** Hashes make locators long. The alternative
  is short local IDs, which would give up self-verification.
