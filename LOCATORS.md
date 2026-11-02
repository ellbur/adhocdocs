# Locators

*Status: implemented in [`locators.py`](src/adhocdocs/locators.py) (formats,
resolution) and [`objects.py`](src/adhocdocs/objects.py) (hashing). The
docstrings there are the spec; this file keeps the reasons.*

A **locator** names a document, or a place inside one. Search hits, tags,
notes, viewer selections and literate-page references all use them, so they
are the main data tools exchange (Principle 4). Where a standard fits we
borrow it: W3C Web Annotation selectors (position, quote), RFC 5147 `char=`,
PDF `page=`, Media Fragments `xywh=`.

## Goals

1. **Stable:** the same meaning tomorrow, after re-ingestion, on another
   machine.
2. **Checkable:** a tool can tell whether a locator still points at the text
   it was made for, and fails loudly when it doesn't.
3. **Plain:** readable in a terminal, easy to pass as an argument.
4. **One format:** Bates numbers, page:line and printed page labels are
   aliases that resolve to locators, not separate systems.

## Document identity

Identity follows git. Content (the original bytes) and the **document
object** (content hash plus identity facts like custodian, source, parent)
are separate hashed objects; the document ID is the SHA-256 of the object's
RFC 8785 serialization.

- **Which facts count is an ingestion choice.** A matter that ignores
  custodians leaves `custodian` out, and identical files collapse into one
  document. Same file, different choices: different documents, by definition.
- **Only fixed facts.** Tags, notes, layers, Bates numbers and review status
  attach to the ID from outside.
- **Children inherit identity** through `parent` in their object, so the same
  attachment in two custodians' copies of an email has two IDs.
- **Corrections create new IDs,** linked by a `supersede` event; nothing is
  edited in place.
- **Layers belong to content,** so exact duplicates share them.
- Chain of custody (who ingested what, when) is in the provenance log, not
  the ID.

## Resolution

Before showing or relying on a locator, a tool resolves it to **exact**,
**re-anchored** or **broken**: follow `supersede`; use the named layer, or
another layer with the same text hash; otherwise search the primary layer
for the quote (prefix and suffix break ties). Exactly one match is
re-anchored; anything else is broken. A tool never falls back to whatever
text is now at the old offsets.

## Not built yet

- **Aliases:** `ctl:` control numbers wait for control-number records.
  *Built:* `tr:<doc>#pl=14:3,15:7` (transcript page:line, through `line`
  segments), `#label=iv` (printed page labels), and `bates:ABC000123[-…]`
  (through production records; a range spanning documents resolves to a
  list), and `exh:12`, `exh:DX-12`, `exh:12#page=3` (through exhibit sets;
  pages are the exhibit's own, mapped back to the original's).
- `sheet=…&cells=B2:D10` resolves to the rectangle's text (tab-separated,
  one row per line), and its quote is that text; the viewer selects cells
  this way. A quoted page reference can't use cells yet: it needs a char
  range.
- `page=3&xywh=x,y,w,h` (fractions of the page) is a region of a page; it
  resolves to that page, and is checked to lie on it. Pages quote regions
  as images.
- `bytes=a,b` is a range of the original's bytes, for files no extractor
  reads. Its quote is `{"sha256": …}` (plus `"hex"` for ranges up to 64
  bytes), and it never names a layer: the original can't change, so it
  is exact or broken, never re-anchored. Pages quote bytes as hex.
- `ptr=/items/3/amount` (RFC 6901 JSON Pointer, percent-encoded) names a
  value in a JSON or JSON Lines document. Like `pl=`, it is an alias: it
  resolves to the `char` range of the value's text. See [DATA.md](DATA.md).
- `xpath=/orders/order[2]/sku` (a small subset of XPath, percent-encoded)
  names an element's content or an attribute's value in an XML document;
  an alias for its `char` range, like `ptr=`.
- `col=Amount&row=7` names spreadsheet (and CSV) cells by the column's
  header text: an alias for `cells=` (`row=7-9` for several, `col` alone
  for the column below its header, `header=2` if the header isn't row 1).
  The viewer is sent what an alias names (`view open` expands it).

## Decisions to confirm

- **Code-point offsets.** Python counts code points; JavaScript counts UTF-16
  units, so browser-side code needs a conversion.
- **Page-fraction coordinates** rather than PDF points, so images and PDFs
  work alike.
- **Full hashes in the string form.** Long, but self-verifying; tools accept
  8+ hex digit prefixes on input.
