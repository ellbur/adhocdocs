# Quotation Bench

*Status: built: [`tools/quotebench/`](../tools/quotebench)
chooses the passages ([`passages.py`](../tools/quotebench/passages.py), written to
[`tasks.json`](../tools/quotebench/tasks.json)), runs a cell of the grid
([`run.py`](../tools/quotebench/run.py)), checks a memo ([`check.py`](../tools/quotebench/check.py),
[`align.py`](../tools/quotebench/align.py)), prices it ([`cost.py`](../tools/quotebench/cost.py),
[`pricing.toml`](../tools/quotebench/pricing.toml)) and tabulates the runs
([`report.py`](../tools/quotebench/report.py)). `run.py` needs a Claude API credential. The first
run, one cloud session per cell driven by another session, is in
[`results/2026-11-20/`](../tools/quotebench/results/2026-11-20): no misquotation in any cell.*

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

## What the first run changed

In the first run ([`results/2026-11-20/`](../tools/quotebench/results/2026-11-20)),
neither tool cell used `page ref add --words`, which means typing the passage
out. Both wrote scripts against the store to find where paragraphs and rule
subdivisions end, built locators by hand, added the 48 references in a loop,
and worked around the default citations. That was about three quarters of
their effort. So `page ref add` now takes roughly where a passage starts
(`--from`) and how far it goes (`--to`, `--paras`, `--item`, `--section`),
looks first in what Claude just read, and shows what it found
([PAGES.md](PAGES.md), [`extent.py`](../src/adhocdocs/extent.py)). On this
bench's 48 requests, one `--batch` call with each request's opening words gets
47 exact passages and asks about the 48th: "(2) With Leave." is in Rule 30 and
in Rule 31, and the candidates say which rule each is under. Citations got
`cite-style: filename`, and a PDF page without a printed number is cited "at
PDF p. N".


## Results so far

Three tasks, four cells each, one run per cell, all on Claude Opus 5.5 in
Claude Code cloud sessions driven by another session (`create_session`,
`send_message`) from branches holding the sample matter without the bench.
Each results directory has the work products, the checker's findings and a
README.

| task | quotations per memo | wrong words, all cells | cheapest cell |
|---|---|---|---|
| [48 long block quotations](../tools/quotebench/results/2026-11-20) | 48 | 0 | no tool, Opus alone ($1.77) |
| [a chronology of the dispute](../tools/quotebench/results/2026-11-20-chronology) | 70–124 | 0 | no tool, Opus alone ($2.42) |
| [every deadline in the FRCP and Title 9](../tools/quotebench/results/2026-11-20-deadlines) | 101–486 | 0 | **page tool**, Opus alone ($2.51) |

- **Opus 5.5 doesn't misquote here**, typed or not: 0 wrong words in some
  1,100 typed quotations. When asked for exact quotations it extracts them
  with scripts; otherwise it types them, from what it read, and they still
  come out right. It can overstate what it checked.
- **So the page tool's value is that nobody has to trust that**: without it,
  a third of a chronology's quotations had no adjacent citation or came
  from a scan read by eye; with it, every quotation is the document's text
  at a recorded range, checked by `page check`.
- **The tool's overhead is now small.** It was re-reading passages (nothing
  said they were checked), big previews and help, rendering to see
  citations, and instructions asking for extra checks. Since `--from`,
  ranges from scripts, quiet batches and "done when `page check` passes",
  the tool cell was the cheapest on the deadlines task.
- **Delegating reading to Sonnet hasn't saved money yet**: the lead's large
  fixed context, its handback turns and its review of what came back cost
  as much as reading alone, and Sonnet adds its own. With the `reader`
  subagent (checked references with notes, a short reply) the handoff
  works, but the delegated leads also took on more scope, so the deadline
  costs aren't like for like.

## Next: a large FTC docket

A stress test on a real collection: about 500 quotations, from thousands of
pages, for a question that needs reading rather than a keyword search.

- **The docket:** *In the Matter of Benco Dental Supply Co., Henry Schein,
  Inc. and Patterson Companies, Inc.*, FTC Docket No. 9379: an alleged
  agreement among the three largest dental distributors to refuse to deal
  with buying groups. The ALJ found Benco and Patterson liable and dismissed
  the complaint against Schein (Initial Decision, October 2019). Public: the
  case page's filings (complaint, motions, orders, the Commission's opinion
  on summary decision, post-trial briefs, the Initial Decision) and the
  trial transcripts listed on the FTC's transcripts page.
- **The question:** for each distributor, what evidence complaint counsel
  relied on to show it joined the agreement, how that company explained the
  same conduct as its own independent decision, and how the ALJ weighed each
  piece (and why it came out differently for Schein). Answering it means
  following the same emails through both sides' briefs, the testimony and
  the decision, quoting each link.
- **Getting it:** it has been fetched (Nov 2026) and is kept, with its OCR
  and its passages' embeddings, in a public bucket,
  `gs://ellbur-adhocdocs-corpora/ftc/` (about 430 MB), so anyone can
  replicate the runs; no account or gcloud is needed. To make it a matter,
  ready to search:

  ```sh
  sh tools/corpus/ftc_matter.sh                    # matter/ here, or MATTER=<dir>
  ```

  That pulls it into `corpus-ftc/` (git-ignored, checked against
  `sources.jsonl`), ingests it with
  [`tools/corpus/benco-9379.toml`](../tools/corpus/benco-9379.toml) and
  builds the indexes. Or step by step:

  ```sh
  uv run python tools/corpus/pull_corpus.py                # [--no-ocr-cache] [--no-embed-cache]
  export ADHOC_OCR_CACHE=$PWD/corpus-ftc/ocr-cache         # ingest reads OCR from it instead of running tesseract
  export ADHOC_EMBED_CACHE=$PWD/corpus-ftc/embed-cache     # the semantic index looks passages up in it instead of embedding them
  ```

  91 files: the case page's 87 and the trial transcripts' volumes 17–20,
  the only ones ftc.gov posts (volumes 1–16 aren't linked anywhere on it).
  Three of complaint counsel's filings (the pre-trial brief, and the
  oppositions to Patterson's motions to dismiss and for summary decision,
  70–100 MB each, mostly scanned exhibits) were cut off every time the
  script fetched them and were downloaded in a browser; `sources.jsonl`
  says so. About 1,300 pages are scans, OCRed by
  [`tools/corpus/ocr_cache.py`](../tools/corpus/ocr_cache.py) into the cache,
  keyed by page image, each entry naming the tesseract and model that read
  it. Where the same ones are installed, a hit is what tesseract would say
  again; where another one is, the page is OCRed afresh; where there is no
  tesseract (the cloud sessions), the cache is the scanned pages' only text.
  The embedding cache holds the semantic index's vector for each passage
  (33,000 distinct ones), keyed by the model's weights and the passage's
  text, so a fresh matter's semantic index takes about a minute instead of
  about an hour of CPU; another model, or text that differs, is embedded
  as usual. Without it, `ftc_matter.sh` builds the keyword index and leaves
  the semantic one building in the background (`idx status` shows how far
  it is; searches say what they cover meanwhile).
- **Fetching it again**, from a session whose network allows `www.ftc.gov`, with
  [`tools/corpus/fetch_docket.py`](../tools/corpus/fetch_docket.py). It
  follows only links from the pages it starts from (no URL is guessed),
  honours robots.txt, waits between requests, and records which page linked
  to each file:

  ```sh
  uv run python tools/corpus/fetch_docket.py --out corpus-ftc/benco-9379 \
    --start https://www.ftc.gov/legal-library/browse/cases-proceedings/151-0190-bencoscheinpatterson-matter \
    --start https://www.ftc.gov/legal-library/browse/frequently-requested-foia-records/transcripts-adjudicative-proceedings \
    --follow /legal-library/browse/cases-proceedings/ \
    --follow /transcripts-adjudicative-proceedings/benco \
    --keep 9379 --keep benco --keep schein --keep patterson --dry-run
  ```

  Look at the dry run's list first, then run it without `--dry-run`.
  Then fill the OCR cache (`uv run python tools/corpus/ocr_cache.py
  corpus-ftc/benco-9379 --cache corpus-ftc/ocr-cache --check`), make the
  matter, fill the embedding cache from its finished semantic index
  (`uv run python tools/corpus/embed_cache.py --matter matter --cache
  corpus-ftc/embed-cache --check`, which also rebuilds the index from the
  cache alone and compares), and copy them all to the bucket (`gcloud storage rsync -r corpus-ftc
  gs://ellbur-adhocdocs-corpora/ftc`).
- **Then:** put the docket on a base branch as its own matter (a
  manifest like `corpus/manifest.toml`, the workspace hook and the reader
  agent, no bench files); run the four cells with a prompt in the style of
  the chronology's (`prompt-chronology.md`), checked with `freecheck.py`.
  A cell will likely run for an hour or more and cost tens of dollars, so
  start with the two Opus-alone cells.
