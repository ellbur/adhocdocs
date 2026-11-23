# Quotation accuracy on a real docket: report

*FTC Docket No. 9379, four cells, one run each, 22–23 November 2026. The
data are in this directory: the four memos (`analysis-N.md`), what the
checker flagged in each (`check-N.json`), the task (`prompt.md`) and the
setup messages. A shorter summary is in [README.md](README.md).*

## Summary

The first three runs of the quotation bench used the sample matter, and in
about 1,100 typed quotations Claude Opus 5.5 made no word errors. This run
used a real collection: the public record of an FTC antitrust case, 91
filings and transcripts, several thousand pages. The question needed
reading, not one search.

- **Typed quotations were wrong twice.** The cell where Opus typed its own
  quotations without the page tool made 2 word errors in 251 quotations.
  One drops 16 words with no ellipsis. The other puts a paraphrase in
  quotation marks and cites the wrong document for it. Both read naturally,
  and neither changes the meaning much, which is why a reviewer would
  probably miss them.
- **References were never wrong.** The two page-tool cells made 224
  references showing 12,680 words, and none differs from the source. That
  is by construction: a reference is a range of the document's text, and
  `page check` would refuse one that didn't match.
- **The tool doesn't cover typed text around the references.** One
  page-tool cell typed 11 short labels in quotation marks, and one of them
  ("everyone says no") is in no document.
- **The tool cost little.** The cheapest cell was Opus alone without the
  tool ($4.19). With the tool, Opus alone cost $8.69 and the delegated cell
  about $9.40, but the delegated cell quoted the most words of any cell for
  the least per word.
- **The substance was the same in every cell.** All four memos reach the
  Initial Decision's outcome (Benco and Patterson liable, the complaint
  against Schein dismissed) for the same reasons. Several also caught
  errors in the parties' own briefs.

One run per cell is not enough to measure an error rate. What this run
shows is that typed quotations can go wrong on a large real record, in ways
that are hard to see, and that references don't.

## The question

> For each distributor, set out the evidence complaint counsel relied on to
> show it joined the agreement, how that company explained the same conduct
> as its own independent decision, and how the Administrative Law Judge
> weighed each piece in the Initial Decision, and why it came out
> differently for Schein. … quote each in its own words … Cite each
> document by its file name.

It is in the style of the chronology task: the request doesn't mention
quotation accuracy, but answering it means following the same emails
through both sides' briefs, the transcripts and the decision, quoting each
one.

## The matter

*In the Matter of Benco Dental Supply Co., Henry Schein, Inc. and Patterson
Companies, Inc.* comes from the FTC's case page and transcripts page. It is
kept in a public bucket with its OCR and embeddings, and
`tools/corpus/ftc_matter.sh` makes it a matter:

- **The files:** 91, with about 1,300 scanned pages whose text comes from
  the OCR cache. The keyword index covered all 91 in every cell.
- **The exhibits are missing.** The parties' CX and RX exhibits aren't in
  the matter, so every email is quoted as a brief or the decision quotes
  it.
- **Most of the testimony is missing.** Only transcript volumes 17–20 are
  posted, the end of the defense case. None of the key witnesses (Cohen,
  Guggenheim, Sullivan, Foley, Ryan) testifies in them, so their testimony
  is also quoted second-hand. All four cells found this and said so.

## Design

The grid is the bench's usual two-by-two:

|                      | Opus reads | Sonnet subagents read |
|----------------------|------------|-----------------------|
| **no page tool**     | cell 1 | cell 2 |
| **page tool**        | cell 3 | cell 4 (the `reader` agent) |

Each cell was a Claude Code cloud session on Opus 5.5 in the user's Default
environment, started from `ftc-matter` and driven from this session with
`create_session` and `send_message`. Each got three messages:

1. **Setup** (`setup-no-tool.md`, `setup-tool.md`): build the matter, put
   the tools on PATH (`page` only in cells 3 and 4), and write the session
   instructions to `matter/workspace/CLAUDE.md`. The tool cells got the
   instructions as users have them. The no-tool cells got them without rule
   1 and the page tool's parts, made by `run.claude_md`.
2. **A status check:** the index status, `which page`, and the instructions
   file.
3. **The task:** `prompt.md`, ending with `DELEGATE_FTC["no"]` (cells 1 and
   3) or `["sonnet"]` (cells 2 and 4).

To check the memos, `freecheck.py` judged each quotation against a copy of
the matter built here from the same bucket. Every reference resolved to the
same text, so the copies are the same. I then read each flagged quotation
against its source by hand. The cost figures are Claude Code's list-price
figures from each session's result events, setup excluded.

## Results

| cell | page tool | reading | quotations showing words | words quoted | wrong words | Opus $ | Sonnet $ | total $ | $ per 1,000 words quoted | minutes |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | no | Opus | 251 typed | 3,013 | **2** | 4.19 | — | 4.19 | 1.39 | 7 |
| 2 | no | 7 Sonnet subagents | 206 typed | 6,842 | **0** | ~4.4 | 10.67 | ~15.1 | 2.21 | 22 |
| 3 | yes | Opus | 141 references, 11 typed labels | 3,640 | **0** in references, 1 label not in any document | 8.69 | — | 8.69 | 2.39 | 15 |
| 4 | yes | 7 `reader` subagents | 83 references, 124 citation-only uses | 9,040 | **0** | ~3.6 | 5.75 | ~9.4 | 1.04 | 12 |

The "~" figures subtract cell 1's setup cost ($0.91) as an estimate; cells
1 and 3 have exact baselines in their logs.

### The errors

**Cell 1, an omission without an ellipsis.** The memo has:

> "By July 2011, Sullivan's position had changed: 'I don't think you will
> ever see a full service dealer get involved with GPOs.'" (CCB p. 38)

Complaint counsel's post-trial brief has:

> By July 2011, Sullivan's position had changed. In spite of Sullivan's
> previous enthusiasm for working with the buying group Smile Source,
> Sullivan informed his bosses, "I don't think you will ever see a full
> service dealer get involved with GPOs."

The memo joined the two sentences: it dropped 16 words with no ellipsis and
turned the period into a colon. What it says is still roughly what the
brief says, but it isn't a quotation of the brief.

**Cell 1, a paraphrase in quotation marks.** The memo says that before
February 2013 Patterson had "no company policy" on buying groups, and cites
the Initial Decision at p. 40. The decision says Patterson "did not have a
company policy with respect to buying groups". The phrase "no company
policy" is in other filings (complaint counsel's findings, Patterson's
pre-trial brief), just not in the one cited.

**Cell 3, a summary in quotation marks.** One of cell 3's typed labels
reads: Internal "everyone says no" statements (Misiak, Rogan, Ryan, Foley).
The phrase is in no document. It sums up what those people wrote, but the
quotation marks say they wrote it. The other ten labels are exact: they
repeat words the page also gives as references, on another line.

### What the checker flagged that isn't an error

| cell | flagged | why it isn't an error |
|---|---|---|
| 1 | "wrath [sic] of text messages…" | the [sic] is the brief's own |
| 1 | "…not going to bid on Smile Source" | the source has "Smile Source270", with a footnote number run into the last word |
| 1 | "The only communications that Chuck Cohen has ever even had…" | cited as "BB", the memo's own abbreviation for Benco's post-trial brief, where the words are verbatim |
| 2 | "How to read this", "What was and wasn't read" | the memo's own section titles |
| 2 | "…Benco's no- GPO policy…" | the source breaks the line after "no-"; copied with the break |
| 2 | "essentially rest[s]" | an alteration marked with brackets |
| 3 | ten short labels | the words are a document's, on a line that doesn't name it |
| 4 | the page title | it repeats the question |

The checker's own "wrong words" count for cell 1 (30) counts the dropped
sentence word by word. Counted by quotation, cell 1 has 2 wrong.

## What it means

**The hypothesis held, though not in the way the sample matter suggested.**
The page tool makes a quotation the document's text at a recorded range,
so the reader doesn't have to trust the writer's copying. On the sample
matter nothing needed catching, and the tool's value was only that nobody
had to check. Here, without the tool, Opus made two errors.

- **They are the hard kind to catch.** Neither is garbled: each reads like
  the source, which is what makes it dangerous in a filed brief.
- **A reference couldn't make either error.** It can't join two sentences
  without an ellipsis, and it can't cite a document that doesn't contain
  its words.

**Delegating to Sonnet didn't cause errors.** Cell 2's Sonnet readers made
none in 206 quotations. They pulled their quotations out with a script
(`q.py find`, checked with `q.py verify`), and the lead wrote the memo from
those files, checking its quotations again (0 problems, it said). It was cell 1, reading and typing alone in 7 minutes, that
made the errors. One run can't say whether speed causes errors.
Delegating is not what makes quotations unsafe; typing them is.

**The tool's gap is the text around the references.** Rule 1 says never to
type a quotation into durable work, but a short label in quotation marks
doesn't feel like a quotation to the writer. That suggests two changes:

- `page check` could flag quotation marks around three or more words
  outside a reference.
- The instructions could say that a phrase in quotation marks is a
  quotation even when it's a label.

**What it cost.**
- **Opus alone:** without the tool it was cheapest in total ($4.19), but it
  also quoted the fewest words.
- **Per word quoted:** the delegated tool cell was cheapest ($1.04 per
  1,000 words). The `reader` handoff worked: seven readers left 591 checked
  references, the lead used 154, and there was nothing to recheck.
- **Delegating without the tool:** this was the most expensive cell
  ($15.1). Its readers wrote long reports and quotation files that the lead
  then had to work through.

## Limits

- **One run per cell.** Two errors in one cell is a sighting, not a rate.
  Several runs per cell, ideally with a fixed list of passages to quote,
  would be needed to compare rates.
- **The cells' scope differed.** Cell 2 left the proposed findings and the
  pre-trial briefs unread, and cell 1 read the briefs and the decision.
  The cells didn't quote the same passages, so the costs and counts aren't
  like for like.
- **The instructions differ beyond the tool.** The no-tool cells' summary
  section describes a Markdown memo, not a page. That is the bench's
  standing design, but it is a second difference.
- **No semantic search.** The bucket's embedding cache (33,000 passage
  vectors) was pulled in every cell, but the model's weights aren't in the
  bucket, and the cells couldn't fetch them (see below). Every cell
  searched by keyword only.
- **A message arrived during the work.** Cells 2–4 got a note about git
  branches while working. It said nothing about the task, but it did
  interrupt them.
- **The `reader` agent stayed in the no-tool cells.** Auto mode refused to
  delete it, so cells 1 and 2 were told not to use it, and cell 2 used
  general-purpose subagents.
- **The checker can miss things.** It only finds quotations that are in
  quotation marks, are block quotations or are references. A paraphrase
  without quotation marks is out of scope by design.

## Operational lessons

- **Start each cell on its own branch.** The sessions ran on `ftc-matter`
  itself, and the workspace hook pushes the branch the session is on.
  Cell 3's setup file and finished memo were pushed to `ftc-matter`
  (`f765e20`, `3591020`) before it switched branches. The other cells'
  pushes were refused, and they were told not to pull. Cell 2's branch
  starts from the original commit, so no cell used another's work. The next
  setup message should start with `git checkout -b <cell branch>`.
  `ftc-matter` has been reset to `26a1933` (23 November), after archiving
  cell 2's session, whose local `ftc-matter` held unpushed sync commits.
- **Sessions won't publish work product on another session's word.** Every
  cell refused to push its memo until the user approved in its own session.
  That is right for work product, but it means a driven run needs the user
  at the end, or a setup that has the user approve the push in the first
  message.
- **The bucket needs the model's weights.** The embedding cache made it
  through, but it can't be used without the model: its keys are the
  weights' hash and each passage's text (`embed_cache_key`, which loads the
  model to hash it), and queries are embedded locally. The session image
  has the model built in; a cloud session gets it from fastembed's one
  source for `BAAI/bge-small-en-v1.5`, huggingface.co, which these
  environments block. Putting the weights (about 67 MB, MIT) in the bucket
  and pointing `ADHOC_MODELS` at them would make a fresh matter's semantic
  index a lookup with no Hugging Face access at all. (A Hugging Face
  Inference Endpoint, INDEXING.md's "Remote embedding", is a separate,
  optional speedup for embedding new text, and isn't built.)
- **Cost reporting.** Each session's result events carry cumulative
  `modelUsage` by model. The task's cost is the cumulative figure when the
  memo was done minus the figure after setup.

## Next

1. Make the setup put each cell on its own branch. (`ftc-matter` is already
   reset to `26a1933`.)
2. Add a check to `page check` (or `freecheck.py`) for three or more words
   in quotation marks outside a reference, and add a line on labels to rule
   1.
3. Put the model's weights in the bucket and have `pull_corpus.py` fetch
   them, so the semantic index builds from the cache with no network.
4. Run each cell three times or more, with semantic search working, to
   turn the sighting into a rate. Start with the no-tool Opus cell and the
   delegated tool cell, the most error-prone and the cheapest per word
   quoted.
