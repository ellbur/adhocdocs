# Literate Pages

*Status: built: [`literate.py`](src/adhocdocs/literate.py) has the format
(directives, `refs` block, alterations, `as=`),
[`pages.py`](src/adhocdocs/pages.py) rendering, citations and export,
[`quoting.py`](src/adhocdocs/quoting.py) formatting and pictures of pages,
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
  quotation) and writes the layer and quote fingerprint. A hand-written
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
    hash. Pictures need pages: PDFs and scans, not emails or Word files.
  - **spreadsheet cells** (`sheet=…&cells=…`, a grid selection): a table in
    a block quotation, the cells joined by semicolons inline. The cells'
    text is the quote fingerprint, so a changed cell breaks the reference.

  Formatting and pictures aren't stored with the text layers. They're
  recomputed from the original, so documents ingested before this existed
  get them too.
- **Citations are generated** from the locator, the document's **cite-as**
  name and the page's `cite-style`. Cite-as names live in
  `workspace/cite-as.jsonl`, outside document identity. Without one, a name
  is made from metadata: "Kettering Dep." from a deposition title, "Email
  from A to B" from headers, else the title or file name. The default style
  gives "Kettering Dep. 6:12–14", "HFS000124–25" (for a produced document's
  pages), "Warehouse Log at 2" and "Email from … (Mar. 9, 2026)". A page can name a Python file with its own `cite(info)`.
  Inline citations follow the quotation in parentheses; `cite=footnote`
  and `cite=none` change that.
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

- Clicking a low-confidence marker to see the page image (`as=image` shows
  it in the page instead).
- Formatting for OCR text, transcript views and tracked-change views other
  than the one shown, and paragraph-level formatting (headings, lists,
  alignment). Pictures of documents without pages (emails, Word files).
- A tool that suggests `join`s for scanned text.

## Decided

- A single file with a trailing `refs` block.
- Directive syntax (CommonMark generic directives proposal).
- Hyphen joins as explicit alterations. (Search joins them on its own, in
  [`textnorm.py`](src/adhocdocs/textnorm.py).)
- The unbacked-quote lint as a warning only.
- Cite-as names as a workspace file, and cite styles as Python files.
