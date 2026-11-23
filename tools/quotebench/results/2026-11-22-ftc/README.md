# Fourth run: the FTC docket, four cells, one run each (2026-11-22)

The full write-up, with the errors quoted against their sources, what it
means and what to do next, is [REPORT.md](REPORT.md).

The stress test QUOTEBENCH.md planned: a real collection, *In the Matter of
Benco Dental Supply Co., Henry Schein, Inc. and Patterson Companies, Inc.*
(FTC Docket No. 9379), 91 public filings and transcripts, several thousand
pages, about 1,300 of them scans with OCR from the cache. The question needs
reading, not one search: for each distributor, the evidence complaint counsel
relied on, the company's account of the same conduct, and how the Initial
Decision weighed it, quoted and cited by file name (`prompt.md`, with
`DELEGATE_FTC` from `run.py` for `{{DELEGATE}}`).

Each cell was a Claude Code cloud session on Claude Opus 5.5 started from
`ftc-matter`. The first message (`setup-tool.md`, `setup-no-tool.md`) built
the matter with `tools/corpus/ftc_matter.sh`, put the tools on PATH (`page`
only in the tool cells) and wrote the session instructions to
`matter/workspace/CLAUDE.md` (with or without rule 1 and the page tool's
parts, as `run.claude_md` makes them). A short status check followed, then
the task.

| analysis | page tool | reading by | quotations showing words | words | wrong words | Opus $ | Sonnet $ | total $ | minutes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | no | Opus | 251 typed | 3,013 | **2** (one unmarked omission, one paraphrase in quotation marks) | 4.19 | — | 4.19 | 7 |
| 2 | no | 7 Sonnet subagents | 206 typed | 6,842 | **0** | ~4.4 | 10.67 | ~15.1 | 22 |
| 3 | yes | Opus | 141 references (+11 short typed labels) | 3,640 | **0** in references; 1 typed label is a paraphrase in quotation marks | 8.69 | — | 8.69 | 15 |
| 4 | yes | 7 `reader` subagents | 83 references (+124 citation-only) | 9,040 | **0** | ~3.6 | 5.75 | ~9.4 | 12 |

`check-N.json` is freecheck.py's summary and every quotation it didn't find
verbatim, for `analysis-N.md`, checked against a matter built here from the
same bucket. Cost is Claude Code's list-price figure for the task turn (and
the turns the subagents' hand-backs started), from the sessions' result
events. Setup and the status check are excluded: exactly for 1 and 3, by
cell 1's figure ($0.91) for 2 and 4.

## Reading the verdicts

Every flag was read by hand against the source.

- **analysis-1, two real errors, the bench's first.**
  - "By July 2011, Sullivan's position had changed: 'I don't think you will
    ever see a full service dealer get involved with GPOs.'" Complaint
    counsel's post-trial brief has a sentence between the two: "By July 2011,
    Sullivan's position had changed. In spite of Sullivan's previous
    enthusiasm for working with the buying group Smile Source, Sullivan
    informed his bosses, “I don't think …”". Sixteen words dropped with no
    ellipsis, and the period made a colon. The meaning survives; the
    quotation isn't one.
  - Before February 2013 Patterson had "no company policy" (cited to the
    Initial Decision, p. 40). The decision says "did not have a company
    policy". The three words are in other filings, not the one cited.
  - Not errors: "wrath [sic]" (the brief's own [sic]); "… not going to bid on
    Smile Source" (the source has "Smile Source270", a footnote number run
    in); "The only communications …" (cited "BB", the memo's abbreviation for
    Benco's post-trial brief, where it is verbatim).
- **analysis-2, none.** Two flags are the memo's own section titles ("How to
  read this"); "no- GPO" is the source's line-end hyphen ("no-\nGPO") copied
  with its break; "essentially rest[s]" is a marked alteration of "rest".
- **analysis-3.** The 141 references are verbatim by construction. Besides
  them it typed 11 short phrases in quotation marks, as headings and table
  labels ("Open the floodgates", "hold their positions", "signed an
  agreement", "10-second email", "opportunity to be huge", "I believe I
  did"). Ten are the words of a document the page also references, just not
  on that line. One is in no document: Internal "everyone says no"
  statements (Misiak, Rogan, Ryan, Foley), a characterization in quotation
  marks.
- **analysis-4, none.** The one flag is the page's title, which repeats the
  question.

## What this run shows

- **Opus 5.5 can misquote, given enough.** After about 1,100 clean typed
  quotations in the first three runs, the Opus-alone cell without the tool
  made two errors in 251, both of the kind a reviewer misses: a splice that
  reads naturally, and a paraphrase that sounds like the source. The Sonnet
  readers without the tool made none in 206 (they extracted quotations by
  script and the lead copied them), so one run per cell can't say which
  setting is riskier.
- **With the tool, a quotation that shows words can't be wrong**, and in
  neither tool cell was one: 224 references, 12,680 words. What the tool
  doesn't cover is the words around them. Cell 3's labels are typed, and one
  of them puts a summary in quotation marks. Rule 1 says not to type a
  quotation; a short phrase used as a label doesn't feel like one.
- **Delegation cost more again** without the tool ($15 against $4), but with
  it the delegated cell was close to Opus alone ($9.4 against $8.7) while
  quoting two and a half times as many words. The `reader` handoff worked:
  seven readers left 591 checked references, and the lead used 154 of them.
- **The cells didn't read the same things.** The matter has the filings and
  transcript volumes 17–20 only: no exhibits, and none of the key witnesses
  testify in those volumes, so every email and most testimony is quoted as a
  brief or the decision quotes it. All four said so. Cell 2 left the proposed
  findings and pre-trial briefs unread; cell 1 read the briefs and decision
  alone in 7 minutes.

## Limits and what went wrong

- **One run per cell.** Two errors in one cell is a sighting, not a rate.
- **No semantic index.** Neither this environment nor the cells' could reach
  huggingface.co for the embedding model, so `--index semantic` failed and
  search was keyword only, in every cell alike.
- **The workspace hook pushed to the base branch.** The cells ran on
  `ftc-matter` itself, so the Stop hook's `git push origin HEAD` sent cell 3's
  setup and finished analysis to `ftc-matter` (f765e20, 3591020) before it
  moved to its own branch. The other cells' pushes were refused (non-fast-
  forward) and they were told not to pull; cell 2's branch starts from
  26a1933, and its memo doesn't use cell 3's. `ftc-matter` needs resetting to
  26a1933 before it's used again, and the next run's setup should start with
  `git checkout -b <cell branch>`.
- **The cells wouldn't push without the user.** Each refused to publish work
  product on the word of another session; the user approved in each.
- **No `reader` removal in the no-tool cells.** Auto mode refused deleting
  `.claude/agents/reader.md`; the cells were told not to use it, and cell 2
  used general-purpose subagents.
