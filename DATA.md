# Unknown Formats and Schemas

*Status: a first version is built: [`raw.py`](src/adhocdocs/raw.py) (bytes),
[`structured.py`](src/adhocdocs/structured.py) (JSON pointers and schemas),
[`interp.py`](src/adhocdocs/interp.py) (interpretations), the `raw` and
`interp` CLIs (`--help` has the usage), and `:interp` in pages
([PAGES.md](PAGES.md)).*

Collections hold files nothing was built to read: a proprietary call-log
export, a device's binary dump, a database, a JSON export from a system
whose fields no one has documented. They are evidence, so they belong in
the matter whether or not anything can read them. Claude can often work out
what they are. The design question is how to use what Claude works out
without letting it pass for what the document says (Principle 5).

## Three kinds of reading

| | What it is | Quotable? | How it's shown |
|---|---|---|---|
| **Text** | a text layer: the document's own words | yes | quotations (`:q`) |
| **Bytes and values** | an original's bytes, or a JSON value's own text | yes | hex (`as=hex`), or text |
| **Interpretation** | what Claude thinks bytes or fields mean | **never** | `:interp`, captioned as Claude's reading |

### Bytes

An original is stored exactly as given and never changes, so a range of
its bytes is as fixed as text in a layer. A **byte locator**
(`doc:<id>#bytes=0,16`) names one. Its fingerprint is the bytes' SHA-256,
plus their hex for short ranges, so a superseded or mistaken document
can't quietly show other bytes. Bytes are shown as a hex dump (offset,
sixteen bytes in hex, the printable ASCII among them): a fixed display
rule like the whitespace rule for text, and the only official reading of
an unknown format.

`raw info` says what can be told without knowing the format: size, hash,
the signature its first bytes match (SQLite, gzip, Parquet, EVTX, …),
entropy (near 8 bits a byte: compressed or encrypted), how much looks like
text, and whether text is JSON, JSON Lines or XML. `raw strings` finds runs
of ASCII, UTF-8 and UTF-16 text, each with its byte locator, and `raw hex`
dumps a range. Ingest records the signature in the document's metadata
(`signature`). `search 'kind:other'` lists every file nothing reads, and
`kind:json`, `kind:ndjson` and `kind:xml` the structured ones.

### Values in structured data

JSON, JSON Lines and XML are recognized at ingest (kinds `json`, `ndjson`,
`xml`) and keep their literal text as their layer, like any text file, plus
what the syntax says (`valid`, `shape`, `records`). Their syntax is
standard even when their schema isn't, so:

- **`ptr=` locators** (RFC 6901 JSON Pointer: `doc:<id>#ptr=/items/3/amount`)
  name a value by its path and resolve to that value's text in the layer.
  A value quoted by pointer is quoted verbatim, escapes and all, because it
  *is* the layer's text. A string's range is inside its quote marks. In
  JSON Lines the first step is the record's number, from 0. A key that
  appears twice in an object is an error, not a guess at which one.
- **`raw schema`** lists every path in the data (`/items/*/amount`, with
  `*` for any index), the types and counts found there, how many of its
  parent objects have it, and examples, each with its `ptr=` locator.
  XML gets element and attribute paths with counts and examples, but no
  pointers yet.

### Interpretations

Working out that bytes 0x40–0x80 are a table of little-endian timestamps,
or that `/r/7/t` is a sender, is **interpretation**: often right, useful,
and never the document's words. Claude records one by writing a
**decoder** and running it with `interp run`:

```sh
interp run <doc> --name call-log --title "Calls decoded from calls.cdr" \
    --about "Fixed 48-byte records; field meanings inferred from …; fairly sure" \
    -- python3 decoders/cdr.py
```

The decoder gets the first original on stdin (and every input's path in
`$ADHOC_INPUT`/`$ADHOC_INPUTS`) and prints its reading: JSON (a table, a
tree or a value), CSV, text, or a chart as SVG or PNG. The result goes in
`workspace/interpretations/<name>/`:

- `record.json`: the title, Claude's account of what the file is and how
  sure it is (`about`), the inputs (document and content hash), the decoder
  as run, and the output's type and hash.
- `decoder/`: copies of the files on the decoder's command line, with their
  hashes. The decoder is run from the copies, so the record says exactly
  what produced the output, even after Claude edits its script.
- `output.*`: what the decoder printed.

`interp check` confirms the inputs are still there with the same content,
that nothing in the folder has changed, and, run again, that the decoder
gives the same output. So an interpretation is checkable *as an
interpretation*: anyone can see what made it and make it again. That is
not the same as its being right.

**Evidence links.** An object in JSON output with `"_bytes": [a, b]` (a
range of the first input), `"_ptr"` (a pointer into it) or `"_loc"` (any
locator) links to what it was read from. Tables show these as a source
column, and the bytes or value behind a row can be quoted. This is how an
interpretation earns trust: each decoded record points at bytes anyone can
check.

**Searching them.** `interp search` runs the query language of `search`
(words, phrases, prefixes, Boolean, `w/N`, `/s`, `/p`) over the outputs,
exhaustively. JSON is searched value by value (keys aren't), and `/p`
means "in the same record". Each hit names the reading, where in its output
(a pointer and its record, a CSV row and column, or a text line), and the
evidence locator its record gives, if any. Hits are marked
`"unofficial": true`: they are places in Claude's readings, so what gets
quoted is the evidence, not the hit. Field filters (`from:`, `tag:`) are
refused: they describe documents. Pictures aren't searched, and the
coverage says how many were left out. It is a separate search on purpose,
so a reading never turns up among `search`'s results.

**Showing them.** `interp view` puts one in a viewer pane and `interp
render` writes HTML: a table, a tree (collapsible, no scripts), text, a
value or a picture. Every rendering is a captioned figure: *Interpretation
by Claude, not the document's own content*, with what it decoded, the
decoder's hash and the output's hash.

## In pages

```markdown
The file begins with the bytes :q{#magic}, which match no known format.
Read as records, the largest payment went to :interp{#top-party}.

::interp{#calls as=table}
```

- `:interp{#id}` shows a value inline, set apart and followed by "(Claude's
  reading of …)"; `::interp{#id}` shows a captioned figure (`as=table`,
  `tree`, `value`, `text` or `image`).
- A reference to an interpretation names it and its output's hash (`page
  ref add page.md calls --interp call-log --ptr /calls`). If the
  interpretation is made again with different output, the reference
  breaks, so a page never silently shows a reading it wasn't written
  about.
- **A `:q` of an interpretation is a broken reference**, and so is an
  `:interp` of document text. The lint can't make Claude's prose accurate,
  but it stops a reading from being set as a quotation.
- Export writes interpretations as labeled text, tables or pictures, and
  the provenance file lists them under `interpretations`, apart from
  `quotations`, each marked `"unofficial": true` with its inputs, decoder
  and output hashes. Word can't hold SVG, so a chart for a Word export
  should be PNG.

## Decided

- **Interpretations are not text layers.** Layers are literal
  ([TEXTLAYERS.md](TEXTLAYERS.md)), and search hits and quotations come
  from layers. Keeping interpretations out of the store and the indexes
  means no hit or quotation can come from one by accident. They are
  searched separately (`interp search`), with every hit labeled as a
  reading and pointing at its evidence. Nothing stops Claude building its
  own index of them for a task (Principle 3), as long as it says it is
  searching its readings.
- **`interp search` scans at query time** rather than keeping an index:
  outputs are usually small, so it is exhaustive and never stale. A very
  large decoded output would want a cached index like `keyword`'s.
- **Decoders are Claude's code, run in the container,** like any other
  script Claude writes. Nothing about them widens access (Principle 2).
- **Hex is the display rule for bytes.** It is lossless and conventional.
  A rendering in some other form (decoded text, a picture) is an
  interpretation.
- **Pointers resolve to text,** not to decoded values. `"Café"` quotes
  as written. Decoding the escape would be a change to the words, and
  pages record every change.
- **Recognizing JSON changes the kind** (`json` rather than `text`) for
  documents ingested from now on. Pointers also work on documents ingested
  earlier as `text`, by checking the text.

## Not built yet

- Semantic search of interpretations, and showing `interp search` hits in
  a list pane.
- Pointers into XML (an XPath subset), and pointers into CSV and
  spreadsheets by header name rather than cell.
- Interpretations travelling with published pages: publish the
  interpretation's folder alongside the page (`work publish`) for now.
- A hex mode in the viewer's document pane. Byte locators open the
  document, but the viewer doesn't scroll to or highlight the bytes. For
  now, Claude shows bytes with `raw hex`, in pages, or in a pane it builds.
- A `signature:` search field (the signature is in metadata, not yet in
  the fields index).
- Built-in readers for common structured binaries (SQLite, Parquet, EVTX).
  Claude can decode them itself with an interpretation. Formats used often
  enough to deserve an official reader become extractors, with real layers.
