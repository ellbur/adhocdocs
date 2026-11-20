# Quotation Bench

*Status: built, not yet run against the API: [`tools/quotebench/`](../tools/quotebench)
chooses the passages ([`passages.py`](../tools/quotebench/passages.py), written to
[`tasks.json`](../tools/quotebench/tasks.json)), runs a cell of the grid
([`run.py`](../tools/quotebench/run.py)), checks a memo ([`check.py`](../tools/quotebench/check.py),
[`align.py`](../tools/quotebench/align.py)), prices it ([`cost.py`](../tools/quotebench/cost.py),
[`pricing.toml`](../tools/quotebench/pricing.toml)) and tabulates the runs
([`report.py`](../tools/quotebench/report.py)). Running it needs a Claude API credential.*

Pages make a quotation a reference, so the words a reader sees are the
document's ([PAGES.md](PAGES.md)). This bench measures what that buys: how
often Claude misquotes when it types quotations, how often it does with the
page tool, and what each costs.

It doesn't wait for a lucky slip. Claude Opus copies text in front of it
very well, so the bench builds in the pressure a real memo has: many long
block quotations (48 of them, 80 to 390 words each, about 7,400 words in
all) from long documents that don't fit in one read.

## The grid

|                      | Opus reads | Sonnet subagents read |
|----------------------|------------|-----------------------|
| **no page tool**     | types the quotations | types them from the subagents' reports |
| **page tool**        | `page ref add` | `page ref add` from the subagents' reports |

- **Page tool, or not.** With it, the session is as users have it:
  `page` on PATH, and the session instructions (container/CLAUDE.md) with
  rule 1, never type a quotation into durable work. Without it, no `page`,
  and the same instructions without rule 1 or the page tool's parts
  (`run.claude_md`, checked by a test). Claude then types quotations, as
  anyone would. Everything else is the same.
- **Who reads.** Opus is told to do the reading itself, or to hand it to
  subagents running Sonnet to keep the cost down. Delegating is how a
  reading-heavy job gets cheaper, and it is where quotations pass through a
  second model's report before they reach the memo.

The hypothesis: with the page tool, delegating to Sonnet costs less
without costing accuracy, because a misquoted passage can't become a
reference. `page ref add --words` finds the words in the document or
refuses, so Opus must check against the source before anything reaches the
memo.

The task is the same in every cell ([`prompt.md`](../tools/quotebench/prompt.md)
and `tasks.json`): a memo with a heading per item, each item's passage as a
block quotation exactly as it appears, and a citation by file name. Every
run is on Claude Opus 5.5, with Claude Code's default effort.

## The passages

From the sample matter's long, born-digital public documents, chosen by
`passages.py` with a fixed seed:

- **24 from the FMCSA's property-broker transparency NPRM** (HTML, about
  23,500 words): one paragraph or two, named by section and opening words
  ("Section VI.B.4 ('Cost of Providing Transaction Records'): the paragraph
  beginning 'The proposed rule does not include an automatic disclosure
  provision. FMCSA …'"). The NPRM restates itself, and some openings are
  shared, which the request then lengthens until it is unique.
- **18 from the Federal Rules of Civil Procedure** (PDF, about 65,000
  words): whole subdivisions, paragraphs and subparagraphs by citation, and
  only ones with no page break inside, so no running head or page number
  sits in the middle of a passage.
- **6 sections of Title 9** (HTML, with a PDF copy in the matter too),
  without their notes.

The text has no OCR, no lost ligatures (both Supreme Court opinions in the
matter have them: "confrm", so they're left out), and no page furniture
inside a passage. A word the source has is unambiguous.

The Federal Rules and Title 9 may be in the model's memory. That is part of
what's measured: a quotation from memory that doesn't match the document is
a misquotation of it. The results are kept per document, so the NPRM, which
is unlikely to be memorized, can be read alone.

## What counts as a misquotation

`align.py` compares each quotation with the document word by word. A
quotation is verbatim when its words are a contiguous run of the
document's, apart from what the quoter marked: an ellipsis for an omission,
brackets for an alteration ("[T]he", "[the vendor]"), "[sic]". These differ
from the source without being errors:

- whitespace and line breaks;
- straight or curly quotation marks and apostrophes; dashes;
- a hyphen at the end of a line: joined ("expected"), kept ("ex-pected"),
  or copied with its line break ("ex- pected").

**Word errors** are words dropped, added, changed, or a run out of order,
and they are the headline. Letter case (a capital at the start of a
quotation) and punctuation are counted, but apart. A quotation's
**citation** is right when it names the document the words best match (or
another copy of it). **Coverage** is how much of the requested passage the
quotations reproduce. It says whether the memo did the job, not whether the
words are right: a quotation that stops early, with an ellipsis or without,
is verbatim but incomplete.

Quotations in a memo are found under the item headings: block quotations
(`>` lines), long inline ones ("…" of twelve words or more) if there is no
block quotation, and page references (`::q{#id}`), read the way the
renderer shows them, marks and all. A page reference that doesn't resolve is
**broken**: the renderer shows a red placeholder and export refuses, so the
reader sees a failure, not wrong words. A typed quotation in a page is
checked like any other, so a quotation that went around the tool counts.

## Cost

`run.py` puts a meter between Claude Code and the API: the screenshot
recorder's proxy ([SCREENSHOTS.md](SCREENSHOTS.md)), which holds the
credential and logs each response's model and usage. `cost.py` prices them
at `pricing.toml`'s list prices (input, output, cache reads, cache writes
at 1.25x for 5 minutes and 2x for an hour), by model, so a cell's dollars
split into Opus and Sonnet. Side requests Claude Code makes (titles, command
checks) count too. Claude Code's own estimate (`total_cost_usd` in its JSON
output) is kept beside it as a cross-check.

## Running it

```sh
# the sample matter, indexed (as tools/screenshots makes it)
uv run adhoc init ~/matters/bench
uv run adhoc --matter ~/matters/bench ingest --manifest corpus/manifest.toml
uv run idx --matter ~/matters/bench update

# the passages (tasks.json is committed; this remakes it the same)
uv run python tools/quotebench/passages.py --matter ~/matters/bench

# each cell, as many runs as you'll report (every run is reported)
export ANTHROPIC_API_KEY=…            # or the Workload Identity Federation variables
for tool in no yes; do for d in no sonnet; do
  uv run python tools/quotebench/run.py --matter ~/matters/bench --tool $tool --delegate $d --repeat 3
done; done

uv run python tools/quotebench/report.py --markdown
```

Each run gets a fresh copy of the matter and its own directory under
`tools/quotebench/runs/` (not committed). The directory holds the memo,
what the checker found (`check.json`), every request's usage
(`requests.jsonl`) and its price (`cost.json`), Claude Code's output and
session log, and `result.json`. To look at one memo again:

```sh
uv run python tools/quotebench/check.py runs/RUN/memo.md --matter ~/matters/bench
```

Report every run: a misquotation per run and per quotation, for each cell,
with each error listed (check.json has the source's words, the
quotation's, and where). Quotations within one run aren't independent (a
habit, like copying from a subagent's report, repeats), so the per-run
count, how many memos had any misquotation, is the one a filed brief
cares about.

## Limits

- **Not in the session container.** `run.py` runs Claude Code directly on
  the machine, as `scenario.py --direct` does: the same tools, instructions
  and permissions (no web), but no isolation. Claude could read the
  repository, `tasks.json` included. Its session log shows whether it did.
  A run in the container (`adhoc open --api-socket`, as recorded scenarios
  use) would close this.
- **No viewer.** `view` and `page view` fail in these runs. The task
  doesn't need them.
- **Prices change.** `pricing.toml` says when its prices were read; update
  it before publishing numbers.
