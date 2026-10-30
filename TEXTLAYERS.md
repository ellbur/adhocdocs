# Text Layers

*Status: draft.*

A **text layer** is one extracted text of a document's content, plus what is
needed to relate that text back to the original. Search, quoting, snippets
and highlighting all work on layers. [LOCATORS.md](LOCATORS.md) says how
locators point into them.

A single piece of content can have several layers:

- native text and OCR
- a first OCR pass and a later, better one
- a Word document's text with tracked changes accepted, and the same text
  with the changes shown

## Goals

1. **Literal.** A layer records the text as it appears in the original. Any
   cleanup belongs to search or display, not to the layer. Literal quotation
   (Principle 5) depends on this.
2. **Plain.** The text is an ordinary UTF-8 file. Claude can `grep` it, read
   it, or load it in a script without special tools (Principles 3 and 4).
3. **Hashed like documents.** A layer is identified by a hash of everything
   that makes it that layer, as in git, and it never changes once written.
4. **Relatable to the original.** Every range of text can be traced back to
   where it is on the page, where the original has positions. That is what
   lets the viewer highlight a quote.

## Parts of a layer

A layer is made of up to three stored files, tied together by a small layer
object:

| Part | File | Required |
|---|---|---|
| text | UTF-8 plain text | yes |
| segments | JSON Lines: labeled ranges of the text | no |
| geometry | JSON Lines: where ranges of the text sit on the page | no |

### The layer object

```json
{
  "type": "layer",
  "v": 1,
  "content": "sha256:3f9a…c41e",
  "view": "native",
  "text": "sha256:8b21…07fd",
  "segments": "sha256:51c0…e2a9",
  "geometry": "sha256:d4e7…1f30"
}
```

- `content`: the content hash this layer was extracted from. Layers belong to
  content, not to documents, so every copy of the same bytes shares them.
- `view`: which reading of the content this is (see [Views](#views)).
- `text`, `segments`, `geometry`: hashes of the stored part files. If a part
  is absent, its field is left out.

The **layer ID** is the SHA-256 of this object, serialized with the JSON
Canonicalization Scheme (RFC 8785), the same way as document objects.

Some facts are deliberately **not** part of a layer's identity:

- which extractor made it, its version and settings, and when it ran
- whether it is the document's primary layer

Two runs that produce identical output produce the same layer. Extraction
runs are recorded in the append-only provenance log. The primary layer is
recorded by a separate pointer that can change (see
[Primary layer](#primary-layer)).

## The text

- **Encoding and normalization:** UTF-8, Unicode NFC, `\n` line endings.
  Nothing else is normalized. Hyphens at line ends, irregular spacing,
  ligatures and OCR errors are all kept as they appear.
- **Offsets:** counted in Unicode code points from 0, with half-open ranges.
  Python's `open(path).read()` gives a string indexed the same way.
- **Pages:** for content with pages, pages are separated by a form feed
  (`\f`, U+000C). The form feed is the only page signal. Page *n* is the text
  after the *(n−1)*th form feed and before the *n*th. Extractors replace any
  form feeds found in the source text with a space, so a form feed always
  means a page break. A plain-text tool can therefore split pages with
  `split('\f')`.
- **Reading order:** the text is in reading order. Where things physically
  sit on the page is recorded in the geometry.
- **Lines:** line breaks follow the original where it has them, as in PDFs,
  scans and transcripts. Where the original has only paragraphs, as in .docx
  or email bodies, each paragraph is one line.
- **Empty is valid.** An empty text file means the content was extracted and
  has no text, such as a photo with no writing in it. A document with *no*
  layer at all hasn't been extracted.

## Segments

Segments are labeled ranges of the text. They are the general way to record
structure, so that each format doesn't need its own structure schema. Each
line of the file is one segment:

```json
{"start": 0, "end": 42, "kind": "header", "name": "from"}
{"start": 120, "end": 4310, "kind": "body"}
{"start": 2900, "end": 4310, "kind": "quoted"}
{"start": 512, "end": 540, "kind": "line", "label": "14:3"}
```

- `start` and `end` are code-point offsets into the text. Segments may nest
  and overlap.
- `kind` is drawn from a shared vocabulary, which can be extended. Starting
  kinds:

| kind | Used for |
|---|---|
| `header` | an email or document header field; `name` says which |
| `body` | main text of an email or similar |
| `quoted` | quoted earlier messages within an email |
| `signature` | email signature block |
| `paragraph`, `heading`, `list-item` | document structure |
| `table`, `row`, `cell` | tables; `cell` may carry an A1 `ref` |
| `sheet` | a spreadsheet sheet; `name` is its name |
| `footnote`, `comment`, `header-footer` | text outside the main flow |
| `insertion`, `deletion` | tracked changes, in a `markup` view |
| `line` | a numbered line, with `label` `"page:line"` for transcripts |
| `page-label` | the printed page number; `label` is its text |
| `redaction` | an area that was blacked out in the source |

Selector aliases resolve through segments:

- `pl=14:3,15:7` resolves through `line` segments.
- `label=iv` resolves through `page-label` segments.
- `cells=B2:D10` resolves through `cell` segments.

That keeps format-specific knowledge in the extractor, not in every tool that
reads locators.

## Geometry

Geometry links ranges of text to positions on a page, for content with pages.
Each line of the file is one span:

```json
{"start": 1200, "end": 1209, "page": 14, "box": [0.112, 0.304, 0.187, 0.318], "conf": 0.97}
```

- `box` is `[x0, y0, x1, y1]`, measured as fractions of the page width and
  height from the top left, the same coordinates as the `xywh=` selector.
- The extractor chooses the granularity: one span per character, word or
  line. The viewer highlights a range by taking the union of the spans that
  overlap it. If spans are words and the range starts mid-word, the whole
  word is highlighted.
- `conf` is an optional OCR confidence from 0 to 1. The quote checker can
  warn when a quotation rests on low-confidence text.
- Line breaks and form feeds don't need spans of their own.

## Views

A layer's `view` says which reading of the content it gives. Starting views:

| view | Meaning |
|---|---|
| `native` | Text embedded in the file, in the form a reader sees it. For .docx, this means tracked changes accepted. |
| `ocr` | Text recognized from page images. |
| `markup` | Text including tracked changes and comments, marked with `insertion`, `deletion` and `comment` segments. |
| `original` | For a document with tracked changes, the text before the changes were made. |
| `transcript` | Deposition testimony without margin line numbers. Line numbers are recorded as `line` segments. Derived from a `native` or `ocr` layer. |

### Derived layers

Some views are computed from another layer rather than from the content, for
example a `transcript` view that drops the line numbers. A derived layer adds
two fields to its object:

```json
{
  "derived_from": "sha256:<source layer ID>",
  "offset_map": "sha256:<map file>"
}
```

The offset map is a JSON Lines file of runs:

```json
{"start": 0, "end": 512, "source_start": 3}
```

Each run says that the derived text `[start, end)` is a copy of the source
text starting at `source_start`. Text that was dropped from the source has no
run, and text added in the derived layer has no source.

The map is needed because a derived layer does not need geometry of its own.
To highlight a range from the derived layer, the viewer maps it back to the
source layer's offsets and uses the source's geometry.

## Per-format conventions

How each format becomes a layer. These describe what the default extractors
do. Other extractors may make other choices, as long as the result is valid
under the rules above.

- **PDF with a text layer:** a `native` view. Pages are separated by form
  feeds, and geometry is taken from the PDF.
- **Scanned PDF, TIFF and other images:** an `ocr` view, with geometry and
  confidence.
- **Email (.eml, .msg):** a `native` view. It starts with a header block of
  From, To, Cc, Bcc, Date and Subject, one line each as `Name: value`, each
  marked with a `header` segment. A blank line and then the body follow. The
  body is the `text/plain` part if there is one, otherwise the HTML converted
  to text. Quoted replies are marked with `quoted` segments. Attachments are
  separate documents, as covered by the document identity rules.
- **.docx:** a `native` view with tracked changes accepted. A `markup` view
  is added whenever the file contains tracked changes or comments.
  Footnotes, comments, headers and footers come after the main text, each
  with its segment. Neither view has pages. To cite by page, use a rendered
  PDF, which is a separate derived document.
- **.xlsx and .csv:** a `native` view. Each sheet is a `sheet` segment. Rows
  are lines and cells are separated by tabs, and each cell has a `cell`
  segment with its `ref`. The text holds cell values. Formulas can go in a
  later view.
- **HTML:** a `native` view of the rendered text.
- **Deposition transcripts:** a `native` or `ocr` layer of the PDF, plus a
  derived `transcript` view.

## Primary layer

For each content hash, one layer is marked as **primary**. That is the layer
used when a locator doesn't name one, and the default for search and
display.

- The primary pointer is not part of any object's identity.
- It is changed by recording a new pointer in the provenance log. It is never
  edited in place.
- Changing the primary layer never breaks a durable locator, because durable
  locators always name their layer.

## Storage

Everything goes in one object store addressed by hash, laid out like git's:
`objects/<first 2 hex>/<remaining 62 hex>`. The store holds:

- content
- document and layer objects
- text, segment, geometry and offset-map files

Text files are stored uncompressed, so they can be read directly.

Commands for finding things in the store without knowing its layout are a
job for a small CLI, sketched here and to be specified with the tools:

```
layers <doc-or-content>           list layers and their views
text <locator>                    print the text at a locator
text-path <locator>               print the path of the layer's text file
```

## Effect on locators

[LOCATORS.md](LOCATORS.md) defined the layer ID as the hash of the text. With
this design, the layer ID becomes the hash of the layer object. The layer
object contains the text's hash, so locators can still check themselves.

Resolving a locator gets one shortcut. If the locator's layer is missing but
another layer of the same content has the **same text hash**, the offsets are
still exact. This happens, for example, when OCR geometry improved but the
text did not change. The tool uses the new layer and reports the locator as
re-anchored, with the offsets unchanged.

## Decisions to confirm

- **Form feeds in the text** as the only page signal, rather than a separate
  list of where each page starts. Form feeds keep the text usable by plain
  tools, but the text no longer exactly matches the extractor's raw output.
- **Extractor info kept out of layer identity,** so identical output means
  the same layer. The alternative is to include the extractor, which would
  give separate IDs for the same text produced by different tools.
- **Email header block.** Putting headers in the text means they can be
  searched and quoted. The cost is that the text includes lines that aren't in
  the message body.
- **Accepted changes as the default .docx view.** That matches what a reader
  sees in Word with markup hidden. The changes still matter, so the `markup`
  view is always produced when there are any.
