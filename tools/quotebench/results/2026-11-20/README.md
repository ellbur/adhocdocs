# First run: four cells, one run each (2026-11-20)

Each cell was one Claude Code cloud session on Claude Opus 5.5 (Claude Code
2.1.329, default effort), started from `main` (none of the bench's files in
it), with the session instructions appended to its system prompt
(`instructions-tool.md` or `instructions-no-tool.md`, which are
`run.claude_md` with the paths of the cloud checkout). A first turn set up
the sample matter and the tools on PATH (`page` only in the tool cells); the
second gave the task (`prompt.md`, the 48 items of `tasks.json`); the memo
was fetched afterwards. Another session drove them (`create_session`,
`send_message`), not `run.py`: there was no API key for the meter. Cost is
Claude Code's own list-price figure for the task turn, from the session's
result events (`model_usage_since_last_result`), setup excluded.

| memo | page tool | reading by | items misquoted | words quoted | coverage | Opus $ | Sonnet $ | total $ |
|---|---|---|---|---|---|---|---|---|
| memo-1 | no | Opus | 0/48 | 7,332 | 1.00 | 1.77 | — | 1.77 |
| memo-2 | no | Sonnet subagents | 0/48 | 7,321 | 1.00 | 1.42 | 0.60 | 2.02 |
| memo-3 | yes | Opus | 0/48 | 7,321 | 1.00 | 2.17 | — | 2.17 |
| memo-4 | yes | Sonnet subagents | 0/48 | 7,321 | 1.00 | 1.80 | 1.46 | 3.26 |

`check-N.json` is check.py's summary for `memo-N.md`. Citations were right
in all 192 quotations, with no case or punctuation differences.

## What happened

- **No cell typed a quotation.** Without the page tool, Opus wrote scripts
  that cut the passages out of the document text (memo-1 from the HTML
  original and the PDF's layer) and compared each against the indexed text
  before writing the memo. With Sonnet subagents (memo-2), the subagents
  only located each passage's start and end; Opus extracted the words
  itself. With the tool, both cells built the page from locators (memo-4's
  from the subagents' reports) and `page check`ed it.
- **So the tool bought no accuracy on this task, and cost more**, mostly in
  mechanics: finding where a paragraph or subdivision ends (the tool has no
  such unit), the default citation (the PDF's internal title, and the PDF's
  page index shown as a printed page where a page has no label), and
  adding 48 references one call at a time. Delegating saved little: Opus
  did the extraction or checking itself in both delegated cells.
- **Checker fixes this run found** (none changed a verdict once fixed):
  a block quotation starting "(l) …" read as a citation line; Markdown
  escapes (`\[o\]nly`) read literally; the stored passages of items 41
  and 42 included the rule's history note.

## Limits

One run per cell. The children saw that their messages came from another
Claude session, though nothing in their transcripts suggests they took it
for a test. The cloud session's own system prompt and tools came with them:
the same in every cell, but not the session container's.
