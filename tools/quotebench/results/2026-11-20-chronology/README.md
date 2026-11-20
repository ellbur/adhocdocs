# Second run: a chronology, four cells, one run each (2026-11-20)

The task wasn't framed as quoting: a chronology of the delivery dispute for a
partner meeting, "what the people involved said at the time, in their own
words", each document cited by file name (`prompt-no.md`, `prompt-sonnet.md`;
`prompt-chronology.md` in the bench). The quotations are many and short,
from the synthetic custodians' email, logs, letters and a deposition, with
forwards and replies that repeat each other.

Each cell was a Claude Code cloud session on Claude Opus 5.5 started from
the `sample-matter` branch (one commit: `main` with the new `page ref add
--from`, no bench files, and a Stop hook that commits and pushes the
workspace's Markdown, so the chronology came out without anyone being
asked). The page tool's cells had `page` and instruction rule 1, which now
points at `--from`.

| chron | page tool | reading by | quotations | words | verbatim | uncited | unverifiable | other | wrong words | Opus $ | Sonnet $ | total $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | no | Opus | 124 typed | 2,398 | 76 | 25 | 15 | 8 | **0** | 2.42 | — | 2.42 |
| 2 | no | Sonnet subagents | 100 typed | 1,426 | 82 | 13 | 4 | 1 | **0** | 2.31 | 1.39 | 3.69 |
| 3 | yes | Opus | 70 references | 1,674 | 70 | 0 | 0 | 0 | **0** | 3.27 | — | 3.27 |
| 4 | yes | Sonnet subagents | 76 references | 1,584 | 76 | 0 | 0 | 0 | **0** | 2.34 | 1.35 | 3.69 |

`check-N.json` is freecheck.py's summary and every quotation it didn't find
verbatim, for `chron-N.md`. Cost is Claude Code's list-price figure for the
task (all its result events after setup).

## Reading the verdicts

- **Wrong words: none, in any cell.** Each typed quotation that freecheck
  didn't find verbatim was read by hand:
  - **uncited** (chron-1 25, chron-2 13): the words are a document's, word
    for word, but the line names no file (the closing analysis refers to
    "the roster", "his deposition").
  - **unverifiable** (15, 4): cited to the three scanned PDFs, which have
    no text here. chron-1 read them as images and quoted its reading,
    marked "[scan, my reading]"; the words can't be checked against
    anything but a picture.
  - **other** (chron-1: 7 "not found", 1 "misquoted"; chron-2: 1
    "misquoted"): more readings of the scans, one document's metadata
    title, and two quotations that leave out a running page header
    ("Harrowgate Fastener Supply Co. — Internal — Page 1") that falls
    inside the passage. Leaving out a page header is what a careful person
    does, not a misquotation.
- **Without the tool, about a third of the quotations need a person to
  find their source** (uncited or read off a picture). With it, every
  quotation is a reference to text, so none can be uncited, and the scans
  were either quoted as Claude's own reading (an interpretation, chron-3)
  or not quoted (chron-4).

## How the tool was used (from the transcripts)

- **The forgiving interface was used, and trusted.** Both tool cells built
  their references in `--batch` files: chron-3 69 rows (46 `from`+`to`, 23
  exact `words` for short phrases), chron-4 88 rows over five batches.
  Neither read a passage back with `text` after adding it: they checked the
  preview `page ref add` printed (first and last words, words around it,
  length) and ran `page check`.
- **What the previews caught:** chron-3 had one loose end (asked to
  confirm; it gave a longer `--to`) and one passage across a page header
  (redone as an elided quotation). chron-4 gave `from` alone for log rows,
  table cells and deposition answers, where a "paragraph" in text without
  blank lines ran on (a whole table, the rest of a deposition): about a
  dozen, all redone from the previews with `--to`.
- **Fixed since:** a passage found in identical copies of a document is one
  passage (chron-4 had to find hash prefixes when `--in <file name>` named
  three copies), and a default extent of over 120 words in text without
  blank lines is asked about ("say where it ends with --to"), not written.
- Delegation again saved nothing: chron-4's Sonnet subagents returned
  quotations "copied character-for-character from `text` output" with
  their opening words, the lead made the references, and the Sonnet cells
  cost the most.

## Limits

One run per cell. The sessions saw the hook's "Sync the workspace" commits
(chron-4 asked whether to push the last one), and that their messages came
from another session. The scanned PDFs had no OCR in any session or here.
