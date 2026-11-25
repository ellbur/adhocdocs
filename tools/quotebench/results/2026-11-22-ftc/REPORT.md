# Quotation accuracy on a real FTC docket

*An experiment run on 22–23 November 2026. The data are in this directory:
the four memos (`analysis-N.md`), what the checker flagged in each
(`check-N.json`), the task (`prompt.md`) and the setup messages.
[IMPROVEMENTS.md](IMPROVEMENTS.md) lists what the sessions' transcripts show
could be better in the tools, and [README.md](README.md) is a shorter summary
for readers who know the bench.*

## In brief

Claude was asked to write a legal analysis of a real antitrust case, full of
quotations from the case documents, in four different setups.

- **Where Claude typed its own quotations, 2 of 251 were wrong.** This was
  the setup with no quotation tool, where Claude did all the reading itself.
  The errors are small and believable: a quotation with words silently left
  out, and a paraphrase presented as a quotation.
- **Where the quotations came from the document itself, none were wrong.**
  With adhocdocs' page tool, a quotation is a pointer to the document's
  text, not something Claude types. 224 quotations made that way had no
  errors.
- **The tool doesn't cover everything.** In one tool setup, Claude also
  typed a few short phrases in quotation marks as labels, and one of those
  ("everyone says no") is in no document.

In three earlier experiments, on a smaller invented case file, Claude made
no errors at all in about 1,100 typed quotations. So this is the first time
the experiment has caught a misquotation, and it happened on the larger,
real record. With one run of each setup, it shows that the errors happen,
not how often.

## Background

### The problem

A lawyer who quotes a document in a brief or memo has to quote it exactly.
A quotation that drops words without an ellipsis, changes a word, or
attributes words to the wrong document can mislead the reader and the court,
and it is the kind of mistake opposing counsel looks for. When an AI writes
the memo, every quotation is something it copied out of a document, or
remembered, and copying can go wrong. Checking every quotation by hand
against its source is slow, so in practice many go unchecked.

### What adhocdocs does about it

[adhocdocs](../../../../README.md) is a set of tools for working on a legal
matter's documents with Claude Code. One of them, the **page tool**
(`page`), changes how quotations are made:

- **Typed quotation:** Claude writes the words between quotation marks
  itself, as anyone would. Whether they match the document depends on
  Claude's copying.
- **Reference:** Claude says which passage it means, roughly where it starts
  and ends, and the tool records that span of the document's text. When the
  memo is shown, the words are filled in from the document. Claude never
  types them, so they can't differ from the source. If the document's text
  at that spot ever changed, the reference would show as broken, not as
  different words.

### The experiment, and what earlier runs found

The **quotation bench** ([docs/QUOTEBENCH.md](../../../../docs/QUOTEBENCH.md))
measures how often Claude misquotes, with and without the page tool, and
what each costs. It crosses two choices, giving four setups ("cells"):

|                      | Claude Opus does the reading | Opus hands the reading to cheaper Sonnet subagents |
|----------------------|------------|-----------------------|
| **no page tool** (typed quotations)   | cell 1 | cell 2 |
| **page tool** (references)            | cell 3 | cell 4 |

Handing reading to subagents is the usual way to make a reading-heavy job
cheaper. It is also where quotations pass through a second model's report
before they reach the memo, so it might be riskier.

The first three tasks used the project's sample matter, a small invented
case file. In about 1,100 typed quotations across those runs, Claude Opus
5.5 made no word errors. There the page tool's value was only that nobody
had to check the quotations, not that it caught anything. Its overhead had
also come down until it was the cheapest setup on one task.

This fourth task was meant as a stress test: a real collection, much larger,
with many more quotations, and a question that needs reading, not one
search.

## This run

### The case

*In the Matter of Benco Dental Supply Co., Henry Schein, Inc. and Patterson
Companies, Inc.*, FTC Docket No. 9379. The FTC alleged that the three
largest US dental distributors agreed among themselves not to do business
with buying groups (groups of dentists who buy together for discounts). The
Administrative Law Judge found that Benco and Patterson had agreed, and
dismissed the complaint against Schein (Initial Decision, October 2019).

The matter is the public record from the FTC's website:
- **The files:** 91 of them, several thousand pages: the complaint,
  motions, briefs and orders, the Initial Decision, and four volumes of
  trial transcript. About 1,300 pages are scans, read by OCR.
- **The emails aren't in it.** The parties' exhibits aren't public. So
  every email is quoted as a brief or the decision quotes it.
- **The key witnesses aren't in it.** Only transcript volumes 17–20 are
  posted, and none of the key witnesses testifies in them.

All four cells noticed both gaps and said so in their memos.

### The task

The same request went to each cell:

> For each distributor, set out the evidence complaint counsel relied on to
> show it joined the agreement, how that company explained the same conduct
> as its own independent decision, and how the Administrative Law Judge
> weighed each piece in the Initial Decision, and why it came out
> differently for Schein. … quote each in its own words … Cite each
> document by its file name.

The request doesn't mention quotation accuracy. Answering it means following
the same emails through both sides' briefs, the transcripts and the
decision, quoting each.

### How the memos were checked

A checking script (`freecheck.py`) found every quotation in each memo and
compared it word by word with the document it cites. These differences
aren't counted as errors:

- spacing and line breaks;
- the kind of quotation marks or dashes;
- changes the writer marked: an ellipsis for omitted words, brackets for
  an alteration, "[sic]".

Every quotation the checker flagged was then read by hand against its
source. Most flags were not errors; those are listed below.

## Results

| cell | page tool | who read | quotations | words quoted | wrong | cost | minutes |
|---|---|---|---|---|---|---|---|
| 1 | no | Opus | 251 typed | 3,013 | **2** | $4.19 | 7 |
| 2 | no | 7 Sonnet subagents | 206 typed | 6,842 | **0** | ~$15.10 | 22 |
| 3 | yes | Opus | 141 references, plus 11 typed labels | 3,640 | **0** in references; 1 typed label | $8.69 | 15 |
| 4 | yes | 7 Sonnet `reader` subagents | 83 references | 9,040 | **0** | ~$9.40 | 12 |

Cost is the list price of the task's own API use, Opus and Sonnet together,
with setup excluded. "~" means the setup cost was estimated rather than read
from the log. Cell 4 also cited 124 passages without quoting them;
citations show no words, so they can't be misquoted.

All four memos reach the decision's outcome for the same reasons, and
several also caught mistakes in the parties' own briefs: wrong dates, and an
email one brief attributes to the wrong person.

### The errors, shown against their sources

**Cell 1: words left out with no ellipsis.** The memo quotes complaint
counsel's post-trial brief as saying:

> "By July 2011, Sullivan's position had changed: 'I don't think you will
> ever see a full service dealer get involved with GPOs.'"

The brief actually says:

> By July 2011, Sullivan's position had changed. In spite of Sullivan's
> previous enthusiasm for working with the buying group Smile Source,
> Sullivan informed his bosses, "I don't think you will ever see a full
> service dealer get involved with GPOs."

The memo joined two sentences and dropped the 16 words between them
without marking the gap. The gist survives, but the brief doesn't say what
the memo's quotation says it does.

**Cell 1: a paraphrase in quotation marks, cited to the wrong document.**
The memo says that before February 2013 Patterson had "no company policy"
on buying groups, citing the Initial Decision at p. 40. The decision's words
are "did not have a company policy with respect to buying groups". The
phrase "no company policy" does appear in other filings, but not in the one
cited.

**Cell 3: a summary in quotation marks.** Cell 3 used the page tool for its
141 quotations, all exact. But in its tables and headings it also typed 11
short phrases in quotation marks as labels. Ten are exact. One reads:
Internal "everyone says no" statements (Misiak, Rogan, Ryan, Foley). No
document says "everyone says no". The label sums up what those people
wrote, but the quotation marks claim they wrote it.

None of these is garbled. Each reads like the source, which is why a
reviewer would probably let it through, and why it would be dangerous in a
filed brief.

### Flags that weren't errors

| cell | flagged | why it isn't an error |
|---|---|---|
| 1 | "wrath [sic] of text messages…" | the [sic] is in the brief itself |
| 1 | "…not going to bid on Smile Source" | the document's text has a footnote number run into the last word ("Source270") |
| 1 | "The only communications that Chuck Cohen has ever even had…" | cited by the memo's own short name for Benco's brief ("BB"), where the words are exact |
| 2 | "How to read this", "What was and wasn't read" | the memo's own section titles |
| 2 | "…Benco's no- GPO policy…" | the source breaks the line at "no-"; cell 2's own formatting script turned the break into a space |
| 2 | "essentially rest[s]" | an alteration, marked with brackets |
| 3 | ten short labels | the words are exact, on a line that doesn't name their document |
| 4 | the memo's title | it repeats the question |

The checker's raw count of wrong words for cell 1 is 30, because it counts
each of the 16 dropped words. Counted by quotation, cell 1 has 2 wrong.

## What it means

**The page tool did what it is for.** Without it, Claude made two errors of
the hard-to-catch kind. A reference can't make either one: it can't join
two sentences without marking the gap, and it can't cite a document that
doesn't contain its words. On the sample matter, the case for the tool was
that nobody had to check. Here there was something to catch.

**Typing is the risk, not delegating.** Cell 2's Sonnet readers made no
errors in 206 typed quotations. They used a script they wrote themselves to
extract each quotation from the document's text, and checked it the same
way, so in effect nobody typed. The errors came from cell 1, where Opus
read and typed alone, finishing in 7 minutes. Its quotations were never
checked after writing. About a dozen of them, the misquoted one among them,
appear in no tool output the session saw, which suggests they were typed
from memory of this public case. That is an inference.

**The tool's gap is the text around the references.** The instructions say
never to type a quotation, but a short label in quotation marks doesn't
feel like one to the writer. `page check` only flags typed quotations of
five words or more. It should flag shorter ones, and the instructions
should say that a phrase in quotation marks is a quotation even when it's a
label.

**On cost, the tool held its own.**
- **Cheapest overall:** cell 1, Opus alone without the tool, at $4.19. It
  also quoted the fewest words.
- **Cheapest per word quoted:** cell 4, the tool with Sonnet readers, at
  $1.04 per 1,000 words. Its readers left 591 checked references, and the
  lead used 154 of them with nothing to recheck.
- **Most expensive:** cell 2, delegating without the tool, at about $15.
  Its readers produced large quotation files that the lead had to work
  through.

## Limits

- **One run per cell.** Two errors in one cell is a sighting, not a rate.
  Comparing rates needs several runs of each setup, ideally with a fixed
  list of passages to quote.
- **The cells didn't do the same reading.** Cell 2 skipped the proposed
  findings and pre-trial briefs, and each cell chose its own passages, so
  counts and costs aren't like for like.
- **The instructions differ slightly beyond the tool.** The no-tool cells'
  instructions describe a plain Markdown memo instead of a page. This is
  the bench's standing design.
- **No semantic search.** Search was by keyword only in every cell, because
  the embedding model couldn't be downloaded (see below).
- **One interruption.** Cells 2–4 got a short note about git branches while
  they worked. It said nothing about the task.
- **The checker only sees marked quotations.** It finds text in quotation
  marks, block quotations and references. A paraphrase without quotation
  marks is out of its scope.

## How it was run

Each cell was a Claude Code cloud session on Claude Opus 5.5, started from
the `ftc-matter` branch and driven from another session (`create_session`,
`send_message`). Each got three messages:

1. **Setup** (`setup-no-tool.md`, `setup-tool.md`): build the matter with
   `tools/corpus/ftc_matter.sh`, put the tools on PATH (`page` only in cells
   3 and 4), and write the session instructions to
   `matter/workspace/CLAUDE.md`. The tool cells got the instructions as
   users have them. The no-tool cells got them without rule 1 and the page
   tool's parts, as `run.claude_md` makes them.
2. **A status check:** index status, `which page`, and the instructions
   file.
3. **The task:** `prompt.md`, ending with `DELEGATE_FTC["no"]` (cells 1 and
   3) or `["sonnet"]` (cells 2 and 4) from `run.py`.

`freecheck.py` checked the memos against a copy of the matter built from
the same public bucket. Every reference resolved to the same text, so the
copies match. Costs are Claude Code's list-price figures from each
session's result events. Cells 1 and 3 have exact setup baselines; for 2
and 4, cell 1's setup cost ($0.91) was subtracted.

## Operational lessons

- **Start each cell on its own branch.** The sessions ran on `ftc-matter`
  itself, and the workspace hook pushes whatever branch the session is on.
  Cell 3's setup file and finished memo landed on `ftc-matter` before it
  switched branches. The other cells' pushes were refused and they were told
  not to pull, so no cell saw another's work. `ftc-matter` has since been
  reset to `26a1933` (23 November), after archiving cell 2's session, whose
  local `ftc-matter` held unpushed commits.
- **Sessions won't publish work product on another session's word.** Every
  cell refused to push its memo until the user approved in its own session.
  That is right for work product, but a driven run needs the user at the
  end, or setup that has the user approve the push in the first message.
- **The bucket needs the embedding model.** The embedding cache made it
  through, but it can't be used without the model:
  - its keys are built from the model weights' hash (`embed_cache_key`);
  - queries are embedded with the model.

  The session image has the model built in. A cloud session must download
  it from huggingface.co, the only source fastembed has for
  `BAAI/bge-small-en-v1.5`, and these environments block that host. With
  the weights in the bucket, which `tools/corpus/model_bundle.py` now
  supports, a fresh matter's semantic index needs no Hugging Face access at
  all. A Hugging Face Inference Endpoint (INDEXING.md, "Remote embedding")
  is a separate, optional speedup, and isn't built.
- **Cost reporting.** Each session's result events carry cumulative
  `modelUsage` by model. A task's cost is the cumulative figure when the
  memo was done, minus the figure after setup.

## Next

1. Make the setup put each cell on its own branch.
2. Make `page check` (and `freecheck.py`) flag short phrases in quotation
   marks outside a reference, and add a line on labels to rule 1.
3. Upload the model to the bucket (`tools/corpus/model_bundle.py export`,
   then `gcloud storage rsync`; the commands are in QUOTEBENCH.md).
4. Run each cell three or more times, with semantic search working, to turn
   the sighting into a rate. Start with cell 1, the most error-prone, and
   cell 4, the cheapest per word quoted.
5. Work through [IMPROVEMENTS.md](IMPROVEMENTS.md).
