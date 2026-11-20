# Third run: a deadlines reference, four cells, one run each (2026-11-20)

A reading-heavy task, meant to give delegation something to save: "list
every time limit" in the Federal Rules of Civil Procedure (about 65,000
words) and Title 9, "quote the language that sets the limit", cite rule and
file, grouped by stage of the case (`prompt.md`). Cloud sessions on Claude
Opus 5.5, as in the second run, from `sample-matter` (no tool) and
`sample-matter-reader` (with the page tool and a `reader` subagent, Sonnet:
reads a part, leaves checked references with notes in
`parts/<name>.resolved.jsonl`, replies in a few lines). The page tool now has
quiet batches, ranges from scripts (`doc`/`start`/`end` rows), `--resolved`,
and `page check` saying when a page is done; the instructions say not to read
references back and to render only if asked.

| deadlines | page tool | reading by | quotations | words | wrong words | Opus $ | Sonnet $ | total $ | minutes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | no | Opus | 164 typed | 3,959 | **0** | 3.27 | — | 3.27 | 11 |
| 2 | no | 7 Sonnet subagents | 486 typed | 10,077 | **0** | 2.81 | 3.38 | 6.19 | 11 |
| 3 | yes | Opus | 101 references | 4,528 | **0** | 2.51 | — | 2.51 | 13 |
| 4 | yes | 7 `reader` subagents | 293 references | 6,729 | **0** | 3.14 | 2.11 | 5.25 | 16 |

Cost is Claude Code's list-price figure for the task (setup excluded; a
final "nothing to commit" reply, about $0.2–0.3, is inside it).

## Reading the verdicts

freecheck's flags, each read by hand: none is a wrong word. In
deadlines-1, "must be served" (§ 12, right) was taken as cited to an opinion
named later in the line, and "within the time/period" is the memo describing
its own search terms. In deadlines-4, "Court may change" is a field label.
Two checker fixes came out of this run: a dash between words now separates
them ("sent—or", which deadlines-2 quotes everywhere, had read as one word),
and a letter-numbered rule's running head ("Rule F") is page furniture.

## What changed, and what didn't

- **With the tool, Opus alone was the cheapest cell** ($2.51 against $3.27
  without it), the first time the page tool cost less than typing. Its
  references came from one batch (some rows had bad ids or ambiguous
  starts, fixed by giving character offsets), and it didn't read them back.
- **Delegation still cost more**, nearly double, with or without the tool:
  - Opus alone didn't read the whole PDF either: it found the limits by
    searching for number-of-days phrases and reading around them, so there
    was little reading for Sonnet to take over.
  - The lead's own work stayed: finding and splitting the documents first,
    handback turns at 120–150K context, and after the readers returned,
    checking or reviewing every quotation (deadlines-2 re-checked all 490 by
    script; deadlines-4 read a 69 KB review file of every quote).
  - The readers over-included: 490 and 293 entries against 101 alone,
    which made the merge and review bigger. Coverage may be better; cost
    isn't.
  - The reader handoff mostly worked: all seven left resolved files and the
    lead added them in a batch, but 110 of 316 rows first failed on
    mixed-case ids that the readers' dry runs hadn't checked (now they do),
    and the readers shared a scratch folder (now each has its own).
