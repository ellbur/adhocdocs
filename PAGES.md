# Literate Pages

*Status: designed, not built.*

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
2. **Readable source:** references appear by short names; locators sit at
   the bottom.
3. **No typed quotations:** there is no syntax for supplying a quotation's
   text. Elisions and alterations are data, and the renderer produces their
   marks.
4. **Fails loudly:** a broken reference never renders as text, and export
   refuses while any are broken.

## Format

Optional YAML front matter, a Markdown body with directives, and a fenced
`refs` block at the end with one JSON locator per line plus an `id`:

~~~~markdown
---
title: Chronology of the March shipment
cite-style: default
---

On March 9, Müller wrote that :q{#muller-not-received}. The carrier's
receipt shows delivery :cite{#pod}.

::q{#kettering-dep}

```refs
{"id": "muller-not-received", "doc": "sha256:3f9a…", "layer": "sha256:8b21…", "select": {"char": [1200, 1231]}, "quote": {"exact": "the shipment was never received", "prefix": "…", "suffix": "…"}}
{"id": "pod", "doc": "sha256:77c0…", "select": {"page": 1}}
{"id": "kettering-dep", "doc": "sha256:e19b…", "layer": "sha256:0a4d…", "parts": [{"select": {"char": [5120, 5388]}, "quote": {…}}, {"select": {"char": [5610, 5702]}, "quote": {…}}]}
```
~~~~

Directives use the CommonMark generic directives proposal (supported by
`remark-directive`):

| Directive | Renders as |
|---|---|
| `:q{#id}` | inline “quotation” plus citation |
| `::q{#id}` (own line) | block quotation plus citation |
| `:cite{#id}` | citation only |
| `[text](ref:id)` | link that opens the viewer there |

Options: `cite=none|footnote`, `layout=lines` (keep line breaks; the default
for transcripts), `signal="see"`.

- References are usually written by tools (`page ref add page.md <id>
  <locator>`, or straight from a search hit's `loc`), which take the quote
  from the layer. A hand-written fingerprint that doesn't match makes the
  reference broken; it can't change the text.
- **Elisions** use `parts`: ranges in one layer, in order, not overlapping,
  joined with " … ".
- Only quoted references need `layer` and `quote`.

## Alterations

Positions are layer offsets, so they can be checked and move with the
reference if it is re-anchored. Every change to the words is marked, so the
quotation reads back into its source by removing the brackets.

| Alteration | JSON | Renders as |
|---|---|---|
| replace | `{"replace": [1200, 1201], "with": "T"}` | `[T]` |
| insert | `{"insert": 1215, "text": "the vendor"}` | `[the vendor]` |
| sic | `{"sic": 1222}` | `[sic]` after the offset |
| emphasis | `{"emphasis": [1209, 1214]}` | italics, "(emphasis added)" |
| lead / trail | `{"lead": "…"}` / `{"trail": "…"}` | ellipsis at start or end |
| join | `{"join": 1240}` | a line-end hyphen removed (unbracketed by convention, but recorded and shown in verification) |

The renderer's only unrecorded changes are display rules: line breaks and
whitespace runs become one space (unless `layout=lines`), and double quotes
inside an inline quotation become single quotes.

## Citations

Generated, never typed, from the locator (pages, page:line, Bates), the
document's **cite-as** record (a short name or exhibit number, attached from
outside like tags), and the page's `cite-style`, a small swappable formatter.
For example, transcript `pl=14:3,15:7` gives "Jones Dep. 14:3–15:7"; a
produced document gives "ABC000124–25".

## Tools

- `page render` produces static HTML for a `page` pane. Quotations are
  `data-text` elements the viewer fills in, styled apart from prose and
  clickable. Re-anchored references get a marker, broken ones a red
  placeholder, and low-confidence OCR a marker that shows the page image.
  There are no editing controls.
- `page check` resolves every reference and lints. It flags Markdown
  blockquotes, quotation marks around more than a few words of prose
  (possible unbacked quotes, a warning only), and unused or missing IDs.
  `page refs update` rewrites re-anchored references.
- `page export --format docx|pdf|html` refuses on broken references, writes
  quotations as literal text, and writes `<name>.provenance.json` with each
  quotation's reference, locator, rendered text, alterations and status.

## Decisions to confirm

- A single file with a trailing `refs` block, not a separate refs file.
- Directive syntax, rather than a custom `{{q id}}`.
- Hyphen joins as explicit alterations. Safer, but more work for scanned
  documents; a tool could suggest them. (Search already joins them, in
  [`textnorm.py`](src/adhocdocs/textnorm.py).)
- The unbacked-quote lint as a warning only.
