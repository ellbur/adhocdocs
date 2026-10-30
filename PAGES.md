# Literate Pages

*Status: draft.*

A **page** is a piece of written work product, such as a memo, a chronology
or a draft brief section, that quotes and cites documents. Following
Principle 5, the quotations in a page are **references** to document text,
and the renderer fills in the text. Claude writes the prose and chooses what
to quote. The words inside the quotation marks always come from the
document.

What this guarantees: every rendered quotation is the actual text at its
locator, or else it is visibly marked as broken. What it does *not*
guarantee: that the passage supports the point the prose makes. That is still
a matter for human judgment, and it is easy to check because every quotation
can be clicked through to its source.

## Goals

1. **Plain source.** A page is a Markdown file. Claude edits it like any
   other file, and git can version and diff it.
2. **Readable source.** References appear in the prose by short names. The
   long locators are kept at the bottom of the file.
3. **No typed quotations.** The source has no syntax for supplying a
   quotation's text. Elisions and alterations are structured data, and the
   renderer produces their marks.
4. **Fails loudly.** A broken reference is never rendered as text, and an
   export refuses to proceed while any are broken.

## File format

A page is a single `.md` file in three parts:

1. optional YAML front matter
2. Markdown body (CommonMark plus footnotes), with directives for references
3. a fenced `refs` block at the end that defines the references

~~~~markdown
---
title: Chronology of the March shipment
cite-style: default
---

# The shipment

On March 3, Smith wrote that :q{#smith-not-received}. Two days later the
warehouse log showed the pallet as delivered :cite{#log-mar-5}.

Jones's deposition is to the same effect:

::q{#jones-dep-pallet}

The full email chain is [here](ref:smith-email).

```refs
{"id": "smith-not-received", "doc": "sha256:3f9a…", "layer": "sha256:8b21…", "select": {"char": [1200, 1231]}, "quote": {"exact": "the shipment was never received", "prefix": "stated that ", "suffix": ", and that"}}
{"id": "log-mar-5", "doc": "sha256:77c0…", "select": {"page": 2}}
{"id": "jones-dep-pallet", "doc": "sha256:e19b…", "layer": "sha256:0a4d…", "parts": [{"select": {"char": [5120, 5388]}, "quote": {"exact": "…"}}, {"select": {"char": [5610, 5702]}, "quote": {"exact": "…"}}]}
{"id": "smith-email", "doc": "sha256:3f9a…"}
```
~~~~

### Directives

These use the syntax of the
[CommonMark generic directives proposal](https://talk.commonmark.org/t/generic-directives-plugins-syntax/444),
which existing Markdown tooling supports (e.g. `remark-directive`).

| Directive | Placement | Renders as |
|---|---|---|
| `:q{#id}` | inline | “quoted text” plus citation |
| `::q{#id}` | its own line | block quotation plus citation |
| `:cite{#id}` | inline | citation only, with no quotation |
| `[text](ref:id)` | inline | link that opens the viewer at the reference |

Directive options (all optional):

- `:q{#id cite=none}` omits the citation, for example when the next sentence
  cites it. `cite=footnote` puts the citation in a footnote.
- `::q{#id layout=lines}` keeps the source's line breaks. This is the default
  for transcript layers, which show Q/A lines.
- `:cite{#id signal="see"}` sets an introductory signal, which renders in
  italics before the citation.

Directives have no content in brackets (`:q[...]`). There is no way to supply
quotation text.

### The `refs` block

The fenced block labeled `refs` holds one JSON object per line. Each object is
a locator in the JSON form from [LOCATORS.md](LOCATORS.md), plus an `id`.

- `id`: a short name, unique within the page, using lowercase letters,
  digits and hyphens. Claude chooses names that describe the passage, such as
  `smith-not-received`, so the prose stays readable.
- **Single-range references** are ordinary locators.
- **Elided references** use `parts`: an ordered list of selectors, each with
  its own `quote`. All parts are in the same document and layer, in text
  order, and they don't overlap. The renderer joins them with " … ".
- `alter`: a list of alterations (see below).
- References that are only cited or linked don't need a `quote` or `layer`.
  A reference that is quoted must have both, so that the quoted text can be
  checked.

The `refs` block is normally written by tools, for example
`page ref add page.md smith-not-received <locator>`, which takes the quote
fingerprint from the document. Claude may also write references with code.
In either case the renderer checks each reference against the document. A
hand-written fingerprint that doesn't match makes the reference broken; it
can't change the rendered text.

## Alterations

Each alteration is applied by the renderer and shown in the conventional
marked form. Positions are layer offsets, the same coordinates as the
reference's selectors, so they can be checked and they shift with the
reference if it is re-anchored.

| Alteration | JSON | Renders as |
|---|---|---|
| replace | `{"replace": [1200, 1201], "with": "T"}` | `[T]`, replacing the original character or characters |
| insert | `{"insert": 1215, "text": "the vendor"}` | `[the vendor]` |
| sic | `{"sic": 1222}` | `[sic]` after the given offset |
| emphasis | `{"emphasis": [1209, 1214]}` | italics, plus "(emphasis added)" in the citation |
| omit-internal | (implied by `parts`) | ` … ` between parts |
| lead / trail | `{"lead": "…"}` / `{"trail": "…"}` | an ellipsis at the start or end, where the citation style calls for one |

Every alteration is marked. There is no unmarked change to the words, so the
rendered quotation can be read back into its source text by removing the
brackets.

## Display normalization

The layer text is literal. To render a quotation, the renderer applies these
**display rules only**, and nothing else:

- **Inline quotations:** line breaks and form feeds become spaces, and runs
  of whitespace collapse into one space.
- **Block quotations:** the same, unless `layout=lines` is set.
- **Nested quotation marks:** double quotation marks inside an inline
  quotation become single quotation marks, following the usual legal
  convention.

The renderer does **not** remove hyphens at line ends. If "ship-\nment"
should be shown as "shipment", that is recorded as a `join` alteration
(`{"join": 1240}`). It is unbracketed by convention, but it is recorded, and
the verification view shows it.

## Citations

Citations are generated, never typed. The renderer builds each one from:

- the reference's locator (pages, page:line, Bates numbers from segments and
  production records)
- the document's **cite-as** record: a short name such as "Smith Email" or
  "Jones Dep.", or an exhibit number. This attaches to the document ID from
  outside, like tags, and it can change.
- the page's `cite-style`

Examples in the default style:

| Reference | Citation |
|---|---|
| transcript, `pl=14:3,15:7` | Jones Dep. 14:3–15:7 |
| produced document, pages 2–3 | ABC000124–25 |
| unproduced PDF, page 2 | Warehouse Log at 2 |
| email, text range | Smith Email (Mar. 3, 2026) |
| with emphasis | Smith Email (Mar. 3, 2026) (emphasis added) |

Citation styles are small, swappable formatters. A page can name another
style, and Claude can write one.

## Rendering

`page render page.md` produces an HTML view, which is a display in the sense
of Principle 1:

- Quoted text is styled differently from prose. Clicking it opens the viewer
  at the locator, with the range highlighted.
- Each reference shows its resolution status. `exact` shows nothing extra.
  `re-anchored` gets a visible marker. `broken` is rendered as a red
  placeholder naming the reference, never as text.
- Where a quote rests on low-confidence OCR, a marker shows it. Clicking the
  marker shows the page-image region beside the quote.
- There are no editing controls. The user asks Claude for changes. A
  selection in the rendered page, such as a quotation or a paragraph, can be
  read by Claude as the reference ID or the source line range.

## Checking

`page check page.md` resolves every reference and reports in JSON:

```json
{"id": "smith-not-received", "status": "exact"}
{"id": "jones-dep-pallet", "status": "re-anchored", "new": {…}}
{"id": "log-mar-5", "status": "exact"}
{"lint": "unbacked-quote", "line": 14, "text": "“never shipped it”"}
```

Lints:

- **Markdown blockquotes** (lines starting with `>`) in the body are flagged.
  Pages quote documents with `::q`.
- **Long quotations in prose:** text in quotation marks that runs longer than
  a few words is flagged as a possible unbacked quotation. This is a warning,
  not an error, because prose can legitimately contain quotation marks.
- **Unused references and missing IDs** are flagged.

`page refs update page.md` rewrites re-anchored references in place, so they
point at their new locations.

## Export

`page export page.md --format docx|pdf|html` produces a standalone document:

- It **refuses** to export if any reference is broken.
- If any are re-anchored, it reports them, and proceeds only when told to.
- Each quotation is written in as literal text with its citation.
- It writes a **provenance record** next to the output (`<name>.provenance.json`).
  For each quotation, the record lists the reference ID, the locator, the
  rendered text, the alterations applied, and the resolution status.
- The export and its provenance record are hashed and logged in the
  append-only provenance log.

## Where pages live

Pages are work product. They are mutable and are edited over time, so they
are ordinary files in the workspace, versioned with git, and not part of the
object store. Their references point into the object store.

## Decisions to confirm

- **Single file with a trailing `refs` block,** rather than a separate
  `page.refs.jsonl` file. A single file keeps a page self-contained, at the
  cost of a long block at the bottom.
- **Directive syntax** (`:q{#id}`), rather than a custom form like
  `{{q id}}`. Directives are an existing proposal with tooling support.
- **Hyphen joins as explicit alterations,** rather than automatic
  de-hyphenation. Explicit joins are safer, but they mean more work when
  quoting from scanned documents. A tool could suggest the joins.
- **Unbacked-quote lint as a warning only.**
