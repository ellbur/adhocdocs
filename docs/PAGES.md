# Literate Pages

*Status: built: [`literate.py`](../src/adhocdocs/literate.py) has the format
(directives, `refs` block, alterations, `as=`),
[`pages.py`](../src/adhocdocs/pages.py) rendering, citations and export,
[`quoting.py`](../src/adhocdocs/quoting.py) formatting and pictures of pages,
and `page --help` the usage. `page view` shows a page in the viewer.*

A **page** is written work product (a memo, a chronology, a draft brief
section) that quotes and cites documents. Following Principle 5, quotations
are **references** to document text, and the renderer fills the text in.
Claude writes the prose and chooses what to quote; the words inside the
quotation marks always come from the document.

This guarantees that every rendered quotation is the text at its locator, or
is visibly broken. It does not guarantee that the passage supports the
prose; that is human judgment, made easy because every quotation clicks
through to its source.

## Goals

1. **Plain source:** a Markdown file, edited like any file, versioned with git.
2. **Readable source:** references appear by short names; locators sit in a
   `refs` block at the bottom.
3. **No typed quotations:** there is no syntax for supplying a quotation's
   text. Elisions and alterations are data, and the renderer produces their
   marks.
4. **Fails loudly:** a broken reference renders as a red placeholder, never
   as text, and export refuses while any are broken.

## How it works

- **References come from the store.** `page ref add page.md <id>
  <locator>...` takes a search hit's `loc` (or several, for an elided
  quotation) and writes the layer and quote fingerprint. `--in <doc>
  --words "…"` finds the locator from the words instead: they must occur
  once in the document's text, matched exactly except for whitespace and
  straight or curly quote marks, so the quotation is still the layer's. A hand-written
  fingerprint that doesn't match makes the reference broken; it can't
  change the text.
- **Every change to the words is marked** (`[T]`, `[the vendor]`, `[sic]`,
  `…`, emphasis noted in the citation), so a quotation reads back into its
  source by removing the marks. A line-end hyphen is joined only by an
  explicit, recorded `join`. Alterations are positioned by layer offsets,
  and they move with their part when a reference is re-anchored.
- **Display rules are the only unrecorded changes:** whitespace runs become
  one space (except `layout=lines`, the default for transcript block
  quotes), and double quotes inside an inline quotation become single.
- **Rendering for the viewer:** each run of document text is a `data-text`
  span naming its exact layer range, which the viewer fills from the store.
  The renderer's own copy sits inside, so the HTML also reads in a browser.
  Marks are separate spans. Quotations link (`loc:`) to their source.
  Re-anchored and low-confidence-OCR quotations get classes for markers.
  A quotation with low-confidence OCR words is followed by a marker,
  `[check page 3]`, that opens it on the page image (`data-mode="page"`),
  to compare the text with what the page shows. Exports leave the marker
  out.
- **Quoting more than words.** A quotation can also be shown as:
  - **formatted text** (`as=rich`): bold, italic, underline and so on.
    The formatting comes from the original at render time: Word run
    properties, HTML tags (emails, web pages), or PDF fonts (approximate,
    since fonts don't record intent). The words are still the layer's, and
    are checked as for plain text; only how they look is added. Export
    records where the formatting came from.
  - **a picture of the page** (`as=image`, block only): the full lines a
    text quotation is on, cropped from MuPDF's rendering (with
    `highlight=yes` marking the quoted words), or a **region reference**
    (`page=…&xywh=…`, an Alt-drag in the viewer). The words behind it are
    still checked; export records each picture's page, rectangle, words and
    hash. Documents without pages of their own (emails, Word files, web
    pages) are pictured as their text set as pages, with its formatting.
    The quoted words are found on those pages by reading them back, and
    the picture is captioned as adhocdocs's rendering, not the original's
    layout (in provenance too). Regions need real pages.
  - **spreadsheet cells** (`sheet=…&cells=…`, a grid selection): a table in
    a block quotation, the cells joined by semicolons inline. The cells'
    text is the quote fingerprint, so a changed cell breaks the reference.
  - **bytes** (`bytes=…`, for files nothing reads): a hex dump in a block,
    hex digits inline (`as=hex`), cited "ledger at bytes 8–31". A value in
    JSON is quoted by its `ptr=` locator, as ordinary text.
- **Interpretations are not quotations.** `:interp{#id}` and
  `::interp{#id}` show Claude's own reading of a file of unknown format or
  schema (a table, tree, value, text or chart made by a recorded decoder),
  captioned as Claude's, never as the document's. A `:q` of one is a
  broken reference, and export lists them apart from quotations
  (`interpretations` in the provenance). See [DATA.md](DATA.md).

  Formatting and pictures aren't stored with the text layers. They're
  recomputed from the original, so documents ingested before this existed
  get them too.
- **What a claim was checked against.** A quotation shows what supports a
  claim. It doesn't show whether anything says otherwise, or, for "the
  collection has no X", what was searched. A **search reference** (`page
  ref search page.md nr '"never received" OR "not received"' --read all
  --against LOC --why "…"`) records:
  - the keyword search;
  - the documents it found;
  - how many of them the author read for contrary statements;
  - the passages judged to go against the claim.

  In the rendered page, `:checked{#nr}` shows a short line at the claim:
  "checked: 21 documents, read all 21: 1 against". It is red when anything
  goes against the claim. After the paragraph comes the search itself:
  - its query and coverage (searched out of all documents, and how many
    have no searchable text);
  - one row per item, with copies folded together;
  - the passages judged against the claim first, each with its why;
  - a snippet of each document's first hit, which opens it.

  The search runs again whenever the page is checked or shown. If it finds
  other documents now (new documents ingested, OCR run), the reference is
  re-anchored until the author reads the new ones and runs `page refs
  update`.

  Two parts of a check don't rest on the author's word:
  - **What was read** comes from the read log (`readlog.py`). `text`
    records every document or passage it shows Claude in
    `workspace/.adhoc/reads.jsonl`. A document counts as read when it was
    shown whole, or in a passage of at least 120 characters overlapping
    one of its hits. A copy of a read document counts too. The line reads
    "18 of 22 read", and adds "(the author says all)" when `--read`
    claimed more. Each row is marked "read" or "not read". Without a log,
    `--read` is shown as "by the author's account".

    A script that runs `text` over many documents and writes them to files
    is a dump, not a reading. In the first trial with the read log, Claude
    dumped 815 documents that way, and every check said "read all". So
    reads that come in a burst (20 or more within a minute) don't count.
    The line says "1 of 22 read, 21 more only in a bulk dump". Reading the
    dumped files afterwards isn't visible to the tools, so the count is a
    floor.
  - **Passages against the claim are proposed by the tools.** `page ref
    search` runs the claim's query as a one-sided dispute
    ([DISPUTES.md](DISPUTES.md)). It keeps the sentences that deny, doubt,
    or report and reject the claim as proposals, each with its stance and
    speaker. The author judges each one with `page ref judge page.md nr
    LOC against|not --why "…"`. Proposals left unjudged turn the line amber
    ("1 proposed as against, not judged"), and `page check` lists them. The
    reader sees what the tools found as well as what the author kept.

  A search reference has to be a keyword search, because a check must be
  exhaustive. Exports show the line, and the provenance file lists every
  document under `checks`.
- **The documents behind a point.** The prose cites one or two documents
  for a point. A **list reference** (`page ref list page.md arrived
  lists/x.set.list.jsonl --point arrived`) holds the rest: a list file, or
  one point of a key set. `:listed{#arrived}` shows a count that opens, in
  the page, to each document with its why and what it covers. This is the
  tip of the iceberg with the rest one click away
  ([LISTS.md](LISTS.md#key-sets)).
- **Citations are generated** from the locator, the document's **cite-as**
  name and the page's `cite-style`. Cite-as names live in
  `workspace/cite-as.jsonl`, outside document identity. Without one, a name
  is made from metadata: "Kettering Dep." from a deposition title, "Email
  from A to B" from headers, else the title or file name. The default style
  gives "Kettering Dep. 6:12–14", "HFS000124–25" (for a produced document's
  pages), "Warehouse Log at 2" and "Email from … (Mar. 9, 2026)". A page can name a Python file with its own `cite(info)`.
  Inline citations follow the quotation in parentheses; `cite=footnote`
  and `cite=none` change that. With `cite-exhibits: true` in the front
  matter (or the name of an exhibit set), a document that became an
  exhibit is cited as one: "Ex. 12 at 3", the exhibit's own page.
- **Checking** (`page check`) resolves every reference and flags Markdown
  blockquotes, quoted prose of five or more words (a possible unbacked
  quotation, a warning only), and missing, unused and duplicate IDs. Claude's
  notes get the same check. `page refs update` rewrites re-anchored
  references.
- **Export** (`page export --format html|docx|pdf`) refuses while anything
  is broken, and while anything is re-anchored unless told otherwise. It
  writes quotations as literal text, plus `<output>.provenance.json` with
  each quotation's locators, rendered text, alterations, status and
  citation. It records an `export` event with both hashes in the workspace
  log.
- **The documents a page cites** (`page docs`), in the order the prose
  first quotes, cites or links them, as JSON or a list file (`--list`)
  that names the page. A page and a key set ([LISTS.md](LISTS.md#key-sets))
  can then describe the same documents.

## Not built yet

- Formatting for OCR text, transcript views and tracked-change views other
  than the one shown, and paragraph-level formatting (headings, lists,
  alignment). Pictures of a Word file or email in its own layout (that
  needs Word, or the sender's mail program).
- A tool that suggests `join`s for scanned text.

## Decided

- A single file with a trailing `refs` block.
- Directive syntax (CommonMark generic directives proposal).
- Hyphen joins as explicit alterations. (Search joins them on its own, in
  [`textnorm.py`](../src/adhocdocs/textnorm.py).)
- The unbacked-quote lint as a warning only.
- Cite-as names as a workspace file, and cite styles as Python files.
